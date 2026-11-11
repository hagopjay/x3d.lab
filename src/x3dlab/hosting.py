"""Cell 1: start the x3d_mcp server and the gateway, publish them, prove they work, print the card.

    lab = x3dlab.up(tunnel="cloudflared")     # or "none", "ngrok"

Routes:
  colab   Colab's built-in port proxy. Private to your Google login, nothing to install, laptop only.
  tunnel  cloudflared (no account, random URL) or ngrok (token, optional fixed domain). Reaches your phone
          and remote MCP clients. Protected by the dashboard key (in the URL) and a separate MCP token.
  local   http://127.0.0.1:<port> from inside the VM (what the self-test and notebook code use).
"""

from __future__ import annotations

import io
import json
import os
import secrets
import sys
import tarfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import breakit, install, jobs as jobs_mod, keys, pins, procs, quickstart, runtime, tunnels, ui
from .events import bus
from .mcpclient import X3DMCP

_current = None


def current():
    if _current is None:
        raise RuntimeError("The lab is not running yet: run cell 1 (x3dlab.up()).")
    return _current


@dataclass
class Lab:
    dashboard_key: str
    mcp_token: str
    blob_token: str
    mcp_port: int = 8000
    gateway_port: int = 8765
    gateway_host: str = "127.0.0.1"
    expose_mcp: bool = True
    xite_src: str = ""
    xite_css: str = ""
    xite_dir: Path | None = None
    runtime_info: dict = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    urls: dict = field(default_factory=dict)
    selftest: list = field(default_factory=list)
    browsers: list = field(default_factory=list)
    copilot_dir: Path | None = None
    session: str = field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S"))
    jobs: jobs_mod.Jobs | None = None
    client: X3DMCP | None = None
    gateway: object = None
    panel: object = None
    blender: object = None
    runs: list = field(default_factory=list)
    upstream_client: X3DMCP | None = None
    browser: object = None

    # ------------------------------------------------------------------ addresses
    @property
    def mcp_base(self) -> str:
        return "http://127.0.0.1:%d" % self.mcp_port

    @property
    def mcp_host(self) -> str:
        return "127.0.0.1:%d" % self.mcp_port

    @property
    def out_dir(self) -> Path:
        return runtime.sub("out")

    def dashboard_url(self, route: str | None = None) -> str:
        route = route or ("tunnel" if "tunnel" in self.urls else "colab" if "colab" in self.urls else "local")
        base = self.urls[route].rstrip("/")
        return "%s/k/%s/" % (base, self.dashboard_key)

    def mcp_url(self, route: str | None = None) -> str:
        route = route or ("tunnel" if "tunnel" in self.urls else "local")
        return self.urls[route].rstrip("/") + "/mcp"

    def hosting_info(self) -> dict:
        """For the dashboard: route names and bases, never keys or tokens."""
        return {"routes": {k: v for k, v in self.urls.items()}, "selftest": [{"check": c, "ok": ok} for c, ok, _ in self.selftest]}

    def on_beacon(self, kind: str, data: dict) -> None:
        if kind == "hello":
            self.browsers.append({"t": time.time(), **{k: data.get(k) for k in ("route", "ua", "gl", "dpr")}})
            bus.info("a browser opened the dashboard (%s, %s)" % (data.get("route", "?"), str(data.get("gl", "?"))[:60]),
                     src="browser")

    # ------------------------------------------------------------------ notebook helpers
    def scene(self, prompt: str, provider: str = "auto", model: str | None = None, mode: str = "simple",
              rounds: int = 2, version: str = "4.0") -> quickstart.SceneResult:
        """Run the prompt -> validate -> repair loop as a job (it shows up live on the dashboard too)."""
        job = self.jobs.submit("scene", dict(prompt=prompt, provider=provider, model=model, mode=mode, rounds=rounds,
                                            version=version))
        done = self.jobs.wait(job.id, timeout=900)
        if done.status == "error":
            raise RuntimeError(done.error)
        return done.result["_obj"]

    def check(self, xml: str):
        return self.client.check(xml)

    def breakit(self, defect: str, base: str | None = None):
        return self.client.run(breakit.run_one(self.client, defect, base or breakit.LANTERN))

    def tool(self, name: str, **args):
        return self.client.tool(name, **args)

    def load_copilot(self, source: str = "drive", **kw) -> str:
        from . import copilot
        self.copilot_dir = copilot.load(source, **kw)
        url = self.dashboard_url() + "copilot/"
        bus.info("Copilot Validator Demo mounted at …/copilot/", src="copilot")
        return url

    def setup_blender(self, prefer: str = "auto"):
        """Install/probe Blender and attach it, so stress runs can start from the dashboard too."""
        from . import blender
        self.blender = blender.setup(prefer)
        return self.blender

    def render(self, xml_or_path: str, samples: int = 128, resolution=(1280, 720), backend: str | None = None,
               denoise: bool = True, camera: str = "viewpoint") -> dict:
        """Render a scene (XML text or a file path) with Cycles. Returns the worker result plus `url` for the dashboard."""
        import uuid as _uuid
        if self.blender is None:
            self.setup_blender()
        rid = _uuid.uuid4().hex[:10]
        d = runtime.sub("out", rid)
        src = Path(xml_or_path) if not str(xml_or_path).lstrip().startswith("<") else None
        if src is None:
            src = d / "scene.x3d"
            src.write_text(xml_or_path, encoding="utf-8")
        r = self.blender.render(str(src), str(d / "render.png"), backend=backend, samples=samples, resolution=resolution,
                                denoise=denoise, camera=camera, job=rid)
        res = dict(r.get("result") or {}, ok=r["ok"], log=r["log"], url="out/%s/render.png" % rid, path=str(d / "render.png"))
        if r["ok"]:
            bus.emit("render ready", src="lab", kind="render", job=rid,
                     data={"url": res["url"], "case": "your scene · %s · %d spp" % (res.get("backend"), samples), "seconds": res.get("seconds", 0)})
        return res

    def stress(self, cfg=None, preset: str = "standard"):
        """Run the 02 Lab stress test (blocking). Rows stream to the panel and the dashboard."""
        from . import stress
        cfg = cfg or stress.RunConfig.preset_for(preset)
        run = stress.run(cfg, self, self.blender, self.browser)
        self.runs.append(run)
        return run

    def hog(self, cfg=None, **kw):
        """The GPU hog run: one corpus through every engine, findings per project (blocking). See x3dlab.hog."""
        from . import hog
        return hog.run(self, cfg, **kw)

    def foundry(self, cfg=None, **kw):
        """One Foundry round: target, generate, referee, engines, claims, shrink, write, learn (blocking). See x3dlab.foundry."""
        from . import foundry
        return foundry.run(self, cfg, **kw)

    def duel(self, cfg=None, **kw):
        """Same prompts to several models; x3d_mcp scores every scene (blocking). See x3dlab.duel."""
        from . import duel
        return duel.run(self, cfg, **kw)

    def research(self, cfg=None, preset: str = "research"):
        """Run the 03 research tiers (blocking): fuzz, matrix, cross-renderer, scale, traces."""
        return self.stress(cfg, preset)

    def status(self) -> dict:
        return {"processes": procs.status(), "routes": self.urls, "jobs": [j.public() for j in self.jobs.recent(5)],
                "browsers": self.browsers[-5:], "events_file": str(bus.jsonl_path)}

    def client_configs(self) -> dict:
        url, tok = self.mcp_url(), self.mcp_token
        return {
            "Claude Code": 'claude mcp add --transport http x3d-colab %s --header "Authorization: Bearer %s"' % (url, tok),
            "Claude Desktop (claude_desktop_config.json)": json.dumps({"mcpServers": {"x3d-colab": {
                "command": "npx", "args": ["-y", "mcp-remote", url, "--header", "Authorization:${X3D_LAB_AUTH}"],
                "env": {"X3D_LAB_AUTH": "Bearer " + tok}}}}, indent=2),
            "Cursor (.cursor/mcp.json)": json.dumps({"mcpServers": {"x3d-colab": {
                "url": url, "headers": {"Authorization": "Bearer " + tok}}}}, indent=2),
            "MCP Inspector": "npx @modelcontextprotocol/inspector\n# Transport: Streamable HTTP · URL: %s\n"
                             "# Authentication > header Authorization: Bearer %s" % (url, tok),
            "Python (mcp<2)": ("from mcp import ClientSession\nfrom mcp.client.streamable_http import streamablehttp_client\n"
                               "async with streamablehttp_client(%r, headers={'Authorization': 'Bearer %s'}) as (r, w, _):\n"
                               "    async with ClientSession(r, w) as s:\n        await s.initialize()\n"
                               "        print([t.name for t in (await s.list_tools()).tools])" % (url, tok)),
        }

    def card(self) -> None:
        ui.show(_card_html(self))

    def start_upstream(self, port: int = 8001) -> X3DMCP:
        """The Web3D Consortium server (pinned upstream commit) on 127.0.0.1:`port`, never exposed by the gateway."""
        src = install.upstream()
        hostport = "127.0.0.1:%d" % port
        env = dict(os.environ, MCP_TRANSPORT="streamable-http", HOST="127.0.0.1", PORT=str(port), PYTHONPATH=str(src),
                   PYTHONUNBUFFERED="1", X3D_MCP_ALLOWED_HOSTS="%s,localhost:%d" % (hostport, port),
                   X3D_MCP_ALLOWED_ORIGINS="http://%s,http://localhost:%d" % (hostport, port))
        for k in [k for k in env if k.endswith("_API_KEY") or k.startswith("X3D_MCP_BLOB")]:
            env.pop(k)
        procs.start("x3d_mcp_upstream", [sys.executable, str(Path(src) / "server.py")], env=env, port=port,
                    health="http://127.0.0.1:%d/pulse" % port, timeout=120, classify=procs.mcp_log_classifier())
        self.upstream_client = X3DMCP("http://127.0.0.1:%d/mcp" % port)
        with bus.span("consortium validator warm-up", track="Validator", src="lab"):
            v = self.upstream_client.check(breakit.LANTERN)
        bus.info("consortium x3d_mcp %s on port %d: sample scene %s" % (pins.get("x3d_mcp", "upstream_ref")[:8], port,
                 "accepted" if v.ok else "REJECTED (%s)" % v.report(2)), src="lab")
        return self.upstream_client

    def start_browser(self, viewport=(640, 360), gpu: bool | None = None):
        """Headless Chromium + X_ITE for the 03 notebook (the Copilot oracle too, when load_copilot() ran).
        gpu=True tries the real GPU instead of SwiftShader; browser.info["gpu"] says what was used."""
        from . import browser as browser_mod
        if self.browser is not None:
            self.browser.close()
        self.browser = browser_mod.start(copilot=self.copilot_dir, viewport=viewport, gpu=gpu)
        return self.browser

    def down(self) -> None:
        if self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
        tunnels.stop_all()
        procs.stop_all()
        if self.panel:
            self.panel.close()
        bus.info("lab stopped", src="lab")


