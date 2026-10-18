from atlas import graph as g


def test_google_callback_identity_and_allowlist(client, monkeypatch):
    from atlas import main

    seeded = []
    monkeypatch.setenv("ATLAS_ALLOWED_EMAILS", "owner@example.com")
    monkeypatch.setattr(main, "seed", seeded.append)

    async def token(request):
        return {
            "userinfo": {
                "sub": "test-google-subject",
                "email": "owner@example.com",
                "name": "Test owner",
                "email_verified": True,
            }
        }

    monkeypatch.setattr(main.oauth.google, "authorize_access_token", token)
    response = client.get("/auth/google/callback", follow_redirects=False)
    assert response.status_code == 307
    me = client.get("/api/me").json()
    assert me["user"]["auth_type"] == "google"
    assert me["user"]["tenant"].startswith("u_")
    assert seeded == [me["user"]["tenant"]]
    assert me["csrf"]
    monkeypatch.setenv("ATLAS_ALLOWED_EMAILS", "someone-else@example.com")
    assert client.get("/api/graph").status_code == 403


def test_google_callback_denies_unverified_identity(client, monkeypatch):
    from atlas import main

    async def token(request):
        return {"userinfo": {"sub": "unverified", "email": "test@example.com", "email_verified": False}}

    monkeypatch.setattr(main.oauth.google, "authorize_access_token", token)
    response = client.get("/auth/google/callback", follow_redirects=False)
    assert "auth_error=" in response.headers["location"]
    assert client.get("/api/me").json()["user"]["auth_type"] == "demo"


