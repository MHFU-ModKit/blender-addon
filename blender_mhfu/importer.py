"""Blender scene builder for MHFU big-monster PACs (read-only importer, Phase 1).

Thin `bpy` glue over `mhfu_model` (parsing) + `mhfu_model.convert` (math). Builds:
  * an armature from the skeleton bind-pose (bones parented by the index tree),
  * one mesh object per PMO vertex group (the rigid per-bone bind unit),
    each fully weighted to its bone via an Armature modifier,
  * one Blender Action per animation (dequantized euler/loc/scale f-curves).

Coordinate convention: the engine is Y-up; we convert to Blender Z-up with
conv(x,y,z) = (x, -z, y), applied uniformly to bone heads and vertices.

Geometry/skeleton are correct; rotation order / root-motion scale for animations
may need a tweak per species — verify visually and adjust ANIM_ROT_MODE if needed.
"""
from __future__ import annotations

import bpy
from mathutils import Vector

from mhfu_model import load_pac
from mhfu_model import convert as C

ANIM_ROT_MODE = "XYZ"


def conv(x, y, z):
    """Engine (Y-up) -> Blender (Z-up)."""
    return Vector((x, -z, y))


# --------------------------------------------------------------------------- #
def _build_armature(skel, name, coll):
    arm = bpy.data.armatures.new(name + "_arm")
    arm_obj = bpy.data.objects.new(name + "_arm", arm)
    coll.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")

    world = C.bone_world_positions(skel)
    kids = C.children_of(skel)
    by_index = {b.index: b for b in skel.bones}

    ebs = {}
    for b in skel.bones:
        eb = arm.edit_bones.new("bone_%d" % b.index)
        eb.head = conv(*world[b.index])
        ebs[b.index] = eb
    # parent links
    for b in skel.bones:
        if b.parent in ebs:
            ebs[b.index].parent = ebs[b.parent]
    # tails: point at the (mean) child head, else extend along parent direction
    for b in skel.bones:
        eb = ebs[b.index]
        ck = [ebs[c].head for c in kids.get(b.index, []) if c in ebs]
        if ck:
            tail = sum(ck, Vector((0, 0, 0))) / len(ck)
            if (tail - eb.head).length < 1e-4:
                tail = eb.head + Vector((0, 0, 1))
        elif b.parent in ebs:
            d = eb.head - ebs[b.parent].head
            tail = eb.head + (d.normalized() if d.length > 1e-4 else Vector((0, 0, 1)))
        else:
            tail = eb.head + Vector((0, 0, 1))
        eb.tail = tail

    bpy.ops.object.mode_set(mode="OBJECT")
    return arm_obj


def _build_meshes(model, skel, name, coll, arm_obj):
    n_bones = len(skel.bones)
    objs = []
    for g in model.mesh_groups:
        me = bpy.data.meshes.new("%s_grp%02d" % (name, g.index))
        verts = [conv(v["x"], v["y"], v["z"]) for v in g.vertices]
        faces = [(f["v1"], f["v2"], f["v3"]) for f in g.faces]
        me.from_pydata(verts, [], faces)
        me.update()
        # UVs
        if g.vertices and "u" in g.vertices[0]:
            uvl = me.uv_layers.new(name="UV")
            for poly in me.polygons:
                for li in poly.loop_indices:
                    vi = me.loops[li].vertex_index
                    v = g.vertices[vi]
                    uvl.data[li].uv = (v.get("u", 0.0), 1.0 - v.get("v", 0.0))
        obj = bpy.data.objects.new(me.name, me)
        coll.objects.link(obj)
        # rigid skin: full-weight this group to its bone via an Armature modifier
        bidx = g.index if g.index < n_bones else n_bones - 1
        vg = obj.vertex_groups.new(name="bone_%d" % bidx)
        vg.add(range(len(verts)), 1.0, "REPLACE")
        obj.parent = arm_obj
        mod = obj.modifiers.new("Armature", "ARMATURE")
        mod.object = arm_obj
        objs.append(obj)
    return objs


def _build_actions(anims, arm_obj):
    if not anims:
        return 0
    arm_obj.animation_data_create()
    pbones = arm_obj.pose.bones
    for pb in pbones:
        pb.rotation_mode = ANIM_ROT_MODE
    made = 0
    last_frame = 1
    for anim in anims:
        action = bpy.data.actions.new("anim_%03d" % anim.slot)
        for bone_index, kind, axis, pts in C.animation_fcurves(anim):
            bn = "bone_%d" % bone_index
            if bn not in pbones:
                continue
            pb = pbones[bn]
            prop = {"rot": "rotation_euler", "loc": "location", "scl": "scale"}[kind]
            dp = pb.path_from_id(prop)
            fc = action.fcurves.find(dp, index=axis) or action.fcurves.new(dp, index=axis)
            for frame, val in pts:
                fc.keyframe_points.insert(frame, val, options={"FAST"})
                last_frame = max(last_frame, int(frame))
        made += 1
    # leave the first action assigned so the timeline shows motion
    if made:
        arm_obj.animation_data.action = bpy.data.actions["anim_%03d" % anims[0].slot]
        bpy.context.scene.frame_end = max(bpy.context.scene.frame_end, last_frame)
    return made


def import_pac(filepath, import_anims=True):
    """Import a big-monster PAC into the current scene. Returns the armature object."""
    import os
    mm = load_pac(filepath)
    name = os.path.splitext(os.path.basename(filepath))[0]

    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)

    if not mm.skeleton:
        raise RuntimeError("%s has no skeleton sub-resource (small monster?)" % name)

    arm_obj = _build_armature(mm.skeleton, name, coll)
    # Stash the source path so the exporter can re-load it and apply only the
    # edits read from the scene (untouched sub-resources stay byte-identical).
    arm_obj["mhfu_source_pac"] = os.path.abspath(filepath)
    if mm.model:
        _build_meshes(mm.model, mm.skeleton, name, coll, arm_obj)
    n = _build_actions(mm.anim.animations if (import_anims and mm.anim) else [], arm_obj)

    print("[mhfu] imported %s: %d bones, %d mesh groups, %d animations"
          % (name, len(mm.skeleton.bones),
             len(mm.model.mesh_groups) if mm.model else 0, n))
    return arm_obj
