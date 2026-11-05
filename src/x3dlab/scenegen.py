"""Deterministic X3D scene families for the stress test. Same (family, size, seed) -> same bytes.

    primitives  n shapes (Box, Sphere, Cone, Cylinder) on a grid, over a ground slab
    meshes      IndexedFaceSet tori, about n vertices in total
    lights      n lights of every type, intensities above 1 included (the X3D 3.3 path clamps them)
    materials   n material variants: diffuse, emissive, specular, transparent
    hierarchy   nested Transforms n levels deep, with DEF/USE reuse
    animated    n shapes, each spun by a TimeSensor and an OrientationInterpolator
"""

from __future__ import annotations

import math
import random

FAMILIES = ("primitives", "meshes", "lights", "materials", "hierarchy", "animated")
PALETTE = ["0.90 0.20 0.20", "0.95 0.55 0.10", "0.95 0.85 0.15", "0.35 0.80 0.30", "0.15 0.65 0.60", "0.20 0.45 0.90",
           "0.50 0.30 0.85", "0.90 0.35 0.70", "0.85 0.85 0.88", "0.25 0.25 0.30"]
GEOMS = ['<Sphere radius="0.6"/>', '<Box size="1 1 1"/>', '<Cone bottomRadius="0.6" height="1.2"/>', '<Cylinder radius="0.5" height="1.2"/>']


def _doc(title: str, body: list[str], profile: str = "Immersive", extent: float = 10.0) -> str:
    head = ['<NavigationInfo type=\'"EXAMINE" "ANY"\'/>', '<Background skyColor="0.10 0.11 0.14"/>',
            '<Viewpoint description="overview" position="0 %.1f %.1f" orientation="1 0 0 -0.45"/>' % (extent * 0.55, extent * 1.15)]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<X3D profile="%s" version="4.0">\n  <head><meta name="title" content="%s"/>'
            '<meta name="generator" content="x3dlab.scenegen"/></head>\n  <Scene>\n    %s\n  </Scene>\n</X3D>\n'
            % (profile, title, "\n    ".join(head + body)))


def _grid(i: int, side: int, sp: float = 1.6):
    gx, gz = divmod(i, side)
    return (gx - (side - 1) / 2) * sp, (gz - (side - 1) / 2) * sp


def _ground(size: float) -> str:
    return ('<Transform translation="0 -0.7 0"><Shape><Appearance><Material diffuseColor="0.55 0.55 0.58"/></Appearance>'
            '<Box size="%.1f 0.2 %.1f"/></Shape></Transform>' % (size, size))


def primitives(n: int, seed: int = 7) -> str:
    rnd = random.Random(seed)
    side = max(1, math.ceil(math.sqrt(n)))
    body = ['<DirectionalLight direction="-0.4 -1 -0.5" intensity="0.9"/>', _ground(side * 1.6 + 3)]
    for i in range(n):
        x, z = _grid(i, side)
        body.append('<Transform translation="%.2f 0 %.2f" rotation="0 1 0 %.3f"><Shape><Appearance><Material diffuseColor="%s"/>'
                    '</Appearance>%s</Shape></Transform>' % (x, z, rnd.random() * 6.283, rnd.choice(PALETTE), rnd.choice(GEOMS)))
    return _doc("primitives %d" % n, body, extent=side * 1.6 + 3)


def _torus(R: float, r: float, seg: int, ring: int) -> tuple[str, str]:
    pts, idx = [], []
    for i in range(seg):
        a = 2 * math.pi * i / seg
        for j in range(ring):
            b = 2 * math.pi * j / ring
            pts.append("%.4f %.4f %.4f" % ((R + r * math.cos(b)) * math.cos(a), r * math.sin(b), (R + r * math.cos(b)) * math.sin(a)))
    for i in range(seg):
        for j in range(ring):
            a, b = i * ring + j, ((i + 1) % seg) * ring + j
            c, d = ((i + 1) % seg) * ring + (j + 1) % ring, i * ring + (j + 1) % ring
            idx.append("%d %d %d %d -1" % (a, b, c, d))
    return " ".join(idx), ", ".join(pts)


