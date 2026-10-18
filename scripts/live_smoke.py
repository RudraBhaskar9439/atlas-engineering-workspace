"""Explicit paid smoke check. Uses the app's shared INR budget and synthetic company only."""

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from atlas.main import app
from atlas import storage


def run():
    results = []
    with TestClient(app, base_url="http://localhost:8001") as client:
        login = client.post("/auth/demo", json={"role": "owner"}).json()
        client.headers["X-CSRF-Token"] = login["csrf"]
        client.post("/api/scenarios", json={"scenario": "resolved"}).raise_for_status()
        first_body = {
            "question": "Explain why the Orbit launch was approved. Cite the exact current authentication evidence and identify the launch owner.",
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20251001",
        }
        started = time.monotonic()
        first = client.post("/api/chat", json=first_body)
        results.append(
            {
                "check": "Claude investigation",
                "passed": first.status_code == 200,
                "seconds": round(time.monotonic() - started, 2),
                "detail": first.json().get("detail")
                if first.status_code != 200
                else "Real model response committed to checkpoint",
            }
        )
        print(json.dumps(results[-1]))
        if first.status_code == 200:
            task = first.json()["task"]
            client.post("/api/scenarios", json={"scenario": "regression"}).raise_for_status()
            body = {
                "question": "Continue the saved investigation as planner. What changed in the authentication evidence, which launch decision needs review, and what should its owner do next?",
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "task_id": task["id"],
                "expected_revision": task["revision"],
                "role": "planner",
            }
            started = time.monotonic()
            second = client.post("/api/chat", json=body)
            results.append(
                {
                    "check": "Claude to Gemini handoff after regression",
                    "passed": second.status_code == 200 and bool(second.json().get("revalidated_changes")),
                    "seconds": round(time.monotonic() - started, 2),
                    "detail": second.json().get("detail")
                    if second.status_code != 200
                    else "Changed evidence revalidated before real Gemini response",
                    "task_id": task["id"],
                }
            )
            print(json.dumps(results[-1]))
            if second.status_code == 200:
                task2 = second.json()["task"]
                if task2["actions"]:
                    action = task2["actions"][0]
                    path = f"/api/tasks/{task2['id']}/actions/{action['id']}/approve"
                    approval = client.post(path, json={"expected_revision": task2["revision"]})
                    repeat = client.post(path, json={"expected_revision": task2["revision"]})
                    results.append(
                        {
                            "check": "Real handoff proposal approval and duplicate retry",
                            "passed": approval.status_code == 200 and repeat.json().get("duplicate") is True,
                        }
                    )
                    print(json.dumps(results[-1]))
        report = {"results": results, "budget": storage.budget()}
        out = Path(".atlas-data/live-evaluation.json")
        out.write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {
                    "budget_reserved_inr": report["budget"]["reserved_inr"],
                    "budget_cap_inr": report["budget"]["cap_inr"],
                }
            )
        )
    return results


if __name__ == "__main__":
    run()
