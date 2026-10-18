import base64
import copy
import hashlib
import io
import json
import tarfile

import httpx
import pytest
from fastapi.testclient import TestClient

from atlas import changes, github, models, runner, sandbox, storage, graph as g
from atlas.patches import archive_files, make_patch, overlay, pack_files, patch_hash, safe_path


@pytest.mark.parametrize(
    "path", ["../escape", "/etc/passwd", "x/../../bad", ".git/config", ".env.local", "a\\b", "a//b"]
)
def test_patch_paths_reject_escape_and_private_files(path):
    with pytest.raises(ValueError):
        safe_path(path)


def test_exact_edits_preserve_source_and_reject_ambiguous_replacements():
    original = {"app.py": "def total(a, b):\n    return a - b\n"}
    files = make_patch(
        [{"path": "app.py", "old": "return a - b", "new": "return a + b"}], original, {"app.py": {}}
    )
    assert files[0]["after"] == "def total(a, b):\n    return a + b\n"
    with pytest.raises(ValueError, match="uniquely"):
        make_patch([{"path": "app.py", "old": "missing", "new": "x"}], original, {"app.py": {}})
    with pytest.raises(ValueError, match="context"):
        make_patch([{"path": "secret.py", "old": "x", "new": "y"}], {}, {"secret.py": {}})
    with pytest.raises(ValueError, match="automation"):
        make_patch([{"path": ".github/workflows/ci.yml", "old": None, "new": "run: anything"}], {}, {})


def test_archive_validation_overlay_and_symlink_rejection():
    original = {"app.py": b"return 1\n", "tests/test_app.py": b"assert True\n"}
    assert archive_files(pack_files(original)) == original
    patch = [{"path": "app.py", "before": "return 1\n", "after": "return 2\n"}]
    assert overlay(original, patch)["app.py"] == b"return 2\n"
    assert overlay(original, patch, True)["app.py"] == b"return 1\n"
    with pytest.raises(ValueError, match="differs"):
        overlay({"app.py": b"changed"}, patch)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        item = tarfile.TarInfo("link")
        item.type, item.linkname = tarfile.SYMTYPE, "/etc/passwd"
        archive.addfile(item)
    with pytest.raises(ValueError, match="regular files"):
        archive_files(stream.getvalue())


def state(identity):
    g.put_source(
        identity["tenant"], "public", "GH-test-42", "Reported issue", "Adding numbers subtracts them."
    )
    files = [
        {
            "path": "app.py",
            "before": "def total(a,b):\n    return a-b\n",
            "after": "def total(a,b):\n    return a+b\n",
            "mode": "100644",
        }
    ]
    return changes.save(
        identity,
        {
            "repository": "owner/repo",
            "base_sha": "a" * 40,
            "default_branch": "main",
            "tree": [{"path": "app.py", "sha": "b" * 40, "size": 30, "mode": "100644"}],
            "issue": {
                "number": 42,
                "title": "Wrong total",
                "body": "Adding numbers subtracts them.",
                "url": "https://github.com/owner/repo/issues/42",
            },
            "comments": [],
            "evidence": {"source_id": "GH-test-42", "revision": 1},
            "files": files,
            "originals": {"app.py": files[0]["before"]},
            "selected_paths": ["app.py"],
            "status": "review",
            "patch_hash": patch_hash("a" * 40, files),
            "summary": "Correct addition.",
            "notes": [],
            "validation": None,
        },
    )


def test_change_privacy_stale_updates_and_edit_invalidates_checks(identity):
    change = state(identity)
    with pytest.raises(LookupError):
        changes.get({**identity, "uid": "another-user"}, change["id"])
    with pytest.raises(ValueError, match="updated elsewhere"):
        changes.get(identity, change["id"], change["revision"] + 1)
    change["validation"] = {"status": "passed", "patch_hash": change["patch_hash"]}
    change = changes.save(identity, change, change["revision"])
    edited = changes.edit(
        identity,
        change["id"],
        change["revision"],
        [{"path": "app.py", "content": "def total(a,b):\n    return sum((a,b))\n"}],
    )
    assert edited["validation"] is None and edited["patch_hash"] != change["patch_hash"]
    assert changes.listing(identity)[0]["id"] == change["id"]


