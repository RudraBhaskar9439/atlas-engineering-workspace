import asyncio
import hashlib
import json
import os
import secrets
from typing import Literal
from contextlib import asynccontextmanager
from pathlib import Path

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from redis.exceptions import RedisError

from . import graph as g
from . import changes, github, models, storage
from .config import BASE_URL, DEMO_ENABLED, REDIRECT_URI, ROOT
from .fixture import apply_scenario, seed

TASK_CONSTRAINTS = ["Use authorized, cited evidence.", "Require approval before local task mutations."]

oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@asynccontextmanager
async def lifespan(app):
    storage.setup()
    # No paid API requests occur during startup or fixture creation.
    try:
        if g.health():
            seed("demo")
    except RedisError:
        pass  # Health and API responses explain the unavailable database without preventing the UI from starting.
    with storage.connection() as db:
        db.execute("UPDATE jobs SET status='interrupted' WHERE status='running'")
        db.execute("UPDATE usage SET status='uncertain' WHERE status='reserved'")
    yield


app = FastAPI(title="Atlas Company Brain", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=storage.SESSION_SECRET,
    same_site="lax",
    https_only=BASE_URL.startswith("https://"),
    max_age=86400 * 7,
    session_cookie="atlas_session",
)


@app.middleware("http")
async def boundary(request: Request, call_next):
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        return JSONResponse({"detail": "Invalid content length"}, status_code=400)
    if length > 500000:
        return JSONResponse({"detail": "Request exceeds the 500 KB limit"}, status_code=413)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        allowed = {BASE_URL, "http://localhost:5173", "http://127.0.0.1:5173", "http://127.0.0.1:8001"}
        if origin and origin not in allowed:
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith(("/api/", "/auth/")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ValueError)
async def invalid(request, error):
    return JSONResponse(
        {"detail": str(error)},
        status_code=409
        if "newer checkpoint" in str(error).lower() or "changed since" in str(error).lower()
        else 400,
    )


@app.exception_handler(LookupError)
async def missing(request, error):
    return JSONResponse({"detail": "Record not found or inaccessible"}, status_code=404)


@app.exception_handler(RedisError)
async def database_unavailable(request, error):
    return JSONResponse(
        {"detail": "The company graph is unavailable. Start the local database and retry."}, status_code=503
    )


def user(request: Request):
    identity = request.session.get("user")
    if not identity:
        raise HTTPException(401, "Sign in or open the synthetic demo first")
    allowed = {v.strip().lower() for v in os.getenv("ATLAS_ALLOWED_EMAILS", "").split(",") if v.strip()}
    if identity.get("auth_type") == "google" and allowed and identity["email"].lower() not in allowed:
        raise HTTPException(403, "Workspace access has been revoked")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if not secrets.compare_digest(
            request.headers.get("X-CSRF-Token", ""), request.session.get("csrf", "missing")
        ):
            raise HTTPException(403, "Session verification failed. Refresh and retry.")
    return identity


def editor(identity=Depends(user)):
    if identity["role"] not in {"owner", "engineer"}:
        raise HTTPException(403, "This account has read-only access")
    return identity


def owner(identity=Depends(user)):
    if identity["role"] != "owner":
        raise HTTPException(403, "Owner access required")
    return identity


def establish(request, identity):
    request.session.clear()
    request.session["user"] = identity
    request.session["csrf"] = secrets.token_urlsafe(32)


@app.get("/api/health")
def health():
    try:
        connected = g.health()
    except Exception:
        connected = False
    return {
        "app": "Atlas",
        "database": connected,
        "google_configured": bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET")),
        "redirect_uri": REDIRECT_URI,
    }


@app.get("/api/me")
def me(request: Request):
    return {
        "user": request.session.get("user"),
        "csrf": request.session.get("csrf"),
        "demo_enabled": DEMO_ENABLED,
    }


class DemoLogin(BaseModel):
    role: str = "owner"


