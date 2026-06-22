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


def _uv_by_vertex(mesh):
    """Per-vertex UV (first loop's UV for each vertex) from the active UV layer."""
    uvl = mesh.uv_layers.active
    if uvl is None:
        return {}
    out = {}
    for poly in mesh.polygons:
        for li in range(poly.loop_start, poly.loop_start + poly.loop_total):
            vi = mesh.loops[li].vertex_index
            if vi not in out:
                uv = uvl.data[li].uv
                out[vi] = (uv.x, uv.y)
    return out


def _apply_meshes(arm_obj, mm):
    """Write edited mesh-object vertex positions back into the model's groups, and
    collect any ADDED geometry for the topology pass.

    Each imported mesh object is `<name>_grpNN` (NN = draw order) and parented to the
    armature; its first len(group) vertices keep the group's order. A MOVE of an
    existing vertex updates the group position (engine units, via conv_inv) and flags
    `model.edited`. EXTRA vertices beyond the source count are NEW geometry: their
    positions / normals / UVs are read from Blender and stashed on
    `model.additions` (keyed by the group's source vgroup index `vg_rec`), applied
    after repack via pmo_topology. Removing vertices is still rejected."""
    if not mm.model:
        return 0
    arm_inv = arm_obj.matrix_world.inverted_safe()   # bake object transforms too
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
        n_old = len(g.vertices)
        if len(mv) < n_old:
            raise RuntimeError(
                "mesh '%s' has %d vertices but the source group has %d — REMOVING "
                "geometry isn't supported; keep >= the original count"
                % (obj.name, len(mv), n_old))
        xf = arm_inv @ obj.matrix_world          # vertex -> armature(import) frame:
        nf = xf.to_3x3()                         # normals: rotation/scale part only
        for i in range(n_old):                   # captures Object-Mode transforms too
            ex, ey, ez = conv_inv(xf @ mv[i].co)
            v = g.vertices[i]
            if v is None:
                continue
            if (round(v["x"], 3), round(v["y"], 3), round(v["z"], 3)) != \
                    (round(ex, 3), round(ey, 3), round(ez, 3)):
                v["x"], v["y"], v["z"] = ex, ey, ez
                edited += 1
        if len(mv) == n_old:
            continue
        # --- ADDED geometry: extra verts [n_old, len(mv)) + the faces touching them
        mesh = obj.data
        uvmap = _uv_by_vertex(mesh)
        new_verts = []
        for i in range(n_old, len(mv)):
            ex, ey, ez = conv_inv(xf @ mv[i].co)
            nx, ny, nz = conv_inv(nf @ mv[i].normal)
            bu, bv = uvmap.get(i, (0.0, 0.0))    # un-flip V (importer stored 1.0 - v)
            new_verts.append({"x": ex, "y": ey, "z": ez,
                              "i": nx, "j": ny, "k": nz, "u": bu, "v": 1.0 - bv})
        mesh.calc_loop_triangles()
        tris = []
        for lt in mesh.loop_triangles:
            vi = tuple(lt.vertices)
            if any(x >= n_old for x in vi):      # only faces that touch new verts
                tris.append(vi)
        if not tris:
            raise RuntimeError(
                "mesh '%s' has %d extra vertices but no faces using them — add faces "
                "to the new geometry" % (obj.name, len(mv) - n_old))
        mm.model.additions.append({"vg_rec": g.vg_rec, "verts": new_verts,
                                   "tris": tris})
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


def _apply_additions(pac_bytes, model):
    """Apply any ADDED geometry (model.additions) to a repacked PAC via the topology
    encoder, returning a (bigger) PAC. No-op when there are no additions, so the
    same-size reshape path is byte-for-byte unchanged."""
    if not getattr(model, "additions", None):
        return pac_bytes
    from mhfu_model import pac as _pac
    from mhfu_model import pmo_topology as topo
    P = _pac.MonsterPac.from_bytes(pac_bytes)
    sub = next((s for s in P.subs if s.magic == b"pmo\x00"), None)
    if sub is None:
        raise RuntimeError("no PMO sub-resource to grow")
    header, groups = topo.parse(sub.data)
    scale = header[2:5]
    for add in model.additions:
        g = groups[add["vg_rec"]]
        topo.grow_group_explicit(g, add["verts"], add["tris"], scale=scale,
                                 weight_slot=add.get("weight_slot", 0))
    grown = topo.serialize(sub.data, header, groups)
    P.subs[sub.index] = _pac.SubResource(sub.index, grown)
    return P.to_bytes()


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
    data = _apply_additions(repack(mm), mm.model)
    with open(filepath, "wb") as f:
        f.write(data)
    return rep


