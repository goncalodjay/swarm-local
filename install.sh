#!/usr/bin/env bash
#
# swarm-kura installer — one line, no clone:
#
#   curl -fsSL https://raw.githubusercontent.com/goncalodjay/swarm-local/master/install.sh | bash
#
# Windows (PowerShell):
#   irm https://raw.githubusercontent.com/goncalodjay/swarm-local/master/install.ps1 | iex
#
# What it does (same model as Claude Code / opencode / Hermes installers):
#   1. Detects the OS and architecture (linux|darwin|windows x x64|arm64).
#   2. Checks the runtime dependencies (bash >= 4, Python >= 3.10, git, herdr,
#      engram) and offers to install the missing ones with the platform's
#      package manager (Homebrew on macOS, apt/dnf/pacman/zypper/apk on Linux,
#      winget on Windows).
#   3. Downloads the latest GitHub release bundle for this platform (prebuilt
#      TUI + Gherkin toolchain), verifies its sha256, and installs it under
#      ~/.swarm-kura/versions/<version>. With no release published it installs
#      the default branch instead and builds the TUI locally with Bun.
#   4. Points ~/.swarm-kura/current at it and puts `swarm-kura` on the PATH
#      (~/.local/bin). Older versions are pruned (the last 3 are kept).
#
# Then, inside a project:  swarm-kura init   ·   later:  swarm-kura update
#
# Options:
#   --version <tag>     install that release tag (default: latest release)
#   --branch <name>     install a branch head instead of a release (dev channel)
#   --update            only install when a newer version exists (used by `swarm-kura update`)
#   --force             reinstall even if that version is already installed
#   --yes, -y           install missing dependencies without asking
#   --no-deps           do not check/install dependencies
#   --no-modify-path    do not touch shell rc files
#   --from <dir>        install from a local checkout instead of GitHub (development)
#
# Environment: SWARM_KURA_REPO (owner/repo), SWARM_KURA_HOME (~/.swarm-kura),
#              SWARM_KURA_BIN_DIR (~/.local/bin), GITHUB_TOKEN (API rate limit).
#
# Must stay compatible with bash 3.2 (macOS /bin/bash): no associative arrays,
# no mapfile, no ${var,,}.

set -euo pipefail

REPO="${SWARM_KURA_REPO:-goncalodjay/swarm-local}"
KURA_HOME="${SWARM_KURA_HOME:-$HOME/.swarm-kura}"
BIN_DIR="${SWARM_KURA_BIN_DIR:-$HOME/.local/bin}"
WANT_VERSION=""
WANT_BRANCH=""
EXPLICIT_BRANCH=""
UPDATE_ONLY=0
FORCE=0
ASSUME_YES=0
CHECK_DEPS=1
MODIFY_PATH=1
KEEP_VERSIONS=3
FROM_DIR=""

while [ $# -gt 0 ]; do
  case "$1" in
    --version) WANT_VERSION="${2:?--version needs a tag}"; shift ;;
    --branch) WANT_BRANCH="${2:?--branch needs a name}"; EXPLICIT_BRANCH="$WANT_BRANCH"; shift ;;
    --update) UPDATE_ONLY=1 ;;
    --force) FORCE=1 ;;
    --yes | -y) ASSUME_YES=1 ;;
    --no-deps) CHECK_DEPS=0 ;;
    --no-modify-path) MODIFY_PATH=0 ;;
    --from) FROM_DIR="$(cd "${2:?--from needs a directory}" && pwd)"; shift ;;
    -h | --help) sed -n '2,40p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; exit 2 ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
if [ -t 2 ]; then
  C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RESET=$'\033[0m'
else
  C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_RESET=""
