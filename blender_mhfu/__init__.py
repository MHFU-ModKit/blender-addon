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
    "name": "MHFU Monster Importer",
    "author": "MHFU RE project",
    "version": (0, 1, 0),
    "blender": (3, 0, 0),
    "location": "File > Import > MHFU Monster (.bin)",
    "description": "Import MHFU big-monster model PAC (mesh + skeleton + animation)",
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
from bpy_extras.io_utils import ImportHelper  # noqa: E402


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


def _menu(self, context):
    self.layout.operator(IMPORT_OT_mhfu_monster.bl_idname, text="MHFU Monster (.bin)")


def register():
    bpy.utils.register_class(IMPORT_OT_mhfu_monster)
    bpy.types.TOPBAR_MT_file_import.append(_menu)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu)
    bpy.utils.unregister_class(IMPORT_OT_mhfu_monster)


if __name__ == "__main__":
    register()
