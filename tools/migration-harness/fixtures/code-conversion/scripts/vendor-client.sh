#!/usr/bin/env bash
# Vendors the SpiceDB TypeScript client into one fixture, per
# spicedb-client-integration/references/installation.md -- clone at the pinned
# commit, copy spicedb-typescript/ plus its sibling proto-clients/spicedb-typescript-proto/,
# rewrite the one workspace:* line so plain npm resolves it, then npm install.
#
# Usage: vendor-client.sh <fixture-dir>
#
# Neither the clone (.vendor/) nor the copied third_party/ tree is committed --
# ../.gitignore excludes both. Re-run this any time third_party/ is missing or you
# want to re-vendor at a moved pin.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURES_DIR="$(dirname "$SCRIPT_DIR")"
VENDOR_CLONE_DIR="$FIXTURES_DIR/.vendor/spicedb-clients-prototype"
PINNED_COMMIT="549c4e90e7a1488adcf268e0e0033e48d5b5f0a4"
REPO_URL="https://github.com/authzed/spicedb-clients-prototype"

FIXTURE_DIR="${1:?usage: vendor-client.sh <fixture-dir>}"
FIXTURE_DIR="$(cd "$FIXTURE_DIR" && pwd)"

if [ ! -d "$VENDOR_CLONE_DIR/.git" ]; then
  echo "Cloning $REPO_URL at $PINNED_COMMIT into $VENDOR_CLONE_DIR ..."
  mkdir -p "$(dirname "$VENDOR_CLONE_DIR")"
  git clone --quiet "$REPO_URL" "$VENDOR_CLONE_DIR"
  git -C "$VENDOR_CLONE_DIR" checkout --quiet "$PINNED_COMMIT"
else
  CURRENT_COMMIT="$(git -C "$VENDOR_CLONE_DIR" rev-parse HEAD)"
  if [ "$CURRENT_COMMIT" != "$PINNED_COMMIT" ]; then
    echo "Re-pinning existing clone from $CURRENT_COMMIT to $PINNED_COMMIT ..."
    git -C "$VENDOR_CLONE_DIR" fetch --quiet origin "$PINNED_COMMIT"
    git -C "$VENDOR_CLONE_DIR" checkout --quiet "$PINNED_COMMIT"
  fi
fi

# The vendored TypeScript client and its proto sibling ship source only, no
# prebuilt dist/ -- installation.md's step 4 ("build or import as usual") and
# migrate-code.md step 4.3 ("confirm the vendored client actually builds") both
# require this before any call site can import it. Build once, in the shared
# clone, via the monorepo's own pnpm workspace (proto-clients/spicedb-typescript-proto
# and spicedb-typescript are both listed in its pnpm-workspace.yaml) -- not per
# fixture -- then copy the built dist/ alongside src/ into each fixture.
if [ ! -d "$VENDOR_CLONE_DIR/spicedb-typescript/dist" ]; then
  echo "Building vendored TypeScript client + proto (pnpm, once) ..."
  ( cd "$VENDOR_CLONE_DIR" && pnpm install --silent --frozen-lockfile )
  ( cd "$VENDOR_CLONE_DIR" && pnpm --filter @spicedb/proto build )
  ( cd "$VENDOR_CLONE_DIR" && pnpm --filter @spicedb/client build )
fi

DEST="$FIXTURE_DIR/third_party/spicedb-clients"
rm -rf "$DEST"
mkdir -p "$DEST"
# --exclude node_modules: pnpm's workspace node_modules are symlinks into its own
# content-addressed store: copying them verbatim outside the workspace produces
# dangling links. Each fixture's own `npm install` (below) resolves the vendored
# packages' runtime dependencies fresh, from their package.json, so nothing here
# needs the vendored packages' own node_modules.
rsync -a --exclude node_modules "$VENDOR_CLONE_DIR/spicedb-typescript/" "$DEST/spicedb-typescript/"
mkdir -p "$DEST/proto-clients"
rsync -a --exclude node_modules "$VENDOR_CLONE_DIR/proto-clients/spicedb-typescript-proto/" "$DEST/proto-clients/spicedb-typescript-proto/"

# installation.md's TypeScript recipe: rewrite the pnpm workspace:* proto reference
# to a plain relative file: reference so plain npm resolves it.
node -e '
  const fs = require("fs");
  const path = process.argv[1];
  const pkg = JSON.parse(fs.readFileSync(path, "utf-8"));
  if (pkg.dependencies && pkg.dependencies["@spicedb/proto"] === "workspace:*") {
    pkg.dependencies["@spicedb/proto"] = "file:../proto-clients/spicedb-typescript-proto";
    fs.writeFileSync(path, JSON.stringify(pkg, null, 2) + "\n");
  }
' "$DEST/spicedb-typescript/package.json"

echo "Vendored client at $PINNED_COMMIT into $DEST"

echo "Running npm install in $FIXTURE_DIR ..."
( cd "$FIXTURE_DIR" && npm install --no-audit --no-fund --silent )

echo "Done."
