"""Offline hole inspector — render a ported PAC from many angles with BACKFACE CULLING
so skinning tears / gaps read like the in-game engine (a tear shows as a see-through
hole instead of Blender's default visible interior). Optionally STRESS-POSE the armature
(manual bone rotations, NOT the imported anim — the importer's FK diverges from the
engine, but the skinning weights are correct, so manual posing reproduces the tears).

Use to iterate on skinning fixes without cold-booting: fix skinning -> re-render -> repeat
until no holes from any angle, then one cold boot to confirm.

Run:
  /Applications/Blender.app/Contents/MacOS/Blender --background \
      --python blender_mhfu/hole_check.py -- <pac.bin> <out_prefix> [pose]

  pose = "bind" (default) | "stress" (spread legs/wings + bend spine/neck) | "anim" (mid clip)
Writes <out_prefix>_<angle>.png for angles: front, below, left, right, q34, chest.
"""
import math
import os
import sys

import bpy
from mathutils import Vector, Euler

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "tools")))
import importer  # noqa

_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PAC = _argv[0] if _argv else os.path.expanduser(
    "~/Desktop/shit/MHFU/tmp/brute_tigrex_v53_animfix.bin")
OUT = _argv[1] if len(_argv) > 1 else "/tmp/holes"
POSE = _argv[2] if len(_argv) > 2 else "stress"


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)


def enable_backface_cull():
    for m in bpy.data.materials:
        m.use_backface_culling = True
        # also make holes obvious: a flat unlit-ish look helps but keep textures


def bbox(meshes):
    lo = Vector((1e9, 1e9, 1e9)); hi = Vector((-1e9, -1e9, -1e9))
    deps = bpy.context.evaluated_depsgraph_get()
    for o in meshes:
        ev = o.evaluated_get(deps); me = ev.to_mesh()
        for v in me.vertices:
            w = o.matrix_world @ v.co
            lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
        ev.to_mesh_clear()
    return lo, hi


def stress_pose(arm):
    """GENTLE coherent pose: rotate every bone by the SAME small angle on ONE axis,
    so each chain curls smoothly (a "banana") instead of zig-zag-scrambling. This
    separates adjacent body-region boundaries just enough to open seam tears while the
    monster stays recognizable. (Alternating-direction or large angles scramble — the
    rotation compounds down the chain.)"""
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="POSE")
    for pb in arm.pose.bones:
        pb.rotation_mode = "XYZ"
        pb.rotation_euler = Euler((0.06, 0.0, 0.04), "XYZ")
    bpy.ops.object.mode_set(mode="OBJECT")


def anim_pose(arm):
    acts = list(bpy.data.actions)
    if not acts:
        return
    if not arm.animation_data:
        arm.animation_data_create()
    arm.animation_data.action = acts[len(acts) // 2]
    fr = acts[len(acts) // 2].frame_range
    bpy.context.scene.frame_set(int((fr[0] + fr[1]) / 2))


def render_from(name, eye_dir, lo, hi):
    center = (lo + hi) * 0.5
    size = (hi - lo).length or 1.0
    for o in list(bpy.data.objects):
        if o.type in ("CAMERA", "LIGHT"):
            bpy.data.objects.remove(o, do_unlink=True)
    cam_data = bpy.data.cameras.new("cam"); cam_data.clip_end = size * 30
    cam = bpy.data.objects.new("cam", cam_data); bpy.context.scene.collection.objects.link(cam)
    cam.location = center + Vector(eye_dir).normalized() * size * 1.4
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    key = (center - cam.location).normalized()
    for d, e in ((key, 5.0), (Vector((-0.4, 0.5, 0.7)), 2.5)):
        ld = bpy.data.lights.new("s", "SUN"); ld.energy = e
        lo2 = bpy.data.objects.new("s", ld); bpy.context.scene.collection.objects.link(lo2)
        lo2.rotation_euler = Vector(d).to_track_quat("-Z", "Y").to_euler()
    sc = bpy.context.scene
    try: sc.render.engine = "BLENDER_EEVEE"
    except Exception:
        try: sc.render.engine = "BLENDER_EEVEE_NEXT"
        except Exception: pass
    sc.render.resolution_x = 900; sc.render.resolution_y = 700
    sc.render.filepath = f"{OUT}_{name}.png"
    bpy.ops.render.render(write_still=True)
    print(f"[hole_check] {name} -> {sc.render.filepath}", flush=True)


def main():
    clear()
    arm = importer.import_pac(PAC, import_anims=(POSE == "anim"))
    enable_backface_cull()
    if POSE == "stress":
        stress_pose(arm)
    elif POSE == "anim":
        anim_pose(arm)
    meshes = [o for o in arm.children if o.type == "MESH"]
    lo, hi = bbox(meshes)
    print(f"[hole_check] PAC={os.path.basename(PAC)} pose={POSE} bbox Z[{lo.z:.0f},{hi.z:.0f}]", flush=True)
    # Blender: X right, Y fwd(-z mhfu), Z up. Chest faces -Y/down.
    for name, d in (("front", (0, -1, 0.15)), ("below", (0, -0.5, -1)),
                    ("chest", (0, -0.6, -0.5)), ("left", (-1, -0.2, 0)),
                    ("right", (1, -0.2, 0)), ("q34", (0.8, -0.9, -0.4))):
        render_from(name, d, lo, hi)


main()
