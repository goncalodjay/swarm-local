#!/usr/bin/env bash
#
# install_swarm.sh — entry point for installing SwarmForge on a brand-new
# machine in a single step.
#
# What it does:
#   1. Detects the host platform (OS + arch).
#   2. Downloads the matching prebuilt TUI binary from the configured GitHub
#      release, verifies its sha256, and stages it where swarm-init can find it.
#   3. If the download fails (or no release is published yet for this triple),
#      falls back to building the TUI locally with Bun (still requires Node).
#   4. Runs ./swarm-init on the requested target directory, or
#      ./swarm-init --update when it already holds an install.
#
# Usage:
#   ./install_swarm.sh                     # install into the current dir
#   ./install_swarm.sh /path/to/project    # install into a project dir
#   ./install_swarm.sh --yes /path         # update an existing install without
#                                          # the confirmation prompt (CI / no tty)
#   SWARM_RELEASE_TAG=v1.2.3 ./install_swarm.sh
#   SWARM_RELEASE_REPO=owner/swarm-local ./install_swarm.sh
#
# Required on the destination: bash, curl, tar (for tarball fallback), python3,
# herdr, engram, git, plus the agent CLIs you plan to use (opencode, codex, claude,
# hermes, copilot, grok, pi). Node is only required when the release does not
# provide a binary for this platform and the script has to build it; Bun is
# installed automatically in that case if it is missing.

set -euo pipefail

RELEASE_REPO="${SWARM_RELEASE_REPO:-goncalodjay/swarm-local}"
RELEASE_TAG="${SWARM_RELEASE_TAG:-latest}"
TARGET_DIR=""
UPDATE_ARGS=()
while (($#)); do
  case "$1" in
  --yes | -y)
    UPDATE_ARGS+=(--yes)
    ;;
  --languages | -l)
    [[ $# -ge 2 ]] || {
      echo "Missing value for $1" >&2
      exit 2
    }
    UPDATE_ARGS+=(--languages "$2")
    shift
    ;;
  -*)
    echo "Unknown option: $1" >&2
    exit 2
    ;;
  *)
    TARGET_DIR="$1"
    ;;
  esac
  shift
done
TARGET_DIR="${TARGET_DIR:-$PWD}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TUI_BUNDLE_NAME="swarm-tui"
LOCAL_BUNDLE="$SCRIPT_DIR/tui/dist/swarm-tui"
PLATFORM=""

mprintf() { printf "$@" >&2; }

header() {
  printf '\033[1;7m SwarmForge installer \033[0m\n' >&2
  printf '\033[2m%s\033[0m\n\n' "$1" >&2
}

err() { printf '\033[31mError:\033[0m %s\n' "$1" >&2; exit 1; }

# Windows ships python.exe only; python3 may resolve to the Microsoft Store
# stub ("App execution alias"), which prints an error and exits non-zero.
# Resolve a real, runnable Python 3 and cache it in $PYTHON.
find_python() {
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys' >/dev/null 2>&1; then
    PYTHON=python3
    return 0
  fi
  if command -v python >/dev/null 2>&1 && python -c 'import sys' >/dev/null 2>&1; then
    PYTHON=python
    return 0
  fi
  PYTHON=""
  return 1
}

detect_platform() {
  local os arch
  case "$(uname -s)" in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
    MINGW*|MSYS*|CYGWIN*) os=windows ;;
    *) err "Unsupported OS: $(uname -s)" ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch=x64 ;;
    arm64|aarch64) arch=arm64 ;;
    *) err "Unsupported architecture: $(uname -m)" ;;
  esac
  printf '%s-%s' "$os" "$arch"
}

