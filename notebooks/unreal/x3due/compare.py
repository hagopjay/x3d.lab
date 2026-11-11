"""Judge Unreal's stills against X_ITE (and Cycles) with the lab's own referee, plus what the lab does not have yet.

Silhouettes and colour use x3dlab.formats (compare_pngs: `match`, the 2-pixel-forgiving silhouette F-score, and
colour_kind with its 64/255 threshold), so Unreal's numbers sit on the same scale as the Foundry's glTF numbers.
Added here: a structural similarity index (SSIM) on luminance, the viewpoint wrappers X_ITE needs to show the
same view, and the contact sheet.
"""

from __future__ import annotations

import io
import math
import re

from . import convert as C

KEY = "1 0 1"     # x3dlab.foundry.KEY: the flat magenta background every comparison picture is taken on


def _gauss(sigma=1.5, r=5):
    import numpy as np
    x = np.arange(-r, r + 1)
    g = np.exp(-(x * x) / (2 * sigma * sigma))
    return g / g.sum()


def _blur(a, g):
    import numpy as np
    r = len(g) // 2
    p = np.pad(a, ((0, 0), (r, r)), mode="reflect")
    h = sum(g[i] * p[:, i:i + a.shape[1]] for i in range(len(g)))
    p = np.pad(h, ((r, r), (0, 0)), mode="reflect")
    return sum(g[i] * p[i:i + a.shape[0], :] for i in range(len(g)))


def luminance(png: bytes, size=None):
    import numpy as np
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    if size and im.size != tuple(size):
        im = im.resize(tuple(size), Image.LANCZOS)
    a = np.asarray(im).astype(float) / 255.0
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


def ssim(a, b, mask=None) -> float:
    """Wang et al. 2004 SSIM (Gaussian window sigma 1.5, K1 0.01, K2 0.03, data range 1) on two luminance arrays.
    `mask`: average only where it is True (e.g. where either engine drew something)."""
    import numpy as np
    if a.shape != b.shape:
        raise ValueError("sizes differ: %s vs %s" % (a.shape, b.shape))
    g = _gauss()
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    mu_a, mu_b = _blur(a, g), _blur(b, g)
    saa = _blur(a * a, g) - mu_a ** 2
    sbb = _blur(b * b, g) - mu_b ** 2
    sab = _blur(a * b, g) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + c1) * (2 * sab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (saa + sbb + c2))
    if mask is not None and mask.any():
        return float(m[mask].mean())
    return float(m.mean())


def judge(ref_png: bytes, test_png: bytes, formats=None) -> dict:
    """formats.compare_pngs (match, iou, coverage, colour delta) + colour_kind + SSIM where either drew."""
    out = {}
    if formats is not None:
        out = dict(formats.compare_pngs(ref_png, test_png))
        out["colour_kind"] = formats.colour_kind(out)
    try:
        import numpy as np
        a, b = luminance(ref_png), luminance(test_png, None)
        if a.shape != b.shape:
            from PIL import Image
            h, w = a.shape
            b = luminance(test_png, (w, h))
        ka, kb = key_mask(ref_png, a.shape), key_mask(test_png, a.shape)
        out["ssim"] = round(ssim(a, b, ka | kb), 4)
    except Exception as exc:      # pragma: no cover - reported, not fatal
        out["ssim_error"] = str(exc)[:200]
    return out


def key_mask(png: bytes, shape=None, key=(255, 0, 255), tol=60):
    """Covered pixels on the magenta key background (as x3dlab.browser.silhouette 'key')."""
    import numpy as np
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    if shape and im.size != (shape[1], shape[0]):
        im = im.resize((shape[1], shape[0]), Image.NEAREST)
    rgb = np.asarray(im).astype(int)
    return np.abs(rgb - np.array(key)).sum(axis=-1) > tol


def verdict(j: dict, pass_match=0.9, warn_match=0.7) -> str:
    m = j.get("match")
    if m is None:
        return "SKIP"
    if m >= pass_match and not j.get("colour_kind"):
        return "PASS"
    return "WARN" if m >= warn_match else "FAIL"


# ---------------------------------------------------------------------- scenes X_ITE can show
def _fmt(v):
    return " ".join("%.6g" % x for x in v)


def viewpoint_wrapper(manifest_vp: dict, inline_url: str, headlight: bool, key: str | None = KEY, version="4.0") -> str:
    """A small X3D that binds one of the original's viewpoints (as a world-space Viewpoint) and Inlines the scene (or a
    glTF). Viewpoints inside an Inline are not bound, so this is how X_ITE is made to show viewpoint k (the same trick
    as x3dlab.formats.inline_wrapper, which shows the first one)."""
    pos, rot = world_viewpoint(manifest_vp)
    bg = '<Background skyColor="%s"/>' % key if key else ""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<X3D profile="Full" version="%s"><head/><Scene>%s'
            '<NavigationInfo headlight="%s" type=\'"EXAMINE" "ANY"\'/>'
            '<Viewpoint DEF="x3dlab_view" position="%s" orientation="%s" fieldOfView="%.6g"/>'
            '<Inline url=\'"%s"\'/></Scene></X3D>\n') % (version, bg, "true" if headlight else "false", _fmt(pos), _fmt(rot),
                                                     manifest_vp.get("fieldOfView", math.pi / 4), inline_url)


