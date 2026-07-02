#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="$ROOT_DIR/packages/obsidian-cortex-plugin"
REQUESTED_OUT_DIR="${1:-$ROOT_DIR/outputs/obsidian-plugin}"
if [[ "$REQUESTED_OUT_DIR" = /* ]]; then
  OUT_DIR="$REQUESTED_OUT_DIR"
else
  OUT_DIR="$ROOT_DIR/$REQUESTED_OUT_DIR"
fi

"$ROOT_DIR/scripts/check_obsidian_plugin.sh" >&2

VERSION="$(node -p "require('$PLUGIN_DIR/manifest.json').version")"
PACKAGE_DIR="$OUT_DIR/cortex-memory"
ZIP_PATH="$OUT_DIR/cortex-memory-$VERSION.zip"
ZIP_FILE="$(basename "$ZIP_PATH")"

rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR" "$OUT_DIR"

cp "$PLUGIN_DIR/manifest.json" "$PACKAGE_DIR/manifest.json"
cp "$PLUGIN_DIR/main.js" "$PACKAGE_DIR/main.js"
cp "$PLUGIN_DIR/versions.json" "$PACKAGE_DIR/versions.json"

(
  cd "$OUT_DIR"
  rm -f "$ZIP_FILE"
  zip -qr "$ZIP_FILE" cortex-memory
)

echo "$ZIP_PATH"
