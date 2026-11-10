"""The one port the outside world sees.

    /k/<key>/...        dashboard, API, event stream, run files and the Copilot tab. The key lives in the
                        path, so every relative URL a page loads carries it: this works in Colab's output
                        iframe (where cookies are third-party), through a tunnel and on a phone.
                        ?key=... and the X-Lab-Key header are accepted too.
    /mcp, /blob         reverse proxy to the x3d_mcp server on 127.0.0.1, for remote MCP clients
                        (Claude Code, Claude Desktop, Cursor, MCP Inspector). Needs `Authorization: Bearer
                        <mcp token>`, a different secret from the dashboard key, so handing a client MCP access
                        does not hand it your model budget.
    /healthz            {"ok": true}, unauthenticated, for tunnel checks.

Every response carries a Server-Timing header (Chrome DevTools > Network > Timing).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import mimetypes
import re
import threading
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, unquote

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import breakit, keys, pins, procs
from .events import BusHandler, bus

STATIC = Path(__file__).with_name("static")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("model/x3d+xml", ".x3d")

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
_HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
        "authorization", "cookie", "origin", "referer", "proxy-authorization", "accept-encoding"}


def _same(given: str | None, expected: str) -> bool:
    """Constant-time compare that never raises (the old app answered 500 for a non-ASCII key)."""
    if not given or not expected:
        return False
    a = hashlib.sha256(given.encode("utf-8", "surrogatepass")).digest()
    b = hashlib.sha256(expected.encode("utf-8", "surrogatepass")).digest()
    return hmac.compare_digest(a, b)


def _bearer(headers: dict) -> str | None:
    scheme, _, value = (headers.get("authorization") or "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else None


class Gate:
    """Pure ASGI middleware (streaming-safe): authentication, /k/<key>/ prefix, Server-Timing."""

    def __init__(self, app, lab):
        self.app, self.lab = app, lab

    async def _deny(self, send, status: int, message: str, extra: list | None = None):
        body = json.dumps({"error": message}).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())] + (extra or [])
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        t0 = time.perf_counter()
        path = scope.get("path", "/")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

        if "\x00" in path or "%00" in scope.get("raw_path", b"").decode("latin-1", "replace").lower():
            return await self._deny(send, 400, "bad path")

        if path in ("/healthz", "/favicon.ico"):
            pass
        elif path in ("/mcp", "/blob") or path.startswith(("/mcp/", "/blob/")):
            if not self.lab.expose_mcp:
                return await self._deny(send, 404, "remote MCP access is switched off (EXPOSE_MCP=False)")
            if not _same(_bearer(headers), self.lab.mcp_token):
                return await self._deny(send, 401, "missing or wrong bearer token for /mcp",
                                        [(b"www-authenticate", b'Bearer realm="x3d-lab-mcp"')])
        else:
            m = re.match(r"^/k/([^/]+)(/.*)?$", path)
            if m:
                key = unquote(m.group(1))
                if not _same(key, self.lab.dashboard_key):
                    return await self._deny(send, 401, "wrong key")
                if m.group(2) is None:   # /k/<key> -> /k/<key>/ so relative URLs resolve under the key
                    resp = RedirectResponse(path + "/", status_code=307)
                    return await resp(scope, receive, send)
                inner = m.group(2)
                if inner in ("/mcp", "/blob") or inner.startswith(("/mcp/", "/blob/")):
                    return await self._deny(send, 404, "MCP is served at /mcp with a bearer token, not under /k/")
                scope = dict(scope, path=inner, raw_path=inner.encode())
            else:
                qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
                key = (qs.get("key") or [None])[0] or headers.get("x-lab-key")
                if not _same(key, self.lab.dashboard_key):
                    return await self._deny(send, 401, "missing or wrong key: open the link printed by cell 1")

        async def send_timed(message):
            if message["type"] == "http.response.start":
                dur = (time.perf_counter() - t0) * 1000
                parts = ["gateway;dur=%.1f" % dur]
                for name, ms in (scope.get("state") or {}).get("timings", []):
                    parts.append("%s;dur=%.1f" % (name, ms))
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + [
                    (b"server-timing", ", ".join(parts).encode()), (b"x-content-type-options", b"nosniff")]
            await send(message)

        return await self.app(scope, receive, send_timed)


# ---------------------------------------------------------------------- request bodies
class JobRequest(BaseModel):
    kind: Literal["scene", "breakit", "validate", "stress"]
    params: dict[str, Any] = Field(default_factory=dict)


class ValidateRequest(BaseModel):
    xml: str = Field(min_length=1, max_length=20_000_000)


class BreakitRequest(BaseModel):
    defect: str = Field(min_length=1, max_length=40)
    base: str | None = Field(default=None, max_length=2_000_000)


class Beacon(BaseModel):
    type: str = Field(min_length=1, max_length=40)
    data: dict[str, Any] = Field(default_factory=dict)


def _timing(request: Request, name: str, ms: float) -> None:
    timings = getattr(request.state, "timings", None) or []
    timings.append((name, ms))
    request.state.timings = timings


def create_app(lab) -> FastAPI:
    from contextlib import asynccontextmanager
    state: dict = {"http": None}

    @asynccontextmanager
    async def lifespan(_app):
        state["http"] = httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=600, write=600, pool=10))
        try:
            yield
        finally:
            await state["http"].aclose()

    app = FastAPI(title="x3d.lab gateway", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.exception_handler(Exception)
    async def _crash(request: Request, exc: Exception):
        bus.error("gateway error on %s: %s: %s" % (request.url.path, type(exc).__name__, exc), src="gateway")
        return JSONResponse({"error": "%s: %s" % (type(exc).__name__, str(exc)[:300])}, status_code=500)

    # ------------------------------------------------------------------ pages
    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html = (STATIC / "dashboard" / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("{{XITE_SRC}}", lab.xite_src).replace("{{XITE_CSS}}", lab.xite_css),
                            headers={"cache-control": "no-store"})

    @app.get("/view", response_class=HTMLResponse)
    async def viewer():
        """One scene in X_ITE: /k/<key>/view?src=out/<job>/<file>.x3d (the two-MCP agent's Chrome opens this)."""
        html = (STATIC / "viewer.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("{{XITE_SRC}}", lab.xite_src).replace("{{XITE_CSS}}", lab.xite_css),
                            headers={"cache-control": "no-store"})

    @app.get("/static/{path:path}")
    async def static(path: str):
        p = (STATIC / path).resolve()
        if STATIC.resolve() not in p.parents or not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, headers={"cache-control": "no-cache"})

    @app.get("/vendor/x_ite/{path:path}")
    async def xite_files(path: str):
        root = lab.xite_dir
        if not root:
            return JSONResponse({"error": "X_ITE is loaded from the CDN in this session"}, status_code=404)
        root = Path(root).resolve()
        p = (root / path).resolve()
        if root not in p.parents or not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, headers={"cache-control": "public, max-age=86400"})

    # ------------------------------------------------------------------ API
    @app.get("/api/health")
    async def health(request: Request):
        t = time.perf_counter()
        mcp = "down"
        try:
            r = await state["http"].get(lab.mcp_base + "/pulse", timeout=3)
            mcp = "up" if r.status_code == 200 else "HTTP %d" % r.status_code
        except Exception as exc:
            mcp = "down: %s" % type(exc).__name__
        _timing(request, "mcp-pulse", (time.perf_counter() - t) * 1000)
        return {"mcp": mcp, "pins": pins.summary(), "runtime": lab.runtime_info, "uptime_s": round(time.time() - lab.started),
                "llm": keys.public_status(), "copilot": bool(lab.copilot_dir), "expose_mcp": lab.expose_mcp,
                "hosting": lab.hosting_info(), "jobs": {"llm_runs": lab.jobs.llm_jobs_started, "llm_limit": lab.jobs.max_llm_jobs},
                "breakit": [{"id": d.id, "title": d.title} for d in breakit.CATALOG],
                "blender": ({"version": lab.blender.version, "best": lab.blender.best_backend} if getattr(lab, "blender", None) else None)}

    @app.get("/api/llm")
    async def llm_status():
        return keys.public_status()

    @app.post("/api/jobs", status_code=202)
    async def submit(body: JobRequest):
        from .jobs import JobRefused
        try:
            job = lab.jobs.submit(body.kind, body.params)
        except JobRefused as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        return {"job": job.id, "status": job.status}

    @app.get("/api/jobs")
    async def jobs_list():
        return [j.public() for j in lab.jobs.recent()]

    @app.get("/api/jobs/{job_id}")
    async def job_get(job_id: str):
        j = lab.jobs.get(job_id) if _SAFE_ID.match(job_id) else None
        if not j:
            return JSONResponse({"error": "no such job"}, status_code=404)
        return j.public()

    @app.post("/api/validate")
    async def validate(body: ValidateRequest, request: Request):
        t = time.perf_counter()
        v = await lab.client.verdict(body.xml)
        _timing(request, "validate", (time.perf_counter() - t) * 1000)
        return v.to_dict()

    @app.post("/api/breakit")
    async def breakit_one(body: BreakitRequest, request: Request):
        if body.defect not in breakit.BY_ID:
            return JSONResponse({"error": "unknown defect", "known": list(breakit.BY_ID)}, status_code=400)
        t = time.perf_counter()
        o = await breakit.run_one(lab.client, body.defect, body.base or breakit.LANTERN)
        _timing(request, "breakit", (time.perf_counter() - t) * 1000)
        return {**o.row(), "xml": o.xml}

    @app.post("/api/beacon", status_code=204)
    async def beacon(body: Beacon):
        if len(json.dumps(body.data, default=str)) > 8000:
            return JSONResponse({"error": "beacon too large"}, status_code=413)
        level = "WARN" if body.type in ("console-error", "xite-error") else "INFO"
        bus.emit("browser: %s" % body.type, level=level, src="browser", kind="browser", data=body.data)
        lab.on_beacon(body.type, body.data)
        return Response(status_code=204)

    @app.get("/events")
    async def events(request: Request, since: int = 0):
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=4000)

        def push(ev):
            def put():
                if not q.full():
                    q.put_nowait(ev)
            loop.call_soon_threadsafe(put)

        unsubscribe = bus.subscribe(push)
        try:
            last = int(request.headers.get("last-event-id") or since or 0)
        except ValueError:
            last = 0
        last = max(0, min(last, bus.latest_seq()))     # a cursor from the future (or another session) means "from now"

        def fmt(ev):
            return "id: %d\ndata: %s\n\n" % (ev["seq"], json.dumps(ev, default=str))

        async def gen():
            nonlocal last
            try:
                yield ": hello\n\nretry: 3000\n\n"
                for ev in bus.since(last, limit=600):
                    last = ev["seq"]
                    yield fmt(ev)
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": ping %d\n\n" % int(time.time())
                        continue
                    if ev["seq"] <= last:
                        continue
                    last = ev["seq"]
                    yield fmt(ev)
            finally:
                unsubscribe()

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"cache-control": "no-cache, no-transform", "x-accel-buffering": "no"})

    @app.get("/out/{job_id}/{name:path}")
    async def out_file(job_id: str, name: str):
        parts = name.split("/")
        if not _SAFE_ID.match(job_id) or len(parts) > 3 or any(not _SAFE_NAME.match(x) or x in (".", "..") for x in parts):
            return JSONResponse({"error": "not found"}, status_code=404)
        p = (lab.out_dir / job_id / name).resolve()
        if lab.out_dir.resolve() not in p.parents or not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        media = "model/x3d+xml" if name.endswith(".x3d") else None
        return FileResponse(p, media_type=media, headers={"cache-control": "no-store"})

    # ------------------------------------------------------------------ Copilot Validator Demo (private build)
    @app.get("/copilot")
    async def copilot_root():
        return RedirectResponse("copilot/", status_code=307)

    @app.get("/copilot/{path:path}")
    async def copilot(path: str):
        root = lab.copilot_dir
        if not root:
            return HTMLResponse("<p>The Copilot Validator Demo is not loaded. Run the Copilot cell in the notebook "
                                "(it loads your private build; it is never part of the public repo).</p>", status_code=404)
        root = Path(root).resolve()
        p = (root / (path or "index.html")).resolve()
        if p.is_dir():
            p = p / "index.html"
        if p != root and root not in p.parents or not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, headers={"cache-control": "no-cache"})

    # ------------------------------------------------------------------ MCP reverse proxy
    async def proxy(request: Request, upstream_path: str):
        url = lab.mcp_base + upstream_path
        if request.url.query:
            url += "?" + request.url.query
        fwd = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
        fwd["host"] = lab.mcp_host                          # keeps the SDK's DNS-rebinding guard satisfied
        fwd["accept-encoding"] = "identity"                 # bytes are relayed raw, so ask for them uncompressed
        if lab.blob_token:
            fwd["authorization"] = "Bearer " + lab.blob_token   # the server's own token, never shown to clients
        body = request.stream() if request.method in ("POST", "PUT", "PATCH") else None
        t = time.perf_counter()
        try:
            req = state["http"].build_request(request.method, url, headers=fwd, content=body)
            resp = await state["http"].send(req, stream=True)
        except httpx.HTTPError as exc:
            return JSONResponse({"error": "x3d_mcp unreachable: %s" % type(exc).__name__}, status_code=502)
        _timing(request, "mcp", (time.perf_counter() - t) * 1000)
        keep = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP}   # content-encoding kept: raw bytes
        bus.debug("proxy %s %s -> %d" % (request.method, upstream_path, resp.status_code), src="gateway")

        async def body_iter():
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await resp.aclose()
        return StreamingResponse(body_iter(), status_code=resp.status_code, headers=keep)

    @app.api_route("/mcp", methods=["GET", "POST", "DELETE"])
    async def mcp_root(request: Request):
        return await proxy(request, "/mcp")

    @app.api_route("/mcp/{rest:path}", methods=["GET", "POST", "DELETE"])
    async def mcp_sub(request: Request, rest: str):
        return await proxy(request, "/mcp/" + rest)

    @app.api_route("/blob", methods=["GET", "PUT", "POST", "HEAD", "DELETE"])
    async def blob_root(request: Request):
        return await proxy(request, "/blob")

    @app.api_route("/blob/{rest:path}", methods=["GET", "PUT", "POST", "HEAD", "DELETE"])
    async def blob_sub(request: Request, rest: str):
        return await proxy(request, "/blob/" + rest)

    return Gate(app, lab)


