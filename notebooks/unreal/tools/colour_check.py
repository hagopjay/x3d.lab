"""Measure what each engine draws for the same X3D colours and light (x3d.lab, 12 Nov 2026).

    python colour_check.py OUT_DIR [X3D_BLENDER_IO_CHECKOUT]

Needs the bpy wheel, Playwright and Chrome, and an add-on checkout with the x-ite-colour change (the color_space
import option). Writes the stills, the glbs and table.json to OUT_DIR, and prints the tables.

The two questions it settled with pixels:

1. Colour space: X_ITE draws Material / UnlitMaterial colours as sRGB values; Blender, glTF and Unreal read the
   add-on's unconverted colours as linear. How far apart are they, per X3D construct?
2. glTF light units: Blender's default SPEC export vs COMPAT ("unitless"), read back by X_ITE and Babylon.js.

X3D -> (x3d-blender-io checkout) -> glb, then X_ITE on the X3D, X_ITE on the glb, and Babylon.js on the glb,
all in headless Chrome (SwiftShader). Pixels are sampled at each quad's centre.
"""
import io
import json
import math
import os
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve()
LAB = HERE.parents[3]                                       # x3d.lab
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "colour_check")
OUT.mkdir(parents=True, exist_ok=True)
# a checkout of hagopjay/x3d-blender-io (its tests/ hold the helper that registers the add-on with bpy)
ADDON = Path(sys.argv[2] if len(sys.argv) > 2 else os.environ.get("X3DLAB_BLENDER_IO_DIR", "x3d-blender-io"))
sys.path[:0] = [str(ADDON / "tests"), str(LAB / "src"), str(LAB / "notebooks" / "unreal")]

import bpy  # noqa: E402
from PIL import Image  # noqa: E402
from test_roundtrip_bpy import _register_extension  # noqa: E402

W, H = 640, 360
GREYS = (0.25, 0.5, 0.75)
QUAD = "<Coordinate point='-0.4 -0.4 0 0.4 -0.4 0 0.4 0.4 0 -0.4 0.4 0'/>"
VIEW = "<Viewpoint position='0 0 10' fieldOfView='0.785398'/>"


def ifs(extra=""):
    return "<IndexedFaceSet solid='false' coordIndex='0 1 2 3 -1'>%s%s</IndexedFaceSet>" % (QUAD, extra)


def shape(x, y, appearance, geometry):
    return "<Transform translation='%g %g 0'><Shape>%s%s</Shape></Transform>" % (x, y, appearance, geometry)


ROWS = {   # row name -> (y, appearance(g), geometry(g))
    "Material emissive": (3, lambda g: "<Appearance><Material diffuseColor='0 0 0' emissiveColor='%g %g %g'/></Appearance>" % (g, g, g),
                          lambda g: ifs()),
    "UnlitMaterial": (1, lambda g: "<Appearance><UnlitMaterial emissiveColor='%g %g %g'/></Appearance>" % (g, g, g),
                      lambda g: ifs()),
    "PhysicalMaterial emissive": (-1, lambda g: "<Appearance><PhysicalMaterial baseColor='0 0 0' emissiveColor='%g %g %g'/></Appearance>" % (g, g, g),
                                  lambda g: ifs()),
    "Color node, no material": (-3, lambda g: "", lambda g: ifs("<Color color='%s'/>" % " ".join(["%g %g %g" % (g, g, g)] * 4))),
}
LIT = {    # lit by one DirectionalLight of intensity 0.3, head-on
    "Material diffuse 0.8": (2, "<Appearance><Material diffuseColor='0.8 0.8 0.8'/></Appearance>"),
    "PhysicalMaterial base 0.8": (-2, "<Appearance><PhysicalMaterial baseColor='0.8 0.8 0.8' metallic='0' roughness='1'/></Appearance>"),
}
X3D_HEAD = "<?xml version='1.0' encoding='UTF-8'?><X3D profile='Full' version='4.0'><Scene>"


def colour_scene():
    body = ["<NavigationInfo headlight='false'/>", "<Background skyColor='0.2 0.2 0.2'/>", VIEW]
    for name, (y, app, geo) in ROWS.items():
        for i, g in enumerate(GREYS):
            body.append(shape(2 * (i - 1), y, app(g), geo(g)))
    return X3D_HEAD + "".join(body) + "</Scene></X3D>"


