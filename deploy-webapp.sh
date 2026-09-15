#!/bin/bash
# Rebuild webapp + copy bundle into gateway static + rebuild gateway image.
# Run after ANY webapp/src change — otherwise the panel serves the OLD bundle.
# (webapp/dist is gitignored; gateway/app/static is the served copy.)
set -e
cd "$(dirname "$0")"
cd webapp && npm run build 2>&1 | grep -E "error|✓ built"
cd ..
cp -r webapp/dist/* gateway/app/static/
cp webapp/dist/index.html gateway/app/static/index.html
# sanity: every asset referenced by index.html must exist
missing=0
for f in $(grep -o '/app/assets/[^"]*' gateway/app/static/index.html | sed 's|/app/assets/||' | sort -u); do
  [ -f "gateway/app/static/assets/$f" ] || { echo "MISSING: $f"; missing=1; }
done
[ "$missing" = 1 ] && exit 1
docker compose build gateway 2>&1 | tail -1
docker compose up -d gateway 2>&1 | tail -1
echo "deployed — verify: curl -s http://127.0.0.1:9001/health"
