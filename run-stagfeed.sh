#!/usr/bin/env bash
set -euo pipefail
ROOT=/opt/stag/stag-feed
WRITE=()
[ "${1:-}" = "--write" ] && WRITE=(-e STAG_WRITE=1)

SINCE="${STAG_SINCE:-$(date -d '3 days ago' +%F)}"
echo ">> fetch (SimpleFIN, since $SINCE)"
( cd "$ROOT/Stag-Feed" && python3 stag_feed.py --since "$SINCE" )

echo ">> merge ($([ ${#WRITE[@]} -eq 0 ] && echo DRY-RUN || echo WRITE))"
docker run --rm --network stag_default \
  -v /opt/stag/frontend:/app -v "$ROOT/Stag-Feed/out":/csv:ro -w /app \
  --env-file /opt/stag/.env --env-file "$ROOT/stag-feed.env" \
  -e STAG_TX_CSV=/csv/transactions.csv -e STAG_BAL_CSV=/csv/balances.csv \
  "${WRITE[@]}" \
  node:22-slim npx --yes vite-node stagfeed/couchImport.ts
