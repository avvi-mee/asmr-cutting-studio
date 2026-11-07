"""Self-checks for the parts that must stay exact (no Blender needed): python test_studio.py"""
import tempfile
import wave
from pathlib import Path

import plan
import sound
import studio

# guide page 5: papaya 50 -> impacts 29, 35, ... 323, fall 343, end 420 (17.5 s)
tl = plan.timeline(50)
assert tl["impacts"][:2] == [29, 35] and tl["impacts"][-1] == 323, tl["impacts"][-1]
assert tl["fall_start"] == 343 and tl["frame_end"] == 420 and 420 / 24 == 17.5
# guide page 7: soap 12 parallel cuts -> last impact 95, 13 pieces
assert plan.timeline(12)["impacts"][-1] == 95
soap = plan.resolve("Soap bar")
assert soap["pattern"] == "parallel" and plan.piece_count(soap, 12) == 13 and soap["label"] == "SOAP BAR"
# papaya: 1 lengthwise + 49 horizontal = 50 strokes, 100 half-slices
papaya = plan.resolve("Papaya")
cuts = plan.cut_list(papaya, 50)
assert len(cuts) == 50 and cuts[0]["kind"] == "lengthwise" and plan.piece_count(papaya, 50) == 100
zs = [c["z"] for c in cuts[1:]]
assert zs == sorted(zs, reverse=True), "knife works top to bottom"
assert plan.resolve("Green kiwi")["sound"] == "wet" and plan.resolve("gajar")["sound"] == "crisp"
assert plan.resolve("Mystery rock")["pattern"] == "parallel"  # unknown names still work
for bad in (0, -3, "2.5", "abc", True, 201):
    try:
        plan.validate_cuts(bad)
        raise AssertionError(f"accepted {bad!r}")
    except ValueError:
        pass
assert plan.validate_cuts("12") == 12

# random cut angles: different every seed, but neighbouring planes never cross inside the object
import math  # noqa: E402
import random  # noqa: E402

for name, n in (("Papaya", 50), ("Soap bar", 12), ("Funnel", 30), ("Watermelon", 200), ("Kiwi", 3)):
    pr = plan.resolve(name)
    h, R = pr["size"][2], plan.max_radius(pr)
    seen = set()
    for seed in range(20):
        hz = [c for c in plan.cut_list(pr, n, random.Random(seed)) if c["kind"] == "horizontal"][::-1]  # bottom->top
        seen.add(round(hz[0]["slope"][0], 4))
        for lo, hi in zip(hz, hz[1:]):
            gap = hi["z"] - lo["z"]
            slope_diff = math.hypot(hi["slope"][0] - lo["slope"][0], hi["slope"][1] - lo["slope"][1])
            assert gap - R * slope_diff >= plan.MIN_SLICE * gap - 1e-9, (name, seed)
        assert all(math.atan(math.hypot(*c["slope"])) <= plan.MAX_TILT + 0.13 for c in hz)
    assert len(seen) > 15, "angle should change from run to run"
assert all(c["slope"] == (0.0, 0.0) for c in plan.cut_list(papaya, 50))  # no rng -> straight cuts
for name in plan.PRESETS:  # every preset resolves and has the fields the scene builder needs
    pr = plan.resolve(name)
    assert pr["shape"] in ("egg", "cylinder", "box") and pr["sound"] in sound.TEXTURES, name
    assert plan.radius_x(pr, pr["size"][2] / 2) > 0, name

# soundtrack: exact length, hits at (frame-1)/fps, no clipping
info = {"object": "Soap bar", "cuts": 3, "fps": 24, "frame_end": 60, "sound": "dry", "landings": [50, 52],
        "impacts": [{"frame": f, "kind": "horizontal"} for f in (29, 35, 41)]}
L, R = sound.build(info)
assert len(L) == 60 / 24 * sound.SR
first = next(i for i, v in enumerate(L) if abs(v) > 1e-3)
assert abs(first - 28 / 24 * sound.SR) < 0.003 * sound.SR, first
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "t.wav"
    sound.write(p, L, R)
    with wave.open(str(p)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (2, 48000, 120000)
    # PNG validity check used for render resume
    good, bad = Path(d) / "0001.png", Path(d) / "0002.png"
    good.write_bytes(studio.PNG_SIG + b"\0" * 100 + b"\0\0\0\0IEND\xaeB`\x82")
    bad.write_bytes(studio.PNG_SIG + b"\0" * 100)
    assert studio.png_ok(good) and not studio.png_ok(bad)
    assert studio.clean_frames(Path(d), 3) == 1 and not bad.exists()
print("all checks passed")
