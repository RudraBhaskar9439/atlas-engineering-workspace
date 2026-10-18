"""Internal worker: fixed Docker operations, never repository code on the host."""

import asyncio
import io
import os
from pathlib import Path
import secrets
import tarfile
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
import httpx

from .patches import MAX_ARCHIVE, archive_files, safe_path

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
LOCK = asyncio.Lock()
IMAGE = "atlas-python-sandbox:local"


def command(mode, path):
    safe_path(path)
    if path.startswith("-"):
        raise ValueError("A test path cannot be a command option.")
    if mode == "pytest":
        return [
            "/usr/bin/timeout",
            "--signal=KILL",
            "65",
            "/app/.venv/bin/python",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--",
            path,
        ]
    if mode == "unittest":
        return [
            "/usr/bin/timeout",
            "--signal=KILL",
            "65",
            "/app/.venv/bin/python",
            "-m",
            "unittest",
            "discover",
            "-s",
            path,
            "-v",
        ]
    raise ValueError("Unsupported test runner.")


def log_text(data):
    result = bytearray()
    while len(data) >= 8 and data[0] in {0, 1, 2} and data[1:4] == b"\0\0\0":
        length = int.from_bytes(data[4:8], "big")
        result.extend(data[8 : 8 + length])
        data = data[8 + length :]
    result.extend(data)
    return result.decode(errors="replace")[-12000:]


async def docker(client, method, path, payload=None, content=None):
    response = await client.request(method, "http://docker/v1.45/" + path, json=payload, content=content)
    if response.status_code >= 400:
        raise ValueError("Docker could not complete isolated validation. Build the sandbox image and retry.")
    return response


async def execute(archive, mode, test_path):
    files = archive_files(archive)
    if not files:
        raise ValueError("Validation requires a nonempty source archive.")
    cmd = command(mode, test_path)
    volume = "atlas-check-" + uuid4().hex
    containers = []
    transport = httpx.AsyncHTTPTransport(uds="/var/run/docker.sock")
    async with httpx.AsyncClient(transport=transport, timeout=80) as client:
        try:
            await docker(
                client, "POST", "volumes/create", {"Name": volume, "Labels": {"atlas.validation": "true"}}
            )
            staging = (
                await docker(
                    client,
                    "POST",
                    "containers/create",
                    {
                        "Image": IMAGE,
                        "Labels": {"atlas.validation": "true"},
                        "NetworkDisabled": True,
                        "HostConfig": {
                            "NetworkMode": "none",
                            "Mounts": [{"Type": "volume", "Source": volume, "Target": "/workspace"}],
                        },
                    },
                )
            ).json()["Id"]
            containers.append(staging)
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as output:
                for name, content in files.items():
                    item = tarfile.TarInfo(name)
                    item.size, item.mode, item.uid, item.gid = len(content), 0o644, 10001, 10001
                    output.addfile(item, io.BytesIO(content))
            await docker(
                client, "PUT", f"containers/{staging}/archive?path=/workspace", content=stream.getvalue()
            )
            created = (
                await docker(
                    client,
                    "POST",
                    "containers/create",
                    {
                        "Image": IMAGE,
                        "Labels": {"atlas.validation": "true"},
                        "Cmd": cmd,
                        "User": "10001:10001",
                        "WorkingDir": "/workspace",
                        "NetworkDisabled": True,
                        "Env": [
                            "PYTHONDONTWRITEBYTECODE=1",
                            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
                            "PYTHONPATH=/workspace",
                            "HOME=/tmp",
                        ],
                        "HostConfig": {
                            "NetworkMode": "none",
                            "ReadonlyRootfs": True,
                            "Memory": 536870912,
                            "NanoCpus": 1000000000,
                            "PidsLimit": 64,
                            "CapDrop": ["ALL"],
                            "SecurityOpt": ["no-new-privileges"],
                            "Tmpfs": {"/tmp": "rw,nosuid,noexec,size=64m"},
                            "Mounts": [
                                {"Type": "volume", "Source": volume, "Target": "/workspace", "ReadOnly": True}
                            ],
                        },
                    },
                )
            ).json()["Id"]
            containers.append(created)
            await docker(client, "POST", f"containers/{created}/start")
            timed_out = False
            try:
                result = await asyncio.wait_for(docker(client, "POST", f"containers/{created}/wait"), 60)
                exit_code = result.json()["StatusCode"]
            except (TimeoutError, httpx.TimeoutException):
                timed_out, exit_code = True, 124
                await docker(client, "POST", f"containers/{created}/kill")
            logs = await docker(client, "GET", f"containers/{created}/logs?stdout=1&stderr=1&tail=150")
            return {
                "exit_code": exit_code,
                "timed_out": timed_out,
                "output": log_text(logs.content),
                "command": cmd,
                "isolated": True,
            }
        finally:
            for container in reversed(containers):
                try:
                    await client.delete(f"http://docker/v1.45/containers/{container}?force=true&v=true")
                except httpx.RequestError:
                    pass
            try:
                await client.delete(f"http://docker/v1.45/volumes/{volume}")
            except httpx.RequestError:
                pass


@app.post("/run")
async def run(request: Request, mode: Literal["pytest", "unittest"], test_path: str = "tests"):
    path = Path(os.getenv("ATLAS_RUNNER_TOKEN_FILE", "/run/atlas-runner/token"))
    if (
        not path.exists()
        or len(path.read_text().strip()) < 32
        or not secrets.compare_digest(path.read_text().strip(), request.headers.get("X-Atlas-Runner", ""))
    ):
        raise HTTPException(403, "Runner authorization failed")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_ARCHIVE:
            raise HTTPException(413, "Validation archive exceeds its limit")
    async with LOCK:
        try:
            return await execute(bytes(data), mode, test_path)
        except (ValueError, httpx.RequestError) as error:
            raise HTTPException(
                400, str(error) if isinstance(error, ValueError) else "Docker is unavailable"
            ) from None
