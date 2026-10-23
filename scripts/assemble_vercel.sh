#!/usr/bin/env bash
# Assemble the Vercel static output. The read API lives in api/index.py (Vercel
# Python function) and reads the same preview/release tree copied here.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/.vercel-out"
rm -rf "$out"
mkdir -p "$out/design-system" "$out/release"
cp "$root/preview/index.html" "$out/index.html"
cp "$root/preview/role-atlas.html" "$root/preview/role-atlas.css" "$root/preview/role-atlas.js" "$out/"
cp -R "$root/preview/role-atlas-assets" "$out/role-atlas-assets"
cp "$root/design-concepts/design-system/"*.css "$out/design-system/"
cp -R "$root/preview/release/." "$out/release/"
rm -rf "$out/release/.market-staging" "$out/release/.staging"
echo "assembled $(find "$out" -type f | wc -l) files into $out"
