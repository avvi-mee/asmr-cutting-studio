"""Runs inside Blender (bundled Python).

  preview: build the scene from a clean file, save scene.blend + impacts.json, render opening / middle cut / fall PNGs
  render:  open the saved scene.blend and render numbered PNG frames (existing frames are kept, so it resumes)
  test:    tiny setup-check scene -> preview.png

blender -b --factory-startup --python blender/scene.py -- --mode preview --run <run dir>
"""
import json
import math
import random
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import plan  # noqa: E402

UP = Vector((0, 0, 1))


# ---------------------------------------------------------------- helpers
def lin(hexstr, a=1.0):
    h = hexstr.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c) + (a,)


def new_mat(name):
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True  # deprecated (always on) in newer Blender
    except Exception:
        pass
    nt = m.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None) or nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        nt.links.new(bsdf.outputs[0], out.inputs[0])
    return m, nt, bsdf


def put(nt, sock, v):
    if isinstance(v, bpy.types.NodeSocket):
        nt.links.new(v, sock)
    else:
        sock.default_value = v


def setin(node, name, v):
    if name in node.inputs:
        put(node.id_data, node.inputs[name], v)


def by_id(socks, ident):
    return next(s for s in socks if s.identifier == ident)


def m_(nt, op, a, b=None, clamp=False):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation, n.use_clamp = op, clamp
    put(nt, n.inputs[0], a)
    if b is not None:
        put(nt, n.inputs[1], b)
    return n.outputs[0]


def smooth(nt, v, lo, hi):
    """0 below lo, 1 above hi (smoothstep). lo > hi inverts."""
    n = nt.nodes.new("ShaderNodeMapRange")
    n.interpolation_type = "SMOOTHSTEP"
    put(nt, n.inputs["Value"], v)
    n.inputs["From Min"].default_value, n.inputs["From Max"].default_value = lo, hi
    return n.outputs["Result"]


def mix(nt, fac, a, b):
    n = nt.nodes.new("ShaderNodeMix")
    n.data_type = "RGBA"
    put(nt, by_id(n.inputs, "Factor_Float"), fac)
    put(nt, by_id(n.inputs, "A_Color"), a)
    put(nt, by_id(n.inputs, "B_Color"), b)
    return by_id(n.outputs, "Result_Color")


def orco(nt):
    """Original object-space position stored on every piece, so textures stay continuous across cuts."""
    n = nt.nodes.new("ShaderNodeAttribute")
    n.attribute_type, n.attribute_name = "GEOMETRY", "src_co"
    return n.outputs["Vector"]


def noise(nt, vec, scale, detail=4.0, rough=0.55):
    n = nt.nodes.new("ShaderNodeTexNoise")
    put(nt, n.inputs["Vector"], vec)
    n.inputs["Scale"].default_value, n.inputs["Detail"].default_value = scale, detail
    setin(n, "Roughness", rough)
    return n.outputs["Fac"]


def bump(nt, height, strength, distance=0.02):
    n = nt.nodes.new("ShaderNodeBump")
    n.inputs["Strength"].default_value, n.inputs["Distance"].default_value = strength, distance
    put(nt, n.inputs["Height"], height)
    return n.outputs["Normal"]


def angle01(nt, vec):
    """Angle around the vertical axis, 0..1."""
    n = nt.nodes.new("ShaderNodeTexGradient")
    n.gradient_type = "RADIAL"
    put(nt, n.inputs["Vector"], vec)
    return n.outputs["Fac"]


def emission_mat(name, color, strength):
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:
        pass
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    e, o = nt.nodes.new("ShaderNodeEmission"), nt.nodes.new("ShaderNodeOutputMaterial")
    e.inputs["Color"].default_value, e.inputs["Strength"].default_value = color, strength
    nt.links.new(e.outputs[0], o.inputs[0])
    return m


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def mesh_obj(name, bm, mats=()):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    return link(bpy.data.objects.new(name, me))


