"""Compare where the native vs Brute mesh sits RELATIVE TO THE SKELETON ORIGIN (Z=0).

Imports each PAC in BIND pose, prints the mesh vertical (Z) min/max relative to the
armature origin, and renders a side view with a ground plane at Z=0 so we can SEE if
one mesh dips below origin more than the other (= a static model-datum offset = sink
hypothesis (a)). MHFU-Y maps to Blender-Z (up) per importer.py:36.

Run:
  /Applications/Blender.app/Contents/MacOS/Blender --background \
      --python blender_mhfu/compare_height.py
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


def bbox(meshes):
    lo = Vector((1e9, 1e9, 1e9)); hi = Vector((-1e9, -1e9, -1e9))
    for o in meshes:
        for v in o.data.vertices:
            w = o.matrix_world @ v.co
            lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
    return lo, hi


def render(label, out, lo, hi):
    # ground plane at Z=0 (skeleton origin)
    bpy.ops.mesh.primitive_plane_add(size=max((hi - lo).length, 2000) * 2.0, location=(0, 0, 0))
    center = (lo + hi) * 0.5
    size = (hi - lo).length or 1.0
    cam_data = bpy.data.cameras.new("cam"); cam_data.clip_end = size * 30
    cam = bpy.data.objects.new("cam", cam_data); bpy.context.scene.collection.objects.link(cam)
    # straight side view (looking down -Y), so Z(up) offset vs the ground plane is visible
    cam.location = Vector((center.x, center.y - size * 2.2, 0.0))
    cam.rotation_euler = (1.5708, 0, 0)
    bpy.context.scene.camera = cam
    ld = bpy.data.lights.new("sun", "SUN"); ld.energy = 5.0
    lo2 = bpy.data.objects.new("sun", ld); bpy.context.scene.collection.objects.link(lo2)
    lo2.rotation_euler = (0.9, 0.2, 0.3)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE_NEXT" if hasattr(bpy.data, "node_groups") else "BLENDER_EEVEE"
    try: sc.render.engine = "BLENDER_EEVEE"
    except Exception: pass
    sc.render.resolution_x = 800; sc.render.resolution_y = 600
    sc.render.filepath = out
    bpy.ops.render.render(write_still=True)
    print(f"[{label}] rendered -> {out}", flush=True)


def do(label, pac, out):
    clear()
    arm = importer.import_pac(pac, import_anims=False)
    meshes = [o for o in arm.children if o.type == "MESH"]
    lo, hi = bbox(meshes)
    print(f"\n[{label}] {os.path.basename(pac)}", flush=True)
    print(f"  world bbox  X[{lo.x:.1f},{hi.x:.1f}]  Y[{lo.y:.1f},{hi.y:.1f}]  Z(up)[{lo.z:.1f},{hi.z:.1f}]", flush=True)
    print(f"  vertical (Z) relative to skeleton origin: min={lo.z:.1f}  max={hi.z:.1f}  (origin=0)", flush=True)
    render(label, out, lo, hi)
    return lo.z, hi.z


def main():
    nz = do("NATIVE", NATIVE, "/tmp/cmp_native.png")
    bz = do("BRUTE", BRUTE, "/tmp/cmp_brute.png")
    print("\n==================== HEIGHT COMPARISON ====================", flush=True)
    print(f"  NATIVE vertical Z: [{nz[0]:.1f}, {nz[1]:.1f}]", flush=True)
    print(f"  BRUTE  vertical Z: [{bz[0]:.1f}, {bz[1]:.1f}]", flush=True)
    print(f"  delta min (brute-native) = {bz[0]-nz[0]:.1f}  (negative = Brute dips LOWER = model sink)", flush=True)
    print("  If ~0: bind-pose datum identical -> sink is runtime (terrain), not the model.", flush=True)
    print("==========================================================", flush=True)


main()
