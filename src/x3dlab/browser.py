"""A headless Chromium that loads scenes in X_ITE, for the 03 notebook's oracles, renders and traces.

    hb = browser.start()                       # Playwright + the pinned X_ITE, served offline from the kernel
    r = hb.load(xml, screenshot=True)          # LoadResult: ok, errors, warnings, timings, heap, png
    with hb.trace("out/trace.json"):           # Chrome trace with x3d.lab tracks; open it in DevTools > Performance
        hb.load(big_xml)
    hb.copilot(xml)                            # the private Copilot validator in the same browser, if loaded
    hb.close()

Nothing goes over the network: the harness page, X_ITE and the Copilot build are answered by `page.route` from
local folders under a made-up origin (https://x3dlab.invalid), so CORS and CDN outages never matter.
Each load is wrapped in performance.measure() entries on custom DevTools tracks (group "x3d.lab"), so a trace
opened in Chrome DevTools shows parse / replaceWorld / first frame as named bars next to X_ITE's own work.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import mimetypes
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import aio, pins
from .events import bus

ORIGIN = "https://x3dlab.invalid"
_COMMON_ARGS = ["--ignore-gpu-blocklist", "--no-sandbox", "--disable-dev-shm-usage",
                # several pages share one browser: a page in the background must not get throttled animation frames,
                # or "first frames" measures Chrome's throttling instead of X_ITE
                "--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"]
# SwiftShader: WebGL drawn on the CPU. Identical on every machine, so correctness checks agree everywhere; timings are CPU timings.
CHROME_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] + _COMMON_ARGS
# GPU attempt: ANGLE on Vulkan, which needs the NVIDIA Vulkan driver (an ICD in /usr/share/vulkan/icd.d) inside the container.
# Without it Chrome falls back to SwiftShader on its own; Browser.info["gpu"] says which renderer was really used.
GPU_ARGS = ["--use-angle=vulkan", "--enable-features=Vulkan,VulkanFromANGLE,DefaultANGLEVulkan", "--enable-unsafe-webgpu",
            "--enable-unsafe-swiftshader"] + _COMMON_ARGS
SOFTWARE_RENDERERS = ("swiftshader", "llvmpipe", "software", "softpipe")


def want_gpu(gpu: bool | None = None) -> bool:
    """gpu=None follows $X3DLAB_CHROME_GPU (1/true/yes); the default is SwiftShader."""
    if gpu is not None:
        return bool(gpu)
    return os.environ.get("X3DLAB_CHROME_GPU", "").strip().lower() in ("1", "true", "yes", "on")


def is_software(renderer: str | None) -> bool:
    return not renderer or any(s in renderer.lower() for s in SOFTWARE_RENDERERS)

HARNESS = r"""<!doctype html><html><head><meta charset=utf-8>
<link rel=stylesheet href="/xite/x_ite.css">
<style>html,body{margin:0;background:#fff}x3d-canvas{display:block;width:%(w)dpx;height:%(h)dpx}</style>
<script src="/xite/x_ite.min.js"></script></head>
<body><x3d-canvas id=c splashScreen=false notifications=false contextMenu=false></x3d-canvas>
<script>
const T = {trackGroup: "x3d.lab"};
function bar(name, t0, track, props) {
  try { performance.measure(name, {start: t0, end: performance.now(),
        detail: {devtools: Object.assign({dataType: "track-entry", track: track, color: "primary",
                 properties: Object.entries(props || {}).map(([k, v]) => [k, String(v)])}, T)}}); } catch (e) {}
}
window.lab = {
  ready: customElements.whenDefined("x3d-canvas").then(() => document.getElementById("c").browser),
  async load(xml, opts) {
    opts = opts || {};
    const b = await this.ready, out = {ok: false, t: {}, errors: []};
    b.setBrowserOption("Antialiased", opts.antialiased !== false);
    const t0 = performance.now();
    try {
      const scene = await b.createX3DFromString(xml);
      out.t.parse = performance.now() - t0; bar("parse", t0, "X_ITE load", {bytes: xml.length});
      const t1 = performance.now();
      await b.replaceWorld(scene);
      out.t.replace = performance.now() - t1; bar("replaceWorld", t1, "X_ITE load", {});
      const t2 = performance.now();
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      if (opts.frames) for (let i = 0; i < opts.frames; i++) await new Promise(r => requestAnimationFrame(r));
      out.t.frame = performance.now() - t2; bar("first frames", t2, "X_ITE render", {frames: 2 + (opts.frames || 0)});
      out.rootNodes = scene.rootNodes.length;
      out.profile = scene.profile ? scene.profile.name : null;
      out.version = scene.specificationVersion;
      out.ok = true;
    } catch (e) {
      out.errors.push(String(e && e.message || e).slice(0, 600));
    }
    out.t.total = performance.now() - t0;
    bar(out.ok ? "load ok" : "load FAILED", t0, "X_ITE load", {ok: out.ok});
    if (performance.memory) out.heap = performance.memory.usedJSHeapSize;
    return out;
  },
};
</script></body></html>"""


@dataclass
class LoadResult:
    ok: bool
    errors: list = field(default_factory=list)      # exceptions plus console errors during the load
    warnings: list = field(default_factory=list)    # console warnings during the load
    t: dict = field(default_factory=dict)           # ms: parse, replace, frame, total
    heap: int | None = None
    root_nodes: int | None = None
    version: str | None = None
    png: bytes | None = None
    ms: float = 0.0

    def summary(self) -> str:
        if not self.ok:
            return "rejected: " + (self.errors[0][:160] if self.errors else "?")
        return "loaded in %.0f ms (parse %.0f · replace %.0f · frame %.0f)%s%s" % (
            self.t.get("total", 0), self.t.get("parse", 0), self.t.get("replace", 0), self.t.get("frame", 0),
            " · %d warnings" % len(self.warnings) if self.warnings else "",
            " · %d console errors" % len(self.errors) if self.errors else "")


def xite_dir() -> Path:
    """The pinned X_ITE dist folder (downloaded once from npm), shared with the gateway's 'npm' mode."""
    from .hosting import _xite
    if os.environ.get("X3DLAB_XITE_DIR"):
        return Path(os.environ["X3DLAB_XITE_DIR"])
    return _xite("npm")[2]


def chromium_path() -> str | None:
    """A Chromium for Playwright: its own download, or the system one (chrome-devtools-mcp reuses this)."""
    if os.environ.get("X3DLAB_CHROMIUM"):
        return os.environ["X3DLAB_CHROMIUM"]
    for root in (os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), os.path.expanduser("~/.cache/ms-playwright"), "/opt/pw-browsers"):
        if root and os.path.isdir(root):
            for p in sorted(Path(root).glob("chromium-*/chrome-linux*/chrome"), reverse=True):
                return str(p)
    return shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")


