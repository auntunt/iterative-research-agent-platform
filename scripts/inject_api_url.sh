#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${ROOT_DIR}/frontend/config.js"
API_URL="${1:-${API_BASE_URL:-}}"
if [[ -z "${PYTHON_BIN:-}" && -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

usage() {
  cat <<'EOF'
Usage:
  scripts/inject_api_url.sh https://public-api.example.com

Environment:
  API_BASE_URL   Used when no positional URL is provided.
  PYTHON_BIN     Python interpreter used to write the config file.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -z "${API_URL}" ]]; then
  echo "API URL is required. Pass it as an argument or set API_BASE_URL." >&2
  exit 2
fi

if [[ ! "${API_URL}" =~ ^https?:// ]]; then
  echo "API URL must start with http:// or https://: ${API_URL}" >&2
  exit 2
fi

"${PYTHON_BIN}" - "${CONFIG_FILE}" "${API_URL}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

target = Path(sys.argv[1])
api_url = sys.argv[2].rstrip("/")
content = """(function configureDemo(window) {
  window.__APP_CONFIG__ = Object.freeze({
    API_BASE_URL: %s
  });
})(window);
""" % json.dumps(api_url)

target.write_text(content, encoding="utf-8")
print(f"Injected API_BASE_URL into {target}: {api_url}")
PY
