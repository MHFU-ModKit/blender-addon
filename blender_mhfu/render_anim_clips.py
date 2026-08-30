"""Render one MP4 per ported MHP3rd animation clip — a moveset catalog for labeling.

Imports an MHP3rd monster PAC (model file_N + GE file_N+1 + moveset file_N+2; N from
MHFU_CLIP_MON, default 5248 = the Brute. Zinogre = 5339)
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
from mhfu_model import p3rd_anim_map as _p3am  # noqa: E402

_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUTDIR = _argv[0] if _argv else "/tmp/brute_anim_clips"
ONLY = int(_argv[1]) if len(_argv) > 1 else None
# PNG stills per clip for the contact sheet; 0 disables.
SHEET = int(os.environ.get("MHFU_CLIP_SHEET", "4"))
TRAVEL: dict = {}                      # slot -> (net, peak) root displacement

_ROOT = os.path.abspath(os.path.join(_HERE, "..", "workspace", "extracted_mhp3", "data_files"))
# Monster PACs run in 5-file groups, so model N => geometry N+1, moveset N+2.
# MHFU_CLIP_MON is that base id; the Brute (05248) stays the default so every
# existing invocation is unchanged. Zinogre = 5339.
_MON = int(os.environ.get("MHFU_CLIP_MON", "5248"))
MODEL = os.environ.get("MHFU_CLIP_MODEL") or os.path.join(_ROOT, "file_%05d.bin" % _MON)
GEO = os.environ.get("MHFU_CLIP_GEO") or os.path.join(_ROOT, "file_%05d.bin" % (_MON + 1))
ANIM = os.environ.get("MHFU_CLIP_ANIM") or os.path.join(_ROOT, "file_%05d.bin" % (_MON + 2))

# 🔴 STILLS and VIDEO are sized separately, because they are ~94% / ~6% of the cost
# and only one of them is read closely. A clip is ~200 video frames against 12
# stills, so the video sets the wall-clock while the CONTACT SHEET — built from the
# stills — is what a labelling pass actually reads. Rendering both at sheet
# resolution turned a 30-minute job into a 3-hour one for no legibility gained.
# MHFU_CLIP_RES sizes the stills; MHFU_CLIP_VIDEO_RES sizes the mp4 (default: same).
_res = os.environ.get("MHFU_CLIP_RES", "640x480").split("x")
RES = (int(_res[0]), int(_res[1]))
_vres = os.environ.get("MHFU_CLIP_VIDEO_RES", "").split("x")
VRES = (int(_vres[0]), int(_vres[1])) if len(_vres) == 2 else RES
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


def root_travel(skel, anim, b2r):
    """(net, peak) distance the ROOT joint travels over the clip, in engine units.

    `strip_root_motion` deletes exactly this so the clip plays in place and stays in
    frame — but locomotion is half of what a move IS. A forward lunge and a standing
    bite look alike once the travel is gone, so measure it before removing it and put
    it in the index: 0 = in place, ~1000 = about one body length.
    """
    s = fk_bake.sample(anim, b2r)
    n = len(skel.bones)
    roots = [b.index for b in skel.bones if not (0 <= b.parent < n)]
    frames = fk_bake.key_frames(s)
    path = []
    for f in frames:
        v = Vector((0, 0, 0))
        for r in roots:
            per = s.get(r)
            if not per or not per["loc"]:
                continue
            b = next(x for x in skel.bones if x.index == r)
            v += Vector([fk_bake.at(per["loc"].get(a, []), f, b.bind_pos[a])
                         for a in (0, 1, 2)])
        path.append(v)
    if not path:
        return 0.0, 0.0
    net = (path[-1] - path[0]).length
    peak = max((v - path[0]).length for v in path)
    return net, peak


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


def bind_bounds(meshes):
    """World bbox of the meshes at BIND pose."""
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for o in meshes:
        for v in o.data.vertices:
            w = o.matrix_world @ v.co
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return lo, hi


def moveset_bounds(arm, meshes, acts, per_clip=5):
    """World bbox of the DEFORMED mesh sampled across the WHOLE moveset.

    🔴 Framing on the bind pose is not good enough. The camera is fixed for the
    whole render, and a clip that rears, lunges or hops puts the monster outside a
    bind-pose frame — it renders CROPPED, and a cropped frame is unlabelable in a
    way `moveset_sheet.union_bbox` cannot undo (it crops, it cannot un-clip). Since
    the frame is shared, the bbox has to be too: sample every clip, union the lot.
    """
    dg = bpy.context.evaluated_depsgraph_get()
    scn = bpy.context.scene
    lo, hi = bind_bounds(meshes)
    prev = arm.animation_data.action if arm.animation_data else None
    for a in acts:
        arm.animation_data.action = a
        f0, f1 = a.frame_range
        for k in range(per_clip):
            scn.frame_set(int(round(f0 + (f1 - f0) * k / max(1, per_clip - 1))))
            dg.update()
            for o in meshes:
                oe = o.evaluated_get(dg)
                mw = oe.matrix_world
                for c in oe.bound_box:
                    w = mw @ Vector(c)
                    lo = Vector(map(min, lo, w))
                    hi = Vector(map(max, hi, w))
    if arm.animation_data:
        arm.animation_data.action = prev
    return lo, hi


CAMS: list = []                        # [(still-prefix, camera object)]


def setup_camera(lo, hi):
    center = (lo + hi) * 0.5
    size = (hi - lo).length or 1.0

    cam_data = bpy.data.cameras.new("cam")
    cam_data.clip_start = size * 0.01
    cam_data.clip_end = size * 40.0
    cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    # 3/4 front. `size` is the diagonal of the bbox the caller passed, so 1.05x
    # already clears the whole moveset — the old 1.7x was slack bought to survive a
    # bind-pose bbox that under-reported the animated extent.
    cam.location = center + Vector((0.85, -1.0, 0.40)).normalized() * size * 1.05
    cam.rotation_euler = (center - cam.location).normalized().to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    CAMS.append(("still", cam))

    # ⚠️ A SECOND, SIDE camera. These monsters are long, and a 3/4-front view
    # foreshortens the whole body onto a few pixels — a tail sweep and a body slam
    # look alike from there, which is precisely the distinction a labelling pass has
    # to make. One extra still costs ~0.2 s because the scene is already built.
    side_data = bpy.data.cameras.new("cam_side")
    side_data.clip_start, side_data.clip_end = cam_data.clip_start, cam_data.clip_end
    side = bpy.data.objects.new("cam_side", side_data)
    bpy.context.scene.collection.objects.link(side)
    side.location = center + Vector((1.0, 0.0, 0.18)).normalized() * size * 1.05
    side.rotation_euler = (center - side.location).normalized().to_track_quat("-Z", "Y").to_euler()
    CAMS.append(("side", side))

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
    scn.render.resolution_x, scn.render.resolution_y = VRES
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
        scn.render.resolution_x, scn.render.resolution_y = RES
        main_cam = scn.camera
        for prefix, cam in (CAMS or [("still", main_cam)]):
            scn.camera = cam
            for k in range(SHEET):
                scn.frame_set(lo + int(round(span * k / max(1, SHEET - 1))))
                scn.render.filepath = os.path.join(
                    outdir, "%s_%03d_%d" % (prefix, slot, k))
                bpy.ops.render.render(write_still=True)
        scn.camera = main_cam
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

    # 🔴 DEFAULT = "native": keep the weights the IMPORTER resolved, which since
    # `pmo_p3rd.parse` / `pmo._attach_influences` are the PMO's OWN bone palette.
    # Every other mode here THROWS THOSE AWAY and re-guesses, and a guess is the
    # bottom rung of the skinning ladder (transfer > source weights > auto_skin).
    # That mattered invisibly until 2026-08-30: the rest pose is clean under ANY
    # skinning — every bone is at bind — so the guess only shows once a clip plays.
    # On the Zinogre (181 groups / 51 bones) `auto_skin` collapsed him into an
    # unreadable ball in every frame of every clip, which reads exactly like "the
    # port is still deformed" and is nothing of the kind: his FK is sane and the
    # built PAC renders clean under render_port_views. `auto` was the right default
    # only while the importer's own skin was the 166-of-214 weld it no longer is.
    skin = os.environ.get("MHFU_CLIP_SKIN", "native")
    if skin == "blend":
        blend_skin(arm, meshes)
    elif skin == "rigid":
        rebind_nearest_bone(arm, meshes)
    elif skin == "auto":
        mm = load_pac_p3rd(MODEL, geo_path=GEO, anim_path=ANIM)
        proper_skin(arm, meshes, mm)
    else:                                   # native: the file's own palette
        nvg = sum(len(o.vertex_groups) for o in meshes)
        print("[clips] skin=native: keeping the PMO's own palette (%d vertex groups "
              "over %d objects)" % (nvg, len(meshes)))
    # 🔴 REBAKE THE ACTIONS. The importer keys `pose_bone.rotation_euler` from the
    # engine's channels, which are a different quantity (see fk_bake) — the result
    # bends every extremity away while the rest pose is clean. MHFU_CLIP_RAW=1
    # keeps the importer's version for comparison.
    mm_full = load_pac_p3rd(MODEL, geo_path=GEO, anim_path=ANIM)
    if not os.environ.get("MHFU_CLIP_RAW") and mm_full.anim and mm_full.skeleton:
        want = mm_full.anim.animations
        if ONLY is not None:
            want = [a for a in want if a.slot == ONLY]
        # 🔴 An MHP3rd record does NOT drive the joint with the same index. Without
        # this map the moveset is read onto the wrong bones: the tail gets nothing
        # and freezes at bind pose while the skin spanning it stretches, and every
        # limb curve lands one joint off. MHFU_CLIP_EM overrides the lookup;
        # MHFU_CLIP_POSITIONAL=1 restores the old (wrong) behaviour for comparison.
        b2r = None
        if not os.environ.get("MHFU_CLIP_POSITIONAL"):
            em = int(os.environ.get("MHFU_CLIP_EM") or _p3am.em_for_model_pac(_MON))
            nrec = max((len(a.tracks) for a in want), default=0)
            nb = len(mm_full.skeleton.bones)
            b2r = _p3am.for_monster(em, nrec, nb)
            r2b = {r: b for b, r in b2r.items()}
            print("[clips] em%03d: %d records -> joints %d..%d of %d (offset=%s)"
                  % (em, nrec, min(r2b.values()), max(r2b.values()), nb,
                     _p3am.BONE_OFFSET.get(em, _p3am.DEFAULT_BONE_OFFSET)))
            b2r = r2b
        fk_bake.bake_all(arm, mm_full.skeleton, want,
                         order=os.environ.get("MHFU_ROT_MODE", "XYZ"),
                         bone_of_record=b2r)
        # 🔴 AFTER the bake, never before. `bake_all` deletes every Action and
        # rebuilds it, so a strip that ran earlier is silently undone. It only
        # SHOWED on a monster whose loc records reach the root: the Zinogre at
        # offset 0 keys bone_0.location, the camera is framed on the bind pose,
        # and the clip walks him out of frame — which reads as "the port is
        # still broken". The Brute at offset 2 keys no root, so the control
        # never caught it.
        TRAVEL.update({a.slot: root_travel(mm_full.skeleton, a, b2r) for a in want})
        strip_root_motion(mm_full)

    scn = bpy.context.scene
    _acts = sorted(bpy.data.actions, key=_slot_of)
    if ONLY is not None:
        _acts = [a for a in _acts if _slot_of(a) == ONLY]
    if _acts and not os.environ.get("MHFU_CLIP_REST"):
        _lo, _hi = moveset_bounds(arm, meshes, _acts)
    else:
        _lo, _hi = bind_bounds(meshes)
    center, size = setup_camera(_lo, _hi)
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

    acts = _acts
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
            w.writerow(["anim_id", "clip_file", "frames", "seconds",
                        "travels_net", "travels_peak", "label", "notes"])
            for slot, nframes, secs in rows:
                net, peak = TRAVEL.get(slot, (0.0, 0.0))
                w.writerow(["anim_%03d" % slot, "anim_%03d.mp4" % slot, nframes, secs,
                            round(net), round(peak), "", ""])
        print("RENDER wrote index %s (%d clips)" % (idx, len(rows)))


main()
