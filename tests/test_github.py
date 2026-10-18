import copy

import httpx
import pytest

from atlas import github, graph as g, integrations, models


RECORD = {
    "number": 42,
    "title": "Refresh token fails",
    "body": "Steps to reproduce the failure.",
    "state": "open",
    "user": {"login": "reporter"},
    "assignees": [{"login": "maintainer"}],
    "labels": [{"name": "bug"}],
    "updated_at": "2026-10-18T09:00:00Z",
    "comments": 1,
}


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1/private",
        "https://github.com@evil.example/owner/repo",
        "https://github.com/owner/repo?token=secret",
        "owner/repo/issues/42",
        "owner/..",
    ],
)
def test_public_github_repository_rejects_untrusted_urls(value):
    with pytest.raises(ValueError):
        github.repository_name(value)


def test_public_github_repository_normalizes_name():
    assert github.repository_name("https://github.com/FalkorDB/FalkorDB.git/") == "falkordb/falkordb"


@pytest.mark.asyncio
async def test_public_repository_tree_keeps_source_and_tests_before_generated_files(monkeypatch):
    paths = [f"build/graphblas/generated_{i}.c" for i in range(3001)] + [
        "src/query.c",
        "tests/test_query.py",
        "README.md",
    ]

    async def get(path, params=None):
        if path == "owner/repo":
            return {"private": False}, False
        if "/commits/" in path:
            return {"sha": "a" * 40}, False
        return {
            "tree": [
                {"path": p, "type": "blob", "mode": "100644", "sha": "b" * 40, "size": 30} for p in paths
            ]
        }, False

    monkeypatch.setattr(github, "_get", get)
    result = await github.repository_tree("owner/repo")
    assert result["tree_truncated"] and len(result["tree"]) == 3000
    assert [v["path"] for v in result["tree"][:3]] == ["src/query.c", "tests/test_query.py", "README.md"]


@pytest.mark.asyncio
async def test_public_github_list_filters_prs_and_preserves_pagination(monkeypatch):
    calls = []

    async def get(path, params=None):
        calls.append((path, params))
        if path == "owner/repo":
            return {"private": False}, False
        return [RECORD, {"number": 43, "pull_request": {}}], True

    monkeypatch.setattr(github, "_get", get)
    result = await github.list_issues("owner/repo", "closed", 2)
    assert [v["number"] for v in result["issues"]] == [42]
    assert result["has_next"] and result["page"] == 2
    assert calls[-1][1]["state"] == "closed" and calls[-1][1]["page"] == 2


