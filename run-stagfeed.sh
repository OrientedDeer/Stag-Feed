#!/usr/bin/env bash
set -euo pipefail
ROOT=/opt/stag/stag-feed
WRITE=()
[ "${1:-}" = "--write" ] && WRITE=(-e STAG_WRITE=1)

# Window must outlast bank posting lag, not just cover "recent" days: a card
# charge can post several days after its transaction date, and the importer
# dedupes on SimpleFIN id, so a late-posting txn only backfills if it's STILL
# inside the fetch window when it finally appears. 3 days missed real Chase
# charges (06-15); 14 gives posting lag comfortable margin (SimpleFIN caps at 90).
SINCE="${STAG_SINCE:-$(date -d '14 days ago' +%F)}"
echo ">> fetch (SimpleFIN, since $SINCE)"
( cd "$ROOT/Stag-Feed" && python3 stag_feed.py --since "$SINCE" )

# Don't leave personal financial data on disk. The merge step below consumes
# these CSVs; remove them on exit (even on error) so they never linger between
# runs.
trap 'rm -f "$ROOT/Stag-Feed/out/transactions.csv" "$ROOT/Stag-Feed/out/balances.csv"' EXIT

echo ">> merge ($([ ${#WRITE[@]} -eq 0 ] && echo DRY-RUN || echo WRITE))"
docker run --rm --network stag_default \
  -v /opt/stag/frontend:/app -v "$ROOT/Stag-Feed/out":/csv:ro -w /app \
  --env-file /opt/stag/frontend/selfhost/.env --env-file "$ROOT/stag-feed.env" \
  -e STAG_TX_CSV=/csv/transactions.csv -e STAG_BAL_CSV=/csv/balances.csv \
  "${WRITE[@]}" \
  node:22-slim npx --yes vite-node stagfeed/couchImport.ts