fi
info() { printf '%s\n' "$*" >&2; }
step() { printf '%s==>%s %s\n' "$C_BOLD" "$C_RESET" "$*" >&2; }
ok() { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*" >&2; }
warn() { printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die() { printf '%sError:%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# Prompts read from the terminal: under `curl | bash` stdin is the script.
TTY_IN=""
if [ -r /dev/tty ] && (exec </dev/tty) 2>/dev/null; then TTY_IN=/dev/tty; fi
confirm() { # confirm <question>  (default yes)
  local answer=""
  [ "$ASSUME_YES" = 1 ] && return 0
  [ -n "$TTY_IN" ] || return 1
  printf '%s [S/n]: ' "$1" >&2
  read -r answer <"$TTY_IN" || answer="n"
  case "$answer" in "" | s | S | si | sí | y | Y | yes) return 0 ;; *) return 1 ;; esac
}

# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------
OS=""
ARCH=""
detect_platform() {
  case "$(uname -s)" in
    Linux) OS=linux ;;
    Darwin) OS=darwin ;;
    MINGW* | MSYS* | CYGWIN*) OS=windows ;;
    *) die "Sistema operativo no soportado: $(uname -s)" ;;
  esac
  local machine
  machine="$(uname -m)"
  if [ "$OS" = windows ]; then
    machine="${PROCESSOR_ARCHITEW6432:-${PROCESSOR_ARCHITECTURE:-$machine}}"
  fi
  case "$machine" in
    x86_64 | amd64 | AMD64 | x64) ARCH=x64 ;;
    arm64 | aarch64 | ARM64) ARCH=arm64 ;;
    *) die "Arquitectura no soportada: $machine" ;;
  esac
  # A shell running under Rosetta reports x86_64 on Apple Silicon: install
  # the native arm64 build.
  if [ "$OS" = darwin ] && [ "$ARCH" = x64 ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = 1 ]; then
    ARCH=arm64
  fi
  TRIPLE="$OS-$ARCH"
}

have() { command -v "$1" >/dev/null 2>&1; }

sha256_of() {
  if have sha256sum; then sha256sum "$1" | awk '{print $1}'
  elif have shasum; then shasum -a 256 "$1" | awk '{print $1}'
  else openssl dgst -sha256 "$1" | awk '{print $NF}'
  fi
}

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
BREW=""
find_brew() {
  local b
  for b in brew /opt/homebrew/bin/brew /usr/local/bin/brew /home/linuxbrew/.linuxbrew/bin/brew; do
    if have "$b" || [ -x "$b" ]; then BREW="$b"; return 0; fi
  done
  return 1
}

modern_bash() {
  local b
  for b in /opt/homebrew/bin/bash /usr/local/bin/bash /home/linuxbrew/.linuxbrew/bin/bash $(type -ap bash 2>/dev/null); do
    [ -x "$b" ] || continue
    if "$b" -c '[ "${BASH_VERSINFO[0]}" -ge 4 ]' 2>/dev/null; then echo "$b"; return 0; fi
  done
  return 1
}

modern_python() {
  local p
  for p in python3 python3.14 python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3 python; do
    have "$p" || [ -x "$p" ] || continue
    if "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then echo "$p"; return 0; fi
  done
  return 1
}

LINUX_PM=""
detect_linux_pm() {
  local pm
  for pm in apt-get dnf yum pacman zypper apk; do
    if have "$pm"; then LINUX_PM="$pm"; return 0; fi
  done
  return 1
}

SUDO=""
[ "$(id -u 2>/dev/null || echo 1)" = 0 ] || { have sudo && SUDO="sudo"; }

linux_pkg_cmd() { # linux_pkg_cmd <package...>
  case "$LINUX_PM" in
    apt-get) echo "$SUDO apt-get update && $SUDO apt-get install -y $*" ;;
    dnf | yum) echo "$SUDO $LINUX_PM install -y $*" ;;
    pacman) echo "$SUDO pacman -S --needed --noconfirm $*" ;;
    zypper) echo "$SUDO zypper install -y $*" ;;
    apk) echo "$SUDO apk add $*" ;;
  esac
}

