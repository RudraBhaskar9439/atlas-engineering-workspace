"""Authenticated client for the isolated validation service."""

import os
from pathlib import Path
import secrets

import httpx


def token():
    path = Path(os.getenv("ATLAS_RUNNER_TOKEN_FILE", "/run/atlas-runner/token"))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    return path.read_text().strip()


async def run_tests(archive, mode, test_path):
    if mode not in {"pytest", "unittest"}:
        raise ValueError("Choose pytest or unittest for isolated Python validation.")
    url = os.getenv("ATLAS_RUNNER_URL")
    if not url:
        raise ValueError(
            "The validation service is not configured. Start the Atlas runner with Docker Compose."
        )
    try:
        async with httpx.AsyncClient(timeout=100) as client:
            response = await client.post(
                url + "/run",
                params={"mode": mode, "test_path": test_path},
                content=archive,
                headers={"X-Atlas-Runner": token(), "Content-Type": "application/x-tar"},
            )
    except httpx.RequestError:
        raise ValueError("The validation service is unavailable. Start the runner and retry.") from None
    if response.status_code != 200:
        raise ValueError("Isolated validation could not start. Check the runner and sandbox image.")
    return response.json()
