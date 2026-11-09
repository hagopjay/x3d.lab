# %% [markdown]
# <a href="https://colab.research.google.com/github/hagopjay/x3d.lab/blob/main/notebooks/00_Basic_MCP.ipynb" target="_parent"><img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/></a>
#
# # x3d.lab · 00 Basic: an X3D MCP server on Colab, exposed and smoke-tested
#
# This notebook does three things, and every line of code is in the cells:
#
# 1. **Installs** an X3D [MCP](https://modelcontextprotocol.io) server: [hagopjay/x3d_mcp](https://github.com/hagopjay/x3d_mcp) by default, or the official [Web3DConsortium/x3d_mcp](https://github.com/Web3DConsortium/x3d_mcp) by changing one word.
# 2. **Exposes** it so Claude Code, Claude Desktop, Cursor or the MCP Inspector can use it: only inside Colab (`none`), through a Cloudflare quick tunnel (no account), or through ngrok.
# 3. **Smoke-tests** it thoroughly: the protocol, every tool, the validator's verdicts on good and broken scenes, concurrent load, big documents and hostile input. Then writes a report.
#
# A CPU runtime is enough. *Runtime › Run all* works; the whole notebook takes about two minutes (about one more with `RUN_SLOW_TOOLS`).
#
# **Why two servers?** The fork is ahead of upstream: exact-fix hints, ROUTE-order, colour-range and H-Anim checks, a blob store for scenes over the 4 MiB request limit, splat tools and a claims ledger (48 tools against 34). Upstream's `pyproject.toml` allows `mcp` 2.x, where `mcp.server.fastmcp` (which its server imports) no longer exists, so this notebook pins `mcp<2` for either server. Set `PIN_MCP_BELOW_2 = False` to see upstream's start-up failure for yourself.

# %%
#@title 1 · Settings { display-mode: "form" }
SERVER = "hagopjay"  #@param ["hagopjay", "consortium"]
TUNNEL = "none"  #@param ["none", "cloudflared", "ngrok"]
PIN_MCP_BELOW_2 = True  #@param {type:"boolean"}
RUN_SLOW_TOOLS = False  #@param {type:"boolean"}
LOCAL_XITE = False  #@param {type:"boolean"}
LOAD_SESSIONS = 16  #@param {type:"slider", min:1, max:64, step:1}
LOAD_CALLS_PER_SESSION = 10  #@param {type:"slider", min:1, max:50, step:1}
VERBOSE = False  #@param {type:"boolean"}
NGROK_DOMAIN = ""  #@param {type:"string"}

# The one place the two servers differ. Change `ref` to test another commit, branch or tag.
SERVERS = {
    "hagopjay":   {"repo": "hagopjay/x3d_mcp",         "ref": "13121b68e6d9c8ffe12cb8bc9996c1bf01a90d67"},
    "consortium": {"repo": "Web3DConsortium/x3d_mcp", "ref": "main"},
}
MCP_PORT = 8000      # the server itself, on 127.0.0.1 only
PROXY_PORT = 8080    # the token-checking proxy that a tunnel points at
CLOUDFLARED_VERSION = "2026.10.7"

import os
WORK = "/content" if os.path.isdir("/content") else os.path.expanduser("~/x3d_basic")
os.makedirs(WORK, exist_ok=True)
SRC_DIR = os.path.join(WORK, "x3d_mcp_" + SERVER)
print("server:", SERVERS[SERVER]["repo"], "@", SERVERS[SERVER]["ref"][:12], "| tunnel:", TUNNEL, "| work dir:", WORK)

# %% [markdown]
# ## 2 · Install
#
# Clones the chosen server at its ref and installs the dependencies listed in *its own* `pyproject.toml`, with `mcp` pinned below 2. Uses `uv` when Colab has it (much faster), `pip` otherwise.

# %%
#@title 2 · Install the server { display-mode: "form" }
import shutil, sys, tomllib

repo, ref = SERVERS[SERVER]["repo"], SERVERS[SERVER]["ref"]
if not os.path.isdir(os.path.join(SRC_DIR, ".git")):
    !git clone -q --depth 1 https://github.com/{repo}.git {SRC_DIR}
!git -C {SRC_DIR} fetch -q --depth 1 origin {ref} && git -C {SRC_DIR} checkout -q -f FETCH_HEAD
COMMIT = !git -C {SRC_DIR} log -1 --format=%h·%ad·%s --date=short
COMMIT = COMMIT[0]

with open(os.path.join(SRC_DIR, "pyproject.toml"), "rb") as fh:
    declared = tomllib.load(fh)["project"]["dependencies"]
deps = [d for d in declared if not (PIN_MCP_BELOW_2 and d.replace(" ", "").lower().startswith("mcp"))]
if PIN_MCP_BELOW_2:
    deps.append("mcp>=1.29,<2")
deps += ["psutil", "httpx", "qrcode"] + (["pyngrok>=7"] if TUNNEL == "ngrok" else [])
# Install into the interpreter that runs this kernel: `uv pip install --system` targets whichever Python uv finds first, which is not
# the kernel's own inside a virtual environment, a Jupyter install or CI (the server then fails with "No module named 'mcp'").
PIP = f"uv pip install --python {sys.executable} -q" if shutil.which("uv") else f"{sys.executable} -m pip install -q"
quoted = " ".join("'%s'" % d for d in deps)
print("installing:", ", ".join(deps))
!{PIP} {quoted}
if RUN_SLOW_TOOLS:   # render_image / render_current_scene drive a headless Chromium through Playwright (about a minute)
    !{PIP} playwright
    !{sys.executable} -m playwright install --with-deps chromium > /dev/null

import importlib.metadata as md
print("\n%s at %s" % (repo, COMMIT))
for name in ("mcp", "x3d", "lxml", "xmlschema", "rdflib", "numpy", "zstandard"):
    try:
        print("  %-10s %s" % (name, md.version(name)))
    except md.PackageNotFoundError:
        pass
print("  python    ", sys.version.split()[0])

# %% [markdown]
# ## 3 · Start the server
#
# The server listens on `127.0.0.1` only: nothing outside this VM can reach it except through the proxy in the next cell. Rerunning this cell first stops any server left on the port. That matters because a server process survives a kernel restart, and a health check that only asks "does the port answer?" would be fooled by the old one, so this cell also checks that the process answering is the one it just started.

# %%
#@title 3 · Start it on 127.0.0.1 { display-mode: "form" }
import json, re, secrets, signal, subprocess, threading, time, urllib.request
import psutil

def listeners(port):
    return {c.pid for c in psutil.net_connections("tcp") if c.status == "LISTEN" and c.laddr.port == port and c.pid}

def free_port(port):
    for pid in listeners(port) - {os.getpid()}:
        print("stopping pid %d left on port %d" % (pid, port))
        try:
            psutil.Process(pid).terminate(); psutil.Process(pid).wait(5)
        except psutil.Error:
            pass

free_port(MCP_PORT)