# ---------------------------------------------------------------------- X_ITE source
def _xite(mode: str) -> tuple[str, str, Path | None]:
    """'cdn' (default) or 'npm' (download the pinned tarball once and serve it from the gateway)."""
    ver = pins.get("x_ite", "version")
    if mode == "cdn":
        js = pins.xite_url(ver)
        return js, js.replace("x_ite.min.js", "x_ite.css"), None
    dest = runtime.sub("vendor", "x_ite-%s" % ver)
    if os.environ.get("X3DLAB_XITE_DIR"):                  # a local copy (offline machines, tests)
        dest = Path(os.environ["X3DLAB_XITE_DIR"])
    if not (dest / "x_ite.min.js").is_file():
        url = "https://registry.npmjs.org/x_ite/-/x_ite-%s.tgz" % ver
        with bus.span("download X_ITE %s from npm" % ver, src="install"):
            data = urllib.request.urlopen(url, timeout=120).read()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for m in tf.getmembers():
                if not m.name.startswith("package/dist/") or not (m.isfile() or m.isdir()) or ".." in m.name:
                    continue
                rel = m.name[len("package/dist/"):]
                if not rel:
                    continue
                target = dest / rel
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tf.extractfile(m).read())
    return "vendor/x_ite/x_ite.min.js", "vendor/x_ite/x_ite.css", dest


