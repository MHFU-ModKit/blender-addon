"""Headless test: the addon's MHP3rd-port path reproduces the CLI byte-for-byte.

Proves the "Port MHP3rd Monster" operator's backend (exporter.port_p3rd_monster_pac)
produces the same .bin as tools/build_p3rd_port.py, and that the addon module imports
+ registers cleanly. Run:

    /Applications/Blender.app/Contents/MacOS/Blender --background \
        --python blender_mhfu/test_p3rd_port.py -- \
        <model.bin> <frame.bin> <geo.bin> <anim.bin> [out.bin]
"""
from __future__ import annotations

import os
import sys
import traceback


def _setup():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    for d in [here, repo, os.path.join(repo, "tools")]:
        if d not in sys.path:
            sys.path.insert(0, d)


def run():
    _setup()
    import exporter
    import build_p3rd_port  # noqa: F401  (ensures CLI module imports)
    from mhfu_model import port_p3rd as PORT

    sep = sys.argv.index("--") if "--" in sys.argv else len(sys.argv)
    a = sys.argv[sep + 1:]
    if len(a) < 4:
        print("usage: ... -- <model> <frame> <geo> <anim> [out]", file=sys.stderr)
        sys.exit(1)
    model, frame, geo, anim = a[0], a[1], a[2], a[3]
    out = a[4] if len(a) > 4 else "/tmp/brute_p3rd_port_addon.bin"

    errors = []
    # 1. addon must import + register without error
    try:
        import importlib
        import blender_mhfu  # the addon package (dev checkout)
        importlib.reload(blender_mhfu)
        blender_mhfu.register()
        blender_mhfu.unregister()
        print("[test] addon register/unregister OK")
    except Exception:
        traceback.print_exc()
        errors.append("addon register failed")

    # 2. CLI reference
    pac_ref, info = PORT.port_monster(open(model, "rb").read(), open(frame, "rb").read(),
                                      geo_companion=open(geo, "rb").read(),
                                      anim_blob=open(anim, "rb").read())
    print("[test] CLI backend: %d verts, %d clips, %s/48 bones, %d B" %
          (info["src_verts"], info.get("anim_clips", 0),
           info.get("bone_map_matched"), info["total"]))

    # 3. addon exporter path
    addon_info = exporter.port_p3rd_monster_pac(model, frame, out, geo_path=geo,
                                                anim_path=anim)
    got = open(out, "rb").read()
    if got != pac_ref:
        d = next((i for i in range(min(len(got), len(pac_ref))) if got[i] != pac_ref[i]), -1)
        errors.append("addon output differs from CLI at byte %d (len %d vs %d)"
                      % (d, len(got), len(pac_ref)))
    else:
        print("[test] addon port == CLI port (byte-identical, %d B)" % len(got))

    # 4. the SHIPPING configuration, not just the defaults. The Brute that gets
    #    injected is skin="transfer"; this wrapper hardcoded "auto" until
    #    2026-08-24, so a green parity test said nothing about the build in use.
    ref_t, info_t = PORT.port_monster(open(model, "rb").read(), open(frame, "rb").read(),
                                      geo_companion=open(geo, "rb").read(),
                                      anim_blob=open(anim, "rb").read(),
                                      skin="transfer")
    out_t = out + ".transfer"
    exporter.port_p3rd_monster_pac(model, frame, out_t, geo_path=geo,
                                   anim_path=anim, skin="transfer")
    got_t = open(out_t, "rb").read()
    if got_t != ref_t:
        errors.append("addon skin='transfer' output differs from CLI (%d vs %d B)"
                      % (len(got_t), len(ref_t)))
    else:
        print("[test] addon skin='transfer' == CLI (byte-identical, %d B), "
              "clips=%s slots=%s" % (len(got_t), info_t.get("anim_clips"),
                                     info_t.get("anim", {}).get("slots")))

    if errors:
        for e in errors:
            print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
    print("[test] PASS — addon MHP3rd-port reproduces the CLI; addon registers clean")
    sys.exit(0)


if __name__ == "__main__":
    run()
