# Resolves a real, runnable Python >= 3.10 and caches it in $PYTHON.
# - Windows ships python.exe only; `python3` may resolve to the Microsoft Store
#   stub ("App execution alias"), which prints an error and exits non-zero.
# - macOS ships /usr/bin/python3 3.9 (Command Line Tools), too old for the
#   swarm (PEP 604 annotations): versioned names and Homebrew are tried first.
# Source this file, then call `find_python`; it returns 0 and sets $PYTHON,
# or returns 1 with $PYTHON empty when no usable interpreter is found.
# SWARMFORGE_PYTHON forces a specific interpreter.
find_python() {
  local candidate
  for candidate in ${SWARMFORGE_PYTHON:+"$SWARMFORGE_PYTHON"} python3 python3.14 python3.13 python3.12 python3.11 python3.10 \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      PYTHON="$candidate"
      return 0
    fi
  done
  PYTHON=""
  return 1
}
