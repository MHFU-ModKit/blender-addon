"""Play a BUILT MHFU PAC's own in-game animation and render it — offline.

The gap this closes: `render_check.py` renders the BIND POSE, and the bind pose is
identical between a good port and a broken one — the animation sub is where ports
go wrong. So a port could only ever be judged by booting the emulator, walking to
the monster and watching. This plays the clips straight out of the PAC that will
be injected.

`anim_ingame.to_flat_anim` recombines the engine's per-stream bone partitions
(Tigrex: 31 + 9 + 5, and the skeleton confirms the split at `bone+0x50`) back into
whole-rig clips.

⚠️ **NOT an oracle yet — but the "cluster of parts floating beside it" now has a
named cause (2026-08-27).** `importer._build_meshes` clamped every mesh group whose
draw index exceeded the bone count onto the LAST bone
(`bidx = min(g.index, n_bones - 1)`). `file_06185` has **214 groups against 48 bones**,
so **166 of them** were welded to one bone — that is the floating cluster, and it is
not the FK. The clamp is fixed for MHP3rd (per-record bind index), but MHFU PACs still
parse with `mesh_record = -1`, so the fix does not reach them: they need the `vg_rec`
breadcrumb, which is where to start.

Ruled out along the way: the loc channels (only the hip carries a full triple; that is
root motion), the anim-track → bone mapping (`bone+0x50` says 0..30 / 31..39 / 40..44,
matching the concatenation), pose-relative rotation (fixed below — that is what made it
explode outright, and `fk_bake.py` now bakes the same maths into Actions so every
renderer gets it), and euler ORDER (all six give near-identical results).

    ./blender_mhfu/blender-docker.sh --background \
        --python blender_mhfu/render_ported_anim.py -- \
        tmp/brute_tigrex_v67_hostslots.bin /tmp/v67 51 7 53

Writes <outdir>/slot_<NNN>_f<frame>.png — a strip of poses per slot. With no slot
list it renders a default spread of recognisable moves. Env: MHFU_ANIM_SHOTS
(poses per clip, default 4), MHFU_ANIM_RES ("960x720"), MHFU_RENDER_ENGINE
(default CYCLES — the only headless-safe one), MHFU_CYCLES_SAMPLES (24).
"""
import os
import sys

import bpy
from mathutils import Euler, Matrix, Vector

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "tools")))
import importer  # noqa: E402
from mhfu_model import anim_ingame as AI  # noqa: E402
from mhfu_model import convert as C  # noqa: E402
from mhfu_model import load_pac  # noqa: E402
from mhfu_model.pac import MonsterPac  # noqa: E402

# engine (Y-up) -> Blender (Z-up), as a matrix, so it can conjugate a rotation
ENG2BL = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))

_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PAC = _argv[0]
OUTDIR = _argv[1] if len(_argv) > 1 else "/tmp/ported_anim"
# 51 = "spin to win", 7 = charge forward, 48 = bite forward, 53 = roar
SLOTS = [int(a) for a in _argv[2:]] or [7, 48, 51, 53]

SHOTS = int(os.environ.get("MHFU_ANIM_SHOTS", "4"))
_res = os.environ.get("MHFU_ANIM_RES", "960x720").split("x")
RES = (int(_res[0]), int(_res[1]))


def _clips(path):
    """slot -> whole-rig Animation, straight out of the PAC's anim sub."""
    ig = AI.parse_ingame(MonsterPac.from_bytes(open(path, "rb").read()).subs[3].data)
    return {a.slot: a for a in AI.to_flat_anim(ig).animations}


def _sample(anim):
    """joint -> {kind: {axis: [(frame, value), ...]}}, sorted, ready to interpolate."""
    out = {}
    for j, tr in enumerate(anim.tracks):
        per = out.setdefault(j, {"rot": {}, "loc": {}})
        for ch in tr.channels:
            kind, axis = C.channel_kind(ch.type)
            if kind in ("rot", "loc"):
                per[kind][axis] = sorted(
                    (kf.frame, C.dequantize(kind, kf.value)) for kf in ch.keyframes)
    return out


def _at(pts, frame, default=0.0):
    """Linear sample of a keyframe list. Ease in/out are ignored — this is a
    visual check, not a playback engine."""
    if not pts:
        return default
    if frame <= pts[0][0]:
        return pts[0][1]
    if frame >= pts[-1][0]:
        return pts[-1][1]
    for (f0, v0), (f1, v1) in zip(pts, pts[1:]):
        if f0 <= frame <= f1:
            t = 0.0 if f1 == f0 else (frame - f0) / (f1 - f0)
            return v0 + (v1 - v0) * t
    return pts[-1][1]


