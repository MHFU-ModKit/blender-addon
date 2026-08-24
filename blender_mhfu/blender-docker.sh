#!/usr/bin/env bash
# Run Blender headless in a container, with this repo mounted at its real path.
#
# The server has no Blender (and no sudo to install one), so every headless
# check in this directory — render_check.py, render_anim_clips.py, the
# test_*.py addon-vs-CLI parity tests — goes through here.
#
#   ./blender_mhfu/blender-docker.sh --background --python blender_mhfu/render_check.py \
#       -- tmp/brute_tigrex_v65_slotfix.bin /tmp/v65.png
#
# ⚠️ The repo is mounted at its OWN absolute path, not /work. In a git worktree
# `workspace/` is a symlink into the main checkout, so the main checkout gets
# mounted too, at its own path — otherwise the symlink dangles inside the
# container and every script that reads game data fails with ENOENT.
#
# ⚠️ --user is not optional. Without it Blender writes its output as root and
# the next host-side step dies on PermissionError (the same trap the PRX
# Makefile hit).
set -euo pipefail

IMG="${MHFU_BLENDER_IMG:-nytimes/blender:latest}"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"

# ⚠️ Forward the MHFU_* knobs with explicit values. `docker run -e NAME` (no
# value) silently forwards nothing here, so a `MHFU_ANIM_SHOTS=2 ./blender-docker.sh`
# looked like it worked and rendered the default count instead.
envs=()
for v in MHFU_ANIM_CHANNELS MHFU_ANIM_SHOTS MHFU_ANIM_RES \
         MHFU_RENDER_ENGINE MHFU_CYCLES_SAMPLES MHFU_ROT_MODE MHFU_CLIP_RES MHFU_CLIP_STEP; do
    [ -n "${!v-}" ] && envs+=(-e "$v=${!v}")
done

mounts=(-v "$repo":"$repo")
for link in workspace tmp; do
    tgt="$(readlink -f "$repo/$link" 2>/dev/null || true)"
    case "$tgt" in
        ""|"$repo"/*) ;;                       # absent, or already inside the mount
        *) mounts+=(-v "$tgt":"$tgt") ;;
    esac
done

exec docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    "${envs[@]}" \
    "${mounts[@]}" \
    -w "$repo" \
    "$IMG" blender "$@"
