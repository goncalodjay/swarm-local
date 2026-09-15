#!/usr/bin/env bash
#
# Preview the SwarmForge TUI without running a real swarm.
#
# The dashboard needs three things to show something interesting: a
# roles.tsv, a running herdr session, and one workspace per role. Launching
# the real swarm provides all three, but it also starts four paid agents.
# This script fakes the herdr side with placeholder workspaces so the TUI
# can be exercised for free, and tears them down on exit.
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
FAKE_WORKSPACES=1

while (($#)); do
  case "$1" in
    --binary) MODE="binary"; shift ;;
    --install) MODE="install"; shift ;;
    --real) FAKE_WORKSPACES=0; shift ;;
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

# --- placeholder herdr workspaces ----------------------------------------

HERDR_SESSION=""
CREATED=()

start_placeholders() {
  HERDR_SESSION="$(cat "$PROJECT/.swarmforge/herdr-session" 2>/dev/null || true)"
  if [[ -z "$HERDR_SESSION" ]]; then
    echo "No herdr session recorded; run ./swarm --test-parse in $PROJECT." >&2
    exit 1
  fi
  if ! herdr --session "$HERDR_SESSION" workspace list >/dev/null 2>&1; then
    herdr --session "$HERDR_SESSION" server >/dev/null 2>&1 &
    for _ in $(seq 1 50); do
      herdr --session "$HERDR_SESSION" workspace list >/dev/null 2>&1 && break
      sleep 0.2
    done
  fi

  while IFS=$'\t' read -r role wt path session display agent receive pane_id workspace_id; do
    [[ -n "$role" ]] || continue
    if [[ -n "$workspace_id" ]] && herdr --session "$HERDR_SESSION" workspace get "$workspace_id" >/dev/null 2>&1; then
      echo "    $session already running, leaving it alone"
      continue
    fi
    local created
    created="$(herdr --session "$HERDR_SESSION" workspace create --cwd "$PROJECT" --label "$session" --no-focus)"
    workspace_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["result"]["workspace"]["workspace_id"])' "$created")"
    pane_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["result"]["root_pane"]["pane_id"])' "$created")"
    herdr --session "$HERDR_SESSION" pane run "$pane_id" "sleep 86400" >/dev/null 2>&1 || true
    CREATED+=("$workspace_id")
    python3 - "$ROLES_FILE" "$role" "$pane_id" "$workspace_id" <<'PY'
import sys
path, role, pane_id, workspace_id = sys.argv[1:5]
lines = open(path, encoding="utf-8").read().splitlines()
out = []
for line in lines:
    fields = line.split("\t")
    if fields and fields[0] == role:
        while len(fields) < 9:
            fields.append("")
        fields[7] = pane_id
        fields[8] = workspace_id
        line = "\t".join(fields)
    out.append(line)
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
  done < "$ROLES_FILE"
  echo "==> placeholder workspaces: ${CREATED[*]:-none}"
}

cleanup() {
  local status=$?
  # CREATED is always declared, but may be empty; guard the expansion so
  # `set -u` does not trip on an empty array.
  if ((${#CREATED[@]} > 0)); then
    for workspace_id in "${CREATED[@]}"; do
      herdr --session "$HERDR_SESSION" workspace close "$workspace_id" 2>/dev/null || true
    done
    echo "==> removed placeholder workspaces"
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

# --- run -----------------------------------------------------------------

((FAKE_WORKSPACES)) && start_placeholders

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