def world_viewpoint(vp: dict):
    """(world position, world orientation as axis-angle) of a manifest viewpoint."""
    m = vp.get("world_matrix") or C.mat4_identity()
    R = C.axis_angle_to_mat3(vp["orientation"][:3], vp["orientation"][3])
    f = C.norm(C.mat4_dir(m, C.mat3_vec(R, (0, 0, -1))))
    u = C.norm(C.mat4_dir(m, C.mat3_vec(R, (0, 1, 0))))
    return C.mat4_point(m, vp["position"]), look_rotation(f, u)


def look_rotation(forward, up):
    """X3D SFRotation that turns the default view (-Z forward, +Y up) to (forward, up)."""
    z = C.norm(tuple(-x for x in forward))
    x = C.norm(C.cross(up, z))
    y = C.cross(z, x)
    m = ((x[0], y[0], z[0]), (x[1], y[1], z[1]), (x[2], y[2], z[2]))
    tr = m[0][0] + m[1][1] + m[2][2]
    ang = math.acos(max(-1.0, min(1.0, (tr - 1) / 2)))
    if ang < 1e-9:
        return (0.0, 0.0, 1.0, 0.0)
    if abs(ang - math.pi) < 1e-6:
        # axis from the diagonal
        ax = [math.sqrt(max(0.0, (m[i][i] + 1) / 2)) for i in range(3)]
        i = max(range(3), key=lambda k: ax[k])
        for j in range(3):
            if j != i:
                ax[j] = math.copysign(ax[j], m[i][j] + m[j][i])
        return tuple(C.norm(ax)) + (math.pi,)
    ax = (m[2][1] - m[1][2], m[0][2] - m[2][0], m[1][0] - m[0][1])
    return tuple(C.norm(ax)) + (ang,)


def auto_viewpoint(bounds, fov=0.7854, direction=(0.55, 0.35, 1.0)):
    """A Viewpoint that frames `bounds` ((lo), (hi)) from the front-right-above, for glTF inputs that bring no camera."""
    lo, hi = bounds
    c = tuple((a + b) / 2 for a, b in zip(lo, hi))
    r = max(1e-3, math.dist(lo, hi) / 2)
    d = C.norm(direction)
    dist = r / math.sin(fov / 2) * 1.05
    pos = tuple(c[i] + d[i] * dist for i in range(3))
    rot = look_rotation(tuple(-x for x in d), (0, 1, 0))
    return {"type": "Viewpoint", "DEF": "x3dlab_auto", "description": "framed from the bounds", "position": pos,
            "orientation": rot, "fieldOfView": fov, "centerOfRotation": c, "world_matrix": C.mat4_identity(), "index": 0}


def gltf_control_x3d(glb_name: str, bounds, fov=0.7854) -> tuple[str, dict]:
    """An X3D scene that is just the glTF under an automatic camera with a headlight: the control input, so Unreal and
    X_ITE see the same file with no Blender step in between."""
    vp = auto_viewpoint(bounds, fov)
    pos, rot = vp["position"], vp["orientation"]
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<X3D profile="Full" version="4.0"><head>'
           '<meta name="description" content="x3d.lab control: a glTF sample shown as is"/></head><Scene>'
           '<NavigationInfo headlight="true"/><Background skyColor="0.18 0.18 0.2"/>'
           '<Viewpoint DEF="x3dlab_auto" description="auto" position="%s" orientation="%s" fieldOfView="%.6g"/>'
           '<Inline url=\'"%s"\'/></Scene></X3D>\n') % (_fmt(pos), _fmt(rot), fov, glb_name)
    return xml, vp


# ---------------------------------------------------------------------- contact sheet
def contact_sheet(rows, out_path, cell=(480, 270), title="x3d.lab · Unreal vs X_ITE vs Cycles"):
    """rows: [{"label": str, "cells": [(caption, png_bytes_or_None), ...]}] -> one PNG grid with captions."""
    from PIL import Image, ImageDraw
    ncol = max((len(r["cells"]) for r in rows), default=1)
    pad, head, cap = 8, 34, 20
    W = pad + ncol * (cell[0] + pad)
    H = head + sum(cap * 2 + cell[1] + pad for _ in rows) + pad
    sheet = Image.new("RGB", (W, H), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 10), title, fill=(235, 235, 235))
    y = head
    for r in rows:
        d.text((pad, y), r["label"][:120], fill=(200, 200, 120))
        y += cap
        for i, (caption, png) in enumerate(r["cells"]):
            x = pad + i * (cell[0] + pad)
            if png:
                try:
                    im = Image.open(io.BytesIO(png)).convert("RGB")
                    im.thumbnail(cell)
                    sheet.paste(im, (x + (cell[0] - im.size[0]) // 2, y + (cell[1] - im.size[1]) // 2))
                except Exception:
                    d.text((x + 6, y + 6), "unreadable image", fill=(230, 90, 90))
            else:
                d.rectangle([x, y, x + cell[0], y + cell[1]], outline=(80, 80, 80))
                d.text((x + 6, y + 6), "not rendered", fill=(150, 150, 150))
            d.text((x, y + cell[1] + 3), (caption or "")[:80], fill=(220, 220, 220))
        y += cell[1] + cap + pad
    sheet.save(out_path)
    return out_path


def safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s or "x").strip("_")[:60] or "x"