def _engine_world(skel, samples, frame, order):
    """joint -> 4x4 world matrix in ENGINE space.

    🔴 This is why keying `pose_bone.rotation_euler` does not work: the engine's
    channels are a joint's **absolute local** rotation (skeleton `bind_rot` is all
    zeros, so rest orientation is identity), while a Blender pose rotation is
    relative to the bone's REST orientation, which points head→tail and is not
    identity. Applying one as the other is off by the rest rotation on every
    joint — which is exactly what made even the NATIVE clip explode.
    """
    by_index = {b.index: b for b in skel.bones}
    world = {}

    def resolve(i):
        if i in world:
            return world[i]
        b = by_index[i]
        s = samples.get(i, {"rot": {}, "loc": {}})
        rot = Euler([_at(s["rot"].get(a, []), frame) for a in (0, 1, 2)], order)
        loc = [_at(s["loc"].get(a, []), frame, b.bind_pos[a]) for a in (0, 1, 2)]
        local = Matrix.Translation(loc) @ rot.to_matrix().to_4x4()
        parent = (Matrix.Identity(4) if b.parent not in by_index
                  else resolve(b.parent))
        world[i] = parent @ local
        return world[i]

    for b in skel.bones:
        resolve(b.index)
    return world


def _pose(arm, skel, samples, frame, order):
    """Drive the armature from engine-space world matrices.

    Blender deforms by ``pose.matrix @ bone.matrix_local.inverted()``, so to get
    the engine's deform (anim_world @ bind_world⁻¹) the pose matrix has to be
    ``A @ bind⁻¹ @ matrix_local`` — never the raw animated matrix.
    """
    world = _engine_world(skel, samples, frame, order)
    heads = C.bone_world_positions(skel)
    for b in skel.bones:
        pb = arm.pose.bones.get("bone_%d" % b.index)
        if pb is None:
            continue
        a_bl = ENG2BL @ world[b.index] @ ENG2BL.inverted()
        bind_bl = Matrix.Translation(importer.conv(*heads[b.index]))
        pb.matrix = a_bl @ bind_bl.inverted() @ pb.bone.matrix_local
        bpy.context.view_layer.update()          # parents must settle before children


def _scene(arm):
    meshes = [o for o in arm.children if o.type == "MESH"]
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for o in meshes:
        for v in o.data.vertices:
            w = o.matrix_world @ v.co
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    center, size = (lo + hi) * 0.5, ((hi - lo).length or 1.0)

    cd = bpy.data.cameras.new("cam")
    cd.clip_start, cd.clip_end = size * 0.01, size * 20.0
    cam = bpy.data.objects.new("cam", cd)
    bpy.context.scene.collection.objects.link(cam)
    # ⚠️ frame generously: a clip's root motion carries the body well outside the
    # bind-pose bbox, and a tight camera silently crops the very thing being checked
    cam.location = center + Vector((0.9, -1.0, 0.35)).normalized() * size * 2.4
    cam.rotation_euler = (center - cam.location).normalized() \
        .to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    key = (center - cam.location).normalized()
    for ang, energy in ((key, 6.0), (Vector((-0.5, 0.6, 0.6)).normalized(), 2.0)):
        ld = bpy.data.lights.new("sun", "SUN")
        ld.energy = energy
        lo_ = bpy.data.objects.new("sun", ld)
        bpy.context.scene.collection.objects.link(lo_)
        lo_.rotation_euler = ang.to_track_quat("-Z", "Y").to_euler()

    w = bpy.data.worlds.new("w")
    w.use_nodes = True
    w.node_tree.nodes["Background"].inputs[0].default_value = (0.05, 0.05, 0.06, 1)
    w.node_tree.nodes["Background"].inputs[1].default_value = 0.6
    bpy.context.scene.world = w

    # EEVEE and Workbench need a GL context. That used to mean CYCLES-on-CPU was the
    # only option here, but `mhfu-blender:xvfb` (blender_mhfu/docker/) supplies a
    # virtual display, so `MHFU_RENDER_ENGINE=BLENDER_WORKBENCH` now works and is
    # ~100x faster. CYCLES stays the default only because this script renders a
    # handful of stills, where the difference does not matter.
    scn = bpy.context.scene
    engine = os.environ.get("MHFU_RENDER_ENGINE", "CYCLES")
    scn.render.engine = engine
    if engine == "CYCLES":
        scn.cycles.samples = int(os.environ.get("MHFU_CYCLES_SAMPLES", "24"))
        scn.cycles.use_denoising = True
    scn.render.resolution_x, scn.render.resolution_y = RES


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)

    clips = _clips(PAC)
    skel = load_pac(PAC).skeleton
    arm = importer.import_pac(PAC, import_anims=False)
    _scene(arm)
    order = os.environ.get("MHFU_ROT_MODE", "XYZ")
    for pb in arm.pose.bones:
        pb.rotation_mode = order

    for slot in SLOTS:
        anim = clips.get(slot)
        if anim is None:
            print("[anim] slot %d: EMPTY in this PAC" % slot)
            continue
        samples = _sample(anim)
        last = max((f for j in samples.values() for k in j.values()
                    for pts in k.values() for f, _v in pts), default=1)
        print("[anim] slot %3d: %d joints, %d frames, loop=%d, order=%s"
              % (slot, len(anim.tracks), last, anim.loop, order))
        for i in range(SHOTS):
            f = int(round(last * i / max(SHOTS - 1, 1)))
            _pose(arm, skel, samples, f, order)
            bpy.context.scene.render.filepath = os.path.join(
                OUTDIR, "slot_%03d_f%03d.png" % (slot, f))
            bpy.ops.render.render(write_still=True)
    print("[anim] wrote %s" % OUTDIR)


if __name__ == "__main__":
    main()
