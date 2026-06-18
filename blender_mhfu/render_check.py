"""Headless render of a big-monster PAC — a quick visual check for edits.

Imports a PAC (reshaped / grown / pristine) with the addon's importer (textures +
materials + geometry), auto-frames a camera on the model, lights it from the front,
and writes a PNG. Useful to eyeball a Blender edit or a `pmo_topology` grow without
booting the game (the in-game render is still the ground truth).

Run (note the `--` so Blender passes the rest to this script):

    /Applications/Blender.app/Contents/MacOS/Blender --background \
        --python blender_mhfu/render_check.py -- <pac.bin> <out.png>

With no args it renders the live grown Tigrex PAC from the PPSSPP inject dir to
/tmp/tigrex_edited.png. This is the formalized version of the one-off render that
verified the Phase-5 textured/lit dome.
"""
import os
import sys

import bpy
from mathutils import Vector

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "tools")))
import importer  # noqa: E402

# args after "--"
_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PAC = _argv[0] if _argv else os.path.expanduser(
    "~/.config/ppsspp/PSP/PLUGINS/mhfu_framework/inject/file_06185_grown.bin")
OUT = _argv[1] if len(_argv) > 1 else "/tmp/tigrex_edited.png"


def main():
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
    cam_data.clip_start = size * 0.01
    cam_data.clip_end = size * 20.0
    cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = center + Vector((0.8, -1.0, 0.45)).normalized() * size * 1.5
    cam.rotation_euler = (center - cam.location).normalized().to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    # key light from the front (along the view) + a soft fill
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
        scn.render.engine = "BLENDER_EEVEE_NEXT"      # Blender 4.2+
    except Exception:
        scn.render.engine = "BLENDER_EEVEE"
    scn.render.resolution_x, scn.render.resolution_y = 1280, 960
    scn.render.filepath = OUT
    bpy.ops.render.render(write_still=True)
    print("[render_check] wrote %s (bbox size %.1f)" % (OUT, size))


if __name__ == "__main__":
    main()
