"""Blender scene builder for MHFU + MHP3rd big-monster PACs.

Thin `bpy` glue over `mhfu_model` (parsing) + `mhfu_model.convert` (math). Builds:
  * an armature from the skeleton bind-pose (bones parented by the index tree),
  * one mesh object per PMO vertex group (the rigid per-bone bind unit),
    each fully weighted to its bone via an Armature modifier,
  * one Blender Action per animation (dequantized euler/loc/scale f-curves).

Coordinate convention: the engine is Y-up; we convert to Blender Z-up with
conv(x,y,z) = (x, -z, y), applied uniformly to bone heads and vertices.

Geometry/skeleton are correct; rotation order / root-motion scale for animations
may need a tweak per species — verify visually and adjust ANIM_ROT_MODE if needed.

MHP3rd (gen-3) PACs are detected by 0x80000000 skeleton magic and routed through
pmo_p3rd.parse / skeleton_p3rd.parse automatically.  For in-quest PACs the
companion GE file (file_NNNN+1.bin) is probed automatically; pass `geo_path`
explicitly if the sibling auto-probe fails.  The export path always targets MHFU
1.0 — once imported, the monster can be re-exported and injected into MHFU.
"""
from __future__ import annotations

import os

import bpy
from mathutils import Vector

from mhfu_model import load_pac, load_pac_p3rd
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


def _build_materials(mm, name):
    """Decode the PAC's TMH textures into Blender images + image-textured materials.
    Returns a list indexed by texture index (a mesh group's `material` field). Empty
    if there is no texture sub or it can't be decoded (DXT). Best-effort: never raises."""
    mats = []
    try:
        from mhfu_model.tmh import decode_tmh
        raw = mm.texture.raw if getattr(mm, "texture", None) else None
        imgs = decode_tmh(raw) if raw else []
    except Exception as exc:           # never block geometry import on textures
        print("[mhfu] texture decode skipped: %s" % exc)
        imgs = []
    for tex in imgs:
        w, h, src = tex["width"], tex["height"], tex["rgba"]
        img = bpy.data.images.new("%s_tex%02d" % (name, tex["index"]), width=w, height=h, alpha=True)
        # decoded rows are top-down; Blender image rows are bottom-up -> flip + /255
        inv = 1.0 / 255.0
        px = [0.0] * (w * h * 4)
        rowlen = w * 4
        for r in range(h):
            s = (h - 1 - r) * rowlen
            d = r * rowlen
            row = src[s:s + rowlen]
            for k in range(rowlen):
                px[d + k] = row[k] * inv
        img.pixels.foreach_set(px)
        img.pack()
        mat = bpy.data.materials.new("%s_mat%02d" % (name, tex["index"]))
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = nt.nodes.get("Principled BSDF")
        tnode = nt.nodes.new("ShaderNodeTexImage")
        tnode.image = img
        tnode.interpolation = "Closest"          # crisp PSP-style texels, not blurred
        # Explicitly drive the texture from our "UV" map. Without this, the image
        # node falls back to generated/object coords -> the texture smears across the
        # model's bounding box (the streaky look). The UVMap node resolves by name at
        # render, so it is fine that the meshes/UV layers are built afterwards.
        uvnode = nt.nodes.new("ShaderNodeUVMap")
        uvnode.uv_map = "UV"
        nt.links.new(uvnode.outputs["UV"], tnode.inputs["Vector"])
        if bsdf:
            nt.links.new(tnode.outputs["Color"], bsdf.inputs["Base Color"])
            nt.links.new(tnode.outputs["Alpha"], bsdf.inputs["Alpha"])
        mat.blend_method = "CLIP"
        mats.append(mat)
    return mats


