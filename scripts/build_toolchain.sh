#!/usr/bin/env bash
# Builds the Gherkin toolchain (gherkin-parser, gherkin-mutator,
# gherkin-ir-dry-checker) from the vendored Go sources for a platform triple.
#
# Used by swarm-init when the template has no prebuilt set for the host, and by
# maintainers to refresh the prebuilts in template/toolchain/<triple>/bin.
set -euo pipefail

# Go knobs that change the output (GOFLAGS, GOWORK, GOAMD64, ...) are cleared for
# reproducible builds. go.mod's `go` directive may make a local Go that is older
# trigger a toolchain download (GOTOOLCHAIN) on first build.

usage() {
  cat <<'USAGE'
Usage: build_toolchain.sh [--dry-run] <triple> [dest-bin-dir]
       build_toolchain.sh [--dry-run] --all

<triple> is <os>-<arch>: os = linux|darwin|windows, arch = x64|arm64
(for example windows-x64, darwin-arm64).

Without dest-bin-dir the tools go to template/toolchain/<triple>/bin.
--all builds every supported triple into template/toolchain/<triple>/bin.
--dry-run prints the `go build` commands without running them.
Requires Go on PATH. The binaries are static (CGO_ENABLED=0).
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC="$ROOT/vendor/acceptance-pipeline-specification"
TOOLS=(gherkin-parser gherkin-mutator gherkin-ir-dry-checker)
ALL_TRIPLES=(linux-x64 linux-arm64 darwin-x64 darwin-arm64 windows-x64 windows-arm64)
DRY_RUN=0

err() {
  echo "build_toolchain: $*" >&2
  exit 1
}

# triple_to_go <triple>: prints "<GOOS> <GOARCH>" or fails.
triple_to_go() {
  local os arch
  case "$1" in
    linux-* | darwin-* | windows-*) os="${1%%-*}" ;;
    *) return 1 ;;
  esac
  case "${1#*-}" in
    x64) arch=amd64 ;;
    arm64) arch=arm64 ;;
    *) return 1 ;;
  esac
  printf '%s %s\n' "$os" "$arch"
}

# build_triple <triple> <dest-bin-dir>
build_triple() {
  local triple="$1" dest="$2" goos goarch tool suffix=""
  read -r goos goarch < <(triple_to_go "$triple") || err "Unsupported triple: $triple (expected <linux|darwin|windows>-<x64|arm64>)"
  [[ "$goos" == windows ]] && suffix=".exe"
  ((DRY_RUN)) || mkdir -p "$dest"
  for tool in "${TOOLS[@]}"; do
    if ((DRY_RUN)); then
      printf '(cd %s && CGO_ENABLED=0 GOOS=%s GOARCH=%s go build -trimpath -ldflags="-s -w" -o %s/%s%s ./cmd/%s)\n' \
        "$SRC" "$goos" "$goarch" "$dest" "$tool" "$suffix" "$tool"
    else
      (cd "$SRC" && env -u GOFLAGS -u GOWORK -u GOAMD64 -u GOARM -u GOEXPERIMENT \
        CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" \
        go build -trimpath -ldflags="-s -w" -o "$dest/$tool$suffix" "./cmd/$tool") \
        || err "go build failed for $tool ($triple)"
    fi
  done
}

POSITIONAL=()
ALL=0
while (($# > 0)); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --all) ALL=1 ;;
    -h | --help) usage; exit 0 ;;
    -*) usage >&2; err "Unknown option: $1" ;;
    *) POSITIONAL+=("$1") ;;
  esac
  shift
done

if ((!DRY_RUN)); then
  command -v go >/dev/null 2>&1 || err "Go is not installed or not on PATH (https://go.dev/dl/)."
fi
[[ -d "$SRC/cmd" ]] || err "Go sources not found at $SRC."

if ((ALL)); then
  ((${#POSITIONAL[@]} == 0)) || err "--all takes no other arguments."
  for triple in "${ALL_TRIPLES[@]}"; do
    build_triple "$triple" "$ROOT/template/toolchain/$triple/bin"
  done
else
  ((${#POSITIONAL[@]} >= 1 && ${#POSITIONAL[@]} <= 2)) || { usage >&2; exit 2; }
  triple="${POSITIONAL[0]}"
  build_triple "$triple" "${POSITIONAL[1]:-$ROOT/template/toolchain/$triple/bin}"
fi
