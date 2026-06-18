# blender_mhfu — MHFU big-monster import/export + live inject (Phases 1–4)

Imports a Monster Hunter Freedom Unite big-monster model PAC (`file_0XXXX.bin`)
into Blender as an armature + rigid-skinned meshes + animations, and **exports
edits back to an engine-valid PAC** (or pushes them to the running game) through
the constraint validator. Thin `bpy` glue over the `mhfu_model` library (all
format/math/validation).

> **Import the file the game actually loads for your target monster** — it is NOT
> always `file_0{em_id+0x17AB}`. The native-Tigrex-quest Tigrex's model is
> **`file_06185`**, not `file_06134`/em75 (which that quest never loads). Find the
> real file by linking the live entity to its model buffer (memory
> `phase4-descriptor-table-seam`). **Push to Live** requires the edit to keep the
> **same total file size**, and for a dual-set PAC the **PMO geometry** is the
> editable part.

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

- **Geometry edits = reshape only (same topology).** Moving mesh vertices is carried
  through export (re-encoded in place into the existing vertex buffers); adding or
  removing vertices/faces is rejected with a clear error (a GE-list rebuild — Phase 5).
  The PAC must also stay the **same total size** for live inject (in-place overwrite).
- **Both Edit-Mode vertex moves AND Object-Mode transforms export** — Object-Mode
  move/scale/rotate of a piece is baked relative to the armature; scaling the whole
  armature is ignored (a viewport-fit aid). Edit a piece in either mode.
- **Textures import** — TMH → packed Blender images + Principled materials, UV-mapped
  (view in Material Preview / Rendered). DXT3/5 textures (rare) are skipped.
- **Live inject is racefree (no crashes)** — the overwrite runs synchronously on the game
  thread via a prefix-trampoline on the engine's `get_subresource 0x088B89B0` (the
  pre-transform point where the big-mon loader reads the raw PAC). Proven live 2026-06-18:
  a Blender-edited Tigrex distorts on screen with zero crashes. (The old worker-thread
  overwrite that could collide with the engine's parse is gone.)
- **Ease tangents** on keyframes aren't mapped to Blender handles yet (the engine
  uses a cubic `spline()` with ease-in/out; treated as plain keyframes for now).
- Rotation order / root-motion scale may need a per-species tweak
  (`ANIM_ROT_MODE` in `importer.py`); verify visually.
- Small monsters (em01-class, no skeleton sub-resource) are out of scope.

## Verified

Headless (`blender --background --python`): `file_06134` → 24-bone armature, 28 mesh
objects (729 verts), 22 animations. **Import→export round-trip: unedited output is
byte-identical to the source (149504 B); an edited f-curve re-decodes cleanly with
the texture + skeleton subs byte-identical; a moved mesh vertex re-decodes to the
moved position (+20 in → +20 out) with the texture intact.** Library
math/encoders/validator are unit-tested in `tools/mhfu_model/tests/`.