@pytest.mark.asyncio
async def test_large_source_can_be_read_patched_and_manually_reviewed(identity, monkeypatch):
    before = "// context\n" * 11000 + "return old_value;\n"
    tree = {"tensor.rs": {"sha": "b" * 40, "size": len(before.encode()), "mode": "100644"}}

    async def get(path, params=None):
        assert path == "owner/repo/git/blobs/" + "b" * 40
        return {"content": base64.b64encode(before.encode()).decode()}, False

    monkeypatch.setattr(github, "_get", get)
    originals = await github.read_files("owner/repo", tree, ["tensor.rs"])
    files = make_patch(
        [{"path": "tensor.rs", "old": "return old_value;", "new": "return fixed_value;"}],
        originals,
        tree,
    )
    assert len(files[0]["after"].encode()) > 100000
    change = state(identity)
    change.update(files=files, originals=originals)
    change = changes.save(identity, change, change["revision"])
    reviewed = changes.edit(
        identity,
        change["id"],
        change["revision"],
        [{"path": "tensor.rs", "content": files[0]["after"].replace("fixed_value", "reviewed_value")}],
    )
    assert reviewed["files"][0]["after"].endswith("return reviewed_value;\n")
    with pytest.raises(ValueError, match="160 KB"):
        changes.edit(
            identity, reviewed["id"], reviewed["revision"], [{"path": "tensor.rs", "content": "é" * 80001}]
        )
    with pytest.raises(ValueError, match="160 KB"):
        make_patch([{"path": "tensor.rs", "old": "return old_value;", "new": "é" * 25000}], originals, tree)
    with pytest.raises(ValueError, match="200 KB"):
        make_patch(
            [
                {"path": p, "old": "return old_value;", "new": "return fixed_value;"}
                for p in ("tensor.rs", "other.rs")
            ],
            {"tensor.rs": before, "other.rs": before},
            {"tensor.rs": {}, "other.rs": {}},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contents,limit",
    [
        ({"one.rs": "é" * 80001}, "160 KB"),
        ({"one.rs": "x" * 110000, "two.rs": "x" * 100000}, "200 KB"),
    ],
)
async def test_source_context_enforces_actual_utf8_and_combined_byte_limits(monkeypatch, contents, limit):
    async def get(path, params=None):
        value = contents[path.rsplit("/", 1)[-1]]
        return {"content": base64.b64encode(value.encode()).decode()}, False

    monkeypatch.setattr(github, "_get", get)
    tree = {path: {"sha": path, "size": 1} for path in contents}
    with pytest.raises(ValueError, match=limit):
        await github.read_files("owner/repo", tree, list(contents))


@pytest.mark.asyncio
async def test_patch_generation_uses_exact_context_and_preserves_provider_handoff(identity, monkeypatch):
    change = state(identity)

    async def files(*args):
        return copy.deepcopy(change["originals"])

    async def answer(*args, **kwargs):
        assert kwargs["max_tokens"] == 12000
        assert kwargs["max_context_chars"] == 260000
        assert kwargs["response_schema"] is changes.PatchProposal
        assert "untrusted" in kwargs["system_instructions"]
        return json.dumps(
            {
                "summary": "Correct addition.",
                "notes": [],
                "edits": [{"path": "app.py", "old": "return a-b", "new": "return a+b"}],
            }
        ), {}

    monkeypatch.setattr(github, "read_files", files)
    monkeypatch.setattr(models, "answer", answer)
    proposed = await changes.propose(identity, change["id"], change["revision"], ["app.py"], "gemini", "test")
    assert proposed["files"][0]["after"].endswith("return a+b\n")
    assert proposed["provider"] == "gemini" and proposed["status"] == "review"


@pytest.mark.asyncio
@pytest.mark.parametrize("finishes", [("length", "stop"), ("length", "length"), ("invalid",)])
async def test_patch_output_limit_retries_once_without_saving_partial_edits(identity, monkeypatch, finishes):
    from types import SimpleNamespace

    storage.setup()
    change = state(identity)
    calls = []
    proposal = {
        "summary": "Correct addition.",
        "notes": [],
        "edits": [{"path": "app.py", "old": "return a-b", "new": "return a+b"}],
    }

    async def files(*args):
        return copy.deepcopy(change["originals"])

    async def completion(**kwargs):
        finish = finishes[len(calls)]
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(proposal) if finish == "stop" else '{"summary":"unfinished'
                    ),
                    finish_reason="stop" if finish == "invalid" else finish,
                )
            ],
            usage=None,
        )

    monkeypatch.setattr(github, "read_files", files)
    monkeypatch.setattr(models, "credential", lambda *args: "fake-test-key")
    monkeypatch.setattr(models.litellm, "acompletion", completion)
    if finishes[-1] == "stop":
        result = await changes.propose(
            identity, change["id"], change["revision"], ["app.py"], "anthropic", models.DEFAULTS["anthropic"]
        )
        assert result["revision"] == change["revision"] + 1
        assert result["files"][0]["after"].endswith("return a+b\n")
    else:
        with pytest.raises(ValueError, match="output limit|invalid structured"):
            await changes.propose(
                identity,
                change["id"],
                change["revision"],
                ["app.py"],
                "anthropic",
                models.DEFAULTS["anthropic"],
            )
        assert changes.get(identity, change["id"]) == change
    assert [call["max_tokens"] for call in calls] == [12000, 16000][: len(finishes)]
    assert all(call["timeout"] == 120 for call in calls)
    if len(calls) == 2:
        assert calls[0]["messages"] == calls[1]["messages"]


