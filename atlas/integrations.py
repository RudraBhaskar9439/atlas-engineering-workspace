"""Sponsor-owned ingestion and code analysis; Graphiti is limited to temporal episodes."""

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from datetime import UTC, datetime

from . import graph as g
from . import models, storage
from .config import DATA, DB_HOST, DB_PASSWORD, DB_PORT, ROOT

DOCUMENT_LOCK = asyncio.Lock()
REPOSITORY_LOCK = asyncio.Lock()


def code_subgraph(user, repository_id):
    from falkordb import FalkorDB

    record = next(
        (n for n in g.snapshot(user)["nodes"] if n["id"] == repository_id and n["kind"] == "Repository"), None
    )
    if not record:
        raise LookupError("Repository not found")
    name = record["properties"].get("graph", "")
    pattern = rf"code:atlas_{re.escape(user['tenant'])}_[a-f0-9]{{10}}:_default"
    if not re.fullmatch(pattern, name):
        raise LookupError("No authorized source graph is linked to this repository")
    db = FalkorDB(host=DB_HOST, port=DB_PORT, password=DB_PASSWORD).select_graph(name)
    rows = db.query("MATCH(n) RETURN id(n),labels(n),properties(n) LIMIT 200").result_set
    nodes = []
    for id, labels, props in rows:
        props = {k: v for k, v in props.items() if not k.endswith("embedding")}
        if "path" in props:
            marker = "/analysis/" + name.split(":")[1] + "/"
            props["path"] = (
                str(props["path"]).split(marker, 1)[1]
                if marker in str(props["path"])
                else Path(str(props["path"])).name
            )
        nodes.append(
            {
                "id": "code-" + str(id),
                "kind": next((label for label in labels if label != "Searchable"), "Code"),
                "title": props.get("name", str(id)),
                "properties": props,
                "scope": record["scope"],
                "content": props.get("docstring", ""),
            }
        )
    ids = {n["id"] for n in nodes}
    rows = db.query("MATCH(a)-[r]->(b) RETURN id(a),id(b),type(r) LIMIT 600").result_set
    edges = [
        {"id": f"code-{a}-{kind}-{b}", "source": "code-" + str(a), "target": "code-" + str(b), "kind": kind}
        for a, b, kind in rows
        if "code-" + str(a) in ids and "code-" + str(b) in ids
    ]
    return {"nodes": nodes, "edges": edges, "repository": record["title"]}


def document_rag(user, scope="public"):
    from graphrag_sdk import ConnectionConfig, GraphRAG, LiteLLM, LiteLLMEmbedder

    key = models.credential(user, "gemini")
    if not key:
        raise ValueError("Document ingestion needs the fixed Gemini extraction and embedding connection.")
    return GraphRAG(
        connection=ConnectionConfig(
            host=DB_HOST,
            port=DB_PORT,
            password=DB_PASSWORD,
            graph_name=f"atlas_docs_{user['tenant']}_{scope}",
        ),
        llm=LiteLLM(model="gemini/gemini-2.5-flash", api_key=key, max_tokens=6000),
        embedder=LiteLLMEmbedder(model="gemini/gemini-embedding-001", api_key=key),
        embedding_dimension=3072,
    )


async def ingest_document(user, filename, text):
    async with DOCUMENT_LOCK:
        return await _ingest_document(user, filename, text)


