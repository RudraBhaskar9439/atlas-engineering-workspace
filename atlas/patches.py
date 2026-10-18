"""Pure validation and archive operations shared by the app and isolated runner."""

import difflib
import hashlib
import io
import json
from pathlib import PurePosixPath
import tarfile

MAX_ARCHIVE = 25_000_000
MAX_SOURCE_BYTES = 160_000
MAX_CONTEXT_BYTES = 200_000


def safe_path(value):
    path = PurePosixPath(value)
    if (
        not value
        or len(value) > 300
        or value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or any(p in {"", ".", "..", ".git"} for p in value.split("/"))
        or any(p.startswith(".env") and p != ".env.example" for p in path.parts)
    ):
        raise ValueError("Use a relative source path without private configuration or traversal.")
    return value


def make_patch(edits, originals, tree):
    if not isinstance(edits, list) or not 1 <= len(edits) <= 16:
        raise ValueError("A proposal must contain between one and sixteen exact edits.")
    files = {}
    for edit in edits:
        path = safe_path(edit.get("path", ""))
        if path.startswith(".github/"):
            raise ValueError("Automated changes to GitHub automation are not supported.")
        if path not in files:
            if path in tree and path not in originals:
                raise ValueError("The proposal edits a file that was not included in its context.")
            before = originals.get(path)
            files[path] = {
                "path": path,
                "before": before,
                "after": before or "",
                "mode": tree.get(path, {}).get("mode", "100644"),
            }
        old, new = edit.get("old"), edit.get("new")
        if not isinstance(new, str) or len(new) > 50000 or "\x00" in new:
            raise ValueError("Proposed edits must contain bounded UTF-8 source text.")
        file = files[path]
        if old is None:
            if file["before"] is not None or file["after"]:
                raise ValueError("A new-file edit cannot replace an existing file.")
            file["after"] = new
        else:
            if not isinstance(old, str) or not old or file["after"].count(old) != 1:
                raise ValueError(
                    "An edit did not uniquely match its original source. Select context and regenerate."
                )
            file["after"] = file["after"].replace(old, new, 1)
        if len(file["after"].encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError("A proposed file exceeds the 160 KB review limit.")
    result = [v for v in files.values() if v["before"] != v["after"]]
    if (
        not result
        or len(result) > 8
        or sum(len(v["after"].encode("utf-8")) for v in result) > MAX_CONTEXT_BYTES
    ):
        raise ValueError("Choose a nonempty proposal of at most eight files and 200 KB.")
    return result


def patch_hash(base, files):
    return hashlib.sha256(json.dumps({"base": base, "files": files}, sort_keys=True).encode()).hexdigest()


def unified_diff(files):
    return "\n".join(
        "".join(
            difflib.unified_diff(
                (v["before"] or "").splitlines(keepends=True),
                v["after"].splitlines(keepends=True),
                fromfile="a/" + v["path"] if v["before"] is not None else "/dev/null",
                tofile="b/" + v["path"],
            )
        )
        for v in files
    )


def archive_files(data, strip_root=False):
    result, total = {}, 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            for item in archive:
                if item.isdir():
                    continue
                if not item.isfile() or item.size > 2_000_000:
                    raise ValueError("Repository archives must contain regular files of at most 2 MB.")
                name = item.name.split("/", 1)[-1] if strip_root else item.name
                if any(
                    p in {".git", "node_modules", ".venv", "venv", "__pycache__"}
                    for p in PurePosixPath(name).parts
                ):
                    continue
                if any(p.startswith(".env") and p != ".env.example" for p in PurePosixPath(name).parts):
                    continue
                safe_path(name)
                if name in result:
                    raise ValueError("Repository archive contains duplicate paths.")
                total += item.size
                if total > MAX_ARCHIVE or len(result) >= 5000:
                    raise ValueError("Repository exceeds the isolated validation size limit.")
                result[name] = archive.extractfile(item).read()
    except (tarfile.TarError, EOFError):
        raise ValueError("Repository archive is invalid.") from None
    return result


def pack_files(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, content in sorted(files.items()):
            safe_path(name)
            item = tarfile.TarInfo(name)
            item.size, item.mode, item.uid, item.gid = len(content), 0o644, 10001, 10001
            archive.addfile(item, io.BytesIO(content))
    data = stream.getvalue()
    if len(data) > MAX_ARCHIVE:
        raise ValueError("Validation archive exceeds the size limit.")
    return data


def overlay(files, changes, tests_only=False):
    result = dict(files)
    for change in changes:
        name = safe_path(change["path"])
        is_test = (
            name.startswith("tests/")
            or PurePosixPath(name).name.startswith("test_")
            or name.endswith("_test.py")
        )
        if tests_only and not is_test:
            continue
        actual = result.get(name)
        expected = change["before"].encode() if change["before"] is not None else None
        if actual != expected:
            raise ValueError("Pinned repository content differs from the reviewed patch.")
        result[name] = change["after"].encode()
    return result