# Optional: serve X_ITE (the 3D browser the render tools load) from this VM instead of the jsdelivr CDN.
# The fork reads X3D_MCP_XITE_JS; upstream always uses the CDN.
xite_env = {}
if LOCAL_XITE:
    import functools, http.server, io, tarfile
    render_py = open(os.path.join(SRC_DIR, "src", "tools", "render.py")).read()
    xite_ver = (re.search(r'_XITE_VERSION = "([0-9.]+)"', render_py) or [None, "16.4.3"])[1]
    xite_dir = os.path.join(WORK, "x_ite-" + xite_ver)
    if not os.path.exists(os.path.join(xite_dir, "x_ite.min.js")):
        data = urllib.request.urlopen("https://registry.npmjs.org/x_ite/-/x_ite-%s.tgz" % xite_ver, timeout=120).read()
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            for m in tf.getmembers():
                if m.isfile() and m.name.startswith("package/dist/") and ".." not in m.name:
                    target = os.path.join(xite_dir, m.name[len("package/dist/"):])
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    open(target, "wb").write(tf.extractfile(m).read())
    free_port(8090)
    class XiteFiles(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):          # X_ITE fetches its CSS and WebAssembly cross-origin, like it would from the CDN
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()
        def log_message(self, *args):
            pass
    xite_http = http.server.ThreadingHTTPServer(("127.0.0.1", 8090), functools.partial(XiteFiles, directory=xite_dir))
    threading.Thread(target=xite_http.serve_forever, daemon=True).start()
    xite_env = {"X3D_MCP_XITE_JS": "http://127.0.0.1:8090/x_ite.min.js"}
    print("X_ITE %s served locally for the render tools" % xite_ver + ("" if "_XITE_ENV" in render_py else " (this server ignores it)"))

SERVER_TOKEN = secrets.token_urlsafe(32)      # the server's own blob-store token (hagopjay); never shown
LOG_PATH = os.path.join(WORK, "x3d_mcp_server.log")
env = dict(os.environ, MCP_TRANSPORT="streamable-http", HOST="127.0.0.1", PORT=str(MCP_PORT), PYTHONUNBUFFERED="1",
           X3D_MCP_BLOB_TOKEN=SERVER_TOKEN,
           X3D_MCP_ALLOWED_HOSTS="127.0.0.1:%d,localhost:%d" % (MCP_PORT, MCP_PORT),
           X3D_MCP_ALLOWED_ORIGINS="http://127.0.0.1:%d,http://localhost:%d" % (MCP_PORT, MCP_PORT), **xite_env)
server = subprocess.Popen([sys.executable, os.path.join(SRC_DIR, "src", "server.py")], cwd=SRC_DIR, env=env,
                          stdout=open(LOG_PATH, "w"), stderr=subprocess.STDOUT, start_new_session=True)
MCP_URL = "http://127.0.0.1:%d/mcp" % MCP_PORT

def show_log(n=25):
    print("".join(open(LOG_PATH, errors="replace").readlines()[-n:]))

t0 = time.time()
while True:
    if server.poll() is not None:
        show_log(40)
        raise RuntimeError("the server exited with code %s (log above)" % server.returncode)
    try:
        pulse = json.load(urllib.request.urlopen("http://127.0.0.1:%d/pulse" % MCP_PORT, timeout=2))
        assert server.pid in listeners(MCP_PORT), "port %d answers but not from our process" % MCP_PORT
        break
    except (OSError, ValueError):
        if time.time() - t0 > 120:
            show_log(40)
            raise RuntimeError("no answer on /pulse after 120 s")
        time.sleep(0.5)
print("server pid %d is up in %.1f s: %s" % (server.pid, time.time() - t0, pulse))

# %% [markdown]
# ## 4 · Expose it
#
# A small proxy on port 8080 sits in front of the server. It forwards everything to `127.0.0.1:8000` but only with `Authorization: Bearer <token>`; the token is new every run. The tunnel points at the proxy, never at the server.
#
# | `TUNNEL` | Reachable by | Needs |
# |---|---|---|
# | `none` | code in this notebook only. Colab's own port proxy needs your Google login cookie, which MCP clients don't have | nothing |
# | `cloudflared` | any MCP client, anywhere | nothing: a random `trycloudflare.com` URL |
# | `ngrok` | any MCP client, optionally on a fixed domain | a free `NGROK_AUTHTOKEN` in Colab Secrets (key icon) |

# %%
#@title 4 · Start the proxy and the tunnel { display-mode: "form" }
import hmac, re, stat, threading
import httpx, uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

MCP_TOKEN = secrets.token_urlsafe(32)
HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
       "authorization", "cookie", "origin", "referer", "accept-encoding"}
upstream = httpx.AsyncClient(base_url="http://127.0.0.1:%d" % MCP_PORT, timeout=httpx.Timeout(10, read=600))

async def forward(request: Request):
    if request.url.path == "/healthz":
        return JSONResponse({"ok": True})
    scheme, _, given = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode(), MCP_TOKEN.encode()):
        return JSONResponse({"error": "missing or wrong bearer token"}, 401, {"www-authenticate": "Bearer"})
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    headers.update({"host": "127.0.0.1:%d" % MCP_PORT, "accept-encoding": "identity",
                    "authorization": "Bearer " + SERVER_TOKEN})
    body = request.stream() if request.method in ("POST", "PUT") else None
    req = upstream.build_request(request.method, request.url.path, params=request.query_params, headers=headers, content=body)
    resp = await upstream.send(req, stream=True)
    if VERBOSE:
        print("proxy", request.method, request.url.path, "->", resp.status_code)
    async def relay():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
    return StreamingResponse(relay(), resp.status_code, {k: v for k, v in resp.headers.items() if k.lower() not in HOP})

if "proxy_server" in globals():
    proxy_server.should_exit = True; time.sleep(1)
free_port(PROXY_PORT)
proxy_server = uvicorn.Server(uvicorn.Config(Starlette(routes=[Route("/{path:path}", forward, methods=["GET", "POST", "PUT", "DELETE", "HEAD"])]),
                                             host="127.0.0.1", port=PROXY_PORT, log_level="warning", ws="none"))
threading.Thread(target=proxy_server.run, daemon=True).start()
while not proxy_server.started:
    time.sleep(0.05)

PUBLIC_URL = None
if TUNNEL == "cloudflared":
    exe = os.path.join(WORK, "cloudflared")
    if not os.path.exists(exe):
        urllib.request.urlretrieve("https://github.com/cloudflare/cloudflared/releases/download/%s/cloudflared-linux-amd64"
                                   % CLOUDFLARED_VERSION, exe)
        os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
    for p in psutil.process_iter(["name"]):
        if p.info["name"] == "cloudflared":
            p.terminate()
    cf_log = os.path.join(WORK, "cloudflared.log")     # a log file, not a pipe: an unread pipe can stall the tunnel
    tunnel = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:%d" % PROXY_PORT],
                              stdout=open(cf_log, "w"), stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(90):
        m = re.search(r"https://[-a-z0-9]+\.trycloudflare\.com", open(cf_log).read())
        if m:
            PUBLIC_URL = m.group(0); break
        time.sleep(0.5)
    if not PUBLIC_URL:
        raise RuntimeError("cloudflared gave no URL:\n" + open(cf_log).read()[-1500:])
    time.sleep(3)                                      # a fresh quick tunnel needs a moment
