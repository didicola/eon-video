#!/usr/bin/env python3
# ============================================================================
# EON — war-epic v2  ·  deterministic free-cloud video renderer
# ----------------------------------------------------------------------------
# Rendered on a FREE GitHub Actions runner (ubuntu-latest): Pillow + numpy
# synthesize every frame, ffmpeg encodes H.264 + AAC. Zero external media,
# zero API keys. Byte-stable per seed; receipt -> out/<name>.receipt.json.
#
#   python3 render/render.py --name war-epic-v2 --seed 20261018 --dur 20 --fps 30
# ============================================================================
import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
import wave

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from PIL import __version__ as PIL_VERSION

STORY = 20.0          # timeline constants below are in "story seconds" of a 20 s edit
W, H = 1280, 720      # render size


def ss(e0, e1, x):
    """smoothstep — works for scalars and numpy arrays"""
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def load_font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def text_w(d, s, font, tr):
    return sum(d.textlength(c, font=font) for c in s) + tr * max(0, len(s) - 1)


def draw_tracked(d, x, y, s, font, fill, tr):
    for c in s:
        d.text((x, y), c, font=font, fill=fill)
        x += d.textlength(c, font=font) + tr


# ---------------------------------------------------------------- scene build
def build_scene(rng):
    # nebula backdrop, baked once (soft dust)
    neb = Image.new("RGB", (W, H), (2, 3, 10))
    nd = ImageDraw.Draw(neb, "RGBA")
    for _ in range(8):
        cx, cy = rng.rand() * W, rng.rand() * H
        rad = rng.uniform(200, 520)
        b = ((34, 42, 96), (58, 38, 96), (22, 62, 100), (46, 34, 82))[int(rng.randint(0, 4))]
        for k in range(12):
            r = rad * (1.0 - k / 12.0)
            a = int(9 + 6 * (k / 12.0) ** 2)
            nd.ellipse((cx - r, cy - r, cx + r, cy + r),
                       fill=(int(b[0]), int(b[1]), int(b[2]), a))
    neb = neb.filter(ImageFilter.GaussianBlur(70))

    # stars: 3 parallax layers (count, drift px/s, size, brightness)
    layers = []
    for count, speed, size, bright in ((270, 7.0, 1, 0.42), (175, 15.0, 1, 0.66), (105, 27.0, 2, 0.95)):
        layers.append((rng.rand(count) * W, rng.rand(count) * H, speed, size, bright,
                       0.35 + 0.65 * rng.rand(count), rng.rand(count) * math.tau))

    # fleets: cyan from the left, ember-orange from the right
    ships = []
    for i in range(34):
        left = (i % 2 == 0)
        ships.append(dict(
            t0=4.0 + rng.uniform(0.0, 4.6),
            y0=rng.uniform(0.16, 0.84) * H,
            x0=(-90.0 - rng.uniform(0, 260)) if left else (W + 90.0 + rng.uniform(0, 260)),
            vx=rng.uniform(140, 320) * (1 if left else -1),
            vy=rng.uniform(-30, 30),
            life=rng.uniform(4.2, 7.0),
            col=(118, 216, 255) if left else (255, 152, 72)))

    beams = []
    for i in range(18):
        left = (i % 2 == 0)
        beams.append(dict(
            te=rng.uniform(4.9, 12.7),
            x=rng.uniform(0.14, 0.46) * W if left else rng.uniform(0.54, 0.86) * W,
            y=rng.uniform(0.20, 0.80) * H,
            ang=rng.uniform(-0.45, 0.45) + (0.55 if left else math.pi - 0.55),
            L=rng.uniform(320, 760),
            col=(150, 232, 255) if left else (255, 176, 96)))

    flashes, sparks = [], []
    for _ in range(13):
        te = rng.uniform(5.0, 13.5)
        x, y = rng.uniform(0.16, 0.84) * W, rng.uniform(0.20, 0.80) * H
        mag = rng.uniform(0.55, 1.0)
        flashes.append(dict(te=te, x=x, y=y, mag=mag))
        for _ in range(30):
            sparks.append(dict(te=te, x=x, y=y,
                               a=rng.uniform(0, math.tau),
                               sp=rng.uniform(90, 520) * mag,
                               li=rng.uniform(0.5, 1.5),
                               col=(255, int(rng.uniform(165, 230)), int(rng.uniform(70, 140)))))

    embers = [dict(te=rng.uniform(13.0, 15.5),
                   x=rng.uniform(0.08, 0.92) * W,
                   y=rng.uniform(0.10, 0.90) * H,
                   vx=rng.uniform(-8, 8), vy=rng.uniform(-14, -4),
                   ph=rng.uniform(0, math.tau)) for _ in range(110)]

    return dict(neb=neb, layers=layers, ships=ships, beams=beams,
                flashes=flashes, sparks=sparks, embers=embers)


