import json
from uuid import uuid4

import pytest

from atlas import graph as g, storage
from atlas.fixture import apply_scenario


def state():
    return {
        "objective": "Investigate launch",
        "completed_steps": [],
        "open_questions": [],
        "evidence": [{"source_id": "AUTH-42", "revision": 1}],
        "actions": [{"id": "proposal-1", "title": "Investigate authentication", "status": "proposed"}],
        "messages": [],
        "required_scopes": ["public"],
    }


def test_dependency_review_provenance_and_unrelated_changes(identity):
    before = {d["id"]: len(d["history"]) for d in g.decisions(identity)}
    update = apply_scenario(identity, "regression")
    assert [d["id"] for d in update["affected_decisions"]] == ["decision-launch"]
    decisions = {d["id"]: d for d in g.decisions(identity)}
    assert decisions["decision-launch"]["status"] == "needs_review"
    assert decisions["decision-billing"]["status"] == "current"
    assert decisions["decision-launch"]["checks"][0]["source_id"] in {"AUTH-42", "VERIFY-AUTH"}
    history = g.source_history(identity, "AUTH-42")
    assert history[0]["metadata"]["status"] == "open" and history[1]["metadata"]["status"] == "closed"
    assert apply_scenario(identity, "regression")["changed"] is False
    counts = {d["id"]: len(d["history"]) for d in g.decisions(identity)}
    assert apply_scenario(identity, "unrelated")["affected_decisions"] == []
    assert counts == {d["id"]: len(d["history"]) for d in g.decisions(identity)}
    assert counts["decision-launch"] == before["decision-launch"] + 1


def test_private_checkpoint_restart_and_stale_writes(identity):
    saved = g.save_task(identity, state())
    assert g.get_task(dict(identity), saved["id"])["evidence"] == state()["evidence"]
    newer = g.save_task(identity, {**saved, "objective": "Newer progress"}, 1)
    assert newer["revision"] == 2
    with pytest.raises(ValueError, match="newer checkpoint"):
        g.save_task(identity, saved, 1)
    with pytest.raises(LookupError):
        g.get_task({**identity, "uid": "someone_else"}, saved["id"])
    assert g.get_task(identity, saved["id"])["objective"] == "Newer progress"


def test_changed_evidence_and_duplicate_actions(identity):
    saved = g.save_task(identity, state())
    completed, duplicate = g.approve_action(identity, saved["id"], "proposal-1", 1)
    assert not duplicate and completed["actions"][0]["outcome"]["type"] == "atlas_investigation"
    repeated, duplicate = g.approve_action(identity, saved["id"], "proposal-1", 1)
    assert duplicate and repeated["revision"] == completed["revision"]
    assert len(repeated["completed_steps"]) == 1
    apply_scenario(identity, "regression")
    assert g.revalidate(identity, saved)[0]["reason"] == "updated"
    another = g.save_task(identity, state())
    with pytest.raises(ValueError, match="Evidence changed"):
        g.approve_action(identity, another["id"], "proposal-1", 1)


def test_scope_isolation_in_graph_sources_and_memory(identity):
    viewer = {**identity, "role": "viewer"}
    assert "SECURITY-AUDIT" not in {n["id"] for n in g.snapshot(viewer)["nodes"]}
    with pytest.raises(LookupError):
        g.source_history(viewer, "SECURITY-AUDIT")
    private = g.save_task(identity, {**state(), "required_scopes": ["restricted"]})
    with pytest.raises(LookupError):
        g.get_task(viewer, private["id"])
    assert g.list_tasks(viewer) == []


def test_source_expected_revision(identity):
    with pytest.raises(ValueError, match="changed since"):
        g.put_source(identity["tenant"], "public", "AUTH-42", "Ticket", "bad", {}, 99)
    assert len(g.source_history(identity, "AUTH-42")) == 1


