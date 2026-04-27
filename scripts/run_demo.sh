#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
TASKS_FILE="${TASKS_FILE:-${ROOT_DIR}/demo/tasks.json}"
RESULTS_DIR="${RESULTS_DIR:-${ROOT_DIR}/demo/results/run_$(date +%Y%m%d_%H%M%S)}"
POLL_INTERVAL="${POLL_INTERVAL:-1}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-180}"
if [[ -z "${PYTHON_BIN:-}" && -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi
TASK_FILTER=""

usage() {
  cat <<'EOF'
Usage:
  scripts/run_demo.sh [--base-url URL] [--tasks-file FILE] [--results-dir DIR] [--task KEY]

Environment:
  BASE_URL          API base URL. Defaults to http://127.0.0.1:8000.
  TASKS_FILE       Demo task definition file.
  RESULTS_DIR      Directory for JSON results.
  POLL_INTERVAL    Poll interval in seconds.
  TIMEOUT_SECONDS  Per-task timeout in seconds.
  PYTHON_BIN       Python interpreter for JSON handling.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      BASE_URL="$2"
      shift 2
      ;;
    --tasks-file)
      TASKS_FILE="$2"
      shift 2
      ;;
    --results-dir)
      RESULTS_DIR="$2"
      shift 2
      ;;
    --task)
      TASK_FILTER="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

BASE_URL="${BASE_URL%/}"
mkdir -p "${RESULTS_DIR}"
SUMMARY_JSONL="${RESULTS_DIR}/summary.jsonl"
SUMMARY_JSON="${RESULTS_DIR}/summary.json"
: > "${SUMMARY_JSONL}"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 127
  fi
}

require_command curl
require_command "${PYTHON_BIN}"

if [[ ! -f "${TASKS_FILE}" ]]; then
  echo "Tasks file not found: ${TASKS_FILE}" >&2
  exit 2
fi

TASK_COUNT="$("${PYTHON_BIN}" - "${TASKS_FILE}" "${TASK_FILTER}" <<'PY'
from __future__ import annotations

import json
import sys

tasks = json.load(open(sys.argv[1], encoding="utf-8"))
task_filter = sys.argv[2]
if task_filter:
    tasks = [task for task in tasks if task.get("key") == task_filter or task.get("name") == task_filter]
print(len(tasks))
PY
)"

if [[ "${TASK_COUNT}" == "0" ]]; then
  echo "No demo tasks matched the requested filter." >&2
  exit 2
fi

echo "Demo API: ${BASE_URL}"
echo "Tasks: ${TASKS_FILE}"
echo "Results: ${RESULTS_DIR}"
echo

for ((index = 0; index < TASK_COUNT; index++)); do
  TASK_PAYLOAD="$("${PYTHON_BIN}" - "${TASKS_FILE}" "${TASK_FILTER}" "${index}" <<'PY'
from __future__ import annotations

import json
import sys

tasks = json.load(open(sys.argv[1], encoding="utf-8"))
task_filter = sys.argv[2]
index = int(sys.argv[3])
if task_filter:
    tasks = [task for task in tasks if task.get("key") == task_filter or task.get("name") == task_filter]
task = tasks[index]
metadata = dict(task.get("metadata") or {})
metadata.setdefault("demo_task_key", task.get("key", f"task-{index + 1}"))
metadata.setdefault("demo_task_name", task.get("name", metadata["demo_task_key"]))
print(json.dumps({"task": task["task"], "metadata": metadata}, separators=(",", ":")))
PY
)"

  TASK_INFO="$("${PYTHON_BIN}" - "${TASKS_FILE}" "${TASK_FILTER}" "${index}" <<'PY'
from __future__ import annotations

import json
import sys

tasks = json.load(open(sys.argv[1], encoding="utf-8"))
task_filter = sys.argv[2]
index = int(sys.argv[3])
if task_filter:
    tasks = [task for task in tasks if task.get("key") == task_filter or task.get("name") == task_filter]
