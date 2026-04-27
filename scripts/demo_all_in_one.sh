#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8000}"
LOCAL_API_URL="${LOCAL_API_URL:-http://127.0.0.1:${API_PORT}}"
TASKS_FILE="${TASKS_FILE:-${ROOT_DIR}/demo/tasks.json}"
RESULTS_DIR="${RESULTS_DIR:-${ROOT_DIR}/demo/results/run_$(date +%Y%m%d_%H%M%S)}"
LOG_DIR="${ROOT_DIR}/logs"
LOG_FILE="${LOG_FILE:-${LOG_DIR}/demo.log}"
if [[ -z "${PYTHON_BIN:-}" && -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}"
TUNNEL_TIMEOUT="${TUNNEL_TIMEOUT:-90}"
DEMO_TUNNEL_NAME="${DEMO_TUNNEL_NAME:-${CLOUDFLARE_TUNNEL_NAME:-}}"
DEMO_PUBLIC_API_URL="${DEMO_PUBLIC_API_URL:-${CLOUDFLARE_PUBLIC_URL:-}}"

mkdir -p "${LOG_DIR}" "${RESULTS_DIR}"
: > "${LOG_FILE}"
exec > >(tee -a "${LOG_FILE}") 2>&1

MANAGED_PIDS=()
BACKEND_PID=""
WORKER_PID=""
TUNNEL_PID=""
PUBLIC_API_URL=""

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  log "Cleaning up demo processes..."
  for pid in "${MANAGED_PIDS[@]:-}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
      kill "${pid}" >/dev/null 2>&1 || true
    fi
  done
  for pid in "${MANAGED_PIDS[@]:-}"; do
    if [[ -n "${pid}" ]]; then
      wait "${pid}" >/dev/null 2>&1 || true
    fi
  done
  log "Demo cleanup complete."
  exit "${exit_code}"
}
trap cleanup EXIT INT TERM

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    die "Required command not found: $1"
  fi
}

wait_for_health() {
  local base_url="$1"
  local timeout_seconds="$2"
  local started
  started="$(date +%s)"
  while true; do
    if curl -fsS "${base_url%/}/health" >/dev/null 2>&1; then
      return 0
    fi
    if (( $(date +%s) - started >= timeout_seconds )); then
      return 1
    fi
    sleep 1
  done
}

extract_tunnel_url() {
  "${PYTHON_BIN}" - "${LOG_FILE}" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="ignore")
urls = re.findall(r"https://[A-Za-z0-9.-]+\.trycloudflare\.com", text)
print(urls[-1] if urls else "")
PY
}

wait_for_tunnel_url() {
  local timeout_seconds="$1"
  local started
  started="$(date +%s)"
  while true; do
    PUBLIC_API_URL="$(extract_tunnel_url)"
    if [[ -n "${PUBLIC_API_URL}" ]]; then
      return 0
    fi
    if (( $(date +%s) - started >= timeout_seconds )); then
      return 1
    fi
    sleep 1
  done
}

open_frontend() {
  local url="$1"
  if command -v open >/dev/null 2>&1; then
    open "${url}" >/dev/null 2>&1 || log "Unable to open browser automatically: ${url}"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "${url}" >/dev/null 2>&1 || log "Unable to open browser automatically: ${url}"
  elif command -v start >/dev/null 2>&1; then
    start "${url}" >/dev/null 2>&1 || log "Unable to open browser automatically: ${url}"
  else
    log "No browser opener found. Open this URL manually: ${url}"
  fi
}

start_backend() {
  if wait_for_health "${LOCAL_API_URL}" 2; then
    log "Using existing backend at ${LOCAL_API_URL}"
    return
  fi

  local uvicorn_bin="${UVICORN_BIN:-uvicorn}"
  if [[ -x "${ROOT_DIR}/.venv/bin/uvicorn" && -z "${UVICORN_BIN:-}" ]]; then
    uvicorn_bin="${ROOT_DIR}/.venv/bin/uvicorn"
  fi

  log "Starting FastAPI: uvicorn app.main:app --host ${API_HOST} --port ${API_PORT}"
  "${uvicorn_bin}" app.main:app --host "${API_HOST}" --port "${API_PORT}" >> "${LOG_FILE}" 2>&1 &
  BACKEND_PID="$!"
  MANAGED_PIDS+=("${BACKEND_PID}")

  if ! wait_for_health "${LOCAL_API_URL}" "${HEALTH_TIMEOUT}"; then
    die "Backend did not become healthy at ${LOCAL_API_URL}/health within ${HEALTH_TIMEOUT}s"
  fi
  log "Backend is healthy at ${LOCAL_API_URL}"
}