def meshes(n: int, seed: int = 7) -> str:
    """About n vertices in total, split over up to 16 tori."""
    rnd = random.Random(seed)
    count = max(1, min(16, n // 400 or 1))
    per = max(24, n // count)
    ring = max(6, int(math.sqrt(per / 3)))
    seg = max(8, per // ring)
    side = max(1, math.ceil(math.sqrt(count)))
    body = ['<DirectionalLight direction="-0.4 -1 -0.5" intensity="0.9"/>', _ground(side * 3.2 + 3)]
    idx, pts = _torus(1.0, 0.35, seg, ring)
    for i in range(count):
        x, z = _grid(i, side, 3.2)
        body.append('<Transform translation="%.2f 0.4 %.2f" rotation="1 0 0 %.3f"><Shape><Appearance><Material diffuseColor="%s"/>'
                    '</Appearance><IndexedFaceSet solid="false" coordIndex="%s"><Coordinate point="%s"/></IndexedFaceSet></Shape></Transform>'
                    % (x, z, rnd.random(), rnd.choice(PALETTE), idx, pts))
    return _doc("meshes %d" % (count * seg * ring), body, extent=side * 3.2 + 3)


def lights(n: int, seed: int = 7) -> str:
    rnd = random.Random(seed)
    body = [_ground(14), primitives_block(9, rnd)]
    for i in range(n):
        kind = ("PointLight", "SpotLight", "DirectionalLight")[i % 3]
        inten = (0.4, 0.8, 1.0, 1.5, 2.5)[i % 5]        # >1 is legal in 4.0 and is where the 3.3 writer clamps
        col = rnd.choice(PALETTE)
        if kind == "DirectionalLight":
            body.append('<DirectionalLight DEF="L%d" direction="%.2f -1 %.2f" intensity="%.2f" color="%s"/>' % (i, rnd.uniform(-1, 1), rnd.uniform(-1, 1), inten, col))
        elif kind == "PointLight":
            body.append('<PointLight DEF="L%d" location="%.2f 3 %.2f" intensity="%.2f" color="%s" radius="30"/>' % (i, rnd.uniform(-5, 5), rnd.uniform(-5, 5), inten, col))
        else:
            body.append('<SpotLight DEF="L%d" location="%.2f 4 %.2f" direction="0 -1 0" intensity="%.2f" color="%s" cutOffAngle="0.6" beamWidth="0.4" radius="30"/>'
                        % (i, rnd.uniform(-4, 4), rnd.uniform(-4, 4), inten, col))
    return _doc("lights %d" % n, body, extent=14)


def primitives_block(n: int, rnd: random.Random) -> str:
    side = max(1, math.ceil(math.sqrt(n)))
    parts = []
    for i in range(n):
        x, z = _grid(i, side, 2.0)
        parts.append('<Transform translation="%.2f 0 %.2f"><Shape><Appearance><Material diffuseColor="%s"/></Appearance>%s</Shape></Transform>'
                     % (x, z, rnd.choice(PALETTE), rnd.choice(GEOMS)))
    return "<Group>%s</Group>" % "".join(parts)


def materials(n: int, seed: int = 7) -> str:
    rnd = random.Random(seed)
    side = max(1, math.ceil(math.sqrt(n)))
    body = ['<DirectionalLight direction="-0.4 -1 -0.5" intensity="0.9"/>', '<PointLight location="0 5 5" intensity="0.6"/>',
            _ground(side * 1.6 + 3)]
    for i in range(n):
        x, z = _grid(i, side)
        kind = i % 4
        col = rnd.choice(PALETTE)
        if kind == 0:
            mat = '<Material diffuseColor="%s"/>' % col
        elif kind == 1:
            mat = '<Material diffuseColor="%s" emissiveColor="%s"/>' % (col, " ".join("%.2f" % (float(c) * 0.5) for c in col.split()))
        elif kind == 2:
            mat = '<Material diffuseColor="%s" specularColor="1 1 1" shininess="0.8"/>' % col
        else:
            mat = '<Material diffuseColor="%s" transparency="0.5"/>' % col
        body.append('<Transform translation="%.2f 0 %.2f"><Shape><Appearance>%s</Appearance><Sphere radius="0.6"/></Shape></Transform>' % (x, z, mat))
    return _doc("materials %d" % n, body, extent=side * 1.6 + 3)


def hierarchy(n: int, seed: int = 7) -> str:
    rnd = random.Random(seed)
    body = ['<DirectionalLight direction="-0.4 -1 -0.5" intensity="0.9"/>', _ground(12),
            '<Transform DEF="Unit" translation="0 0 0"><Shape DEF="Brick"><Appearance><Material diffuseColor="%s"/></Appearance>'
            '<Box size="0.6 0.3 0.6"/></Shape></Transform>' % rnd.choice(PALETTE)]
    opened = []
    for i in range(n):
        body.append('<Transform translation="0.25 0.32 0" rotation="0 1 0 0.35"><Shape USE="Brick"/>')
        opened.append("</Transform>")
    body.append("".join(opened))
    return _doc("hierarchy %d" % n, body, extent=max(10, n * 0.4))


def animated(n: int, seed: int = 7) -> str:
    rnd = random.Random(seed)
    side = max(1, math.ceil(math.sqrt(n)))
    body = ['<DirectionalLight direction="-0.4 -1 -0.5" intensity="0.9"/>', _ground(side * 1.6 + 3)]
    for i in range(n):
        x, z = _grid(i, side)
        body.append('<Transform DEF="S%d" translation="%.2f 0 %.2f"><Shape><Appearance><Material diffuseColor="%s"/></Appearance>%s</Shape></Transform>'
                    % (i, x, z, rnd.choice(PALETTE), rnd.choice(GEOMS)))
        body.append('<TimeSensor DEF="T%d" cycleInterval="%.1f" loop="true"/>' % (i, rnd.uniform(2, 8)))
        body.append('<OrientationInterpolator DEF="O%d" key="0 0.5 1" keyValue="0 1 0 0 0 1 0 3.1416 0 1 0 6.2832"/>' % i)
        body.append('<ROUTE fromNode="T%d" fromField="fraction_changed" toNode="O%d" toField="set_fraction"/>' % (i, i))
        body.append('<ROUTE fromNode="O%d" fromField="value_changed" toNode="S%d" toField="set_rotation"/>' % (i, i))
    return _doc("animated %d" % n, body, extent=side * 1.6 + 3)


_FN = {"primitives": primitives, "meshes": meshes, "lights": lights, "materials": materials, "hierarchy": hierarchy, "animated": animated}


def make(family: str, size: int, seed: int = 7) -> str:
    if family not in _FN:
        raise ValueError("family must be one of %s" % (FAMILIES,))
    return _FN[family](max(1, int(size)), seed)
