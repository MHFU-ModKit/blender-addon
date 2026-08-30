"""Orbit views of a BUILT MHFU PAC, posed by its own in-game clip — the port oracle.

The point of this script is to make an offline picture that can be *trusted against
the game*, so a port can be iterated in Blender instead of one cold boot per guess
(a boot + walk-in is ~5 minutes; this is ~15 seconds).

Three things had to be true before that was possible, and all three now are:

1. **The skin is the file's own.** `pmo._attach_influences` resolves the PMO bone
   palette for MHFU PACs the way `pmo_p3rd` always did for MHP3rd, so every vertex
   carries its authentic `(bone, weight)` list. The old fallback — "mesh group N is
   welded to bone N" — welded 166 of the native Tigrex's 214 groups onto one bone,
   which is the "cluster of parts floating beside it" this directory's notes record.
2. **The FK is the engine's.** Rotation channels are a joint's ABSOLUTE LOCAL
   rotation, not a Blender pose rotation; `render_ported_anim._pose` / `fk_bake`
   do the conversion.
3. **The clips are whole-rig.** `anim_ingame.to_flat_anim` recombines the engine's
   per-stream bone partitions.

    ./blender_mhfu/blender-docker.sh --background \
        --python blender_mhfu/render_port_views.py -- <pac.bin> <outdir> [slot] [frame]

⚠️ `outdir` must be REPO-RELATIVE. The container mounts the repo, not /tmp, so a
/tmp path is written inside the container and vanishes on exit — the run reports
success and leaves nothing behind.

Env:
  MHFU_VIEW_RES     "900x700"
  MHFU_VIEW_ANGLES  comma list of side,front,back,three,top   (default all but back)
  MHFU_VIEW_HILITE  comma list of joints to paint RED, e.g. "46,47,48,49,50" — the
                    fastest way to answer "where did that patch of geometry go?".
                    An object is highlighted when its DOMINANT joint is in the set.
  MHFU_VIEW_REST    1 = ignore the clip and render the bind pose
  MHFU_VIEW_ENGINE  default BLENDER_WORKBENCH (needs the xvfb image; see docker/)
"""
import os
import sys

import bpy
from mathutils import Vector

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "tools")))
import importer                                    # noqa: E402
import render_ported_anim as RPA                   # noqa: E402
from mhfu_model import load_pac                    # noqa: E402

_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PAC = _argv[0]
OUTDIR = _argv[1] if len(_argv) > 1 else "tmp/port_views"
SLOT = int(_argv[2]) if len(_argv) > 2 else 7
FRAME = _argv[3] if len(_argv) > 3 else None

_res = os.environ.get("MHFU_VIEW_RES", "900x700").split("x")
RES = (int(_res[0]), int(_res[1]))
ANGLES = [a.strip() for a in
          os.environ.get("MHFU_VIEW_ANGLES", "side,three,front,top").split(",") if a.strip()]
HILITE = {int(x) for x in os.environ.get("MHFU_VIEW_HILITE", "").replace(" ", "").split(",") if x}
REST = bool(os.environ.get("MHFU_VIEW_REST"))
# 1 = hide everything EXCEPT the highlighted joints (answers "what IS that patch?"),
# 2 = hide the highlighted joints (answers "what is left without it?").
ONLY = int(os.environ.get("MHFU_VIEW_ONLY", "0"))

# Where the camera sits, as a direction in Blender space (X right, Y depth, Z up).
# ⚠️ `importer.conv` is (x, y, z) -> (x, -z, y), so the engine's +Z — the NOSE —
# comes out along Blender **-Y**. A camera on -Y therefore sees the FRONT, and the
# profile view is on ±X. Getting this backwards labels every render wrong, which is
# exactly how a chest slab gets filed as "the back".
DIRS = {
    "side":  Vector((1.0, 0.0, 0.12)),
    "three": Vector((0.95, -0.85, 0.35)),
    "front": Vector((0.0, -1.0, 0.15)),
    "back":  Vector((0.0, 1.0, 0.15)),
    "top":   Vector((0.25, 0.0, 1.0)),
}