def aim(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


# ---------------------------------------------------------------- materials
def skin_material(p):
    s = p["skin"]
    m, nt, bsdf = new_mat("Skin")
    co = orco(nt)
    base, acc = lin(s["base"]), lin(s["accent"])
    kind = s["kind"]
    dark = tuple(c * 0.55 for c in base[:3]) + (1,)
    rough = s["rough"]
    if kind == "mottled":
        col = mix(nt, smooth(nt, noise(nt, co, 2.2, 6, 0.6), 0.48, 0.66), base, acc)
        col = mix(nt, m_(nt, "MULTIPLY", smooth(nt, noise(nt, co, 9, 3), 0.55, 0.8), 0.35), col, base)
        col = mix(nt, spots(nt, co, 26, 0.09, 0.5), col, dark)  # small dark specks
        height, strength = noise(nt, co, 40, 3), 0.12
    elif kind == "fuzzy":
        col = mix(nt, smooth(nt, noise(nt, co, 6, 4), 0.4, 0.7), base, acc)
        height, strength = noise(nt, co, 140, 2, 0.8), 0.35
    elif kind == "stripes":
        wave = m_(nt, "SINE", m_(nt, "MULTIPLY", m_(nt, "ADD", angle01(nt, co), m_(nt, "MULTIPLY", noise(nt, co, 3, 3), 0.35)), 2 * math.pi * 14))
        col = mix(nt, smooth(nt, wave, -0.1, 0.35), base, acc)
        col = mix(nt, spots(nt, co, 22, 0.08, 0.6), col, acc)
        height, strength = noise(nt, co, 30, 3), 0.08
    elif kind == "rings":
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        put(nt, sep.inputs[0], co)
        rings = m_(nt, "SINE", m_(nt, "ADD", m_(nt, "MULTIPLY", sep.outputs["Z"], 55), m_(nt, "MULTIPLY", noise(nt, co, 4), 6)))
        col = mix(nt, m_(nt, "MULTIPLY", smooth(nt, rings, 0.6, 1.0), 0.6), base, acc)
        height, strength = smooth(nt, rings, 0.7, 1.0), 0.25
    elif kind == "marble":  # soap: soft veins swirling through the bar
        vein = m_(nt, "ABSOLUTE", m_(nt, "SINE", m_(nt, "ADD", m_(nt, "MULTIPLY", noise(nt, co, 1.6, 5, 0.6), 14), angle01(nt, co))))
        col = mix(nt, m_(nt, "MULTIPLY", smooth(nt, vein, 0.14, 0.02), 0.85), base, acc)
        height, strength = noise(nt, co, 60, 2), 0.04
    elif kind == "speckled":  # strawberry seeds, potato eyes, avocado bumps
        col = mix(nt, smooth(nt, noise(nt, co, 4, 4), 0.35, 0.75), base, tuple(c * 0.8 for c in base[:3]) + (1,))
        dots = spots(nt, co, 16, 0.11, 0.85)
        col = mix(nt, dots, col, acc)
        height, strength = m_(nt, "ADD", dots, m_(nt, "MULTIPLY", noise(nt, co, 50, 3), 0.6)), 0.3
    elif kind == "diamonds":  # pineapple: raised cells with dark seams
        vor = nt.nodes.new("ShaderNodeTexVoronoi")
        vor.feature = "DISTANCE_TO_EDGE"
        put(nt, vor.inputs["Vector"], co)
        vor.inputs["Scale"].default_value = 7.0
        edge = smooth(nt, vor.outputs["Distance"], 0.08, 0.0)
        col = mix(nt, edge, mix(nt, smooth(nt, noise(nt, co, 8), 0.3, 0.7), base, tuple(c * 1.15 for c in base[:3]) + (1,)), acc)
        height, strength = smooth(nt, vor.outputs["Distance"], 0.0, 0.25), 0.6
    elif kind == "crust":  # bread: baked colour patches and fine cracks
        vor = nt.nodes.new("ShaderNodeTexVoronoi")
        vor.feature = "DISTANCE_TO_EDGE"
        put(nt, vor.inputs["Vector"], co)
        vor.inputs["Scale"].default_value = 5.0
        col = mix(nt, smooth(nt, noise(nt, co, 3, 5), 0.35, 0.7), base, acc)
        col = mix(nt, smooth(nt, vor.outputs["Distance"], 0.03, 0.0), col, dark)
        height, strength = m_(nt, "ADD", noise(nt, co, 20, 4), smooth(nt, vor.outputs["Distance"], 0.0, 0.06)), 0.35
    elif kind == "glossy":  # tomato, chocolate, plastic funnel
        col = mix(nt, m_(nt, "MULTIPLY", noise(nt, co, 3, 3), 0.3), base, acc)
        height, strength = noise(nt, co, 80, 2), 0.02
        setin(bsdf, "Coat Weight", 0.6)
        setin(bsdf, "Coat Roughness", 0.06)
    elif kind == "sandy":  # kinetic sand: grainy everywhere
        grain = noise(nt, co, 220, 2, 0.9)
        col = mix(nt, smooth(nt, grain, 0.3, 0.7), base, acc)
        height, strength = grain, 0.5
    elif kind == "dimpled":  # citrus peel pores
        vor = nt.nodes.new("ShaderNodeTexVoronoi")
        put(nt, vor.inputs["Vector"], co)
        vor.inputs["Scale"].default_value = 45.0
        col = mix(nt, smooth(nt, noise(nt, co, 5, 3), 0.4, 0.7), base, acc)
        height, strength = vor.outputs["Distance"], 0.3
    else:  # solid
        col = mix(nt, m_(nt, "MULTIPLY", noise(nt, co, 3, 3), 0.25), base, acc)
        height, strength = noise(nt, co, 25, 2), 0.04
    # micro grain on every surface so nothing looks like smooth plastic
    height = m_(nt, "ADD", height, m_(nt, "MULTIPLY", noise(nt, co, 260, 2, 0.7), 0.25))
    put(nt, bsdf.inputs["Base Color"], col)
    bsdf.inputs["Roughness"].default_value = rough
    setin(bsdf, "Normal", bump(nt, height, strength + 0.05, 0.01))
    if s.get("sss"):
        setin(bsdf, "Subsurface Weight", s["sss"])
        setin(bsdf, "Subsurface Radius", (0.3, 0.2, 0.1))
    return m


def spots(nt, co, scale, radius, density):
    """Mask of small round dots (seeds, specks, pores)."""
    vor = nt.nodes.new("ShaderNodeTexVoronoi")
    put(nt, vor.inputs["Vector"], co)
    vor.inputs["Scale"].default_value = scale
    setin(vor, "Randomness", 1.0)
    dot = smooth(nt, vor.outputs["Distance"], radius, radius * 0.5)
    keep = smooth(nt, vor.outputs["Color"], 1 - density - 0.02, 1 - density)  # only some cells get a dot
    return m_(nt, "MULTIPLY", dot, keep)


def flesh_material(p):
    f = p["flesh"]
    rx, ry, h = p["size"]
    m, nt, bsdf = new_mat("Cut surface")
    co = orco(nt)
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    put(nt, sep.inputs[0], co)
    xn, yn = m_(nt, "DIVIDE", sep.outputs["X"], rx), m_(nt, "DIVIDE", sep.outputs["Y"], ry)
    zn = m_(nt, "SUBTRACT", m_(nt, "DIVIDE", sep.outputs["Z"], h / 2), 1.0)
    cmb = nt.nodes.new("ShaderNodeCombineXYZ")
    put(nt, cmb.inputs["X"], xn)
    put(nt, cmb.inputs["Y"], yn)
    vl = nt.nodes.new("ShaderNodeVectorMath")
    vl.operation = "LENGTH"
    nt.links.new(cmb.outputs[0], vl.inputs[0])
    rxy = vl.outputs["Value"]
    taper = m_(nt, "SUBTRACT", 1.0, m_(nt, "MULTIPLY", zn, p["taper"]))
    if p["shape"] == "egg":
        prof = m_(nt, "MULTIPLY", m_(nt, "SQRT", m_(nt, "MAXIMUM", m_(nt, "SUBTRACT", 1.0, m_(nt, "MULTIPLY", zn, zn)), 1e-4)), taper)
    elif p["shape"] == "cylinder":
        prof = m_(nt, "SUBTRACT", 1.0, m_(nt, "MULTIPLY", m_(nt, "ADD", zn, 1.0), p["taper"] / 2))
    else:
        prof = 1.0
    rho = m_(nt, "DIVIDE", rxy, prof)  # 0 at the axis, 1 at the skin, at any height
    zabs = m_(nt, "ABSOLUTE", zn)
    base = lin(f["base"])
    col = mix(nt, smooth(nt, noise(nt, co, 5, 3), 0.3, 0.8), base, tuple(c * 0.82 for c in base[:3]) + (1,))
    if f["core_r"] > 0:  # core / seed cavity, only in the middle part of the height
        in_core = m_(nt, "MULTIPLY", smooth(nt, rho, f["core_r"] + 0.03, f["core_r"] - 0.03), smooth(nt, zabs, f["core_z"] + 0.05, f["core_z"] - 0.05))
        col = mix(nt, in_core, col, lin(f["core"]))
    if f.get("segments"):  # citrus: pale lines between segments
        seg = m_(nt, "ABSOLUTE", m_(nt, "SINE", m_(nt, "MULTIPLY", angle01(nt, co), math.pi * f["segments"])))
        line = m_(nt, "MULTIPLY", smooth(nt, seg, 0.12, 0.04), smooth(nt, rho, f["core_r"], f["core_r"] + 0.05))
        col = mix(nt, line, col, lin(f["rind"]))
    if f["seeds"]:
        vor = nt.nodes.new("ShaderNodeTexVoronoi")
        put(nt, vor.inputs["Vector"], co)
        vor.inputs["Scale"].default_value = 14.0
        lo, hi = f["seed_band"]
        band = m_(nt, "MULTIPLY", smooth(nt, rho, lo - 0.02, lo + 0.02), smooth(nt, rho, hi + 0.02, hi - 0.02))
        band = m_(nt, "MULTIPLY", band, smooth(nt, zabs, max(f["core_z"], 0.75) + 0.05, max(f["core_z"], 0.75) - 0.05))
        seed = m_(nt, "MULTIPLY", smooth(nt, vor.outputs["Distance"], 0.2, 0.12), band)
        col = mix(nt, seed, col, lin(f["seeds"]))
    height = m_(nt, "MULTIPLY", noise(nt, co, 90, 2), 0.3)
    if f.get("rings"):  # onion / beetroot: concentric layers
        n, ring_col = f["rings"]
        ring = m_(nt, "ABSOLUTE", m_(nt, "SINE", m_(nt, "MULTIPLY", m_(nt, "ADD", rho, m_(nt, "MULTIPLY", noise(nt, co, 6), 0.03)), math.pi * n)))
        line = smooth(nt, ring, 0.22, 0.06)
        col = mix(nt, line, col, lin(ring_col))
        height = m_(nt, "ADD", height, m_(nt, "MULTIPLY", line, -0.6))
    if f.get("layers"):  # cake / chocolate: filling layers across the height
        n, layer_col = f["layers"]
        u = m_(nt, "DIVIDE", sep.outputs["Z"], h)
        band = m_(nt, "ABSOLUTE", m_(nt, "SINE", m_(nt, "MULTIPLY", u, math.pi * (n + 1))))
        col = mix(nt, smooth(nt, band, 0.16, 0.08), col, lin(layer_col))
    if f.get("holes"):  # cheese
        vor = nt.nodes.new("ShaderNodeTexVoronoi")
        put(nt, vor.inputs["Vector"], co)
        vor.inputs["Scale"].default_value = 4.5
        hole = smooth(nt, vor.outputs["Distance"], 0.2, 0.15)
        col = mix(nt, hole, col, lin(f["holes"]))
        height = m_(nt, "ADD", height, m_(nt, "MULTIPLY", hole, -2.0))
    if f.get("crumb"):  # bread / cake / sand / soap: tiny pores
        pores = spots(nt, co, 38, 0.2, 0.7)
        col = mix(nt, m_(nt, "MULTIPLY", pores, 0.35), col, tuple(c * 0.6 for c in base[:3]) + (1,))
        height = m_(nt, "ADD", height, m_(nt, "MULTIPLY", pores, -0.8))
    if f.get("fibers"):  # juicy flesh: fine streaks
        mp = nt.nodes.new("ShaderNodeMapping")
        mp.inputs["Scale"].default_value = (14.0, 14.0, 2.5)
        put(nt, mp.inputs["Vector"], co)
        fib = noise(nt, mp.outputs["Vector"], 3.0, 6, 0.65)
        col = mix(nt, m_(nt, "MULTIPLY", smooth(nt, fib, 0.4, 0.75), 0.3), col, tuple(min(1.0, c * 1.25) for c in base[:3]) + (1,))
        height = m_(nt, "ADD", height, m_(nt, "MULTIPLY", fib, 0.5))
    if f["rind_w"] > 0:
        col = mix(nt, smooth(nt, rho, 1 - f["rind_w"] - 0.02, 1 - f["rind_w"] + 0.01), col, lin(f["rind"]))
    put(nt, bsdf.inputs["Base Color"], col)
    wet = p["sound"] in ("wet", "crisp")
    bsdf.inputs["Roughness"].default_value = 0.3 if wet else 0.6
    setin(bsdf, "Subsurface Weight", 0.15 if wet else 0.05)
    if wet:  # juicy sheen on the cut face
        setin(bsdf, "Coat Weight", 0.4)
        setin(bsdf, "Coat Roughness", 0.05)
    setin(bsdf, "Normal", bump(nt, height, 0.12, 0.01))
    return m


# ---------------------------------------------------------------- object + pieces
def base_bmesh(p):
    rx, ry, h = p["size"]
    bm = bmesh.new()
    if p["shape"] == "egg":
        bmesh.ops.create_uvsphere(bm, u_segments=72, v_segments=64, radius=1.0)
        for v in bm.verts:
            x, y, z = v.co
            k = 1 - p["taper"] * z
            v.co = Vector((x * rx * k, y * ry * k, (z + 1) * h / 2))
    elif p["shape"] == "cylinder":
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=72, radius1=1.0, radius2=1.0, depth=1.0)
        for v in bm.verts:
            x, y, z = v.co
            k = 1 - p["taper"] * (z + 0.5)
            v.co = Vector((x * rx * k, y * ry * k, (z + 0.5) * h))
        caps = [e for f in bm.faces if len(f.verts) > 4 for e in f.edges]
        bmesh.ops.bevel(bm, geom=caps, offset=min(rx * (1 - p["taper"]), h) * 0.18, offset_type="OFFSET",
                        segments=5, profile=0.5, affect="EDGES", clamp_overlap=True)
    else:
        bmesh.ops.create_cube(bm, size=1.0)
        for v in bm.verts:
            v.co = Vector((v.co.x * 2 * rx, v.co.y * 2 * ry, (v.co.z + 0.5) * h))
        bmesh.ops.bevel(bm, geom=list(bm.edges), offset=min(rx, ry) * 0.3, offset_type="OFFSET",
                        segments=6, profile=0.5, affect="EDGES", clamp_overlap=True)
    for f in bm.faces:
        f.smooth, f.material_index = True, 0
    return bm


