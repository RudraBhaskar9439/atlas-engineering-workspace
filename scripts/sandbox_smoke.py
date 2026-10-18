"""Exercise real offline test containers without credentials or model calls."""

import asyncio
import json
from pathlib import Path

from atlas.patches import pack_files
from atlas.runner import execute


async def main():
    tests = """import os
import socket
from pathlib import Path
import pytest
from calc import add

def test_add():
    assert add(2, 3) == 5

def test_no_credentials_or_host_access():
    assert os.getuid() == 10001
    assert not Path('/var/run/docker.sock').exists()
    for name in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'GITHUB_TOKEN'):
        assert not os.getenv(name)
    with pytest.raises(OSError):
        Path('/workspace/unwanted').write_text('blocked')
    with pytest.raises(OSError):
        Path('/app/unwanted').write_text('blocked')

def test_no_network():
    with socket.socket() as connection:
        connection.settimeout(.3)
        with pytest.raises(OSError):
            connection.connect(('1.1.1.1', 443))
"""
    files = {"calc.py": b"def add(a,b):\n    return a-b\n", "tests/test_calc.py": tests.encode()}
    baseline = await execute(pack_files(files), "pytest", "tests")
    files["calc.py"] = b"def add(a,b):\n    return a+b\n"
    proposed = await execute(pack_files(files), "pytest", "tests")
    assert baseline["exit_code"] == 1, baseline["output"]
    assert proposed["exit_code"] == 0, proposed["output"]
    result = {
        "baseline": baseline,
        "proposed": proposed,
        "model_calls": 0,
        "checks": [
            "regression",
            "non-root user",
            "no credentials",
            "no Docker socket",
            "read-only source and root",
            "no network",
        ],
    }
    destination = Path(__file__).resolve().parent.parent / "outputs" / "atlas-sandbox-evaluation.json"
    destination.write_text(json.dumps(result, indent=2) + "\n")
    print(
        "PASS: real isolated baseline/fix, non-root execution, credential isolation, read-only files, and disabled network."
    )


if __name__ == "__main__":
    asyncio.run(main())