async def _ingest_document(user, filename, text):
    if not text.strip():
        raise ValueError("Document is empty")
    source_id = "DOC-" + hashlib.sha256(filename.encode()).hexdigest()[:12].upper()
    revision, changed = await asyncio.to_thread(
        g.put_source,
        user["tenant"],
        "public",
        source_id,
        filename,
        text,
        {"type": "document", "loader": "GraphRAG SDK"},
    )
    completed = any(
        j["kind"] == "graphrag_ingestion"
        and j["status"] == "completed"
        and j["detail"].get("source_id") == source_id
        and j["detail"].get("revision") == revision
        for j in storage.jobs(user["uid"])
    )
    if not changed and completed:
        return {"source_id": source_id, "revision": revision, "unchanged": True}
    path = DATA / "sources" / user["tenant"] / f"{source_id}@{revision}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    reservation = storage.reserve(user["uid"], "graphrag:document", 70)
    job_id = storage.job(
        user["uid"], "graphrag_ingestion", detail={"source_id": source_id, "revision": revision}
    )
    try:
        async with document_rag(user) as rag:
            from graphrag_sdk import LLMExtractor

            result = await rag.update(
                text=text, document_id=source_id, if_missing="ingest", extractor=LLMExtractor(rag.llm)
            )
        summary = {
            "source_id": source_id,
            "revision": revision,
            "engine": "FalkorDB GraphRAG SDK",
            "result": str(result)[:1000],
        }
        storage.job(user["uid"], "graphrag_ingestion", "completed", summary, job_id)
        storage.finish_reservation(
            reservation, True, "Document processed; embeddings stay fixed across chat models"
        )
        return summary
    except Exception as error:
        storage.job(
            user["uid"],
            "graphrag_ingestion",
            "failed",
            {
                "message": "Source revision preserved; SDK indexing failed. Check provider quota and retry.",
                "error_type": type(error).__name__,
            },
            job_id,
        )
        storage.finish_reservation(reservation, False, type(error).__name__)
        raise ValueError(
            "GraphRAG indexing failed. The immutable source revision is preserved; check the API connection and budget."
        ) from None


async def retrieve_documents(user, question):
    with storage.connection() as db:
        exists = db.execute(
            "SELECT 1 FROM jobs WHERE uid=? AND kind='graphrag_ingestion' AND status='completed' LIMIT 1",
            (user["uid"],),
        ).fetchone()
    if not exists:
        return []
    reservation = storage.reserve(user["uid"], "graphrag:retrieve", 2)
    try:
        async with document_rag(user) as rag:
            result = await rag.retrieve(question)
        storage.finish_reservation(reservation, True)
        if hasattr(result, "model_dump"):
            result = result.model_dump(exclude_none=True)
        elif hasattr(result, "__dict__"):
            result = vars(result)
        return [str(result)[:10000]]
    except Exception as error:
        storage.finish_reservation(reservation, False, type(error).__name__)
        return []


def memory_database(user):
    return "atlas_memory_" + hashlib.sha256((user["tenant"] + ":" + user["uid"]).encode()).hexdigest()[:24]


def graphiti_client(user):
    from graphiti_core import Graphiti
    from graphiti_core.driver.falkordb_driver import FalkorDriver
    from graphiti_core.llm_client.gemini_client import GeminiClient
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
    from graphiti_core.cross_encoder.gemini_reranker_client import GeminiRerankerClient
    from google.genai import types

    key = models.credential(user, "gemini")
    if not key:
        raise ValueError(
            "Temporal memory extraction needs the fixed Gemini connection. Chat can use any connected provider."
        )
    config = LLMConfig(
        api_key=key, model="gemini-2.5-flash", small_model="gemini-2.5-flash", max_tokens=3000, temperature=0
    )
    database = memory_database(user)
    return Graphiti(
        graph_driver=FalkorDriver(host=DB_HOST, port=DB_PORT, password=DB_PASSWORD, database=database),
        llm_client=GeminiClient(config=config, thinking_config=types.ThinkingConfig(thinking_budget=0)),
        embedder=GeminiEmbedder(
            GeminiEmbedderConfig(api_key=key, embedding_model="gemini-embedding-001", embedding_dim=3072)
        ),
        cross_encoder=GeminiRerankerClient(config=config),
        max_coroutines=1,
    )


