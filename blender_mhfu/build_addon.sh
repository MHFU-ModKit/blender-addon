#!/usr/bin/env bash
# Package the MHFU importer addon: vendor mhfu_model beside the addon and zip it.
# Run from the repo root or anywhere; produces blender_mhfu.zip next to this script.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
stage="$(mktemp -d)/blender_mhfu"
mkdir -p "$stage"
cp "$here"/__init__.py "$here"/importer.py "$here"/exporter.py "$stage"/
cp -r "$repo"/tools/mhfu_model "$stage"/mhfu_model
# drop tests + caches from the vendored copy
rm -rf "$stage"/mhfu_model/tests "$stage"/mhfu_model/__pycache__
out="$here/blender_mhfu.zip"
rm -f "$out"
( cd "$(dirname "$stage")" && zip -qr "$out" blender_mhfu )
echo "built $out"
