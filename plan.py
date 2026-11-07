"""Pure-Python plan shared by Blender and the CLI: object presets, cut layout and the impact timeline.

One impact list drives the knife, the counter and the WAV, so they can never drift apart.
"""
import hashlib
import math

FIRST_IMPACT = 29   # guide: first contact on frame 29
SPACING = 6         # then one cut every 6 frames
FALL_DELAY = 20     # slices start falling 20 frames after the last cut
TAIL = 97           # video ends 97 frames after the last cut (papaya 50: 323 -> 420)
MAX_TILT = math.radians(26)   # steepest overall cut angle for a run
MIN_SLICE = 0.4               # tilted neighbours keep at least 40% of the even slice thickness everywhere


def _f(base, core=None, core_r=0.0, core_z=0.0, seeds=None, seed_band=(0, 0), rind=None, rind_w=0.0, **extra):
    """Cut-surface spec. extra: segments=n, rings=(n, colour), layers=(n, colour), holes=colour, crumb=True, fibers=True."""
    return dict(base=base, core=core or base, core_r=core_r, core_z=core_z, seeds=seeds, seed_band=seed_band,
                rind=rind or base, rind_w=rind_w, **extra)


# Colours are sRGB hex. size = (half-width x, half-depth y, height z) in scene units (board top is z=0).
# shape: egg | cylinder | box.  pattern: split = lengthwise first, then N-1 cuts across both halves (2N pieces)
#                               parallel = N cuts (N+1 pieces)
# skin kind: mottled fuzzy stripes rings solid marble speckled diamonds crust glossy sandy dimpled
# sound: wet crisp dry waxy soft hollow crumbly
PRESETS = {
    "papaya": dict(shape="egg", size=(0.62, 0.58, 2.1), taper=0.14, pattern="split", sound="wet",
                   skin=dict(kind="mottled", base="#c9b04a", accent="#5f8a22", rough=0.38),
                   flesh=_f("#f2692a", "#d9541c", 0.36, 0.62, "#141210", (0.12, 0.34), "#d8b85a", 0.05, fibers=True)),
    "kiwi": dict(shape="egg", size=(0.56, 0.52, 1.6), taper=0.05, pattern="parallel", sound="wet",
                 skin=dict(kind="fuzzy", base="#7a5a36", accent="#5a3f22", rough=0.85),
                 flesh=_f("#86b23a", "#efe8c4", 0.2, 0.9, "#151310", (0.22, 0.34), "#6b5a2a", 0.04, fibers=True)),
    "soap": dict(shape="box", size=(0.72, 0.48, 1.9), taper=0.0, pattern="parallel", sound="dry",
                 skin=dict(kind="marble", base="#c9a6e0", accent="#f4ecfa", rough=0.45),
                 flesh=_f("#d7b9ea", crumb=True)),
    "carrot": dict(shape="cylinder", size=(0.42, 0.42, 2.3), taper=0.55, pattern="parallel", sound="crisp",
                   skin=dict(kind="rings", base="#e8741c", accent="#c45a12", rough=0.5),
                   flesh=_f("#f5862a", "#f7a54a", 0.42, 1.0, rind="#e06a18", rind_w=0.06, rings=(3, "#f9b060"))),
    "cucumber": dict(shape="cylinder", size=(0.42, 0.42, 2.3), taper=0.05, pattern="parallel", sound="crisp",
                     skin=dict(kind="stripes", base="#2f5a1e", accent="#5a8a34", rough=0.4),
                     flesh=_f("#cfe3a0", "#b9d68a", 0.45, 1.0, "#e8f0c8", (0.12, 0.4), "#3f7a26", 0.07, fibers=True)),
    "candle": dict(shape="cylinder", size=(0.55, 0.55, 2.0), taper=0.0, pattern="parallel", sound="waxy", wick=True,
                   skin=dict(kind="solid", base="#f1e6cf", accent="#e8dcc0", rough=0.3, sss=0.4),
                   flesh=_f("#f4ead6", rind="#efe3c8", rind_w=0.03)),
    "watermelon": dict(shape="egg", size=(0.95, 0.9, 1.9), taper=0.03, pattern="split", sound="wet",
                       skin=dict(kind="stripes", base="#2c5a1c", accent="#7fae4a", rough=0.3),
                       flesh=_f("#e8333c", seeds="#121010", seed_band=(0.3, 0.7), rind="#e9f2d0", rind_w=0.1, fibers=True)),
    "mango": dict(shape="egg", size=(0.6, 0.48, 1.8), taper=0.1, pattern="split", sound="wet",
                  skin=dict(kind="mottled", base="#e0a92a", accent="#c2492a", rough=0.32),
                  flesh=_f("#f7b52c", "#eef0d8", 0.14, 0.75, rind="#e0a030", rind_w=0.04, fibers=True)),
    "apple": dict(shape="egg", size=(0.75, 0.75, 1.45), taper=-0.06, pattern="parallel", sound="crisp",
                  skin=dict(kind="mottled", base="#b5161e", accent="#e0a83a", rough=0.28),
                  flesh=_f("#f3e9c6", "#e2d39c", 0.2, 0.7, "#3a2210", (0.08, 0.16), "#b5161e", 0.03)),
    "orange": dict(shape="egg", size=(0.75, 0.75, 1.45), taper=0.0, pattern="parallel", sound="wet",
                   skin=dict(kind="dimpled", base="#f08a12", accent="#e07a0c", rough=0.45),
                   flesh=_f("#ff9a1a", "#fff0d0", 0.08, 1.0, rind="#fff2dc", rind_w=0.08, segments=10, fibers=True)),
    "lemon": dict(shape="egg", size=(0.62, 0.62, 1.5), taper=0.0, pattern="parallel", sound="wet",
                  skin=dict(kind="dimpled", base="#f2d21c", accent="#e2c010", rough=0.4),
                  flesh=_f("#f7e45a", "#fffbe0", 0.08, 1.0, rind="#fffbe6", rind_w=0.08, segments=9, fibers=True)),
    "tomato": dict(shape="egg", size=(0.75, 0.75, 1.35), taper=-0.04, pattern="parallel", sound="wet",
                   skin=dict(kind="glossy", base="#d8231a", accent="#b81a12", rough=0.18),
                   flesh=_f("#e8402c", "#f06a3c", 0.3, 1.0, "#f2d27a", (0.32, 0.6), "#d8231a", 0.06)),
    "onion": dict(shape="egg", size=(0.62, 0.62, 1.3), taper=-0.05, pattern="parallel", sound="crisp",
                  skin=dict(kind="mottled", base="#8a3a52", accent="#c9a07a", rough=0.35),
                  flesh=_f("#f3e3ef", rind="#a85078", rind_w=0.04, rings=(9, "#c98ab0"))),
    "beetroot": dict(shape="egg", size=(0.6, 0.6, 1.3), taper=-0.1, pattern="parallel", sound="crisp",
                     skin=dict(kind="mottled", base="#4a1530", accent="#6a2a3a", rough=0.6),
                     flesh=_f("#9e1a4a", rind="#5a1030", rind_w=0.04, rings=(7, "#d0487a"))),
    "dragon fruit": dict(shape="egg", size=(0.62, 0.6, 1.6), taper=0.08, pattern="split", sound="wet",
                         skin=dict(kind="mottled", base="#e0306a", accent="#8ac23a", rough=0.3),
                         flesh=_f("#f4f2ee", seeds="#111111", seed_band=(0.0, 0.86), rind="#e0306a", rind_w=0.05)),
    "avocado": dict(shape="egg", size=(0.62, 0.55, 1.7), taper=0.22, pattern="split", sound="soft",
                    skin=dict(kind="speckled", base="#2f3d1a", accent="#4a5a22", rough=0.7),
                    flesh=_f("#d8e27a", "#7a4a26", 0.42, 0.55, rind="#4f8a2a", rind_w=0.1)),
    "peach": dict(shape="egg", size=(0.72, 0.7, 1.4), taper=-0.02, pattern="split", sound="wet",
                  skin=dict(kind="fuzzy", base="#f2a25a", accent="#d8463a", rough=0.7),
                  flesh=_f("#f7b45a", "#a8452a", 0.25, 0.6, rind="#e8803a", rind_w=0.04, fibers=True)),
    "strawberry": dict(shape="egg", size=(0.55, 0.55, 1.2), taper=-0.35, pattern="parallel", sound="wet",
                       skin=dict(kind="speckled", base="#d01a24", accent="#f0d050", rough=0.3),
                       flesh=_f("#e8404a", "#f8e0e0", 0.35, 0.9, rind="#c8101e", rind_w=0.12, fibers=True)),
    "coconut": dict(shape="egg", size=(0.78, 0.78, 1.5), taper=0.0, pattern="split", sound="hollow",
                    skin=dict(kind="fuzzy", base="#6a4426", accent="#3e2614", rough=0.9),
                    flesh=_f("#f6f3ea", "#dfe9ee", 0.62, 0.8, rind="#5a3a20", rind_w=0.07)),
    "pineapple": dict(shape="egg", size=(0.62, 0.62, 1.9), taper=0.05, pattern="parallel", sound="wet",
                      skin=dict(kind="diamonds", base="#c08a2a", accent="#5a6a1a", rough=0.6),
                      flesh=_f("#f6d84a", "#fbefb0", 0.22, 1.0, rind="#d8b040", rind_w=0.08, fibers=True)),
    "potato": dict(shape="egg", size=(0.7, 0.58, 1.2), taper=0.05, pattern="parallel", sound="crisp",
                   skin=dict(kind="speckled", base="#b88a5a", accent="#7a5a3a", rough=0.8),
                   flesh=_f("#f3e2a8", rind="#c8a070", rind_w=0.03)),
    "cake": dict(shape="box", size=(0.75, 0.6, 1.5), taper=0.0, pattern="parallel", sound="crumbly",
                 skin=dict(kind="solid", base="#e6b4c8", accent="#d99ab4", rough=0.5),
                 flesh=_f("#f3d9a4", rind="#f7d8e4", rind_w=0.06, layers=(3, "#8a3a2a"), crumb=True)),
    "cheese": dict(shape="box", size=(0.8, 0.55, 1.3), taper=0.0, pattern="parallel", sound="soft",
                   skin=dict(kind="solid", base="#f2c230", accent="#e8b020", rough=0.45),
                   flesh=_f("#f6cf45", holes="#c89a18")),
    "bread": dict(shape="box", size=(0.7, 0.6, 1.6), taper=0.0, pattern="parallel", sound="crumbly",
                  skin=dict(kind="crust", base="#a8642a", accent="#6e3a14", rough=0.7),
                  flesh=_f("#f1dfb8", rind="#b8783a", rind_w=0.05, crumb=True)),
    "chocolate": dict(shape="box", size=(0.7, 0.32, 1.8), taper=0.0, pattern="parallel", sound="crisp",
                      skin=dict(kind="glossy", base="#4a2614", accent="#3a1c0e", rough=0.25),
                      flesh=_f("#5a2e16", layers=(2, "#c89a6a"))),
    "sand": dict(shape="box", size=(0.75, 0.6, 1.5), taper=0.0, pattern="parallel", sound="dry",
                 skin=dict(kind="sandy", base="#7ec8e8", accent="#5aaed4", rough=0.95),
                 flesh=_f("#8cd0ec", crumb=True)),
    "funnel": dict(shape="cylinder", size=(0.35, 0.35, 1.9), taper=-1.4, pattern="parallel", sound="hollow",
                   skin=dict(kind="glossy", base="#f06a1a", accent="#d85a10", rough=0.3),
                   flesh=_f("#f27a2a", rind="#d85a10", rind_w=0.05)),
}
ALIASES = {"papita": "papaya", "kheera": "cucumber", "gajar": "carrot", "tarbooz": "watermelon", "aam": "mango",
           "seb": "apple", "santra": "orange", "nimbu": "lemon", "tamatar": "tomato", "wax": "candle",
           "pyaz": "onion", "kanda": "onion", "chukandar": "beetroot", "beet": "beetroot", "dragonfruit": "dragon fruit",
           "pitaya": "dragon fruit", "aadu": "peach", "nariyal": "coconut", "ananas": "pineapple", "aloo": "potato",
           "batata": "potato", "paneer": "cheese", "pav": "bread", "kinetic": "sand"}