def light_scene():
    body = ["<NavigationInfo headlight='false'/>", "<Background skyColor='0 0 0'/>", VIEW,
            "<DirectionalLight direction='0 0 -1' intensity='0.3'/>"]
    for name, (y, app) in LIT.items():
        body.append(shape(0, y, app, ifs()))
    return X3D_HEAD + "".join(body) + "</Scene></X3D>"


def pixel_at(png, x, y):
    """Mean RGB of a 7x7 block at the image position of X3D point (x, y, 0) seen from (0 0 10), fov pi/4 on the short side."""
    im = Image.open(io.BytesIO(png) if isinstance(png, bytes) else png).convert("RGB")
    s = (H / 2) / (10 * math.tan(math.pi / 8))
    px, py = W / 2 + x * s, H / 2 - y * s
    vals = [im.getpixel((int(px) + dx, int(py) + dy)) for dx in range(-3, 4) for dy in range(-3, 4)]
    return [round(sum(v[c] for v in vals) / len(vals)) for c in range(3)][0]


def to_glb(x3d_path, glb_path, lighting, colour_space="AS_IS"):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _register_extension(tempfile.mkdtemp())
    bpy.ops.import_scene.x3d(filepath=str(x3d_path), color_space=colour_space)
    bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format="GLB", export_lights=True,
                              export_import_convert_lighting_mode=lighting)
    return glb_path


def xite(scenes):
    """{name: png bytes} from X_ITE for {name: xml}; relative URLs resolve in OUT."""
    from x3dlab import browser
    b = browser.start(viewport=(W, H))
    b.assets = OUT
    out = {}
    try:
        for name, xml in scenes.items():
            r = b.load(xml, screenshot=True, size=(W, H), frames=4)
            if not r.ok:
                print("X_ITE", name, "failed:", r.errors[:2])
                continue
            out[name] = r.png
            (OUT / ("xite_%s.png" % name)).write_bytes(r.png)
    finally:
        b.close()
    return out