@pytest.mark.asyncio
async def test_automatic_context_finds_files_then_generates_patch(identity, monkeypatch):
    change = state(identity)
    calls = []

    async def answer(user, provider, model, question, evidence, **kwargs):
        calls.append(evidence)
        if "available_files" in evidence:
            assert kwargs["max_tokens"] == 1000
            assert kwargs["response_schema"] is changes.ContextSelection
            assert "untrusted" in kwargs["system_instructions"]
            return json.dumps({"paths": ["app.py"], "reason": "Implementation for the reported total."}), {}
        assert evidence["files"] == change["originals"]
        return json.dumps(
            {
                "summary": "Correct total",
                "edits": [{"path": "app.py", "old": "return a-b", "new": "return a+b"}],
            }
        ), {}

    async def files(repository, tree, paths):
        assert paths == ["app.py"]
        return copy.deepcopy(change["originals"])

    monkeypatch.setattr(models, "answer", answer)
    monkeypatch.setattr(github, "read_files", files)
    result = await changes.propose(identity, change["id"], change["revision"], [], "gemini", "test")
    assert len(calls) == 2
    assert result["selected_paths"] == ["app.py"]
    assert result["context_selection"]["method"] == "automatic"
    assert result["revision"] == change["revision"] + 2
    assert result["files"][0]["after"].endswith("return a+b\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("paths", [[], ["../secret"], "app.py"])
async def test_automatic_context_rejects_invalid_model_paths(identity, monkeypatch, paths):
    change = state(identity)
    change["tree"] += [
        {"path": path, "size": 120000, "sha": "b" * 40, "mode": "100644"} for path in ("big.rs", "other.rs")
    ]

    async def answer(*args, **kwargs):
        return json.dumps({"paths": paths}), {}

    monkeypatch.setattr(models, "answer", answer)
    with pytest.raises(ValueError, match="could not select valid files"):
        await changes.select_context(identity, change, "gemini", "test")


@pytest.mark.asyncio
async def test_automatic_context_bounds_and_deduplicates_model_suggestions(identity, monkeypatch):
    change = state(identity)
    change["tree"] += [
        {"path": path, "size": 120000, "sha": "b" * 40, "mode": "100644"} for path in ("big.rs", "other.rs")
    ]

    async def answer(*args, **kwargs):
        return json.dumps({"paths": ["../secret", "big.rs", "big.rs", "other.rs", "app.py"]}), {}

    monkeypatch.setattr(models, "answer", answer)
    paths, selection = await changes.select_context(identity, change, "gemini", "test")
    assert paths == ["big.rs", "app.py"]
    assert selection["selection_reduced"]


@pytest.mark.asyncio
async def test_automatic_context_keeps_relevant_paths_in_bounded_catalog(identity, monkeypatch):
    change = state(identity)
    change["issue"].update(title="Tensor::resize leaves edges behind", body="Inspect graph/src/tensor.rs")
    change["tree"] = [
        {"path": f"vendor/generated_{i}_" + "x" * 100 + ".rs", "size": 10} for i in range(2000)
    ] + [{"path": "graph/src/tensor.rs", "size": 110123}]

    async def answer(user, provider, model, question, evidence, **kwargs):
        assert evidence["available_files"][0]["path"] == "graph/src/tensor.rs"
        assert len(json.dumps(evidence["available_files"])) < 33000
        assert evidence["partial_file_list"]
        return '{"paths":["graph/src/tensor.rs"],"reason":"Tensor implementation"}', {}

    monkeypatch.setattr(models, "answer", answer)
    paths, _ = await changes.select_context(identity, change, "gemini", "test")
    assert paths == ["graph/src/tensor.rs"]


@pytest.mark.asyncio
async def test_automatic_context_reuses_discovery_after_generation_failure(identity, monkeypatch):
    change = state(identity)
    selections, generations = 0, 0

    async def answer(user, provider, model, question, evidence, **kwargs):
        nonlocal selections, generations
        if "available_files" in evidence:
            selections += 1
            return '{"paths":["app.py"],"reason":"Total implementation"}', {}
        generations += 1
        if generations == 1:
            raise ValueError("Temporary provider failure")
        return '{"summary":"Fix total","edits":[{"path":"app.py","old":"return a-b","new":"return a+b"}]}', {}

    async def files(*args):
        return copy.deepcopy(change["originals"])

    monkeypatch.setattr(models, "answer", answer)
    monkeypatch.setattr(github, "read_files", files)
    with pytest.raises(ValueError, match="Temporary provider failure"):
        await changes.propose(identity, change["id"], change["revision"], [], "gemini", "test")
    saved = changes.get(identity, change["id"])
    assert saved["selected_paths"] == ["app.py"]
    result = await changes.propose(identity, saved["id"], saved["revision"], [], "gemini", "test")
    assert selections == 1 and generations == 2 and result["status"] == "review"


@pytest.mark.asyncio
async def test_automatic_context_rejects_unsupported_files_without_model_call(identity, monkeypatch):
    change = state(identity)
    change["tree"] = [
        {"path": "huge.rs", "size": 160001},
        {"path": "image.png", "size": 100},
        {"path": ".github/workflows/ci.yml", "size": 100},
    ]

    async def answer(*args, **kwargs):
        pytest.fail("A model call is unnecessary without eligible source files")

    monkeypatch.setattr(models, "answer", answer)
    with pytest.raises(ValueError, match="No supported source files"):
        await changes.select_context(identity, change, "gemini", "test")


def test_proposal_api_accepts_automatic_context_without_paths(client, monkeypatch):
    async def propose(user, run_id, expected, paths, provider, model):
        assert paths == [] and expected == 1
        return {"status": "review", "selected_paths": ["app.py"]}

    monkeypatch.setattr(changes, "propose", propose)
    response = client.post(
        "/api/changes/test/propose",
        json={"expected_revision": 1, "provider": "gemini", "model": "gemini-2.5-flash"},
    )
    assert response.status_code == 200 and response.json()["selected_paths"] == ["app.py"]


@pytest.mark.asyncio
async def test_large_patch_context_reaches_model_without_relaxing_chat_limit(identity, monkeypatch):
    from types import SimpleNamespace

    storage.setup()
    original = "// tensor source\n" * 7000 + "// END OF SELECTED FILE"

    async def completion(**kwargs):
        packed = (
            kwargs["messages"][1]["content"].removeprefix("EVIDENCE (JSON):\n").split("\n\nQUESTION:\n", 1)[0]
        )
        assert json.loads(packed)["company"]["files"]["tensor.rs"] == original
        assert kwargs["max_tokens"] == 6000
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="reviewable patch"), finish_reason="stop")
            ],
            usage=None,
        )

    monkeypatch.setattr(models, "credential", lambda *args: "fake-test-key")
    monkeypatch.setattr(models.litellm, "acompletion", completion)
    evidence = {"files": {"tensor.rs": original}}
    with pytest.raises(ValueError, match="Context exceeds"):
        await models.answer(identity, "gemini", "gemini-2.5-flash", "Question", evidence)
    result, _ = await models.answer(
        identity,
        "gemini",
        "gemini-2.5-flash",
        "Propose exact edits",
        evidence,
        max_tokens=6000,
        max_context_chars=260000,
    )
    assert result == "reviewable patch"
    with pytest.raises(ValueError, match="Context exceeds"):
        await models.answer(
            identity,
            "gemini",
            "gemini-2.5-flash",
            "Propose exact edits",
            {"files": {"huge.rs": "x" * 260000}},
            max_context_chars=260000,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,model", [("anthropic", models.DEFAULTS["anthropic"]), ("gemini", models.DEFAULTS["gemini"])]
)
@pytest.mark.parametrize("fenced", [False, True])
async def test_structured_patch_requests_schema_and_preserves_exact_source(
    identity, monkeypatch, provider, model, fenced
):
    from types import SimpleNamespace

    storage.setup()
    proposal = {
        "summary": "Preserve escaped source",
        "notes": [],
        "edits": [{"path": "app.py", "old": 'value = "old"\n', "new": 'value = "\\n"\n'}],
    }
    raw = json.dumps(proposal)

    async def completion(**kwargs):
        schema = kwargs["response_format"]["json_schema"]
        assert schema["strict"] is True
        assert schema["schema"] == changes.PatchProposal.model_json_schema()
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=f"```json\n{raw}\n```" if fenced else raw),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    monkeypatch.setattr(models, "credential", lambda *args: "fake-test-key")
    monkeypatch.setattr(models.litellm, "acompletion", completion)
    text, _ = await models.answer(
        identity, provider, model, "Fix source", {}, response_schema=changes.PatchProposal
    )
    assert json.loads(text) == proposal
    files = make_patch(json.loads(text)["edits"], {"app.py": proposal["edits"][0]["old"]}, {"app.py": {}})
    assert files[0]["after"] == proposal["edits"][0]["new"]