# ---------------------------------------------------------------- one frame
def make_frame(sc, cfg, i):
    fps, S = cfg["fps"], cfg["S"]
    t = i / fps            # wall time
    ts = t / S             # story time
    canvas = sc["neb"].copy()
    d = ImageDraw.Draw(canvas)
    glow = Image.new("RGB", (W, H))
    g = ImageDraw.Draw(glow)
    mov = Image.new("RGB", (W, H))
    m = ImageDraw.Draw(mov)

    # camera shake from recent hits
    amp = 0.0
    for f in sc["flashes"]:
        dt = ts - f["te"]
        if 0.0 <= dt < 0.5:
            amp += f["mag"] * (1.0 - dt / 0.5) * 7.0
    sx = math.sin(t * 57.3) * amp + 10.0 * math.sin(t * 0.25)
    sy = math.cos(t * 71.7) * amp * 0.7 + 6.0 * math.cos(t * 0.19)

    # stars (twinkle + drift + shake)
    for xs, ys, speed, size, bright, bs, ph in sc["layers"]:
        for j in range(len(xs)):
            x = int((xs[j] + t * speed + sx) % W)
            y = int((ys[j] + sy) % H)
            b = bs[j] * (0.72 + 0.28 * math.sin(t * 2.6 + ph[j])) * bright
            c = int(235 * b)
            if size <= 1:
                d.point((x, y), fill=(c, c, min(255, c + 22)))
            else:
                d.ellipse((x - 1, y - 1, x + 1, y + 1), fill=(c, c, min(255, c + 22)))

    # ships: glowing motion trails
    for s in sc["ships"]:
        dt = ts - s["t0"]
        if 0.0 < dt < s["life"]:
            col = s["col"]
            cx = s["x0"] + s["vx"] * dt + sx
            cy = s["y0"] + s["vy"] * dt + sy
            for k in range(12):
                k1, k2 = k * 0.042, (k + 1) * 0.042
                xa = s["x0"] + s["vx"] * (dt - k2) + sx
                ya = s["y0"] + s["vy"] * (dt - k2) + sy
                xb = s["x0"] + s["vx"] * (dt - k1) + sx
                yb = s["y0"] + s["vy"] * (dt - k1) + sy
                f = (1.0 - k / 12.0) ** 1.4
                g.line((xa, ya, xb, yb),
                       fill=(int(col[0] * f), int(col[1] * f), int(col[2] * f)),
                       width=3)
            g.ellipse((cx - 4.5, cy - 4.5, cx + 4.5, cy + 4.5), fill=col)
            m.ellipse((cx - 2, cy - 2, cx + 2, cy + 2),
                      fill=(min(255, col[0] + 50), min(255, col[1] + 40), min(255, col[2] + 40)))

    # beams (brief shots)
    for b in sc["beams"]:
        dte = abs(ts - b["te"])
        if dte < 0.10:
            f = 1.0 - dte / 0.10
            x2 = b["x"] + math.cos(b["ang"]) * b["L"]
            y2 = b["y"] + math.sin(b["ang"]) * b["L"]
            g.line((b["x"], b["y"], x2, y2), fill=tuple(int(c * f) for c in b["col"]), width=6)
            m.line((b["x"], b["y"], x2, y2), fill=(int(240 * f), int(250 * f), 255), width=1)

    # explosions: shock ring + expanding core + sparks
    fk = 0.0
    for f in sc["flashes"]:
        dt = ts - f["te"]
        x, y, mag = f["x"] + sx, f["y"] + sy, f["mag"]
        if 0.0 <= dt < 1.0:
            k = 1.0 - dt
            r = 8 + 130 * (dt ** 0.55) * mag
            g.ellipse((x - r, y - r, x + r, y + r),
                      outline=(int(255 * k), int(215 * k), int(150 * k)), width=4)
            if dt < 0.22:
                r2 = 14 + 90 * (dt / 0.22) * mag
                g.ellipse((x - r2, y - r2, x + r2, y + r2),
                          fill=(int(255 * k), int(245 * k), int(210 * k)))
            if dt < 0.10:
                r3 = 6 + 55 * (dt / 0.10) * mag
                g.ellipse((x - r3, y - r3, x + r3, y + r3), fill=(255, 252, 240))
            if dt < 0.16:
                fk += mag * (1.0 - dt / 0.16)
        elif 1.0 <= dt < 6.5:
            w = (1.0 - (dt - 1.0) / 5.5) * 0.5
            rh = 40 + 130 * (dt - 1.0) / 5.5
            g.ellipse((x - rh, y - rh, x + rh, y + rh),
                      fill=(int(26 * w), int(18 * w), int(12 * w)))
    for s in sc["sparks"]:
        dts = ts - s["te"]
        if 0.0 <= dts < s["li"]:
            f = 1.0 - dts / s["li"]
            x = s["x"] + math.cos(s["a"]) * s["sp"] * dts + sx
            y = s["y"] + math.sin(s["a"]) * s["sp"] * dts + 90 * dts * dts + sy
            c = tuple(int(cc * f) for cc in s["col"])
            g.ellipse((x - 1.8, y - 1.8, x + 1.8, y + 1.8), fill=c)

    # aftermath embers
    for e in sc["embers"]:
        dte = ts - e["te"]
        if 0.0 <= dte < 6.5:
            f = max(0.0, 1.0 - dte / 6.5)
            x = e["x"] + e["vx"] * dte + 6 * math.sin(dte * 1.2 + e["ph"]) + sx
            y = e["y"] + e["vy"] * dte + sy
            g.ellipse((x - 1.5, y - 1.5, x + 1.5, y + 1.5),
                      fill=(int(255 * f), int(142 * f), int(56 * f)))

    # titles
    fnt_big, fnt_sub, fnt_end = sc["fonts"]
    a_t = ss(0.7, 1.6, ts) * (1.0 - ss(3.2, 4.0, ts))
    if a_t > 0.01:
        s1, s2 = "WAR EPIC", "v2 — rendered in the cloud"
        w1 = text_w(m, s1, fnt_big, 26)
        x1 = (W - w1) / 2.0
        y1 = H * 0.42
        draw_tracked(g, x1, y1, s1, fnt_big, (int(110 * a_t), int(122 * a_t), int(150 * a_t)), 26)
        draw_tracked(m, x1, y1, s1, fnt_big, (int(232 * a_t), int(238 * a_t), int(255 * a_t)), 26)
        w2 = text_w(m, s2, fnt_sub, 6)
        draw_tracked(m, (W - w2) / 2.0, y1 + 118, s2, fnt_sub,
                     (int(150 * a_t), int(158 * a_t), int(182 * a_t)), 6)
    a_e = ss(15.9, 16.8, ts) * (1.0 - ss(19.2, 19.9, ts))
    if a_e > 0.01:
        s3 = "EON · free cloud render · ffmpeg on a GitHub runner"
        w3 = text_w(m, s3, fnt_end, 4)
        draw_tracked(m, (W - w3) / 2.0, H - 74, s3, fnt_end,
                     (int(140 * a_e), int(140 * a_e), int(156 * a_e)), 4)

    # compose: nebula+stars, bloom, crisp, screen flash
    comb = ImageChops.add(canvas, glow.filter(ImageFilter.GaussianBlur(7)))
    comb = ImageChops.add(comb, mov)
    if fk > 0.01:
        flat = Image.new("RGB", (W, H), (int(78 * fk), int(68 * fk), int(56 * fk)))
        comb = ImageChops.add(comb, flat)

    # global brightness + vignette + grain -> bytes
    bright = ss(0.0, 0.6, ts) * (1.0 - ss(17.6, 19.9, ts))
    arr = np.asarray(comb).astype(np.float32)
    arr *= float(bright)
    np.multiply(arr, cfg["vig"], out=arr)
    if bright > 0.05:
        arr += cfg["grain"][i % len(cfg["grain"])]
    np.clip(arr, 0, 255, out=arr)
    return arr.astype(np.uint8)