# ---------------------------------------------------------------------- job handlers
def _register_jobs(lab: Lab) -> None:
    def scene_job(job):
        p = job.params
        res = lab.client.run(quickstart.make_scene(
            lab.client, str(p.get("prompt") or "")[:4000], provider=str(p.get("provider") or "auto"), model=p.get("model") or None,
            mode="agent" if p.get("mode") == "agent" else "simple", rounds=max(0, min(int(p.get("rounds", 2)), 4)),
            version="4.1" if str(p.get("version")) == "4.1" else "4.0", job=job.id))
        out = res.summary()
        out["_obj"] = res
        return out

    def uses_llm(p):
        prov = str(p.get("provider") or "auto")
        return prov != "sample" and (prov != "auto" or bool(keys.available()))

    def breakit_job(job):
        o = lab.client.run(breakit.run_one(lab.client, str(job.params.get("defect")), job=job.id))
        return {**o.row(), "xml": o.xml}

    def validate_job(job):
        return lab.client.run(lab.client.verdict(str(job.params.get("xml") or ""), job=job.id)).to_dict()

    def stress_job(job):
        from . import stress
        preset = str(job.params.get("preset") or "standard")
        if preset not in stress.PRESETS:
            raise ValueError("unknown preset %r" % preset)
        run = lab.stress(stress.RunConfig.preset_for(preset))
        return {"run": run.id, "name": run.name, "summary": run.summary()}

    lab.jobs.register("stress", stress_job)
    lab.jobs.register("scene", scene_job, uses_llm)
    lab.jobs.register("breakit", breakit_job)
    lab.jobs.register("validate", validate_job)