@pytest.mark.asyncio
async def test_structured_output_respects_provider_limit(identity, monkeypatch):
    from types import SimpleNamespace

    storage.setup()
    model = models.DEFAULTS["anthropic"]
    selected = "anthropic/" + model
    monkeypatch.setitem(
        models.litellm.model_cost,
        selected,
        {**models.litellm.get_model_info(selected), "max_output_tokens": 2048},
    )

    async def completion(**kwargs):
        assert kwargs["max_tokens"] == 2048
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"summary":"Complete","notes":[],"edits":[]}'),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    monkeypatch.setattr(models, "credential", lambda *args: "fake-test-key")
    monkeypatch.setattr(models.litellm, "acompletion", completion)
    text, _ = await models.answer(
        identity,
        "anthropic",
        model,
        "Fix source",
        {},
        max_tokens=12000,
        response_schema=changes.PatchProposal,
    )
    assert json.loads(text)["summary"] == "Complete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw,finish",
    [
        ("", "length"),
        ('{"summary":"unfinished', "length"),
        ('{"summary":"Complete","notes":[],"edits":[]}', "length"),
        ('Here is the fix: {"summary":"Fix","notes":[],"edits":[]}', "stop"),
        ('{"summary":"Fix","notes":[],"edits":[{"path":"a.py","old":"\\q","new":"x"}]}', "stop"),
        ('{"summary":"Fix","notes":[],"edits":{"path":"a.py"}}', "stop"),
        ('{"summary":"Fix","notes":null,"edits":[]}', "stop"),
    ],
)
async def test_invalid_structured_output_is_rejected_without_source_repair(
    identity, monkeypatch, raw, finish
):
    from types import SimpleNamespace

    storage.setup()

    async def completion(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=raw), finish_reason=finish)],
            usage=SimpleNamespace(
                model_dump=lambda: {"prompt_tokens": 40230, "completion_tokens": 1715, "details": "x" * 1000}
            ),
        )

    monkeypatch.setattr(models, "credential", lambda *args: "fake-test-key")
    monkeypatch.setattr(models.litellm, "acompletion", completion)
    with pytest.raises(
        ValueError, match="output limit" if finish == "length" else "invalid structured response"
    ) as error:
        await models.answer(
            identity,
            "anthropic",
            models.DEFAULTS["anthropic"],
            "Fix source",
            {},
            response_schema=changes.PatchProposal,
        )
    assert "saved work is preserved" in str(error.value)
    operation = storage.budget()["operations"][0]
    detail = json.loads(operation["detail"])
    assert operation["status"] == "completed" and detail["finish_reason"] == finish
    assert detail["completion_tokens"] == 1715 and (not raw or raw not in operation["detail"])
    assert isinstance(error.value, models.OutputLimitError) == (finish == "length")


