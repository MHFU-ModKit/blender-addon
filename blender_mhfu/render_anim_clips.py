"""Render one MP4 per ported MHP3rd animation clip — a moveset catalog for labeling.

Imports the MHP3rd Brute PAC (model file_05248 + GE file_05249 + moveset file_05250)
with the addon importer, which builds one Blender Action per clip named `anim_<slot>`.
Renders each clip to <outdir>/anim_<slot>.mp4 (Workbench, textured — fast and plenty
clear to recognize a move) and writes a labeling template CSV.

    /Applications/Blender.app/Contents/MacOS/Blender --background \
        --python blender_mhfu/render_anim_clips.py -- [outdir] [only_slot]

  outdir     default /tmp/brute_anim_clips
  only_slot  render just this one slot (for a quick pipeline test)

Also writes MHFU_CLIP_SHEET (default 4) PNG stills per clip as `still_<slot>_<k>.png`,
which `tools/moveset_sheet.py` composes into one labelling contact sheet.

Env: MHFU_CLIP_RES (e.g. 640x480), MHFU_CLIP_STEP (frame step, default 1),
MHFU_CLIP_SHEET (stills per clip, 0 to disable).
"""
import csv
import glob
import os
import sys

import bpy
from mathutils import Vector

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "tools")))
import importer  # noqa: E402
from mhfu_model import load_pac_p3rd  # noqa: E402
from mhfu_model import convert as C  # noqa: E402
from mhfu_model import pmo_skin as _skin  # noqa: E402
import fk_bake  # noqa: E402

_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUTDIR = _argv[0] if _argv else "/tmp/brute_anim_clips"
ONLY = int(_argv[1]) if len(_argv) > 1 else None
# PNG stills per clip for the contact sheet; 0 disables.
SHEET = int(os.environ.get("MHFU_CLIP_SHEET", "4"))

_ROOT = os.path.abspath(os.path.join(_HERE, "..", "workspace", "extracted_mhp3", "data_files"))
MODEL = os.path.join(_ROOT, "file_05248.bin")
GEO = os.path.join(_ROOT, "file_05249.bin")
ANIM = os.path.join(_ROOT, "file_05250.bin")

_res = os.environ.get("MHFU_CLIP_RES", "640x480").split("x")
RES = (int(_res[0]), int(_res[1]))
STEP = int(os.environ.get("MHFU_CLIP_STEP", "1"))
FPS = 30


def _slot_of(action):
    try:
        return int(action.name.split("_")[1])
    except Exception:
        return 1 << 30


def rebind_nearest_bone(arm, meshes):
    """Rigid: bind each mesh GROUP (whole piece) to the bone nearest its centroid.
    The MHP3rd pieces are modelled in place, so this assembles a coherent monster
    whose per-clip motion reads clearly. Pieces are stiff but DON'T tear/stretch
    (unlike the per-vertex blend, where wing-membrane verts stretch to a far bone) —
    cleaner for recognizing/labeling moves."""
    heads = {b.name: (arm.matrix_world @ b.head_local) for b in arm.data.bones}
    for o in meshes:
        if not o.data.vertices:
            continue
        c = Vector((0, 0, 0))
        for v in o.data.vertices:
            c += o.matrix_world @ v.co
        c /= len(o.data.vertices)
        bname = min(heads, key=lambda n: (heads[n] - c).length)
        for vg in list(o.vertex_groups):
            o.vertex_groups.remove(vg)
        vg = o.vertex_groups.new(name=bname)
        vg.add(range(len(o.data.vertices)), 1.0, "REPLACE")


def _neighborhoods(arm, hops):
    """Per-bone tree-neighborhood (bone names within `hops` skeleton edges) — used
    to keep a vertex's blend partners anatomically connected (the auto_skin
    chain-aware fix: a tail vert blends adjacent tail joints, not a euclidean-near
    leg bone)."""
    names = [b.name for b in arm.data.bones]
    adj = {n: set() for n in names}
    for b in arm.data.bones:
        if b.parent:
            adj[b.name].add(b.parent.name)
            adj[b.parent.name].add(b.name)
    nbhd = {}
    for n in names:
        seen = {n}
        frontier = {n}
        for _ in range(hops):
            nxt = set()
            for x in frontier:
                nxt |= adj[x]
            nxt -= seen
            seen |= nxt
            frontier = nxt
        nbhd[n] = seen
    return nbhd


