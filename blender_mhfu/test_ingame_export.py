"""Headless test: the Blender addon's IN-GAME export path produces a usable .bin.

Proves milestone 2 end-to-end through real Blender (not just the library): import
an MHFU big-monster PAC, run exporter.export_ingame_bindpose_pac (the path behind
the "Export PAC (in-game anim)" operator), and verify the output is a structurally
valid recursive 3-stream in-game anim PAC with rest-pose (non-empty) bone sections.

Run:
    /Applications/Blender.app/Contents/MacOS/Blender --background \
        --python blender_mhfu/test_ingame_export.py -- <mhfu_pac.bin> [<out.bin>]

Exit 0 = pass, 1 = fail (details on stderr).
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
    import importer        # dev checkout (same pattern as the other headless tests)
    import exporter
    from mhfu_model import anim_ingame as ai
    from mhfu_model import pac as _pac

    sep = sys.argv.index("--") if "--" in sys.argv else len(sys.argv)
    args = sys.argv[sep + 1:]
    src_pac = args[0] if args else None
    out_pac = args[1] if len(args) > 1 else "/tmp/brute_ingame_export.bin"
    if not src_pac or not os.path.isfile(src_pac):
        print("usage: ... -- <mhfu_pac.bin> [out.bin]", file=sys.stderr)
        sys.exit(1)

    errors = []
    print("[test] import MHFU PAC: %s" % src_pac)
    arm = importer.import_pac(src_pac, import_anims=False)
    n_bones = len(arm.data.bones)
    print("[test]   armature bones=%d" % n_bones)
    if n_bones == 0:
        errors.append("import: 0 bones")

    print("[test] export in-game bind-pose PAC: %s" % out_pac)
    try:
        info = exporter.export_ingame_bindpose_pac(arm, out_pac)
        print("[test]   info=%r" % (info,))
    except Exception:
        traceback.print_exc()
        errors.append("export raised")
        info = None

    if info and os.path.isfile(out_pac):
        data = open(out_pac, "rb").read()
        pac = _pac.MonsterPac.from_bytes(data)
        m = ai.parse_ingame(pac.subs[3].data)
        pops = [len(s.clips) for s in m.streams]
        print("[test]   PAC %d B | anim streams populated=%r" % (len(data), pops))
        # at least one populated stream, every bone section has rot keyframes
        live = [s for s in m.streams if s.clips]
        if not live:
            errors.append("no populated anim streams")
        total_bones = 0
        for st in live:
            blk = next(iter(st.clips.values()))
            total_bones += len(blk.bones)
            for b in blk.bones:
                if not b.channels or any(not c.keyframes for c in b.channels):
                    errors.append("bone section without keyframes (would collapse)")
                    break
        if total_bones != info["animated"]:
            errors.append("stream bones %d != animated %d" % (total_bones, info["animated"]))
        print("[test]   total anim bones=%d (animated=%d)" % (total_bones, info["animated"]))

    if errors:
        for e in errors:
            print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
    print("[test] PASS — addon in-game export produces a valid recursive 3-stream "
          "rest-pose PAC")
    sys.exit(0)


if __name__ == "__main__":
    run()
