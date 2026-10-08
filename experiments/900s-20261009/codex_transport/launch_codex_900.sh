#!/usr/bin/env bash
set -euo pipefail
: "${CIPHEUR_EXPERIMENT_ROOT:?Set the isolated experiment directory}"
ROOT="$CIPHEUR_EXPERIMENT_ROOT"
RUNNER="${CIPHEUR_CODEX_RUNNER:-$ROOT/scripts/run_codex_900.py}"
LOG="$ROOT/logs/codex_master.log"
PID_FILE="$ROOT/registrations/codex_master.pid"
PYTHON_BIN="${CIPHEUR_PYTHON_BIN:-python3}"
test -f "$RUNNER"
test ! -e "$PID_FILE"
mkdir -p "$ROOT/logs"
nohup "$PYTHON_BIN" -B "$RUNNER" > "$LOG" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > "$PID_FILE"
sleep 2
kill -0 "$pid"
printf '{"codex_master_pid":%s,"log":"%s"}\n' "$pid" "$LOG"