def test_csrf_and_viewer_restrictions(client):
    assert (
        client.post(
            "/api/scenarios", json={"scenario": "unrelated"}, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/scenarios", json={"scenario": "unrelated"}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    login = client.post("/auth/demo", json={"role": "viewer"}).json()
    client.headers["X-CSRF-Token"] = login["csrf"]
    assert client.post("/api/scenarios", json={"scenario": "regression"}).status_code == 403
    assert client.get("/api/sources/SECURITY-AUDIT/history").status_code == 404
    assert client.get("/api/budget").status_code == 403


def test_encrypted_credentials_and_budget_guard(client):
    fake = "test-credential-not-a-real-secret-" + uuid4().hex
    assert client.put("/api/providers/openai", json={"key": fake}).status_code == 200
    with storage.connection() as db:
        row = db.execute(
            "SELECT secret FROM credentials WHERE uid='demo_owner' AND provider='openai'"
        ).fetchone()
    assert fake.encode() not in row[0]
    assert storage.get_key("demo_owner", "openai") == fake
    assert fake not in client.get("/api/providers").text
    before_budget = storage.budget()["reserved_inr"]
    reservation = storage.reserve("test_owner", "test_operation", 2)
    storage.finish_reservation(reservation, False)
    with pytest.raises(ValueError, match="budget exhausted"):
        storage.reserve("test_owner", "too_much", storage.budget()["cap_inr"] + 1)
    assert storage.budget()["reserved_inr"] == before_budget + 2
    storage.delete_key("demo_owner", "openai")


def test_chat_handoff_and_approval_use_current_graph(client, monkeypatch):
    from atlas import models

    async def answer(user, provider, model, question, evidence, task=None, memory=None, role="investigator"):
        return "AUTH-42 currently supports review. [AUTH-42@" + str(
            next(s["revision"] for s in evidence["sources"] if s["id"] == "AUTH-42")
        ) + "]", {}

    monkeypatch.setattr(models, "answer", answer)
    first = client.post(
        "/api/chat", json={"question": "Investigate launch", "provider": "openai", "model": "test"}
    )
    assert first.status_code == 200, first.text
    task = first.json()["task"]
    client.post("/api/scenarios", json={"scenario": "regression"})
    second = client.post(
        "/api/chat",
        json={
            "question": "Continue investigation",
            "provider": "gemini",
            "model": "test",
            "task_id": task["id"],
            "expected_revision": task["revision"],
            "role": "planner",
        },
    )
    assert second.status_code == 200, second.text
    task2 = second.json()["task"]
    assert task2["handoffs"][-1]["to"] == "gemini"
    assert second.json()["revalidated_changes"]
    assert task2["evidence"][0]["revision"] > task["evidence"][0]["revision"]
    assert (
        client.post(
            "/api/chat",
            json={
                "question": "Stale write",
                "provider": "openai",
                "model": "test",
                "task_id": task["id"],
                "expected_revision": 1,
            },
        ).status_code
        == 409
    )


def test_public_import_boundaries(client):
    assert client.post("/api/import/repository", json={"url": "http://127.0.0.1/.env"}).status_code == 400
    assert client.post("/api/import/document", files={"file": ("x.exe", b"not text")}).status_code == 400
    assert "OPENAI_API_KEY=" not in client.get("/.env").text


def test_structured_import_and_source_editor(client):
    from pathlib import Path

    body = json.loads(Path("demo/company-import.json").read_text())
    response = client.post("/api/import/records", json=body)
    assert response.status_code == 200, response.text
    decisions = {d["id"]: d for d in client.get("/api/decisions").json()}
    assert decisions["decision-customer"]["owner"] == "Ada Rivera"
    assert decisions["decision-customer"]["status"] == "current"
    update = client.put(
        "/api/sources/CUSTOMER-17",
        json={
            "content": "Customer declined the rollout scope.",
            "metadata": {"status": "declined"},
            "expected_revision": 1,
        },
    )
    assert update.status_code == 200, update.text
    assert update.json()["affected_decisions"][0]["status"] == "needs_review"
    before = len(client.get("/api/graph").json()["nodes"])
    invalid = client.post(
        "/api/import/records",
        json={
            "entities": [{"id": "bad", "kind": "Person", "title": "Bad input"}],
            "relationships": [{"source": "bad", "target": "does-not-exist", "kind": "OWNS"}],
        },
    )
    assert invalid.status_code == 400
    assert len(client.get("/api/graph").json()["nodes"]) == before


@pytest.mark.asyncio
async def test_graphiti_new_episode_and_duplicate_retry(identity, monkeypatch):
    from types import SimpleNamespace
    from atlas import integrations

    calls = []

    class Client:
        async def build_indices_and_constraints(self):
            pass

        async def add_episode(self, **kwargs):
            assert kwargs.get("uuid") is None
            assert "source" in kwargs and "episode_type" not in kwargs
            assert kwargs["group_id"] == integrations.memory_database(identity)
            calls.append(kwargs)
            return SimpleNamespace(episode=SimpleNamespace(uuid="test-episode"))

        async def close(self):
            pass

    monkeypatch.setattr(integrations, "graphiti_client", lambda user: Client())
    saved = g.save_task(
        identity, {**state(), "episode_status": "pending", "latest_findings": "Authentication needs review."}
    )
    first = await integrations.remember_task(identity, saved)
    assert first["episode_id"] == "test-episode"
    repeated = await integrations.remember_task(identity, g.get_task(identity, saved["id"]))
    assert repeated["status"] == "already_saved" and len(calls) == 1
    from falkordb import FalkorDB

    FalkorDB().select_graph(integrations.memory_database(identity)).delete()


def test_disconnect_blocks_server_fallback(client):
    from atlas import models

    assert client.delete("/api/providers/anthropic").status_code == 200
    assert models.credential({"uid": "demo_owner", "role": "owner"}, "anthropic") is None
    assert client.post("/api/providers/anthropic/server").status_code == 200


def test_conversation_history_is_not_discarded(client, monkeypatch):
    from atlas import models

    async def answer(*args, **kwargs):
        return "Saved answer with source [AUTH-42@1].", {}

    monkeypatch.setattr(models, "answer", answer)
    task = None
    for i in range(17):
        body = {
            "question": f"Keep instruction number {i} for the task.",
            "provider": "anthropic",
            "model": "test",
        }
        if task:
            body.update(task_id=task["id"], expected_revision=task["revision"])
        response = client.post("/api/chat", json=body)
        assert response.status_code == 200, response.text
        task = response.json()["task"]
    loaded = client.get("/api/tasks/" + task["id"]).json()
    assert len(loaded["messages"]) == 34
    assert loaded["messages"][0]["content"] == "Keep instruction number 0 for the task."


def test_duplicate_import_ids_are_rejected_before_writes(client):
    before = client.get("/api/graph").json()
    result = client.post(
        "/api/import/records",
        json={
            "entities": [{"id": "DUPLICATE", "kind": "Person", "title": "Person"}],
            "sources": [{"id": "DUPLICATE", "title": "Source", "content": "Evidence"}],
        },
    )
    assert result.status_code == 400
    assert client.get("/api/graph").json() == before


def test_chat_context_contains_authorized_repository_structure(identity, monkeypatch):
    from atlas import integrations

    g.add_entity(
        identity["tenant"],
        "public",
        {
            "id": "repo-test",
            "kind": "Repository",
            "title": "Test repo",
            "properties": {"graph": "authorized-test-graph"},
        },
    )
    calls = []

    def structure(user, repository_id):
        calls.append((user["uid"], repository_id))
        return {
            "repository": "Test repo",
            "nodes": [
                {
                    "id": "code-1",
                    "kind": "Function",
                    "title": "rotate_token",
                    "properties": {"name": "rotate_token", "docstring": "x" * 1000},
                },
            ],
            "edges": [],
        }

    monkeypatch.setattr(integrations, "code_subgraph", structure)
    evidence = g.context(identity, "What does rotate_token do?")
    assert calls == [(identity["uid"], "repo-test")]
    assert evidence["repository_structure"][0]["nodes"][0]["title"] == "rotate_token"
    assert len(evidence["repository_structure"][0]["nodes"][0]["properties"]["docstring"]) == 250


@pytest.mark.asyncio
async def test_gemini_chat_keeps_output_for_visible_answer(identity, monkeypatch):
    from types import SimpleNamespace
    from atlas import models

    async def completion(**kwargs):
        assert kwargs["reasoning_effort"] == "none"
        assert kwargs["max_tokens"] == 1200
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Partial answer"),
                    finish_reason="length",
                )
            ],
            usage=None,
        )

    monkeypatch.setattr(models, "credential", lambda *args: "fake-test-key")
    monkeypatch.setattr(models.litellm, "acompletion", completion)
    text, usage = await models.answer(identity, "gemini", "gemini-2.5-flash", "Question", {})
    assert "reached the output limit" in text
    assert usage["finish_reason"] == "length"


