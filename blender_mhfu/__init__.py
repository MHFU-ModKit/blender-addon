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
from bpy.props import BoolProperty, StringProperty  # noqa: E402
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


_CLASSES = (IMPORT_OT_mhfu_monster, EXPORT_OT_mhfu_monster,
            VALIDATE_OT_mhfu_monster, VIEW3D_PT_mhfu_compat)


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
