# Resolves a real, runnable Python 3 and caches it in $PYTHON.
# Windows ships python.exe only; `python3` may resolve to the Microsoft Store
# stub ("App execution alias"), which prints an error and exits non-zero.
# Source this file, then call `find_python`; it returns 0 and sets $PYTHON,
# or returns 1 with $PYTHON empty when no usable interpreter is found.
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