def dominant_joint(obj):
    """The vertex group holding the most total weight on this object."""
    tot = {}
    for v in obj.data.vertices:
        for g in v.groups:
            name = obj.vertex_groups[g.group].name
            tot[name] = tot.get(name, 0.0) + g.weight
    if not tot:
        return -1
    best = max(tot, key=tot.get)
    try:
        return int(best.split("_")[1])
    except (IndexError, ValueError):
        return -1


def flat_materials(meshes):
    """Workbench-visible flat colours: grey, or RED for a highlighted joint."""
    grey = bpy.data.materials.new("port_grey")
    grey.diffuse_color = (0.72, 0.72, 0.74, 1.0)
    red = bpy.data.materials.new("port_hilite")
    red.diffuse_color = (0.90, 0.13, 0.13, 1.0)
    n_hi = 0
    for o in meshes:
        j = dominant_joint(o)
        hot = j in HILITE
        n_hi += 1 if hot else 0
        o.data.materials.clear()
        o.data.materials.append(red if hot else grey)
    return n_hi


def bounds(meshes):
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    dg = bpy.context.evaluated_depsgraph_get()
    for o in meshes:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        for v in me.vertices:
            w = o.matrix_world @ v.co
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
        ev.to_mesh_clear()
    return lo, hi


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)

    arm = importer.import_pac(PAC, import_anims=False)
    meshes = [o for o in arm.children if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("no meshes imported from %s" % PAC)
    n_hi = flat_materials(meshes)
    print("[views] %d mesh objects, %d highlighted (joints %s)"
          % (len(meshes), n_hi, sorted(HILITE) or "-"))
    if ONLY:
        keep = []
        for o in meshes:
            hot = dominant_joint(o) in HILITE
            if hot if ONLY == 1 else not hot:
                keep.append(o)
            else:
                o.hide_render = True
        print("[views] MHFU_VIEW_ONLY=%d -> %d of %d objects visible"
              % (ONLY, len(keep), len(meshes)))
        meshes = keep or meshes

    order = os.environ.get("MHFU_ROT_MODE", "XYZ")
    for pb in arm.pose.bones:
        pb.rotation_mode = order

    label = "rest"
    if not REST:
        clips = RPA._clips(PAC)
        anim = clips.get(SLOT)
        if anim is None:
            print("[views] slot %d is EMPTY — rendering the bind pose instead" % SLOT)
        else:
            skel = load_pac(PAC).skeleton
            samples = RPA._sample(anim)
            last = max((f for j in samples.values() for k in j.values()
                        for pts in k.values() for f, _v in pts), default=1)
            frame = int(FRAME) if FRAME is not None else int(last * 0.5)
            print("[views] slot %d: %d joints, %d frames -> posing frame %d"
                  % (SLOT, len(anim.tracks), last, frame))
            RPA._pose(arm, skel, samples, frame, order)
            label = "s%03d_f%03d" % (SLOT, frame)

    scn = bpy.context.scene
    scn.render.engine = os.environ.get("MHFU_VIEW_ENGINE", "BLENDER_WORKBENCH")
    scn.render.resolution_x, scn.render.resolution_y = RES
    scn.render.film_transparent = False
    if scn.render.engine == "BLENDER_WORKBENCH":
        sh = scn.display.shading
        sh.light = "STUDIO"
        sh.color_type = "MATERIAL"
        sh.show_shadows = True
        sh.show_cavity = True
    if scn.world is None:
        scn.world = bpy.data.worlds.new("w")

    lo, hi = bounds(meshes)
    center = (lo + hi) * 0.5
    size = (hi - lo).length or 1.0
    cd = bpy.data.cameras.new("cam")
    cd.clip_start, cd.clip_end = size * 0.005, size * 40.0
    cam = bpy.data.objects.new("cam", cd)
    scn.collection.objects.link(cam)
    scn.camera = cam

    out = []
    for ang in ANGLES:
        d = DIRS.get(ang)
        if d is None:
            print("[views] unknown angle %r — skipped" % ang)
            continue
        cam.location = center + d.normalized() * size * 1.15
        cam.rotation_euler = (center - cam.location).normalized() \
            .to_track_quat("-Z", "Y").to_euler()
        path = os.path.join(OUTDIR, "%s_%s.png" % (label, ang))
        scn.render.filepath = path
        bpy.ops.render.render(write_still=True)
        out.append(path)
    print("[views] wrote %d images to %s" % (len(out), OUTDIR))


if __name__ == "__main__":
    main()
