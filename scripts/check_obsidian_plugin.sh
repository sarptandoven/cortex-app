#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="$ROOT_DIR/packages/obsidian-cortex-plugin"

cd "$PLUGIN_DIR"

if [[ ! -d node_modules ]]; then
  npm ci
fi

npm run typecheck
npm run build

node <<'NODE'
const fs = require("fs");
const path = require("path");

const required = ["manifest.json", "versions.json", "main.js", "package.json"];
for (const file of required) {
  if (!fs.existsSync(path.join(process.cwd(), file))) {
    throw new Error(`Missing required plugin file: ${file}`);
  }
}

const manifest = JSON.parse(fs.readFileSync("manifest.json", "utf8"));
const versions = JSON.parse(fs.readFileSync("versions.json", "utf8"));
const pkg = JSON.parse(fs.readFileSync("package.json", "utf8"));
const apiPackagePath = path.resolve(process.cwd(), "../obsidian-api/package.json");
const apiTypesPath = path.resolve(process.cwd(), "../obsidian-api/obsidian.d.ts");
const apiLicensePath = path.resolve(process.cwd(), "../obsidian-api/LICENSE.md");
const apiPkg = JSON.parse(fs.readFileSync(apiPackagePath, "utf8"));

if (!manifest.id || !manifest.name || !manifest.version || !manifest.minAppVersion) {
  throw new Error("manifest.json is missing required Obsidian plugin metadata");
}
if (manifest.version !== pkg.version) {
  throw new Error(`Plugin version mismatch: manifest ${manifest.version} != package ${pkg.version}`);
}
if (versions[manifest.version] !== manifest.minAppVersion) {
  throw new Error(`versions.json does not map ${manifest.version} to ${manifest.minAppVersion}`);
}
if (pkg.devDependencies?.obsidian !== "file:../obsidian-api") {
  throw new Error("Cortex Obsidian plugin must build against the vendored ../obsidian-api package");
}
if (apiPkg.name !== "obsidian" || apiPkg.license !== "MIT") {
  throw new Error("Vendored Obsidian API package metadata is invalid");
}
if (!fs.existsSync(apiTypesPath) || !fs.existsSync(apiLicensePath)) {
  throw new Error("Vendored Obsidian API package is missing type definitions or license");
}
NODE

cd "$ROOT_DIR"
if git ls-files --error-unmatch packages/obsidian-cortex-plugin/main.js >/dev/null 2>&1; then
  git diff --exit-code -- packages/obsidian-cortex-plugin/main.js
fi