def cut_piece(base, planes):
    """Copy the whole object and keep the part on the + side of every (point, normal) plane; caps get material 1."""
    bm = base.copy()
    for co, no in planes:
        bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=co, plane_no=no, clear_inner=True)
        # the mesh is closed before each cut, so every open edge now belongs to this cut (geom_cut misses some)
        edges = [e for e in bm.edges if e.is_boundary]
        if not edges:
            continue
        for f in bmesh.ops.holes_fill(bm, edges=edges, sides=0)["faces"]:
            f.material_index, f.smooth = 1, False
        for e in edges:
            e.smooth = False
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return bm


def plane_normal(slope):
    """Cut plane z = b - ax*x - ay*y  ->  unit normal pointing up."""
    return Vector((slope[0], slope[1], 1.0)).normalized()


def build_pieces(p, cut_info, mats):
    """Pre-cut every piece. Piece j sits between horizontal cut j (below) and j+1 (above); split objects get halves.

    Each piece is rotated so its local Z follows its cut planes, so the seam gap can open along the cut direction.
    """
    split = cut_info[0]["kind"] == "lengthwise"
    hz = sorted((c for c in cut_info if c["kind"] == "horizontal"), key=lambda c: c["z"])  # bottom -> top
    n_h = len(hz)
    m = Vector((math.cos(cut_info[0]["azimuth"]), math.sin(cut_info[0]["azimuth"]), 0)) if split else Vector((1, 0, 0))
    base = base_bmesh(p)
    pieces = []
    for j in range(n_h + 1):
        below, above = (hz[j - 1] if j > 0 else None), (hz[j] if j < n_h else None)
        normals = [plane_normal(c["slope"]) for c in (below, above) if c]
        n_avg = sum(normals, Vector()).normalized() if normals else UP.copy()
        q0 = UP.rotation_difference(n_avg)
        for side in ((-1, 1) if split else (0,)):
            planes = []
            # tiny offsets so a cut never lands exactly on a vertex ring/meridian (degenerate faces break the caps)
            if side:
                planes.append((m * 1.3e-4, m * side))
            if below:
                planes.append((Vector((0, 0, below["z"] + 1.3e-4)), plane_normal(below["slope"])))
            if above:
                planes.append((Vector((0, 0, above["z"] + 1.3e-4)), -plane_normal(above["slope"])))
            bm = cut_piece(base, planes)
            if not bm.verts:  # can't happen for convex shapes (every plane passes through the axis), but never crash
                bm.free()
                continue
            co = [v.co.copy() for v in bm.verts]
            inv = q0.inverted()
            local = [inv @ c for c in co]
            lo = Vector([min(c[i] for c in local) for i in range(3)])
            hi = Vector([max(c[i] for c in local) for i in range(3)])
            c_local = (lo + hi) / 2
            center = q0 @ c_local
            name = f"Slice_{j:03d}" + {-1: "_L", 1: "_R", 0: ""}[side]
            obj = mesh_obj(name, bm, mats)
            obj.data.attributes.new("src_co", "FLOAT_VECTOR", "POINT").data.foreach_set("vector", [c for v in co for c in v])
            obj.data.transform(inv.to_matrix().to_4x4())
            obj.data.transform(Matrix.Translation(-c_local))
            obj.location = center
            obj.rotation_mode = "QUATERNION"
            obj.rotation_quaternion = q0
            pieces.append({"obj": obj, "j": j, "side": side, "center": center, "q0": q0, "n": n_avg, "th": hi.z - lo.z,
                           "below": below, "above": above, "verts": [c - c_local for c in local]})
    base.free()
    return pieces, n_h, split, m