# ---------------------------------------------------------------------- self-test
def _sse_hello(url: str, timeout: float = 10) -> bool:
    with httpx.Client(timeout=timeout) as h:
        with h.stream("GET", url, headers={"accept": "text/event-stream"}) as r:
            if r.status_code != 200:
                return False
            for chunk in r.iter_text():
                if "hello" in chunk or "data:" in chunk:
                    return True
    return False


def selftest(lab: Lab, route: str) -> list:
    base = lab.urls[route].rstrip("/")
    k = lab.dashboard_url(route)
    checks = []

    def add(name, fn):
        t = time.time()
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, "%s: %s" % (type(exc).__name__, str(exc)[:160])
        checks.append(("%s: %s" % (route, name), ok, detail))
        bus.emit("self-test %s: %s -> %s" % (route, name, "ok" if ok else "FAILED"), level="INFO" if ok else "ERROR",
                 src="selftest", data={"detail": detail, "ms": round((time.time() - t) * 1000)})

    with httpx.Client(timeout=20, follow_redirects=False) as h:
        add("gateway answers", lambda: ((r := h.get(base + "/healthz")).status_code == 200, r.status_code))
        add("dashboard key works", lambda: ((r := h.get(k + "api/health")).status_code == 200 and r.json()["mcp"] == "up",
                                            "%s mcp=%s" % (r.status_code, r.json().get("mcp") if r.status_code == 200 else "?")))
        add("wrong key refused", lambda: ((r := h.get(base + "/k/not-the-key/api/health")).status_code == 401, r.status_code))
        add("live event stream", lambda: (_sse_hello(k + "events"), "first bytes arrived"))
        if lab.expose_mcp:
            add("MCP refuses no token", lambda: ((r := h.post(base + "/mcp", json={})).status_code == 401, r.status_code))

            def mcp_ok():
                c = X3DMCP(base + "/mcp", token=lab.mcp_token)
                tools = c.run(c.list_tools(), timeout=60)
                return len(tools) > 10, "%d tools through the proxy" % len(tools)
            add("MCP tools/list with token", mcp_ok)
    return checks


