#!/usr/bin/env bash
# End-to-end runner for one code-conversion fixture:
#   1. Start a fresh OpenFGA container, create a store, write shared/model.fga.
#   2. Seed it with shared/seed-relationships.json.
#   3. Vendor the SpiceDB TypeScript client into the fixture and npm install.
#   4. Run the fixture's ORIGINAL app against live OpenFGA -> out/baseline.jsonl.
#   5. Start spicedb serve-testing, write shared/schema.zed to it.
#   6. Seed it with the same seed data, translated through shared/migration-map.json.
#   7. Run the fixture's CONVERTED app against live SpiceDB -> out/converted.jsonl.
#   8. Compare the two JSONL outputs (scripts/compare.mjs) and report PASS/FAIL.
#   9. Tear down both the container and the spicedb process.
#
# Usage: scripts/run-fixture.sh <openfga-client|openfga-api|auth0-fga-api>
#
# Requires: docker, the `fga` CLI, the `zed` CLI, the `spicedb` binary, node/npx, jq.
# Ports (override via env if they collide with something already running):
#   OPENFGA_HTTP_PORT (default 28091), OPENFGA_GRPC_PORT (28090),
#   OPENFGA_PLAYGROUND_PORT (28092), SPICEDB_GRPC_PORT (28051)
set -euo pipefail

FIXTURE="${1:?usage: run-fixture.sh <openfga-client|openfga-api|auth0-fga-api>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURES_DIR="$(dirname "$SCRIPT_DIR")"
FIXTURE_DIR="$FIXTURES_DIR/$FIXTURE"
SHARED_DIR="$FIXTURES_DIR/shared"

if [ ! -d "$FIXTURE_DIR" ]; then
  echo "no such fixture: $FIXTURE (expected a directory at $FIXTURE_DIR)" >&2
  exit 1
fi

OPENFGA_HTTP_PORT="${OPENFGA_HTTP_PORT:-28091}"
OPENFGA_GRPC_PORT="${OPENFGA_GRPC_PORT:-28090}"
OPENFGA_PLAYGROUND_PORT="${OPENFGA_PLAYGROUND_PORT:-28092}"
SPICEDB_GRPC_PORT="${SPICEDB_GRPC_PORT:-28051}"
CONTAINER_NAME="code-conversion-fixture-openfga"
SPICEDB_TOKEN="fixture-$(date +%s)-$$"
OPENFGA_URL="http://localhost:${OPENFGA_HTTP_PORT}"
SPICEDB_ENDPOINT="localhost:${SPICEDB_GRPC_PORT}"

OUT_DIR="$FIXTURE_DIR/out"
mkdir -p "$OUT_DIR"

SPICEDB_PID=""
cleanup() {
  echo "--- [$FIXTURE] cleanup ---"
  if [ -n "$SPICEDB_PID" ]; then kill "$SPICEDB_PID" >/dev/null 2>&1 || true; fi
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "=== [$FIXTURE] starting OpenFGA ($CONTAINER_NAME on :$OPENFGA_HTTP_PORT) ==="
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER_NAME" \
  -p "${OPENFGA_HTTP_PORT}:8080" -p "${OPENFGA_GRPC_PORT}:8081" -p "${OPENFGA_PLAYGROUND_PORT}:3000" \
  openfga/openfga:latest run >/dev/null

printf 'waiting for OpenFGA'
for _ in $(seq 1 30); do
  if curl -sf "$OPENFGA_URL/healthz" >/dev/null 2>&1; then echo " ok"; break; fi
  printf '.'; sleep 1
done

STORE_ID=$(fga store create --api-url "$OPENFGA_URL" --name "code-conversion-fixture-${FIXTURE}" | jq -r '.store.id')
echo "store: $STORE_ID"
fga model write --api-url "$OPENFGA_URL" --store-id "$STORE_ID" --file "$SHARED_DIR/model.fga" >/dev/null
echo "model written"

echo "=== [$FIXTURE] seeding OpenFGA ==="
jq -c '.[]' "$SHARED_DIR/seed-relationships.json" | while read -r row; do
  rtype=$(echo "$row" | jq -r '.resourceType')
  rid=$(echo "$row" | jq -r '.resourceId')
  rel=$(echo "$row" | jq -r '.relation')
  stype=$(echo "$row" | jq -r '.subjectType')
  sid=$(echo "$row" | jq -r '.subjectId')
  srel=$(echo "$row" | jq -r '.subjectRelation // empty')
  subject="${stype}:${sid}"
  if [ -n "$srel" ]; then subject="${subject}#${srel}"; fi
  fga tuple write --api-url "$OPENFGA_URL" --store-id "$STORE_ID" "$subject" "$rel" "${rtype}:${rid}" >/dev/null
done
echo "seeded $(jq 'length' "$SHARED_DIR/seed-relationships.json") tuples"

echo "=== [$FIXTURE] vendoring SpiceDB client + npm install ==="
"$SCRIPT_DIR/vendor-client.sh" "$FIXTURE_DIR"

echo "=== [$FIXTURE] running ORIGINAL app against live OpenFGA ==="
( cd "$FIXTURE_DIR" && FGA_API_URL="$OPENFGA_URL" FGA_STORE_ID="$STORE_ID" npx tsx src/original/app.ts ) | tee "$OUT_DIR/baseline.jsonl"

echo "=== [$FIXTURE] starting spicedb serve-testing (:$SPICEDB_GRPC_PORT) ==="
spicedb serve-testing --grpc-addr ":${SPICEDB_GRPC_PORT}" --http-enabled=false --readonly-grpc-enabled=false \
  --skip-release-check --log-level warn &
SPICEDB_PID=$!
sleep 2

zed schema write "$SHARED_DIR/schema.zed" --endpoint "$SPICEDB_ENDPOINT" --token "$SPICEDB_TOKEN" --insecure
echo "schema written"

echo "=== [$FIXTURE] seeding SpiceDB ==="
node "$SCRIPT_DIR/gen-spicedb-seed.mjs" | while read -r resource relation subject; do
  zed relationship create "$resource" "$relation" "$subject" --endpoint "$SPICEDB_ENDPOINT" --token "$SPICEDB_TOKEN" --insecure >/dev/null
done
echo "seeded"

echo "=== [$FIXTURE] running CONVERTED app against live SpiceDB ==="
( cd "$FIXTURE_DIR" && SPICEDB_ENDPOINT="$SPICEDB_ENDPOINT" SPICEDB_TOKEN="$SPICEDB_TOKEN" npx tsx src/converted/app.ts ) | tee "$OUT_DIR/converted.jsonl"

echo "=== [$FIXTURE] comparing ==="
node "$SCRIPT_DIR/compare.mjs" "$OUT_DIR/baseline.jsonl" "$OUT_DIR/converted.jsonl"
