#!/usr/bin/env bash
# One-command local app. Does not touch Render. Binds 127.0.0.1 so it
# cannot collide with the process already on :7790.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${LOCAL_PORT:-7791}"
HOST="${LOCAL_HOST:-127.0.0.1}"

port_busy() {
  ss -ltn 2>/dev/null | grep -qE ":${1}[[:space:]]" && return 0
  return 1
}

if port_busy "$PORT"; then
  echo "error: ${HOST}:${PORT} is already in use." >&2
  echo "Stop that process, or run: LOCAL_PORT=<free-port> make dev" >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -e .

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Wrote .env from .env.example — add OPENROUTER_API_KEY for live analyze." >&2
fi

python3 - <<'PY'
from pathlib import Path
text = Path(".env").read_text()
key = ""
for line in text.splitlines():
    if line.startswith("OPENROUTER_API_KEY="):
        key = line.split("=", 1)[1].strip().strip("'").strip('"')
print("OPENROUTER_API_KEY:", "set" if key else "MISSING (live analyze will not work)")
writes = ""
base = ""
for line in text.splitlines():
    if line.startswith("AIA_ALLOW_WRITES="):
        writes = line.split("=", 1)[1].strip().lower()
    if line.startswith("AIA_API_BASE="):
        base = line.split("=", 1)[1].strip()
live = base and writes in {"1", "true", "yes", "on"}
print("AIA live push:", "ON" if live else "off (needs both AIA_API_BASE and AIA_ALLOW_WRITES=true)")
dbg = ""
for line in text.splitlines():
    if line.startswith("BOUNDARY_DEBUG="):
        dbg = line.split("=", 1)[1].strip().lower()
env_dbg = __import__("os").environ.get("BOUNDARY_DEBUG", "").strip().lower()
render = bool(
    __import__("os").environ.get("RENDER")
    or __import__("os").environ.get("RENDER_SERVICE_ID")
)
on = (env_dbg or dbg) in {"1", "true", "yes", "on"} and not render
print("BOUNDARY_DEBUG:", "on (console + session/boundary_trace.txt)" if on else "off")
PY

if [[ ! -f web/dist/index.html ]]; then
  (cd web && npm install && npm run build)
fi

echo "Local app: http://${HOST}:${PORT}  (Render demo is separate; leave it alone)"
exec pdfsplit serve --host "$HOST" --port "$PORT"