# ---------------------------------------------------------------------- the card
def _card_html(lab: Lab) -> str:
    e = ui.esc
    rt = lab.runtime_info
    rows = []
    for name, ok, detail in lab.selftest:
        rows.append("<tr><td>%s</td><td style='color:%s'>%s</td><td style='color:#888'>%s</td></tr>"
                    % (e(name), "#1d6b3a" if ok else "#c62828", "ok" if ok else "FAILED", e(detail)))
    links = []
    if "colab" in lab.urls:
        links.append("<li><b>Colab proxy</b> (private to your Google login): <a href='%s' target='_blank'>open the dashboard</a></li>"
                     % e(lab.dashboard_url("colab")))
    if "tunnel" in lab.urls:
        links.append("<li><b>Tunnel</b> (works on your phone; the key is in the link, treat it like a password): "
                     "<a href='%s' target='_blank'>open the dashboard</a></li>" % e(lab.dashboard_url("tunnel")))
    if not links:
        links.append("<li>Local only: %s</li>" % e(lab.dashboard_url("local")))
    qr = ""
    if "tunnel" in lab.urls:
        qr = "<div style='float:right;margin:0 0 8px 12px;text-align:center'>%s<div style='font-size:11px;color:#888'>scan for your phone</div></div>" \
             % ui.qr_svg(lab.dashboard_url("tunnel"))
    mcp = ""
    if lab.expose_mcp and "tunnel" in lab.urls:
        mcp = "<h4 style='margin:14px 0 4px'>Connect an MCP client to this Colab</h4>"
        mcp += ui.copy_block("MCP endpoint", lab.mcp_url())
        mcp += ui.copy_block("Bearer token (new every run)", lab.mcp_token, secret=True)
        mcp += "".join("<details><summary>%s</summary>%s</details>" % (e(name), ui.copy_block(name, text, secret=True))
                       for name, text in lab.client_configs().items())
    elif lab.expose_mcp:
        mcp = ("<p style='font-size:13px'>Remote MCP clients (Claude Code, Claude Desktop, Cursor) need a public URL: "
               "rerun this cell with <code>TUNNEL = \"cloudflared\"</code>. Colab's own proxy only works inside your browser.</p>")
    return """<div style="font:14px/1.5 system-ui,sans-serif;border:1px solid #8885;border-radius:10px;padding:12px 16px;max-width:980px">
{qr}<div style="font-size:18px;font-weight:600">x3d.lab is running</div>
<div style="color:#888;font-size:12px">{rt} · x3d_mcp {mcp_pin} · X_ITE {xite}</div>
<ul style="margin:8px 0">{links}</ul>
<details><summary>Self-test ({n_ok}/{n} passed)</summary><table style="font-size:12px">{rows}</table></details>
{mcp}
<p style="font-size:12px;color:#888;margin-top:10px">Keys and tokens change every time this cell runs. Clear this output
(Edit &gt; Clear output) before you share or save the notebook with outputs. Stop everything with <code>lab.down()</code>.</p>
</div>""".format(qr=qr, rt=e(rt.get("line", "")), mcp_pin=e(pins.summary()["x3d_mcp"]), xite=e(pins.get("x_ite", "version")),
                 links="".join(links), rows="".join(rows), n=len(lab.selftest), n_ok=sum(1 for _, ok, _ in lab.selftest if ok),
                 mcp=mcp)