def export_ingame_bindpose_pac(arm_obj, filepath, split=None):
    """Export a big-monster PAC with a real MHFU **in-game** bind-pose animation.

    This is the path that produces a `.bin` the MHFU engine's per-frame animation
    walker accepts (the flat lobby anim written by `export_pac` is the wrong format
    for in-game playback — see docs/ANIMATION_FORMAT.md). It re-encodes the
    skeleton + model from the scene, carries textures + secondary subs through
    verbatim, and replaces the animation sub with a recursive 3-stream bind-pose
    (`anim_ingame.swap_anim_to_bindpose`) sized to the skeleton's animated-bone
    count. Every animated bone falls back to the skeleton bind transform, so the
    monster renders static in its rest pose — the first milestone toward authored
    in-game motion. The anim sub is padded to the source size, so the PAC stays the
    same total size as the source (the proven same-size in-place inject path).

    `split` optionally sets the 3-stream bone partition (default mirrors native:
    [remainder, 9, 5]). Returns an info dict (animated count + split).
    """
    from mhfu_model import anim_ingame as AI
    mm = build_model_from_scene(arm_obj)
    # validate geometry/skeleton only; the anim sub is replaced wholesale below.
    rep = K.validate(mm.model, mm.skeleton, None, target_species=None)
    if not rep.ok:
        raise RuntimeError("export blocked — %d constraint error(s):\n%s"
                           % (len(rep.errors), "\n".join(str(r) for r in rep.errors)))
    mm.anim = None                       # leave the source anim sub untouched in repack
    base = repack(mm)                    # skeleton + model re-encoded; anim sub = source bytes
    out, info = AI.swap_anim_to_bindpose(base, split=split, keep_size=True)
    with open(filepath, "wb") as f:
        f.write(out)
    return info


def export_ingame_realmotion_pac(arm_obj, filepath, flat_anim_pac, anim_sub=3,
                                 host_count=None, split=None,
                                 src_skeleton_pac=None, src_skeleton_sub=0,
                                 bone_map=None):
    """Export a big-monster PAC with REAL in-game motion from a flat anim source.

    The real-motion sibling of :func:`export_ingame_bindpose_pac`. It re-encodes the
    skeleton + model from the scene, carries textures + secondary subs through
    verbatim, parses a FLAT (lobby / MHP3rd `anim.py`) animation pack and converts it
    to the recursive 3-stream in-game format via
    :func:`anim_ingame.swap_anim_to_realmotion`.

    ``flat_anim_pac`` is the path to a monster PAC whose sub ``anim_sub`` (default 3)
    holds the flat animation (e.g. the converted MHP3rd Brute anim). ``host_count``
    is the bone count the engine's joint walk uses = the **host overlay slot's** count
    (Tigrex = 45), NOT the injected skeleton's own animated count; pass it for a
    PORTED monster so the anim partition + skeleton declared count + host all agree
    (omitting it falls back to the skeleton's count — only correct for a same-rig
    monster). ``split`` overrides the 3-stream partition (default native-shaped).

    The skeleton sub's animated-count word is auto-synced to ``host_count`` and the
    anim is padded to the source size (same-size in-place inject). Returns the info
    dict from :func:`swap_anim_to_realmotion`.

    CROSS-GAME BONE ALIGNMENT (the generalizable port path): when the source anim's
    skeleton bone order differs from the scene's MHFU skeleton (different bone count,
    order, or leading-static roots), copying tracks 1:1 by index tears the mesh. Pass
    ``src_skeleton_pac`` (the source monster PAC; its sub ``src_skeleton_sub`` holds
    the source 0xC0000000 skeleton) and the exporter builds a ``bone_map`` via
    :func:`bone_match.match_skeleton_objects` (target joint -> source bone, by bind
    position) so each track lands on the joint it actually drives, leaving unmatched
    target joints static. Or pass an explicit ``bone_map`` to override. Omit both for
    the legacy 1:1 path (only correct when source and target share bone order).
    """
    from mhfu_model import anim_ingame as AI
    from mhfu_model import anim as flatmod
    from mhfu_model import bone_match as BM
    from mhfu_model import skeleton as skelmod
    from mhfu_model.pac import MonsterPac
    mm = build_model_from_scene(arm_obj)
    rep = K.validate(mm.model, mm.skeleton, None, target_species=None)
    if not rep.ok:
        raise RuntimeError("export blocked — %d constraint error(s):\n%s"
                           % (len(rep.errors), "\n".join(str(r) for r in rep.errors)))
    mm.anim = None
    base = repack(mm)
    with open(flat_anim_pac, "rb") as f:
        fpac = MonsterPac.from_bytes(f.read())
    flat = flatmod.parse(fpac.subs[anim_sub].data)
    # Build the cross-game bone correspondence if a source skeleton was given.
    if bone_map is None and src_skeleton_pac is not None:
        with open(src_skeleton_pac, "rb") as f:
            spac = MonsterPac.from_bytes(f.read())
        src_skel = skelmod.parse(spac.subs[src_skeleton_sub].data)
        bone_map = BM.match_skeleton_objects(src_skel, mm.skeleton)
    out, info = AI.swap_anim_to_realmotion(base, flat, host_count=host_count,
                                           split=split, keep_size=True,
                                           bone_map=bone_map)
    if bone_map is not None:
        info = dict(info); info["bone_map_matched"] = sum(
            1 for v in bone_map.values() if v is not None)
    with open(filepath, "wb") as f:
        f.write(out)
    return info