# ---------------------------------------------------------------- set, knife, text
def build_set(p):
    rx, ry, h = p["size"]
    board_w, board_d, board_t = max(4.4, rx * 2 + 3.4), max(2.8, ry * 2 + 1.6), 0.22
    wood, nt, bsdf = new_mat("Wood")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (0.35, 6.0, 1.0)
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    grain = noise(nt, mp.outputs["Vector"], 3.0, 8, 0.65)
    col = mix(nt, smooth(nt, grain, 0.35, 0.75), lin("#d9b48a"), lin("#b88a5e"))
    put(nt, bsdf.inputs["Base Color"], col)
    bsdf.inputs["Roughness"].default_value = 0.5
    setin(bsdf, "Normal", bump(nt, grain, 0.12))
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * board_w, v.co.y * board_d, (v.co.z - 0.5) * board_t))
    bmesh.ops.bevel(bm, geom=list(bm.edges), offset=0.05, offset_type="OFFSET", segments=4, profile=0.5, affect="EDGES")
    for f in bm.faces:
        f.smooth = True
    mesh_obj("Board", bm, [wood])

    floor, nt, bsdf = new_mat("Floor")
    ftc = nt.nodes.new("ShaderNodeTexCoord")
    fd = nt.nodes.new("ShaderNodeVectorMath")
    fd.operation = "LENGTH"
    nt.links.new(ftc.outputs["Object"], fd.inputs[0])
    put(nt, bsdf.inputs["Base Color"], mix(nt, smooth(nt, fd.outputs["Value"], 5.0, 16.0), lin("#4a5560"), lin("#0d1013")))
    bsdf.inputs["Roughness"].default_value = 0.8
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=60)
    for v in bm.verts:
        v.co.z = -board_t
    mesh_obj("Floor", bm, [floor])

    world = bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    try:
        world.use_nodes = True
    except Exception:
        pass
    # camera sees a near-black backdrop; lighting/reflections see a soft studio sky so the steel blade isn't black
    wnt = world.node_tree
    bg = next(n for n in wnt.nodes if n.type == "BACKGROUND")
    tc = wnt.nodes.new("ShaderNodeTexCoord")  # world "Generated" = view direction, z = up
    sep = wnt.nodes.new("ShaderNodeSeparateXYZ")
    wnt.links.new(tc.outputs["Generated"], sep.inputs[0])
    up = smooth(wnt, sep.outputs["Z"], -0.1, 0.8)
    lit = mix(wnt, up, lin("#1a1d21"), lin("#b9c0c8"))
    is_cam = wnt.nodes.new("ShaderNodeLightPath").outputs["Is Camera Ray"]
    put(wnt, bg.inputs["Color"], mix(wnt, is_cam, lit, lin("#0d1013")))
    bg.inputs["Strength"].default_value = 0.6

    for name, loc, energy, size in (("Key", (-4.5, -3.8, 5.5), 1400, 4.0), ("Fill", (4.5, -3.0, 2.5), 260, 3.0),
                                    ("Rim", (1.0, 4.5, 4.5), 700, 3.0)):
        ld = bpy.data.lights.new(name, "AREA")
        ld.energy, ld.size = energy, size
        lo = link(bpy.data.objects.new(name, ld))
        lo.location = loc
        aim(lo, (0, 0, h * 0.45))
    return board_w, board_d


