#!/usr/bin/env bash
# R9.3A local preview: an isolated backend (:8093) and frontend (:3093) running on a COPY of the data.
#
#   scripts/r93a-preview.sh up        start both (refuses if either port is busy; never stops anything else)
#   scripts/r93a-preview.sh down      stop only what this script started
#   scripts/r93a-preview.sh status    ports, pids, URLs
#   scripts/r93a-preview.sh reset     stop, then restore the preview database from its verified snapshot
#   scripts/r93a-preview.sh verify    check the preview database against its recorded checksum (read-only)
#
# It reads and writes ONLY $PIVOT_R93A_DATA (default ~/pivot-r9-3a-data). It never opens the owner's
# development database, never touches the servers on :8000/:8010/:3000, and never needs a provider key:
# every GOAT figure it shows was ingested into the preview database earlier, server-side.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${PIVOT_R93A_DATA:-$HOME/pivot-r9-3a-data}"
DB="${PIVOT_R93A_DB:-$DATA/preview.db}"
READY="${PIVOT_R93A_READY:-$DATA/backups/preview-ready.db}"
API_PORT="${PIVOT_R93A_API_PORT:-8093}"
WEB_PORT="${PIVOT_R93A_WEB_PORT:-3093}"
RUN="$DATA/run"

py() {
  for candidate in "$ROOT/backend/.venv/bin" "${PIVOT_VENV:-}/bin" "$HOME/pivot-work/pivot/backend/.venv/bin"; do
    if [ -x "$candidate/$1" ]; then echo "$candidate/$1"; return; fi
  done
  echo "no backend virtualenv found (looked for $1 in backend/.venv, \$PIVOT_VENV, ~/pivot-work/pivot/backend/.venv)" >&2
  exit 3
}

busy() { lsof -tiTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

wait_http() { # url, seconds
  for _ in $(seq 1 "$2"); do
    if curl -fsS -o /dev/null --max-time 3 "$1" 2>/dev/null; then return 0; fi
    sleep 1
  done
  return 1
}

cmd_up() {
  [ -f "$DB" ] || { echo "preview database not found at $DB — see docs/r9-3a/local-preview-and-activation.md (restore it with: $0 reset)" >&2; exit 2; }
  for port in "$API_PORT" "$WEB_PORT"; do
    if busy "$port" && ! alive "$RUN/api.pid" && ! alive "$RUN/web.pid"; then
      echo "port $port is already in use by something else; set PIVOT_R93A_API_PORT / PIVOT_R93A_WEB_PORT or stop it yourself. Nothing was started." >&2
      exit 2
    fi
  done
  mkdir -p "$RUN"
  if ! alive "$RUN/api.pid"; then
    (
      cd "$ROOT/backend"
      DATABASE_URL="sqlite:///$DB" \
      CONTRACT_DATA_PROVIDER=balldontlie \
      PLAYER_INTELLIGENCE_PREVIEW=true \
      CORS_ORIGINS="http://localhost:$WEB_PORT" \
      PYTHONPATH="$ROOT/backend" \
      nohup "$(py uvicorn)" app.main:app --port "$API_PORT" >"$RUN/api.log" 2>&1 &
      echo $! >"$RUN/api.pid"
    )
  fi
  if ! alive "$RUN/web.pid"; then
    flags=""
    # Turbopack refuses a node_modules symlink that points outside the project root; webpack does not.
    [ -L "$ROOT/frontend/node_modules" ] && flags="--webpack"
    (
      cd "$ROOT/frontend"
      NEXT_PUBLIC_API_URL="http://localhost:$API_PORT" \
      nohup npx next dev $flags -p "$WEB_PORT" >"$RUN/web.log" 2>&1 &
      echo $! >"$RUN/web.pid"
    )
  fi
  wait_http "http://localhost:$API_PORT/api/v1/teams" 40 || { echo "backend did not come up; see $RUN/api.log" >&2; exit 1; }
  wait_http "http://localhost:$WEB_PORT/" 90 || { echo "frontend did not come up; see $RUN/web.log" >&2; exit 1; }
  cat <<MSG
Pivot R9.3A preview is running.
  Open:      http://localhost:$WEB_PORT/salary-cap-center
             http://localhost:$WEB_PORT/trade-evaluator
             http://localhost:$WEB_PORT/player-explorer
  Backend:   http://localhost:$API_PORT/api/v1/teams   (database: $DB)
  Flags:     CONTRACT_DATA_PROVIDER=balldontlie  PLAYER_INTELLIGENCE_PREVIEW=true   (set for this preview only)
  Logs:      $RUN/api.log  $RUN/web.log
  Stop:      $0 down
MSG
}

cmd_down() {
  for name in web api; do
    pidfile="$RUN/$name.pid"
    if alive "$pidfile"; then
      pid="$(cat "$pidfile")"
      pkill -P "$pid" 2>/dev/null || true   # children of the process we started (next dev spawns workers)
      kill "$pid" 2>/dev/null || true
      echo "stopped $name (pid $pid)"
    fi
    rm -f "$pidfile"
  done
}

cmd_status() {
  for pair in "api:$API_PORT" "web:$WEB_PORT"; do
    name="${pair%%:*}"; port="${pair##*:}"
    if alive "$RUN/$name.pid"; then echo "$name: running (pid $(cat "$RUN/$name.pid")) on :$port"
    elif busy "$port"; then echo "$name: port $port is in use by another process (not started by this script)"
    else echo "$name: stopped"; fi
  done
}

cmd_verify() {
  [ -f "$READY" ] || { echo "no snapshot at $READY" >&2; exit 2; }
  expected="$(cut -d' ' -f1 "$READY.sha256")"
  actual="$(shasum -a 256 "$READY" | cut -d' ' -f1)"
  if [ "$expected" = "$actual" ]; then echo "snapshot OK ($actual)"; else echo "snapshot checksum MISMATCH" >&2; exit 1; fi
  sqlite3 "file:$READY?mode=ro" "pragma integrity_check;" | head -1
}

cmd_reset() {
  cmd_verify
  cmd_down
  cp "$READY" "$DB"
  echo "preview database restored from $READY"
}

case "${1:-}" in
  up) cmd_up ;;
  down) cmd_down ;;
  status) cmd_status ;;
  reset) cmd_reset ;;
  verify) cmd_verify ;;
  *) sed -n '2,14p' "$0"; exit 2 ;;
esac