# ---------------------------------------------------------------- audio bed
def build_audio(sc, cfg, path, seed):
    sr = 44100
    dur, S = cfg["dur"], cfg["S"]
    n = int(sr * dur)
    tt = np.arange(n) / sr
    rng = np.random.RandomState(seed + 99)
    aud = np.zeros(n)

    # drone (A1 stack with slow LFOs)
    for f, a, lf in ((55.0, 0.17, 0.05), (82.5, 0.11, 0.07), (110.0, 0.075, 0.11), (164.8, 0.03, 0.09)):
        aud += a * (0.72 + 0.28 * np.sin(math.tau * lf * tt + f)) * np.sin(math.tau * f * tt)

    # space rumble: low-passed noise, swelling through the battle
    nz = rng.randn(n)
    sp = np.fft.rfft(nz)
    fr = np.fft.rfftfreq(n, 1.0 / sr)
    nz = np.fft.irfft(sp / (1.0 + (fr / 160.0) ** 2), n)
    story = tt / S
    env = ss(3.2, 6.5, story) * (1.0 - ss(16.6, 19.4, story))
    aud += 3.2 * nz / (np.max(np.abs(nz)) + 1e-9) * env

    # sub-boom on every explosion
    for f in sc["flashes"]:
        i0 = int(sr * f["te"] * S)
        seg = min(int(sr * 1.5), n - i0)
        if seg <= 0:
            continue
        tsb = np.arange(seg) / sr
        fq = 44.0 - 18.0 * (tsb / 1.5)
        ph = math.tau * np.cumsum(fq) / sr
        aud[i0:i0 + seg] += 0.34 * f["mag"] * np.sin(ph) * np.exp(-2.6 * tsb)
        nn = rng.randn(seg)
        nf = np.fft.rfft(nn)
        ff = np.fft.rfftfreq(seg, 1.0 / sr)
        nn = np.fft.irfft(nf / (1.0 + (ff / 220.0) ** 2), seg)
        aud[i0:i0 + seg] += 0.18 * f["mag"] * nn * np.exp(-5.0 * tsb)

    aud *= ss(0.0, 0.5, story) * (1.0 - ss(17.6, 19.9, story))
    aud = aud / (np.max(np.abs(aud)) + 1e-9) * 0.89
    pcm = (aud * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(np.column_stack([pcm, pcm]).flatten().tobytes())


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="war-epic-v2")
    ap.add_argument("--seed", type=int, default=20261018)
    ap.add_argument("--dur", type=float, default=20.0)
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()

    S = args.dur / STORY
    frames = int(round(args.fps * args.dur))
    rng = np.random.RandomState(args.seed)
    sc = build_scene(rng)
    sc["fonts"] = (load_font(86), load_font(28), load_font(26))

    rg = np.random.RandomState(args.seed + 7)
    grain = [rg.randint(-4, 5, size=(H, W, 3)).astype(np.float32) for _ in range(6)]
    yy, xx = np.mgrid[0:H, 0:W]
    rr = np.sqrt(((xx - W / 2.0) / (W / 2.0)) ** 2 + ((yy - H / 2.0) / (H / 2.0)) ** 2) / math.sqrt(2.0)
    vig = (1.0 - 0.55 * np.clip(rr, 0, 1.0) ** 2.2).astype(np.float32)[:, :, None]

    cfg = dict(fps=args.fps, S=S, dur=args.dur, vig=vig, grain=grain)
    os.makedirs("out", exist_ok=True)
    mp4 = f"out/{args.name}.mp4"
    wav = f"out/{args.name}.wav"
    build_audio(sc, cfg, wav, args.seed)

    t0 = time.time()
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-video_size", f"{W}x{H}",
           "-framerate", str(args.fps), "-i", "pipe:0",
           "-i", wav,
           "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", "-c:a", "aac", "-b:a", "160k",
           "-shortest", mp4]
    logf = f"out/{args.name}.ffmpeg.log"
    with open(logf, "wb") as lf:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=lf)
        for i in range(frames):
            proc.stdin.write(make_frame(sc, cfg, i).tobytes())
            if i % 90 == 0 or i == frames - 1:
                print(f"frame {i + 1}/{frames} ({(i + 1) * 100 // frames}%)", flush=True)
        proc.stdin.close()
        rc = proc.wait()

    if rc != 0:
        print("FFMPEG FAILED — log tail:")
        print(open(logf).read()[-3000:])
        sys.exit(1)

    probe = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                            "-show_format", "-show_streams", mp4],
                           capture_output=True, text=True).stdout
    try:
        pinfo = json.loads(probe)
    except Exception:
        pinfo = {}
    streams = [{k: s.get(k) for k in ("codec_name", "codec_type", "width", "height", "nb_frames", "duration")}
               for s in pinfo.get("streams", [])]
    sha = hashlib.sha256(open(mp4, "rb").read()).hexdigest()
    ffv = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    receipt = dict(
        name=args.name, seed=args.seed, fps=args.fps, dur=args.dur, frames=frames,
        sha256=sha, bytes=os.path.getsize(mp4), render_seconds=round(time.time() - t0, 1),
        streams=streams,
        python=sys.version.split()[0], pillow=PIL_VERSION, numpy=np.__version__,
        ffmpeg=ffv, host=platform.node(), platform=platform.platform(), cpus=os.cpu_count(),
        timeline="war-epic v2 - 3-act space battle (stars / fleets / aftermath), synthesized audio",
    )
    with open(f"out/{args.name}.receipt.json", "w") as fh:
        json.dump(receipt, fh, indent=2)
    print(json.dumps(receipt, indent=2))
    print("RENDER-OK", args.name)


if __name__ == "__main__":
    main()
