"""Bake an engine animation into a Blender Action, CORRECTLY.

🔴 THE BUG THIS EXISTS TO FIX. `importer._build_actions` writes the engine's
rotation channels straight onto `pose_bone.rotation_euler`. Those are not the same
quantity. The engine's channels are a joint's **absolute local rotation** — the
skeleton's `bind_rot` is all zeros, so a joint's rest orientation is identity — while
a Blender pose rotation is relative to the bone's REST orientation, which points
head→tail and is not identity. Applying one as the other is off by the rest rotation
on **every joint**, and because the error rides down the parent chain it compounds:
the root looks fine and the extremities swing away.

That is exactly what a Brute Tigrex rendered from `render_anim_clips.py` shows —
head tilted up, wings splayed up, back arched, tail flung out of frame — while the
same model in REST pose is clean. It is a playback bug in our Blender code, not a
fault in the PAC and not something the game does.

`render_ported_anim.py` worked the correct FK out and applies it by setting
`pose_bone.matrix` per frame, with a `view_layer.update()` after every bone so
parents settle before children. That is right but far too slow to bake 58 clips.
Here the same result is computed analytically: walk the tree top-down and turn each
desired pose matrix into `matrix_basis` directly, which needs no dependency-graph
evaluation at all.

    pb.matrix = pb.parent.matrix @ parent.bone.matrix_local⁻¹
                @ pb.bone.matrix_local @ pb.matrix_basis

so, rearranged, `matrix_basis` follows from the desired pose and the parent's.
"""
import math

import bpy
from mathutils import Matrix

from mhfu_model import convert as C

# Engine is Y-up, Blender is Z-up. Same basis change `importer.conv` applies to
# points, as a matrix so it can conjugate a rotation.
ENG2BL = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def sample(anim, bone_of_record=None):
    """joint -> {kind: {axis: [(frame, value), ...]}}, sorted for interpolation.

    🔴 `bone_of_record` is NOT optional for an MHP3rd SOURCE clip. MHP3rd stores
    ``skeleton_bone = record + bone_offset + skipped_so_far`` — record N does not
    drive joint N — so reading a P3rd moveset positionally puts the thigh's curve
    on the shin and leaves the tail frozen at bind pose. Pass the map from
    `mhfu_model.p3rd_anim_map.for_monster()`. MHFU's own flat clips ARE positional
    (verified on native file_06185: track i == joint i), so None is right for a
    built PAC — and only for a built PAC.
    """
    out = {}
    for j, tr in enumerate(anim.tracks):
        if bone_of_record is not None:
            j = bone_of_record.get(j)
            if j is None:
                continue
        per = out.setdefault(j, {"rot": {}, "loc": {}})
        for ch in tr.channels:
            kind, axis = C.channel_kind(ch.type)
            if kind in ("rot", "loc"):
                per[kind][axis] = sorted(
                    (kf.frame, C.dequantize(kind, kf.value)) for kf in ch.keyframes)
    return out


def key_frames(samples):
    """Every frame at which ANY channel has a keyframe.

    Baking only these instead of every integer frame cuts the work by ~5x. The
    engine interpolates its channels linearly between keys and Blender interpolates
    the baked basis linearly between the same instants, so the two agree exactly AT
    the keys and differ only in how the compounded rotation curves in between —
    invisible in a labelling render.
    """
    fs = {0}
    for per in samples.values():
        for chans in per.values():
            for pts in chans.values():
                fs.update(f for f, _v in pts)
    return sorted(fs)


def at(pts, frame, default=0.0):
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


def engine_world(skel, samples, frame, order="XYZ"):
    """joint -> 4x4 world matrix in ENGINE space."""
    from mathutils import Euler
    by_index = {b.index: b for b in skel.bones}
    world = {}

    def resolve(i):
        if i in world:
            return world[i]
        b = by_index[i]
        s = samples.get(i, {"rot": {}, "loc": {}})
        rot = Euler([at(s["rot"].get(a, []), frame) for a in (0, 1, 2)], order)
        loc = [at(s["loc"].get(a, []), frame, b.bind_pos[a]) for a in (0, 1, 2)]
        local = Matrix.Translation(loc) @ rot.to_matrix().to_4x4()
        parent = (Matrix.Identity(4) if b.parent not in by_index else resolve(b.parent))
        world[i] = parent @ local
        return world[i]

    for b in skel.bones:
        resolve(b.index)
    return world


def _desired_poses(skel, samples, frame, heads, order):
    """joint -> the armature-space matrix the pose bone should have."""
    world = engine_world(skel, samples, frame, order)
    inv = ENG2BL.inverted()
    out = {}
    for b in skel.bones:
        a_bl = ENG2BL @ world[b.index] @ inv
        bind_bl = Matrix.Translation(
            (heads[b.index][0], -heads[b.index][2], heads[b.index][1]))
        out[b.index] = a_bl @ bind_bl.inverted()
    return out


def bake_action(arm, skel, anim, name, heads, order="XYZ", bone_of_record=None):
    """Build one Blender Action for `anim`. Returns it (or None if empty)."""
    samples = sample(anim, bone_of_record)
    frames = key_frames(samples)
    if len(frames) < 2:
        return None
    pbones = arm.pose.bones
    action = bpy.data.actions.new(name)

    # Only bones the clip actually drives need curves; an undriven joint's basis is
    # constant, so keying it only multiplies the file size.
    driven = {j for j, per in samples.items() if per["rot"] or per["loc"]}
    curves = {}

    for frame in frames:
        desired = _desired_poses(skel, samples, frame, heads, order)
        for b in skel.bones:
            i = b.index
            if i not in driven:
                continue
            pb = pbones.get("bone_%d" % i)
            if pb is None or i not in desired:
                continue
            ml = pb.bone.matrix_local
            want = desired[i] @ ml                     # the pose matrix we want
            par = pb.parent
            if par is None:
                basis = ml.inverted() @ want
            else:
                pi = int(par.name.split("_")[1])
                if pi not in desired:
                    basis = ml.inverted() @ want
                else:
                    # parent_pose @ parent.matrix_local⁻¹ collapses to desired[pi],
                    # so no dependency-graph evaluation is needed to get the basis.
                    basis = (desired[pi] @ ml).inverted() @ want
            loc, rot, _scl = basis.decompose()
            eul = rot.to_euler(order)
            for axis in range(3):
                for prop, val in (("location", loc[axis]),
                                  ("rotation_euler", eul[axis])):
                    key = (i, prop, axis)
                    fc = curves.get(key)
                    if fc is None:
                        fc = curves[key] = action.fcurves.new(
                            pb.path_from_id(prop), index=axis)
                    fc.keyframe_points.insert(frame, val, options={"FAST"})
    for fc in action.fcurves:
        fc.update()
    return action


def bake_all(arm, skel, anims, order="XYZ", log=print, bone_of_record=None):
    """Replace every Action on `arm` with a correctly-baked one."""
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)
    heads = C.bone_world_positions(skel)
    for pb in arm.pose.bones:
        pb.rotation_mode = order
    arm.animation_data_create()
    made = 0
    for anim in anims:
        act = bake_action(arm, skel, anim, "anim_%03d" % anim.slot, heads, order,
                          bone_of_record)
        if act is not None:
            made += 1
    log("[fk_bake] baked %d/%d clips (order=%s, record->bone map=%s)"
        % (made, len(anims), order,
           "positional" if bone_of_record is None else "%d records" % len(bone_of_record)))
    return made