require_basic_tools() {
  local missing=()
  for tool in bash curl herdr engram git; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
  done
  find_python || missing+=("python3")
  if ((${#missing[@]} > 0)); then
    err "Missing required tools: ${missing[*]}. Install them and retry."
  fi
}

fetch_release_bundle() {
  local triple="$1"
  local asset_name="${TUI_BUNDLE_NAME}-${triple}.bin"
  local sha_name="${asset_name}.sha256"
  local url_base
  if [[ "$RELEASE_TAG" == "latest" ]]; then
    url_base="https://github.com/${RELEASE_REPO}/releases/latest/download"
  else
    url_base="https://github.com/${RELEASE_REPO}/releases/download/${RELEASE_TAG}"
  fi
  local asset_url="${url_base}/${asset_name}"
  local sha_url="${url_base}/${sha_name}"

  mprintf 'Downloading %s from %s …\n' "$asset_name" "$asset_url"
  if ! curl -fsSL --retry 3 -o "$TMPDIR/$asset_name" "$asset_url"; then
    return 1
  fi
  if curl -fsSL --retry 3 -o "$TMPDIR/$sha_name" "$sha_url"; then
    local want got
    want="$(awk '{print $1}' "$TMPDIR/$sha_name")"
    got="$(sha256sum "$TMPDIR/$asset_name" | awk '{print $1}')"
    if [[ "$want" != "$got" ]]; then
      mprintf 'sha256 mismatch (expected %s, got %s)\n' "$want" "$got"
      return 1
    fi
    mprintf 'sha256 verified.\n'
  else
    mprintf 'No sha256 published — skipping integrity check.\n'
  fi
  install -m 0755 "$TMPDIR/$asset_name" "$LOCAL_BUNDLE"
}

install_bun() {
  mprintf 'Bun not found; installing it from https://bun.sh …\n'
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      powershell -NoProfile -ExecutionPolicy Bypass -c "irm bun.sh/install.ps1 | iex" \
        || err "Bun install failed. Install it manually from https://bun.sh/ and retry."
      ;;
    *)
      curl -fsSL https://bun.sh/install | bash \
        || err "Bun install failed. Install it manually from https://bun.sh/ and retry."
      ;;
  esac
  export BUN_INSTALL="${BUN_INSTALL:-$HOME/.bun}"
  export PATH="$BUN_INSTALL/bin:$PATH"
  command -v bun >/dev/null 2>&1 || err "Bun was installed but is not on PATH (looked in $BUN_INSTALL/bin)."
  mprintf 'Bun %s ready.\n' "$(bun --version)"
}

build_bundle_locally() {
  if [[ -x "$LOCAL_BUNDLE" ]]; then
    mprintf 'Reusing existing TUI bundle at %s\n' "$LOCAL_BUNDLE"
    return 0
  fi
  mprintf '\nNo release bundle for this platform; building TUI from source.\n'
  command -v node >/dev/null 2>&1 || err "Node is required to build the TUI (https://nodejs.org/)."
  command -v bun >/dev/null 2>&1 || install_bun
  (cd "$SCRIPT_DIR/tui" && npm install --no-audit --no-fund && npm run build)
  # Bun's --compile appends .exe on Windows, and Windows needs that suffix to
  # run the binary.
  resolve_local_bundle
}

# Bun's --compile appends .exe on Windows: point LOCAL_BUNDLE at the file that
# actually exists, so the installer neither rebuilds the TUI nor installs a
# binary that cannot be launched.
resolve_local_bundle() {
  if [[ "$PLATFORM" == windows-* && -f "$LOCAL_BUNDLE.exe" ]]; then
    LOCAL_BUNDLE="$LOCAL_BUNDLE.exe"
  fi
}

stage_from_manifest() {
  # Fallback: if the release ships a manifest.json, look the entry up by
  # <os>-<arch> instead of guessing the asset name. Lets publishers change
  # asset naming without breaking installers.
  local triple="$1"
  local tmpdir="$2"
  local manifest_url
  if [[ "$RELEASE_TAG" == "latest" ]]; then
    manifest_url="https://github.com/${RELEASE_REPO}/releases/latest/download/manifest.json"
  else
    manifest_url="https://github.com/${RELEASE_REPO}/releases/download/${RELEASE_TAG}/manifest.json"
  fi
  if ! curl -fsSL --retry 3 -o "$tmpdir/manifest.json" "$manifest_url"; then
    return 1
  fi
  find_python || return 1
  local asset_url
  asset_url="$("$PYTHON" - "$tmpdir/manifest.json" "$triple" <<'PY'
import json, sys, pathlib
manifest = json.loads(pathlib.Path(sys.argv[1]).read_text())
triple = sys.argv[2]
for entry in manifest.get("binaries", []):
    name = entry["name"]
    base = name.rsplit(".", 1)[0]  # strip .bin / .exe
    parts = base.split("-")
    if len(parts) >= 3 and f"{parts[-2]}-{parts[-1]}" == triple:
        print(name)
        break
PY
)"
  [[ -n "$asset_url" ]] || return 1
  mprintf 'Manifest points at %s for %s.\n' "$asset_url" "$triple"
  curl -fsSL --retry 3 -o "$tmpdir/$asset_url" "https://github.com/${RELEASE_REPO}/releases/download/${RELEASE_TAG}/$asset_url"
  local sha
  sha="$("$PYTHON" - "$tmpdir/manifest.json" "$asset_url" <<'PY'
import json, sys, pathlib
manifest = json.loads(pathlib.Path(sys.argv[1]).read_text())
target = sys.argv[2]
for entry in manifest.get("binaries", []):
    if entry["name"] == target:
        print(entry["sha256"])
        break
PY
)"
  if [[ -n "$sha" ]]; then
    local got
    got="$(sha256sum "$tmpdir/$asset_url" | awk '{print $1}')"
    if [[ "$sha" != "$got" ]]; then
      mprintf 'sha256 mismatch from manifest (expected %s, got %s)\n' "$sha" "$got"
      return 1
    fi
    mprintf 'sha256 verified from manifest.\n'
  fi
  install -m 0755 "$tmpdir/$asset_url" "$LOCAL_BUNDLE"
}

