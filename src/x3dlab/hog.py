"""The GPU hog run: one corpus, every engine, findings sorted by the project that owns them.

    cfg = hog.HogConfig()                       # every stage on; see the fields below
    run = lab.hog(cfg)                          # or hog.run(lab, cfg)
    hog.show(run, lab)                          # matrix, findings by project, links
    run.dir                                     # report.html, findings.json, matrix.json, issues/<project>.md

Stages (each can be switched off):
  probe     the machine as the engines see it: GPU, driver, the graphics libraries Chrome and the OptiX denoiser need,
            Cycles backends, and which renderer Chrome really gets in SwiftShader and in GPU mode
  corpus    one scene per X3D node from x3d.loom's bridge fixtures (106 nodes) plus the lab's own scenes, each as
            X3D 4.0 and as X3D 4.1
  matrix    every corpus scene through: the fork validator (x3d_mcp), lxml against the version's own XSD, the upstream
            validator (optional), X_ITE (loads? console errors? draws anything?), Blender (imports what? exports
            valid 4.0 and 4.1? which node types come back?), and Cycles on the GPU (draws anything?)
  loom      x3d.loom's Babylon bridge fixture suite: its reader + Babylon against X_ITE, node by node
  stress    the 02 Lab heavy preset, GPU only, after a warm-up render (first-render kernel loading is measured apart)
  traces    Chrome traces of the pagoda (cold, then warm) and of meshes of growing size
  findings  rules over everything above -> findings for x3d_mcp, X_ITE, x3d-blender-io, x3d.loom/Babylon, Copilot,
            each with evidence and the scene that shows it, plus one issue draft per project

Nothing here sends a scene anywhere except to the engines on this machine. The loom stage downloads Node.js,
Babylon.js (pinned) and x3d.loom (pinned) once.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import platform
import re
import shutil
import subprocess
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import aio, runtime, scenegen, scenes
from .events import bus

LOOM_REPO = "https://github.com/hagopjay/x3d.loom"
LOOM_REF = "d55fd0e"                    # main on 10 Nov 2026 (bridge-review merged)
BABYLON_VERSION = "9.32.0"              # the version the loom receipts were taken with
NODE_VERSION = "22.11.0"
STAGES = ("probe", "corpus", "matrix", "loom", "stress", "traces", "findings")

# Components whose nodes put something on screen by themselves (in a Shape, or as a light/background).
VISUAL_COMPONENTS = {"Geometry3D", "Geometry2D", "Shape", "Rendering", "Texturing", "Texturing3D", "CubeMapTexturing", "Lighting",
                     "EnvironmentalEffects", "NURBS", "CADGeometry", "ParticleSystems", "VolumeRendering", "Text", "HAnim",
                     "Grouping", "TextureProjection", "GaussianSplats"}



# The seven node types X3D 4.1 adds (UOM 4.1 minus UOM 4.0). No loom fixture covers them; each scene follows the 4.1
# object model (where_can_place / UOM accepts), so a validator that rejects one disagrees with the model, not with us.
SPEC41_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<X3D profile="Full" version="4.1" xmlns:xsd="http://www.w3.org/2001/XMLSchema-instance" xsd:noNamespaceSchemaLocation="https://www.web3d.org/specifications/x3d-4.1.xsd">\n  <head><meta name="title" content="%s.x3d"/><meta name="description" content="x3d.lab hog corpus: X3D 4.1 node %s"/></head>\n  <Scene>\n    <Viewpoint position="0 0 6"/>\n%s\n  </Scene>\n</X3D>\n'
SPEC41 = {
    'EnvironmentLight': '    <EnvironmentLight color="1 0.95 0.9" intensity="0.8" ambientIntensity="0.2"/>\n    <Shape><Appearance><PhysicalMaterial baseColor="0.8 0.8 0.85" metallic="1" roughness="0.25"/></Appearance><Sphere radius="1.2"/></Shape>',
    'FontLibrary': '    <FontLibrary family="Lab Sans" url=\'"fonts/lab-sans.woff2"\'/>\n    <Shape><Appearance><Material diffuseColor="0.9 0.6 0.1"/></Appearance><Text string=\'"Hello 4.1"\'><FontStyle family=\'"Lab Sans" "SANS"\' size="0.8" justify=\'"MIDDLE" "MIDDLE"\'/></Text></Shape>',
    'GaussianSplats': '    <GaussianSplats positions="-1 0 0 0 0 0 1 0 0" scales="0.3 0.3 0.3 0.4 0.2 0.2 0.2 0.4 0.2" orientations="0 0 0 1 0 0 0.3827 0.9239 0 0 0 1" opacities="0.9 0.8 1" sphericalHarmonicsDegree0Coef0="1 0.2 0.2 0.2 1 0.2 0.2 0.2 1"/>',
    'HAnimPose': '    <HAnimHumanoid DEF="Hu" name="hu" version="2.0" skeletalConfiguration="BASIC" loa="1">\n      <HAnimJoint containerField="skeleton" DEF="hu_humanoid_root" name="humanoid_root" center="0 1 0">\n        <HAnimJoint DEF="hu_sacroiliac" name="sacroiliac" center="0 0.9 0"/>\n      </HAnimJoint>\n      <HAnimJoint containerField="joints" USE="hu_humanoid_root"/>\n      <HAnimJoint containerField="joints" USE="hu_sacroiliac"/>\n      <HAnimPose DEF="Bow" name="bow" description="bend forward" transitionDuration="1">\n        <HAnimJoint name="sacroiliac" rotation="1 0 0 0.6"/>\n      </HAnimPose>\n    </HAnimHumanoid>',
    'InlineGeometry': '    <Shape><Appearance><Material diffuseColor="0.3 0.6 0.9"/></Appearance><InlineGeometry url=\'"models/teapot.obj"\' smooth="true"/></Shape>',
    'RenderedTexture': '    <Shape><Appearance><Material/><RenderedTexture width="256" height="256" singleFrame="true"/></Appearance><Box size="2 2 2"/></Shape>',
    'Tangent': '    <Shape><Appearance><Material diffuseColor="0.6 0.8 0.4"/></Appearance>\n      <IndexedTriangleSet index="0 1 2">\n        <Coordinate point="-1 -1 0 1 -1 0 0 1 0"/>\n        <Normal vector="0 0 1 0 0 1 0 0 1"/>\n        <Tangent vector="1 0 0 1 1 0 0 1 1 0 0 1"/>\n      </IndexedTriangleSet></Shape>',
}


@dataclass
class HogConfig:
    stages: list = field(default_factory=lambda: list(STAGES))
    versions: list = field(default_factory=lambda: ["4.0", "4.1"])
    loom_ref: str = LOOM_REF
    loom_dir: str = ""                      # an existing x3d.loom checkout; empty = clone LOOM_REF
    include_lab_scenes: bool = True
    max_items: int = 0                      # 0 = the whole corpus (handy to cap for a quick look)
    consortium: bool = False                # also judge with the upstream Web3DConsortium/x3d_mcp
    chrome_gpu: bool = True                 # try the GPU for X_ITE and the traces (falls back to SwiftShader by itself)
    enable_graphics: bool = True            # install the matching NVIDIA graphics userspace first (x3dlab.graphics)
    blender: bool = True
    blender_versions: list = field(default_factory=lambda: ["X3D40", "X3D41"])
    render_samples: int = 16
    render_size: list = field(default_factory=lambda: [320, 180])
    blender_chunk: int = 8                  # tasks per Blender process; a crash only costs one chunk
    workers: int = 0                        # parallel Blender processes; 0 = from the machine
    stress_preset: str = "heavy"
    trace_sizes: list = field(default_factory=lambda: [20000, 200000, 1000000])
    seed: int = 7
    name: str = ""


@dataclass
class Item:
    id: str
    node: str
    version: str
    source: str            # loom | lab
    xml: str = field(repr=False)
    component: str = ""
    visual: bool = False
    path: str = ""
    expect: str = ""        # "valid": the scene follows the object model, so any rejection is a finding


@dataclass
class Finding:
    target: str            # x3d_mcp | x_ite | blender_io | loom | copilot | lab
    severity: str          # high | medium | low | info
    title: str
    nodes: list = field(default_factory=list)
    evidence: str = ""
    repro: list = field(default_factory=list)


@dataclass
class HogRun:
    cfg: HogConfig
    id: str
    name: str
    dir: Path
    started: float = field(default_factory=time.time)
    ended: float | None = None
    probe: dict = field(default_factory=dict)
    items: list = field(default_factory=list)
    matrix: dict = field(default_factory=dict)        # item id -> {engine: result}
    loom: dict = field(default_factory=dict)
    stress: dict = field(default_factory=dict)
    traces: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)       # stage -> seconds
    probe_graphics_enable: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)        # stage -> error text

    def file_url(self, name: str) -> str:
        return "out/%s/%s" % (self.dir.name, name)


# ====================================================================== probe
def _sh(cmd: list[str], timeout: float = 30) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception as exc:
        return "unavailable: %s" % exc


NVIDIA_LIBS = ("libGLX_nvidia", "libEGL_nvidia", "libnvidia-glcore", "libnvidia-eglcore", "libnvidia-rtcore", "libnvoptix",
               "libnvidia-glvkspirv", "libcuda", "libnvidia-ml")
NVOPTIX_BIN = ("/usr/share/nvidia/nvoptix.bin", "/usr/lib/x86_64-linux-gnu/nvoptix.bin", "/usr/lib64/nvoptix.bin",
               "/usr/local/nvidia/lib64/nvoptix.bin")
VULKAN_ICD_DIRS = ("/usr/share/vulkan/icd.d", "/etc/vulkan/icd.d", "/usr/local/share/vulkan/icd.d")


def graphics_stack() -> dict:
    """What NVIDIA's graphics side of the driver looks like inside this container (compute can work without it)."""
    smi = _sh(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,compute_cap", "--format=csv,noheader"])
    ldc = _sh(["ldconfig", "-p"])
    libs = {name: (name in ldc) for name in NVIDIA_LIBS}
    icds = []
    for d in VULKAN_ICD_DIRS:
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                icds.append(os.path.join(d, f))
    nv_icd = [p for p in icds if "nvidia" in p.lower()]
    optix_bin = [p for p in NVOPTIX_BIN if os.path.exists(p)]
    caps = os.environ.get("NVIDIA_DRIVER_CAPABILITIES", "")
    gpu = smi.strip().splitlines()[0] if smi.strip() and not smi.startswith("unavailable") else None
    verdicts = []
    if gpu:
        verdicts.append("Chrome can use the GPU" if nv_icd and libs.get("libGLX_nvidia") else
                        "Chrome cannot use the GPU here: no NVIDIA Vulkan ICD / GL library in the container, so WebGL and WebGPU run on SwiftShader")
        verdicts.append("OptiX denoiser should work" if optix_bin or libs.get("libnvoptix") else
                        "OptiX denoiser cannot start: nvoptix.bin / libnvoptix missing (the OptiX ray tracer can still work)")
    return {"gpu": gpu, "nvidia_smi": smi.strip()[:300], "driver_capabilities": caps or None, "libs": libs, "vulkan_icds": icds,
            "nvidia_vulkan_icd": nv_icd, "nvoptix_bin": optix_bin, "verdicts": verdicts}


def chrome_renderers(xite: bool = False) -> dict:
    """Which WebGL renderer and WebGPU adapter Chrome gets with the SwiftShader flags and with the GPU flags."""
    from . import browser as B
    out = {}
    try:
        B.ensure_playwright()
    except Exception as exc:
        return {"error": "playwright unavailable: %s" % exc}

    async def one(args):
        from playwright.async_api import async_playwright
        exe = B.chromium_path()
        async with async_playwright() as pw:
            br = await pw.chromium.launch(headless=True, args=args, **({"executable_path": exe} if exe else {}))
            try:
                page = await br.new_page()
                await page.goto("about:blank")
                webgl = await page.evaluate("""() => { const c = document.createElement('canvas').getContext('webgl2');
                    if (!c) return 'none'; const d = c.getExtension('WEBGL_debug_renderer_info');
                    return d ? c.getParameter(d.UNMASKED_RENDERER_WEBGL) : 'webgl2'; }""")
                return {"chromium": br.version, "webgl": webgl, "software": B.is_software(webgl)}
            finally:
                await br.close()

    for name, args in (("swiftshader", B.CHROME_ARGS), ("gpu", B.GPU_ARGS)):
        try:
            out[name] = aio.run(one(args), 120)
        except Exception as exc:
            out[name] = {"error": str(exc)[:300]}
    return out


def probe(lab=None) -> dict:
    import psutil
    d = {"python": platform.python_version(), "cpus": os.cpu_count(), "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
         "graphics": graphics_stack()}
    try:
        d["runtime"] = runtime.detect().as_dict()
    except Exception as exc:
        d["runtime"] = {"error": str(exc)}
    bl = getattr(lab, "blender", None)
    if bl is not None:
        d["blender"] = {"version": bl.version, "best": bl.best_backend, "backends": bl.info.get("backends")}
    d["chrome"] = chrome_renderers()
    return d


# ====================================================================== corpus
def loom_checkout(ref: str = LOOM_REF, path: str = "") -> Path:
    """x3d.loom at `ref` (a shallow fetch of that one commit), or an existing checkout."""
    if path:
        p = Path(path)
        if not (p / "x3d.webgpu" / "bridge").is_dir():
            raise RuntimeError("%s is not an x3d.loom checkout (no x3d.webgpu/bridge)" % p)
        return p
    p = runtime.sub("vendor") / ("x3d.loom-%s" % ref)
    if (p / "x3d.webgpu" / "bridge" / "fixtures").is_dir():
        return p
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True)
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1")
    for cmd in (["git", "init", "-q"], ["git", "remote", "add", "origin", LOOM_REPO],
                ["git", "fetch", "-q", "--depth", "1", "origin", ref], ["git", "checkout", "-q", "FETCH_HEAD"]):
        r = subprocess.run(cmd, cwd=p, capture_output=True, text=True, env=env, timeout=600)
        if r.returncode:
            raise RuntimeError("%s failed: %s" % (" ".join(cmd), (r.stderr or r.stdout)[-400:]))
    return p