def ensure_playwright() -> None:
    """pip install playwright and its Chromium (with system libraries) if this kernel lacks them."""
    from . import install
    try:
        import playwright  # noqa: F401  (presence check)
    except ImportError:
        install.pip(["playwright>=1.48"])
    if chromium_path() is None:
        import sys
        with bus.span("playwright install chromium", src="install"):
            install.sh([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], timeout=900)


class Browser:
    """One headless Chromium, one harness page per viewport. All methods are coroutines; the sync wrappers below
    run them on the lab's background loop so notebook cells stay simple."""

    def __init__(self, xite: Path, copilot: Path | None = None, viewport=(640, 360), gpu: bool | None = None):
        self.xite, self.copilot_dir, self.viewport = Path(xite), copilot, tuple(viewport)
        self.gpu_requested = want_gpu(gpu)
        self.assets: Path | None = None    # scenes' relative URLs (textures, fonts, inlines) resolve here
        self._pw = self._browser = self._ctx = None
        self._pages: dict = {}
        self._console: list = []
        self._lock = asyncio.Lock()
        self.info: dict = {}

    # ------------------------------------------------------------------ lifecycle
    async def astart(self, headless: bool = True) -> "Browser":
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        exe = chromium_path()
        self._browser = await self._pw.chromium.launch(headless=headless, args=GPU_ARGS if self.gpu_requested else CHROME_ARGS,
                                                       **({"executable_path": exe} if exe else {}))
        self._ctx = await self._browser.new_context(viewport={"width": self.viewport[0], "height": self.viewport[1]},
                                                    device_scale_factor=1)
        await self._ctx.route(ORIGIN + "/**", self._serve)
        page = await self._page(self.viewport)
        self.info = {"chromium": self._browser.version, "xite": pins.get("x_ite", "version"),
                     "webgl": await page.evaluate("""() => { const c = document.createElement('canvas').getContext('webgl2');
                         if (!c) return 'none'; const d = c.getExtension('WEBGL_debug_renderer_info');
                         return d ? c.getParameter(d.UNMASKED_RENDERER_WEBGL) : 'webgl2'; }""")}
        self.info["gpu_requested"] = self.gpu_requested
        self.info["gpu"] = not is_software(self.info["webgl"])
        if self.gpu_requested and not self.info["gpu"]:
            bus.warn("Chrome GPU mode requested but WebGL runs on %s: timings are CPU timings" % self.info["webgl"], src="browser")
        bus.info("headless Chromium %s · X_ITE %s · %s" % (self.info["chromium"], self.info["xite"], self.info["webgl"]),
                 src="browser", data=self.info)
        return self

    async def aclose(self) -> None:
        for obj in (self._ctx, self._browser):
            with contextlib.suppress(Exception):
                await obj.close()
        if self._pw:
            with contextlib.suppress(Exception):
                await self._pw.stop()
        self._pw = self._browser = self._ctx = None
        self._pages.clear()

    async def _serve(self, route) -> None:
        path = route.request.url[len(ORIGIN):].split("?")[0]
        if self.assets is not None and path.startswith("/harness/") and not re.fullmatch(r"/harness/\d+x\d+", path):
            f = (Path(self.assets) / path[len("/harness/"):]).resolve()
            if Path(self.assets).resolve() in f.parents and f.is_file():
                ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                return await route.fulfill(status=200, body=f.read_bytes(), content_type=ctype)
            return await route.fulfill(status=404, body="not found")
        if path.startswith("/harness"):
            m = re.match(r"/harness/(\d+)x(\d+)", path)
            w, h = (int(m.group(1)), int(m.group(2))) if m else self.viewport
            return await route.fulfill(status=200, content_type="text/html", body=HARNESS % {"w": w, "h": h})
        for prefix, root in (("/xite/", self.xite), ("/copilot/", self.copilot_dir)):
            if root and path.startswith(prefix):
                rel = path[len(prefix):] or "index.html"
                f = (Path(root) / rel).resolve()
                if Path(root).resolve() in f.parents and f.is_file():
                    ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                    return await route.fulfill(status=200, body=f.read_bytes(), content_type=ctype,
                                               headers={"cache-control": "max-age=3600"})
        await route.fulfill(status=404, body="not found")

    async def _page(self, size):
        size = tuple(size)
        p = self._pages.get(size)
        if p is None or p.is_closed():
            p = await self._ctx.new_page()
            await p.set_viewport_size({"width": size[0], "height": size[1]})
            p.on("console", lambda m: self._console.append((time.time(), m.type, m.text)))
            p.on("pageerror", lambda e: self._console.append((time.time(), "error", "pageerror: %s" % e)))
            await p.goto("%s/harness/%dx%d" % (ORIGIN, size[0], size[1]))
            await p.evaluate("() => window.lab.ready.then(() => true)")
            self._pages[size] = p
        return p

    # ------------------------------------------------------------------ the work
    async def aload(self, xml: str, screenshot: bool = False, size=None, frames: int = 0, timeout: float = 60,
                    background: str | None = None, antialiased: bool = True) -> LoadResult:
        """Load one scene. `background` ("1 0 1") replaces every Background with a flat colour (silhouettes)."""
        if background is not None:
            xml = flat_background(xml, background)
        async with self._lock:                         # one X_ITE world at a time per page
            page = await self._page(size or self.viewport)
            await page.bring_to_front()
            mark = len(self._console)
            t0 = time.perf_counter()
            try:
                d = await asyncio.wait_for(page.evaluate("([x, o]) => window.lab.load(x, o)",
                                                         [xml, {"frames": frames, "antialiased": antialiased}]), timeout)
            except asyncio.TimeoutError:
                await page.close()                     # a wedged world: start a fresh page next time
                return LoadResult(False, ["timeout after %.0f s" % timeout], ms=(time.perf_counter() - t0) * 1000)
            except Exception as exc:
                return LoadResult(False, ["harness: %s" % str(exc)[:300]], ms=(time.perf_counter() - t0) * 1000)
            png = None
            if screenshot and d.get("ok"):
                png = await page.locator("#c").screenshot(type="png")
            logs = self._console[mark:]
        errors = list(d.get("errors") or []) + [t for _, k, t in logs if k == "error" and not _benign(t)]
        warns = [t for _, k, t in logs if k in ("warning", "warn") and not _benign(t)]
        r = LoadResult(bool(d.get("ok")) and not any(e.startswith("pageerror") for e in errors), errors, warns, d.get("t") or {},
                       d.get("heap"), d.get("rootNodes"), d.get("version"), png, (time.perf_counter() - t0) * 1000)
        bus.emit("X_ITE %s" % r.summary(), level="DEBUG" if r.ok else "INFO", src="browser", kind="xite_load",
                 data={"ok": r.ok, "t": r.t, "heap": r.heap, "errors": r.errors[:3], "warnings": len(r.warnings)})
        return r

    async def acopilot(self, xml: str, timeout: float = 60) -> dict | None:
        """The Copilot validator's verdict, normalised to {ok, errors, raw}; None when no Copilot build is loaded."""
        if not self.copilot_dir:
            return None
        page = self._pages.get("copilot")
        if page is None or page.is_closed():
            page = await self._ctx.new_page()
            await page.goto(ORIGIN + "/copilot/index.html")
            await page.wait_for_function("() => window.__x3dcopilot && window.__x3dcopilot.validator", timeout=timeout * 1000)
            self._pages["copilot"] = page
        raw = await asyncio.wait_for(page.evaluate("""async (x) => {
            const r = await window.__x3dcopilot.validator.validate(x);
            try { return JSON.parse(JSON.stringify(r)); } catch (e) { return String(r); } }""", xml), timeout)
        return normalise_copilot(raw)

    async def ametrics(self) -> dict:
        """Chrome's Performance.getMetrics for the harness page (JS heap, nodes, layout and script time)."""
        page = await self._page(self.viewport)
        cdp = await self._ctx.new_cdp_session(page)
        try:
            await cdp.send("Performance.enable")
            m = await cdp.send("Performance.getMetrics")
            return {x["name"]: x["value"] for x in m["metrics"]}
        finally:
            with contextlib.suppress(Exception):
                await cdp.detach()

    async def atrace_start(self, screenshots: bool = True) -> None:
        page = await self._page(self.viewport)
        await self._browser.start_tracing(page=page, screenshots=screenshots,
                                          categories=["devtools.timeline", "disabled-by-default-devtools.timeline",
                                                      "disabled-by-default-devtools.timeline.frame", "blink.user_timing",
                                                      "v8.execute", "disabled-by-default-v8.cpu_profiler", "gpu", "loading"])

    async def atrace_stop(self, path: str | Path) -> dict:
        data = await self._browser.stop_tracing()
        Path(path).write_bytes(data)
        return trace_summary(data)

    # ------------------------------------------------------------------ sync helpers for notebook cells
    def load(self, xml: str, **kw) -> LoadResult:
        return aio.run(self.aload(xml, **kw), kw.get("timeout", 60) + 30)

    def copilot(self, xml: str) -> dict | None:
        return aio.run(self.acopilot(xml), 120)

    def metrics(self) -> dict:
        return aio.run(self.ametrics(), 60)

    @contextlib.contextmanager
    def trace(self, path: str | Path, screenshots: bool = True):
        """Record a Chrome trace of everything inside the block. Open the file in DevTools > Performance."""
        aio.run(self.atrace_start(screenshots), 60)
        box: dict = {}
        try:
            yield box
        finally:
            box.update(aio.run(self.atrace_stop(path), 120))
            box["path"] = str(path)
            bus.info("trace written: %s (%d events, %d x3d.lab bars)" % (path, box.get("events", 0), len(box.get("measures", []))),
                     src="browser", data={k: v for k, v in box.items() if k != "measures"})

    def close(self) -> None:
        aio.run(self.aclose(), 60)


