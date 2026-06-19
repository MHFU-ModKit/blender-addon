"""Headless render of an MHP3rd monster PAC.

Usage:
    /Applications/Blender.app/Contents/MacOS/Blender --background \
        --python blender_mhfu/render_p3rd.py -- <pac.bin> <out.png> [geo.bin]

geo.bin is optional; if omitted the script probes for file_(N+1).bin automatically
(in-quest PACs) or treats the PAC as self-contained (lobby PACs).
"""
import os
import sys

import bpy
from mathutils import Vector

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "tools")))
import importer  # noqa: E402

_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if len(_argv) < 2:
    print("Usage: render_p3rd.py -- <pac.bin> <out.png> [geo.bin]")
    sys.exit(1)

PAC = _argv[0]
OUT = _argv[1]
GEO = _argv[2] if len(_argv) > 2 else None


def main():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)

    arm = importer.import_pac_p3rd(PAC, geo_path=GEO, import_anims=False)
    meshes = [o for o in arm.children if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("no mesh objects imported from %s" % PAC)

    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for o in meshes:
        for v in o.data.vertices:
            w = o.matrix_world @ v.co
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    center = (lo + hi) * 0.5
    size = (hi - lo).length or 1.0

    cam_data = bpy.data.cameras.new("cam")
    cam_data.clip_start = size * 0.01
    cam_data.clip_end = size * 20.0
    cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = center + Vector((0.8, -1.0, 0.45)).normalized() * size * 1.5
    cam.rotation_euler = (center - cam.location).normalized().to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    key_dir = (center - cam.location).normalized()
    for ang, energy in ((key_dir, 6.0), (Vector((-0.5, 0.6, 0.6)).normalized(), 2.0)):
        ld = bpy.data.lights.new("sun", "SUN")
        ld.energy = energy
        lobj = bpy.data.objects.new("sun", ld)
        bpy.context.scene.collection.objects.link(lobj)
        lobj.rotation_euler = ang.to_track_quat("-Z", "Y").to_euler()

    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.05, 0.05, 0.06, 1)
    bg.inputs[1].default_value = 0.6
    bpy.context.scene.world = world

    scn = bpy.context.scene
    try:
        scn.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        scn.render.engine = "BLENDER_EEVEE"
    scn.render.resolution_x, scn.render.resolution_y = 1280, 960
    scn.render.filepath = OUT
    bpy.ops.render.render(write_still=True)
    print("[render_p3rd] wrote %s (bbox size %.1f)" % (OUT, size))


if __name__ == "__main__":
    main()