@app.post("/auth/demo")
def demo_login(body: DemoLogin, request: Request):
    if not DEMO_ENABLED:
        raise HTTPException(404)
    if body.role not in {"owner", "viewer"}:
        raise ValueError("Choose owner or viewer")
    seed("demo")
    establish(
        request,
        {
            "uid": "demo_" + body.role,
            "name": "Alex Morgan" if body.role == "owner" else "Guest reviewer",
            "email": "alex@orbit.example" if body.role == "owner" else "reviewer@orbit.example",
            "role": body.role,
            "tenant": "demo",
            "auth_type": "demo",
        },
    )
    return me(request)


@app.get("/auth/google")
async def google_login(request: Request):
    if not os.getenv("GOOGLE_CLIENT_ID") or not os.getenv("GOOGLE_CLIENT_SECRET"):
        raise HTTPException(503, "Google sign-in credentials are missing")
    return await oauth.google.authorize_redirect(request, REDIRECT_URI)


@app.get("/auth/google/callback")
async def google_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        info = token.get("userinfo")
        if not info or not info.get("email_verified"):
            raise ValueError("Email must be verified")
        allowed = {x.strip().lower() for x in os.getenv("ATLAS_ALLOWED_EMAILS", "").split(",") if x.strip()}
        if allowed and info["email"].lower() not in allowed:
            raise ValueError("Account not allowed")
        uid = hashlib.sha256(info["sub"].encode()).hexdigest()[:24]
        identity = {
            "uid": uid,
            "name": info.get("name", info["email"]),
            "email": info["email"],
            "role": "owner",
            "tenant": "u_" + uid,
            "auth_type": "google",
        }
        with storage.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO accounts VALUES(?,?,?,?)",
                (uid, info["email"], identity["name"], info.get("picture", "")),
            )
        await asyncio.to_thread(seed, identity["tenant"])
        establish(request, identity)
        return RedirectResponse(BASE_URL)
    except Exception:
        return RedirectResponse(
            BASE_URL + "/?auth_error=Google+sign-in+failed.+Check+the+client,+test+users,+and+redirect+URI."
        )


@app.post("/auth/logout")
def logout(request: Request, identity=Depends(user)):
    request.session.clear()
    return {"ok": True}


@app.get("/api/graph")
def graph_data(identity=Depends(user)):
    return g.snapshot(identity)


@app.get("/api/decisions")
def decision_data(identity=Depends(user)):
    return g.decisions(identity)


@app.get("/api/repositories/{repository_id}/graph")
def repository_graph(repository_id: str, identity=Depends(user)):
    from .integrations import code_subgraph

    return code_subgraph(identity, repository_id)


@app.get("/api/sources/{source_id}/history")
def source_history(source_id: str, identity=Depends(user)):
    return g.source_history(identity, source_id)


class Scenario(BaseModel):
    scenario: str


@app.post("/api/scenarios")
def scenario(body: Scenario, identity=Depends(editor)):
    return apply_scenario(identity, body.scenario)


class SourceUpdate(BaseModel):
    content: str = Field(max_length=30000)
    metadata: dict
    expected_revision: int


@app.put("/api/sources/{source_id}")
def update_source(source_id: str, body: SourceUpdate, identity=Depends(editor)):
    history = g.source_history(identity, source_id)
    current = history[0]
    revision, changed = g.put_source(
        identity["tenant"],
        current["scope"],
        source_id,
        current["title"],
        body.content,
        body.metadata,
        body.expected_revision,
    )
    return {
        "revision": revision,
        "changed": changed,
        "affected_decisions": g.evaluate(identity["tenant"], current["scope"], source_id) if changed else [],
    }


@app.get("/api/providers")
def providers(identity=Depends(user)):
    return models.provider_status(identity)


class KeyBody(BaseModel):
    key: str = Field(min_length=10, max_length=500)