def test_native_code_graph_authorization_and_source_paths(identity):
    from falkordb import FalkorDB
    from atlas import integrations

    name = f"code:atlas_{identity['tenant']}_0123456789:_default"
    db = FalkorDB().select_graph(name)
    try:
        g.add_entity(
            identity["tenant"],
            "restricted",
            {
                "id": "repo-native",
                "kind": "Repository",
                "title": "Private repo",
                "properties": {"graph": name},
            },
        )
        db.query(
            "CREATE (:Searchable:Function {name:$name,path:$path,src_start:1,src_end:2})",
            {
                "name": "rotate_token",
                "path": f"/private/server/analysis/atlas_{identity['tenant']}_0123456789/auth.py",
            },
        )
        result = integrations.code_subgraph(identity, "repo-native")
        assert result["nodes"][0]["kind"] == "Function"
        assert result["nodes"][0]["properties"]["path"] == "auth.py"
        with pytest.raises(LookupError):
            integrations.code_subgraph({**identity, "role": "viewer"}, "repo-native")
    finally:
        db.delete()


@pytest.mark.asyncio
async def test_rejected_document_budget_does_not_start_a_job(identity, monkeypatch):
    from atlas import integrations

    def exhausted(*args):
        raise ValueError("API testing budget exhausted")

    monkeypatch.setattr(storage, "reserve", exhausted)
    before = storage.jobs(identity["uid"])
    with pytest.raises(ValueError, match="budget exhausted"):
        await integrations.ingest_document(identity, "budget-check.md", "Preserve this source.")
    assert storage.jobs(identity["uid"]) == before