def resolve(name: str) -> dict:
    """Preset for an object name ('Soap bar' -> soap). Unknown names get a stable generic look from the name."""
    key = name.strip().lower()
    for word in sorted(list(PRESETS) + list(ALIASES), key=len, reverse=True):
        if word in key:
            preset = dict(PRESETS[ALIASES.get(word, word)])
            break
    else:
        h = hashlib.sha1(key.encode()).digest()
        hue = h[0] / 255
        preset = dict(shape="egg", size=(0.62, 0.58, 1.8), taper=0.06, pattern="parallel", sound="soft",
                      skin=dict(kind="mottled", base=_hsv(hue, 0.6, 0.75), accent=_hsv(hue + 0.08, 0.7, 0.5), rough=0.4),
                      flesh=_f(_hsv(hue + 0.03, 0.45, 0.95), _hsv(hue + 0.03, 0.3, 1.0), 0.25, 0.8,
                               rind=_hsv(hue, 0.6, 0.7), rind_w=0.04, fibers=True))
    preset["label"] = name.strip().upper()
    return preset


def _hsv(h, s, v) -> str:
    import colorsys
    return "#%02x%02x%02x" % tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h % 1, s, v))


def validate_cuts(cuts) -> int:
    """Cuts must be a positive whole number (guide rule). Upper bound keeps a run under ~60 s of video."""
    if isinstance(cuts, bool) or not isinstance(cuts, (int, str)) or not str(cuts).strip().isdigit():
        raise ValueError(f"Cuts must be a positive whole number, got {cuts!r}")
    n = int(cuts)
    if not 1 <= n <= 200:
        raise ValueError(f"Cuts must be between 1 and 200, got {n}")
    return n