def build_knife(p):
    rx, ry, h = p["size"]
    L, W = max(2.8, 2.6 * rx + 0.9), 0.62
    steel, _, bsdf = new_mat("Steel")
    bsdf.inputs["Base Color"].default_value = lin("#e2e5e9")
    bsdf.inputs["Metallic"].default_value, bsdf.inputs["Roughness"].default_value = 0.9, 0.24
    handle, nt, hb = new_mat("Handle")
    put(nt, hb.inputs["Base Color"], mix(nt, smooth(nt, noise(nt, nt.nodes.new("ShaderNodeTexCoord").outputs["Object"], 8), 0.4, 0.7), lin("#3a2a1e"), lin("#24180f")))
    hb.inputs["Roughness"].default_value = 0.45
    rig = link(bpy.data.objects.new("KnifeRig", None))
    rig.rotation_mode = "QUATERNION"
    # blade in the local XY plane: heel at x=0, tip at x=-L, cutting edge on y=0, spine at y=W, thin wedge in z
    bm = bmesh.new()
    pts = [(0, 0), (-L * 0.8, 0.0), (-L, W * 0.55), (-L * 0.75, W), (0, W)]
    top = [bm.verts.new((x, y, 0.016 if y > 0.05 else 0.002)) for x, y in pts]
    bot = [bm.verts.new((x, y, -0.016 if y > 0.05 else -0.002)) for x, y in pts]
    bm.faces.new(top)
    bm.faces.new(list(reversed(bot)))
    for i in range(len(pts)):
        bm.faces.new((top[i], top[(i + 1) % len(pts)], bot[(i + 1) % len(pts)], bot[i]))
    blade = mesh_obj("Blade", bm, [steel])
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector(((v.co.x + 0.5) * 1.15 + 0.06, (v.co.y + 0.5) * W * 0.7 + W * 0.18, v.co.z * 0.13))
    bmesh.ops.bevel(bm, geom=list(bm.edges), offset=0.05, offset_type="OFFSET", segments=4, profile=0.5, affect="EDGES")
    for f in bm.faces:
        f.smooth = True
    hnd = mesh_obj("Handle", bm, [handle])
    for o in (blade, hnd):
        o.parent = rig
    return rig, L, W


def build_text(cam, p, cuts, impacts, frame_end, watermark, vfov):
    half = math.tan(vfov / 2)  # visible half-height at 1 unit in front of the camera
    bold = regular = None
    for path, slot in (("C:/Windows/Fonts/arialbd.ttf", "bold"), ("C:/Windows/Fonts/arial.ttf", "regular")):
        if Path(path).exists():
            f = bpy.data.fonts.load(path)
            bold, regular = (f, regular) if slot == "bold" else (bold, f)

    def text(name, body, y, size, mat, font, spacing=1.0):
        cu = bpy.data.curves.new(name, "FONT")
        cu.body, cu.size, cu.align_x, cu.align_y, cu.space_character = body, size * half, "CENTER", "CENTER", spacing
        if font:
            cu.font = font
        o = link(bpy.data.objects.new(name, cu))
        o.data.materials.append(mat)
        o.parent = cam
        o.location = (0, y * half, -1.0)
        o.visible_shadow = False
        return o

    white = emission_mat("Text white", (1, 1, 1, 1), 1.7)  # AgX maps 1.0 to light grey
    lime = emission_mat("Text lime", lin("#b8e06a"), 1.0)
    soft = emission_mat("Text soft", (0.75, 0.75, 0.75, 1), 1.0)
    text("Label", p["label"], 0.80, 0.042, lime, regular or bold, 1.35)
    text("Watermark", watermark, -0.86, 0.05, soft, bold or regular)
    width = max(2, len(str(cuts)))
    starts = [1] + impacts
    for k in range(cuts + 1):
        o = text(f"Counter_{k:03d}", f"{k:0{width}d} / {cuts:0{width}d} CUTS", 0.66, 0.085, white, bold or regular)
        end = starts[k + 1] - 1 if k < cuts else frame_end
        for attr in ("hide_render", "hide_viewport"):
            setattr(o, attr, k != 0)
            o.keyframe_insert(attr, frame=1)
            if k:
                setattr(o, attr, False)
                o.keyframe_insert(attr, frame=starts[k])
            if end < frame_end:
                setattr(o, attr, True)
                o.keyframe_insert(attr, frame=end + 1)