def _build_meshes(model, skel, name, coll, arm_obj, materials=None):
    n_bones = len(skel.bones)
    materials = materials or []
    objs = []
    # position of each group inside its own mesh record — see the bind-index note below
    _local_index, _seen = {}, {}
    for _g in model.mesh_groups:
        k = _seen.get(_g.mesh_record, 0)
        _local_index[(_g.mesh_record, _g.index)] = k
        _seen[_g.mesh_record] = k + 1
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
        # texture material (g.material = texture index into the decoded TMH set)
        if materials:
            mi = g.material if 0 <= g.material < len(materials) else 0
            me.materials.append(materials[mi])
        obj = bpy.data.objects.new(me.name, me)
        coll.objects.link(obj)
        # rigid skin: full-weight this group to its bone via an Armature modifier
        #
        # 🔴 DO NOT CLAMP TO THE LAST BONE. `bidx = min(g.index, n_bones - 1)` welded
        # 43 of the Brute's 88 groups onto bone 45 — which sits in an auxiliary chain
        # no clip animates — so nearly half the model stayed put while the rest moved.
        # Invisible in rest pose, and it reads as "holes in his back" the moment
        # anything plays. Draw order is only the bind index WITHIN a mesh record: no
        # MHP3rd record holds more groups than there are bones (largest is 41 of 46),
        # while the global index overflows for 42 of 88.
        # ⚠️ MHFU PACs come out of the parser with mesh_record = -1 (records are not
        # split there yet), so this changes nothing for them — they still need the
        # `vg_rec` breadcrumb. Verified in range, NOT verified against the game.
        bidx = g.index
        if bidx >= n_bones:
            local = _local_index.get((g.mesh_record, g.index))
            bidx = local if local is not None and local < n_bones else n_bones - 1
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


def _import_mm(mm, filepath, import_anims=True, source_mhfu_pac=None):
    """Shared Blender scene builder for any MonsterModel (MHFU or MHP3rd).

    `source_mhfu_pac`: if set, stashed as `mhfu_source_pac` on the armature so
    the exporter re-loads it for MHFU 1.0 write-back.  For MHFU imports this is
    the filepath itself; for MHP3rd imports it should be the converted MHFU target
    path (or None while that conversion is pending — the exporter will warn).
    """
    name = os.path.splitext(os.path.basename(filepath))[0]

    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)

    if not mm.skeleton:
        raise RuntimeError("%s has no skeleton sub-resource (small monster?)" % name)

    arm_obj = _build_armature(mm.skeleton, name, coll)
    # Stash the source path so the exporter can re-load it and apply only the
    # edits read from the scene (untouched sub-resources stay byte-identical).
    src = source_mhfu_pac or os.path.abspath(filepath)
    arm_obj["mhfu_source_pac"] = src
    # Mark game origin so downstream tools can branch on it.
    is_p3rd = (mm.model is not None and
               getattr(mm.model, "version", None) == b"102\x00")
    arm_obj["mhfu_source_game"] = "p3rd" if is_p3rd else "mhfu"

    materials = _build_materials(mm, name)
    if mm.model:
        _build_meshes(mm.model, mm.skeleton, name, coll, arm_obj, materials)
    n = _build_actions(mm.anim.animations if (import_anims and mm.anim) else [], arm_obj)

    print("[mhfu] imported %s (%s): %d bones, %d mesh groups, %d animations, %d textures"
          % (name, arm_obj["mhfu_source_game"],
             len(mm.skeleton.bones),
             len(mm.model.mesh_groups) if mm.model else 0,
             n, len(materials)))
    return arm_obj


def import_pac(filepath, import_anims=True):
    """Import a big-monster PAC into the current scene. Returns the armature object.

    Auto-detects MHFU (0xC0000000 skeleton) vs MHP3rd (0x80000000 skeleton).
    For MHP3rd in-quest PACs the companion GE file (file_NNNN+1.bin) is probed
    automatically from the same directory — use import_pac_p3rd() to supply it
    explicitly.
    """
    mm = load_pac(filepath)      # auto-detects game; companion probe built into load_pac_p3rd
    return _import_mm(mm, filepath, import_anims=import_anims,
                      source_mhfu_pac=os.path.abspath(filepath))


def import_pac_p3rd(filepath, geo_path=None, anim_path=None, import_anims=True,
                    source_mhfu_pac=None):
    """Import an MHP3rd (gen-3) PAC into the current scene.

    Parameters
    ----------
    filepath:       Path to the MHP3rd model PAC (e.g. file_05148.bin).
    geo_path:       Path to the companion GE file (file_05149.bin for file_05148).
                    If None, the function probes for `file_(N+1).bin` automatically.
    anim_path:      Path to the separate animation file (e.g. file_05252.bin for
                    em023 Black Tigrex, file_05413.bin for em058 Tigrex).
                    Decoded via anim.parse_p3rd(). Optional — geometry imports without it.
    import_anims:   Build Blender Actions from animation data (if anim_path provided).
    source_mhfu_pac: Path to the MHFU 1.0 PAC this MHP3rd monster will be converted
                    into.  Stashed on the armature so the exporter can write back.
                    Leave None if you just want to view the geometry.

    Returns the armature object.
    """
    mm = load_pac_p3rd(filepath, geo_path=geo_path, anim_path=anim_path)
    return _import_mm(mm, filepath, import_anims=import_anims,
                      source_mhfu_pac=source_mhfu_pac)