async def remember_task(user, state):
    from graphiti_core.nodes import EpisodeType
    from uuid import NAMESPACE_URL, uuid5

    with g.LOCK:
        current = g.get_task(user, state["id"])
        if current["revision"] != state["revision"]:
            raise ValueError("A newer checkpoint exists. Reload before saving an episode.")
        if current.get("episode_status") == "completed":
            return {"status": "already_saved"}
        if current.get("episode_status") == "processing":
            with storage.connection() as db:
                active = db.execute(
                    "SELECT 1 FROM jobs WHERE uid=? AND kind='graphiti_episode' AND status='running' AND json_extract(detail,'$.task_id')=? LIMIT 1",
                    (user["uid"], state["id"]),
                ).fetchone()
            if active:
                raise ValueError(
                    "Episode ingestion is already processing. Check the integration job before retrying."
                )
        reservation = storage.reserve(user["uid"], "graphiti:episode", 45)
        stable_id = current.get("episode_key") or str(
            uuid5(NAMESPACE_URL, state["id"] + str(state.get("revision")))
        )
        current["episode_key"] = stable_id
        current["episode_status"] = "processing"
        current = g.save_task(user, current, current["revision"])
        job_id = storage.job(
            user["uid"], "graphiti_episode", detail={"task_id": state["id"], "episode_key": stable_id}
        )
    client = None
    try:
        client = graphiti_client(user)
        await client.build_indices_and_constraints()
        from falkordb import FalkorDB

        memory_graph = FalkorDB(host=DB_HOST, port=DB_PORT, password=DB_PASSWORD).select_graph(
            memory_database(user)
        )
        known = memory_graph.query(
            "MATCH(e:Episodic {name:$name}) RETURN e.uuid LIMIT 1",
            {"name": "Atlas task checkpoint " + stable_id},
        ).result_set
        body = json.dumps(
            {
                "task": state["objective"],
                "findings": state.get("latest_findings", "")[:900],
                "completed_steps": state.get("completed_steps", [])[-3:],
                "evidence": state.get("evidence", [])[:6],
            }
        )
        added = await client.add_episode(
            name="Atlas task checkpoint " + stable_id,
            episode_body=body,
            source_description="Atlas observed task progress; not authoritative company state",
            reference_time=datetime.now(UTC),
            source=EpisodeType.text,
            group_id=memory_database(user),
            uuid=known[0][0] if known else None,
        )
        episode_id = added.episode.uuid
        with g.LOCK:
            current = g.get_task(user, state["id"])
            current_checkpoint_saved = current.get("episode_key") == stable_id
            if current_checkpoint_saved:
                current["episode_status"] = "completed"
                current["episode_id"] = episode_id
                g.save_task(user, current, current["revision"])
        storage.job(
            user["uid"],
            "graphiti_episode",
            "completed",
            {"task_id": state["id"], "episode_id": episode_id, "checkpoint_revision": state["revision"]},
            job_id,
        )
        storage.finish_reservation(reservation, True)
        return {
            "status": "completed",
            "episode_id": episode_id,
            "current_checkpoint_saved": current_checkpoint_saved,
        }
    except Exception as error:
        with g.LOCK:
            current = g.get_task(user, state["id"])
            if current.get("episode_key") == stable_id:
                current["episode_status"] = "pending"
                g.save_task(user, current, current["revision"])
        storage.job(
            user["uid"],
            "graphiti_episode",
            "failed",
            {
                "message": "Exact checkpoint preserved; temporal episode extraction failed.",
                "error_type": type(error).__name__,
            },
            job_id,
        )
        storage.finish_reservation(reservation, False, type(error).__name__)
        raise ValueError(
            "Temporal memory extraction failed. Exact task progress is still saved; check the model connection."
        ) from None
    finally:
        if client:
            await client.close()