header "Target: ${TARGET_DIR}"
require_basic_tools

if [[ ! -d "$SCRIPT_DIR" ]] || [[ ! -f "$SCRIPT_DIR/swarm-init" ]]; then
  err "install_swarm.sh must live alongside swarm-init (got SCRIPT_DIR=$SCRIPT_DIR)."
fi

mkdir -p "$(dirname "$LOCAL_BUNDLE")"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

PLATFORM="$(detect_platform)"
resolve_local_bundle
mprintf 'Detected platform: %s\n\n' "$PLATFORM"

TUI_OK=0
if fetch_release_bundle "$PLATFORM" 2>/dev/null; then
  TUI_OK=1
fi
if ((!TUI_OK)) && stage_from_manifest "$PLATFORM" "$TMPDIR" 2>/dev/null; then
  TUI_OK=1
fi

if ((!TUI_OK)); then
  build_bundle_locally || err "Failed to obtain a TUI bundle for $PLATFORM."
fi

[[ -x "$LOCAL_BUNDLE" ]] || err "TUI bundle missing or not executable at $LOCAL_BUNDLE."

# swarm-init installs the Gherkin toolchain build matching this platform.
export SWARMFORGE_PLATFORM="$PLATFORM"

# An existing install is UPDATED in place (scripts, role prompts, constitution,
# launchers, TUI, toolchain; swarmforge.conf is reconciled, not overwritten).
# swarm-init --update checks for a running swarm and uncommitted work first and
# asks for confirmation (or needs --yes without a terminal).
if [[ -f "$TARGET_DIR/swarmforge/swarmforge.conf" ]]; then
  mprintf '\nInstalación existente encontrada; actualizando %s ...\n' "$TARGET_DIR"
  exec "$SCRIPT_DIR/swarm-init" --update ${UPDATE_ARGS[@]+"${UPDATE_ARGS[@]}"} "$TARGET_DIR"
fi

mprintf '\nRunning swarm-init on %s …\n' "$TARGET_DIR"
exec "$SCRIPT_DIR/swarm-init" ${UPDATE_ARGS[@]+"${UPDATE_ARGS[@]}"} "$TARGET_DIR"
