from __future__ import annotations

import json
import time
import urllib.request


BASE_URL = "http://127.0.0.1:8000"


def request(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


task = request(
    "POST",
    "/run_task",
    {"task": "Generate a technical report about multi-agent systems", "metadata": {"tenant": "demo"}},
)
print(json.dumps(task, indent=2))

for _ in range(10):
    state = request("GET", f"/task/{task['task_id']}")
    print(state["status"], state["metrics"])
    if state["status"] in {"success", "failed"}:
        print(json.dumps(state, indent=2))
        break
    time.sleep(1)