@app.put("/api/providers/{provider}")
def connect(provider: str, body: KeyBody, identity=Depends(owner)):
    if provider not in models.ENV_KEYS:
        raise ValueError("Unknown provider")
    key = body.key.strip()
    if len(key) < 10:
        raise ValueError("Enter a valid API key without surrounding spaces")
    storage.save_key(identity["uid"], provider, key)
    return {"connected": True}


@app.delete("/api/providers/{provider}")
def disconnect(provider: str, identity=Depends(owner)):
    if provider not in models.ENV_KEYS:
        raise ValueError("Unknown provider")
    storage.save_key(identity["uid"], provider, "")
    return {"ok": True, "server_key_available": bool(os.getenv(models.ENV_KEYS[provider]))}


@app.post("/api/providers/{provider}/server")
def reconnect_server(provider: str, identity=Depends(owner)):
    if provider not in models.ENV_KEYS or not os.getenv(models.ENV_KEYS[provider]):
        raise ValueError("No server credential is available for this provider")
    storage.delete_key(identity["uid"], provider)
    return {"connected": True}


@app.get("/api/providers/{provider}/models")
async def provider_models(provider: str, identity=Depends(owner)):
    if provider not in models.ENV_KEYS:
        raise ValueError("Unknown provider")
    return {"models": await models.discover(identity, provider)}


@app.get("/api/budget")
def budget(identity=Depends(owner)):
    return storage.budget()


@app.get("/api/tasks")
def tasks(identity=Depends(user)):
    return g.list_tasks(identity)


@app.get("/api/tasks/{task_id}")
def task(task_id: str, identity=Depends(user)):
    value = g.get_task(identity, task_id)
    return {**value, "evidence_changes": g.revalidate(identity, value)}


class Chat(BaseModel):
    question: str = Field(min_length=2, max_length=5000)
    provider: Literal["openai", "anthropic", "gemini"]
    model: str = Field(min_length=1, max_length=150, pattern=r"^[A-Za-z0-9_.:-]+$")
    task_id: str | None = None
    expected_revision: int | None = None
    role: str = "investigator"