def start(copilot: str | Path | None = None, viewport=(640, 360), headless: bool = True, install: bool = True,
          gpu: bool | None = None) -> Browser:
    """Start the headless browser. `copilot`: a folder with the Copilot build (lab.copilot_dir) to enable that oracle.
    `gpu`: try the real GPU (ANGLE on Vulkan) instead of SwiftShader; None follows $X3DLAB_CHROME_GPU."""
    if install:
        ensure_playwright()
    b = Browser(xite_dir(), Path(copilot) if copilot else None, viewport, gpu)
    return aio.run(b.astart(headless), 180)


# ---------------------------------------------------------------------- helpers (pure, unit-tested)
_BENIGN = ("WebGL", "GPU stall", "swiftshader", "SwiftShader", "favicon", "Automatic fallback to software WebGL",
           "GroupMarkerNotSet", "X_ITE v", "Welcome to X_ITE")


def _benign(text: str) -> bool:
    return any(b in text for b in _BENIGN)


def flat_background(xml: str, rgb: str = "1 0 1") -> str:
    """Remove Background/TextureBackground nodes and add a flat one first in the Scene, so silhouettes are easy."""
    xml = re.sub(r"<(Background|TextureBackground)\b[^>]*/>", "", xml)
    xml = re.sub(r"<(Background|TextureBackground)\b.*?</\1>", "", xml, flags=re.S)
    return re.sub(r"(<Scene\b[^>]*>)", r'\1<Background skyColor="%s"/>' % rgb, xml, count=1)


