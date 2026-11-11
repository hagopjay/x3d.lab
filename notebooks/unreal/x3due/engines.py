"""The other engines, through the merged x3dlab package (not re-implemented here):

  X3D -> glTF (and USD)   x3dlab.blender "convert" task + x3dlab.formats (validator, expected losses)   -- the Foundry's leg
  X_ITE stills            x3dlab.browser (Playwright Chromium, ANGLE on Vulkan when gpu=True)
  Cycles stills           x3dlab.blender "render" task (frames the first X3D Viewpoint)

    conv = engines.to_gltf(bl, "scene.x3d", "scene.glb")       # Foundry convert + Khronos validator + losses
    shots = engines.xite_views(hb, scene_dir, "scene.x3d", manifest, size=(960, 540))
"""

from __future__ import annotations

import io
from pathlib import Path

from . import compare, x3dscene


def to_gltf(bl, src, out, fmt="glb") -> dict:
    """The Foundry's trip (x3dlab.blender_worker task 'convert'), then formats.validate_gltf and formats.losses."""
    from x3dlab import formats, hog
    r = bl.run([{"kind": "convert", "src": str(src), "format": fmt, "out": str(out)}], timeout=1800, label="unreal-convert")[0]
    res = r.get("result") or {}
    if not r.get("ok") and not res.get("error"):
        res["error"] = "blender convert failed"
    xml = Path(src).read_text(encoding="utf-8", errors="replace")
    res["losses"] = formats.losses(hog._node_types(xml), res)
    if fmt in ("glb", "gltf") and res.get("output"):
        res["validator"] = formats.validate_gltf([res["output"]]).get(res["output"], {})
    return res


def xite_views(hb, scene_dir, main_name, manifest, size=(960, 540), names=None, timeout=60) -> dict:
    """One X_ITE still per viewpoint on the magenta key background: a wrapper scene binds the viewpoint and Inlines
    the (behaviour-stripped) original, so all engines show the authored, time-0 state."""
    from x3dlab import aio
    scene_dir = Path(scene_dir)
    static = scene_dir / ("_static_" + Path(main_name).name)
    src = (scene_dir / main_name).read_text(encoding="utf-8", errors="replace")
    static.write_text(x3dscene.strip_behaviour(src), encoding="utf-8")
    hb.assets = scene_dir
    out = {}
    for vp in manifest["viewpoints"]:
        if vp["type"] != "Viewpoint":
            continue
        n = x3dscene.viewpoint_name(vp)
        if names and n not in names:
            continue
        xml = compare.viewpoint_wrapper(vp, static.name, manifest.get("headlight", True))
        r = aio.run(hb.aload(xml, screenshot=True, size=size, frames=4, timeout=timeout, background=compare.KEY), timeout + 30)
        out[n] = {"png": r.png, "ok": r.ok, "errors": r.errors[:3], "ms": round(r.ms)}
    return out


def gltf_view(hb, scene_dir, glb_name, vp, size=(960, 540), headlight=True, timeout=60) -> dict:
    """X_ITE drawing a glTF (the control inputs, or the converted file) under one viewpoint."""
    from x3dlab import aio
    hb.assets = Path(scene_dir)
    xml = compare.viewpoint_wrapper(vp, glb_name, headlight)
    r = aio.run(hb.aload(xml, screenshot=True, size=size, frames=4, timeout=timeout, background=compare.KEY), timeout + 30)
    return {"png": r.png, "ok": r.ok, "errors": r.errors[:3]}


def cycles_first_view(bl, src, out_png, size=(960, 540), samples=64) -> dict:
    """Cycles still of the first Viewpoint with a transparent film, composited on the magenta key so formats.compare_pngs
    reads it the same way as the X_ITE and Unreal key stills."""
    r = bl.render(str(src), str(out_png), samples=samples, resolution=size, denoise=True, film_transparent=True)
    res = r.get("result") or {}
    if r.get("ok") and Path(out_png).exists():
        from PIL import Image
        im = Image.open(out_png).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 0, 255, 255))
        bg.alpha_composite(im)
        buf = io.BytesIO()
        bg.convert("RGB").save(buf, format="PNG")
        res["png_key"] = buf.getvalue()
    return {"ok": r.get("ok"), **res}
