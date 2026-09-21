#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/.vercel-out"
rm -rf "$out"
mkdir -p "$out/design-system" "$out/release"
cp "$root/preview/index.html" "$out/index.html"
cp "$root/design-concepts/design-system/"*.css "$out/design-system/"
cp "$root/design-concepts/app.html" "$out/desk.html"
cp -R "$root/preview/release/." "$out/release/"
# app.html links to design-system/ relatively; desk.html is already at the output root.