def to_version(xml: str, version: str) -> str:
    """The same scene declared as another X3D version (the version attribute and the XSD location)."""
    xml = re.sub(r'(<X3D\b[^>]*?\bversion=)(["\'])[0-9.]+\2', lambda m: '%s%s%s%s' % (m.group(1), m.group(2), version, m.group(2)), xml, count=1)
    return re.sub(r"x3d-[0-9.]+\.xsd", "x3d-%s.xsd" % version, xml, count=1)


def corpus(cfg: HogConfig, loom_dir: Path | None) -> list[Item]:
    from . import uom as uom_mod
    comp, comp41 = {}, {}
    try:
        comp = {n: s.component for n, s in uom_mod.load("4.0").nodes.items()}
        comp41 = {n: s.component for n, s in uom_mod.load("4.1").nodes.items()}
    except Exception:
        pass
    base = []
    if loom_dir is not None:
        fx = loom_dir / "x3d.webgpu" / "bridge" / "fixtures"
        for f in sorted(fx.glob("*.x3d")):
            node = f.stem
            base.append(("loom", node, f.read_text(encoding="utf-8"), str(f)))
    if cfg.include_lab_scenes:
        base.append(("lab", "lantern", scenes.LANTERN, "x3dlab.scenes.LANTERN"))
        base.append(("lab", "pagoda", scenes.PAGODA, "x3dlab.scenes.PAGODA"))
        for fam in scenegen.FAMILIES:
            base.append(("lab", "family:" + fam, scenegen.make(fam, 8 if fam != "meshes" else 1200, cfg.seed), "scenegen.make(%r)" % fam))
    items = []
    for src, node, xml, path in base:
        c = comp.get(node, "")
        visual = (c in VISUAL_COMPONENTS) or src == "lab"
        for v in cfg.versions:
            items.append(Item("%s@%s" % (node, v), node, v, src, to_version(xml, v), c, visual, path))
    if cfg.max_items:
        keep = {n for _, n, _, _ in base[:cfg.max_items]}
        items = [i for i in items if i.node in keep]
    if "4.1" in cfg.versions:
        for node, body in SPEC41.items():
            items.append(Item("%s@4.1" % node, node, "4.1", "spec41", SPEC41_HEAD % (node, node, body), comp41.get(node, ""),
                              True, "x3dlab.hog.SPEC41", "valid"))
    return items


