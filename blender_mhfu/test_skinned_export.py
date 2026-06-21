"""Headless test: the addon's RE-SKIN export path reproduces the CLI build.

Proves the "Export PAC (re-skin / port)" operator end-to-end through real Blender:
import a Brute geometry PAC, run exporter.export_skinned_monster_pac with the native
Tigrex frame, and verify the result is byte-identical to the offline reference built
by tools/build_brute_pac.py (chain-aware blend skinning, same-size in-place). With no
scene edits the Blender path and the CLI path must agree exactly.

Run:
    /Applications/Blender.app/Contents/MacOS/Blender --background \
        --python blender_mhfu/test_skinned_export.py -- \
        <geometry_pac.bin> <frame_pac.bin> [<out.bin>]

Exit 0 = pass, 1 = fail.
"""
from __future__ import annotations

import os
import sys
import traceback


def _setup_path():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    for d in [here, repo, os.path.join(repo, "tools")]:
        if d not in sys.path:
            sys.path.insert(0, d)


def run():
    _setup_path()
    import importer
    import exporter
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools"))
    import build_brute_pac

    sep = sys.argv.index("--") if "--" in sys.argv else len(sys.argv)
    args = sys.argv[sep + 1:]
    if len(args) < 2:
        print("usage: ... -- <geometry_pac.bin> <frame_pac.bin> [out.bin]",
              file=sys.stderr)
        sys.exit(1)
    geo_pac, frame_pac = args[0], args[1]
    out_pac = args[2] if len(args) > 2 else "/tmp/brute_skinned_export.bin"

    errors = []
    print("[test] import geometry PAC: %s" % geo_pac)
    arm = importer.import_pac(geo_pac, import_anims=False)
    print("[test]   armature bones=%d" % len(arm.data.bones))

    print("[test] CLI reference build (build_brute_pac, hops=1)")
    ref, st = build_brute_pac.build(geo_pac, frame_pac, nb=3, hops=1)
    print("[test]   ref %d B  vgroups=%d verts=%d avg_pal=%.1f"
          % (len(ref), st["vgroups"], st["verts"], st["avg_pal"]))

    print("[test] addon export_skinned_monster_pac -> %s" % out_pac)
    try:
        info = exporter.export_skinned_monster_pac(arm, out_pac, frame_pac=frame_pac,
                                                   nb=3, hops=1)
        print("[test]   info=%r" % (info,))
    except Exception:
        traceback.print_exc()
        errors.append("export raised")
        info = None

    if info and os.path.isfile(out_pac):
        got = open(out_pac, "rb").read()
        if len(got) != len(ref):
            errors.append("size %d != ref %d" % (len(got), len(ref)))
        elif got != ref:
            # locate first diff for diagnostics
            d = next((i for i in range(len(ref)) if ref[i] != got[i]), -1)
            errors.append("bytes differ from CLI ref at offset %d" % d)
        else:
            print("[test]   BYTE-IDENTICAL to CLI reference")

    if errors:
        for e in errors:
            print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
    print("[test] PASS — addon re-skin export reproduces the CLI build byte-for-byte")
    sys.exit(0)


if __name__ == "__main__":
    run()
