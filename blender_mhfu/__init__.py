"""MHFU big-monster importer — Blender addon (Phase 1, read-only).

Imports a Monster Hunter Freedom Unite big-monster model PAC
(`file_0XXXX.bin`, e.g. Tigrex = file_06134) as an armature + meshes + animations.

Install: zip this folder (with a bundled `mhfu_model/`, see build_addon.sh) and
install via Blender > Preferences > Add-ons > Install. Then:
  File > Import > MHFU Monster (.bin)

For development without packaging, point Blender's script directory at the repo
or run build_addon.sh to vendor `mhfu_model` in beside this file.
"""
import os
import sys

bl_info = {
    "name": "MHFU Monster Import/Export",
    "author": "MHFU RE project",
    "version": (0, 2, 0),
    "blender": (3, 0, 0),
    "location": "File > Import/Export > MHFU Monster (.bin); View3D > Sidebar > MHFU",
    "description": "Import/export MHFU big-monster PAC (mesh + skeleton + animation) "
                   "with constraint validation",
    "category": "Import-Export",
}

# Make `mhfu_model` importable: prefer a copy vendored next to this file, else
# fall back to the repo's tools/ dir (dev checkout).
_here = os.path.dirname(__file__)
for cand in (_here, os.path.abspath(os.path.join(_here, "..", "tools"))):
    if os.path.isdir(os.path.join(cand, "mhfu_model")) and cand not in sys.path:
        sys.path.insert(0, cand)


def _reload():
    # re-import cleanly on addon reload during development
    import importlib
    import mhfu_model
    from mhfu_model import convert, pac, pmo, skeleton, anim, model
    for m in (model, pac, skeleton, anim, pmo, convert, mhfu_model):
        importlib.reload(m)


import bpy  # noqa: E402
from bpy.props import BoolProperty, IntProperty, StringProperty  # noqa: E402
from bpy_extras.io_utils import ExportHelper, ImportHelper  # noqa: E402


def _active_monster(context):
    """The armature the operators act on: active object or its armature parent."""
    o = context.active_object
    if o is None:
        return None
    if o.type == "ARMATURE" and "mhfu_source_pac" in o:
        return o
    if o.parent and o.parent.type == "ARMATURE" and "mhfu_source_pac" in o.parent:
        return o.parent
    for ob in context.scene.objects:
        if ob.type == "ARMATURE" and "mhfu_source_pac" in ob:
            return ob
    return None