def timeline(cuts: int) -> dict:
    """Frame numbers (1-based, 24 fps): impacts, fall start and last frame."""
    impacts = [FIRST_IMPACT + SPACING * i for i in range(cuts)]
    return {"impacts": impacts, "fall_start": impacts[-1] + FALL_DELAY, "frame_end": impacts[-1] + TAIL}


def max_radius(preset: dict) -> float:
    return max(math.hypot(preset["size"][0], preset["size"][1]) * max(1.0, 1 - preset["taper"]), 1e-3)


def cut_slopes(n: int, height: float, radius: float, rng=None) -> list[tuple[float, float]]:
    """Slope (ax, ay) of each horizontal cut plane z = b - ax*x - ay*y, bottom to top.

    One random overall angle per run plus a wandering per-cut wobble ("yede-vakde" slices). Neighbouring planes
    may differ only so much that they never cross inside the object, so every slice stays a real slice.
    """
    if rng is None or n == 0:
        return [(0.0, 0.0)] * n
    # lean the planes roughly towards the camera (front-left) so the blade face and the cut faces stay visible
    tilt, az = math.tan(rng.uniform(0.25, 1.0) * MAX_TILT), math.radians(-118) + rng.uniform(-1.1, 1.1)
    base = (tilt * math.cos(az), tilt * math.sin(az))
    step = (1 - MIN_SLICE) * height / (n + 1) / radius  # max |slope difference| between neighbours
    wob = [0.0, 0.0]
    out = []
    for _ in range(n):
        for i in range(2):  # clamped random walk: each step <= 0.6*step/sqrt(2) per axis, so |difference| < step
            wob[i] = max(-0.12, min(0.12, wob[i] + rng.uniform(-0.42, 0.42) * step))
        out.append((base[0] + wob[0], base[1] + wob[1]))
    return out


