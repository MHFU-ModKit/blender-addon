# blender_mhfu — MHFU big-monster importer (Phase 1, read-only)

Imports a Monster Hunter Freedom Unite big-monster model PAC (`file_0XXXX.bin`,
e.g. Tigrex = `file_06134`) into Blender as an armature + rigid-skinned meshes +
animations. Thin `bpy` glue over the `mhfu_model` library (all format/math).

## Install

```bash
./build_addon.sh          # vendors mhfu_model + produces blender_mhfu.zip
```
Blender → Edit > Preferences > Add-ons > Install… → pick `blender_mhfu.zip` →
enable. Then **File > Import > MHFU Monster (.bin)**.

Dev (no packaging): the addon also finds `mhfu_model` in the repo's `tools/` dir
when this folder lives at `<repo>/blender_mhfu/`.

## What it builds

- **Armature** from the skeleton bind-pose (PAC sub-0), bones parented by the
  index tree; engine Y-up converted to Blender Z-up.
- **One mesh object per PMO vertex group** (the rigid per-bone unit), each
  full-weighted to its bone via an Armature modifier (so animations pose it).
- **One Action per animation** (PAC sub-3): euler/location/scale f-curves,
  dequantized (rot×90/4096, loc/16, scl/256).

## Known limitations (Phase 1)

- **Read-only.** Export/encoders are Phase 3.
- **Textures deferred** — UVs are imported but TMH→image is a follow-up.
- **Ease tangents** on keyframes aren't mapped to Blender handles yet (the engine
  uses a cubic `spline()` with ease-in/out; treated as plain keyframes for now).
- Rotation order / root-motion scale may need a per-species tweak
  (`ANIM_ROT_MODE` in `importer.py`); verify visually.
- Small monsters (em01-class, no skeleton sub-resource) are out of scope.

## Verified

Headless smoke (`blender --background --python`): `file_06134` → 24-bone armature,
28 mesh objects (729 verts), 22 animations. Conversion math is unit-tested in
`tools/mhfu_model/tests/test_convert.py`.
