"""Blender scene -> MHFU big-monster PAC (Phase 3 write-back).

Safe-by-construction export: instead of reconstructing a whole PAC from the scene
(which would lose every byte we don't model — masks, ease tangents, the secondary
gap table, textures, a second model set), we RE-LOAD the original source PAC and
apply only the edits read back from the scene:

  * animation keyframe values + frames  (Blender Actions -> engine channels)
  * skeleton bind-pose translations      (armature edit-bone heads -> local offsets)

Everything else is carried through byte-identical by the library's encoders, and
the result is run through the constraint validator (export is blocked on errors).

The source path is stashed on the armature by the importer
(`arm_obj["mhfu_source_pac"]`). An unedited import->export reproduces the source
file byte-for-byte (quantize∘dequantize is exact); an edit changes only the
intended words (in-place) or triggers a de-aliasing rebuild (size change).
"""
from __future__ import annotations

import re

import bpy
from mathutils import Vector

from mhfu_model import load_pac, repack
from mhfu_model import convert as C
from mhfu_model import constraints as K

_SLOT_RE = re.compile(r"anim_(\d+)")
_BONE_RE = re.compile(r'bones\["bone_(\d+)"\]')
_PROP_KIND = {"rotation_euler": "rot", "location": "loc", "scale": "scl"}


def conv_inv(v) -> tuple:
    """Inverse of importer.conv: Blender (Z-up) -> engine (Y-up).

    import conv(x,y,z) = (x, -z, y); so engine (x, y, z) = (X, Z, -Y)."""
    return (v.x, v.z, -v.y)


def _channel_for(track, kind, axis):
    for ch in track.channels:
        k, a = C.channel_kind(ch.type)
        if k == kind and a == axis:
            return ch
    return None


def _apply_actions(arm_obj, mm):
    """Write each Action's f-curve points back into the matching anim channels."""
    if not mm.anim:
        return 0
    by_slot = mm.anim.by_slot()
    edited = 0
    for action in bpy.data.actions:
        m = _SLOT_RE.fullmatch(action.name) or _SLOT_RE.match(action.name)
        if not m:
            continue
        slot = int(m.group(1))
        anim = by_slot.get(slot)
        if anim is None:
            continue
        for fc in action.fcurves:
            bm = _BONE_RE.search(fc.data_path)
            if not bm:
                continue
            bone_index = int(bm.group(1))
            prop = fc.data_path.rsplit(".", 1)[-1]
            kind = _PROP_KIND.get(prop)
            if kind is None or bone_index >= len(anim.tracks):
                continue
            ch = _channel_for(anim.tracks[bone_index], kind, fc.array_index)
            if ch is None:
                continue
            pts = sorted(fc.keyframe_points, key=lambda kp: kp.co.x)
            old = list(ch.keyframes)
            new = []
            for i, kp in enumerate(pts):
                frame = int(round(kp.co.x))
                value = C.quantize(kind, kp.co.y)
                ei = old[i].ease_in if i < len(old) else 0
                eo = old[i].ease_out if i < len(old) else 0
                from mhfu_model.model import Keyframe
                new.append(Keyframe(value=value, frame=frame,
                                    ease_in=ei, ease_out=eo))
            if new != old:
                ch.keyframes = new
                edited += 1
    return edited


def _apply_bindpose(arm_obj, mm):
    """Write armature edit-bone head translations back as local bind offsets."""
    if not mm.skeleton:
        return 0
    arm = arm_obj.data
    heads = {}
    # read edit-bone heads (need EDIT mode)
    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    for eb in arm.edit_bones:
        bm = re.fullmatch(r"bone_(\d+)", eb.name)
        if bm:
            heads[int(bm.group(1))] = Vector(eb.head)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = prev_active

    by_index = {b.index: b for b in mm.skeleton.bones}
    edited = 0
    for b in mm.skeleton.bones:
        if b.index not in heads:
            continue
        head = heads[b.index]
        parent_head = heads.get(b.parent, Vector((0, 0, 0))) if b.parent != -1 \
            else Vector((0, 0, 0))
        local = conv_inv(head - parent_head)
        if tuple(round(c, 3) for c in local) != tuple(round(c, 3) for c in b.bind_pos):
            b.bind_pos = local
            edited += 1
    return edited


_GRP_RE = re.compile(r"_grp(\d+)$")


def _apply_meshes(arm_obj, mm):
    """Write edited mesh-object vertex positions back into the model's groups.

    Each imported mesh object is `<name>_grpNN` and parented to the armature; its
    vertex order matches the group's. A vertex MOVE updates the group's position
    (engine units, via conv_inv) and flags `model.edited`. A vertex-COUNT change is
    a topology edit the encoder can't represent yet -> raise a clear error here."""
    if not mm.model:
        return 0
    groups = {g.index: g for g in mm.model.mesh_groups}
    edited = 0
    for obj in arm_obj.children:
        if obj.type != "MESH":
            continue
        m = _GRP_RE.search(obj.name)
        if not m:
            continue
        gi = int(m.group(1))
        g = groups.get(gi)
        if g is None:
            continue
        mv = obj.data.vertices
        if len(mv) != len(g.vertices):
            raise RuntimeError(
                "mesh '%s' has %d vertices but the source group has %d — adding or "
                "removing geometry (topology edits) isn't supported yet; reshape "
                "with the same vertex count" % (obj.name, len(mv), len(g.vertices)))
        for i, bv in enumerate(mv):
            ex, ey, ez = conv_inv(bv.co)
            v = g.vertices[i]
            if v is None:
                continue
            if (round(v["x"], 3), round(v["y"], 3), round(v["z"], 3)) != \
                    (round(ex, 3), round(ey, 3), round(ez, 3)):
                v["x"], v["y"], v["z"] = ex, ey, ez
                edited += 1
    if edited:
        mm.model.edited = True
    return edited


def build_model_from_scene(arm_obj):
    """Re-load the source PAC and apply the scene's edits; return the MonsterModel."""
    src = arm_obj.get("mhfu_source_pac")
    if not src:
        raise RuntimeError(
            "no source PAC recorded on %r — re-import the monster with this addon"
            % arm_obj.name)
    mm = load_pac(src)
    _apply_actions(arm_obj, mm)
    _apply_bindpose(arm_obj, mm)
    _apply_meshes(arm_obj, mm)
    return mm


def validate_scene(arm_obj, target_species=None):
    """Build the edited model and run the constraint validator. Returns a Report."""
    mm = build_model_from_scene(arm_obj)
    return K.validate(mm.model, mm.skeleton, mm.anim, target_species=target_species)


def export_pac(arm_obj, filepath, target_species=None):
    """Validate, then repack the edited monster to `filepath`. Raises on error."""
    mm = build_model_from_scene(arm_obj)
    rep = K.validate(mm.model, mm.skeleton, mm.anim, target_species=target_species)
    if not rep.ok:
        raise RuntimeError("export blocked — %d constraint error(s):\n%s"
                           % (len(rep.errors), "\n".join(str(r) for r in rep.errors)))
    data = repack(mm)
    with open(filepath, "wb") as f:
        f.write(data)
    return rep