# ---------------------------------------------------------------------- running it inside the kernel
class GatewayServer:
    """uvicorn in a daemon thread of this kernel, so the dashboard shares the event bus with the notebook."""

    def __init__(self, asgi_app, host: str = "127.0.0.1", port: int = 8765):
        self.app, self.host, self.port = asgi_app, host, port
        self.server = None
        self.thread: threading.Thread | None = None

    def start(self, timeout: float = 20) -> None:
        import logging
        import uvicorn
        procs.free_port(self.port)
        for name in ("uvicorn.error", "uvicorn"):
            lg = logging.getLogger(name)
            lg.handlers = [BusHandler("gateway")]
            lg.propagate = False
            lg.setLevel(logging.WARNING)
        cfg = uvicorn.Config(self.app, host=self.host, port=self.port, log_config=None, access_log=False,
                             lifespan="on", timeout_keep_alive=30, ws="none")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True, name="x3dlab-gateway")
        self.thread.start()
        t = time.time() + timeout
        while time.time() < t and not self.server.started:
            if not self.thread.is_alive():
                raise procs.ProcError("gateway thread stopped while starting (port %d in use?)" % self.port)
            time.sleep(0.05)
        if not self.server.started:
            raise procs.ProcError("gateway did not start on port %d" % self.port)
        procs.register_inprocess(self.port, self.stop)
        bus.info("gateway listening on http://%s:%d" % (self.host, self.port), src="gateway")

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=8)
        procs.unregister_inprocess(self.port)