# ---------------------------------------------------------------------- up()
def up(tunnel: str = "none", *, expose_mcp: bool = True, show: str = "link", log_level: str = "INFO",
       log_density: str = "compact", log_to_screen: bool = True, mcp_port: int = 8000, gateway_port: int = 8765,
       gateway_host: str = "127.0.0.1", xite: str = "cdn", max_llm_runs: int = 30, ngrok_domain: str | None = None,
       panel: bool = True) -> Lab:
    """Start everything for cell 1 and return the Lab handle. Safe to rerun: old servers are replaced."""
    global _current
    if tunnel not in ("none", "cloudflared", "ngrok"):
        raise ValueError("tunnel must be 'none', 'cloudflared' or 'ngrok'")
    if _current is not None:
        try:
            _current.down()
        except Exception:
            pass
        _current = None

    session = time.strftime("%Y%m%d-%H%M%S")
    bus.configure(level=log_level, density=log_density, to_screen=log_to_screen,
                  jsonl=runtime.sub("sessions") / ("%s.jsonl" % session))
    lab_panel = None
    if panel and log_to_screen and ui.have_widgets():
        from IPython.display import display
        lab_panel = ui.LivePanel(level=log_level, density=log_density)
        bus.screen = None                      # the panel replaces line-by-line printing
        display(lab_panel.widget)

    rt = runtime.detect()
    info = rt.as_dict()
    info["line"] = rt.line()
    bus.info(rt.line(), src="lab", data=info)

    lab = Lab(dashboard_key=secrets.token_urlsafe(18), mcp_token=secrets.token_urlsafe(32), blob_token=secrets.token_urlsafe(32),
              mcp_port=mcp_port, gateway_port=gateway_port, gateway_host=gateway_host, expose_mcp=expose_mcp,
              runtime_info=info, session=session)
    lab.panel = lab_panel
    for s in (lab.dashboard_key, lab.mcp_token, lab.blob_token):
        bus.add_secret(s)
    lab.xite_src, lab.xite_css, lab.xite_dir = _xite(xite)

    # 1. the x3d_mcp server, bound to loopback only (the old notebook listened on 0.0.0.0)
    src = install.server_ready()
    hostport = "127.0.0.1:%d" % mcp_port
    env = dict(os.environ, MCP_TRANSPORT="streamable-http", HOST="127.0.0.1", PORT=str(mcp_port),
               X3D_MCP_BLOB_TOKEN=lab.blob_token, PYTHONPATH=str(src), PYTHONUNBUFFERED="1",
               # the SDK's DNS-rebinding guard: only loopback Host/Origin values (the gateway rewrites Host)
               X3D_MCP_ALLOWED_HOSTS="%s,localhost:%d" % (hostport, mcp_port),
               X3D_MCP_ALLOWED_ORIGINS="http://%s,http://localhost:%d" % (hostport, mcp_port))
    for k in [k for k in env if k.endswith("_API_KEY")]:
        env.pop(k)                             # the validator never needs model keys
    procs.start("x3d_mcp", [sys.executable, str(Path(src) / "server.py")], env=env, port=mcp_port,
                health="http://127.0.0.1:%d/pulse" % mcp_port, timeout=120, classify=procs.mcp_log_classifier())
    lab.client = X3DMCP("http://127.0.0.1:%d/mcp" % mcp_port, token=lab.blob_token)
    with bus.span("validator warm-up (loads the XSD and X3DUOM once)", track="Validator", src="lab"):
        warm = lab.client.check(breakit.LANTERN)
    if not warm.ok:
        bus.error("the validator rejected the built-in sample scene: %s" % warm.report(), src="lab")

    # 2. jobs and the gateway (in this kernel, so it shares the event stream)
    lab.jobs = jobs_mod.Jobs(max_llm_jobs=max_llm_runs)
    _register_jobs(lab)
    from .gateway import GatewayServer, create_app
    lab.gateway = GatewayServer(create_app(lab), gateway_host, gateway_port)
    lab.gateway.start()
    lab.urls["local"] = "http://127.0.0.1:%d" % gateway_port

    # 3. routes
    if rt.colab:
        try:
            from google.colab.output import eval_js
            lab.urls["colab"] = eval_js("google.colab.kernel.proxyPort(%d, {'cache': false})" % gateway_port).rstrip("/")
        except Exception as exc:
            bus.warn("Colab proxy URL unavailable: %s" % exc, src="lab")
    if tunnel == "cloudflared":
        lab.urls["tunnel"] = tunnels.cloudflared(gateway_port)
    elif tunnel == "ngrok":
        lab.urls["tunnel"] = tunnels.ngrok(gateway_port, ngrok_domain)

    # 4. prove it: locally, and through the tunnel the way a phone or Claude Code would arrive
    lab.selftest = selftest(lab, "local")
    if "tunnel" in lab.urls:
        time.sleep(2)                          # quick tunnels need a moment before the first request
        lab.selftest += selftest(lab, "tunnel")

    _current = lab
    lab.card()
    if show == "iframe" and rt.colab:
        try:
            from google.colab import output
            output.serve_kernel_port_as_iframe(gateway_port, path="/k/%s/" % lab.dashboard_key, height=720)
        except Exception as exc:
            bus.warn("could not embed the dashboard: %s" % exc, src="lab")
    failed = [c for c, ok, _ in lab.selftest if not ok]
    if failed:
        bus.error("self-test failures: %s" % "; ".join(failed), src="lab")
    else:
        bus.info("ready: %d self-test checks passed" % len(lab.selftest), src="lab")
    return lab