async def recall(user, question):
    with storage.connection() as db:
        exists = db.execute(
            "SELECT 1 FROM jobs WHERE uid=? AND kind='graphiti_episode' AND status='completed' LIMIT 1",
            (user["uid"],),
        ).fetchone()
    if not exists:
        return []
    reservation = storage.reserve(user["uid"], "graphiti:recall", 3)
    client = None
    try:
        client = graphiti_client(user)
        results = await client.search(question, group_ids=[memory_database(user)], num_results=5)
        storage.finish_reservation(reservation, True)
        return [
            {"fact": r.fact, "valid_at": r.valid_at.isoformat() if r.valid_at else None, "uuid": r.uuid}
            for r in results
        ]
    except Exception as error:
        storage.finish_reservation(reservation, False, type(error).__name__)
        return []
    finally:
        if client:
            await client.close()


async def git_output(*args):
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        *map(str, args),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        },
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), 120)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ValueError("Repository Git operation exceeded the time limit") from None
    if proc.returncode:
        raise ValueError("Repository Git operation failed. Check the public URL and accessibility.")
    return output.decode(errors="replace")


async def index_repository(user, url):
    async with REPOSITORY_LOCK:
        return await _index_repository(user, url)


async def _index_repository(user, url):
    if url == "demo":
        target = DATA / "fixtures" / "orbit-api"
        target.mkdir(parents=True, exist_ok=True)
        sources = list((ROOT / "demo" / "orbit-api").glob("*.py"))
        for stale in target.glob("*.py"):
            if stale.name not in {p.name for p in sources}:
                stale.unlink()
        for source in sources:
            shutil.copyfile(source, target / source.name)
        if not (target / ".git").exists():
            for command in [
                ["git", "init", "-b", "main", str(target)],
                ["git", "-C", str(target), "add", "auth.py", "billing.py"],
                [
                    "git",
                    "-C",
                    str(target),
                    "-c",
                    "user.name=Noah Williams",
                    "-c",
                    "user.email=noah@orbit.example",
                    "commit",
                    "-m",
                    "Fix refresh-token rotation and add annual billing fixture",
                ],
            ]:
                await git_output(*command[1:])
        elif await git_output("-C", target, "status", "--porcelain", "--", "*.py"):
            await git_output("-C", target, "add", "--", "*.py")
            await git_output(
                "-C",
                target,
                "-c",
                "user.name=Noah Williams",
                "-c",
                "user.email=noah@orbit.example",
                "commit",
                "-m",
                "Update synthetic repository evidence",
            )
    else:
        if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?", url):
            raise ValueError(
                "Use a public GitHub HTTPS repository URL without credentials or query parameters"
            )
        folder = hashlib.sha256(url.encode()).hexdigest()[:20]
        target = DATA / "repositories" / user["tenant"] / folder
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
                checkout = Path(temporary) / "checkout"
                await git_output("clone", "--depth", "1", "--filter=blob:limit=1m", "--", url, checkout)
                checkout.rename(target)
        else:
            # This is Atlas's disposable import cache, never the user's original repository.
            await git_output(
                "-C", target, "fetch", "--depth", "1", "--no-recurse-submodules", "origin", "HEAD"
            )
            await git_output("-C", target, "reset", "--hard", "FETCH_HEAD")
    total = sum(p.stat().st_size for p in target.rglob("*") if p.is_file() and not p.is_symlink())
    if any(
        p.is_symlink()
        for p in target.rglob("*")
        if not set(p.relative_to(target).parts) & {".git", "venv", ".venv", "node_modules"}
    ):
        raise ValueError("Repository symlinks are not supported by the isolated analyser")
    if total > 100_000_000:
        raise ValueError("Repository exceeds the 100 MB analysis limit")
    executable = ROOT / ".tools" / "codegraph" / "bin" / "cgraph"
    if not executable.exists():
        raise ValueError("Code-Graph runtime is missing. Run scripts/setup.sh first.")
    history = await git_output("-C", target, "log", "-5", "--format=%H|%an|%aI|%s")
    commits = [
        {"sha": p[0], "author": p[1], "time": p[2], "message": p[3]}
        for line in history.splitlines()
        if len(p := line.split("|", 3)) == 4
    ]
    if not commits:
        raise ValueError("Repository has no readable committed revision")
    canonical = "synthetic fixture" if url == "demo" else url.rstrip("/").removesuffix(".git")
    name = (
        "atlas_"
        + user["tenant"]
        + "_"
        + hashlib.sha256((canonical + commits[0]["sha"]).encode()).hexdigest()[:10]
    )
    # Only source snapshots enter the analyzer. No dependency manifests or executable build hooks are copied.
    analysis = DATA / "analysis" / name
    analysis.mkdir(parents=True, exist_ok=True)
    count = 0
    for folder, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in {".git", "venv", ".venv", "node_modules", "__pycache__"}]
        for filename in files:
            source = Path(folder) / filename
            if source.suffix != ".py":
                continue
            if source.is_symlink() or source.stat().st_size > 256000:
                raise ValueError("Source files must be ordinary files under 256 KB")
            count += 1
            if count > 1000:
                raise ValueError("Repository exceeds the 1,000 Python-file analysis limit")
            destination = analysis / source.relative_to(target)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    if not count:
        raise ValueError("This build indexes Python repositories. No Python source files were found.")
    job_id = storage.job(user["uid"], "codegraph_index", detail={"repository": target.name})
    env = {
        **os.environ,
        "FALKORDB_HOST": DB_HOST,
        "FALKORDB_PORT": str(DB_PORT),
        "FALKORDB_PASSWORD": DB_PASSWORD or "",
        "CODE_GRAPH_PY_RESOLVER": "tree_sitter",
        "CODE_GRAPH_INDEX_WORKERS": "1",
        "PYTHON_DOTENV_DISABLED": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    }
    for key in [*models.ENV_KEYS.values(), "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]:
        env.pop(key, None)
    proc = await asyncio.create_subprocess_exec(
        str(executable),
        "index",
        str(analysis),
        "--repo",
        name,
        "--ignore",
        "node_modules",
        "--ignore",
        ".git",
        "--ignore",
        ".venv",
        "--ignore",
        "venv",
        cwd=ROOT,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), 180)
        if proc.returncode:
            raise ValueError("Code-Graph analysis failed; the source repository was not executed.")
        results = []
        for line in output.decode(errors="replace").splitlines():
            if line.startswith("{"):
                try:
                    value = json.loads(line)
                    if value.get("status") == "ok":
                        results.append(value)
                except json.JSONDecodeError:
                    continue
        if not results:
            raise ValueError("Code-Graph did not return a completed index result")
        result = results[-1]
        result["commits"] = commits
        result["indexed_python_files"] = count
        branch = result.get("branch", "_default")
        existing = next(
            (
                n
                for n in g.snapshot(user)["nodes"]
                if n["kind"] == "Repository" and n["properties"].get("url") in {url, canonical}
            ),
            None,
        )
        g.add_entity(
            user["tenant"],
            "public",
            {
                "id": existing["id"]
                if existing
                else "REPO-" + hashlib.sha256(canonical.encode()).hexdigest()[:20],
                "kind": "Repository",
                "title": target.name,
                "properties": {
                    "engine": "FalkorDB Code-Graph",
                    "graph": "code:" + name + ":" + branch,
                    "stats": result,
                    "url": canonical,
                    "commit": commits[0]["sha"],
                },
            },
        )
        storage.job(user["uid"], "codegraph_index", "completed", result, job_id)
        return result
    except TimeoutError:
        proc.kill()
        await proc.wait()
        storage.job(
            user["uid"],
            "codegraph_index",
            "failed",
            {"message": "Indexing exceeded the 180-second limit"},
            job_id,
        )
        raise ValueError("Repository analysis exceeded the time limit") from None
    except Exception as error:
        storage.job(
            user["uid"],
            "codegraph_index",
            "failed",
            {"message": "Repository indexing failed", "error_type": type(error).__name__},
            job_id,
        )
        raise ValueError(
            "Code-Graph indexing failed. Check the supported language and local runtime."
        ) from None