@pytest.mark.asyncio
async def test_validation_binds_results_to_exact_patch(identity, monkeypatch):
    change = state(identity)

    async def archive(*args):
        return pack_files(
            {"root/app.py": change["files"][0]["before"].encode(), "root/tests/test_app.py": b"assert True"}
        )

    calls = []

    async def run(archive, mode, path):
        calls.append(archive_files(archive))
        return {"exit_code": 1 if len(calls) == 1 else 0, "output": "1 passed", "timed_out": False}

    monkeypatch.setattr(github, "repository_archive", archive)
    monkeypatch.setattr(sandbox, "run_tests", run)
    validated = await changes.validate(identity, change["id"], change["revision"], "pytest", "tests")
    assert (
        validated["status"] == "validated" and validated["validation"]["patch_hash"] == change["patch_hash"]
    )
    assert b"a-b" in calls[0]["app.py"] and b"a+b" in calls[1]["app.py"]


@pytest.mark.asyncio
async def test_publication_requires_current_validation_and_uses_draft_pr(identity, monkeypatch):
    change = state(identity)
    with pytest.raises(ValueError, match="passing checks"):
        await changes.publish(identity, change["id"], change["revision"], "me/repo")
    change["validation"] = {
        "status": "passed",
        "patch_hash": change["patch_hash"],
        "regression_verified": True,
        "mode": "pytest",
        "test_path": "tests",
    }
    change = changes.save(identity, change, change["revision"])
    calls = []

    async def public(*args):
        pytest.fail("A draft PR retains its tested base even when upstream has newer commits")

    async def authorized(user, method, path, payload=None, params=None):
        calls.append((method, path, payload))
        if path == "repos/me/repo":
            return {"private": False, "parent": {"full_name": "owner/repo"}}
        if path.endswith("/pulls") and method == "GET":
            return []
        if "/git/ref/" in path:
            raise LookupError()
        if path.endswith("/git/commits/" + "a" * 40):
            return {"tree": {"sha": "tree-base"}}
        if "/compare/" in path:
            content = change["files"][0]["after"].encode()
            return {
                "files": [
                    {
                        "filename": "app.py",
                        "sha": hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest(),
                    }
                ]
            }
        if path.endswith("/pulls"):
            assert payload["draft"] is True and payload["base"] == "main"
            return {"html_url": "https://github.com/owner/repo/pull/5", "number": 5}
        return {"sha": "new-object"}

    monkeypatch.setattr(github, "_get", public)
    monkeypatch.setattr(github, "authorized", authorized)
    published = await changes.publish(identity, change["id"], change["revision"], "me/repo")
    again = await changes.publish(identity, published["id"], published["revision"], "me/repo")
    assert published["publication"]["number"] == again["publication"]["number"] == 5
    assert len([v for v in calls if v[0] == "POST" and v[1].endswith("/pulls")]) == 1
    assert not any("refs/heads/main" in str(v) for v in calls)


@pytest.mark.asyncio
async def test_github_connection_encrypts_and_does_not_return_token(identity, monkeypatch):
    storage.setup()

    async def authenticate(token, *args):
        assert token == "github-test-token-private"
        return {"login": "engineer"}

    monkeypatch.setattr(github, "_authenticated", authenticate)
    result = await github.connect(identity, "github-test-token-private")
    assert result == {"connected": True, "login": "engineer"}
    assert storage.get_key(identity["uid"], "github") == "github-test-token-private"
    storage.delete_key(identity["uid"], "github")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,error,message",
    [
        ("repos/me/missing", ValueError, "Create my fork"),
        ("repos/me/repo/actions/workflows", ValueError, "Enable Actions"),
        ("repos/me/repo/git/ref/heads/atlas-branch", LookupError, "reference"),
    ],
)
async def test_github_missing_destination_is_actionable_without_breaking_branch_recovery(
    monkeypatch, path, error, message
):
    client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={"message": "private-marker"}))
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs))
    with pytest.raises(error, match=message) as failure:
        await github._authenticated("fake-token", "GET", path)
    assert "private-marker" not in str(failure.value) and "fake-token" not in str(failure.value)


