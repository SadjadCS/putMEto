#!/usr/bin/env bash
# Start, stop, and check the PutMeTo server.
#
#   ./putmeto.sh start     Start PutMeTo in the background
#   ./putmeto.sh stop      Stop it the way Ctrl+C does, so the LinkedIn browser closes cleanly
#   ./putmeto.sh restart   Stop, then start (loads code changes)
#   ./putmeto.sh status    Show whether it is running
#   ./putmeto.sh logs      Follow the server log
#
# Uses PUTMETO_PORT (default 8000) and PUTMETO_DATA_DIR (default ./data), like run.py.
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PUTMETO_PORT:-8000}"
DATA_DIR="${PUTMETO_DATA_DIR:-$PWD/data}"
LOG="$DATA_DIR/putmeto.log"
PYTHON=".venv/bin/python"
URL="http://127.0.0.1:$PORT"

# The process serving the port, however it was started (this script, a terminal, or an editor).
server_pid() {
  lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
}

is_putmeto() {
  ps -o command= -p "$1" 2>/dev/null | grep -qE "run\.py|backend\.main"
}

start() {
  local pid
  pid="$(server_pid)"
  if [ -n "$pid" ]; then
    if is_putmeto "$pid"; then
      echo "PutMeTo is already running at $URL (process $pid)."
      return 0
    fi
    echo "Port $PORT is used by another program (process $pid). Stop it, or start PutMeTo with PUTMETO_PORT=8001 $0 start."
    exit 1
  fi
  if [ ! -x "$PYTHON" ]; then
    echo "Missing $PYTHON. Set it up first:"
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
  fi
  mkdir -p "$DATA_DIR"
  chmod 700 "$DATA_DIR"
  nohup "$PYTHON" run.py >>"$LOG" 2>&1 </dev/null &
  local launched=$!
  for _ in $(seq 1 30); do
    if curl -fs -m 2 -o /dev/null "$URL/api/health" 2>/dev/null; then
      echo "PutMeTo is running at $URL (process $launched). Log: $LOG"
      return 0
    fi
    if ! kill -0 "$launched" 2>/dev/null; then
      echo "PutMeTo stopped while starting. Last log lines:"
      tail -n 20 "$LOG"
      exit 1
    fi
    sleep 1
  done
  echo "PutMeTo did not answer within 30 seconds; it may still be starting. Check $LOG"
  exit 1
}

stop() {
  local pid
  pid="$(server_pid)"
  if [ -z "$pid" ]; then
    echo "PutMeTo is not running on port $PORT."
    return 0
  fi
  if ! is_putmeto "$pid"; then
    echo "Port $PORT is used by another program (process $pid), not PutMeTo. Nothing was stopped."
    exit 1
  fi
  # Interrupt like Ctrl+C, so the app stops background searches and closes the
  # LinkedIn browser itself, which keeps your sign-in saved.
  kill -INT "$pid"
  for _ in $(seq 1 30); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "PutMeTo stopped."
      return 0
    fi
    sleep 1
  done
  echo "Still shutting down after 30 seconds; asking it to finish."
  kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 1 10); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "PutMeTo stopped."
      return 0
    fi
    sleep 1
  done
  # A forced stop could leave the LinkedIn browser profile unsaved, so that is left to you.
  echo "PutMeTo did not stop (process $pid). If you must, force it with: kill -9 $pid"
  exit 1
}

status() {
  local pid
  pid="$(server_pid)"
  if [ -n "$pid" ] && is_putmeto "$pid"; then
    echo "PutMeTo is running at $URL (process $pid)."
  elif [ -n "$pid" ]; then
    echo "PutMeTo is not running; port $PORT is used by another program (process $pid)."
    exit 1
  else
    echo "PutMeTo is not running."
    exit 1
  fi
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop && start ;;
  status) status ;;
  logs) touch "$LOG" && tail -n 50 -f "$LOG" ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs}"
    exit 2
    ;;
esac