# Missing deps as parallel lists: name, install command ("" = manual only), hint.
DEP_NAMES=()
DEP_CMDS=()
DEP_HINTS=()
add_missing() { DEP_NAMES+=("$1"); DEP_CMDS+=("$2"); DEP_HINTS+=("$3"); }

ENGRAM_DOC="https://github.com/Gentleman-Programming/engram/blob/main/docs/INSTALLATION.md"

plan_dependencies() {
  [ "$OS" = linux ] && detect_linux_pm || true
  find_brew || true

  if ! modern_bash >/dev/null; then
    case "$OS" in
      darwin) add_missing "bash>=4" "${BREW:+$BREW install bash}" "macOS trae bash 3.2; swarm-init necesita bash >= 4 (brew install bash)" ;;
      linux) add_missing "bash>=4" "$(linux_pkg_cmd bash)" "instalá bash >= 4 con el gestor de paquetes" ;;
      windows) add_missing "bash>=4" "" "actualizá Git for Windows: https://git-scm.com/download/win" ;;
    esac
  fi
  if ! modern_python >/dev/null; then
    case "$OS" in
      darwin) add_missing "python>=3.10" "${BREW:+$BREW install python@3.12}" "macOS trae Python 3.9; hace falta >= 3.10 (brew install python@3.12)" ;;
      linux) add_missing "python>=3.10" "$(linux_pkg_cmd python3)" "si tu distro trae < 3.10, usá pyenv/mise/uv para instalar 3.12" ;;
      windows) add_missing "python>=3.10" "winget install -e --id Python.Python.3.12" "https://www.python.org/downloads/" ;;
    esac
  fi
  if ! have git; then
    case "$OS" in
      darwin) add_missing git "${BREW:+$BREW install git}" "xcode-select --install" ;;
      linux) add_missing git "$(linux_pkg_cmd git)" "" ;;
      windows) add_missing git "winget install -e --id Git.Git" "" ;;
    esac
  fi
  have tar || add_missing tar "$( [ "$OS" = linux ] && linux_pkg_cmd tar)" ""
  if ! have herdr; then
    case "$OS" in
      darwin)
        if [ -n "$BREW" ]; then add_missing herdr "$BREW install herdr" "https://herdr.dev"
        else add_missing herdr "curl -fsSL https://herdr.dev/install.sh | sh" "https://herdr.dev"; fi ;;
      linux) add_missing herdr "curl -fsSL https://herdr.dev/install.sh | sh" "https://herdr.dev" ;;
      windows) add_missing herdr "powershell -NoProfile -ExecutionPolicy Bypass -c \"irm https://herdr.dev/install.ps1 | iex\"" "https://herdr.dev" ;;
    esac
  fi
  if ! have engram; then
    if [ -n "$BREW" ] && [ "$OS" != windows ]; then
      add_missing engram "$BREW install gentleman-programming/tap/engram" "$ENGRAM_DOC"
    else
      add_missing engram "" "$ENGRAM_DOC"
    fi
  fi
}