def test_memory_recall_requires_csrf(client, monkeypatch):
    from atlas import integrations

    calls = []

    async def recall(user, question):
        calls.append(question)
        return [{"fact": "Saved fact"}]

    monkeypatch.setattr(integrations, "recall", recall)
    assert client.get("/api/memory").status_code == 404
    assert client.post("/api/memory", json={}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/api/memory", json={}).json()["facts"][0]["fact"] == "Saved fact"
    assert calls == ["current work"]


def test_encrypted_provider_key_rejects_whitespace(client):
    assert client.put("/api/providers/openai", json={"key": " " * 20}).status_code == 400


def test_import_ids_are_unique_across_scopes(client):
    before = client.get("/api/graph").json()
    response = client.post(
        "/api/import/records",
        json={
            "scope": "public",
            "sources": [{"id": "SECURITY-AUDIT", "title": "Collision", "content": "Wrong scope"}],
        },
    )
    assert response.status_code == 400
    assert client.get("/api/graph").json() == before


async def test_repository_reimport_tracks_new_commit_and_deleted_file(identity, monkeypatch, tmp_path):
    import shutil
    from atlas import integrations

    original = integrations.ROOT
    fixture = tmp_path / "demo" / "orbit-api"
    fixture.mkdir(parents=True)
    for source in (original / "demo" / "orbit-api").glob("*.py"):
        shutil.copyfile(source, fixture / source.name)
    runtime = tmp_path / ".tools" / "codegraph" / "bin"
    runtime.mkdir(parents=True)
    (runtime / "cgraph").symlink_to(original / ".tools" / "codegraph" / "bin" / "cgraph")
    monkeypatch.setattr(integrations, "ROOT", tmp_path)
    first = await integrations.index_repository(identity, "demo")
    record = next(
        n for n in g.snapshot(identity)["nodes"] if n["properties"].get("url") == "synthetic fixture"
    )
    previous_graph = record["properties"]["graph"]
    assert first["indexed_python_files"] == 2
    (fixture / "billing.py").unlink()
    second = await integrations.index_repository(identity, "demo")
    repositories = [
        n for n in g.snapshot(identity)["nodes"] if n["properties"].get("url") == "synthetic fixture"
    ]
    assert len(repositories) == 1 and repositories[0]["id"] == record["id"]
    assert repositories[0]["properties"]["graph"] != previous_graph
    assert first["commits"][0]["sha"] != second["commits"][0]["sha"]
    assert second["indexed_python_files"] == 1
    assert not any(
        n["title"] in {"billing.py", "annual_total"}
        for n in integrations.code_subgraph(identity, record["id"])["nodes"]
    )


def test_decision_without_evidence_stays_in_review(identity):
    tenant = identity["tenant"]
    g.add_entity(
        tenant, "public", {"id": "empty-decision", "kind": "Decision", "title": "Unsubstantiated decision"}
    )
    g.add_entity(
        tenant,
        "public",
        {
            "id": "uncited",
            "kind": "Fact",
            "title": "Uncited condition",
            "properties": {"field": "status", "equals": None},
        },
    )
    g.add_edge(tenant, "public", "empty-decision", "uncited", "DEPENDS_ON")
    g.evaluate(tenant, "public")
    decision = next(d for d in g.decisions(identity) if d["id"] == "empty-decision")
    assert decision["status"] == "needs_review"
    assert decision["checks"][0]["missing"] and not decision["checks"][0]["passed"]
    g.add_entity(tenant, "public", {"id": "no-conditions", "kind": "Decision", "title": "No conditions"})
    g.evaluate(tenant, "public")
    decision = next(d for d in g.decisions(identity) if d["id"] == "no-conditions")
    assert decision["status"] == "needs_review" and decision["checks"] == []


def test_decision_without_complete_traversal_reports_limit(identity):
    tenant = identity["tenant"]
    g.add_entity(
        tenant, "public", {"id": "deep-decision", "kind": "Decision", "title": "Long dependency chain"}
    )
    previous = "deep-decision"
    for i in range(9):
        id = f"chain-{i}"
        g.add_entity(tenant, "public", {"id": id, "kind": "Project", "title": id})
        g.add_edge(tenant, "public", previous, id, "DEPENDS_ON")
        previous = id
    g.add_edge(tenant, "public", previous, "fact-auth-closed", "DEPENDS_ON")
    g.evaluate(tenant, "public")
    decision = next(d for d in g.decisions(identity) if d["id"] == "deep-decision")
    assert decision["status"] == "needs_review" and decision["traversal_limit_reached"]


def test_citation_versions_and_open_questions_survive_handoff(client, monkeypatch):
    from atlas import models

    async def answer(user, provider, model, question, evidence, task=None, *args):
        return "Historical evidence [AUTH-42@1, VERIFY-AUTH@1]. Invalid [AUTH-42@999].", {}

    monkeypatch.setattr(models, "answer", answer)
    body = {"question": "Review evidence", "provider": "anthropic", "model": "test"}
    first = client.post("/api/chat", json=body).json()["task"]
    client.post("/api/scenarios", json={"scenario": "regression"})
    original_save = g.save_task
    captured = []

    def capture(user, state, expected_revision=None):
        captured.append(user)
        return original_save(user, state, expected_revision)

    monkeypatch.setattr(g, "save_task", capture)
    second = client.post(
        "/api/chat",
        json={**body, "provider": "gemini", "task_id": first["id"], "expected_revision": first["revision"]},
    ).json()["task"]
    assert {"source_id": "AUTH-42", "revision": 1} in second["evidence"]
    assert all(r["revision"] != 999 for r in second["evidence"])
    assert len(second["citation_warnings"]) == 2
    assert client.get("/api/tasks/" + second["id"]).json()["evidence_changes"]
    identity = captured[0]
    second["open_questions"].append("Who approves the exception?")
    saved = original_save(identity, second, second["revision"])
    resumed = client.post(
        "/api/chat", json={**body, "task_id": saved["id"], "expected_revision": saved["revision"]}
    ).json()["task"]
    assert "Who approves the exception?" in resumed["open_questions"]


async def test_graphiti_old_episode_does_not_complete_a_new_checkpoint(identity, monkeypatch):
    from types import SimpleNamespace
    from atlas import integrations
    from falkordb import FalkorDB

    state = g.save_task(
        identity,
        {
            "objective": "Original objective",
            "latest_findings": "Original findings",
            "messages": [],
            "completed_steps": [],
            "evidence": [],
            "required_scopes": ["public"],
            "episode_status": "pending",
        },
    )

    class Client:
        async def build_indices_and_constraints(self):
            pass

        async def add_episode(self, **kwargs):
            assert "Original findings" in kwargs["episode_body"]
            current = g.get_task(identity, state["id"])
            current.update(latest_findings="New findings", episode_key=None, episode_status="pending")
            g.save_task(identity, current, current["revision"])
            return SimpleNamespace(episode=SimpleNamespace(uuid="old-checkpoint-episode"))

        async def close(self):
            pass

    monkeypatch.setattr(integrations, "graphiti_client", lambda user: Client())
    try:
        result = await integrations.remember_task(identity, state)
        assert result["status"] == "completed" and not result["current_checkpoint_saved"]
        latest = g.get_task(identity, state["id"])
        assert latest["latest_findings"] == "New findings" and latest["episode_status"] == "pending"
    finally:
        FalkorDB().select_graph(integrations.memory_database(identity)).delete()


def test_checkpoint_revalidates_repository_commit(identity):
    record = {
        "id": "commit-repo",
        "kind": "Repository",
        "title": "Versioned repository",
        "properties": {"graph": "code-old", "commit": "sha-old"},
    }
    g.add_entity(identity["tenant"], "public", record)
    state = {
        "evidence": [],
        "repository_evidence": [{"repository_id": "commit-repo", "graph": "code-old", "commit": "sha-old"}],
    }
    assert g.revalidate(identity, state) == []
    record["properties"] = {"graph": "code-new", "commit": "sha-new"}
    g.add_entity(identity["tenant"], "public", record)
    changes = g.revalidate(identity, state)
    assert changes[0]["reason"] == "repository_updated" and changes[0]["current_commit"] == "sha-new"


async def test_graphiti_recall_uses_separate_user_namespaces(identity, monkeypatch):
    from atlas import integrations, storage

    groups = []

    class Client:
        async def search(self, query, group_ids, num_results):
            groups.extend(group_ids)
            return []

        async def close(self):
            pass

    monkeypatch.setattr(integrations, "graphiti_client", lambda user: Client())
    other = {**identity, "uid": "another-private-user"}
    for user in (identity, other):
        storage.job(user["uid"], "graphiti_episode", "completed")
        await integrations.recall(user, "Current work")
    assert groups == [integrations.memory_database(identity), integrations.memory_database(other)]
    assert groups[0] != groups[1]