# ---------------------------------------------------------------- animation
def animate_knife(rig, L, W, p, cut_info, impacts, frame_end):
    """The blade always lies in the cut plane: tilted cuts tilt the knife, the angled split turns the chop."""
    rx, ry, h = p["size"]
    chop_base = Matrix(((0, 0, 1), (1, 0, 0), (0, 1, 0))).to_quaternion()  # blade in the vertical YZ plane, edge down
    keys = []  # (frame, location, quaternion)

    def plane_z(c, x):  # height of the cut plane along the knife's path (y = 0)
        return c["z"] - c["slope"][0] * x

    def contact_x(c, side):  # where that path meets the skin on the right (+1) or left (-1)
        x = plan.radius_x(p, c["z"])
        for _ in range(5):
            x = plan.radius_x(p, plane_z(c, side * x))
        return x

    def sweep(c, tip_x):
        q = UP.rotation_difference(plane_normal(c["slope"]))
        return Vector((tip_x, 0, plane_z(c, tip_x))) - q @ Vector((-L, W / 2, 0)), q

    first = cut_info[0]
    gap = 0.0
    if first["kind"] == "lengthwise":
        f, qz = impacts[0], Quaternion(UP, first["azimuth"])
        qc = qz @ chop_base
        chop = lambda edge_z: qz @ Vector((0, ry + 0.3, edge_z))  # noqa: E731
        keys += [(1, chop(h + 4.0), qc), (f - 9, chop(h + 1.2), qc), (f - 3, chop(h + 0.45), qc),
                 (f, chop(h), qc), (f + 2, chop(0.0), qc), (f + 3, chop(0.3), qc)]
        gap = 0.03
    else:
        loc, q = sweep(first, contact_x(first, 1) + 0.9)
        keys += [(1, loc + Vector((1.6, 0, 3.0)), q), (impacts[0] - 9, loc + Vector((0, 0, 0.4)), q)]
    for c, f in zip(cut_info, impacts):
        if c["kind"] != "horizontal":
            continue
        r, rl = contact_x(c, 1) + gap, contact_x(c, -1) + gap
        for frame, tip in ((f - 2, r + 0.35), (f, r), (f + 2, -rl - 0.12), (f + 3, r + 0.2)):
            keys.append((frame, *sweep(c, tip)))
    last = keys[-1]
    keys += [(impacts[-1] + 10, last[1] + Vector((2.0, 0, 2.8)), last[2]), (frame_end, last[1] + Vector((2.0, 0, 2.8)), last[2])]
    prefs = bpy.context.preferences.edit
    old = prefs.keyframe_new_interpolation_type
    prefs.keyframe_new_interpolation_type = "LINEAR"  # crisp mechanical knife motion
    prev = None
    for f, loc, q in sorted(keys, key=lambda k: k[0]):
        q = q.copy()
        if prev is not None:
            q.make_compatible(prev)  # turn the short way between strokes
        rig.location, rig.rotation_quaternion = loc, q
        rig.keyframe_insert("location", frame=f)
        rig.keyframe_insert("rotation_quaternion", frame=f)
        prev = q
    prefs.keyframe_new_interpolation_type = old


def key_pose(obj, f, loc, quat=None, scale=None):
    obj.location = loc
    obj.keyframe_insert("location", frame=f)
    if quat is not None:
        obj.rotation_quaternion = quat
        obj.keyframe_insert("rotation_quaternion", frame=f)
    if scale is not None:
        obj.scale = scale
        obj.keyframe_insert("scale", frame=f)


def animate_pieces(pieces, n_h, split, m, p, cut_info, impacts, fall_start, rng):
    """Seams open when the blade passes (gaps along each cut's own direction), then every slice topples."""
    h = p["size"][2]
    g = min(0.012, 0.25 * h / (n_h + 1))  # cut seam gap
    g1 = 0.05                              # lengthwise seam gap
    frame_of = {id(c): f for c, f in zip(cut_info, impacts)}
    split_frame = impacts[0] if split else None

    def pose(pc, x_open, bo, to):
        th = max(pc["th"], 1e-4)
        loc = pc["center"] + m * (pc["side"] * g1 / 2 * x_open) + pc["n"] * (g / 4 * (bo - to))
        return loc, Vector((1, 1, max(0.2, (th - g / 2 * (bo + to)) / th)))

    for pc in pieces:
        obj = pc["obj"]
        events = []
        if split_frame and pc["side"]:
            events.append((split_frame, "x"))
        if pc["below"]:
            events.append((frame_of[id(pc["below"])], "bo"))
        if pc["above"]:
            events.append((frame_of[id(pc["above"])], "to"))
        state = {"x": 0, "bo": 0, "to": 0}
        loc, sc = pose(pc, 0, 0, 0)
        key_pose(obj, 1, loc, pc["q0"], sc)
        for f, what in sorted(events):  # closed at contact, open 2 frames later when the blade is through
            loc, sc = pose(pc, state["x"], state["bo"], state["to"])
            key_pose(obj, f, loc, scale=sc)
            state[what] = 1
            loc, sc = pose(pc, state["x"], state["bo"], state["to"])
            key_pose(obj, f + 2, loc, scale=sc)
        pc["start"], pc["scale"] = loc.copy(), sc.copy()
    return animate_fall(pieces, p, n_h, fall_start, rng, split, m), g


