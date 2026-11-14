#!/usr/bin/env bash
# Build the static read-only demo dashboard from demo/snapshot and push it to the gh-pages branch.
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