def blend_skin(arm, meshes, nb=3, hops=2):
    """Per-vertex nearest-N-bone inverse-distance blend (chain-aware) — the auto_skin
    method. The MHP3rd source has no weights; the addon importer binds each mesh group
    rigidly to one bone (group=bone by index), which splays/holes under animation.
    Here each VERTEX blends its `nb` nearest bones drawn only from its primary bone's
    tree-neighborhood, so joints deform smoothly without cross-region bleed."""
    heads = {b.name: (arm.matrix_world @ b.head_local) for b in arm.data.bones}
    names = list(heads.keys())
    nbhd = _neighborhoods(arm, hops)
    for o in meshes:
        for vg in list(o.vertex_groups):
            o.vertex_groups.remove(vg)
        cache = {}
        for vi, v in enumerate(o.data.vertices):
            w = o.matrix_world @ v.co
            prim = min(names, key=lambda n: (heads[n] - w).length)
            cand = sorted(nbhd[prim], key=lambda n: (heads[n] - w).length)[:nb]
            ws = [1.0 / ((heads[n] - w).length + 1e-4) for n in cand]
            s = sum(ws) or 1.0
            for n, wt in zip(cand, ws):
                g = cache.get(n) or o.vertex_groups.new(name=n)
                cache[n] = g
                g.add([vi], wt / s, "REPLACE")


def proper_skin(arm, meshes, mm):
    """The porter's tested anti-tear blend: pmo_skin.auto_skin with chain-aware
    (parents), segment-distance + region-lock (defaults). Skins the SOURCE geometry
    to the SOURCE rig (keeps clean source anim IDs). This is the same routine that
    made the in-game Brute 'way less torn apart' — vs the naive per-vertex blend
    (which stretched the wing) or the rigid bind (which disconnects at the joints)."""
    import re
    skel = mm.skeleton
    n = len(skel.bones)
    wp = C.bone_world_positions(skel)            # {index: (x,y,z)} engine space
    bw = [tuple(wp[i]) for i in range(n)]
    parents = [0] * n
    for b in skel.bones:
        parents[b.index] = b.parent
    vgs = _skin.auto_skin(mm.model.mesh_groups, bw, parents=parents, nb=3, hops=1)

    by_idx = {}
    for o in meshes:
        m = re.search(r"grp(\d+)", o.name)
        if m:
            by_idx[int(m.group(1))] = o
    for vg in vgs:
        o = by_idx.get(vg.index)
        if o is None or len(o.data.vertices) != len(vg.influences):
            continue
        for g in list(o.vertex_groups):
            o.vertex_groups.remove(g)
        cache = {}
        for vi, infl in enumerate(vg.influences):
            for bone, w in infl:
                if bone is None or bone < 0 or w <= 0:
                    continue
                nm = "bone_%d" % bone
                g = cache.get(nm) or o.vertex_groups.new(name=nm)
                cache[nm] = g
                g.add([vi], float(w), "ADD")


def strip_root_motion(mm):
    """Remove world-translation channels on the root bone(s) so each clip plays
    IN PLACE (locomotion stays centred in frame instead of walking out of view).
    Rotation + non-root translation (the body articulation) are kept."""
    n = len(mm.skeleton.bones)
    roots = [b.index for b in mm.skeleton.bones if not (0 <= b.parent < n)]
    paths = {'pose.bones["bone_%d"].location' % i for i in roots}
    for act in bpy.data.actions:
        for fc in list(act.fcurves):
            if fc.data_path in paths:
                act.fcurves.remove(fc)


def setup_camera(meshes):
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
    cam_data.clip_end = size * 40.0
    cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    # 3/4 front, pulled back generously so root motion stays roughly in frame
    cam.location = center + Vector((0.85, -1.0, 0.40)).normalized() * size * 1.7
    cam.rotation_euler = (center - cam.location).normalized().to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.05, 0.05, 0.06, 1)
    bg.inputs[1].default_value = 0.8
    bpy.context.scene.world = world
    return center, size


def setup_render(scn):
    scn.render.engine = "BLENDER_WORKBENCH"
    sh = scn.display.shading
    sh.light = "STUDIO"
    sh.color_type = "TEXTURE"
    sh.show_shadows = True
    sh.show_cavity = True
    scn.render.resolution_x, scn.render.resolution_y = RES
    scn.render.fps = FPS
    scn.frame_step = STEP
    scn.render.image_settings.file_format = "FFMPEG"
    scn.render.ffmpeg.format = "MPEG4"
    scn.render.ffmpeg.codec = "H264"
    scn.render.ffmpeg.constant_rate_factor = "MEDIUM"
    scn.render.use_file_extension = True


