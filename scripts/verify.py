"""Run each build phase without paid model calls; keep its result and diagnostic log."""

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PHASES = {
    "foundation": [
        ["uv", "run", "pytest", "-q", "-k", "csrf or encrypted or disconnect or google or public_import"]
    ],
    "data": [["uv", "run", "pytest", "-q", "-k", "source or import or repository or code_graph or document"]],
    "decisions": [
        [
            "uv",
            "run",
            "pytest",
            "-q",
            "-k",
            "dependency or changed_evidence or same_decision or decision_without",
        ]
    ],
    "memory": [
        [
            "uv",
            "run",
            "pytest",
            "-q",
            "-k",
            "checkpoint or handoff or history or graphiti or gemini_chat or instruction or citation",
        ]
    ],
    "interface": [["npm", "--prefix", "frontend", "run", "build"], ["npm", "--prefix", "frontend", "test"]],
    "release": [
        ["uv", "run", "pytest", "-q"],
        ["uv", "run", "ruff", "check", "atlas", "tests", "scripts"],
        ["uv", "lock", "--check"],
        ["bash", "-n", "scripts/setup.sh", "scripts/start.sh"],
        ["docker", "compose", "--profile", "app", "config", "--quiet"],
        ["docker", "compose", "--profile", "app", "--profile", "tools", "build", "atlas", "sandbox"],
        ["uv", "run", "python", "-m", "scripts.sandbox_smoke"],
    ],
}


def run(selected):
    report_path = ROOT / "outputs" / "atlas-phase-report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {"phases": {}}
    logs = ROOT / ".atlas-data" / "verification"
    logs.mkdir(parents=True, exist_ok=True)
    for phase in selected:
        checks = []
        with (logs / f"{phase}.log").open("w") as log:
            for command in PHASES[phase]:
                log.write("\n" + " ".join(command) + "\n")
                log.flush()
                try:
                    code = subprocess.run(command, cwd=ROOT, stdout=log, stderr=log, timeout=600).returncode
                except (OSError, subprocess.TimeoutExpired) as error:
                    log.write(type(error).__name__ + "\n")
                    code = 1
                checks.append({"command": command, "passed": code == 0})
        passed = all(c["passed"] for c in checks)
        report["phases"][phase] = {"status": "passed" if passed else "failed", "checks": checks}
        report["updated_at"] = datetime.now(UTC).isoformat()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"{phase}: {'PASS' if passed else 'FAIL'} ({len(checks)} checks)", flush=True)
    return all(report["phases"][p]["status"] == "passed" for p in selected)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["all", *PHASES], default="all")
    args = parser.parse_args()
    raise SystemExit(0 if run(list(PHASES) if args.phase == "all" else [args.phase]) else 1)
