#!/usr/bin/env bash
#
# Preview the SwarmForge TUI without running a real swarm.
#
# The dashboard needs three things to show something interesting: a
# roles.tsv, a live tmux socket, and one session per role. Launching the
# real swarm provides all three, but it also starts four paid agents. This
# script fakes the tmux side with placeholder sessions so the TUI can be
# exercised for free, and tears them down on exit.
#
# Usage:
#   tui/dev-preview.sh              # run from source, no compile (fastest)
#   tui/dev-preview.sh --binary     # compile and run the shipped binary
#   tui/dev-preview.sh --install    # compile, install into .swarmforge, run ./swarm tui
#   tui/dev-preview.sh --real       # do not fake anything; use whatever is running
#
set -euo pipefail

TUI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TUI_DIR/.." && pwd)"
PROJECT="${PROJECT:-$REPO_DIR}"

MODE="source"
FAKE_SESSIONS=1

while (($#)); do
  case "$1" in
    --binary) MODE="binary"; shift ;;
    --install) MODE="install"; shift ;;
    --real) FAKE_SESSIONS=0; shift ;;
    --project) PROJECT="$(cd "$2" && pwd)"; shift 2 ;;
    -h|--help) awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

ROLES_FILE="$PROJECT/.swarmforge/roles.tsv"
if [[ ! -f "$ROLES_FILE" ]]; then
  echo "No SwarmForge project at $PROJECT (missing .swarmforge/roles.tsv)." >&2
  echo "Run ./swarm --test-parse there first." >&2
  exit 1
fi

build() {
  echo "==> building"
  (cd "$TUI_DIR" && npm run --silent build)
}

install_binary() {
  local target="$PROJECT/.swarmforge/tui/swarm-tui"
  # install refuses to overwrite, which is right for real installs and
  # wrong for a dev loop, so clear the previous artifact first.
  rm -f "$target"
  node "$TUI_DIR/src/install.ts" "$TUI_DIR/dist/swarm-tui" "$PROJECT"
}

# --- placeholder tmux sessions ------------------------------------------

SOCKET=""
CREATED=()

start_placeholders() {
  SOCKET="$(cat "$PROJECT/.swarmforge/tmux-socket" 2>/dev/null || true)"
  if [[ -z "$SOCKET" ]]; then
    echo "No tmux socket recorded; run ./swarm --test-parse in $PROJECT." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$SOCKET")"
  while IFS=$'\t' read -r _role _wt _path session _rest; do
    [[ -n "$session" ]] || continue
    if tmux -S "$SOCKET" has-session -t "$session" 2>/dev/null; then
      echo "    $session already running, leaving it alone"
      continue
    fi
    tmux -S "$SOCKET" new-session -d -s "$session" "sleep 86400"
    CREATED+=("$session")
  done < "$ROLES_FILE"
  echo "==> placeholder sessions: ${CREATED[*]:-none}"
}

cleanup() {
  local status=$?
  # CREATED is always declared, but may be empty; guard the expansion so
  # `set -u` does not trip on an empty array.
  if ((${#CREATED[@]} > 0)); then
    for session in "${CREATED[@]}"; do
      tmux -S "$SOCKET" kill-session -t "$session" 2>/dev/null || true
    done
    echo "==> removed placeholder sessions"
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

# --- run -----------------------------------------------------------------

((FAKE_SESSIONS)) && start_placeholders

case "$MODE" in
  source)
    echo "==> running from source (no compile)"
    (cd "$PROJECT" && "$REPO_DIR/node_modules/.bin/bun" "$TUI_DIR/main.ts")
    ;;
  binary)
    build
    echo "==> running dist/swarm-tui"
    (cd "$PROJECT" && "$TUI_DIR/dist/swarm-tui")
    ;;
  install)
    build
    install_binary
    echo "==> running ./swarm tui"
    (cd "$PROJECT" && ./swarm tui)
    ;;
esac
