#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
probe_python=python3
if [[ -x .venv/bin/python ]]; then probe_python=.venv/bin/python; fi
api_status=0
"$probe_python" scripts/dev-status.py api || api_status=$?
web_status=0
"$probe_python" scripts/dev-status.py web || web_status=$?
if [[ "$api_status" != 0 && "$api_status" != 10 ]]; then
  echo 'Port 8000 is occupied by another service or an outdated/unhealthy API. Stop that process before starting ReturnRadar.' >&2
  exit 1
fi
if [[ "$web_status" != 0 && "$web_status" != 10 ]]; then
  echo 'Port 5173 is occupied by another service. Stop that process before starting ReturnRadar.' >&2
  exit 1
fi
if [[ "$api_status" == 0 && "$web_status" == 0 ]]; then
  if ! "$probe_python" scripts/dev-status.py proxy; then
    echo 'ReturnRadar is running, but its frontend cannot reach the API. Restart the existing app.' >&2
    exit 1
  fi
  echo 'ReturnRadar is already running: http://127.0.0.1:5173/'
  exit 0
fi
if ! command -v node >/dev/null 2>&1; then
  if [[ -x /private/tmp/node-v22.16.0-darwin-arm64/bin/node ]]; then
    export PATH="/private/tmp/node-v22.16.0-darwin-arm64/bin:$PATH"
  else
    echo 'Install Node.js 22 or later, then rerun this script.' >&2
    exit 1
  fi
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
if [[ ! -d node_modules ]]; then npm ci; fi
if [[ ! -f .env ]]; then cp .env.example .env; fi
api_pid=''
cleanup() {
  if [[ -n "$api_pid" ]]; then
    kill "$api_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ "$api_status" == 10 ]]; then
  .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --env-file .env --reload --reload-dir backend &
  api_pid=$!
  ready=false
  for ((attempt=0; attempt<40; attempt++)); do
    if ! kill -0 "$api_pid" 2>/dev/null; then
      echo 'The API failed to start. Resolve the error above before retrying.' >&2
      exit 1
    fi
    if .venv/bin/python scripts/dev-status.py api; then ready=true; break; fi
    sleep 0.25
  done
  if [[ "$ready" != true ]]; then
    echo 'The API did not become ready. Check the startup error above.' >&2
    exit 1
  fi
else
  echo 'Using the existing ReturnRadar API on port 8000.'
fi
if [[ "$web_status" == 0 ]]; then
  echo 'Using the existing frontend: http://127.0.0.1:5173/'
  # Keep the newly started API attached to this terminal.
  wait "$api_pid"
else
  npm run dev -- --port 5173 --strictPort
fi