def test_runner_commands_and_auth_boundary(tmp_path, monkeypatch):
    assert runner.command("pytest", "tests")[-2:] == ["--", "tests"]
    with pytest.raises(ValueError):
        runner.command("pytest", "-c")
    key = tmp_path / "token"
    key.write_text("private-runner-token")
    monkeypatch.setenv("ATLAS_RUNNER_TOKEN_FILE", str(key))
    with TestClient(runner.app) as client:
        assert client.post("/run?mode=pytest", content=b"not an archive").status_code == 403
    assert runner.log_text(b"\x01\0\0\0\0\0\0\x05hello") == "hello"


def test_changes_api_csrf_and_viewer_boundary(client):
    assert (
        client.post(
            "/api/changes", json={"repository": "owner/repo", "number": 42}, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    me = client.post("/auth/demo", json={"role": "viewer"}).json()
    client.headers["X-CSRF-Token"] = me["csrf"]
    assert client.post("/api/changes", json={"repository": "owner/repo", "number": 42}).status_code == 403
    assert client.get("/api/changes").json() == []


def test_sample_workspace_is_explicit_and_cannot_publish(client, monkeypatch):
    response = client.post("/api/changes/demo")
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["is_demo"] and run["repository"] == "sample/annual-billing"
    assert len(run["files"]) == 2 and "ROUND_HALF_UP" in run["files"][0]["after"]
    denied = client.post(
        f"/api/changes/{run['id']}/publish",
        json={
            "expected_revision": run["revision"],
            "destination": "me/repo",
            "approved_patch": run["patch_hash"],
        },
    )
    assert denied.status_code == 400 and "Sample changes" in denied.text


@pytest.mark.asyncio
async def test_publication_unknown_outcome_preserves_proposal_for_recovery(identity, monkeypatch):
    change = state(identity)
    change["validation"] = {
        "status": "passed",
        "patch_hash": change["patch_hash"],
        "regression_verified": True,
        "mode": "pytest",
        "test_path": "tests",
    }
    change = changes.save(identity, change, change["revision"])
    created = False

    async def public(*args):
        return {"sha": "a" * 40}, False

    async def authorized(user, method, path, payload=None, params=None):
        nonlocal created
        if path == "repos/me/repo":
            return {"private": False, "parent": {"full_name": "owner/repo"}}
        if path.endswith("/pulls") and method == "GET":
            return [{"html_url": "https://github.com/owner/repo/pull/6", "number": 6}] if created else []
        if "/git/ref/" in path:
            raise LookupError()
        if path.endswith("/git/commits/" + "a" * 40):
            return {"tree": {"sha": "base-tree"}}
        if "/compare/" in path:
            data = change["files"][0]["after"].encode()
            return {
                "files": [
                    {
                        "filename": "app.py",
                        "sha": hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest(),
                    }
                ]
            }
        if path.endswith("/pulls"):
            created = True
            raise ValueError("GitHub request timed out. Refresh before retrying.")
        return {"sha": "object"}

    monkeypatch.setattr(github, "_get", public)
    monkeypatch.setattr(github, "authorized", authorized)
    with pytest.raises(ValueError, match="timed out"):
        await changes.publish(identity, change["id"], change["revision"], "me/repo")
    pending = changes.get(identity, change["id"])
    assert pending["publish_started"] and pending["patch_hash"] == change["patch_hash"]
    with pytest.raises(ValueError, match="publication"):
        changes.edit(identity, pending["id"], pending["revision"], [{"path": "app.py", "content": "changed"}])
    recovered = await changes.publish(identity, pending["id"], pending["revision"], "me/repo")
    assert recovered["publication"]["number"] == 6


@pytest.mark.asyncio
async def test_github_ci_requires_the_reviewed_commit_and_success(identity, monkeypatch):
    change = state(identity)
    calls = []
    branch_head = "c" * 40

    async def public(*args):
        pytest.fail("CI validates the pinned commit without requiring the latest upstream commit")

    async def push(*args):
        return branch_head

    async def authorized(user, method, path, payload=None, params=None):
        calls.append((method, path, payload, params))
        if path == "repos/me/repo":
            return {"private": False, "parent": {"full_name": "owner/repo"}}
        if "/git/ref/" in path:
            return {"object": {"sha": branch_head}}
        if path.endswith("/actions/runs"):
            ci = changes.get(identity, change["id"])["ci"]
            return {
                "workflow_runs": [
                    {
                        "id": 10,
                        "workflow_id": 4,
                        "head_sha": "wrong",
                        "head_branch": ci["branch"],
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "id": 11,
                        "workflow_id": 4,
                        "head_sha": branch_head,
                        "head_branch": ci["branch"],
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": "https://github.com/me/repo/actions/runs/11",
                        "name": "Native build and tests",
                    },
                ]
            }
        return {}

    monkeypatch.setattr(github, "_get", public)
    monkeypatch.setattr(github, "authorized", authorized)
    monkeypatch.setattr(changes, "push_review_branch", push)
    started = await changes.start_ci(identity, change["id"], change["revision"], "me/repo", 4)
    assert started["ci"]["head_sha"] == branch_head and started["validation"] is None
    assert any("/dispatches" in c[1] for c in calls)
    checked = await changes.refresh_ci(identity, started["id"], started["revision"])
    assert checked["validation"]["kind"] == "github_ci" and checked["validation"]["status"] == "passed"
    assert (
        checked["validation"]["head_sha"] == branch_head
        and checked["validation"]["patch_hash"] == change["patch_hash"]
    )
    branch_head = "d" * 40
    with pytest.raises(ValueError, match="branch changed"):
        await changes.refresh_ci(identity, checked["id"], checked["revision"])


@pytest.mark.asyncio
async def test_validation_pr_stays_in_owned_fork_and_recovers_after_timeout(identity, monkeypatch):
    change = state(identity)
    calls, pulls = [], []

    async def push(*args):
        return "c" * 40

    async def authorized(user, method, path, payload=None, params=None):
        calls.append((method, path, payload))
        if path == "user":
            return {"login": "me"}
        if path == "repos/me/repo":
            return {
                "private": False,
                "fork": True,
                "owner": {"login": "me"},
                "parent": {"full_name": "owner/repo"},
                "default_branch": "main",
            }
        if path == "repos/me/repo/actions/workflows":
            return {"workflows": []}
        assert path == "repos/me/repo/pulls"
        if method == "GET":
            assert params["head"].startswith("me:atlas/") and params["base"] == "main"
            return pulls.copy()
        assert payload["draft"] is True and payload["base"] == "main"
        assert "github.com/owner/repo" not in payload["body"]
        pulls.append({"number": 7, "html_url": "https://github.com/me/repo/pull/7", "state": "open"})
        raise ValueError("GitHub request timed out")

    monkeypatch.setattr(changes, "push_review_branch", push)
    monkeypatch.setattr(github, "authorized", authorized)
    with pytest.raises(ValueError, match="timed out"):
        await changes.start_ci(identity, change["id"], change["revision"], "me/repo", trigger="pull_request")
    pending = changes.get(identity, change["id"])
    assert pending["ci"]["head_sha"] == "c" * 40
    recovered = await changes.start_ci(
        identity, pending["id"], pending["revision"], "me/repo", trigger="pull_request"
    )
    repeated = await changes.start_ci(
        identity, recovered["id"], recovered["revision"], "me/repo", trigger="pull_request"
    )
    assert repeated["ci"]["validation_pr"]["number"] == 7
    assert sum(method == "POST" for method, _, _ in calls) == 1
    assert not any("repos/owner/repo/pulls" == path for _, path, _ in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, True])
async def test_validation_issue_repairs_existing_pr_without_duplicate_trackers(
    identity, monkeypatch, timeout
):
    change = state(identity)
    change["ci"] = {"destination": "me/repo", "head_sha": "c" * 40, "validation_pr": {"number": 7}}
    change = changes.save(identity, change, change["revision"])
    calls, issues = [], []
    pull = {
        "number": 7,
        "html_url": "https://github.com/me/repo/pull/7",
        "state": "open",
        "body": "Human review notes.",
    }

    async def push(*args):
        return "c" * 40

    async def authorized(user, method, path, payload=None, params=None):
        calls.append((method, path, payload))
        if path == "user":
            return {"login": "me"}
        if path == "repos/me/repo":
            return {
                "private": False,
                "fork": True,
                "has_issues": False,
                "owner": {"login": "me"},
                "parent": {"full_name": "owner/repo"},
                "default_branch": "main",
            }
        if path == "repos/me/repo/actions/workflows":
            return {"workflows": [{"state": "active", "path": ".github/workflows/pr-linked-issue.yml"}]}
        if path == "repos/me/repo/issues":
            if method == "GET":
                return copy.deepcopy(issues)
            assert "github.com/owner/repo" not in payload["body"]
            issues.append(
                {
                    "number": 2,
                    "html_url": "https://github.com/me/repo/issues/2",
                    "state": "open",
                    "body": payload["body"],
                }
            )
            if timeout:
                raise ValueError("GitHub request failed")
            return copy.deepcopy(issues[0])
        if path == "repos/me/repo/pulls":
            assert method == "GET"
            return [copy.deepcopy(pull)]
        if path == "repos/me/repo/pulls/7":
            assert method == "PATCH"
            pull.update(payload)
            return copy.deepcopy(pull)
        raise AssertionError(path)

    monkeypatch.setattr(changes, "push_review_branch", push)
    monkeypatch.setattr(github, "authorized", authorized)
    if timeout:
        with pytest.raises(ValueError, match="request failed"):
            await changes.start_ci(
                identity, change["id"], change["revision"], "me/repo", trigger="pull_request"
            )
        change = changes.get(identity, change["id"])
    result = await changes.start_ci(
        identity, change["id"], change["revision"], "me/repo", trigger="pull_request"
    )
    again = await changes.start_ci(
        identity, result["id"], result["revision"], "me/repo", trigger="pull_request"
    )
    assert again["ci"]["validation_issue"]["number"] == 2
    assert again["ci"]["validation_pr"]["number"] == 7
    assert pull["body"] == "Human review notes.\n\nCloses #2"
    assert sum(method == "POST" and path.endswith("/issues") for method, path, _ in calls) == 1
    assert sum(method == "PATCH" and path.endswith("/pulls/7") for method, path, _ in calls) == 1
    assert all(path == "user" or path.startswith("repos/me/repo") for _, path, _ in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("fork,owner", [(False, "me"), (True, "other")])
async def test_validation_pr_refuses_upstream_and_other_accounts(identity, monkeypatch, fork, owner):
    change = state(identity)

    async def authorized(user, method, path, payload=None, params=None):
        assert method == "GET"
        return (
            {"login": "me"}
            if path == "user"
            else {
                "private": False,
                "fork": fork,
                "owner": {"login": owner},
                "parent": {"full_name": "owner/repo"},
            }
        )

    monkeypatch.setattr(github, "authorized", authorized)
    with pytest.raises(ValueError, match="fork owned"):
        await changes.start_ci(identity, change["id"], change["revision"], "me/repo", trigger="pull_request")


@pytest.mark.asyncio
async def test_ci_without_a_matching_run_requires_action_and_can_recover(identity, monkeypatch):
    change = state(identity)
    change["ci"] = {
        "destination": "me/repo",
        "branch": "atlas/test",
        "head_sha": "c" * 40,
        "workflow_id": None,
    }
    change = changes.save(identity, change, change["revision"])
    runs = [
        {
            "id": 1,
            "head_sha": "unrelated",
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
        }
    ]

    async def authorized(user, method, path, payload=None, params=None):
        assert method == "GET"
        if "/git/ref/" in path:
            return {"object": {"sha": "c" * 40}}
        return {"workflow_runs": runs}

    monkeypatch.setattr(github, "authorized", authorized)
    change = await changes.refresh_ci(identity, change["id"], change["revision"])
    assert change["ci"]["status"] == "not_started" and not change["ci"]["runs"]
    with pytest.raises(ValueError, match="passing checks"):
        await changes.publish(identity, change["id"], change["revision"], "me/repo")
    runs.append(
        {"id": 2, "head_sha": "c" * 40, "head_branch": "atlas/test", "status": "queued", "conclusion": None}
    )
    change = await changes.refresh_ci(identity, change["id"], change["revision"])
    assert change["ci"]["status"] == "pending"
    runs[-1].update(status="completed", conclusion="success")
    change = await changes.refresh_ci(identity, change["id"], change["revision"])
    assert change["ci"]["status"] == "passed" and change["validation"]["status"] == "passed"


@pytest.mark.asyncio
async def test_native_patch_requires_repository_ci(identity):
    change = state(identity)
    change["files"][0]["path"] = "src/engine.rs"
    change = changes.save(identity, change, change["revision"])
    with pytest.raises(ValueError, match="GitHub CI"):
        await changes.validate(identity, change["id"], change["revision"], "pytest", "tests")


def test_github_read_credentials_are_request_scoped(identity):
    storage.setup()
    storage.save_key(identity["uid"], "github", "scoped-fake-token")
    assert github.READ_TOKEN.get() is None
    with github.read_credentials(identity):
        assert github.READ_TOKEN.get() == "scoped-fake-token"
    assert github.READ_TOKEN.get() is None
    storage.delete_key(identity["uid"], "github")


@pytest.mark.asyncio
async def test_ci_validated_proposal_creates_draft_with_ci_provenance(identity, monkeypatch):
    change = state(identity)
    change["validation"] = {
        "kind": "github_ci",
        "status": "passed",
        "patch_hash": change["patch_hash"],
        "head_sha": "c" * 40,
        "destination": "me/repo",
        "run_url": "https://github.com/me/repo/actions/runs/12",
    }
    change = changes.save(identity, change, change["revision"])

    async def public(*args):
        return {"sha": "a" * 40}, False

    async def push(*args):
        return "c" * 40

    async def authorized(user, method, path, payload=None, params=None):
        if path == "repos/me/repo":
            return {"private": False, "parent": {"full_name": "owner/repo"}}
        if path.endswith("/pulls") and method == "GET":
            return []
        assert payload["draft"] is True
        assert "GitHub Actions" in payload["body"] and "c" * 40 in payload["body"]
        return {"html_url": "https://github.com/owner/repo/pull/12", "number": 12}

    monkeypatch.setattr(github, "_get", public)
    monkeypatch.setattr(github, "authorized", authorized)
    monkeypatch.setattr(changes, "push_review_branch", push)
    published = await changes.publish(identity, change["id"], change["revision"], "me/repo")
    assert published["publication"]["number"] == 12