def render_clip(scn, arm, action, outdir):
    arm.animation_data.action = action
    fr = action.frame_range
    scn.frame_start = int(fr[0])
    scn.frame_end = int(fr[1])
    # render to a temp base, then rename to anim_NNN.mp4 (movie output appends the
    # frame range to the filepath, so the final name is unpredictable up front)
    for stale in glob.glob(os.path.join(outdir, ".tmp_clip*")):
        os.remove(stale)
    scn.render.filepath = os.path.join(outdir, ".tmp_clip")
    bpy.ops.render.render(animation=True)
    slot = _slot_of(action)
    produced = sorted(glob.glob(os.path.join(outdir, ".tmp_clip*")))
    dst = os.path.join(outdir, "anim_%03d.mp4" % slot)
    if produced:
        os.replace(produced[0], dst)

    # A few PNG stills alongside the video. Scrubbing 58 mp4s to find one move is
    # slow; a contact sheet of the whole moveset is how you actually label it. The
    # extra frames cost ~0.2 s on a 2.6 s clip because the scene is already set up.
    if SHEET > 0:
        fmt = scn.render.image_settings.file_format
        scn.render.image_settings.file_format = "PNG"
        lo, hi = int(fr[0]), int(fr[1])
        span = max(1, hi - lo)
        for k in range(SHEET):
            scn.frame_set(lo + int(round(span * k / max(1, SHEET - 1))))
            scn.render.filepath = os.path.join(outdir, "still_%03d_%d" % (slot, k))
            bpy.ops.render.render(write_still=True)
        scn.render.image_settings.file_format = fmt
    return slot, int(fr[1] - fr[0] + 1)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)

    arm = importer.import_pac_p3rd(MODEL, geo_path=GEO, anim_path=ANIM, import_anims=True)
    meshes = [o for o in arm.children if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("no meshes imported")

    skin = os.environ.get("MHFU_CLIP_SKIN", "auto")
    if skin == "blend":
        blend_skin(arm, meshes)
    elif skin == "rigid":
        rebind_nearest_bone(arm, meshes)
    else:                                   # default: porter's tested anti-tear auto_skin
        mm = load_pac_p3rd(MODEL, geo_path=GEO, anim_path=ANIM)
        proper_skin(arm, meshes, mm)
        strip_root_motion(mm)
    # 🔴 REBAKE THE ACTIONS. The importer keys `pose_bone.rotation_euler` from the
    # engine's channels, which are a different quantity (see fk_bake) — the result
    # bends every extremity away while the rest pose is clean. MHFU_CLIP_RAW=1
    # keeps the importer's version for comparison.
    mm_full = load_pac_p3rd(MODEL, geo_path=GEO, anim_path=ANIM)
    if not os.environ.get("MHFU_CLIP_RAW") and mm_full.anim and mm_full.skeleton:
        want = mm_full.anim.animations
        if ONLY is not None:
            want = [a for a in want if a.slot == ONLY]
        fk_bake.bake_all(arm, mm_full.skeleton, want,
                         order=os.environ.get("MHFU_ROT_MODE", "XYZ"))

    center, size = setup_camera(meshes)
    scn = bpy.context.scene
    setup_render(scn)

    # MHFU_CLIP_REST=1 renders ONE still with the armature in REST position and
    # stops. Same camera, same skinning, same engine as the clips — which is the
    # point: it separates "the mesh is wrong" from "the animation is wrong", and
    # a bind pose rendered by a different script with different lighting cannot.
    if os.environ.get("MHFU_CLIP_REST"):
        arm.data.pose_position = "REST"
        scn.render.image_settings.file_format = "PNG"
        cam = scn.camera
        # Orbit + a top-down. One three-quarter view cannot tell a hole in the mesh
        # from a dark texture or a face pointing away, and "holes in his back" is
        # exactly the claim that needs looking at from above.
        import math
        views = {"front": (0.0, -1.0, 0.25), "side": (1.0, 0.0, 0.25),
                 "rear": (0.0, 1.0, 0.25), "top": (0.0, -0.001, 1.0),
                 "under": (0.0, -0.001, -1.0), "three_quarter": (0.85, -1.0, 0.40)}
        for name, d in views.items():
            v = Vector(d).normalized()
            cam.location = center + v * size * 1.7
            cam.rotation_euler = (center - cam.location).normalized().to_track_quat(
                "-Z", "Y").to_euler()
            scn.render.filepath = os.path.join(OUTDIR, "rest_" + name)
            bpy.ops.render.render(write_still=True)
        print("RENDER wrote %d REST views -> %s" % (len(views), OUTDIR))
        return

    acts = sorted(bpy.data.actions, key=_slot_of)
    if ONLY is not None:
        acts = [a for a in acts if _slot_of(a) == ONLY]
    print("RENDER %d clip(s) -> %s (res=%dx%d step=%d)" % (len(acts), OUTDIR, RES[0], RES[1], STEP))

    rows = []
    for i, a in enumerate(acts):
        slot, nframes = render_clip(scn, arm, a, OUTDIR)
        secs = nframes / float(FPS)
        rows.append((slot, nframes, round(secs, 2)))
        print("RENDER_DONE [%d/%d] anim_%03d frames=%d (%.1fs)" % (i + 1, len(acts), slot, nframes, secs))

    # labeling template (only (re)write the full index when rendering everything)
    if ONLY is None:
        idx = os.path.join(OUTDIR, "_moveset_index.csv")
        with open(idx, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["anim_id", "clip_file", "frames", "seconds", "label", "notes"])
            for slot, nframes, secs in rows:
                w.writerow(["anim_%03d" % slot, "anim_%03d.mp4" % slot, nframes, secs, "", ""])
        print("RENDER wrote index %s (%d clips)" % (idx, len(rows)))


main()
