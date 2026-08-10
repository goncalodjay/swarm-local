#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IR_DIR="$ROOT/build/acceptance/ir"
GENERATED_DIR="$ROOT/build/acceptance/generated"

command -v gherkin-parser >/dev/null 2>&1 || { echo "gherkin-parser not on PATH" >&2; exit 1; }

mkdir -p "$IR_DIR" "$GENERATED_DIR"

for feature in "$ROOT"/features/*.feature; do
  [ -e "$feature" ] || continue
  name="$(basename "$feature" .feature)"
  gherkin-parser "$feature" "$IR_DIR/$name.json"
done

for ir in "$IR_DIR"/*.json; do
  [ -e "$ir" ] || continue
  node "$ROOT/acceptance/generator.ts" "$ir" "$GENERATED_DIR"
done

node --test "$GENERATED_DIR"/*.test.ts
