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
#   4. Runs ./swarm-init on the requested target directory.
#
# Usage:
#   ./install_swarm.sh                     # install into the current dir
#   ./install_swarm.sh /path/to/project    # install into a project dir
#   SWARM_RELEASE_TAG=v1.2.3 ./install_swarm.sh
#   SWARM_RELEASE_REPO=owner/swarm-local ./install_swarm.sh
#
# Required on the destination: bash, curl, tar (for tarball fallback), python3,
# tmux, git, plus the agent CLIs you plan to use (opencode, codex, claude,
# hermes, copilot, grok, pi). Node and Bun are only required when the release
# does not provide a binary for this platform and the script has to build it.

set -euo pipefail

RELEASE_REPO="${SWARM_RELEASE_REPO:-nousresearch/swarm-local}"
RELEASE_TAG="${SWARM_RELEASE_TAG:-latest}"
TARGET_DIR="${1:-$PWD}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TUI_BUNDLE_NAME="swarm-tui"
LOCAL_BUNDLE="$SCRIPT_DIR/tui/dist/swarm-tui"

mprintf() { printf "$@" >&2; }

header() {
  printf '\033[1;7m SwarmForge installer \033[0m\n' >&2
  printf '\033[2m%s\033[0m\n\n' "$1" >&2
}

err() { printf '\033[31mError:\033[0m %s\n' "$1" >&2; exit 1; }

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
  for tool in bash curl python3 tmux git; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
  done
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

build_bundle_locally() {
  if [[ -x "$LOCAL_BUNDLE" ]]; then
    mprintf 'Reusing existing TUI bundle at %s\n' "$LOCAL_BUNDLE"
    return 0
  fi
  mprintf '\nNo release bundle for this platform; building TUI from source.\n'
  command -v node >/dev/null 2>&1 || err "Node is required to build the TUI (https://nodejs.org/)."
  command -v bun >/dev/null 2>&1 || err "Bun is required to build the TUI (https://bun.sh/)."
  (cd "$SCRIPT_DIR/tui" && npm install --no-audit --no-fund && npm run build)
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
  if ! command -v python3 >/dev/null 2>&1; then
    return 1
  fi
  local asset_url
  asset_url="$(python3 - "$tmpdir/manifest.json" "$triple" <<'PY'
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
  sha="$(python3 - "$tmpdir/manifest.json" "$asset_url" <<'PY'
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

mprintf '\nRunning swarm-init on %s …\n' "$TARGET_DIR"
exec "$SCRIPT_DIR/swarm-init" "$TARGET_DIR"