elif TUNNEL == "ngrok":
    from pyngrok import conf, ngrok
    token = os.environ.get("NGROK_AUTHTOKEN")
    if not token:
        try:
            from google.colab import userdata
            token = userdata.get("NGROK_AUTHTOKEN")
        except Exception:
            pass
    if not token:
        raise RuntimeError("add NGROK_AUTHTOKEN in Colab Secrets (free at ngrok.com), or use TUNNEL='cloudflared'")
    conf.get_default().auth_token = token
    ngrok.kill()
    PUBLIC_URL = ngrok.connect(PROXY_PORT, "http", **({"domain": NGROK_DOMAIN} if NGROK_DOMAIN else {})).public_url.replace("http://", "https://")

ENDPOINT = (PUBLIC_URL or "http://127.0.0.1:%d" % PROXY_PORT) + "/mcp"

# A quick check through the endpoint, the way a remote client arrives
with httpx.Client(timeout=30) as h:
    base = ENDPOINT[:-4]
    checks = {"proxy answers": h.get(base + "/healthz").status_code == 200,
              "no token -> 401": h.post(ENDPOINT, json={}).status_code == 401,
              "token -> MCP handshake": h.post(ENDPOINT, headers={"authorization": "Bearer " + MCP_TOKEN,
                  "accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "check", "version": "0"}}}).status_code == 200}
print("endpoint checks:", ", ".join("%s %s" % (k, "ok" if v else "FAILED") for k, v in checks.items()))

from IPython.display import HTML, display
def block(label, text, secret=False):
    bid = "b" + secrets.token_hex(4)
    return ("<div style='margin:6px 0'><b style='font-size:12px'>%s</b> <button onclick=\"navigator.clipboard.writeText("
            "document.getElementById('%s').innerText)\">copy</button><pre id='%s' onclick=\"this.style.filter='none'\" "
            "style='margin:2px 0;padding:6px;border:1px solid #8884;border-radius:6px;white-space:pre-wrap;word-break:break-all;%s'>%s</pre></div>"
            % (label, bid, bid, "filter:blur(5px);cursor:pointer" if secret else "", text.replace("&", "&amp;").replace("<", "&lt;")))
configs = {
    "Claude Code": 'claude mcp add --transport http x3d-colab %s --header "Authorization: Bearer %s"' % (ENDPOINT, MCP_TOKEN),
    "Claude Desktop (claude_desktop_config.json, via mcp-remote)": json.dumps({"mcpServers": {"x3d-colab": {
        "command": "npx", "args": ["-y", "mcp-remote", ENDPOINT, "--header", "Authorization:${X3D_AUTH}"],
        "env": {"X3D_AUTH": "Bearer " + MCP_TOKEN}}}}, indent=2),
    "Cursor (.cursor/mcp.json)": json.dumps({"mcpServers": {"x3d-colab": {"url": ENDPOINT, "headers": {"Authorization": "Bearer " + MCP_TOKEN}}}}, indent=2),
    "MCP Inspector": "npx @modelcontextprotocol/inspector\n# Transport: Streamable HTTP · URL: %s\n# Header: Authorization: Bearer %s" % (ENDPOINT, MCP_TOKEN),
}
html = "<div style='font:14px system-ui;border:1px solid #8885;border-radius:10px;padding:10px 14px;max-width:900px'>"
html += "<div style='font-size:17px;font-weight:600'>%s is serving MCP</div>" % repo
if PUBLIC_URL:
    try:
        import qrcode, qrcode.image.svg
        html += "<div style='float:right;background:#fff;border-radius:6px'>%s</div>" % qrcode.make(
            ENDPOINT, image_factory=qrcode.image.svg.SvgPathImage, box_size=3, border=2).to_string(encoding="unicode")
    except Exception:
        pass
    html += block("Endpoint", ENDPOINT) + block("Bearer token (new every run; click to reveal)", MCP_TOKEN, secret=True)
    html += "".join("<details><summary>%s</summary>%s</details>" % (k, block(k, v, secret=True)) for k, v in configs.items())
else:
    html += ("<p>TUNNEL is <code>none</code>: the server is reachable from this notebook only (the smoke test below uses it "
             "directly). For Claude Code, Desktop or Cursor, set TUNNEL to <code>cloudflared</code> and rerun cells 1, 4 and on.</p>")
html += "<p style='font-size:12px;color:#888'>Clear this output before sharing the notebook. Stop everything with the last cell.</p></div>"
display(HTML(html))

# %% [markdown]
# ## 5 · Smoke test
#
# Five parts, each its own cell, each adding rows to one results table:
#
# | | Checks | Fails when |
# |---|---|---|
# | **5a** Protocol | handshake, capabilities, tools/resources/prompts listings, schema sanity, ping | the server breaks MCP itself |
# | **5b** Every tool | each tool called with realistic arguments (stateful tools as chains), timed | a call raises or times out. A tool answering with an error *message* is a warning: it may be the arguments |
# | **5c** Verdicts | known-good scenes pass; 16 known defects are caught by the expected layer | a good scene fails or a defect slips through |
# | **5d** Load | `LOAD_SESSIONS` concurrent sessions × `LOAD_CALLS_PER_SESSION` validations; latency percentiles, memory | any call fails |
# | **5e** Big & hostile | 0.5–20 MB scenes, entity bombs, external entities, deep nesting, binary junk, local-file reads | the server dies, hangs, or leaks a local file |
#
# The smoke test talks to the server directly on `127.0.0.1` (the proxy only adds the token check, which cell 4 already tested).

# %%
#@title 5 · Smoke-test kit (scenes, helpers, results table) { display-mode: "form" }
import asyncio, base64, statistics, struct, traceback
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

GOOD = """<?xml version="1.0" encoding="UTF-8"?>
<X3D profile="Immersive" version="4.0">
  <head><meta name="title" content="Spinning lantern"/></head>
  <Scene>
    <NavigationInfo type='"EXAMINE" "ANY"'/>
    <Background skyColor="0.05 0.07 0.15"/>
    <Viewpoint description="front" position="0 1.6 8" orientation="1 0 0 -0.12"/>
    <DirectionalLight direction="-0.3 -1 -0.4" intensity="0.8"/>
    <Transform DEF="Spinner" translation="0 1 0">
      <Shape DEF="Body">
        <Appearance><Material DEF="Glow" diffuseColor="0.95 0.55 0.1" emissiveColor="0.3 0.12 0"/></Appearance>
        <Box size="1 1.4 1"/>
      </Shape>
      <Transform translation="0 1 0">
        <Shape><Appearance><Material diffuseColor="0.6 0.1 0.1"/></Appearance><Cone bottomRadius="0.9" height="0.6"/></Shape>
      </Transform>
    </Transform>
    <Transform translation="2.5 0.7 0" scale="0.6 0.6 0.6"><Shape USE="Body"/></Transform>
    <TimeSensor DEF="Clock" cycleInterval="6" loop="true"/>
    <OrientationInterpolator DEF="Turn" key="0 0.5 1" keyValue="0 1 0 0 0 1 0 3.14159 0 1 0 6.28318"/>
    <ROUTE fromNode="Clock" fromField="fraction_changed" toNode="Turn" toField="set_fraction"/>
    <ROUTE fromNode="Turn" fromField="value_changed" toNode="Spinner" toField="set_rotation"/>
  </Scene>
</X3D>"""
GOOD2 = """<?xml version="1.0" encoding="UTF-8"?>
<X3D profile="Interchange" version="4.0"><Scene><Viewpoint position="0 0 6"/>
<Shape><Appearance><Material diffuseColor="0.2 0.6 1"/></Appearance><Sphere radius="1.5"/></Shape></Scene></X3D>"""

RESULTS = []          # one row per check: part, name, status (PASS/WARN/FAIL/SKIP), ms, detail

def record(part, name, status, ms=None, detail=""):
    RESULTS[:] = [r for r in RESULTS if (r["part"], r["name"]) != (part, name)]
    RESULTS.append({"part": part, "name": name, "status": status, "ms": None if ms is None else round(ms, 1), "detail": str(detail)[:300]})
    if VERBOSE or status in ("FAIL",):
        print("%-4s %-5s %-34s %8s  %s" % (part, status, name, "" if ms is None else "%.0f ms" % ms, str(detail)[:120]))

def table(part=None):
    rows = [r for r in RESULTS if part is None or r["part"] == part]
    colors = {"PASS": "#1d6b3a", "WARN": "#9a5b00", "FAIL": "#c62828", "SKIP": "#888"}
    counts = {s: sum(r["status"] == s for r in rows) for s in colors}
    head = " · ".join("<b style='color:%s'>%d %s</b>" % (colors[s], n, s) for s, n in counts.items() if n)
    body = "".join("<tr><td>%s</td><td style='color:%s'>%s</td><td>%s</td><td align=right>%s</td><td style='color:#888'>%s</td></tr>"
                   % (r["part"], colors[r["status"]], r["status"], r["name"], "" if r["ms"] is None else "%.0f" % r["ms"],
                      r["detail"].replace("<", "&lt;")[:160]) for r in rows)
    display(HTML("<div style='font:13px system-ui'>%s</div><table style='font-size:12px'><tr><th>part</th><th>status</th>"
                 "<th align=left>check</th><th>ms</th><th align=left>detail</th></tr>%s</table>" % (head, body)))

def session(url=MCP_URL, token=SERVER_TOKEN, read_timeout=120):
    """An MCP client session to the server (async context manager)."""
    class _S:
        async def __aenter__(self):
            self.http = create_mcp_http_client(headers={"Authorization": "Bearer " + token}, timeout=httpx.Timeout(30, read=read_timeout))
            await self.http.__aenter__()
            self.ctx = streamable_http_client(url, http_client=self.http)
            r, w, _ = await self.ctx.__aenter__()
            self.cs = ClientSession(r, w)
            await self.cs.__aenter__()
            self.init = await self.cs.initialize()
            return self.cs
        async def __aexit__(self, *exc):
            for c in (self.cs, self.ctx, self.http):
                try:
                    await c.__aexit__(None, None, None)
                except Exception:
                    pass
    return _S()

def text_of(res):
    return "\n".join(c.text for c in res.content if getattr(c, "text", None))

def server_alive():
    try:
        return server.poll() is None and urllib.request.urlopen("http://127.0.0.1:%d/pulse" % MCP_PORT, timeout=5).status == 200
    except OSError:
        return False

def rss_mb():
    try:
        return psutil.Process(server.pid).memory_info().rss / 2**20
    except psutil.Error:
        return float("nan")

def run(coro):
    """Run async code from a cell, whether or not an event loop is already running (Colab has one)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()

def raw_call(tool, args, timeout=240):
    """tools/call over plain JSON-RPC (no client session): an HTTP error is just a status code here."""
    hdr = {"Authorization": "Bearer " + SERVER_TOKEN, "accept": "application/json, text/event-stream"}
    with httpx.Client(timeout=timeout) as h:
        r = h.post(MCP_URL, headers=hdr, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke-raw", "version": "0"}}})
        sid = r.headers.get("mcp-session-id")
        hdr.update({"mcp-session-id": sid} if sid else {})
        h.post(MCP_URL, headers=hdr, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = h.post(MCP_URL, headers=hdr, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        payload = None
        if r.status_code == 200:
            body = r.text
            if "text/event-stream" in r.headers.get("content-type", ""):
                body = "".join(line[5:].strip() for line in body.splitlines() if line.startswith("data:"))
            try:
                payload = json.loads(body)
            except ValueError:
                payload = None
        if sid:
            h.delete(MCP_URL, headers=hdr)
        return r.status_code, payload

async def verdict(s, xml):
    """(xsd_valid, semantic_errors, first_message) from validate_x3d + validate_semantic."""
    x = json.loads(text_of(await s.call_tool("validate_x3d", {"content": xml})))
    m = text_of(await s.call_tool("validate_semantic", {"content": xml}))
    head = m.strip().splitlines()[0] if m.strip() else ""
    if head.startswith("# Semantic Check:") and "All Clear" not in head and "Report" not in head:
        sem = [head.split(":", 1)[1].strip()]
    else:
        sem, section = [], None
        for line in m.splitlines():
            if line.startswith("## "):
                section = line[3:].strip().lower()
            elif section and section.startswith("error") and line.strip().startswith("- **["):
                sem.append(line.strip()[5:].split("]", 1)[0])
    first = (x.get("errors") or [""])[0] if not x.get("valid") else (sem[0] if sem else "")
    return bool(x.get("valid")), sem, first

print("kit ready: results go to RESULTS; table() shows them")

# %%
#@title 5a · Protocol { display-mode: "form" }
async def part_a():
    t = time.perf_counter()
    sess = session()
    s = await sess.__aenter__()
    try:
        init = sess.init
        record("5a", "initialize handshake", "PASS", (time.perf_counter() - t) * 1000,
               "%s %s · protocol %s" % (init.serverInfo.name, init.serverInfo.version, init.protocolVersion))
        caps = init.capabilities.model_dump(exclude_none=True)
        record("5a", "capabilities", "PASS" if "tools" in caps else "FAIL", None, ", ".join(sorted(caps)))
        record("5a", "server instructions", "PASS" if (init.instructions or "").strip() else "WARN", None,
               "%d chars" % len(init.instructions or ""))
        t = time.perf_counter()
        await s.send_ping()
        record("5a", "ping", "PASS", (time.perf_counter() - t) * 1000)
        res = await s.list_tools()
        tools = res.tools
        names = [t.name for t in tools]
        record("5a", "tools/list", "PASS" if tools else "FAIL", None, "%d tools" % len(tools))
        record("5a", "tool names unique", "PASS" if len(set(names)) == len(names) else "FAIL")
        bad = [t.name for t in tools if not (t.inputSchema or {}).get("type") == "object"]
        record("5a", "input schemas are objects", "FAIL" if bad else "PASS", None, ", ".join(bad))
        nodesc = [t.name for t in tools if len((t.description or "").strip()) < 20]
        record("5a", "every tool has a description", "WARN" if nodesc else "PASS", None, ", ".join(nodesc))
        try:
            rs = (await s.list_resources()).resources
            record("5a", "resources/list", "PASS", None, ", ".join(str(r.uri) for r in rs) or "none")
            for r in rs[:3]:
                t = time.perf_counter()
                body = await s.read_resource(r.uri)
                size = sum(len(getattr(c, "text", "") or "") for c in body.contents)
                record("5a", "read %s" % r.uri, "PASS" if size else "WARN", (time.perf_counter() - t) * 1000, "%d chars" % size)
        except Exception as e:
            record("5a", "resources/list", "WARN", None, e)
        try:
            ps = (await s.list_prompts()).prompts
            record("5a", "prompts/list", "PASS", None, ", ".join(p.name for p in ps))
            for p in ps:
                args = {a.name: "a red cube" if "desc" in a.name else GOOD2 for a in (p.arguments or []) if a.required}
                got = await s.get_prompt(p.name, args)
                record("5a", "prompt %s" % p.name, "PASS" if got.messages else "WARN", None, "%d message(s)" % len(got.messages))
        except Exception as e:
            record("5a", "prompts", "WARN", None, e)
        try:
            r = await s.call_tool("no_such_tool_xyz", {})
            refused = r.isError
        except Exception:
            refused = True
        await s.send_ping()
        record("5a", "unknown tool refused, session survives", "PASS" if refused else "FAIL")
    finally:
        await sess.__aexit__(None, None, None)
    return tools

TOOLS = run(part_a())
table("5a")

# %%
#@title 5b · Every tool { display-mode: "form" }
SLOW = {"render_image", "render_current_scene"}          # need a headless browser in the server's environment
RENDERS = []                                              # (tool, base64 PNG) from the render tools

def tiny_splat_ply(n=12):
    """A minimal 3D Gaussian Splatting PLY (binary little-endian), n splats."""
    props = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    head = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n%send_header\n" % (n, "".join("property float %s\n" % p for p in props))
    body = b"".join(struct.pack("<14f", i * 0.1, 0, 0, 0.5, 0.2, 0.1, 2.0, -3, -3, -3, 1, 0, 0, 0) for i in range(n))
    return head.encode() + body

def blank(res):
    """True when every returned image is a single flat colour (a render that drew nothing)."""
    try:
        import io
        from PIL import Image
        imgs = [Image.open(io.BytesIO(base64.b64decode(c.data))) for c in res.content if getattr(c, "type", "") == "image"]
        return bool(imgs) and all(len(set(im.convert("RGB").getdata())) <= 1 for im in imgs)
    except Exception:
        return False

def synth(schema, name=""):
    """Arguments for a tool this notebook has no recipe for, built from its JSON Schema."""
    out = {}
    for key in schema.get("required", []):
        p = schema.get("properties", {}).get(key, {})
        t = p.get("type") or next((a.get("type") for a in p.get("anyOf", []) if a.get("type") != "null"), "string")
        if "enum" in p:
            out[key] = p["enum"][0]
        elif t == "string":
            k = key.lower()
            out[key] = GOOD if k in ("content", "x3d", "xml", "scene") else "Transform" if "node" in k or "type" in k else "test"
        elif t == "integer":
            out[key] = max(1, p.get("minimum", 1))
        elif t == "number":
            out[key] = 1.0
        elif t == "boolean":
            out[key] = False
        elif t == "array":
            out[key] = []
        else:
            out[key] = {}
    return out

async def part_b():
    have = {t.name: t for t in TOOLS}
    done = set()
    async with session(read_timeout=180) as s:
        async def call(name, args, expect_error=False, label=None):
            label = label or name
            done.add(name)
            if name not in have:
                return None
            if name in SLOW and not RUN_SLOW_TOOLS:
                record("5b", label, "SKIP", None, "slow (needs a headless browser): set RUN_SLOW_TOOLS")
                return None
            t = time.perf_counter()
            try:
                res = await asyncio.wait_for(s.call_tool(name, args), 120)
            except Exception as e:
                record("5b", label, "FAIL", (time.perf_counter() - t) * 1000, "%s: %s" % (type(e).__name__, e))
                return None
            ms = (time.perf_counter() - t) * 1000
            out = text_of(res)
            images = sum(getattr(c, "type", "") == "image" for c in res.content)
            RENDERS.extend((label, c.data) for c in res.content if getattr(c, "type", "") == "image")
            soft_error = res.isError or out.lstrip().startswith('{\n  "error"') or out.lstrip().startswith('{"error"')
            if expect_error:
                record("5b", label, "PASS" if soft_error else "WARN", ms, "refused as expected" if soft_error else "accepted: " + out[:100])
            elif images and blank(res):
                note = " ".join(text_of(res).split())[:110]
                record("5b", label, "WARN", ms, "blank image%s" % (": " + note if note else ": X_ITE drew nothing (CDN unreachable? try LOCAL_XITE)"))
            else:
                record("5b", label, "WARN" if soft_error else "PASS", ms, out[:140].replace("\n", " ") if soft_error
                       else "%d chars%s" % (len(out), " + %d image(s)" % images if images else ""))
            return out

        # stateless document tools
        for name, args in [
            ("validate_x3d", {"content": GOOD}), ("validate_semantic", {"content": GOOD}), ("autofix_x3d", {"content": GOOD}),
            ("repair_x3d", {"content": GOOD}), ("exact_fix_hints", {"content": GOOD}),
            ("convert_x3d", {"content": GOOD, "from_encoding": "xml", "to_encoding": "json"}),
            ("modify_x3d_node", {"content": GOOD, "def_name": "Glow", "field_changes": json.dumps({"diffuseColor": "0 0 1"})}),
            ("move_x3d_node", {"content": GOOD, "def_name": "Clock"}),
            ("remove_x3d_node", {"content": GOOD, "def_name": "Clock"}),
            ("add_x3d_route", {"content": GOOD, "from_node": "Clock", "from_field": "isActive", "to_node": "Clock", "to_field": "set_enabled"}),
            ("animate_x3d_node", {"content": GOOD, "target_def": "Spinner", "field_name": "translation", "from_value": "0 0 0", "to_value": "0 2 0"}),
            ("x3dom_page", {"content": GOOD2}), ("x3dom_starter", {}),
            ("render_image", {"content": GOOD2, "width": 160, "height": 120}),
        ]:
            await call(name, args)
        # knowledge tools
        for name, args in [
            ("describe_node", {"node_type": "OrientationInterpolator"}), ("list_nodes", {"component": "Grouping"}),
            ("list_profiles", {}), ("list_components", {}), ("node_parents", {"node_type": "ImageTexture"}),
            ("where_can_place", {"child": "ImageTexture", "limit": 5}), ("x3d_animation_info", {"topic": "interpolators"}),
            ("x3d_generation_schema", {"profile": "Interchange", "max_nodes": 5, "return_mode": "summary"}),
            ("describe_ontology_term", {"term": "Transform"}),
            ("query_ontology", {"sparql": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 3"}),
            ("query_ontology_page", {"sparql": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 20", "limit": 5}),
            ("create_geometry", {"shape": "sphere", "color": [0.2, 0.6, 1.0]}),
            ("compose_scene", {"objects": [{"shape": "box", "color": [1, 0, 0]}, {"shape": "sphere", "translation": [2, 0, 0]}]}),
        ]:
            await call(name, args)
        # the stateful scene builder, as one chain
        await call("reset_scene", {})
        await call("create_scene", {"description": "smoke test scene", "profile": "Immersive"})
        ids = {}
        for node, fields in [("Transform", {"translation": [0, 0.5, 0]}), ("Shape", None), ("Box", {"size": [2, 2, 2]}),
                             ("Appearance", None), ("Material", {"diffuseColor": [0.9, 0.4, 0.1]})]:
            out = await call("create_node", {"node_type": node, **({"fields": fields} if fields else {})}, label="create_node %s" % node)
            m = re.search(r"ID:\s*([-0-9a-f]{8,})", out or "")
            ids[node] = m.group(1) if m else None
        if all(ids.values()):
            await call("add_child", {"parent_id": ids["Shape"], "child_id": ids["Box"], "container_field": "geometry"}, label="add_child Box→Shape")
            await call("add_child", {"parent_id": ids["Appearance"], "child_id": ids["Material"], "container_field": "material"}, label="add_child Material→Appearance")
            await call("add_child", {"parent_id": ids["Shape"], "child_id": ids["Appearance"], "container_field": "appearance"}, label="add_child Appearance→Shape")
            await call("add_child", {"parent_id": ids["Transform"], "child_id": ids["Shape"]}, label="add_child Shape→Transform")
            await call("def_node", {"node_id": ids["Transform"], "name": "SmokeT"})
            await call("use_node", {"def_name": "SmokeT"})
            await call("set_field", {"node_id": ids["Transform"], "field_name": "rotation", "value": [1, 1, 0, 0.6]})
            await call("add_route", {"from_node": ids["Transform"], "from_field": "translation_changed", "to_node": ids["Transform"], "to_field": "set_translation"})
            built = await call("get_scene", {})
            await call("validate_current_scene", {})
            await call("render_current_scene", {"width": 160, "height": 120})
            current = next((r for r in RESULTS if r["name"] == "render_current_scene"), None)
            if current and current["status"] == "WARN" and current["detail"].startswith("blank") and built:
                # Cross-check: render the same scene XML with render_image. If that draws, the blank frame is the tool's fault.
                await call("render_image", {"content": built, "width": 160, "height": 120}, label="render_image (same scene as render_current_scene)")
                same = next((r for r in RESULTS if r["name"].startswith("render_image (same scene")), None)
                if same and same["status"] == "PASS":
                    record("5b", "render_current_scene vs render_image", "WARN", None,
                           "same scene: render_image draws it, render_current_scene returns a blank frame (likely a server bug)")
            await call("remove_node", {"node_id": ids["Box"]})
        await call("reset_scene", {}, label="reset_scene (end)")
        # blob store (hagopjay)
        if "blob_put" in have:
            out = await call("blob_put", {"content": GOOD, "name": "smoke.x3d", "media_type": "model/x3d+xml"})
            bid = json.loads(out)["blob_id"] if out and out.strip().startswith("{") else None
            if bid:
                await call("blob_info", {"blob_id": bid})
                await call("blob_read", {"blob_id": bid, "length": 64})
                await call("validate_x3d", {"blob_id": bid}, label="validate_x3d (blob_id)")
                await call("blob_delete", {"blob_id": bid})
            out = await call("blob_put", {"content": base64.b64encode(tiny_splat_ply()).decode(), "encoding": "base64", "name": "tiny.ply"},
                             label="blob_put (splat PLY)")
            sid = json.loads(out)["blob_id"] if out and out.strip().startswith("{") else None
            if sid:
                await call("splat_inspect", {"blob_id": sid})
                await call("splat_chunk", {"blob_id": sid, "target": 4})
                await call("blob_delete", {"blob_id": sid}, label="blob_delete (splat)")
        # claims ledger (hagopjay)
        if "claim_append" in have:
            out = await call("claim_append", {"claim": "the smoke test ran", "grade": "Confirmed", "subject": SERVER})
            ledger = json.loads(out).get("ledger", "") if out and out.strip().startswith("{") else ""
            await call("claims_page", {"ledger": ledger})
            await call("claims_verify", {"ledger": ledger})
        # anything this notebook does not know yet (new tools upstream): schema-built arguments
        for name in sorted(set(have) - done):
            await call(name, synth(have[name].inputSchema or {}, name), label="%s (schema-built args)" % name)

run(part_b())
covered = {r["name"].split()[0] for r in RESULTS if r["part"] == "5b"}
record("5b", "coverage", "PASS" if {t.name for t in TOOLS} <= covered else "WARN", None,
       "%d of %d tools called" % (len({t.name for t in TOOLS} & covered), len(TOOLS)))
table("5b")
if RENDERS:
    display(HTML("".join("<figure style='display:inline-block;margin:6px'><img src='data:image/png;base64,%s' width=240>"
                         "<figcaption style='font-size:12px'>%s</figcaption></figure>" % (data, name) for name, data in RENDERS)))

# %%
#@title 5c · Verdicts on good and broken scenes { display-mode: "form" }
def sub(pattern, repl, xml):
    out, n = re.subn(pattern, repl, xml, count=1, flags=re.S)
    assert n, "defect pattern not found: " + pattern
    return out

def move_up(xml, block):
    m = re.search(block, xml, re.S)
    return xml.replace(m.group(0), "", 1).replace('<Transform DEF="Spinner"', m.group(0).strip() + '\n    <Transform DEF="Spinner"', 1)

# name: (how to break GOOD, the layer that should catch it: xml | xsd | semantic | nobody)
DEFECTS = {
    "Python boolean True":        (lambda x: sub(r'<Box size="1 1.4 1"/>', '<Box size="1 1.4 1" solid="True"/>', x), "xsd"),
    "node that does not exist":   (lambda x: sub(r"</Scene>", "<Banana/></Scene>", x), "xsd"),
    "misspelled field":           (lambda x: sub(r'diffuseColor="0.95', 'difuseColor="0.95', x), "xsd"),
    "colour with two numbers":    (lambda x: sub(r'"0.95 0.55 0.1"', '"0.95 0.55"', x), "xsd"),
    "colour above 1.0":           (lambda x: sub(r'"0.95 0.55 0.1"', '"1.8 0.55 0.1"', x), "xsd"),
    "duplicate DEF":              (lambda x: sub(r'<Material diffuseColor="0.6', '<Material DEF="Glow" diffuseColor="0.6', x), "xsd"),
    "missing profile":            (lambda x: sub(r' profile="Immersive"', "", x), "xsd"),
    "USE of an undefined DEF":    (lambda x: sub(r'USE="Body"', 'USE="Bodyy"', x), "semantic"),
    "USE before its DEF":         (lambda x: move_up(x, r'\s*<Transform translation="2.5 0.7 0"[^>]*><Shape USE="Body"/></Transform>'), "semantic"),
    "ROUTE type mismatch":        (lambda x: sub(r'toNode="Turn" toField="set_fraction"', 'toNode="Spinner" toField="set_rotation"', x), "semantic"),
    "ROUTE before its DEF":       (lambda x: move_up(x, r'\s*<ROUTE fromNode="Turn"[^>]*/>'), "semantic"),
    "interpolator too short":     (lambda x: sub(r' 0 1 0 6.28318"', '"', x), "semantic"),
    "PBR outside the profile":    (lambda x: sub(r'<Material DEF="Glow"[^>]*/>', '<PhysicalMaterial DEF="Glow" baseColor="0.9 0.5 0.1"/>', x), "semantic"),
    "texture in the wrong field": (lambda x: sub(r'<Material DEF="Glow"[^>]*/>', '<PhysicalMaterial DEF="Glow"><ImageTexture url=\'"a.png"\'/></PhysicalMaterial>',
                                                 x.replace('profile="Immersive"', 'profile="Full"')), "semantic"),
    "unclosed tag":               (lambda x: sub(r"</Scene>", "", x), "xml"),
    "valid but invisible":        (lambda x: x.replace('emissiveColor="0.3 0.12 0"/>', 'emissiveColor="0 0 0" transparency="1"/>')
                                                .replace('<Material diffuseColor="0.6 0.1 0.1"/>', '<Material diffuseColor="0.6 0.1 0.1" transparency="1"/>'), "nobody"),
}

async def part_c():
    async with session() as s:
        for name, xml in (("good: spinning lantern", GOOD), ("good: sphere (Interchange)", GOOD2)):
            t = time.perf_counter()
            ok, sem, first = await verdict(s, xml)
            record("5c", name, "PASS" if ok and not sem else "FAIL", (time.perf_counter() - t) * 1000, first or "valid")
        for name, (breaker, expect) in DEFECTS.items():
            xml = breaker(GOOD)
            t = time.perf_counter()
            ok, sem, first = await verdict(s, xml)
            got = ("xml" if "parse" in first.lower() else "xsd") if not ok else ("semantic" if sem else "nobody")
            if got == expect:
                status = "PASS"
            elif expect != "nobody" and got == "nobody":
                status = "FAIL"            # a real defect slipped through every check
            else:
                status = "WARN"            # caught, but by a different layer than the pinned fork
            record("5c", name, status, (time.perf_counter() - t) * 1000,
                   "caught by %s%s · %s" % (got, "" if got == expect else " (expected %s)" % expect, first[:120]))

run(part_c())
table("5c")

# %%
#@title 5d · Concurrent load { display-mode: "form" }
async def worker(n, lat, errors):
    try:
        async with session() as s:
            for _ in range(LOAD_CALLS_PER_SESSION):
                t = time.perf_counter()
                try:
                    r = await s.call_tool("validate_x3d", {"content": GOOD})
                    if r.isError or not json.loads(text_of(r)).get("valid"):
                        errors.append("wrong verdict")
                except Exception as e:
                    errors.append(type(e).__name__)
                lat.append((time.perf_counter() - t) * 1000)
    except Exception as e:
        errors.append("session: %s" % type(e).__name__)

async def part_d():
    lat, errors = [], []
    before = rss_mb()
    t = time.perf_counter()
    await asyncio.gather(*(worker(i, lat, errors) for i in range(LOAD_SESSIONS)))
    wall = time.perf_counter() - t
    q = statistics.quantiles(lat, n=100) if len(lat) > 1 else [lat[0]] * 99
    total = LOAD_SESSIONS * LOAD_CALLS_PER_SESSION
    record("5d", "%d sessions × %d validations" % (LOAD_SESSIONS, LOAD_CALLS_PER_SESSION), "FAIL" if errors else "PASS", wall * 1000,
           "%d/%d ok · %.1f calls/s%s" % (total - len(errors), total, total / wall, " · errors: %s" % sorted(set(errors)) if errors else ""))
    record("5d", "latency p50 / p95 / p99", "PASS", q[49], "%.0f / %.0f / %.0f ms (max %.0f)" % (q[49], q[94], q[98], max(lat)))
    after = rss_mb()
    record("5d", "server memory", "PASS" if after - before < 300 else "WARN", None, "%.0f MB → %.0f MB" % (before, after))
    record("5d", "server alive afterwards", "PASS" if server_alive() else "FAIL")
    return lat

LATENCIES = run(part_d())
table("5d")
try:
    import matplotlib.pyplot as plt
    plt.figure(figsize=(7, 2.4)); plt.hist(LATENCIES, bins=40, color="#1f7a5c")
    plt.xlabel("validate_x3d latency under load (ms)"); plt.ylabel("calls"); plt.tight_layout(); plt.show()
except Exception:
    pass

# %%
#@title 5e · Big documents and hostile input { display-mode: "form" }
def big_scene(mb):
    """A valid scene of roughly `mb` megabytes (many small shapes)."""
    unit = '<Transform translation="%d 0 0"><Shape><Appearance><Material diffuseColor="0.5 0.5 0.5"/></Appearance><Box size="0.5 0.5 0.5"/></Shape></Transform>\n'
    n = int(mb * 1e6 / len(unit % 100))
    return GOOD2.replace("</Scene>", "".join(unit % i for i in range(n)) + "</Scene>")

HOSTILE = {
    "entity bomb (billion laughs)": '<?xml version="1.0"?><!DOCTYPE X3D [<!ENTITY a "aaaaaaaaaa">' + "".join(
        '<!ENTITY %s "%s">' % (chr(98 + i), ("&%s;" % chr(97 + i)) * 10) for i in range(9)) + ']><X3D profile="Core" version="4.0"><Scene><WorldInfo title="&j;"/></Scene></X3D>',
    "external entity (/etc/passwd)": '<?xml version="1.0"?><!DOCTYPE X3D [<!ENTITY x SYSTEM "file:///etc/passwd">]><X3D profile="Core" version="4.0"><Scene><WorldInfo title="&x;"/></Scene></X3D>',
    "nesting 5000 deep": '<X3D profile="Core" version="4.0"><Scene>' + "<Group>" * 5000 + "</Group>" * 5000 + "</Scene></X3D>",
    "1 MB attribute": '<X3D profile="Core" version="4.0"><Scene><WorldInfo title="%s"/></Scene></X3D>' % ("x" * 1_000_000),
    "binary junk": base64.b64encode(os.urandom(3000)).decode() + "\x00\x01\x02",
    "empty string": "",
    "not X3D (HTML)": "<html><body><script>alert(1)</script></body></html>",
}

async def part_e():
    for mb in (0.5, 2, 3.9, 6, 20):
        xml = big_scene(mb)
        t = time.perf_counter()
        status, payload = await asyncio.to_thread(raw_call, "validate_x3d", {"content": xml})
        ms = (time.perf_counter() - t) * 1000
        label = "inline %.1f MB scene" % (len(xml) / 1e6)
        if status == 200 and payload and "result" in payload:
            out = "".join(c.get("text", "") for c in payload["result"].get("content", []))
            ok = out.strip().startswith("{") and json.loads(out).get("valid")
            record("5e", label, "PASS" if ok else "WARN", ms, "valid" if ok else out[:120])
        elif status == 413:
            record("5e", label, "PASS" if server_alive() else "FAIL", ms, "refused with 413 (request size limit); use the blob store")
        else:
            record("5e", label, "FAIL", ms, "HTTP %s %s" % (status, str(payload)[:120]))
    if "blob_put" in {t.name for t in TOOLS}:
        xml = big_scene(20)
        t = time.perf_counter()
        r = httpx.put("http://127.0.0.1:%d/blob" % MCP_PORT, content=xml.encode(), timeout=120,
                      headers={"Authorization": "Bearer " + SERVER_TOKEN, "content-type": "model/x3d+xml"})
        bid = r.json().get("blob_id") if r.status_code < 300 else None
        async with session(read_timeout=300) as s:
            out = text_of(await s.call_tool("validate_x3d", {"blob_id": bid})) if bid else r.text
        ok = out.strip().startswith("{") and json.loads(out).get("valid")
        record("5e", "%.0f MB scene through PUT /blob" % (len(xml) / 1e6), "PASS" if ok else "FAIL", (time.perf_counter() - t) * 1000,
               "valid" if ok else out[:150])
    for name, doc in HOSTILE.items():
        before = rss_mb()
        t = time.perf_counter()
        try:
            async with session() as s:
                outs = [text_of(await asyncio.wait_for(s.call_tool(tool, {"content": doc}), 60)) for tool in ("validate_x3d", "validate_semantic")]
            leaked = "root:x:0:0" in "".join(outs)
            grew = rss_mb() - before
            status = "FAIL" if leaked or not server_alive() else ("WARN" if grew > 500 else "PASS")
            record("5e", name, status, (time.perf_counter() - t) * 1000,
                   "LEAKED /etc/passwd" if leaked else "handled; memory %+.0f MB · %s" % (grew, outs[0][:80].replace("\n", " ")))
        except Exception as e:
            record("5e", name, "PASS" if server_alive() else "FAIL", (time.perf_counter() - t) * 1000, "refused (%s), server alive" % type(e).__name__)
    # Can a remote caller make the server read its own files?
    async with session() as s:
        r = await s.call_tool("validate_x3d", {"path": "/etc/passwd"})
        out = text_of(r)
        low = out.lower()
        refused = any(w in low for w in ("disabled", "not allowed", "stdio", "provide either", "refus"))
        reads = "root:x:0:0" in out or (not refused and ("parse error" in low or "line 1" in low))   # evidence it opened the file
        record("5e", "path=/etc/passwd over HTTP", "WARN" if reads else "PASS", None,
               ("the server opened a local file for a remote caller: %s" % out[:100]) if reads else "refused: " + out[:100])
    record("5e", "server alive at the end", "PASS" if server_alive() else "FAIL", None, "pid %d, %.0f MB" % (server.pid, rss_mb()))

run(part_e())
table("5e")

# %% [markdown]
# ## 6 · Report
#
# One table for everything, saved as JSON (with the server, commit and versions) next to the server log. In Colab the report downloads with one click.

# %%
#@title 6 · Report { display-mode: "form" }
import platform
report = {"server": repo, "ref": ref, "commit": COMMIT, "tunnel": TUNNEL, "mcp_pinned_below_2": PIN_MCP_BELOW_2,
          "versions": {n: md.version(n) for n in ("mcp", "x3d", "lxml", "xmlschema") if n in {d.metadata["Name"].lower() for d in md.distributions()}},
          "python": sys.version.split()[0], "platform": platform.platform(terse=True), "cpus": os.cpu_count(),
          "load": {"sessions": LOAD_SESSIONS, "calls_per_session": LOAD_CALLS_PER_SESSION},
          "time": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "results": RESULTS}
counts = {s: sum(r["status"] == s for r in RESULTS) for s in ("PASS", "WARN", "FAIL", "SKIP")}
report["summary"] = counts
REPORT_PATH = os.path.join(WORK, "x3d_mcp_smoke_%s_%s.json" % (SERVER, time.strftime("%Y%m%d-%H%M%S")))
with open(REPORT_PATH, "w") as fh:
    json.dump(report, fh, indent=1)
verdict_word = "FAILED" if counts["FAIL"] else ("PASSED with warnings" if counts["WARN"] else "PASSED")
display(HTML("<div style='font:16px system-ui;margin:6px 0'><b>%s</b> %s · %s</div>" % (repo, COMMIT.split("·")[0], verdict_word)))
table()
print("report:", REPORT_PATH, "| server log:", LOG_PATH)
try:
    from google.colab import files
    files.download(REPORT_PATH)
except Exception:
    pass

# %% [markdown]
# ## 7 · Try any tool
#
# Call one tool by name with JSON arguments. `GOOD` (the lantern scene) and `GOOD2` (a sphere) are available as `"$GOOD"` and `"$GOOD2"`.

# %%
#@title 7 · Call a tool { display-mode: "form" }
TOOL = "describe_node"  #@param {type:"string"}
ARGS = '{"node_type": "Transform"}'  #@param {type:"string"}

async def try_tool():
    args = json.loads(ARGS.replace('"$GOOD2"', json.dumps(GOOD2)).replace('"$GOOD"', json.dumps(GOOD)))
    async with session() as s:
        t = time.perf_counter()
        r = await s.call_tool(TOOL, args)
        print("%s in %.0f ms%s\n" % (TOOL, (time.perf_counter() - t) * 1000, " (error)" if r.isError else ""))
        print(text_of(r)[:6000])
run(try_tool())

# %% [markdown]
# ## 8 · Stop
#
# Stops the tunnel, the proxy and the server. The endpoint and token stop working immediately.

# %%
#@title 8 · Stop everything { display-mode: "form" }
if TUNNEL == "ngrok":
    from pyngrok import ngrok
    ngrok.kill()
for p in psutil.process_iter(["name"]):
    if p.info["name"] == "cloudflared":
        p.terminate()
proxy_server.should_exit = True
server.terminate()
try:
    server.wait(5)
except subprocess.TimeoutExpired:
    server.kill()
print("stopped. Last server log lines:")
show_log(8)
