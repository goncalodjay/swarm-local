#!/usr/bin/env zsh
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: swarm-cleanup.sh <herdr-session> <working-dir> [workspace-id ...]" >&2
  exit 1
fi

HERDR_SESSION="$1"
WORKING_DIR="$2"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
shift
shift

has_command() {
  command -v "$1" &>/dev/null
}

if has_command python3; then
  python3 "$SCRIPT_DIR/swarm_python/entrypoints/stop_handoff_daemon.py" "$WORKING_DIR" 2>/dev/null || true
else
  DAEMON_PID_FILE="$WORKING_DIR/.swarmforge/daemon/handoffd.pid"
  if [[ -f "$DAEMON_PID_FILE" ]]; then
    daemon_pid="$(< "$DAEMON_PID_FILE")"
    if [[ "$daemon_pid" == <-> ]]; then
      kill -TERM "$daemon_pid" 2>/dev/null || true
    fi
    rm -f "$DAEMON_PID_FILE"
  fi
fi

for workspace_id in "$@"; do
  herdr --session "$HERDR_SESSION" workspace close "$workspace_id" 2>/dev/null || true
done