# ====================================================================== matrix
def _xsd_oracle(version: str):
    from . import fuzz, uom as uom_mod
    xsd = uom_mod.find_uom_file("4.0").parent / ("x3d-%s.xsd" % version)
    return fuzz.XSDOracle(xsd) if xsd.exists() else None


def _kinds(v) -> list[str]:
    from .duel import kinds_of
    return kinds_of(v)


def _coverage(png: bytes | None) -> float | None:
    if not png:
        return None
    try:
        from .browser import silhouette
        m = silhouette(png, "key")
        return round(float(m.mean()), 4)
    except Exception:
        return None


def _node_types(xml: str) -> list[str]:
    try:
        from lxml import etree
        root = etree.fromstring(xml.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True, recover=True))
        sc = root.find("Scene")
        return sorted({el.tag for el in (sc.iter() if sc is not None else []) if el is not sc and isinstance(el.tag, str) and el.tag[:1].isupper()})
    except Exception:
        return []


async def _validate_all(lab, items: list[Item], cfg: HogConfig, run: HogRun) -> None:
    oracles = {v: _xsd_oracle(v) for v in cfg.versions}
    up = getattr(lab, "upstream_client", None) if cfg.consortium else None
    for it in items:
        m = run.matrix.setdefault(it.id, {})
        v = await lab.client.verdict(it.xml, job=run.id)
        m["fork"] = {"ok": v.ok, "xsd_valid": v.xsd_valid, "kinds": _kinds(v), "first": (v.errors[0].message[:200] if v.errors else ""),
                     "messages": {k: e.message[:240] for k, e in zip(_kinds(v), v.errors)}}
        o = oracles.get(it.version)
        if o is not None:
            r = await o(it.xml)
            m["xsd"] = {"ok": r.verdict == "accept", "first": (r.detail or "")[:200]}
        if up is not None:
            u = await up.verdict(it.xml, job=run.id)
            m["consortium"] = {"ok": u.ok, "kinds": _kinds(u), "first": (u.errors[0].message[:200] if u.errors else "")}


_RESOURCE = re.compile(r"Couldn't load (URL|image)|Failed to load resource|net::ERR_|404")


async def _xite_all(hb, items: list[Item], run: HogRun, assets: Path | None = None) -> None:
    hb.assets = assets
    for it in items:
        m = run.matrix.setdefault(it.id, {})
        r = await hb.aload(it.xml, screenshot=True, background="1 0 1", frames=2, timeout=60)
        real = [e for e in r.errors if not _RESOURCE.search(e)]
        m["x_ite"] = {"ok": r.ok, "errors": real[:3], "resource_errors": len(r.errors) - len(real), "warnings": len(r.warnings),
                      "ms": round(r.t.get("total", 0) or r.ms, 1), "coverage": _coverage(r.png) if r.ok else None}


def _blender_tasks(items: list[Item], cfg: HogConfig, scene_dir: Path, backend: str) -> list[dict]:
    tasks = []
    for it in items:
        src = scene_dir / ("%s.x3d" % re.sub(r"[^A-Za-z0-9._@-]", "_", it.id))
        src.write_text(it.xml, encoding="utf-8")
        tasks.append({"kind": "import", "src": str(src), "_item": it.id, "_what": "import"})
        for bv in cfg.blender_versions:
            tasks.append({"kind": "roundtrip", "src": str(src), "version": bv, "generations": 1,
                          "out_prefix": str(scene_dir / ("%s.rt" % src.stem)), "_item": it.id, "_what": "roundtrip:" + bv})
        if it.visual:
            tasks.append({"kind": "render", "src": str(src), "out_png": str(scene_dir / ("%s.cycles.png" % src.stem)),
                          "backend": backend, "samples": int(cfg.render_samples), "resolution": list(cfg.render_size),
                          "denoise": False, "camera": "viewpoint", "film_transparent": True, "adaptive": False,
                          "_item": it.id, "_what": "render"})
    return tasks


