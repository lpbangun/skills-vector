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
# Public, compact selected Role Atlas export only; never copy an audit/store.
atlas="$root/research/role-atlas/2026-Q4-union-r3"
mkdir -p "$out/role-atlas/roles" "$out/role-atlas/views"
cp "$atlas"/roles/*.json "$out/role-atlas/roles/"
cp "$atlas"/views/*.json "$out/role-atlas/views/"
cp "$atlas/release.json" "$atlas/ledger.json" "$atlas/backtest.json" "$out/role-atlas/"
echo "assembled $(find "$out" -type f | wc -l) files into $out"
