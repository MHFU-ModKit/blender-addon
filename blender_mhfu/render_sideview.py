"""Side-view render for visual monster ID — front-left elevated angle.

Same structure as render_check.py (same path setup, same import importer pattern)
but uses a side/elevation camera angle instead of the default front-view to make
wide flat models (like MHP3rd monsters in engine units) easier to identify.

Run:
    /Applications/Blender.app/Contents/MacOS/Blender --background \
        --python blender_mhfu/render_sideview.py -- <pac.bin> <out.png>
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
PAC = _argv[0] if _argv else None
OUT = _argv[1] if len(_argv) > 1 else "/tmp/monster_side.png"


def main():
    if not PAC:
        raise RuntimeError("usage: blender --background --python render_sideview.py -- <pac> <out.png>")

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)

    arm = importer.import_pac(PAC, import_anims=False)
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
    cam_data.clip_start = size * 0.001
    cam_data.clip_end = size * 20.0
    cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    # Front-left + moderate elevation (avoids top-down for wide flat models)
    cam_dir = Vector((0.55, -1.0, 0.45)).normalized()
    cam.location = center + cam_dir * size * 1.45
    cam.rotation_euler = (
        (center - cam.location).normalized().to_track_quat("-Z", "Y").to_euler()
    )
    bpy.context.scene.camera = cam

    # Key light from camera direction + fill from above-right
    for ang, energy in (
        (cam_dir, 5.0),
        (Vector((-0.3, 0.4, 0.9)).normalized(), 3.0),
    ):
        ld = bpy.data.lights.new("sun", "SUN")
        ld.energy = energy
        lobj = bpy.data.objects.new("sun", ld)
        bpy.context.scene.collection.objects.link(lobj)
        lobj.rotation_euler = ang.to_track_quat("-Z", "Y").to_euler()

    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.04, 0.04, 0.05, 1)
    bg.inputs[1].default_value = 0.5
    bpy.context.scene.world = world

    scn = bpy.context.scene
    try:
        scn.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        scn.render.engine = "BLENDER_EEVEE"
    scn.render.resolution_x, scn.render.resolution_y = 1280, 960
    scn.render.filepath = OUT
    bpy.ops.render.render(write_still=True)
    print("[render_side] wrote %s (bbox size %.1f)" % (OUT, size))


if __name__ == "__main__":
    main()