def _run_chunks(bl, tasks: list[dict], cfg: HogConfig, job: str) -> list[tuple[dict, dict]]:
    """Run tasks in chunks across processes. A chunk that dies is retried task by task, so a crashing scene is named."""
    workers = cfg.workers or max(1, min(6, (os.cpu_count() or 2) // 2))
    chunks = [tasks[i:i + max(1, cfg.blender_chunk)] for i in range(0, len(tasks), max(1, cfg.blender_chunk))]
    strip = lambda ts: [{k: v for k, v in t.items() if not k.startswith("_")} for t in ts]   # noqa: E731

    def go(chunk):
        res = bl.run(strip(chunk), timeout=900, job=job, label="hog")
        out = list(zip(chunk, res))
        died = [t for t, r in out if r["result"] and "exited with code" in str(r["result"].get("error", ""))]
        if died and len(chunk) > 1:
            out = [(t, r) for t, r in out if t not in died]
            for t in died:                             # alone this time: was it this scene, or a neighbour?
                out.append((t, bl.run(strip([t]), timeout=600, job=job, label="hog-retry")[0]))
        return out

    results = []
    with cf.ThreadPoolExecutor(workers) as ex:
        for part in ex.map(go, chunks):
            results.extend(part)
    return results


def _blender_all(lab, items: list[Item], cfg: HogConfig, run: HogRun) -> None:
    bl = lab.blender
    scene_dir = run.dir / "scenes"
    scene_dir.mkdir(exist_ok=True)
    tasks = _blender_tasks(items, cfg, scene_dir, bl.best_backend)
    for t, r in _run_chunks(bl, tasks, cfg, run.id):
        m = run.matrix.setdefault(t["_item"], {})
        res = r.get("result") or {}
        err = res.get("error") or ("" if r.get("ok") else "failed")
        log = [ln for ln in r.get("log", []) if ln.strip()][:5]
        what = t["_what"]
        if what == "import":
            m["blender_import"] = {"ok": not err, "error": err[:240], "counts": res.get("counts"), "log": log}
        elif what.startswith("roundtrip:"):
            bv = what.split(":", 1)[1]
            g = (res.get("generations") or [{}])[0]
            out = g.get("output")
            entry = {"ok": not err and not g.get("error"), "error": (err or g.get("error") or "")[:240], "bytes": g.get("bytes")}
            if out and os.path.exists(out):
                entry["file"] = os.path.relpath(out, run.dir)
                entry["types"] = _node_types(Path(out).read_text(encoding="utf-8", errors="replace"))
            m["blender_rt_" + bv] = entry
        elif what == "render":
            cov = (res.get("pixels") or {}).get("covered")
            m["cycles"] = {"ok": not err, "error": err[:240], "covered": cov, "seconds": res.get("seconds"),
                           "backend": res.get("backend")}


async def _validate_exports(lab, run: HogRun, cfg: HogConfig) -> None:
    """Is what Blender wrote valid X3D of the version it claims?"""
    for m in run.matrix.values():
        for bv in cfg.blender_versions:
            e = m.get("blender_rt_" + bv)
            if not e or not e.get("file"):
                continue
            xml = (run.dir / e["file"]).read_text(encoding="utf-8", errors="replace")
            v = await lab.client.verdict(xml, job=run.id)
            e["valid"] = v.ok
            e["kinds"] = _kinds(v)
            e["first"] = v.errors[0].message[:200] if v.errors else ""


# ====================================================================== loom (Babylon bridge)
def _node_bin() -> str:
    """A Node.js >= 20: the system one, or a pinned download into the lab folder."""
    sysnode = shutil.which("node")
    if sysnode:
        v = _sh([sysnode, "--version"]).strip().lstrip("v")
        if v and v.split(".")[0].isdigit() and int(v.split(".")[0]) >= 20:
            return sysnode
    root = runtime.sub("vendor") / ("node-v%s-linux-x64" % NODE_VERSION)
    exe = root / "bin" / "node"
    if not exe.exists():
        import tarfile
        import urllib.request
        url = "https://nodejs.org/dist/v%s/node-v%s-linux-x64.tar.xz" % (NODE_VERSION, NODE_VERSION)
        with bus.span("download Node.js %s" % NODE_VERSION, src="hog"):
            tmp = runtime.sub("vendor") / "node.tar.xz"
            urllib.request.urlretrieve(url, tmp)
            with tarfile.open(tmp) as tf:
                tf.extractall(runtime.sub("vendor"), filter="data")
            tmp.unlink()
    return str(exe)


def _babylon_js(node: str) -> Path:
    d = runtime.sub("vendor", "babylonjs-%s" % BABYLON_VERSION)
    js = d / "package" / "babylon.js"
    if not js.exists():
        npm = str(Path(node).with_name("npm")) if Path(node).with_name("npm").exists() else "npm"
        r = subprocess.run([npm, "pack", "-q", "babylonjs@%s" % BABYLON_VERSION], cwd=d, capture_output=True, text=True, timeout=600)
        tgz = next(d.glob("babylonjs-*.tgz"), None)
        if tgz is None:
            raise RuntimeError("npm pack babylonjs failed: %s" % (r.stderr or r.stdout)[-300:])
        import tarfile
        with tarfile.open(tgz) as tf:
            tf.extractall(d, filter="data")
    return js


def loom_suite(loom_dir: Path, names: list[str] | None = None, timeout: float = 2400) -> dict:
    """Run x3d.loom's bridge fixtures (reader + Babylon vs X_ITE). Returns {env, results:{node:{...}}, nodes_status}."""
    from . import browser as B
    bridge = loom_dir / "x3d.webgpu" / "bridge"
    node = _node_bin()
    npm = str(Path(node).with_name("npm")) if Path(node).with_name("npm").exists() else "npm"
    env = dict(os.environ, PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1", PATH=str(Path(node).parent) + os.pathsep + os.environ.get("PATH", ""))
    if not (bridge / "node_modules" / "playwright").is_dir():
        r = subprocess.run([npm, "ci", "-q"], cwd=bridge, capture_output=True, text=True, timeout=900, env=env)
        if r.returncode:
            raise RuntimeError("npm ci failed: %s" % (r.stderr or r.stdout)[-400:])
    B.ensure_playwright()
    env.update(CHROMIUM_EXECUTABLE=B.chromium_path() or "", XITE_DIR=str(B.xite_dir()))
    babylon = _babylon_js(node)
    names = names or sorted(p.name[:-len(".test.json")] for p in (bridge / "fixtures").glob("*.test.json"))
    with bus.span("x3d.loom bridge fixtures (%d)" % len(names), src="hog"):
        r = subprocess.run([node, "tools/run-tests.js", "--babylon", str(babylon)] + names, cwd=bridge, capture_output=True,
                           text=True, timeout=timeout, env=env)
    line = next((ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("{")), "")
    if not line:
        raise RuntimeError("run-tests.js printed no result (exit %s): %s" % (r.returncode, (r.stderr or r.stdout)[-600:]))
    out = json.loads(line)
    try:
        nodes = json.loads((bridge / "NODES.json").read_text())["nodes"]
        out["nodes_status"] = dict(Counter(v.get("status") for v in nodes.values()))
        out["node_status"] = {k: v.get("status") for k, v in nodes.items()}
    except Exception:
        pass
    return out


# ====================================================================== stress and traces
def stress_stage(lab, cfg: HogConfig, run: HogRun) -> None:
    from . import stress
    bl = lab.blender
    warm = {}
    if bl is not None and bl.best_backend != "CPU":
        t0 = time.time()
        r = lab.render(scenes.LANTERN, samples=8, resolution=(320, 180), denoise=False)
        warm = {"first_render_s": round(time.time() - t0, 1), "ok": r.get("ok"), "backend": r.get("backend")}
        t0 = time.time()
        lab.render(scenes.LANTERN, samples=8, resolution=(320, 180), denoise=False)
        warm["second_render_s"] = round(time.time() - t0, 1)
    sc = stress.RunConfig.preset_for(cfg.stress_preset, seed=cfg.seed)
    sc.tiers["render"]["compare_cpu"] = False
    sc.tiers["render"]["denoise"] = True
    sc.name = "%s-%s" % (run.name, cfg.stress_preset)
    sr = lab.stress(sc)
    run.stress = {"warmup": warm, "name": sr.name, "summary": sr.summary(), "card": sr.file_url("run_card.json"),
                  "report": sr.file_url("report.html"),
                  "renders": [{k: r.get(k) for k in ("case", "seconds", "samples", "resolution", "backend", "mpix_samples_per_s",
                                                     "peak_mem_mb", "denoiser", "adaptive")} for r in sr.renders],
                  "rows": [asdict(r) for r in sr.rows if r.status != "PASS"][:60]}


def traces_stage(lab, cfg: HogConfig, run: HogRun) -> None:
    hb = lab.browser
    todo = [("pagoda-cold", scenes.PAGODA), ("pagoda-warm", scenes.PAGODA)]
    todo += [("meshes-%d" % n, scenegen.make("meshes", n, cfg.seed)) for n in cfg.trace_sizes]
    tdir = run.dir / "traces"
    tdir.mkdir(exist_ok=True)
    for name, xml in todo:
        path = tdir / ("trace_%s.json" % name)
        with hb.trace(path, screenshots=False) as box:
            r = hb.load(xml, timeout=600, frames=10)
        m = hb.metrics()
        run.traces.append({"case": name, "ok": r.ok, "t": r.t, "events": box.get("events"), "tasks_over_50ms": box.get("tasks_over_50ms"),
                           "long_tasks_ms": (box.get("long_tasks_ms") or [])[:3], "js_heap_mb": round(m.get("JSHeapUsedSize", 0) / 1e6, 1),
                           "file": os.path.relpath(path, run.dir), "renderer": hb.info.get("webgl"), "gpu": hb.info.get("gpu")})


# ====================================================================== findings
_UOMS: dict = {}


def _uom(version: str):
    if version not in _UOMS:
        from . import uom as uom_mod
        _UOMS[version] = uom_mod.load(version)
    return _UOMS[version]


def uom_allows(xml: str, version: str, message: str) -> tuple[bool | None, str]:
    """Does the X3D object model allow the placement an XSD error complains about?
    'Element X: This element is not expected' -> is every X in a field of its parent that accepts X?
    'Element P: Missing child element(s)'    -> is every child of P accepted by the field it sits in?
    Returns (True, detail) when the UOM allows it (so the XSD is stricter than the model), (False, ...) or (None, ...)."""
    m = re.search(r"Element '([A-Za-z0-9]+)': (This element is not expected|Missing child element)", message or "")
    if not m:
        return None, ""
    try:
        from lxml import etree
        root = etree.fromstring(xml.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True))
        u = _uom(version)
    except Exception as exc:
        return None, "could not check: %s" % exc
    tag, kind = m.group(1), m.group(2)

    def placed_ok(child, parent) -> tuple[bool, str]:
        cs, ps = u.nodes.get(child.tag), u.nodes.get(parent.tag)
        if cs is None or ps is None:
            return False, "%s in %s: not in the %s UOM" % (child.tag, parent.tag, version)
        named = child.get("containerField")
        if named:                                           # the scene says which field: check exactly that one
            f = ps.fields.get(named)
            ok = bool(f and child.tag in u.accepts(f.accepts))
            return ok, "%s.%s accepts %s: %s" % (parent.tag, named, child.tag, "yes" if ok else "no")
        # no containerField: the default one (the UOM loader does not always carry it) is a node field that accepts the child
        hits = sorted(n for n, f in ps.fields.items() if f.accepts and f.access != "inputOnly" and child.tag in u.accepts(f.accepts))
        return bool(hits), "%s.%s accepts %s: %s" % (parent.tag, "/".join(hits) or "(no node field)", child.tag, "yes" if hits else "no")

    checks = []
    if kind.startswith("This element"):
        for el in root.iter(tag):
            par = el.getparent()
            if par is not None and par.tag not in ("Scene", "X3D", "head"):
                checks.append(placed_ok(el, par))
    else:
        for el in root.iter(tag):
            checks += [placed_ok(c, el) for c in el if isinstance(c.tag, str) and c.tag[:1].isupper()]
    if not checks:
        return None, ""
    return all(ok for ok, _ in checks), "; ".join(sorted({d for _, d in checks}))[:300]


def _short(nodes: list[str], n: int = 12) -> str:
    nodes = sorted(set(nodes))
    return ", ".join(nodes[:n]) + (" and %d more" % (len(nodes) - n) if len(nodes) > n else "")


def findings(run: HogRun) -> list[Finding]:
    F: list[Finding] = []
    by = defaultdict(list)

    def add(key, item_id, extra=""):
        by[key].append((item_id, extra))

    for it in run.items:
        m = run.matrix.get(it.id, {})
        fork, xsd, xi = m.get("fork"), m.get("xsd"), m.get("x_ite")
        imp, cyc = m.get("blender_import"), m.get("cycles")
        if fork and xsd and fork.get("xsd_valid") is not None and fork["xsd_valid"] != xsd["ok"]:
            add(("x3d_mcp", "schema layer disagrees with lxml on the same XSD (fork %s, lxml %s)" % (
                "accepts" if fork["xsd_valid"] else "rejects", "accepts" if xsd["ok"] else "rejects")), it.id, fork.get("first") or xsd.get("first"))
        msgs = (fork or {}).get("messages") or {}
        uom_ok = {}
        for k in ("node-not-allowed", "missing-child"):
            if k in msgs:
                ok_, detail = uom_allows(it.xml, it.version, msgs[k])
                if ok_:
                    uom_ok[k] = detail
                    add(("x3d_mcp", "the %s XSD rejects a placement the %s object model allows (XSD stricter than UOM)" % (it.version, it.version)),
                        it.id, "%s · %s" % (detail, msgs[k][:120]))
        # -1 separates fans and strips in IndexedTriangleFanSet / IndexedTriangleStripSet (X3D 4.0, 11.4.4 and 11.4.5)
        if "index-out-of-range" in msgs and "uses -1" in msgs["index-out-of-range"] and \
                re.search(r"IndexedTriangle(Fan|Strip)Set", msgs["index-out-of-range"]):
            add(("x3d_mcp", "flags the -1 fan/strip separator in IndexedTriangleFanSet/StripSet as an out-of-range index (false positive)"),
                it.id, msgs["index-out-of-range"])
        if "profile-component" in msgs and xi and xi.get("ok"):
            add(("x3d_mcp", "profile error says X_ITE refuses such scenes, but X_ITE loaded them"), it.id, msgs["profile-component"])
        if it.source == "loom" and fork and not fork["ok"]:
            for k in sorted(set(fork.get("kinds") or [])):
                if (k == "index-out-of-range" and "uses -1" in msgs.get(k, "")) or k in uom_ok:
                    continue
                add(("loom", "fixtures are not valid X3D: %s" % k), it.id, msgs.get(k, ""))
        con = m.get("consortium")
        if fork and con and fork["ok"] != con["ok"]:
            add(("x3d_mcp", "fork and upstream disagree"), it.id, (con.get("first") or fork.get("first")))
        valid = bool(fork and fork["ok"])
        if it.expect == "valid" and fork and not fork["ok"] and not uom_ok:
            layer = "XSD" if (xsd and not xsd["ok"]) else "semantic check"
            add(("x3d_mcp", "%s scene that follows the %s object model is rejected (%s)" % (it.version, it.version, layer)), it.id, fork.get("first"))
        if valid and xi and not xi["ok"]:
            add(("x_ite", "valid scene fails to load"), it.id, (xi.get("errors") or [""])[0])
        if valid and xi and xi["ok"] and xi.get("errors"):
            add(("x_ite", "valid scene logs console errors"), it.id, xi["errors"][0])
        if valid and it.visual and xi and xi["ok"] and xi.get("coverage") is not None and xi["coverage"] < 0.002 and it.source == "lab":
            add(("x_ite", "lab scene draws nothing"), it.id, "coverage %.4f" % xi["coverage"])
        if valid and imp and not imp["ok"]:
            add(("blender_io", "import fails on a valid scene"), it.id, imp.get("error") or (imp.get("log") or [""])[0])
        if valid and imp and imp["ok"] and it.visual and it.component in ("Geometry3D", "Geometry2D", "NURBS", "CADGeometry", "Rendering", "Text"):
            c = imp.get("counts") or {}
            if not c.get("meshes") and not c.get("objects"):
                add(("blender_io", "geometry node imports as nothing"), it.id, "counts %s" % json.dumps(c))
        for k, e in m.items():
            if not k.startswith("blender_rt_"):
                continue
            bv = k[len("blender_rt_"):]
            if valid and not e.get("ok"):
                add(("blender_io", "round trip to %s fails" % bv), it.id, e.get("error"))
            elif valid and e.get("valid") is False:
                add(("blender_io", "export to %s is not valid X3D" % bv), it.id, ", ".join(e.get("kinds") or []) + " · " + (e.get("first") or ""))
        if valid and it.visual and xi and xi.get("coverage") and xi["coverage"] > 0.01 and cyc and cyc.get("ok") and (cyc.get("covered") or 0) < 0.002:
            add(("blender_io", "X_ITE draws it, Cycles shows nothing after import"), it.id, "X_ITE coverage %.3f" % xi["coverage"])
        if cyc and not cyc.get("ok") and valid:
            add(("blender_io", "Cycles render fails after import"), it.id, cyc.get("error"))
    # 4.0 vs 4.1: the same scene, two answers from the same validator
    pairs = defaultdict(dict)
    for it in run.items:
        pairs[it.node][it.version] = run.matrix.get(it.id, {}).get("fork", {}).get("ok")
    for node, vs in pairs.items():
        if "4.0" in vs and "4.1" in vs and vs["4.0"] is not None and vs["4.1"] is not None and vs["4.0"] != vs["4.1"]:
            add(("x3d_mcp", "4.0 and 4.1 verdicts differ"), node + "@4.1", "4.0 %s, 4.1 %s" % ("valid" if vs["4.0"] else "invalid", "valid" if vs["4.1"] else "invalid"))
    # loom
    res = (run.loom or {}).get("results") or {}
    for node, r in res.items():
        if r.get("ok"):
            continue
        if r.get("xiteOk") is False and r.get("oursOk"):
            add(("loom", "X_ITE disagrees with the bridge spec (check X_ITE or the spec)"), node, r.get("msg"))
        elif r.get("oursOk") is False:
            add(("loom", "bridge (Babylon reader) fails its fixture"), node, r.get("ours"))
        else:
            add(("loom", "fixture did not run cleanly"), node, r.get("msg"))
    sev = {"x3d_mcp": "high", "x_ite": "medium", "blender_io": "medium", "loom": "medium"}
    for (target, title), rows in sorted(by.items()):
        nodes = [i for i, _ in rows]
        ev = "; ".join("%s: %s" % (i, (x or "")[:140]) for i, x in rows[:6])
        F.append(Finding(target, "high" if "fails" in title and target != "loom" else sev.get(target, "low"), title,
                         sorted(set(nodes)), ev, ["scenes/%s.x3d" % re.sub(r"[^A-Za-z0-9._@-]", "_", i) for i in nodes[:5] if "@" in i]))
    # standing measurements worth an issue
    g = (run.probe or {}).get("graphics") or {}
    for v in g.get("verdicts", []):
        if "cannot" in v:
            F.append(Finding("lab", "info", v, [], "NVIDIA_DRIVER_CAPABILITIES=%s; ICDs: %s" % (g.get("driver_capabilities"), g.get("vulkan_icds") or "none")))
    st = (run.loom or {}).get("nodes_status")
    if st:
        F.append(Finding("loom", "info", "Babylon bridge coverage of the 260 X3D 4.0 nodes: %s" % ", ".join("%s %d" % kv for kv in sorted(st.items())), []))
    return F


TARGET_TITLES = {"x3d_mcp": "x3d_mcp (validator, hagopjay fork)", "x_ite": "X_ITE", "blender_io": "x3d-blender-io (Blender add-on)",
                 "loom": "x3d.loom (Babylon bridge)", "copilot": "Copilot validator", "lab": "x3d.lab / the machine"}


def issue_markdown(run: HogRun, target: str) -> str:
    fs = [f for f in run.findings if f.target == target]
    if not fs:
        return ""
    lines = ["# %s: findings from the x3d.lab hog run `%s`" % (TARGET_TITLES.get(target, target), run.name), "",
             "Measured on %s · %s · %s." % (((run.probe or {}).get("graphics") or {}).get("gpu") or "no GPU",
                                            "Blender %s" % (((run.probe or {}).get("blender") or {}).get("version") or "–"),
                                            "Chrome WebGL: %s" % (((run.probe or {}).get("chrome") or {}).get("gpu", {}).get("webgl") or "–")), ""]
    for f in fs:
        lines += ["## %s" % f.title, "", "- severity: %s" % f.severity]
        if f.nodes:
            lines.append("- scenes (%d): %s" % (len(f.nodes), _short(f.nodes, 30)))
        if f.evidence:
            lines.append("- evidence: %s" % f.evidence)
        if f.repro:
            lines.append("- repro files (in the run folder): %s" % ", ".join("`%s`" % r for r in f.repro))
        lines.append("")
    return "\n".join(lines)


# ====================================================================== the run
def _stage(run: HogRun, name: str, fn) -> None:
    if name not in run.cfg.stages:
        return
    t0 = time.time()
    try:
        with bus.span("hog: %s" % name, src="hog", job=run.id):
            fn()
    except Exception as exc:
        run.errors[name] = "%s: %s" % (type(exc).__name__, str(exc)[:500])
        bus.error("hog stage %s failed: %s" % (name, run.errors[name]), src="hog", job=run.id)
    run.timings[name] = round(time.time() - t0, 1)
    write(run)


def run(lab, cfg: HogConfig | None = None, **kw) -> HogRun:
    """Blocking. Needs lab = x3dlab.up(); Blender (lab.setup_blender()) for the Blender stages; starts Chrome itself."""
    cfg = cfg or HogConfig(**kw)
    rid = uuid.uuid4().hex[:8]
    r = HogRun(cfg, rid, cfg.name or "hog-%s" % rid, runtime.sub("out", "hog_" + rid))
    loom_dir = {"path": None}

    def st_probe():
        if cfg.enable_graphics:
            from . import graphics
            r.probe_graphics_enable = graphics.enable()
        r.probe = probe(lab)
        if cfg.enable_graphics:
            r.probe["graphics_enable"] = r.probe_graphics_enable

    def st_corpus():
        try:
            loom_dir["path"] = loom_checkout(cfg.loom_ref, cfg.loom_dir)
        except Exception as exc:
            r.errors["corpus.loom"] = str(exc)[:300]
        r.items = corpus(cfg, loom_dir["path"])

    def st_matrix():
        aio.run(_validate_all(lab, r.items, cfg, r), 3600)
        if lab.browser is None or (cfg.chrome_gpu and not lab.browser.gpu_requested):
            lab.start_browser(gpu=cfg.chrome_gpu)
        r.probe.setdefault("x_ite_renderer", lab.browser.info)
        assets = (loom_dir["path"] / "x3d.webgpu" / "bridge" / "fixtures") if loom_dir["path"] else None
        aio.run(_xite_all(lab.browser, r.items, r, assets), 7200)
        if cfg.blender and lab.blender is not None:
            _blender_all(lab, r.items, cfg, r)
            aio.run(_validate_exports(lab, r, cfg), 3600)

    def st_loom():
        p = loom_dir["path"] or loom_checkout(cfg.loom_ref, cfg.loom_dir)
        r.loom = loom_suite(p)

    def st_stress():
        stress_stage(lab, cfg, r)

    def st_traces():
        if lab.browser is None:
            lab.start_browser(gpu=cfg.chrome_gpu)
        traces_stage(lab, cfg, r)

    def st_findings():
        r.findings = findings(r)

    bus.emit("hog run %s started: %s" % (r.name, ", ".join(cfg.stages)), src="hog", kind="hog_start", job=rid)
    for name, fn in (("probe", st_probe), ("corpus", st_corpus), ("matrix", st_matrix), ("loom", st_loom),
                     ("stress", st_stress), ("traces", st_traces), ("findings", st_findings)):
        _stage(r, name, fn)
    r.ended = time.time()
    write(r)
    bus.emit("hog run %s finished: %d findings" % (r.name, len(r.findings)), src="hog", kind="hog_end", job=rid,
             data={"report": r.file_url("report.html"), "errors": r.errors})
    return r


# ====================================================================== files and display
def card(r: HogRun) -> dict:
    return {"kind": "x3dlab.hog", "id": r.id, "name": r.name, "started": r.started, "ended": r.ended, "config": asdict(r.cfg),
            "timings": r.timings, "errors": r.errors, "probe": r.probe,
            "items": [{k: v for k, v in asdict(i).items() if k != "xml"} for i in r.items], "matrix": r.matrix,
            "loom": r.loom, "stress": r.stress, "traces": r.traces, "findings": [asdict(f) for f in r.findings]}


def write(r: HogRun) -> None:
    (r.dir / "hog_card.json").write_text(json.dumps(card(r), indent=1, default=str), encoding="utf-8")
    (r.dir / "findings.json").write_text(json.dumps([asdict(f) for f in r.findings], indent=1), encoding="utf-8")
    idir = r.dir / "issues"
    idir.mkdir(exist_ok=True)
    for t in TARGET_TITLES:
        md = issue_markdown(r, t)
        if md:
            (idir / ("%s.md" % t)).write_text(md, encoding="utf-8")
    (r.dir / "report.html").write_text("<!doctype html><meta charset='utf-8'><title>%s</title><body style='font:14px system-ui;"
                                       "margin:24px;max-width:1200px'>%s</body>" % (r.name, html(r)), encoding="utf-8")


def _cell(ok, text="") -> str:
    if ok is None:
        return "<td style='color:#aaa;text-align:center'>–</td>"
    col = "#1d6b3a" if ok else "#c62828"
    return "<td style='color:%s;text-align:center' title='%s'>%s</td>" % (col, _esc(text), "✓" if ok else "✗")


def _esc(s) -> str:
    from .ui import esc
    return esc(s)


def matrix_rows(r: HogRun) -> list[dict]:
    """One flat row per corpus item: the cells of the report table, also handy for pandas."""
    rows = []
    for it in r.items:
        m = r.matrix.get(it.id, {})
        row = {"id": it.id, "node": it.node, "version": it.version, "component": it.component, "source": it.source,
               "fork": (m.get("fork") or {}).get("ok"), "xsd": (m.get("xsd") or {}).get("ok"),
               "consortium": (m.get("consortium") or {}).get("ok"), "x_ite": (m.get("x_ite") or {}).get("ok"),
               "x_ite_coverage": (m.get("x_ite") or {}).get("coverage"), "blender_import": (m.get("blender_import") or {}).get("ok"),
               "cycles_covered": (m.get("cycles") or {}).get("covered")}
        for bv in r.cfg.blender_versions:
            e = m.get("blender_rt_" + bv) or {}
            row["rt_" + bv] = e.get("ok")
            row["export_valid_" + bv] = e.get("valid")
        rows.append(row)
    return rows


def html(r: HogRun) -> str:
    g = (r.probe or {}).get("graphics") or {}
    ch = (r.probe or {}).get("chrome") or {}
    head = ("<h2 style='margin:0'>%s</h2><div style='color:#555'>%s · %d CPUs · %s GB RAM · Blender %s (%s) · Chrome WebGL "
            "SwiftShader mode: %s · GPU mode: %s</div>" % (
                _esc(r.name), _esc(g.get("gpu") or "no GPU"), (r.probe or {}).get("cpus") or 0, (r.probe or {}).get("ram_gb") or "?",
                _esc(((r.probe or {}).get("blender") or {}).get("version") or "–"), _esc(((r.probe or {}).get("blender") or {}).get("best") or "–"),
                _esc((ch.get("swiftshader") or {}).get("webgl") or "–"), _esc((ch.get("gpu") or {}).get("webgl") or "–")))
    verdicts = "".join("<li>%s</li>" % _esc(v) for v in g.get("verdicts", []))
    if verdicts:
        head += "<ul style='margin:6px 0'>%s</ul>" % verdicts
    tim = " · ".join("%s %.0f s" % kv for kv in r.timings.items())
    head += "<div style='font-size:12px;color:#666'>stages: %s%s</div>" % (_esc(tim), (" · <b style='color:#c62828'>errors: %s</b>" % _esc(r.errors)) if r.errors else "")
    # findings by target
    fb = defaultdict(list)
    for f in r.findings:
        fb[f.target].append(f)
    fh = ""
    for t, title in TARGET_TITLES.items():
        if not fb.get(t):
            continue
        rows = "".join("<tr><td style='padding:2px 8px;color:%s'>%s</td><td style='padding:2px 8px'><b>%s</b></td><td>%d</td>"
                       "<td style='font-size:12px;color:#555'>%s</td></tr>" % (
                           {"high": "#c62828", "medium": "#9a5b00"}.get(f.severity, "#666"), f.severity, _esc(f.title), len(f.nodes),
                           _esc(_short(f.nodes, 8)) + ("<br>" + _esc(f.evidence[:300]) if f.evidence else "")) for f in fb[t])
        fh += "<h3 style='margin:14px 0 4px'>%s</h3><table style='border-collapse:collapse;font-size:13px'>%s</table>" % (_esc(title), rows)
    # matrix
    cols = ["fork", "xsd"] + (["consortium"] if r.cfg.consortium else []) + ["x_ite", "blender_import"] + \
        sum((["rt_" + b, "export_valid_" + b] for b in r.cfg.blender_versions), [])
    th = "".join("<th style='padding:2px 6px;font-weight:500;font-size:11px'>%s</th>" % c.replace("_", " ") for c in cols)
    body = ""
    for row in matrix_rows(r):
        body += "<tr><td style='padding:1px 6px'>%s</td><td style='font-size:11px;color:#666'>%s</td>%s<td style='font-size:11px'>%s</td><td style='font-size:11px'>%s</td></tr>" % (
            _esc(row["id"]), _esc(row["component"]), "".join(_cell(row.get(c)) for c in cols),
            "" if row["x_ite_coverage"] is None else "%.3f" % row["x_ite_coverage"],
            "" if row["cycles_covered"] is None else "%.3f" % row["cycles_covered"])
    mt = ("<h3 style='margin:16px 0 4px'>Matrix (%d scenes)</h3><table style='border-collapse:collapse;font-size:12px'><tr><th></th>"
          "<th style='font-size:11px'>component</th>%s<th style='font-size:11px'>X_ITE drawn</th><th style='font-size:11px'>Cycles drawn</th></tr>%s</table>"
          % (len(r.items), th, body))
    lo = ""
    if r.loom:
        res = r.loom.get("results") or {}
        ok = sum(1 for v in res.values() if v.get("ok"))
        lo = "<h3 style='margin:16px 0 4px'>Babylon bridge (x3d.loom)</h3><div>%d of %d fixtures pass against X_ITE · %s</div>" % (
            ok, len(res), _esc(json.dumps(r.loom.get("env", {}))[:300]))
    sh = ""
    if r.stress:
        sh = "<h3 style='margin:16px 0 4px'>GPU stress (%s)</h3><div>warm-up: %s · summary: %s</div>" % (
            _esc(r.cfg.stress_preset), _esc(r.stress.get("warmup")), _esc(json.dumps(r.stress.get("summary"))[:400]))
    tr = ""
    if r.traces:
        tr = "<h3 style='margin:16px 0 4px'>Traces</h3><table style='font-size:12px'>%s</table>" % "".join(
            "<tr><td>%s</td><td>%s</td><td>parse %.0f · replace %.0f · frames %.0f ms</td><td>heap %s MB</td><td>%s</td></tr>" % (
                _esc(t["case"]), "✓" if t["ok"] else "✗", (t["t"] or {}).get("parse", 0), (t["t"] or {}).get("replace", 0),
                (t["t"] or {}).get("frame", 0), t["js_heap_mb"], _esc(t.get("renderer"))) for t in r.traces)
    return head + fh + lo + sh + tr + mt


def show(r: HogRun, lab=None) -> None:
    from .ui import show as _show
    links = ""
    if lab is not None and hasattr(lab, "dashboard_url"):
        base = lab.dashboard_url()
        links = "<div style='font-size:12px;margin:6px 0'>%s</div>" % " · ".join(
            "<a href='%s%s' target=_blank>%s</a>" % (_esc(base), _esc(r.file_url(f)), f) for f in ("report.html", "findings.json", "hog_card.json"))
    _show("<div style='font:14px system-ui'>%s%s</div>" % (links, html(r)))
