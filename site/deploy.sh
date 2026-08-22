#!/usr/bin/env bash
# Deploy the built site to the Cloudflare Worker (docs/SITE.md).
#
# Needs CLOUDFLARE_API_TOKEN (+ CLOUDFLARE_ACCOUNT_ID) in the environment and
# an existing R2 bucket named velocity-wasm. Any asset over Cloudflare's
# 25 MiB cap (Evidence's DuckDB-WASM binaries) is parked in R2 under its
# content-hashed basename and stripped from the static upload; worker.js
# serves those paths from the bucket.
set -euo pipefail
cd "$(dirname "$0")"

for f in build/_app/immutable/assets/*.wasm; do
  [ -e "$f" ] || continue
  size=$(wc -c < "$f")
  if [ "$size" -gt 26214400 ]; then
    echo "parking $(basename "$f") ($size bytes) in R2"
    npx wrangler r2 object put "velocity-wasm/$(basename "$f")" --file "$f" --remote
    rm "$f"
  fi
done

npx wrangler deploy