ensure_dependencies() {
  step "Revisando dependencias ($TRIPLE)"
  plan_dependencies
  if [ ${#DEP_NAMES[@]} -eq 0 ]; then
    ok "bash >= 4, Python >= 3.10, git, herdr y engram disponibles"
    return 0
  fi
  local i auto=0
  for i in "${!DEP_NAMES[@]}"; do
    if [ -n "${DEP_CMDS[$i]}" ]; then
      warn "falta ${DEP_NAMES[$i]} → ${C_DIM}${DEP_CMDS[$i]}${C_RESET}"
      auto=1
    else
      warn "falta ${DEP_NAMES[$i]} (instalación manual: ${DEP_HINTS[$i]})"
    fi
  done
  if [ "$OS" = darwin ] && [ -z "$BREW" ]; then
    warn "Homebrew no está instalado; es la forma más simple de instalar lo que falta:"
    info "      /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
  fi
  if [ "$auto" = 1 ] && confirm "¿Instalar ahora lo que falta?"; then
    for i in "${!DEP_NAMES[@]}"; do
      [ -n "${DEP_CMDS[$i]}" ] || continue
      step "Instalando ${DEP_NAMES[$i]}"
      if ! bash -c "${DEP_CMDS[$i]}" </dev/null; then
        warn "falló: ${DEP_CMDS[$i]}"
      fi
    done
    hash -r 2>/dev/null || true
    # herdr's installer drops the binary in ~/.local/bin or ~/.herdr/bin.
    PATH="$BIN_DIR:$HOME/.herdr/bin:$PATH"
  elif [ "$auto" = 1 ]; then
    warn "No se instalaron dependencias. Corré los comandos de arriba y después: swarm-kura doctor"
  fi
}

# ---------------------------------------------------------------------------
# Resolve + download
# ---------------------------------------------------------------------------
curl_api() { # curl_api <url>
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    curl -fsSL --retry 2 -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github+json" "$1"
  else
    curl -fsSL --retry 2 -H "Accept: application/vnd.github+json" "$1"
  fi
}

CHANNEL=""   # release | branch
VERSION_ID="" # directory name under versions/
SOURCE_REF="" # tag or commit sha

resolve_version() {
  if [ -n "$FROM_DIR" ]; then
    CHANNEL=local
    SOURCE_REF="$FROM_DIR"
    VERSION_ID="local-$(git -C "$FROM_DIR" rev-parse --short HEAD 2>/dev/null || echo 0)-$(date +%Y%m%d%H%M%S)"
    return 0
  fi
  if [ -z "$WANT_BRANCH" ]; then
    local tag="$WANT_VERSION"
    if [ -z "$tag" ]; then
      tag="$(curl_api "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
        | sed -n 's/^[[:space:]]*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1 || true)"
    fi
    if [ -n "$tag" ]; then
      CHANNEL=release
      SOURCE_REF="$tag"
      VERSION_ID="$tag"
      return 0
    fi
    [ -z "$WANT_VERSION" ] || die "No se encontró la release $WANT_VERSION en $REPO."
    info "${C_DIM}No hay releases publicadas en $REPO; se instala la rama principal.${C_RESET}"
    WANT_BRANCH="$(curl_api "https://api.github.com/repos/$REPO" 2>/dev/null \
      | sed -n 's/^[[:space:]]*"default_branch":[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1 || true)"
    WANT_BRANCH="${WANT_BRANCH:-master}"
  fi
  local sha
  sha="$(git ls-remote "https://github.com/$REPO.git" "refs/heads/$WANT_BRANCH" 2>/dev/null | awk '{print $1}' | head -n 1 || true)"
  [ -n "$sha" ] || die "No se pudo consultar la rama $WANT_BRANCH de https://github.com/$REPO (¿sin red o sin git?)."
  CHANNEL=branch
  SOURCE_REF="$sha"
  VERSION_ID="$WANT_BRANCH-$(printf '%s' "$sha" | cut -c1-7)"
}

current_version() {
  [ -f "$KURA_HOME/current" ] && tr -d '\r\n' <"$KURA_HOME/current" || true
}

WORK=""
cleanup() { [ -n "$WORK" ] && rm -rf "$WORK"; }
trap cleanup EXIT

download_release_bundle() { # -> $WORK/root
  local base="https://github.com/$REPO/releases/download/$SOURCE_REF"
  local asset="swarm-kura-$TRIPLE.tar.gz"
  step "Descargando $asset ($SOURCE_REF)"
  if ! curl -fsSL --retry 3 -o "$WORK/$asset" "$base/$asset"; then
    return 1
  fi
  if curl -fsSL --retry 3 -o "$WORK/$asset.sha256" "$base/$asset.sha256" 2>/dev/null; then
    local want got
    want="$(awk '{print $1}' "$WORK/$asset.sha256")"
    got="$(sha256_of "$WORK/$asset")"
    [ "$want" = "$got" ] || die "sha256 no coincide para $asset (esperado $want, obtenido $got)."
    ok "sha256 verificado"
  else
    warn "la release no publica sha256 para $asset; se omite la verificación"
  fi
  mkdir -p "$WORK/root"
  tar -xzf "$WORK/$asset" -C "$WORK/root"
}

download_source() { # <ref> -> $WORK/root (repo checkout without .git)
  step "Descargando el código fuente ($1)"
  curl -fsSL --retry 3 -o "$WORK/src.tar.gz" "https://codeload.github.com/$REPO/tar.gz/$1" \
    || die "No se pudo descargar https://codeload.github.com/$REPO/tar.gz/$1"
  mkdir -p "$WORK/extract"
  tar -xzf "$WORK/src.tar.gz" -C "$WORK/extract"
  local top
  top="$(find "$WORK/extract" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  [ -n "$top" ] || die "El tarball del código fuente está vacío."
  mv "$top" "$WORK/root"
}

install_bun() {
  have bun && return 0
  [ -x "$HOME/.bun/bin/bun" ] && { PATH="$HOME/.bun/bin:$PATH"; return 0; }
  step "Instalando Bun (solo para compilar la TUI)"
  if [ "$OS" = windows ]; then
    powershell -NoProfile -ExecutionPolicy Bypass -c "irm bun.sh/install.ps1 | iex" || return 1
  else
    curl -fsSL https://bun.sh/install | bash >/dev/null || return 1
  fi
  PATH="$HOME/.bun/bin:$PATH"
  have bun
}

ensure_tui() { # make sure $WORK/root/tui/dist/swarm-tui[.exe] exists
  local root="$WORK/root" exe=""
  [ "$OS" = windows ] && exe=".exe"
  if [ -f "$root/tui/dist/swarm-tui$exe" ]; then return 0; fi
  mkdir -p "$root/tui/dist"
  # A published TUI binary from any release works with the branch code too.
  local tag
  tag="$(curl_api "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
    | sed -n 's/^[[:space:]]*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1 || true)"
  if [ -n "$tag" ] && curl -fsSL --retry 2 -o "$root/tui/dist/swarm-tui$exe" \
    "https://github.com/$REPO/releases/download/$tag/swarm-tui-$TRIPLE.bin" 2>/dev/null; then
    chmod 0755 "$root/tui/dist/swarm-tui$exe"
    ok "TUI precompilada ($tag)"
    return 0
  fi
  rm -f "$root/tui/dist/swarm-tui$exe"
  info "${C_DIM}No hay TUI precompilada para $TRIPLE; se compila localmente con Bun (una sola vez por versión).${C_RESET}"
  install_bun || die "No se pudo instalar Bun (https://bun.sh). Instalalo y reintentá."
  step "Compilando la TUI"
  (cd "$root/tui" && bun install --no-save >/dev/null 2>&1 || bun install) \
    && (cd "$root/tui" && bun run build >/dev/null) \
    || die "Falló la compilación de la TUI (cd tui && bun install && bun run build)."
  [ -f "$root/tui/dist/swarm-tui$exe" ] || die "La compilación no produjo tui/dist/swarm-tui$exe."
  ok "TUI compilada"
}

prune_bundle() { # drop what an install never runs (dev, tests, other platforms)
  local root="$WORK/root" t
  rm -rf "$root/tests" "$root/features" "$root/acceptance" "$root/docs/_archive" "$root/.github" \
    "$root/tui/node_modules" "$root/tui/src" "$root/tui/test" "$root/node_modules" 2>/dev/null || true
  if [ -d "$root/template/toolchain/$TRIPLE" ]; then
    for t in "$root"/template/toolchain/*; do
      [ "$(basename "$t")" = "$TRIPLE" ] || rm -rf "$t"
    done
  fi
}

# ---------------------------------------------------------------------------
# Activate
# ---------------------------------------------------------------------------
write_launchers() { # write_launchers <installed-version-dir>
  local src="$1"
  mkdir -p "$BIN_DIR"
  local launcher="$BIN_DIR/swarm-kura"
  cat >"$launcher.tmp" <<EOF
#!/usr/bin/env bash
# swarm-kura launcher (written by install.sh). Runs the active version.
KURA_HOME="\${SWARM_KURA_HOME:-$KURA_HOME}"
version="\$(tr -d '\r\n' <"\$KURA_HOME/current" 2>/dev/null)"
if [ -z "\$version" ] || [ ! -f "\$KURA_HOME/versions/\$version/bin/swarm-kura" ]; then
  echo "swarm-kura no está instalado correctamente en \$KURA_HOME. Reinstalá con:" >&2
  echo "  curl -fsSL https://raw.githubusercontent.com/$REPO/master/install.sh | bash" >&2
  exit 1
fi
export SWARM_KURA_HOME="\$KURA_HOME"
exec bash "\$KURA_HOME/versions/\$version/bin/swarm-kura" "\$@"
EOF
  chmod 0755 "$launcher.tmp"
  mv -f "$launcher.tmp" "$launcher"
  if [ "$OS" = windows ]; then
    # cmd.exe / PowerShell: run the launcher under Git Bash.
    cp -f "$src/bin/swarm-kura.cmd" "$BIN_DIR/swarm-kura.cmd" 2>/dev/null || true
  fi
}

ensure_path() {
  case ":$PATH:" in *":$BIN_DIR:"*) return 0 ;; esac
  if [ "$MODIFY_PATH" = 0 ] || [ "$OS" = windows ]; then
    warn "$BIN_DIR no está en el PATH. Agregalo para usar swarm-kura."
    return 0
  fi
  local line="export PATH=\"$BIN_DIR:\$PATH\"" rc files="" shell_name
  shell_name="$(basename "${SHELL:-bash}")"
  case "$shell_name" in
    zsh) files="${ZDOTDIR:-$HOME}/.zshrc" ;;
    bash) if [ "$OS" = darwin ]; then files="$HOME/.bash_profile"; else files="$HOME/.bashrc"; fi ;;
    fish)
      mkdir -p "$HOME/.config/fish/conf.d"
      printf 'fish_add_path %s\n' "$BIN_DIR" >"$HOME/.config/fish/conf.d/swarm-kura.fish"
      ok "PATH agregado en ~/.config/fish/conf.d/swarm-kura.fish"
      return 0 ;;
    *) files="$HOME/.profile" ;;
  esac
  for rc in $files; do
    if [ -f "$rc" ] && grep -Fq "$BIN_DIR" "$rc"; then continue; fi
    printf '\n# swarm-kura\n%s\n' "$line" >>"$rc"
    ok "PATH agregado en $rc"
  done
  PATH_HINT="$line"
}

prune_versions() {
  local keep="$1" dir n=0
  # Newest first by modification time; never delete the active one.
  for dir in $(ls -1t "$KURA_HOME/versions" 2>/dev/null); do
    n=$((n + 1))
    [ "$dir" = "$keep" ] && continue
    [ $n -le $KEEP_VERSIONS ] && continue
    rm -rf "${KURA_HOME:?}/versions/$dir"
  done
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
PATH_HINT=""
detect_platform
have curl || die "Falta curl."
have tar || die "Falta tar."
have git || [ "$CHECK_DEPS" = 1 ] || die "Falta git."

printf '%s%s swarm-kura %s %s(%s)%s\n\n' "$C_BOLD" "${C_BOLD:+$'\033[7m'}" "$C_RESET" "$C_DIM" "$TRIPLE" "$C_RESET" >&2

[ "$CHECK_DEPS" = 1 ] && ensure_dependencies
have git || die "Falta git (necesario para el swarm y para consultar versiones)."

resolve_version
PREVIOUS="$(current_version)"
if [ "$FORCE" = 0 ] && [ "$VERSION_ID" = "$PREVIOUS" ] && [ -f "$KURA_HOME/versions/$VERSION_ID/bin/swarm-kura" ]; then
  ok "swarm-kura $VERSION_ID ya es la versión instalada (la última disponible)"
  [ -x "$BIN_DIR/swarm-kura" ] || write_launchers "$KURA_HOME/versions/$VERSION_ID"
  exit 0
fi
[ "$UPDATE_ONLY" = 1 ] && [ -n "$PREVIOUS" ] && step "Actualizando swarm-kura: $PREVIOUS → $VERSION_ID"

WORK="$(mktemp -d)"
if [ "$CHANNEL" = local ]; then
  step "Copiando $FROM_DIR"
  mkdir -p "$WORK/root"
  (cd "$FROM_DIR" && tar -cf - --exclude .git --exclude node_modules --exclude __pycache__ \
    --exclude .swarmforge --exclude .worktrees --exclude ./test .) | tar -xf - -C "$WORK/root"
elif [ "$CHANNEL" = release ]; then
  if ! download_release_bundle; then
    warn "la release $SOURCE_REF no trae bundle para $TRIPLE; se usa su código fuente"
    download_source "$SOURCE_REF"
  fi
else
  download_source "$SOURCE_REF"
fi
[ -f "$WORK/root/swarm-init" ] || die "El paquete descargado no contiene swarm-init."
[ -f "$WORK/root/bin/swarm-kura" ] || die "La versión $VERSION_ID es anterior a swarm-kura (no trae bin/swarm-kura). Instalá una más nueva."
ensure_tui
prune_bundle
printf 'channel=%s\nref=%s\nrepo=%s\ntriple=%s\ninstalled_at=%s\n' \
  "$CHANNEL" "$SOURCE_REF" "$REPO" "$TRIPLE" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$WORK/root/INSTALL_INFO"
[ -z "$WANT_BRANCH" ] || printf 'branch=%s\n' "$WANT_BRANCH" >>"$WORK/root/INSTALL_INFO"
chmod 0755 "$WORK/root/swarm-init" "$WORK/root/bin/swarm-kura" 2>/dev/null || true

mkdir -p "$KURA_HOME/versions"
rm -rf "$KURA_HOME/versions/$VERSION_ID.new"
mv "$WORK/root" "$KURA_HOME/versions/$VERSION_ID.new"
rm -rf "$KURA_HOME/versions/$VERSION_ID"
mv "$KURA_HOME/versions/$VERSION_ID.new" "$KURA_HOME/versions/$VERSION_ID"
printf '%s\n' "$VERSION_ID" >"$KURA_HOME/current.tmp" && mv -f "$KURA_HOME/current.tmp" "$KURA_HOME/current"
# Remembered for `swarm-kura update`: the repo and an explicitly chosen
# branch (dev channel). The automatic branch fallback is not remembered, so
# updates switch to releases as soon as one is published.
{
  printf 'repo=%s\n' "$REPO"
  [ -z "$EXPLICIT_BRANCH" ] || printf 'branch=%s\n' "$EXPLICIT_BRANCH"
} >"$KURA_HOME/config"
write_launchers "$KURA_HOME/versions/$VERSION_ID"
prune_versions "$VERSION_ID"
ensure_path

info ""
ok "${C_BOLD}swarm-kura $VERSION_ID instalado${C_RESET} en $KURA_HOME"
if [ "$UPDATE_ONLY" = 1 ]; then exit 0; fi
info ""
info "Siguientes pasos:"
[ -n "$PATH_HINT" ] && info "  ${C_DIM}# abrí una terminal nueva, o:${C_RESET} $PATH_HINT"
info "  cd /ruta/a/tu/proyecto"
info "  swarm-kura init      ${C_DIM}# instala el swarm en el proyecto (elige agentes y modelos)${C_RESET}"
info "  swarm-kura run       ${C_DIM}# arranca el swarm${C_RESET}"
info "  swarm-kura doctor    ${C_DIM}# revisa dependencias y agentes${C_RESET}"
