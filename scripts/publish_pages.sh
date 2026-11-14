#!/usr/bin/env bash
# Manual fallback: build the static demo from demo/snapshot and push it to a gh-pages branch.
# Normally .github/workflows/pages.yml publishes the demo on every push that touches dashboard/ or demo/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="${PAGES_BASE:-/tracelens/}"
cd "$ROOT/dashboard"
[ -d node_modules ] || npm ci
rm -rf public/demo-data && cp -r ../demo/snapshot public/demo-data
OUT="$(mktemp -d)"
VITE_STATIC_DEMO=1 VITE_BASE="$BASE" VITE_OUT_DIR="$OUT" npx vite build
rm -rf public/demo-data
cp "$OUT/index.html" "$OUT/404.html"; touch "$OUT/.nojekyll"
cd "$OUT" && git init -q -b gh-pages && git add -A && git -c user.name="$(git -C "$ROOT" config user.name || echo TraceLens)" -c user.email="$(git -C "$ROOT" config user.email || echo tracelens@users.noreply.github.com)" commit -qm "Publish demo dashboard" \
  && git push -f "$(git -C "$ROOT" remote get-url origin)" gh-pages
echo "published from $OUT"
