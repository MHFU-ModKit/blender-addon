"""Quantify the render sink: import native vs Brute WITH anims, pose the armature across
each clip, and report the mesh's lowest Z (vertical, =MHFU-Y) relative to the skeleton
origin (Z=0). The native should keep feet near Z=0 (on floor); the Brute's retargeted
anim should drop the mesh well below 0 = the lift amount needed.

Run:
  /Applications/Blender.app/Contents/MacOS/Blender --background \
      --python blender_mhfu/compare_posed.py
"""
import os, sys
import bpy
from mathutils import Vector

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "tools")))
import importer  # noqa

NATIVE = os.path.expanduser("~/Desktop/shit/MHFU/workspace/extracted/data_files/file_06185.bin")
BRUTE = os.path.expanduser("~/Desktop/shit/MHFU/tmp/brute_tigrex_v52_segskin.bin")


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)


def zmin_now(meshes):
    z = 1e9
    deps = bpy.context.evaluated_depsgraph_get()
    for o in meshes:
        ev = o.evaluated_get(deps)
        me = ev.to_mesh()
        for v in me.vertices:
            w = (o.matrix_world @ v.co).z
            if w < z: z = w
        ev.to_mesh_clear()
    return z


def measure(label, pac):
    clear()
    arm = importer.import_pac(pac, import_anims=True)
    meshes = [o for o in arm.children if o.type == "MESH"]
    # rest-pose z-min
    rest = zmin_now(meshes)
    actions = list(bpy.data.actions)
    print(f"\n[{label}] {os.path.basename(pac)}: meshes={len(meshes)} actions={len(actions)} restZmin={rest:.1f}", flush=True)
    if not arm.animation_data:
        arm.animation_data_create()
    overall = rest
    sampled = []
    for ai, act in enumerate(actions[:8]):     # first 8 clips
        arm.animation_data.action = act
        fr = act.frame_range
        clipmin = 1e9
        for f in range(int(fr[0]), int(fr[1]) + 1, max(1, int((fr[1]-fr[0])//6) or 1)):
            bpy.context.scene.frame_set(f)
            z = zmin_now(meshes)
            clipmin = min(clipmin, z); overall = min(overall, z)
        sampled.append(clipmin)
    print(f"[{label}] per-clip Zmin (first 8): {[round(s,1) for s in sampled]}", flush=True)
    print(f"[{label}] OVERALL lowest mesh Z (vs origin 0) = {overall:.1f}", flush=True)
    return rest, overall


def main():
    nr, no = measure("NATIVE", NATIVE)
    br, bo = measure("BRUTE", BRUTE)
    print("\n==================== POSED SINK COMPARISON ====================", flush=True)
    print(f"  NATIVE rest={nr:.1f} overall-lowest={no:.1f}", flush=True)
    print(f"  BRUTE  rest={br:.1f} overall-lowest={bo:.1f}", flush=True)
    print(f"  posed delta (brute-native overall) = {bo-no:.1f}", flush=True)
    print("  If native's posed feet ~0 but Brute's stay ~-316 => the native anim LIFTS to floor,", flush=True)
    print("  the Brute's retarget does NOT. Lift amount ~= -(Brute posed Zmin).", flush=True)
    print("==============================================================", flush=True)


main()