def normalise_copilot(raw) -> dict:
    if isinstance(raw, bool):
        return {"ok": raw, "errors": [], "raw": raw}
    if isinstance(raw, str):
        return {"ok": False, "errors": [raw[:300]], "raw": raw}
    if not isinstance(raw, dict):
        return {"ok": False, "errors": ["unexpected result %r" % (raw,)], "raw": raw}
    errs = raw.get("errors") or [i for i in (raw.get("issues") or []) if str(i.get("severity", "error")).lower() == "error"]
    errs = [e if isinstance(e, str) else (e.get("message") or json.dumps(e))[:300] for e in errs]
    ok = raw.get("valid", raw.get("ok", not errs))
    return {"ok": bool(ok), "errors": errs, "raw": raw}


def trace_summary(data: bytes) -> dict:
    """Events, x3d.lab measures and the busiest main-thread tasks from a Chrome trace."""
    try:
        d = json.loads(data)
    except ValueError:
        return {"events": 0, "measures": []}
    evs = d["traceEvents"] if isinstance(d, dict) else d
    measures, longest = [], []
    for e in evs:
        if e.get("cat") == "blink.user_timing" and e.get("ph") in ("b", "X") and e.get("name") not in ("", None):
            measures.append(e["name"])
        if e.get("name") == "RunTask" and e.get("dur"):
            longest.append(e["dur"] / 1000)
    longest.sort(reverse=True)
    return {"events": len(evs), "measures": measures, "long_tasks_ms": [round(x, 1) for x in longest[:5]],
            "tasks_over_50ms": sum(1 for x in longest if x > 50)}


def silhouette(png: bytes, mode: str = "key", key=(255, 0, 255), tol: int = 60):
    """Boolean mask of covered pixels: `key` = not the flat background colour (X_ITE), `alpha` = alpha > 0.5 (Cycles)."""
    import numpy as np
    from PIL import Image
    im = Image.open(io.BytesIO(png))
    if mode == "alpha":
        a = np.asarray(im.convert("RGBA"))[..., 3]
        return a > 127
    rgb = np.asarray(im.convert("RGB")).astype(int)
    return np.abs(rgb - np.array(key)).sum(axis=-1) > tol


def iou(a, b) -> float:
    import numpy as np
    if a.shape != b.shape:
        raise ValueError("mask sizes differ: %s vs %s" % (a.shape, b.shape))
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 1.0


def overlay_png(a, b) -> bytes:
    """Agreement picture: white both, red X_ITE only, cyan Cycles only, dark neither."""
    import numpy as np
    from PIL import Image
    img = np.zeros(a.shape + (3,), dtype=np.uint8) + 24
    img[a & b] = (235, 235, 235)
    img[a & ~b] = (230, 60, 60)
    img[~a & b] = (40, 200, 220)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()
