"""Public GitHub issues, saved as versioned evidence without GitHub credentials."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import re

import httpx

from . import graph as g, storage

READ_TOKEN = ContextVar("github_read_token", default=None)


@contextmanager
def read_credentials(user):
    context = READ_TOKEN.set(storage.get_key(user["uid"], "github"))
    try:
        yield
    finally:
        READ_TOKEN.reset(context)


def repository_name(value):
    value = value.strip().removeprefix("https://github.com/").rstrip("/").removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", value):
        raise ValueError("Use a public GitHub repository URL or owner/repository.")
    if value.split("/")[1] in {".", ".."}:
        raise ValueError("Invalid GitHub repository name.")
    return value.lower()


async def _get(path, params=None):
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.get(
                "https://api.github.com/repos/" + path,
                params=params,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "Atlas-company-brain",
                    **({"Authorization": "Bearer " + READ_TOKEN.get()} if READ_TOKEN.get() else {}),
                },
            )
    except httpx.RequestError:
        raise ValueError("GitHub is unavailable or timed out. Retry later.") from None
    if response.status_code == 404:
        raise LookupError("Public GitHub repository or issue was not found.")
    if response.status_code in {403, 429}:
        raise ValueError("GitHub denied or rate-limited this read. Check your connection or retry later.")
    if response.status_code in {301, 302, 307, 308}:
        raise ValueError("This GitHub repository moved. Use its current repository URL.")
    if response.status_code != 200:
        raise ValueError("GitHub could not load this repository or issue. Retry later.")
    try:
        return response.json(), "next" in response.links
    except ValueError:
        raise ValueError("GitHub returned an invalid response. Retry later.") from None


async def _repository(value):
    name = repository_name(value)
    record, _ = await _get(name)
    if not isinstance(record, dict) or record.get("private") is not False:
        raise ValueError("Only public GitHub repositories are supported.")
    return {
        "name": name,
        "url": "https://github.com/" + name,
        "default_branch": record.get("default_branch", "main"),
    }


def _issue(record, repository):
    number = record.get("number")
    if not isinstance(number, int) or number < 1 or "pull_request" in record:
        raise ValueError("Choose a GitHub issue, rather than a pull request.")
    body = record.get("body") or ""
    return {
        "number": number,
        "title": record.get("title", "Untitled issue"),
        "state": record.get("state", "unknown"),
        "url": f"{repository['url']}/issues/{number}",
        "author": (record.get("user") or {}).get("login", "unknown"),
        "assignees": [v["login"] for v in record.get("assignees", [])],
        "labels": [v["name"] for v in record.get("labels", [])],
        "updated_at": record.get("updated_at"),
        "body": body[:12000],
        "body_truncated": len(body) > 12000,
        "comments_count": record.get("comments", 0),
    }


async def list_issues(value, state="open", page=1):
    if state not in {"open", "closed", "all"} or not 1 <= page <= 100:
        raise ValueError("Choose a supported issue state and page between 1 and 100.")
    repository = await _repository(value)
    records, has_next = await _get(
        repository["name"] + "/issues",
        {"state": state, "page": page, "per_page": 20, "sort": "updated", "direction": "desc"},
    )
    if not isinstance(records, list):
        raise ValueError("GitHub returned an invalid issue list.")
    return {
        "repository": repository,
        "issues": [_issue(v, repository) for v in records if "pull_request" not in v],
        "page": page,
        "has_next": has_next,
    }


async def issue_details(value, number):
    if not 1 <= number <= 2147483647:
        raise ValueError("Choose a valid issue number.")
    repository = await _repository(value)
    record, _ = await _get(f"{repository['name']}/issues/{number}")
    if not isinstance(record, dict) or record.get("number") != number:
        raise ValueError("GitHub returned an unexpected issue.")
    issue = _issue(record, repository)
    comments = []
    if issue["comments_count"]:
        records, _ = await _get(f"{repository['name']}/issues/{number}/comments", {"per_page": 10})
        if not isinstance(records, list):
            raise ValueError("GitHub returned an invalid comment list.")
        comments = [
            {
                "author": (v.get("user") or {}).get("login", "unknown"),
                "body": (v.get("body") or "")[:1200],
                "body_truncated": len(v.get("body") or "") > 1200,
                "updated_at": v.get("updated_at"),
            }
            for v in records[:10]
        ]
    return {"repository": repository, "issue": issue, "comments": comments}


async def import_issue(user, value, number):
    detail = await issue_details(value, number)
    return await asyncio.to_thread(_save_issue, user, detail)


def _save_issue(user, detail):
    repository, issue = detail["repository"], detail["issue"]
    digest = hashlib.sha256(repository["name"].encode()).hexdigest()[:20]
    source_id = f"GH-{digest}-{issue['number']}"
    ticket_id = f"github-issue-{digest}-{issue['number']}"
    paragraphs = [
        f"GitHub issue {repository['name']}#{issue['number']}: {issue['title']}",
        f"URL: {issue['url']}\nStatus: {issue['state']}\nReporter: {issue['author']}\n"
        f"Assignees: {', '.join(issue['assignees']) or 'unassigned'}\nUpdated: {issue['updated_at']}",
        issue["body"] or "No issue description provided.",
    ]
    if issue["body_truncated"]:
        paragraphs.append("Issue description truncated to 12,000 characters.")
    paragraphs.append(
        f"Comments: {len(detail['comments'])} of {issue['comments_count']} saved. "
        "Each comment is limited to 1,200 characters."
    )
    for comment in detail["comments"]:
        paragraphs.append(
            f"{comment['author']} ({comment['updated_at']}): {comment['body']}"
            + (" [Comment truncated]" if comment["body_truncated"] else "")
        )
    with g.LOCK:
        nodes = g.snapshot(user)["nodes"]
        for record_id, kind in ((source_id, "Source"), (ticket_id, "Ticket")):
            if any(n["id"] == record_id and (n["kind"] != kind or n["scope"] != "public") for n in nodes):
                raise ValueError("GitHub import IDs overlap existing records.")
        existing = next(
            (
                n
                for n in nodes
                if n["kind"] == "Repository"
                and str(n["properties"].get("url", "")).rstrip("/").removesuffix(".git").lower()
                == repository["url"]
                and n["scope"] == "public"
            ),
            None,
        )
        repository_id = (
            existing["id"]
            if existing
            else "REPO-" + hashlib.sha256(repository["url"].encode()).hexdigest()[:20]
        )
        if not existing:
            g.add_entity(
                user["tenant"],
                "public",
                {
                    "id": repository_id,
                    "kind": "Repository",
                    "title": repository["name"],
                    "properties": {"url": repository["url"], "origin": "github"},
                },
            )
        revision, changed = g.put_source(
            user["tenant"],
            "public",
            source_id,
            f"{repository['name']} #{issue['number']}: {issue['title']}",
            "\n\n".join(paragraphs),
            {
                "type": "github_issue",
                "repository": repository["name"],
                "comments_saved": len(detail["comments"]),
                "comments_total": issue["comments_count"],
                "body_truncated": issue["body_truncated"],
                **{
                    key: issue[key]
                    for key in ("url", "number", "state", "author", "assignees", "labels", "updated_at")
                },
            },
        )
        g.add_entity(
            user["tenant"],
            "public",
            {
                "id": ticket_id,
                "kind": "Ticket",
                "title": issue["title"],
                "properties": {"url": issue["url"], "status": issue["state"], "number": issue["number"]},
            },
        )
        g.add_edge(user["tenant"], "public", ticket_id, source_id, "CITES")
        g.add_edge(user["tenant"], "public", ticket_id, repository_id, "DEPENDS_ON")
        if changed:
            g.evaluate(user["tenant"], "public", source_id)
    return {"source_id": source_id, "revision": revision, "changed": changed, **detail}


async def authorized(user, method, path, payload=None, params=None):
    token = storage.get_key(user["uid"], "github")
    if not token:
        raise ValueError("Connect GitHub in Settings before publishing a pull request.")
    return await _authenticated(token, method, path, payload, params)


async def _authenticated(token, method, path, payload=None, params=None):
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.request(
                method,
                "https://api.github.com/" + path,
                json=payload,
                params=params,
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "Atlas-company-brain",
                },
            )
    except httpx.RequestError:
        raise ValueError("GitHub request timed out. Refresh the change before retrying.") from None
    if response.status_code == 404:
        parts = path.split("/")
        if method == "GET" and parts[0] == "repos" and len(parts) == 3:
            raise ValueError(
                f"GitHub cannot find {parts[1]}/{parts[2]} with your connection. "
                "Check the repository name and token access. If you have not created your public fork, "
                "choose Create my fork first."
            )
        if method == "GET" and path.endswith("/actions/workflows"):
            raise ValueError(
                "GitHub Actions workflows are unavailable for this repository. "
                "Enable Actions in your fork and check your token's repository access."
            )
        raise LookupError("GitHub repository or reference was not found or is not accessible.")
    if response.status_code in {401, 403}:
        raise ValueError("GitHub rejected this operation. Check token permissions and selected repositories.")
    if response.status_code == 422:
        raise ValueError(
            "GitHub rejected the branch or pull request. Check fork synchronization and existing branches."
        )
    if response.status_code not in {200, 201, 202, 204}:
        raise ValueError("GitHub could not complete this operation. Refresh before retrying.")
    return {} if response.status_code == 204 else response.json()


async def connect(user, token):
    if not token.strip() or token != token.strip():
        raise ValueError("Enter a GitHub token without leading or trailing whitespace.")
    account = await _authenticated(token, "GET", "user")
    storage.save_key(user["uid"], "github", token)
    return {"connected": True, "login": account["login"]}


async def connection(user):
    if not storage.get_key(user["uid"], "github"):
        return {"connected": False}
    account = await authorized(user, "GET", "user")
    return {"connected": True, "login": account["login"]}


async def repository_tree(value):
    repository = await _repository(value)
    commit, _ = await _get(f"{repository['name']}/commits/{repository['default_branch']}")
    sha = commit["sha"]
    tree, _ = await _get(f"{repository['name']}/git/trees/{sha}", {"recursive": "1"})
    from .patches import safe_path

    files = []
    for item in tree.get("tree", []):
        if item.get("type") != "blob" or item.get("mode") not in {"100644", "100755"}:
            continue
        try:
            safe_path(item["path"])
        except ValueError:
            continue
        files.append({k: item[k] for k in ("path", "sha", "mode", "size") if k in item})
    # Keep source and tests reachable when generated/vendor trees fill the bounded context list.
    files.sort(
        key=lambda item: (
            any(
                part.lower()
                in {
                    "build",
                    "dist",
                    "vendor",
                    "vendors",
                    "node_modules",
                    "deps",
                    "third_party",
                    "extern",
                    "external",
                }
                for part in item["path"].split("/")[:-1]
            ),
            item["path"].split("/", 1)[0] not in {"src", "lib", "tests", "test", "include"},
            item["path"],
        )
    )
    return {
        **repository,
        "base_sha": sha,
        "tree": files[:3000],
        "tree_truncated": tree.get("truncated", False) or len(files) > 3000,
    }


async def read_files(repository, tree, paths):
    import base64
    from .patches import MAX_CONTEXT_BYTES, MAX_SOURCE_BYTES

    if not 1 <= len(paths) <= 6 or len(set(paths)) != len(paths):
        raise ValueError("Select between one and six distinct context files.")
    originals, total = {}, 0
    for path in paths:
        item = tree.get(path)
        if not item or item.get("size", 0) > MAX_SOURCE_BYTES:
            raise ValueError("Select source files of at most 160 KB from the pinned repository.")
        record, _ = await _get(f"{repository}/git/blobs/{item['sha']}")
        try:
            raw = base64.b64decode(record["content"], validate=False)
            content = raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError, KeyError):
            raise ValueError("Context files must contain UTF-8 text.") from None
        if "\x00" in content or len(raw) > MAX_SOURCE_BYTES:
            raise ValueError("Context files must contain UTF-8 source text of at most 160 KB.")
        total += len(raw)
        if total > MAX_CONTEXT_BYTES:
            raise ValueError("Selected context exceeds 200 KB. Choose fewer files.")
        originals[path] = content
    return originals


async def repository_archive(repository, sha):
    from urllib.parse import urlparse
    from .patches import MAX_ARCHIVE

    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("A pinned Git commit is required for validation.")
    repository = repository_name(repository)
    url = f"https://api.github.com/repos/{repository}/tarball/{sha}"
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            async with client.stream(
                "GET",
                url,
                headers={"Authorization": "Bearer " + READ_TOKEN.get()} if READ_TOKEN.get() else {},
            ) as initial:
                if initial.status_code in {301, 302, 307, 308}:
                    url = initial.headers.get("location", "")
                    parsed = urlparse(url)
                    if (
                        parsed.scheme != "https"
                        or parsed.hostname != "codeload.github.com"
                        or parsed.username
                    ):
                        raise ValueError("GitHub returned an unsupported archive destination.")
                elif initial.status_code == 200:
                    data = bytearray()
                    async for chunk in initial.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_ARCHIVE:
                            raise ValueError("Repository archive exceeds the 25 MB validation limit.")
                    return bytes(data)
                else:
                    raise ValueError("GitHub could not provide the pinned repository archive.")
            data = bytearray()
            async with client.stream("GET", url) as download:
                if download.status_code != 200:
                    raise ValueError("The pinned repository archive is unavailable.")
                async for chunk in download.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_ARCHIVE:
                        raise ValueError("Repository archive exceeds the 25 MB validation limit.")
            return bytes(data)
    except httpx.RequestError:
        raise ValueError("Repository archive download timed out. Retry validation later.") from None