task = tasks[index]
print(f"{task.get('key', f'task-{index + 1}')}|{task.get('name', task.get('key', f'task-{index + 1}'))}")
PY
)"
  TASK_KEY="${TASK_INFO%%|*}"
  TASK_NAME="${TASK_INFO#*|}"
  REQUEST_ID="demo-${TASK_KEY}-$(date +%s)"

  echo "==> Running ${TASK_KEY}: ${TASK_NAME}"
  STARTED_AT="$(date +%s)"
  SUBMIT_RESPONSE="$(curl -sS -X POST "${BASE_URL}/run_task" \
    -H "Content-Type: application/json" \
    -H "x-request-id: ${REQUEST_ID}" \
    --data "${TASK_PAYLOAD}")"

  TASK_ID="$(printf '%s' "${SUBMIT_RESPONSE}" | "${PYTHON_BIN}" -c 'import json,sys; print(json.load(sys.stdin)["task_id"])')"
  echo "    task_id: ${TASK_ID}"

  TASK_RESULT_FILE="${RESULTS_DIR}/${TASK_KEY}_${TASK_ID}.json"
  LOG_RESULT_FILE="${RESULTS_DIR}/${TASK_KEY}_${TASK_ID}_logs.json"

  while true; do
    TASK_STATE="$(curl -sS "${BASE_URL}/task/${TASK_ID}")"
    STATUS="$(printf '%s' "${TASK_STATE}" | "${PYTHON_BIN}" -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
    printf '    status: %s\r' "${STATUS}"

    if [[ "${STATUS}" == "success" || "${STATUS}" == "failed" ]]; then
      printf '\n'
      printf '%s\n' "${TASK_STATE}" > "${TASK_RESULT_FILE}"
      curl -sS "${BASE_URL}/logs?task_id=${TASK_ID}&limit=250" > "${LOG_RESULT_FILE}"
      break
    fi

    NOW="$(date +%s)"
    if (( NOW - STARTED_AT > TIMEOUT_SECONDS )); then
      printf '\n'
      echo "Task timed out after ${TIMEOUT_SECONDS}s: ${TASK_ID}" >&2
      printf '%s\n' "${TASK_STATE}" > "${TASK_RESULT_FILE}"
      curl -sS "${BASE_URL}/logs?task_id=${TASK_ID}&limit=250" > "${LOG_RESULT_FILE}" || true
      exit 124
    fi
    sleep "${POLL_INTERVAL}"
  done

  "${PYTHON_BIN}" - "${TASK_RESULT_FILE}" "${LOG_RESULT_FILE}" "${TASK_KEY}" "${TASK_NAME}" "${SUMMARY_JSONL}" <<'PY'
from __future__ import annotations

import json
import sys

state_path, logs_path, task_key, task_name, summary_path = sys.argv[1:6]
state = json.load(open(state_path, encoding="utf-8"))
logs = json.load(open(logs_path, encoding="utf-8"))
steps = state.get("steps", [])
metrics = state.get("metrics", {})
agents = []
for step in steps:
    agent = step.get("agent", "")
    if agent and agent not in agents:
        agents.append(agent)
log_events = [entry.get("event", "") for entry in logs.get("logs", [])]
retry_happened = bool(metrics.get("retries", 0)) or any((step.get("retries") or 0) > 0 for step in steps)
replan_happened = any(event in {"review_failed_replan", "step_failed_replan"} for event in log_events)
if state.get("task_graph", {}).get("replan_reason"):
    replan_happened = True
summary = {
    "task_key": task_key,
    "task_name": task_name,
    "task_id": state.get("task_id"),
    "status": state.get("status"),
    "total_steps": metrics.get("total_steps", len(steps)),
    "agents_used": agents,
    "latency": metrics.get("latency", 0.0),
    "retry_happened": retry_happened,
    "replan_happened": replan_happened,
    "recovery_happened": retry_happened or replan_happened,
    "result_file": state_path,
    "logs_file": logs_path,
}
with open(summary_path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(summary, separators=(",", ":")) + "\n")
print(
    "    summary: "
    f"steps={summary['total_steps']} "
    f"agents={','.join(agents) or 'none'} "
    f"latency={float(summary['latency']):.3f}s "
    f"retry={str(retry_happened).lower()} "
    f"replan={str(replan_happened).lower()}"
)
PY
  echo
done

"${PYTHON_BIN}" - "${SUMMARY_JSONL}" "${SUMMARY_JSON}" "${BASE_URL}" "${TASKS_FILE}" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

jsonl_path, json_path, base_url, tasks_file = sys.argv[1:5]
items = []
with open(jsonl_path, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if line:
            items.append(json.loads(line))

payload = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "base_url": base_url,
    "tasks_file": tasks_file,
    "results": items,
}
with open(json_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
    fh.write("\n")

print("Demo run complete")
print(f"Summary: {json_path}")
for item in items:
    agents = ",".join(item["agents_used"]) or "none"
    print(
        f"- {item['task_key']}: task_id={item['task_id']} "
        f"steps={item['total_steps']} agents={agents} "
        f"latency={float(item['latency']):.3f}s "
        f"retry={str(item['retry_happened']).lower()} "
        f"replan={str(item['replan_happened']).lower()} "
        f"status={item['status']}"
    )
PY