class IMPORT_OT_mhfu_monster(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.mhfu_monster"
    bl_label = "Import MHFU Monster"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".bin"
    filter_glob: StringProperty(default="*.bin", options={"HIDDEN"})
    import_anims: BoolProperty(
        name="Import Animations",
        description="Build a Blender Action per animation slot",
        default=True,
    )

    def execute(self, context):
        try:
            from . import importer
            import importlib
            importlib.reload(importer)
            importer.import_pac(self.filepath, import_anims=self.import_anims)
        except Exception as exc:  # surface a clean error in the UI
            self.report({"ERROR"}, "MHFU import failed: %s" % exc)
            raise
        self.report({"INFO"}, "MHFU monster imported")
        return {"FINISHED"}


class EXPORT_OT_mhfu_monster(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.mhfu_monster"
    bl_label = "Export MHFU Monster"
    bl_options = {"REGISTER"}

    filename_ext = ".bin"
    filter_glob: StringProperty(default="*.bin", options={"HIDDEN"})
    target_species: StringProperty(
        name="Species template",
        description="Validate the skeleton against this registered species template "
                    "(optional)",
        default="",
    )

    def execute(self, context):
        arm = _active_monster(context)
        if arm is None:
            self.report({"ERROR"}, "No imported MHFU monster (armature) in the scene")
            return {"CANCELLED"}
        try:
            from . import exporter
            import importlib
            importlib.reload(exporter)
            rep = exporter.export_pac(arm, self.filepath,
                                      target_species=self.target_species or None)
        except Exception as exc:
            self.report({"ERROR"}, "MHFU export failed: %s" % exc)
            raise
        nwarn = len(rep.warnings)
        self.report({"INFO"}, "MHFU monster exported (%d warning(s))" % nwarn)
        return {"FINISHED"}


class EXPORT_OT_mhfu_monster_ingame(bpy.types.Operator, ExportHelper):
    """Export a big-monster .bin with a real MHFU IN-GAME animation.

    The plain "Export MHFU Monster" writes the flat lobby anim, which the engine's
    per-frame walker rejects in-game. This path writes the recursive 3-stream
    in-game format (anim_ingame): currently a BIND-POSE (the monster stands in its
    skeleton rest pose, no crash) — the validated milestone of the in-game anim
    encoder. Use this to produce a .bin that loads model + bones + textures + a
    working in-game anim together.
    """
    bl_idname = "export_scene.mhfu_monster_ingame"
    bl_label = "Export MHFU Monster (in-game anim)"
    bl_options = {"REGISTER"}

    filename_ext = ".bin"
    filter_glob: StringProperty(default="*.bin", options={"HIDDEN"})

    def execute(self, context):
        arm = _active_monster(context)
        if arm is None:
            self.report({"ERROR"}, "No imported MHFU monster (armature) in the scene")
            return {"CANCELLED"}
        try:
            from . import exporter
            import importlib
            importlib.reload(exporter)
            info = exporter.export_ingame_bindpose_pac(arm, self.filepath)
        except Exception as exc:
            self.report({"ERROR"}, "MHFU in-game export failed: %s" % exc)
            raise
        self.report({"INFO"}, "Exported in-game bind-pose PAC (%d animated bones, "
                    "streams %s)" % (info["animated"], info["split"]))
        return {"FINISHED"}


class EXPORT_OT_mhfu_monster_skinned(bpy.types.Operator, ExportHelper):
    """Export a fully RE-SKINNED big-monster .bin (port path).

    Derives fresh chain-aware blend skinning against the frame skeleton and splices
    the monster's own geometry + materials into the native frame (same-size, the live
    inject layout). This is the path for porting a rigid-piece monster onto an MHFU
    rig — it fixes binding artifacts (e.g. a tail that scrambles under animation) that
    the plain "Export" (in-place reshape) keeps. Offline twin: tools/build_brute_pac.py.
    """
    bl_idname = "export_scene.mhfu_monster_skinned"
    bl_label = "Export MHFU Monster (re-skin)"
    bl_options = {"REGISTER"}

    filename_ext = ".bin"
    filter_glob: StringProperty(default="*.bin", options={"HIDDEN"})
    frame_pac: StringProperty(
        name="Frame PAC",
        description="Native host PAC supplying the skeleton + textures/anim/secondary "
                    "subs (e.g. file_06185.bin). Empty = use the armature's own source PAC",
        default="", subtype="FILE_PATH",
    )
    nb: IntProperty(name="Bones per vertex", default=3, min=1, max=8,
                    description="Blend each vertex to its N nearest bones")
    hops: IntProperty(name="Chain radius", default=1, min=1, max=4,
                      description="Restrict blend bones to this many skeleton edges from "
                                  "the nearest bone (1 = tightest, fixes chain scramble)")

    def execute(self, context):
        arm = _active_monster(context)
        if arm is None:
            self.report({"ERROR"}, "No imported MHFU monster (armature) in the scene")
            return {"CANCELLED"}
        try:
            from . import exporter
            import importlib
            importlib.reload(exporter)
            st = exporter.export_skinned_monster_pac(
                arm, self.filepath, frame_pac=self.frame_pac or None,
                nb=self.nb, hops=self.hops)
        except Exception as exc:
            self.report({"ERROR"}, "MHFU re-skin export failed: %s" % exc)
            raise
        self.report({"INFO"}, "Re-skinned PAC: %d vgroups, %d verts, avg %.1f bones/grp, "
                    "%d/%d B" % (st["vgroups"], st["verts"], st["avg_pal"],
                                 st["pmo_bytes"], st["slot"]))
        return {"FINISHED"}


class PORT_OT_mhfu_p3rd_monster(bpy.types.Operator, ExportHelper):
    """Port an MHP3rd big monster -> injectable MHFU .bin (generalized port pipeline).

    Pick the MHP3rd source files + the MHFU host frame; this splices the monster's own
    geometry (chain-aware skinned onto the host rig), its own textures, and its own
    moveset (retargeted + in-game encoded) into an MHFU PAC. Works for any Tigrex-family
    MHP3rd big monster (see docs/MHP3RD_FILE_MONSTER_MAP.md). Backend shared with the CLI
    so results are reproducible.
    """
    bl_idname = "export_scene.mhfu_p3rd_port"
    bl_label = "Port MHP3rd Monster"
    bl_options = {"REGISTER"}

    filename_ext = ".bin"
    filter_glob: StringProperty(default="*.bin", options={"HIDDEN"})
    model_path: StringProperty(name="MHP3rd model PAC", subtype="FILE_PATH",
                               description="MHP3rd model+skel+TMH PAC (e.g. file_05248)")
    geo_path: StringProperty(name="GE companion", subtype="FILE_PATH",
                             description="MHP3rd GE-list companion (model+1, e.g. file_05249); "
                                         "leave empty if geometry is self-contained")
    anim_path: StringProperty(name="Moveset", subtype="FILE_PATH",
                              description="MHP3rd raw moveset (model+2, e.g. file_05250); "
                                          "empty = keep the host animation")
    frame_path: StringProperty(name="MHFU host frame", subtype="FILE_PATH",
                               description="MHFU host PAC (e.g. file_06185 = Tigrex)")
    nb: IntProperty(name="Bones per vertex", default=3, min=1, max=8)
    hops: IntProperty(name="Chain radius", default=1, min=1, max=4)

    def execute(self, context):
        if not self.model_path or not self.frame_path:
            self.report({"ERROR"}, "Set at least the MHP3rd model PAC + MHFU host frame")
            return {"CANCELLED"}
        try:
            from . import exporter
            import importlib
            importlib.reload(exporter)
            info = exporter.port_p3rd_monster_pac(
                self.model_path, self.frame_path, self.filepath,
                geo_path=self.geo_path or None, anim_path=self.anim_path or None,
                nb=self.nb, hops=self.hops)
        except Exception as exc:
            self.report({"ERROR"}, "MHP3rd port failed: %s" % exc)
            raise
        self.report({"INFO"}, "Ported: %d verts, %d clips, %d/48 bones matched, %d B%s"
                    % (info.get("src_verts", 0), info.get("anim_clips", 0),
                       info.get("bone_map_matched") or 0, info["total"],
                       " (relocate)" if info["needs_relocate"] else ""))
        return {"FINISHED"}


class INJECT_OT_mhfu_monster(bpy.types.Operator):
    """Phase 4: push the edit to the running game (no file dialog, no on-disk edits)."""
    bl_idname = "mhfu.inject_monster"
    bl_label = "Push to Live Game"
    bl_description = ("Validate + repack the active monster and drop it in the PPSSPP "
                      "memstick inject dir; the PRX overwrites the live buffer in place "
                      "(anim updates next frame, skeleton/geom on section re-entry)")

    def execute(self, context):
        arm = _active_monster(context)
        if arm is None:
            self.report({"ERROR"}, "No imported MHFU monster (armature) in the scene")
            return {"CANCELLED"}
        try:
            from . import exporter
            import importlib
            importlib.reload(exporter)
            rep, path = exporter.inject_to_live(arm)
        except Exception as exc:
            self.report({"ERROR"}, "MHFU inject failed: %s" % exc)
            raise
        self.report({"INFO"}, "Pushed to live game: %s (%d warning(s))"
                    % (path, len(rep.warnings)))
        return {"FINISHED"}


class VIEW3D_PT_mhfu_compat(bpy.types.Panel):
    """Sidebar panel: run the constraint validator on the active monster."""
    bl_label = "MHFU Compatibility"
    bl_idname = "VIEW3D_PT_mhfu_compat"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MHFU"

    def draw(self, context):
        layout = self.layout
        arm = _active_monster(context)
        if arm is None:
            layout.label(text="Import an MHFU monster to validate", icon="INFO")
            return
        layout.label(text="Monster: %s" % arm.name)
        layout.operator(VALIDATE_OT_mhfu_monster.bl_idname, icon="CHECKMARK")
        results = context.scene.get("mhfu_validation", None)
        if results is None:
            return
        errors = [r for r in results if r.startswith("[ERROR]")]
        box = layout.box()
        if not results:
            box.label(text="No issues — engine-valid", icon="CHECKMARK")
        else:
            box.label(text="%d error(s), %d warning(s)"
                      % (len(errors), len(results) - len(errors)),
                      icon="ERROR" if errors else "INFO")
            for line in results[:24]:
                ic = "CANCEL" if line.startswith("[ERROR]") else "DOT"
                box.label(text=line[:120], icon=ic)
        layout.operator(EXPORT_OT_mhfu_monster.bl_idname,
                        text="Export PAC (blocked on errors)", icon="EXPORT")
        layout.operator(EXPORT_OT_mhfu_monster_ingame.bl_idname,
                        text="Export PAC (in-game anim)", icon="ARMATURE_DATA")
        layout.operator(EXPORT_OT_mhfu_monster_skinned.bl_idname,
                        text="Export PAC (re-skin / port)", icon="MOD_ARMATURE")
        layout.operator(INJECT_OT_mhfu_monster.bl_idname,
                        text="Push to Live Game (blocked on errors)", icon="PLAY")


class VALIDATE_OT_mhfu_monster(bpy.types.Operator):
    bl_idname = "mhfu.validate_monster"
    bl_label = "Validate"
    bl_description = "Run the MHFU constraint validator on the active monster"

    def execute(self, context):
        arm = _active_monster(context)
        if arm is None:
            self.report({"ERROR"}, "No MHFU monster in the scene")
            return {"CANCELLED"}
        from . import exporter
        import importlib
        importlib.reload(exporter)
        rep = exporter.validate_scene(arm)
        context.scene["mhfu_validation"] = [str(r) for r in rep.results]
        self.report({"INFO"} if rep.ok else {"WARNING"},
                    "Validation: %d error(s), %d warning(s)"
                    % (len(rep.errors), len(rep.warnings)))
        return {"FINISHED"}


def _menu_import(self, context):
    self.layout.operator(IMPORT_OT_mhfu_monster.bl_idname, text="MHFU Monster (.bin)")


def _menu_export(self, context):
    self.layout.operator(EXPORT_OT_mhfu_monster.bl_idname, text="MHFU Monster (.bin)")
    self.layout.operator(EXPORT_OT_mhfu_monster_ingame.bl_idname,
                         text="MHFU Monster — in-game anim (.bin)")
    self.layout.operator(EXPORT_OT_mhfu_monster_skinned.bl_idname,
                         text="MHFU Monster — re-skin / port (.bin)")
    self.layout.operator(PORT_OT_mhfu_p3rd_monster.bl_idname,
                         text="MHP3rd Monster → MHFU port (.bin)")


_CLASSES = (IMPORT_OT_mhfu_monster, EXPORT_OT_mhfu_monster,
            EXPORT_OT_mhfu_monster_ingame, EXPORT_OT_mhfu_monster_skinned,
            PORT_OT_mhfu_p3rd_monster,
            INJECT_OT_mhfu_monster, VALIDATE_OT_mhfu_monster,
            VIEW3D_PT_mhfu_compat)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