@pytest.mark.asyncio
async def test_document_imports_are_serialized(identity, monkeypatch):
    import asyncio
    from atlas import integrations

    active = 0
    peak = 0

    async def ingest(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return {"ok": True}

    monkeypatch.setattr(integrations, "_ingest_document", ingest)
    await asyncio.gather(*[integrations.ingest_document(identity, f"{i}.md", "Text") for i in range(2)])
    assert peak == 1


def test_same_decision_evidence_does_not_duplicate_investigations(client, monkeypatch):
    from atlas import models

    async def answer(*args, **kwargs):
        return "Review current conditions. [AUTH-42@1]", {}

    monkeypatch.setattr(models, "answer", answer)
    body = {"question": "What needs review?", "provider": "anthropic", "model": "test"}
    task = client.post("/api/chat", json=body).json()["task"]
    assert task["constraints"]
    action = task["actions"][0]
    assert action["target"] == "decision-security"
    assert action["evidence"][0]["source_id"] == "SECURITY-AUDIT"
    approved = client.post(
        f"/api/tasks/{task['id']}/actions/{action['id']}/approve",
        json={"expected_revision": task["revision"]},
    ).json()["task"]
    task = client.post(
        "/api/chat", json={**body, "task_id": task["id"], "expected_revision": approved["revision"]}
    ).json()["task"]
    assert len(task["actions"]) == 1
    assert task["actions"][0]["outcome"]["type"] == "atlas_investigation"
    pending = client.post("/api/chat", json=body).json()["task"]
    old_action = pending["actions"][0]
    assert (
        client.put(
            "/api/sources/SECURITY-AUDIT",
            json={
                "content": "Audit now passes.",
                "metadata": {"result": "passed"},
                "expected_revision": 1,
            },
        ).status_code
        == 200
    )
    resumed = client.post(
        "/api/chat", json={**body, "task_id": pending["id"], "expected_revision": pending["revision"]}
    ).json()["task"]
    assert resumed["actions"][0]["status"] == "superseded"
    assert (
        client.post(
            f"/api/tasks/{resumed['id']}/actions/{old_action['id']}/approve",
            json={"expected_revision": resumed["revision"]},
        ).status_code
        == 400
    )