TOPPLE, TILT_MID = 14, math.radians(62)  # frames for the stack to roll over, stack angle when slices start to fan


def animate_fall(pieces, p, n_h, fall_start, rng, split, m):
    """Each half (or the whole stack) rolls over its base edge, staying on the board, then the slices fan out.

    Poses are computed for every frame (linear keys), so nothing floats or sinks between keyframes.
    """
    import numpy as np

    h = p["size"][2]
    if split:  # halves fall away from the split, turned towards the camera diagonal so they stay in frame
        beta = math.atan2(m.y, m.x) + math.radians(35)
    else:      # whole stack (group -1): away from the camera, so the slices lie down showing their cut faces
        beta = math.radians(242 + rng.uniform(-12, 18))  # ~camera forward: the row recedes and stays in frame
    # split halves end leaning (lengthwise face up); whole slices lie down overlapped like fanned cards
    lean, spread, settle = (76, 1.3, 6) if split else (155, 2.4, 10)
    d_right = Vector((math.cos(beta), math.sin(beta), 0))
    groups = {}
    for pc in pieces:
        groups.setdefault(1 if (split and pc["side"] > 0) else -1, []).append(pc)

    def rot(axis, theta):
        return np.array(Matrix.Rotation(theta, 3, axis))

    def lowest(R, pc, loc, scale):  # R: world rotation of the piece (fall @ cut orientation)
        return float(((pc["np"] * scale) @ R.T)[:, 2].min()) + loc[2]

    prefs = bpy.context.preferences.edit
    old = prefs.keyframe_new_interpolation_type
    prefs.keyframe_new_interpolation_type = "LINEAR"
    landings = []
    r_piv = plan.radius_x(p, 0.08 * h)  # outer edge of the base the stack stands on (an egg's bottom is narrow)
    for sign, members in groups.items():
        d = d_right * sign
        axis = UP.cross(d).normalized()
        pivot = np.array(d * r_piv)
        for pc in members:
            pc["np"] = np.array([tuple(v) for v in pc["verts"]])
            jit = rng.uniform(-1.5, 1.5)
            pc["q_final"] = Quaternion(UP, math.radians(rng.uniform(-2.5, 2.5))) @ Quaternion(axis, math.radians(lean + jit))
            low = float((pc["np"] @ np.array(pc["q_final"].to_matrix()).T)[:, 2].min())
            spot = d * (r_piv + pc["center"].z * spread) + Vector((-d.y, d.x, 0)) * rng.uniform(-0.03, 0.03)
            pc["final"] = Vector((spot.x, spot.y, -low + 0.003))  # height in the stack becomes distance along the board
            pc["land"] = fall_start + TOPPLE + settle + round(4 * pc["j"] / max(1, n_h)) + rng.randint(0, 1)
            pc["prev_q"] = Quaternion()
        # phase 1: rigid roll, accelerating like gravity, dropped so the lowest point always touches the board
        for k in range(TOPPLE + 1):
            theta = TILT_MID * (k / TOPPLE) ** 2
            R = rot(axis, theta)
            q_fall = Matrix.Rotation(theta, 3, axis).to_quaternion()
            locs = {id(pc): pivot + R @ (np.array(pc["start"]) - pivot) for pc in members}
            drop = min(lowest(R @ np.array(pc["q0"].to_matrix()), pc, locs[id(pc)], np.array(pc["scale"])) for pc in members)
            for pc in members:
                loc = locs[id(pc)]
                loc[2] -= drop
                q = q_fall @ pc["q0"]
                if k:
                    q.make_compatible(pc["mid_q"])
                key_pose(pc["obj"], fall_start + k, Vector(loc), q, pc["scale"] if k < TOPPLE else Vector((1, 1, 1)))
                pc["mid_loc"], pc["mid_q"] = Vector(loc), q
        # phase 2: each slice slides/fans to its resting pose, never below the board, small bounce at the end
        for pc in members:
            f_mid, land = fall_start + TOPPLE, pc["land"]
            q_end = pc["q_final"].copy()
            q_end.make_compatible(pc["mid_q"])
            for f in range(f_mid + 1, land + 1):
                u = (f - f_mid) / (land - f_mid)
                e = 1 - (1 - u) ** 2
                q = pc["mid_q"].slerp(q_end, e)
                loc = pc["mid_loc"].lerp(pc["final"], e)
                loc.z = max(loc.z, -float((pc["np"] @ np.array(q.to_matrix()).T)[:, 2].min()) + 0.003)
                key_pose(pc["obj"], f, loc, q)
            bump = 0.03 * (1 - pc["j"] / max(1, n_h)) + 0.01
            key_pose(pc["obj"], land + 2, pc["final"] + Vector((0, 0, bump)), q_end)
            key_pose(pc["obj"], land + 4, pc["final"], q_end)
            landings.append(land)
    prefs.keyframe_new_interpolation_type = old
    return sorted(landings)


# ---------------------------------------------------------------- scene / render setup
def setup_render(cfg, frame_end):
    sc = bpy.context.scene
    r = cfg["render"]
    if r.get("engine", "eevee") == "cycles":
        sc.render.engine = "CYCLES"
        sc.cycles.samples = int(r.get("samples", 64))
        sc.cycles.use_denoising = True
    else:
        for eng in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
            try:
                sc.render.engine = eng
                break
            except TypeError:
                continue
        sc.eevee.taa_render_samples = int(r.get("samples", 48))
        for attr in ("use_raytracing", "use_shadows"):
            if hasattr(sc.eevee, attr):
                setattr(sc.eevee, attr, True)
    sc.render.resolution_x, sc.render.resolution_y = int(r["width"]), int(r["height"])
    sc.render.resolution_percentage = 100
    sc.render.fps = int(r["fps"])
    sc.frame_start, sc.frame_end = 1, frame_end
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    sc.render.image_settings.compression = 15
    sc.render.film_transparent = False
    try:
        sc.view_settings.view_transform = "AgX"
    except TypeError:
        pass
    for look in ("AgX - Punchy", "AgX - Medium High Contrast"):
        try:
            sc.view_settings.look = look
            break
        except TypeError:
            continue