@pytest.mark.asyncio
async def test_public_github_rejects_private_repository(monkeypatch):
    async def get(path, params=None):
        assert path == "owner/repo"
        return {"private": True}, False

    monkeypatch.setattr(github, "_get", get)
    with pytest.raises(ValueError, match="Only public"):
        await github.list_issues("owner/repo")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,error", [(404, LookupError), (403, ValueError), (301, ValueError), (500, ValueError)]
)
async def test_public_github_errors_and_fixed_unauthenticated_destination(monkeypatch, status, error):
    original_client = httpx.AsyncClient

    def respond(request):
        assert request.url.host == "api.github.com"
        assert "authorization" not in request.headers
        return httpx.Response(status, json={"message": "upstream details not forwarded"})

    monkeypatch.setattr(
        github.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    with pytest.raises(error) as failure:
        await github._get("owner/repo")
    assert "upstream details" not in str(failure.value)


@pytest.mark.asyncio
async def test_public_github_issue_import_preserves_code_graph_and_versions(identity, monkeypatch):
    g.add_entity(
        identity["tenant"],
        "public",
        {
            "id": "existing-code-repository",
            "kind": "Repository",
            "title": "Existing Code-Graph",
            "properties": {
                "url": "https://github.com/owner/repo",
                "graph": "preserved-native-graph",
                "commit": "abc123",
            },
        },
    )
    comment = {
        "user": {"login": "maintainer"},
        "body": "Initial diagnosis",
        "updated_at": "2026-10-18T09:00:00Z",
    }

    async def get(path, params=None):
        if path == "owner/repo":
            return {"private": False}, False
        if path.endswith("/comments"):
            return [comment], False
        return copy.deepcopy(RECORD), False

    monkeypatch.setattr(github, "_get", get)
    first = await github.import_issue(identity, "owner/repo", 42)
    again = await github.import_issue(identity, "owner/repo", 42)
    assert first["revision"] == again["revision"] == 1
    assert first["changed"] and not again["changed"]
    comment["body"] = "Updated diagnosis"
    updated = await github.import_issue(identity, "owner/repo", 42)
    assert updated["revision"] == 2
    history = g.source_history(identity, first["source_id"])
    assert "Updated diagnosis" in history[0]["content"] and "Initial diagnosis" in history[1]["content"]
    snapshot = g.snapshot(identity)
    repos = [
        n
        for n in snapshot["nodes"]
        if n["kind"] == "Repository" and n["properties"].get("url") == "https://github.com/owner/repo"
    ]
    assert len(repos) == 1 and repos[0]["properties"]["graph"] == "preserved-native-graph"
    assert any(
        e["kind"] == "DEPENDS_ON" and e["target"] == "existing-code-repository" for e in snapshot["edges"]
    )


@pytest.mark.asyncio
async def test_public_github_issue_details_disclose_comment_limits(monkeypatch):
    record = {**RECORD, "body": "x" * 12001, "comments": 12}

    async def get(path, params=None):
        if path == "owner/repo":
            return {"private": False}, False
        if path.endswith("/comments"):
            assert params == {"per_page": 10}
            return [{"body": "y" * 1201, "user": None}] * 10, True
        return record, False

    monkeypatch.setattr(github, "_get", get)
    result = await github.issue_details("owner/repo", 42)
    assert result["issue"]["body_truncated"] and len(result["issue"]["body"]) == 12000
    assert len(result["comments"]) == 10 and result["issue"]["comments_count"] == 12
    assert all(c["body_truncated"] and len(c["body"]) == 1200 for c in result["comments"])


def test_public_github_api_validation_csrf_and_viewer_permissions(client, monkeypatch):
    async def listed(*args):
        return {"issues": [], "repository": {"name": "owner/repo"}, "has_next": False, "page": 1}

    async def imported(*args):
        return {"source_id": "GH-example-42", "revision": 1}

    monkeypatch.setattr(github, "list_issues", listed)
    monkeypatch.setattr(github, "import_issue", imported)
    assert client.get("/api/github/issues", params={"repository": "owner/repo"}).status_code == 200
    assert client.get("/api/github/issues", params={"repository": "owner/repo", "page": 0}).status_code == 422
    assert (
        client.get("/api/github/issues", params={"repository": "owner/repo", "state": "bad"}).status_code
        == 422
    )
    path = "/api/github/issues/42/import"
    assert (
        client.post(path, json={"repository": "owner/repo"}, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    assert client.post(path, json={"repository": "owner/repo"}).status_code == 200
    viewer = client.post("/auth/demo", json={"role": "viewer"}).json()
    client.headers["X-CSRF-Token"] = viewer["csrf"]
    assert client.get("/api/github/issues", params={"repository": "owner/repo"}).status_code == 200
    assert client.post(path, json={"repository": "owner/repo"}).status_code == 403
    client.post("/auth/logout")
    assert client.get("/api/github/issues", params={"repository": "owner/repo"}).status_code == 401


def test_explicit_issue_citation_survives_context_source_limit(identity):
    for i in range(25):
        g.put_source(identity["tenant"], "public", f"OTHER-{i}", "Unrelated", "Unrelated context.")
    g.put_source(identity["tenant"], "public", "GH-target-4", "Different issue", "Different issue.")
    g.put_source(identity["tenant"], "public", "GH-target-42", "Selected GitHub issue", "Reported bug.")
    context = g.context(identity, "Investigate GH-target-42@1 and propose a fix.")
    assert context["sources"][0]["id"] == "GH-target-42"
    assert len(context["sources"]) == 20


def test_public_github_issue_investigation_and_model_handoff(client, monkeypatch):
    repository = {"name": "owner/repo", "url": "https://github.com/owner/repo"}

    async def details(*args):
        return {"repository": repository, "issue": github._issue(RECORD, repository), "comments": []}

    async def no_external_retrieval(*args):
        return []

    async def answer(user, provider, model, question, evidence, task=None, memory=None, role="investigator"):
        source = next(s for s in evidence["sources"] if s["properties"].get("type") == "github_issue")
        assert "Steps to reproduce" in source["content"]
        return f"The reported issue needs investigation [{source['id']}@{source['revision']}].", {}

    monkeypatch.setattr(github, "issue_details", details)
    monkeypatch.setattr(integrations, "retrieve_documents", no_external_retrieval)
    monkeypatch.setattr(integrations, "recall", no_external_retrieval)
    monkeypatch.setattr(models, "answer", answer)
    imported = client.post("/api/github/issues/42/import", json={"repository": "owner/repo"}).json()
    question = f"Investigate {imported['source_id']}@1 and propose tests."
    first = client.post("/api/chat", json={"question": question, "provider": "anthropic", "model": "test"})
    assert first.status_code == 200, first.text
    task = first.json()["task"]
    assert {"source_id": imported["source_id"], "revision": 1} in task["evidence"]
    continued = client.post(
        "/api/chat",
        json={
            "question": "Continue this issue investigation with the same evidence.",
            "provider": "gemini",
            "model": "test",
            "task_id": task["id"],
            "expected_revision": task["revision"],
        },
    )
    assert continued.status_code == 200, continued.text
    next_task = continued.json()["task"]
    assert next_task["id"] == task["id"] and len(next_task["handoffs"]) == 1
    assert {"source_id": imported["source_id"], "revision": 1} in next_task["evidence"]
