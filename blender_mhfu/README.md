# blender_mhfu — MHFU big-monster import/export (Phases 1–3)

Imports a Monster Hunter Freedom Unite big-monster model PAC (`file_0XXXX.bin`,
e.g. Tigrex = `file_06134`) into Blender as an armature + rigid-skinned meshes +
animations, and **exports edits back to an engine-valid PAC** through the
constraint validator. Thin `bpy` glue over the `mhfu_model` library (all
format/math/validation).

## Install

```bash
./build_addon.sh          # vendors mhfu_model + produces blender_mhfu.zip
```
Blender → Edit > Preferences > Add-ons > Install… → pick `blender_mhfu.zip` →
enable. Then **File > Import > MHFU Monster (.bin)**.

Dev (no packaging): the addon also finds `mhfu_model` in the repo's `tools/` dir
when this folder lives at `<repo>/blender_mhfu/`.

## What it builds (import)

- **Armature** from the skeleton bind-pose (PAC sub-0), bones parented by the
  index tree; engine Y-up converted to Blender Z-up.
- **One mesh object per PMO vertex group** (the rigid per-bone unit), each
  full-weighted to its bone via an Armature modifier (so animations pose it).
- **One Action per animation** (PAC sub-3): euler/location/scale f-curves,
  dequantized (rot×90/4096, loc/16, scl/256).

The source path is stashed on the armature (`mhfu_source_pac`) so export can apply
only your edits onto a fresh load — untouched sub-resources stay byte-identical.

## Validate + export (Phases 2–3)

- **View3D sidebar → MHFU → MHFU Compatibility**: `Validate` runs the constraint
  validator on the active monster and lists errors/warnings.
- **File > Export > MHFU Monster (.bin)** (or the panel's Export button): rebuilds
  the data model from the scene (Action f-curves → animation channels; edit-bone
  heads → bind-pose offsets), **runs the validator and blocks on any error**, then
  repacks. An unedited import→export reproduces the source file byte-for-byte; an
  edit changes only the affected words (or de-aliases a shared clip).

## Known limitations

- **Geometry edits not exported yet** — the exporter reloads source geometry
  untouched (so it can never write invalid geometry); animation + skeleton bind-pose
  edits are the supported write-back. Editable PMO GE emission is the Phase-3 stretch.
- **Textures deferred** — UVs are imported but TMH→image is a follow-up.
- **Ease tangents** on keyframes aren't mapped to Blender handles yet (the engine
  uses a cubic `spline()` with ease-in/out; treated as plain keyframes for now).
- Rotation order / root-motion scale may need a per-species tweak
  (`ANIM_ROT_MODE` in `importer.py`); verify visually.
- Small monsters (em01-class, no skeleton sub-resource) are out of scope.

## Verified

Headless (`blender --background --python`): `file_06134` → 24-bone armature, 28 mesh
objects (729 verts), 22 animations. **Import→export round-trip: unedited output is
byte-identical to the source (149504 B); an edited f-curve re-decodes cleanly with
the texture + skeleton subs byte-identical.** Library math/encoders/validator are
unit-tested in `tools/mhfu_model/tests/`.
