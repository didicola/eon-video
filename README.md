# eon-video — free cloud render lane (war-epic v2)

**war-epic v2** — a 20 s · 30 fps · 1280×720 cinematic render of a 3-act space battle.
Every frame and every note is synthesized from a numeric seed by `render/render.py`
(Pillow + numpy → ffmpeg). No external media, no API keys, no GPU, no client browser.

- Watch: https://didicola.github.io/eon-video/
- Receipt (sha256, frames, host, versions): `out/war-epic-v2.receipt.json`

## How it renders
- Runs on a **free GitHub Actions runner** (`ubuntu-latest`) — the "cloud machine".
- `render/render.py` pipes raw RGB frames straight into ffmpeg → H.264 + AAC MP4
  (`+faststart` for instant browser playback).
- Deterministic: same `--seed` → same bytes; the action commits `out/` and Pages serves it.

## Why this stack (mission: "find-the-live-latest-sci-fi-render-tool")
Surveyed the 2026 programmatic-video landscape — Remotion (React, has documented GH-Actions
rendering), Revideo, Motion Canvas, MoviePy, Diffusion Studio (WebCodecs), ComfyUI/Wan (local
GPU diffusion). **Chosen for this lane: pure Python + Pillow + numpy + ffmpeg** — zero heavy
deps, headless on a stock runner, fully deterministic, $0, and no browser in the loop
(v1's encode ran client-side; v2 renders 100% on cloud CPU).

## Run it
Actions → **eon-render** → Run workflow (inputs: `name` / `seed` / `dur` / `fps`).
Locally: `python3 render/render.py --name test --dur 3`

## v1 → v2
| | v1 (2026-10-17) | v2 (this repo) |
|---|---|---|
| fps | 8 | **30** |
| length | 6 s | **20 s** |
| visuals | abstract GIF-frame stitch | **procedural: nebula, parallax stars, ship light-trails, beams, explosions, aftermath embers** |
| audio | none | **synthesized drone + rumble + sub-booms** |
| compute | browser WebCodecs (client) | **GitHub runner (cloud CPU)** |