@app.post("/api/chat")
async def chat(body: Chat, identity=Depends(editor)):
    if body.role not in {"investigator", "planner"}:
        raise ValueError("Unknown agent role")
    state = await asyncio.to_thread(g.get_task, identity, body.task_id) if body.task_id else None
    if state and body.expected_revision != state["revision"]:
        raise ValueError("A newer checkpoint exists. Reload before continuing.")
    if state is not None:
        state.setdefault("constraints", TASK_CONSTRAINTS.copy())
    evidence = await asyncio.to_thread(g.context, identity, body.question)
    from .integrations import retrieve_documents

    evidence["document_retrieval"] = await retrieve_documents(identity, body.question)
    changes = await asyncio.to_thread(g.revalidate, identity, state) if state else []
    if state:
        state = {**state, "evidence_changes": changes}
    memory = []
    from .integrations import recall

    try:
        memory = await recall(identity, body.question)
    except Exception:
        memory = []
    text, usage = await models.answer(
        identity, body.provider, body.model, body.question, evidence, state, memory, body.role
    )
    saved = state or {
        "objective": body.question,
        "messages": [],
        "completed_steps": [],
        "open_questions": [],
        "actions": [],
        "handoffs": [],
        "status": "in_progress",
        "constraints": TASK_CONSTRAINTS.copy(),
    }
    saved["messages"] = saved["messages"] + [
        {"role": "user", "content": body.question, "time": storage.now()},
        {
            "role": "assistant",
            "content": text,
            "provider": body.provider,
            "model": body.model,
            "time": storage.now(),
            "agent_role": body.role,
        },
    ]
    if state and (
        state.get("provider") != body.provider
        or state.get("model") != body.model
        or state.get("agent_role") != body.role
    ):
        saved["handoffs"].append(
            {
                "from": state.get("provider"),
                "to": body.provider,
                "role": body.role,
                "time": storage.now(),
                "evidence_changes": changes,
            }
        )
    import re

    cited = sorted({(id, int(revision)) for id, revision in re.findall(r"\b([A-Za-z0-9_-]+)@(\d+)\b", text)})
    source_map = {s["id"]: s for s in evidence["sources"]}
    references, citation_warnings = [], []
    for source_id, revision in cited:
        source = source_map.get(source_id)
        if not source or not 1 <= revision <= source["revision"]:
            citation_warnings.append(f"{source_id}@{revision}: source revision could not be verified")
            continue
        references.append({"source_id": source_id, "revision": revision})
        if revision != source["revision"]:
            citation_warnings.append(
                f"{source_id}@{revision}: historical evidence; current revision is {source['revision']}"
            )
    open_questions = saved.get("open_questions", []).copy()
    review_question = "Review any failed decision conditions before approving next steps."
    if (
        any(d["status"] == "needs_review" for d in evidence["decisions"])
        and review_question not in open_questions
    ):
        open_questions.append(review_question)
    saved.update(
        {
            "provider": body.provider,
            "model": body.model,
            "agent_role": body.role,
            "required_scopes": list(
                set((saved.get("required_scopes", [])) + [s["scope"] for s in evidence["sources"]])
            ),
            "evidence": references,
            "citation_warnings": citation_warnings,
            "repository_evidence": [
                {key: r.get(key) for key in ("repository_id", "graph", "commit")}
                for r in evidence.get("repository_structure", [])
                if not r.get("unavailable")
            ],
            "latest_findings": text,
            "open_questions": open_questions,
            "evidence_changes": [],
            "episode_status": "pending",
            "episode_key": None,
        }
    )
    saved["completed_steps"].append(
        f"{body.role.title()} reviewed current evidence using {body.provider}/{body.model}"
    )
    for action in saved["actions"]:
        if action.get("status") == "proposed" and action.get("evidence") and g.revalidate(identity, action):
            action["status"] = "superseded"
    for decision in evidence["decisions"]:
        if decision["status"] != "needs_review":
            continue
        references = sorted(
            [
                {"source_id": c["source_id"], "revision": c["revision"]}
                for c in decision["checks"]
                if c["source_id"]
            ],
            key=lambda c: c["source_id"],
        )
        action_id = hashlib.sha256(
            (decision["id"] + json.dumps(references, sort_keys=True)).encode()
        ).hexdigest()[:24]
        if not any(a["id"] == action_id for a in saved["actions"]):
            saved["actions"].append(
                {
                    "id": action_id,
                    "title": "Investigate conditions for " + decision["title"],
                    "target": decision["id"],
                    "owner": decision["owner"],
                    "evidence": references,
                    "status": "proposed",
                }
            )
    committed = await asyncio.to_thread(
        g.save_task, identity, saved, body.expected_revision if body.task_id else None
    )
    return {
        "task": committed,
        "answer": text,
        "evidence": evidence,
        "revalidated_changes": changes,
        "usage": usage,
    }


class Approval(BaseModel):
    expected_revision: int


@app.post("/api/tasks/{task_id}/actions/{action_id}/approve")
def approve(task_id: str, action_id: str, body: Approval, identity=Depends(editor)):
    state, duplicate = g.approve_action(identity, task_id, action_id, body.expected_revision)
    return {"task": state, "duplicate": duplicate}


@app.get("/api/jobs")
def jobs(identity=Depends(user)):
    return storage.jobs(identity["uid"])


class RepositoryImport(BaseModel):
    url: str = Field(max_length=300)