start_worker_if_present() {
  if [[ -x "${ROOT_DIR}/scripts/worker.sh" ]]; then
    log "Starting external worker: scripts/worker.sh"
    "${ROOT_DIR}/scripts/worker.sh" >> "${LOG_FILE}" 2>&1 &
    WORKER_PID="$!"
    MANAGED_PIDS+=("${WORKER_PID}")
  elif [[ -f "${ROOT_DIR}/scripts/worker.py" ]]; then
    log "Starting external worker: ${PYTHON_BIN} scripts/worker.py"
    "${PYTHON_BIN}" "${ROOT_DIR}/scripts/worker.py" >> "${LOG_FILE}" 2>&1 &
    WORKER_PID="$!"
    MANAGED_PIDS+=("${WORKER_PID}")
  elif [[ -f "${ROOT_DIR}/app/worker.py" ]]; then
    log "Starting external worker: ${PYTHON_BIN} -m app.worker"
    "${PYTHON_BIN}" -m app.worker >> "${LOG_FILE}" 2>&1 &
    WORKER_PID="$!"
    MANAGED_PIDS+=("${WORKER_PID}")
  else
    log "No standalone worker entry point found; FastAPI lifespan starts queue workers."
  fi
}

stop_tunnel_process() {
  if [[ -n "${TUNNEL_PID}" ]] && kill -0 "${TUNNEL_PID}" >/dev/null 2>&1; then
    kill "${TUNNEL_PID}" >/dev/null 2>&1 || true
    wait "${TUNNEL_PID}" >/dev/null 2>&1 || true
  fi
  TUNNEL_PID=""
}

start_quick_tunnel() {
  log "Starting quick Cloudflare Tunnel: cloudflared tunnel --url ${LOCAL_API_URL}"
  cloudflared tunnel --url "${LOCAL_API_URL}" >> "${LOG_FILE}" 2>&1 &
  TUNNEL_PID="$!"
  MANAGED_PIDS+=("${TUNNEL_PID}")
  if ! wait_for_tunnel_url "${TUNNEL_TIMEOUT}"; then
    die "Unable to extract a trycloudflare.com URL from cloudflared output within ${TUNNEL_TIMEOUT}s"
  fi
}

start_cloudflare_tunnel() {
  require_command cloudflared

  if [[ -n "${DEMO_TUNNEL_NAME}" ]]; then
    log "Starting named Cloudflare Tunnel: ${DEMO_TUNNEL_NAME}"
    cloudflared tunnel run "${DEMO_TUNNEL_NAME}" >> "${LOG_FILE}" 2>&1 &
    TUNNEL_PID="$!"
    MANAGED_PIDS+=("${TUNNEL_PID}")

    if [[ -n "${DEMO_PUBLIC_API_URL}" ]]; then
      PUBLIC_API_URL="${DEMO_PUBLIC_API_URL%/}"
      log "Using configured public API URL for named tunnel: ${PUBLIC_API_URL}"
    elif wait_for_tunnel_url 20; then
      log "Extracted named tunnel public API URL: ${PUBLIC_API_URL}"
    else
      log "Named tunnel did not expose a public URL in logs. Falling back to quick tunnel for this demo."
      stop_tunnel_process
      start_quick_tunnel
    fi
  else
    start_quick_tunnel
  fi

  PUBLIC_API_URL="${PUBLIC_API_URL%/}"
  if ! wait_for_health "${PUBLIC_API_URL}" "${HEALTH_TIMEOUT}"; then
    die "Public API did not become healthy at ${PUBLIC_API_URL}/health within ${HEALTH_TIMEOUT}s"
  fi
  log "Cloudflare public API is healthy: ${PUBLIC_API_URL}"
}

print_master_summary() {
  local summary_json="$1"
  if [[ ! -f "${summary_json}" ]]; then
    log "No summary file found at ${summary_json}"
    return
  fi
  "${PYTHON_BIN}" - "${summary_json}" <<'PY'
from __future__ import annotations

import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
print()
print("Demo Summary")
print(f"API URL: {summary['base_url']}")
print(f"Results: {sys.argv[1]}")
for item in summary.get("results", []):
    agents = ", ".join(item.get("agents_used", [])) or "none"
    print(
        f"- {item['task_key']} | task_id={item['task_id']} | "
        f"total_steps={item['total_steps']} | agents={agents} | "
        f"latency={float(item.get('latency') or 0.0):.3f}s | "
        f"retry={str(item.get('retry_happened')).lower()} | "
        f"replan={str(item.get('replan_happened')).lower()} | "
        f"status={item['status']}"
    )
PY
}

main() {
  require_command curl
  require_command "${PYTHON_BIN}"

  log "Demo log: ${LOG_FILE}"
  log "Results directory: ${RESULTS_DIR}"

  start_backend
  start_worker_if_present
  start_cloudflare_tunnel

  log "Injecting public API URL into frontend config."
  "${ROOT_DIR}/scripts/inject_api_url.sh" "${PUBLIC_API_URL}"

  log "Opening frontend: ${PUBLIC_API_URL}"
  open_frontend "${PUBLIC_API_URL}"

  log "Running automated demo tasks."
  set +e
  BASE_URL="${PUBLIC_API_URL}" TASKS_FILE="${TASKS_FILE}" RESULTS_DIR="${RESULTS_DIR}" "${ROOT_DIR}/scripts/run_demo.sh"
  local demo_status=$?
  set -e

  print_master_summary "${RESULTS_DIR}/summary.json"

  if [[ "${demo_status}" -ne 0 ]]; then
    die "Automated demo tasks exited with status ${demo_status}"
  fi

  log "Demo is running. Press CTRL+C to stop backend, worker, and tunnel."
  while true; do
    sleep 3600
  done
}

main "$@"