def _overlay_geometry(geo_model, scene_model):
    """Copy the scene's edited per-vertex geometry into a SkinModel read from the
    source PMO, matched by vgroup index. Both come from the same source PMO (same
    walk order, same vertex counts for single-mesh monsters), so index alignment
    holds; a vertex-count mismatch on a group is skipped (left as source geometry)
    rather than risking a torn map. No edits -> identity (reproduces the CLI build).
    """
    by_idx = {g.index: g for g in scene_model.mesh_groups}
    for i, vg in enumerate(geo_model.vgroups):
        sg = by_idx.get(i)
        if sg is None or len(sg.vertices) < len(vg.vertices):
            continue
        for vi, sv in enumerate(sg.vertices[:len(vg.vertices)]):
            dv = vg.vertices[vi]
            if dv is None or sv is None:
                continue
            for k in ("x", "y", "z", "u", "v", "i", "j", "k"):
                if k in sv:
                    dv[k] = sv[k]


def export_skinned_monster_pac(arm_obj, filepath, frame_pac=None,
                               nb=3, hops=1, target_species=None):
    """Export the scene as a fully RE-SKINNED MHFU monster PAC (the from-scratch
    monster path; the offline twin is tools/build_brute_pac.py).

    Unlike :func:`export_pac` (which reshapes the source vgroups in place, keeping
    their original bone binding), this DERIVES fresh chain-aware blend skinning
    (:func:`pmo_skin.auto_skin` with the skeleton tree, ``hops``) against a frame
    skeleton, then splices the Brute's own geometry + material tables into the frame
    — same-size in-place, the proven live-inject layout. This is the path that fixes
    a ported monster's binding (e.g. the Brute tail scramble) and the recommended
    output for porting a rigid-piece monster onto an MHFU rig.

    ``frame_pac`` supplies the skeleton (sub 0) the engine actually drives and the
    textures/animation/secondary subs carried through verbatim; it defaults to the
    armature's own source PAC (correct when the scene was imported from a PAC that
    already pairs the target rig with the ported geometry, e.g. a previous Brute
    build). For a cross-rig port, pass the native host PAC (e.g. file_06185).

    Returns the build stats dict. The result fits the frame's PMO slot (raises
    otherwise — lower ``nb`` or use the relocate inject path for a bigger mesh).
    """
    from mhfu_model import pmo_skin as PS
    from mhfu_model.pac import MonsterPac
    src = arm_obj.get("mhfu_source_pac")
    if not src:
        raise RuntimeError("no source PAC on %r — re-import with this addon" % arm_obj.name)
    frame_path = frame_pac or src
    mm = build_model_from_scene(arm_obj)                  # geometry incl. scene edits
    # geometry + the Brute's OWN material/mesh tables come from the source PMO
    spac = MonsterPac.from_bytes(open(src, "rb").read())
    pmo_sub = next((s for s in spac.subs if s.data[:4] == b"pmo\x00"), None)
    if pmo_sub is None:
        raise RuntimeError("source PAC has no PMO sub-resource")
    geo = PS.read(pmo_sub.data)
    _overlay_geometry(geo, mm.model)
    frame = open(frame_path, "rb").read()
    pac, stats = PS.splice_skinned_pmo(frame, geo, nb=nb, hops=hops)
    with open(filepath, "wb") as f:
        f.write(pac)
    return stats