def cut_list(preset: dict, cuts: int, rng=None) -> list[dict]:
    """Every knife stroke in order (knife works top to bottom) with its plane and a human-readable direction."""
    height = preset["size"][2]
    split = preset["pattern"] == "split" and cuts >= 2
    out = []
    if split:
        az = rng.uniform(-0.45, 0.45) if rng else 0.0  # lengthwise plane turned up to ~26 degrees
        out.append({"kind": "lengthwise", "z": None, "azimuth": az, "slope": (0.0, 0.0),
                    "direction": f"lengthwise: knife chops straight down through the middle (vertical plane turned "
                                 f"{math.degrees(az):+.0f} deg), splitting the object into two halves"})
    n_h = cuts - len(out)
    slopes = cut_slopes(n_h, height, max_radius(preset), rng)  # bottom -> top
    across = "across both halves" if split else "through the whole object"
    for j in range(n_h, 0, -1):
        z, (ax, ay) = height * j / (n_h + 1), slopes[j - 1]
        angle = math.degrees(math.atan(math.hypot(ax, ay)))
        out.append({"kind": "horizontal", "z": z, "slope": (ax, ay), "index": j,
                    "direction": f"cut at {z / height:.0%} height, tilted {angle:.0f} deg, knife slides right-to-left {across}"})
    return out


def piece_count(preset: dict, cuts: int) -> int:
    split = preset["pattern"] == "split" and cuts >= 2
    return 2 * cuts if split else cuts + 1


def radius_x(preset: dict, z: float) -> float:
    """Half-width of the object at height z (matches the mesh generator in blender/scene.py)."""
    rx, _, h = preset["size"]
    zn = max(-1.0, min(1.0, 2 * z / h - 1))
    if preset["shape"] == "egg":
        return rx * math.sqrt(max(0.0, 1 - zn * zn)) * (1 - preset["taper"] * zn)
    if preset["shape"] == "cylinder":
        return rx * (1 - preset["taper"] * (zn + 1) / 2)
    return rx