BABYLON_PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#000}</style>
<script src="babylon.js"></script><script src="babylonjs.loaders.min.js"></script></head><body>
<canvas id="c" width="%(w)d" height="%(h)d" style="width:%(w)dpx;height:%(h)dpx"></canvas><script>
window.done = (async () => {
  BABYLON.SceneLoader.ShowLoadingScreen = false;
  const canvas = document.getElementById('c');
  const engine = new BABYLON.Engine(canvas, false, {preserveDrawingBuffer: true});
  const scene = new BABYLON.Scene(engine);
  scene.useRightHandedSystem = true;
  scene.clearColor = new BABYLON.Color4(%(bg)s, 1);
  await BABYLON.SceneLoader.AppendAsync('', '%(glb)s', scene);
  const cam = new BABYLON.UniversalCamera('cam', new BABYLON.Vector3(0, 0, 10), scene);
  cam.setTarget(new BABYLON.Vector3(0, 0, 0)); cam.fov = Math.PI / 4; cam.fovMode = BABYLON.Camera.FOVMODE_VERTICAL_FIXED;
  scene.activeCamera = cam;
  await scene.whenReadyAsync();
  for (let i = 0; i < 6; i++) { scene.render(); await new Promise(r => requestAnimationFrame(r)); }
  scene.render();
  return {meshes: scene.meshes.length, lights: scene.lights.map(l => [l.getClassName(), l.intensity]),
          png: canvas.toDataURL('image/png'),
          materials: scene.materials.map(m => [m.name, m.getClassName(), m.unlit === true])};
})();
</script></body></html>"""


def babylon(glbs, bg):
    """{name: png} from Babylon.js (WebGL2, default image processing: no tone mapping) for {name: glb path}."""
    from playwright.sync_api import sync_playwright
    from x3dlab import browser as xb
    from x3due import everywhere
    vend = everywhere.babylon_vendor(OUT / "vendor")
    site = OUT / "babylon_site"
    site.mkdir(exist_ok=True)
    for f in ("babylon.js", "babylonjs.loaders.min.js"):
        (site / f).write_bytes((vend / f).read_bytes())
    out = {}
    srv, base = everywhere._serve(site)
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(executable_path=xb.chromium_path(), args=xb.CHROME_ARGS)
            page = br.new_page(viewport={"width": W, "height": H})
            for name, glb in glbs.items():
                (site / Path(glb).name).write_bytes(Path(glb).read_bytes())
                page_name = "page_%s.html" % name                 # one page per glb: Chrome may cache a page by its URL
                (site / page_name).write_text(BABYLON_PAGE % {"w": W, "h": H, "glb": Path(glb).name, "bg": bg})
                page.goto(base + "/" + page_name)
                info = page.evaluate("window.done")
                import base64
                png = base64.b64decode(info.pop("png").split(",", 1)[1])
                out[name] = png
                (OUT / ("babylon_%s.png" % name)).write_bytes(png)
                print("Babylon", name, info)
            br.close()
    finally:
        srv.shutdown()
    return out


def main():
    cs, ls = OUT / "colours.x3d", OUT / "light.x3d"
    cs.write_text(colour_scene())
    ls.write_text(light_scene())
    glbs = {"colours_spec": to_glb(cs, OUT / "colours.glb", "SPEC"),
            "colours_srgb": to_glb(cs, OUT / "colours_srgb.glb", "COMPAT", "SRGB"),
            "light_spec": to_glb(ls, OUT / "light_spec.glb", "SPEC"),
            "light_compat": to_glb(ls, OUT / "light_compat.glb", "COMPAT"),
            "light_srgb": to_glb(ls, OUT / "light_srgb.glb", "COMPAT", "SRGB")}
    wrap = lambda glb, bg: (X3D_HEAD + "<NavigationInfo headlight='false'/><Background skyColor='%s'/>%s<Inline url='\"%s\"'/></Scene></X3D>"
                            % (bg, VIEW, Path(glb).name))
    shots = xite({"colours_x3d": cs.read_text(), "colours_glb": wrap(glbs["colours_spec"], "0.2 0.2 0.2"),
                  "colours_glb_srgb": wrap(glbs["colours_srgb"], "0.2 0.2 0.2"),
                  "light_x3d": ls.read_text(), "light_glb_spec": wrap(glbs["light_spec"], "0 0 0"),
                  "light_glb_compat": wrap(glbs["light_compat"], "0 0 0"), "light_glb_srgb": wrap(glbs["light_srgb"], "0 0 0")})
    bab = babylon({"colours": glbs["colours_spec"], "colours_srgb": glbs["colours_srgb"]}, "0.2, 0.2, 0.2")
    bab.update(babylon({"light_spec": glbs["light_spec"], "light_compat": glbs["light_compat"],
                        "light_srgb": glbs["light_srgb"]}, "0, 0, 0"))
    table = {"colours": {}, "light": {}}
    for name, (y, _a, _g) in ROWS.items():
        for i, g in enumerate(GREYS):
            x = 2 * (i - 1)
            key = "%s %.2f" % (name, g)
            table["colours"][key] = {"X_ITE x3d": pixel_at(shots["colours_x3d"], x, y),
                                     "X_ITE glb as is": pixel_at(shots["colours_glb"], x, y),
                                     "Babylon as is": pixel_at(bab["colours"], x, y),
                                     "X_ITE glb sRGB": pixel_at(shots["colours_glb_srgb"], x, y),
                                     "Babylon sRGB": pixel_at(bab["colours_srgb"], x, y)}
    for name, (y, _a) in LIT.items():
        table["light"][name] = {"X_ITE x3d": pixel_at(shots["light_x3d"], 0, y),
                                "X_ITE glb SPEC": pixel_at(shots["light_glb_spec"], 0, y),
                                "X_ITE glb COMPAT": pixel_at(shots["light_glb_compat"], 0, y),
                                "Babylon glb SPEC": pixel_at(bab["light_spec"], 0, y),
                                "Babylon glb COMPAT": pixel_at(bab["light_compat"], 0, y),
                                "X_ITE COMPAT+sRGB": pixel_at(shots["light_glb_srgb"], 0, y),
                                "Babylon COMPAT+sRGB": pixel_at(bab["light_srgb"], 0, y)}
    (OUT / "table.json").write_text(json.dumps(table, indent=1))
    for part, rows in table.items():
        cols = list(next(iter(rows.values())).keys())
        print("\n%-34s " % part + " ".join("%17s" % c for c in cols))
        for k, v in rows.items():
            print("%-34s " % k + " ".join("%17s" % v[c] for c in cols))


if __name__ == "__main__":
    main()