def build_camera(p, rng=None):
    rx, ry, h = p["size"]
    cam_d = bpy.data.cameras.new("Camera")
    cam_d.sensor_fit, cam_d.sensor_height, cam_d.lens = "VERTICAL", 24.0, 50.0
    vfov = 2 * math.atan(12.0 / 50.0)
    view_h = max(2.8 * h, 6.0 * rx)  # visible height around the object
    dist = (view_h / 2) / math.tan(vfov / 2)
    az, el = math.radians(rng.uniform(-38, -18) if rng else -28), math.radians(rng.uniform(13, 20) if rng else 16)
    target = Vector((0, 0, 0.3 * h))
    cam = link(bpy.data.objects.new("Camera", cam_d))
    cam.location = target + dist * Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    aim(cam, target)
    cam_d.clip_start, cam_d.clip_end = 0.05, 200
    bpy.context.scene.camera = cam
    return cam, vfov


def clear_scene():
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras, bpy.data.curves):
        for item in list(coll):
            coll.remove(item)


def still(path, frame):
    sc = bpy.context.scene
    sc.frame_set(frame)
    sc.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def build(run: Path):
    cfg = json.loads((run / "config.json").read_text("utf-8"))
    cuts = plan.validate_cuts(cfg["cuts"])
    p = plan.resolve(cfg["object"])
    tl = plan.timeline(cuts)
    impacts, fall_start, frame_end = tl["impacts"], tl["fall_start"], tl["frame_end"]
    seed = int(cfg.get("seed") or 0)
    rng = random.Random(seed)  # cut angles, split direction, camera, fall: new every run, reproducible by seed
    cut_info = plan.cut_list(p, cuts, rng)

    clear_scene()
    setup_render(cfg, frame_end)
    mats = [skin_material(p), flesh_material(p)]
    build_set(p)
    pieces, n_h, split, m = build_pieces(p, cut_info, mats)
    if p.get("wick"):
        top = max(pieces, key=lambda pc: pc["j"])["obj"]
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.025, radius2=0.02, depth=0.22)
        wick_mat, _, wb = new_mat("Wick")
        wb.inputs["Base Color"].default_value = lin("#1a1612")
        wick = mesh_obj("Wick", bm, [wick_mat])
        wick.location = Vector((0, 0, p["size"][2] + 0.09))
        bpy.context.view_layer.update()
        wick.parent = top
        wick.matrix_parent_inverse = top.matrix_world.inverted()  # keep its place on the (rotated) top piece
    rig, L, W = build_knife(p)
    animate_knife(rig, L, W, p, cut_info, impacts, frame_end)
    landings, gap = animate_pieces(pieces, n_h, split, m, p, cut_info, impacts, fall_start, rng)
    cam, vfov = build_camera(p, rng)
    build_text(cam, p, cuts, impacts, frame_end, cfg.get("watermark", ""), vfov)

    fps = int(cfg["render"]["fps"])
    info = {
        "object": cfg["object"], "label": p["label"], "cuts": cuts, "fps": fps, "frame_start": 1, "frame_end": frame_end,
        "duration": frame_end / fps, "pattern": "split" if split else "parallel", "pieces": len(pieces), "sound": p["sound"],
        "fall_start": fall_start, "landings": landings, "seam_gap": round(gap, 4), "seed": seed,
        "impacts": [{"cut": i + 1, "frame": f, "time": round((f - 1) / fps, 4), "kind": c["kind"], "direction": c["direction"]}
                    for i, (c, f) in enumerate(zip(cut_info, impacts))],
    }
    (run / "impacts.json").write_text(json.dumps(info, indent=2), "utf-8")
    bpy.context.scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=str(run / "scene.blend"))

    prev = run / "previews"
    prev.mkdir(exist_ok=True)
    mid = impacts[(cuts - 1) // 2] if cuts > 1 else impacts[0]
    still(prev / "opening.png", 12)
    still(prev / "middle_cut.png", mid)
    still(prev / "fall.png", min(frame_end, max(landings) + 12))


def render_frames(run: Path):
    sc = bpy.context.scene
    (run / "frames").mkdir(exist_ok=True)
    sc.render.filepath = str(run / "frames" / "####")
    sc.render.use_overwrite = False   # keep frames that already exist -> resume
    sc.render.use_placeholder = True
    bpy.ops.render.render(animation=True)


def test(out: Path):
    clear_scene()
    setup_render({"render": {"width": 540, "height": 960, "fps": 24, "samples": 16}}, 1)
    p = plan.resolve("Papaya")
    build_set(p)
    mats = [skin_material(p), flesh_material(p)]
    bm = base_bmesh(p)
    obj = mesh_obj("Test", bm, mats)
    obj.data.attributes.new("src_co", "FLOAT_VECTOR", "POINT").data.foreach_set("vector", [c for v in obj.data.vertices for c in v.co])
    cam, vfov = build_camera(p)
    build_text(cam, p, 1, [29], 30, "setup check", vfov)
    out.mkdir(parents=True, exist_ok=True)
    still(out / "preview.png", 1)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = dict(zip(argv[::2], argv[1::2]))
    mode, run = a.get("--mode"), Path(a.get("--run", "."))
    if mode == "preview":
        build(run)
    elif mode == "render":
        render_frames(run)
    elif mode == "test":
        test(run)
    else:
        raise SystemExit(f"unknown mode {mode!r}")
    print("SCENE_OK", mode)


main()
