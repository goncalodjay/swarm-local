#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/lib/python.sh"
find_python || { echo "Python 3 is required (>= 3.10)." >&2; exit 1; }
exec "$PYTHON" "$SCRIPT_DIR/swarm_python/entrypoints/stop_handoff_daemon.py" "$@"