class ImportedEntity(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    kind: str
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(default="", max_length=30000)
    properties: dict = Field(default_factory=dict)


class ImportedSource(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(max_length=30000)
    metadata: dict = Field(default_factory=dict)


class ImportedEdge(BaseModel):
    source: str
    target: str
    kind: str


class RecordsImport(BaseModel):
    scope: str = "public"
    entities: list[ImportedEntity] = Field(default_factory=list, max_length=100)
    sources: list[ImportedSource] = Field(default_factory=list, max_length=50)
    relationships: list[ImportedEdge] = Field(default_factory=list, max_length=300)


@app.post("/api/import/records")
def import_records(body: RecordsImport, identity=Depends(owner)):
    with g.LOCK:
        return _import_records(body, identity)


def _import_records(body, identity):
    if body.scope not in g.scopes(identity):
        raise HTTPException(403, "Scope not accessible")
    imported_ids = [e.id for e in body.entities] + [s.id for s in body.sources]
    if len(imported_ids) != len(set(imported_ids)):
        raise ValueError("Every imported entity and source must have a unique ID")
    nodes = g.snapshot(identity)["nodes"]
    if set(imported_ids) & {n["id"] for n in nodes if n["scope"] != body.scope}:
        raise ValueError("Entity and source IDs must be unique across workspace scopes")
    existing = {n["id"]: n["kind"] for n in nodes if n["scope"] == body.scope}
    ids = set(existing) | {e.id for e in body.entities} | {s.id for s in body.sources}
    for e in body.entities:
        if e.kind not in g.KINDS - {"Source"}:
            raise ValueError("Use a supported entity kind; source records belong in the sources list")
        if e.id in existing and existing[e.id] != e.kind:
            raise ValueError("An existing entity's kind cannot be changed")
        if e.kind == "Fact" and (
            not isinstance(e.properties.get("field"), str) or "equals" not in e.properties
        ):
            raise ValueError("A decision fact requires a field and an equals value")
    for e in body.relationships:
        if e.kind not in g.EDGE_TYPES or e.source not in ids or e.target not in ids:
            raise ValueError("Every relationship must use supported endpoints and a supported type")
    for s in body.sources:
        if s.id in existing and existing[s.id] != "Source":
            raise ValueError("A source ID cannot overwrite another entity kind")
    job_id = storage.job(identity["uid"], "records_import")
    try:
        with g.LOCK:
            for e in body.entities:
                g.add_entity(identity["tenant"], body.scope, e.model_dump())
            for s in body.sources:
                g.put_source(identity["tenant"], body.scope, s.id, s.title, s.content, s.metadata)
            for e in body.relationships:
                g.add_edge(identity["tenant"], body.scope, e.source, e.target, e.kind)
            reviews = g.evaluate(identity["tenant"], body.scope)
        detail = {
            "entities": len(body.entities),
            "sources": len(body.sources),
            "relationships": len(body.relationships),
            "reviews": len(reviews),
        }
        storage.job(identity["uid"], "records_import", "completed", detail, job_id)
        return detail
    except Exception:
        storage.job(
            identity["uid"],
            "records_import",
            "failed",
            {"message": "Import interrupted. Inspect the committed source revisions before retrying."},
            job_id,
        )
        raise ValueError(
            "Records import was interrupted; inspect the committed records and retry safely."
        ) from None


@app.post("/api/import/repository")
async def import_repository(body: RepositoryImport, identity=Depends(owner)):
    from .integrations import index_repository

    return await index_repository(identity, body.url)


@app.get("/api/github/issues")
async def github_issues(
    repository: str = Query(min_length=3, max_length=250),
    state: Literal["open", "closed", "all"] = "open",
    page: int = Query(default=1, ge=1, le=100),
    identity=Depends(user),
):
    with github.read_credentials(identity):
        return await github.list_issues(repository, state, page)


@app.get("/api/github/connection")
async def github_connection(identity=Depends(owner)):
    return await github.connection(identity)


@app.put("/api/github/connection")
async def connect_github(body: KeyBody, identity=Depends(owner)):
    return await github.connect(identity, body.key)


@app.delete("/api/github/connection")
def disconnect_github(identity=Depends(owner)):
    storage.delete_key(identity["uid"], "github")
    return {"connected": False}


class ChangeStart(BaseModel):
    repository: str = Field(min_length=3, max_length=250)
    number: int = Field(ge=1, le=2147483647)


class ChangeProposal(BaseModel):
    expected_revision: int = Field(ge=1)
    paths: list[str] = Field(default_factory=list, max_length=6)
    provider: str = Field(max_length=20)
    model: str = Field(min_length=1, max_length=150, pattern=r"^[A-Za-z0-9_.:-]+$")


class ChangeEdit(BaseModel):
    expected_revision: int = Field(ge=1)
    files: list[dict] = Field(min_length=1, max_length=8)


class ChangeValidation(BaseModel):
    expected_revision: int = Field(ge=1)
    mode: Literal["pytest", "unittest"] = "pytest"
    test_path: str = Field(default="tests", min_length=1, max_length=300)


class ChangePublication(BaseModel):
    expected_revision: int = Field(ge=1)
    destination: str = Field(min_length=3, max_length=200)
    approved_patch: str = Field(min_length=64, max_length=64)
    allow_unverified_draft: bool = Field(default=False, strict=True)


class ChangeCI(ChangePublication):
    workflow_id: int | None = Field(default=None, ge=1)
    trigger: Literal["push", "pull_request"] = "push"


class ChangeRevision(BaseModel):
    expected_revision: int = Field(ge=1)


@app.get("/api/changes")
def list_changes(identity=Depends(user)):
    return changes.listing(identity)


@app.post("/api/changes")
async def start_change(body: ChangeStart, identity=Depends(owner)):
    with github.read_credentials(identity):
        return await changes.create(identity, body.repository, body.number)


@app.post("/api/changes/demo")
def sample_change(identity=Depends(owner)):
    if not DEMO_ENABLED:
        raise HTTPException(404, "Sample workspaces are disabled")
    return changes.create_demo(identity)


@app.get("/api/changes/{run_id}")
def change_details(run_id: str, identity=Depends(user)):
    return changes.get(identity, run_id)


@app.post("/api/changes/{run_id}/propose")
async def propose_change(run_id: str, body: ChangeProposal, identity=Depends(owner)):
    with github.read_credentials(identity):
        return await changes.propose(
            identity, run_id, body.expected_revision, body.paths, body.provider, body.model
        )


@app.put("/api/changes/{run_id}/patch")
def edit_change(run_id: str, body: ChangeEdit, identity=Depends(owner)):
    return changes.edit(identity, run_id, body.expected_revision, body.files)


@app.get("/api/changes/{run_id}/patch")
def download_patch(run_id: str, identity=Depends(user)):
    state = changes.get(identity, run_id)
    return Response(
        state.get("diff", ""),
        media_type="text/x-diff",
        headers={"Content-Disposition": f"attachment; filename=atlas-{state['id'][:8]}.patch"},
    )


@app.get("/api/changes/{run_id}/file")
async def change_source_file(run_id: str, path: str, identity=Depends(user)):
    with github.read_credentials(identity):
        state = changes.get(identity, run_id)
        if state.get("is_demo"):
            if path not in state["demo_source"]:
                raise LookupError("Sample source file not found")
            return {"path": path, "content": state["demo_source"][path], "base_sha": state["base_sha"]}
        files = await github.read_files(state["repository"], {v["path"]: v for v in state["tree"]}, [path])
        return {"path": path, "content": files[path], "base_sha": state["base_sha"]}


@app.post("/api/changes/{run_id}/validate")
async def validate_change(run_id: str, body: ChangeValidation, identity=Depends(owner)):
    with github.read_credentials(identity):
        return await changes.validate(identity, run_id, body.expected_revision, body.mode, body.test_path)


@app.post("/api/changes/{run_id}/fork")
async def fork_change(run_id: str, identity=Depends(owner)):
    return await changes.create_fork(identity, run_id)


@app.get("/api/changes/{run_id}/ci/workflows")
async def change_workflows(run_id: str, destination: str, identity=Depends(owner)):
    changes.get(identity, run_id)
    return await changes.ci_workflows(identity, destination)


@app.post("/api/changes/{run_id}/ci")
async def start_change_ci(run_id: str, body: ChangeCI, identity=Depends(owner)):
    with github.read_credentials(identity):
        state = changes.get(identity, run_id, body.expected_revision)
        if body.approved_patch != state.get("patch_hash"):
            raise ValueError("Review the exact proposal before pushing a public CI branch.")
        return await changes.start_ci(
            identity, run_id, body.expected_revision, body.destination, body.workflow_id, body.trigger
        )


@app.post("/api/changes/{run_id}/ci/refresh")
async def refresh_change_ci(run_id: str, body: ChangeRevision, identity=Depends(owner)):
    return await changes.refresh_ci(identity, run_id, body.expected_revision)


@app.post("/api/changes/{run_id}/publish")
async def publish_change(run_id: str, body: ChangePublication, identity=Depends(owner)):
    state = changes.get(identity, run_id, body.expected_revision)
    if body.approved_patch != state.get("patch_hash"):
        raise ValueError("Review and approve the current patch before publishing.")
    with github.read_credentials(identity):
        return await changes.publish(
            identity, run_id, body.expected_revision, body.destination, body.allow_unverified_draft
        )


@app.get("/api/github/issues/{number}")
async def github_issue(
    number: int,
    repository: str = Query(min_length=3, max_length=250),
    identity=Depends(user),
):
    with github.read_credentials(identity):
        return await github.issue_details(repository, number)


class GitHubRepository(BaseModel):
    repository: str = Field(min_length=3, max_length=250)


@app.post("/api/github/issues/{number}/import")
async def import_github_issue(number: int, body: GitHubRepository, identity=Depends(owner)):
    with github.read_credentials(identity):
        return await github.import_issue(identity, body.repository, number)


@app.post("/api/import/document")
async def import_document(file: UploadFile = File(...), identity=Depends(owner)):
    from .integrations import ingest_document

    content = await file.read(100001)
    if (
        len(content) > 100000
        or not file.filename
        or Path(file.filename).suffix.lower() not in {".md", ".txt"}
    ):
        raise ValueError("Upload a Markdown or text document under 100 KB")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Document must be UTF-8 text") from None
    return await ingest_document(identity, Path(file.filename).name, text)


@app.post("/api/tasks/{task_id}/remember")
async def remember(task_id: str, identity=Depends(editor)):
    from .integrations import remember_task

    return await remember_task(identity, g.get_task(identity, task_id))


class MemoryQuery(BaseModel):
    query: str = Field(default="current work", min_length=2, max_length=500)


@app.post("/api/memory")
async def memory(body: MemoryQuery, identity=Depends(user)):
    from .integrations import recall

    return {"facts": await recall(identity, body.query)}


@app.get("/api/integrations")
def integrations(identity=Depends(user)):
    with storage.connection() as db:
        rows = db.execute(
            "SELECT kind,status,detail FROM jobs WHERE uid=? ORDER BY created_at DESC", (identity["uid"],)
        ).fetchall()
    return {
        "components": [
            "FalkorDB",
            "Code-Graph 0.4.2",
            "GraphRAG SDK 1.4.0",
            "FalkorDB Canvas",
            "Graphiti 0.31.1",
        ],
        "jobs": [{**dict(r), "detail": json.loads(r["detail"])} for r in rows],
    }


DIST = ROOT / "frontend" / "dist"
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/atlas.svg")
def atlas_icon():
    return FileResponse(DIST / "atlas.svg", media_type="image/svg+xml")


@app.get("/{path:path}")
def frontend(path: str):
    if path.startswith(("api/", "auth/")):
        raise HTTPException(404)
    if DIST.exists():
        return FileResponse(DIST / "index.html")
    return JSONResponse({"message": "Atlas backend is running. Build the frontend or run Vite on port 5173."})
