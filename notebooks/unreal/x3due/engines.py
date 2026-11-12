"""The other engines, through the merged x3dlab package (not re-implemented here):

  X3D -> glTF (and USD)   x3dlab.blender "convert" task + x3dlab.formats (validator, expected losses)   -- the Foundry's leg
  X_ITE stills            x3dlab.browser (Playwright Chromium, ANGLE on Vulkan when gpu=True)
  Cycles stills           x3dlab.blender "render" task (frames the first X3D Viewpoint)

    conv = engines.to_gltf(bl, "scene.x3d", "scene.glb")       # Foundry convert + Khronos validator + losses
    shots = engines.xite_views(hb, scene_dir, "scene.x3d", manifest, size=(960, 540))
"""

from __future__ import annotations

import io
import os
import re
import subprocess
from pathlib import Path

from . import compare, x3dscene

BLENDER_IO_REPO = "https://github.com/hagopjay/x3d-blender-io"
REF_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


def blender_io_checkout(ref, dest, repo=BLENDER_IO_REPO, timeout=600) -> dict:
    """The X3D add-on (x3d-blender-io) at `ref` (a branch, tag or commit of the public repository) in `dest`, for
    X3DLAB_BLENDER_IO_DIR: the Unreal notebooks can try an importer newer than x3d.lab's pin (src/x3dlab/pins.toml)
    before the pin moves. Returns {"ok", "ref", "commit", "dir", "why"}; never raises for a bad ref."""
    ref = (ref or "").strip()
    if not REF_OK.match(ref) or ".." in ref:
        return {"ok": False, "ref": ref, "why": "not a branch, tag or commit name: %r" % ref}
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    def git(*args):
        return subprocess.run(["git", "-C", str(dest), *args], capture_output=True, text=True, timeout=timeout)
    if not (dest / ".git").exists():
        git("init", "-q")
    git("remote", "remove", "origin")
    git("remote", "add", "origin", str(repo))
    r = git("fetch", "-q", "--depth", "1", "origin", ref)
    if r.returncode:
        return {"ok": False, "ref": ref, "why": "git fetch %s failed: %s" % (ref, (r.stderr or r.stdout).strip()[-300:])}
    r = git("checkout", "-q", "--force", "FETCH_HEAD")
    if r.returncode:
        return {"ok": False, "ref": ref, "why": "checkout failed: %s" % (r.stderr or r.stdout).strip()[-300:]}
    commit = git("rev-parse", "HEAD").stdout.strip()
    if not (dest / "source" / "__init__.py").exists():
        return {"ok": False, "ref": ref, "commit": commit, "why": "%s has no source/__init__.py: not the X3D add-on" % ref}
    return {"ok": True, "ref": ref, "commit": commit, "dir": str(dest), "why": "x3d-blender-io %s at %s" % (ref, commit[:10])}


def _reads_colour_space(addon_dir) -> bool:
    """True when the add-on checkout reads BLENDER_X3D_COLOR_SPACE (from the x-ite-colour change on)."""
    try:
        return "BLENDER_X3D_COLOR_SPACE" in (Path(addon_dir) / "source" / "import_x3d.py").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def use_blender_io(ref, work, divisions=None, log=print, colour_space=None) -> dict:
    """Point x3dlab's Blender leg at an add-on ref ("pin" = x3d.lab's pins.toml) and set the primitive tessellation
    (BLENDER_X3D_CIRCLE_DIV, read by add-ons from the import-fidelity change on) and the colour space ("x_ite": the
    add-on converts classic X3D colours from sRGB, as X_ITE reads them; "as_is"; read from the x-ite-colour change on).
    Call before x3dlab.blender.setup(). out["colour_space"] is what the add-on will really do: "as_is" when it can't
    convert, so the Unreal leg treats colours the same way."""
    out = {"ref": ref or "pin"}
    if divisions:
        os.environ["BLENDER_X3D_CIRCLE_DIV"] = str(int(divisions))
        out["circle_divisions"] = int(divisions)
    addon_dir = None
    if ref and ref.strip() and ref.strip() != "pin":
        co = blender_io_checkout(ref, Path(work) / "x3d-blender-io")
        out.update(co)
        if co["ok"]:
            os.environ["X3DLAB_BLENDER_IO_DIR"] = co["dir"]
            addon_dir = co["dir"]
            log("Blender I/O add-on: %s" % co["why"])
        else:
            os.environ.pop("X3DLAB_BLENDER_IO_DIR", None)
            log("⚠️ Blender I/O add-on %s not used (%s): the pinned one runs" % (ref, co["why"]))
    else:
        os.environ.pop("X3DLAB_BLENDER_IO_DIR", None)
        out["ok"] = True
    want = colour_space or "as_is"
    able = addon_dir is not None and _reads_colour_space(addon_dir)
    out["colour_space"] = want if (want == "as_is" or able) else "as_is"
    if want == "x_ite" and not able:
        log("⚠️ this add-on does not read BLENDER_X3D_COLOR_SPACE yet (x-ite-colour change): colours go through as they are")
        out["colour_space_why"] = "the add-on does not convert colours yet"
    os.environ["BLENDER_X3D_COLOR_SPACE"] = "SRGB" if out["colour_space"] == "x_ite" else "AS_IS"
    return out


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
