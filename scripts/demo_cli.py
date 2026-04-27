#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"
if VENV_PYTHON.exists() and Path(sys.prefix).resolve() != (ROOT_DIR / ".venv").resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

import requests


DEFAULT_TASKS_FILE = ROOT_DIR / "demo" / "tasks.json"
DEFAULT_BASE_URL = os.environ.get("API_BASE_URL") or os.environ.get("BASE_URL") or "http://127.0.0.1:8000"
TERMINAL_STATUSES = {"success", "failed"}


class ApiClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        response = requests.request(method, url, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response.json()


def load_demo_tasks(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        tasks = json.load(fh)
    if not isinstance(tasks, list):
        raise ValueError(f"Expected a JSON array in {path}")
    return tasks


def find_demo_task(path: Path, key: str) -> dict[str, Any]:
    for task in load_demo_tasks(path):
        if task.get("key") == key or task.get("name") == key:
            return task
    raise KeyError(f"No demo task found for key or name: {key}")


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def summarize_task(state: dict[str, Any], logs: dict[str, Any] | None = None) -> dict[str, Any]:
    steps = state.get("steps", [])
    metrics = state.get("metrics", {})
    agents: list[str] = []
    for step in steps:
        agent = step.get("agent", "")
        if agent and agent not in agents:
            agents.append(agent)

    events = [entry.get("event", "") for entry in (logs or {}).get("logs", [])]
    retry_happened = bool(metrics.get("retries", 0)) or any((step.get("retries") or 0) > 0 for step in steps)
    replan_happened = any(event in {"review_failed_replan", "step_failed_replan"} for event in events)
    if state.get("task_graph", {}).get("replan_reason"):
        replan_happened = True

    return {
        "task_id": state.get("task_id"),
        "status": state.get("status"),
        "total_steps": metrics.get("total_steps", len(steps)),
        "agents_used": agents,
        "latency": metrics.get("latency", 0.0),
        "retry_happened": retry_happened,
        "replan_happened": replan_happened,
        "recovery_happened": retry_happened or replan_happened,
    }


def cmd_list(args: argparse.Namespace) -> int:
    tasks = load_demo_tasks(args.tasks_file)
    for task in tasks:
        print(f"{task.get('key', '-'):<10} {task.get('name', '-')}")
        print(f"           {task.get('task', '')[:140]}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    client = ApiClient(args.base_url, args.timeout)
    if args.task:
        payload = {"task": args.task, "metadata": {"source": "demo-cli"}}
        task_label = "custom"
    else:
        demo_task = find_demo_task(args.tasks_file, args.key)
        metadata = dict(demo_task.get("metadata") or {})
        metadata.setdefault("demo_task_key", demo_task.get("key", args.key))
        metadata.setdefault("demo_task_name", demo_task.get("name", args.key))
        payload = {"task": demo_task["task"], "metadata": metadata}
        task_label = demo_task.get("key", args.key)

    state = client.request("POST", "/run_task", json=payload)
    task_id = state["task_id"]
    print(f"submitted: {task_label}")
    print(f"task_id:   {task_id}")

    if not args.wait:
        print_json(state)
        return 0

    started = time.monotonic()
    while True:
        state = client.request("GET", f"/task/{task_id}")
        status = state["status"]
        print(f"status:    {status}", end="\r", flush=True)
        if status in TERMINAL_STATUSES:
            print()
            logs = client.request("GET", f"/logs?task_id={task_id}&limit=250")
            print_json(summarize_task(state, logs))
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open("w", encoding="utf-8") as fh:
                    json.dump({"task": state, "logs": logs}, fh, indent=2)
                    fh.write("\n")
            return 0 if status == "success" else 1
        if time.monotonic() - started > args.max_wait:
            print()
            print(f"Timed out waiting for task {task_id} after {args.max_wait}s", file=sys.stderr)
            return 124
        time.sleep(args.interval)


def cmd_detail(args: argparse.Namespace) -> int:
    client = ApiClient(args.base_url, args.timeout)
    state = client.request("GET", f"/task/{args.task_id}")
    if args.json:
        print_json(state)
        return 0

    logs = client.request("GET", f"/logs?task_id={args.task_id}&limit=250")
    summary = summarize_task(state, logs)
    print_json(summary)
    print()
    print("Steps")
    for step in state.get("steps", []):
        print(
            f"- #{step.get('step_id')} {step.get('agent')} "
            f"{step.get('status')} retries={step.get('retries', 0)} "
            f"latency={float(step.get('latency') or 0.0):.3f}s"
        )
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    client = ApiClient(args.base_url, args.timeout)
    query = f"?limit={args.limit}"
    if args.task_id:
        query += f"&task_id={args.task_id}"
    logs = client.request("GET", f"/logs{query}")
    if args.json:
        print_json(logs)
        return 0
    for entry in logs.get("logs", []):
        print(
            f"{entry.get('created_at')} "
            f"{entry.get('level', 'INFO'):<5} "
            f"{entry.get('event', ''):<28} "
            f"{entry.get('task_id') or '-'}"
        )
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    client = ApiClient(args.base_url, args.timeout)
    page = client.request("GET", "/tasks")
    if args.json:
        print_json(page)
        return 0
    for task in page.get("tasks", []):
        metrics = task.get("metrics", {})
        print(
            f"{task.get('task_id')} "
            f"{task.get('status'):<8} "
            f"steps={metrics.get('total_steps', 0):<2} "
            f"latency={float(metrics.get('latency') or 0.0):.3f}s "
            f"{task.get('task', '')[:90]}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Demo CLI for the multi-agent task platform.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL.")
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout in seconds.")
    parser.add_argument("--tasks-file", type=Path, default=DEFAULT_TASKS_FILE, help="Demo task JSON file.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List demo tasks.")
    list_parser.set_defaults(func=cmd_list)

    runs_parser = subparsers.add_parser("runs", help="List submitted platform tasks.")
    runs_parser.add_argument("--json", action="store_true", help="Print raw JSON.")
    runs_parser.set_defaults(func=cmd_runs)

    run_parser = subparsers.add_parser("run", help="Run a demo task or custom task.")
    run_parser.add_argument("key", nargs="?", default="simple", help="Demo task key or name.")
    run_parser.add_argument("--task", help="Custom task text. Overrides the demo task key.")
    run_parser.add_argument("--wait", action=argparse.BooleanOptionalAction, default=True, help="Wait for completion.")
    run_parser.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds.")
    run_parser.add_argument("--max-wait", type=float, default=180.0, help="Maximum polling time in seconds.")
    run_parser.add_argument("--output", type=Path, help="Write task and log JSON to a file.")
    run_parser.set_defaults(func=cmd_run)

    detail_parser = subparsers.add_parser("detail", help="Show task detail.")
    detail_parser.add_argument("task_id")
    detail_parser.add_argument("--json", action="store_true", help="Print raw JSON.")
    detail_parser.set_defaults(func=cmd_detail)

    logs_parser = subparsers.add_parser("logs", help="Show platform or task logs.")
    logs_parser.add_argument("--task-id", help="Filter logs by task ID.")
    logs_parser.add_argument("--limit", type=int, default=50, help="Number of log rows.")
    logs_parser.add_argument("--json", action="store_true", help="Print raw JSON.")
    logs_parser.set_defaults(func=cmd_logs)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except requests.HTTPError as exc:
        response = exc.response
        print(f"HTTP {response.status_code}: {response.text}", file=sys.stderr)
        return 1
    except (requests.RequestException, KeyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