def port_p3rd_monster_pac(model_path, frame_path, filepath, geo_path=None,
                          anim_path=None, nb=3, hops=1):
    """Port an MHP3rd big monster -> injectable MHFU PAC (the generalized port path).

    Wraps :func:`mhfu_model.port_p3rd.port_monster` so the Blender addon's
    "Port P3rd Monster" operator and the CLI (`tools/build_p3rd_port.py`) produce the
    byte-identical `.bin`. Splices the MHP3rd monster's chain-aware-skinned geometry,
    its OWN textures, and its OWN moveset (retargeted to the host rig + in-game encoded)
    onto an MHFU host frame (e.g. the Tigrex `file_06185`).

    ``model_path`` = MHP3rd model+skel+TMH PAC; ``geo_path`` = its GE-list companion
    (model+1) when geometry is external; ``anim_path`` = its raw moveset (model+2);
    ``frame_path`` = the MHFU host PAC. Returns the port info dict.
    """
    from mhfu_model import port_p3rd as PORT
    model = open(model_path, "rb").read()
    frame = open(frame_path, "rb").read()
    geo = open(geo_path, "rb").read() if geo_path else None
    anim = open(anim_path, "rb").read() if anim_path else None
    pac, info = PORT.port_monster(model, frame, geo_companion=geo, anim_blob=anim,
                                  nb=nb, hops=hops)
    with open(filepath, "wb") as f:
        f.write(pac)
    return info


def inject_to_live(arm_obj, target_species=None, inject_dir=None):
    """Phase 4: push the edited monster to the running game (no on-disk edits).

    Validates, repacks, and atomically drops the bytes into the PPSSPP memstick
    inject dir as `file_<id>.bin` PLUS a `.orig` copy of the original source PAC.
    The PRX content-matches the live RAW buffer against `.orig` before overwriting
    it with our bytes (so only the intended species is touched). The fileId is
    parsed from the source PAC name. Returns (report, written_path). Raises on
    validation error.

    NOTE: a quest's monster does NOT always use the file_0{em_id+0x17AB} PAC — the
    fileId is taken from whatever source PAC you imported, so import the file the
    game actually loads for that monster (e.g. the native-Tigrex-quest Tigrex is
    file_06185, not file_06134).
    """
    from mhfu_model import inject as K_inject
    mm = build_model_from_scene(arm_obj)
    rep = K.validate(mm.model, mm.skeleton, mm.anim, target_species=target_species)
    if not rep.ok:
        raise RuntimeError("inject blocked — %d constraint error(s):\n%s"
                           % (len(rep.errors), "\n".join(str(r) for r in rep.errors)))
    src = arm_obj.get("mhfu_source_pac") or ""
    file_id = K_inject.file_id_from_name(src)
    orig = None
    try:
        with open(src, "rb") as f:
            orig = f.read()      # pristine source = the PRX content-match gate
    except OSError:
        pass                     # gate falls back to a prefix match if absent
    data = _apply_additions(repack(mm), mm.model)
    if getattr(mm.model, "additions", None):
        # grown PAC -> relocate path (can't overwrite the fixed raw buffer in place).
        # Drive it with: mhfu.inject_relocate(<engine fid = file_id+1>, grown, orig)
        path = K_inject.write_relocate_bytes(data, file_id, inject_dir, orig=orig)
    else:
        path = K_inject.write_inject_bytes(data, file_id, inject_dir, orig=orig)
    return rep, path
