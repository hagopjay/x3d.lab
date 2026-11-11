"""Stage an X3D scene for still renders: freeze its animation at a lit moment, and check each viewpoint sees the model.

Two failures seen on the first A100 run (11 Nov 2026) that are not rendering bugs:

- **A Material Odyssey was pitch black.** All 74 of its lights have ``intensity="0"`` in the file; one TimeSensor fades
  them up through ScalarInterpolators during the tour. Every engine shows the time-0 state (ROUTEs are removed for
  the comparison), and at time 0 the scene is dark. ``freeze`` evaluates the interpolators that a TimeSensor drives at
  one fraction of its cycle and writes the values into the target nodes, so Blender, Unreal and X_ITE all get the same,
  lit, moment.
- **ToyCar was invisible.** The Web3D conversion scales the car by 0.0001 (about 4 cm long) while its viewpoints sit
  about 10 m away, so the car is under a pixel. ``framing`` measures how much of the frame the scene's bounds cover
  from each viewpoint; ``add_auto_viewpoint`` adds a viewpoint framed on the bounds when none of the authored ones
  shows the model, and ``pick`` puts the viewpoints that frame it first.

Pure Python (no Unreal, no Blender): tested in tests/test_ue_staging.py.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

from . import convert as C
from . import x3dscene

INTERPOLATORS = {"ScalarInterpolator": 1, "ColorInterpolator": 3, "PositionInterpolator": 3, "OrientationInterpolator": 4}
LIGHTS = ("DirectionalLight", "PointLight", "SpotLight")


# ---------------------------------------------------------------------- reading
def _routes(text):
    out = []
    for m in re.finditer(r"<ROUTE\b([^>]*)/?>", text):
        a = dict(re.findall(r'(\w+)\s*=\s*["\']([^"\']*)["\']', m.group(1)))
        if {"fromNode", "fromField", "toNode", "toField"} <= set(a):
            out.append(a)
    return out


def _field(name):
    """X3D event names to the field they set: set_intensity / intensity_changed / intensity -> intensity."""
    name = name[4:] if name.startswith("set_") else name
    return name[:-8] if name.endswith("_changed") else name


def _defs(root):
    return {el.get("DEF"): el for el in root.iter() if el.get("DEF")}


def _tag(el):
    return el.tag.rsplit("}", 1)[-1]


def drivers(text):
    """{(target DEF, field): (interpolator kind, keys, values)} for interpolators fed by a TimeSensor's fraction."""
    root = x3dscene.parse(text)
    defs = _defs(root)
    routes = _routes(text)
    fed = {r["toNode"] for r in routes
           if _field(r["fromField"]) == "fraction" and r["fromNode"] in defs and _tag(defs[r["fromNode"]]) == "TimeSensor"
           and _field(r["toField"]) == "fraction"}
    out = {}
    for r in routes:
        src = defs.get(r["fromNode"])
        if src is None or r["fromNode"] not in fed or _tag(src) not in INTERPOLATORS or _field(r["fromField"]) != "value":
            continue
        n = INTERPOLATORS[_tag(src)]
        keys = x3dscene.floats(src.get("key"), [])
        flat = x3dscene.floats((src.get("keyValue") or "").replace(",", " "), [])
        vals = [tuple(flat[i * n:(i + 1) * n]) for i in range(len(flat) // n)]
        if keys and len(vals) == len(keys):
            out[(r["toNode"], _field(r["toField"]))] = (_tag(src), keys, vals)
    return out


# ---------------------------------------------------------------------- evaluating
def _quat(axis_angle):
    x, y, z, a = axis_angle
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    s = math.sin(a / 2)
    return (math.cos(a / 2), x / n * s, y / n * s, z / n * s)


def _axis_angle(q):
    w, x, y, z = q
    w = max(-1.0, min(1.0, w))
    a = 2 * math.acos(w)
    s = math.sqrt(max(0.0, 1 - w * w))
    return (0.0, 0.0, 1.0, 0.0) if s < 1e-9 else (x / s, y / s, z / s, a)


def _slerp(r0, r1, t):
    q0, q1 = _quat(r0), _quat(r1)
    d = sum(a * b for a, b in zip(q0, q1))
    if d < 0:                                       # the shorter way round
        q1, d = tuple(-v for v in q1), -d
    if d > 0.9995:
        q = tuple(a + t * (b - a) for a, b in zip(q0, q1))
    else:
        th = math.acos(d)
        q = tuple((math.sin((1 - t) * th) * a + math.sin(t * th) * b) / math.sin(th) for a, b in zip(q0, q1))
    n = math.sqrt(sum(v * v for v in q)) or 1.0
    return _axis_angle(tuple(v / n for v in q))


def evaluate(kind, keys, vals, f):
    """The interpolator's value_changed at fraction f (X3D: linear between keys, held outside them)."""
    if f <= keys[0]:
        return vals[0]
    if f >= keys[-1]:
        return vals[-1]
    for i in range(1, len(keys)):
        if f <= keys[i]:
            k0, k1 = keys[i - 1], keys[i]
            t = 0.0 if k1 == k0 else (f - k0) / (k1 - k0)
            if kind == "OrientationInterpolator":
                return _slerp(vals[i - 1], vals[i], t)
            return tuple(a + t * (b - a) for a, b in zip(vals[i - 1], vals[i]))
    return vals[-1]


def light_total(text, f, drv=None):
    """Sum of every light's intensity at fraction f (driven value if a TimeSensor drives it, else the authored one)."""
    root = x3dscene.parse(text)
    drv = drivers(text) if drv is None else drv
    total = 0.0
    for el in root.iter():
        if _tag(el) not in LIGHTS or not x3dscene._bool(el.get("on"), True):
            continue
        key = (el.get("DEF"), "intensity")
        total += evaluate(*drv[key], f)[0] if key in drv else x3dscene.floats(el.get("intensity"), [1])[0]
    return total


def brightest(text, steps=500, drv=None):
    """(fraction, total) at the middle of the first stretch where the lights are within 1% of their brightest."""
    drv = drivers(text) if drv is None else drv
    fs = [i / steps for i in range(steps + 1)]
    tot = [light_total(text, f, drv) for f in fs]
    best = max(tot)
    if best <= 0:
        return 0.0, 0.0
    i = tot.index(best)
    lo = i
    while lo > 0 and tot[lo - 1] >= 0.99 * best:
        lo -= 1
    hi = i
    while hi < steps and tot[hi + 1] >= 0.99 * best:
        hi += 1
    f = fs[(lo + hi) // 2]
    return f, tot[(lo + hi) // 2]


def _fmt(v):
    return " ".join("%.6g" % x for x in v)


def _set_attr(text, def_name, field, value):
    """Set field=value on the element with DEF=def_name (first opening tag); returns (text, changed)."""
    rx = re.compile(r"<(\w+)\b[^>]*\bDEF\s*=\s*([\"'])%s\2[^>]*?/?>" % re.escape(def_name))
    m = rx.search(text)
    if not m:
        return text, False
    tag = m.group(0)
    attr = re.compile(r"(\s%s\s*=\s*)([\"'])(.*?)\2" % re.escape(field), re.S)
    if attr.search(tag):
        new = attr.sub(lambda a: "%s%s%s%s" % (a.group(1), a.group(2), value, a.group(2)), tag, count=1)
    else:
        end = len(tag) - (2 if tag.endswith("/>") else 1)
        new = tag[:end] + " %s='%s'" % (field, value) + tag[end:]
    return text[:m.start()] + new + text[m.end():], new != tag


def freeze(text, fraction="auto", dark_ratio=0.25):
    """Write every TimeSensor-driven interpolator's value at `fraction` into its target.

    fraction="auto": only when the scene is dark at time 0 (total light under `dark_ratio` of its brightest moment),
    freeze it at that brightest moment; otherwise leave it as authored. A number freezes at that fraction.
    Returns (text, report). ROUTEs are left in place (the comparison strips them later; Blender ignores them).
    """
    drv = drivers(text)
    rep = {"driven": len(drv), "fraction": None, "set": 0, "lights_t0": None, "lights_frozen": None}
    if not drv:
        rep["why"] = "no TimeSensor-driven interpolators"
        return text, rep
    t0 = light_total(text, 0.0, drv)
    rep["lights_t0"] = round(t0, 4)
    if fraction == "auto":
        f, best = brightest(text, drv=drv)
        if best <= 0 or t0 >= dark_ratio * best:
            rep["why"] = "lit at time 0 (%.3g of %.3g): left as authored" % (t0, best)
            return text, rep
        fraction = f
    fraction = float(fraction)
    out = text
    for (node, field), (kind, keys, vals) in sorted(drv.items()):
        out, changed = _set_attr(out, node, field, _fmt(evaluate(kind, keys, vals, fraction)))
        rep["set"] += int(changed)
    rep.update(fraction=round(fraction, 4), lights_frozen=round(light_total(out, fraction, drv), 4),
               why="frozen at fraction %.3f of the TimeSensor cycle" % fraction)
    return out, rep


# ---------------------------------------------------------------------- framing
def framing(vp, bounds, width=16, height=9, min_cover=0.03):
    """How viewpoint `vp` (a manifest viewpoint) sees the box `bounds` ((lo), (hi)) in world space.

    Returns {"verdict": frames | tiny | misses | inside, "cover": share of the half-height the model's bounding
    sphere spans, "off_deg": angle from the view axis to the model's centre}. "inside" means the camera is within the
    bounding sphere (a room or a hall): it can't be judged this way and counts as framing.
    """
    lo, hi = bounds
    c = tuple((a + b) / 2 for a, b in zip(lo, hi))
    r = max(1e-6, math.dist(lo, hi) / 2)
    m = vp.get("world_matrix") or C.mat4_identity()
    R = C.axis_angle_to_mat3(vp["orientation"][:3], vp["orientation"][3])
    pos = C.mat4_point(m, vp["position"])
    fwd = C.norm(C.mat4_dir(m, C.mat3_vec(R, (0, 0, -1))))
    to = tuple(c[i] - pos[i] for i in range(3))
    d = math.sqrt(sum(v * v for v in to))
    if d <= r:
        return {"verdict": "inside", "cover": None, "off_deg": None, "distance": round(d, 4), "radius": round(r, 4)}
    off = math.acos(max(-1.0, min(1.0, sum(a * b for a, b in zip(fwd, to)) / d)))
    ang = math.asin(min(1.0, r / d))
    fov = vp.get("fieldOfView") if isinstance(vp.get("fieldOfView"), (int, float)) else math.pi / 4
    half_v = fov / 2 if width >= height else math.atan(math.tan(fov / 2) * height / width)
    half_h = math.atan(math.tan(half_v) * width / height)
    cover = ang / half_v
    verdict = "misses" if off - ang > half_h else "tiny" if cover < min_cover else "frames"
    return {"verdict": verdict, "cover": round(cover, 4), "off_deg": round(math.degrees(off), 2),
            "distance": round(d, 4), "radius": round(r, 4)}


def frame_all(manifest, width=16, height=9):
    """{ue name: framing} for every Viewpoint in the manifest (names as Unreal's cameras are named)."""
    from .project import ue_name
    b = manifest.get("bounds")
    if not b:
        return {}
    return {ue_name(x3dscene.viewpoint_name(vp)): framing(vp, b, width, height)
            for vp in manifest.get("viewpoints", []) if vp.get("type") == "Viewpoint"}


def needs_auto(frames):
    return not any(f["verdict"] in ("frames", "inside") for f in frames.values())


def add_auto_viewpoint(text, bounds, fov=0.7854, min_distance=1.0):
    """Add the lab's framed viewpoint (DEF x3dlab_auto, front-right-above, the same as the glTF controls) at the end
    of the Scene, so the authored first viewpoint stays the one bound at load. Returns (text, viewpoint).

    A tiny model (ToyCar: about 4 cm) framed at 45 degrees would put the camera a few centimetres away, inside Unreal's
    10 cm near clip plane and X_ITE's (half the NavigationInfo avatar size, 12.5 cm by default), so both would draw
    nothing. The camera then stays `min_distance` metres away and narrows its field of view instead (a long lens)."""
    from .compare import auto_viewpoint
    r = max(1e-6, math.dist(bounds[0], bounds[1]) / 2)
    if r / math.sin(fov / 2) * 1.05 < min_distance:
        fov = 2 * math.asin(min(1.0, 1.05 * r / min_distance))
    vp = auto_viewpoint(bounds, fov)
    tag = '<Viewpoint DEF="x3dlab_auto" description="framed on the bounds by x3d.lab" position="%s" orientation="%s" fieldOfView="%.6g"/>' % (
        _fmt(vp["position"]), _fmt(vp["orientation"]), vp["fieldOfView"])
    if "DEF=\"x3dlab_auto\"" in text:
        return text, vp
    i = text.rfind("</Scene>")
    if i < 0:
        raise ValueError("no </Scene> in this document")
    return text[:i] + tag + "\n" + text[i:], vp


def pick(names, frames, n):
    """Up to n viewpoint names for the stills: the ones that frame the model first (authored order kept), then the
    lab's x3dlab_auto, then the rest only if nothing else is left."""
    names = list(names)
    good = [x for x in names if (frames.get(x) or {}).get("verdict", "frames") in ("frames", "inside") and x != "x3dlab_auto"]
    auto = [x for x in names if x == "x3dlab_auto"]
    rest = [x for x in names if x not in good and x not in auto]
    return (good + auto + (rest if not (good or auto) else []))[:n]
