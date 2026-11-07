"""Original chop soundtrack from impacts.json: 48 kHz stereo 16-bit WAV, stdlib only (+ ffmpeg for the polish).

Each hit lands at (impact frame - 1) / fps seconds (frame 1 = 0 s), with slight per-hit variation, then soft taps
as the slices land. Hits are soft-attack, double-filtered noise plus a rounded "thock" (no clicks or hiss); ffmpeg
then adds a small room reverb, gentle compression and a limiter. Length always equals the video.
"""
import array
import math
import random
import subprocess
import wave
from pathlib import Path

SR = 48000
PEAK = 0.89  # about -1 dBFS

# lp: noise brightness (Hz), decay: s, swish: blade-through-flesh hiss level and its rise time,
# body: "thock" pitch (Hz) + level, squish: wet low band, grit: scrape/crumble texture (0..1)
TEXTURES = {
    "wet":     dict(lp=1700, decay=0.075, swish=0.55, rise=0.018, body=150, body_gain=0.55, squish=0.8, grit=0.0),
    "crisp":   dict(lp=3600, decay=0.04, swish=0.45, rise=0.008, body=230, body_gain=0.45, squish=0.0, grit=0.15),
    "dry":     dict(lp=2800, decay=0.12, swish=0.7, rise=0.03, body=0, body_gain=0.0, squish=0.0, grit=0.35),
    "waxy":    dict(lp=1100, decay=0.06, swish=0.4, rise=0.012, body=120, body_gain=0.65, squish=0.25, grit=0.0),
    "soft":    dict(lp=1900, decay=0.06, swish=0.45, rise=0.015, body=160, body_gain=0.5, squish=0.35, grit=0.0),
    "hollow":  dict(lp=2200, decay=0.05, swish=0.3, rise=0.006, body=380, body_gain=0.7, squish=0.0, grit=0.0),
    "crumbly": dict(lp=2400, decay=0.1, swish=0.5, rise=0.02, body=140, body_gain=0.35, squish=0.1, grit=0.45),
}
POLISH = "highpass=f=45,lowpass=f=7500,aecho=0.85:0.55:31|53|79:0.22|0.14|0.07," \
         "acompressor=threshold=-20dB:ratio=2.2:attack=8:release=160:makeup=2,alimiter=limit=0.89:level=false"


def _coef(fc: float) -> float:
    return 1 - math.exp(-2 * math.pi * fc / SR)


def _hit(rng, lp, decay, swish=0.5, rise=0.015, body=0.0, body_gain=0.0, squish=0.0, grit=0.0, knock=0.0):
    """One mono hit. Raised-cosine attack, two-pole low-passed noise (rounder than white noise), no clicks."""
    decay *= rng.uniform(0.85, 1.15)
    rise *= rng.uniform(0.8, 1.25)
    n = int(SR * (rise + decay * 7 + 0.02))
    a1 = a2 = _coef(lp * rng.uniform(0.85, 1.15))
    a_sq, a_hp, a_g = _coef(500), _coef(90), _coef(60)
    body *= rng.uniform(0.92, 1.08)
    s1 = s2 = sq = hp = g_s = 0.0
    g_t = 1.0
    phase = 0.0
    out = [0.0] * n
    for i in range(n):
        t = i / SR
        x = rng.uniform(-1.0, 1.0)
        s1 += a1 * (x - s1)
        s2 += a2 * (s1 - s2)
        hp += a_hp * (s2 - hp)
        env = (0.5 - 0.5 * math.cos(math.pi * t / rise)) if t < rise else math.exp(-(t - rise) / decay)
        if grit:  # smoothed random amplitude = soft scrape/crumble instead of harsh crackle
            if i % 240 == 0:
                g_t = 1 - grit * rng.random()
            g_s += a_g * (g_t - g_s)
            env *= g_s if i > 240 else 1.0
        v = (s2 - hp) * env * swish * 3.0
        if squish:
            sq += a_sq * (x - sq)
            v += sq * squish * 3.0 * (t / 0.015) * math.exp(1 - t / 0.015)
        if body_gain:  # rounded thock with a small downward pitch glide
            f = body * (1 + 0.25 * math.exp(-t / 0.02))
            phase += 2 * math.pi * f / SR
            v += body_gain * math.sin(phase) * min(1.0, t / 0.004) * math.exp(-t / 0.05)
        if knock:  # knife meets the wooden board: soft woody tone, no click
            v += knock * min(1.0, t / 0.003) * math.exp(-t / 0.06) * (0.7 * math.sin(2 * math.pi * 180 * t)
                                                                      + 0.25 * math.sin(2 * math.pi * 410 * t))
        out[i] = v
    for i in range(min(n, 480)):  # fade the tail so nothing ends with a step
        out[n - 1 - i] *= i / 480
    return out


def _add(L, R, mono, start, gain, pan):
    a = (pan + 1) * math.pi / 4
    gl, gr = math.cos(a) * gain, math.sin(a) * gain
    for i, v in enumerate(mono, start):
        if i >= len(L):
            break
        L[i] += v * gl
        R[i] += v * gr


def build(info: dict, seed: str = "") -> tuple[list, list]:
    fps, total = info["fps"], round(info["frame_end"] / info["fps"] * SR)
    rng = random.Random(seed or f"{info['object']}|{info['cuts']}|{info.get('seed', '')}")
    tex = TEXTURES.get(info.get("sound"), TEXTURES["soft"])
    L, R = [0.0] * total, [0.0] * total
    for imp in info["impacts"]:
        start = round((imp["frame"] - 1) / fps * SR)
        chop = imp["kind"] == "lengthwise"
        burst = _hit(rng, **tex, knock=0.7 if chop else 0.0)
        _add(L, R, burst, start, rng.uniform(0.8, 1.0) * (1.1 if chop else 1.0), rng.uniform(-0.2, 0.2))
    landings = info.get("landings") or []
    for f in landings:  # slices touching down: soft muted taps
        tap = _hit(rng, lp=1200, decay=0.02, swish=0.35, rise=0.004, body=110, body_gain=0.3)
        _add(L, R, tap, round((f - 1) / fps * SR), rng.uniform(0.15, 0.25), rng.uniform(-0.5, 0.5))
    if landings:  # one soft landing thump as the stack meets the board
        thump = _hit(rng, lp=500, decay=0.12, swish=0.3, rise=0.01, body=85, body_gain=0.7, squish=0.3)
        _add(L, R, thump, round((landings[0] - 1) / fps * SR), 0.6, 0.0)
    return L, R


def write(path: Path, L: list, R: list) -> float:
    peak = max(1e-9, max(map(abs, L)), max(map(abs, R)))
    k = PEAK / peak
    pcm = array.array("h", (0 for _ in range(2 * len(L))))
    pcm[0::2] = array.array("h", (int(v * k * 32767) for v in L))
    pcm[1::2] = array.array("h", (int(v * k * 32767) for v in R))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return len(L) / SR


def make_wav(info: dict, path: Path, ffmpeg: str | None = None) -> float:
    """Write the soundtrack; with ffmpeg, polish it (reverb, compression, limiter) at exactly the same length."""
    L, R = build(info)
    if not ffmpeg:
        return write(path, L, R)
    dry = path.with_name(path.stem + "_dry.wav")
    write(dry, L, R)
    r = subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(dry), "-af", f"{POLISH},apad,atrim=end_sample={len(L)}",
                        "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(path)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffmpeg audio polish failed: {r.stderr.strip()[-400:]}")
    dry.unlink()
    return len(L) / SR
