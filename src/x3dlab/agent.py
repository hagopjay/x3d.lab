"""One model, two MCP servers: x3d_mcp (validate, describe, repair) and chrome-devtools-mcp (a real Chrome to open the
scene in, read its console, trace its performance). The model has to make a scene valid AND prove it renders.

    res = agent.run(lab, scene_xml, provider="anthropic")      # or "openai", "gemini"
    res.final        # the model's report: {"verdict", "summary", "first_frame_ms", ...}
    res.transcript   # every tool call with server, arguments, result size, timing (also in out/<job>/agent.json)

chrome-devtools-mcp runs from npm (`npx -y chrome-devtools-mcp@<pin>`, needs Node >= 20.19) with its own headless
Chrome: --isolated (a throw-away profile), --no-usage-statistics, --no-performance-crux (trace URLs are not sent to the
CrUX API). It opens pages from the lab's gateway on 127.0.0.1.

The model never sees the dashboard key. It works with lab:// addresses (lab://view/<name>), which the toolbox turns
into the real local URL before Chrome gets them, and the key is masked in everything that goes back to the model.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import keys, llm, runtime
from .events import bus

DEVTOOLS_PIN = "1.12.0"
DEVTOOLS_TOOLS = ("navigate_page", "new_page", "list_pages", "select_page", "evaluate_script", "list_console_messages",
                  "get_console_message", "take_screenshot", "take_snapshot", "performance_start_trace", "performance_stop_trace",
                  "performance_analyze_insight", "list_network_requests", "resize_page")
X3D_TOOLS = ("describe_node", "where_can_place", "validate_x3d", "validate_semantic", "exact_fix_hints", "repair_x3d")
HIDDEN_ARGS = ("path", "blob_id", "return_mode", "encoding", "save_path", "filePath")

LOCAL_TOOLS = [
    {"name": "lab_stage_scene",
     "description": "Save an X3D XML document in the lab so Chrome can open it. Returns view_url (lab://view/<name>): "
                    "pass that to devtools_navigate_page. The viewer page logs 'x3d.lab load {...}' with parse/replace/frame "
                    "timings to the console, and window.lab.load(xml) reloads a scene in place.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "short file name, letters/digits/_"},
                                                       "xml": {"type": "string", "description": "the complete X3D XML document"}},
                      "required": ["name", "xml"]}},
    {"name": "lab_finish",
     "description": "Finish with your report. Call exactly once, at the end.",
     "input_schema": {"type": "object", "properties": {
         "verdict": {"type": "string", "enum": ["fixed_and_renders", "valid_but_render_problem", "could_not_fix"]},
         "summary": {"type": "string", "description": "what was wrong, what you changed, what the browser showed"},
         "first_frame_ms": {"type": "number", "description": "first-frames time from the viewer's console line, if measured"},
         "console_errors": {"type": "integer"},
         "final_scene": {"type": "string", "description": "lab://view/<name> of the final staged scene"}},
         "required": ["verdict", "summary"]}},
]

TASK = """You have two MCP servers: x3d_* tools (the X3D validator) and devtools_* tools (a real headless Chrome).

The scene below may be broken. Your job:
1. Check it with x3d_validate_x3d AND x3d_validate_semantic. Fix every error (x3d_repair_x3d and x3d_exact_fix_hints help);
   keep the design. Re-check until both are clean.
2. Prove it renders: lab_stage_scene the fixed scene, devtools_navigate_page to its view_url, then
   devtools_list_console_messages. The viewer logs a line "x3d.lab load {{...}}" with ok, t.parse, t.replace, t.frame.
   Any console error counts against the scene.
3. Measure: devtools_performance_start_trace with reload=true on the viewer page, then devtools_performance_stop_trace;
   name one insight if the trace offers any (devtools_performance_analyze_insight).
4. Call lab_finish with the verdict, a short summary, first_frame_ms and the number of console errors.
Be economical: at most {max_calls} tool calls in total.

Scene:
{scene}"""


@dataclass
class Step:
    n: int
    server: str            # x3d | devtools | lab
    tool: str
    args: dict
    ok: bool
    ms: float
    chars: int
    preview: str = ""


@dataclass
class AgentResult:
    job: str
    provider: str
    model: str
    final: dict = field(default_factory=dict)
    transcript: list = field(default_factory=list)
    tool_calls: int = 0
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0
    error: str | None = None
    files: dict = field(default_factory=dict)
    devtools: str = ""

    def summary(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "transcript"} | {"steps": len(self.transcript)}


# ---------------------------------------------------------------------- chrome-devtools-mcp over stdio
def devtools_command(chromium: str | None = None, headless: bool = True, pin: str = DEVTOOLS_PIN) -> tuple[list, dict]:
    """The npx command line and environment for chrome-devtools-mcp (pure: unit-tested)."""
    npx = shutil.which("npx") or "npx"
    args = [npx, "-y", "chrome-devtools-mcp@%s" % pin, "--isolated", "--no-usage-statistics", "--no-performance-crux",
            "--viewport", "1280x720x1"]
    if headless:
        args.append("--headless")
    if chromium:
        args += ["--executablePath", chromium]
    for a in ("--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--no-sandbox"):
        args.append("--chrome-arg=%s" % a)
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and k not in ("GITHUB_TOKEN", "NGROK_AUTHTOKEN")}
    env.update(CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS="1", CHROME_DEVTOOLS_MCP_NO_CONFIG_DISCOVERY="1")
    return args, env


def node_ok() -> tuple[bool, str]:
    node = shutil.which("node")
    if not node:
        return False, "Node.js is not installed (Colab has it; elsewhere install Node >= 20.19)"
    import subprocess
    v = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    major, minor = (int(x) for x in (v.split(".") + ["0", "0"])[:2])
    if (major, minor) < (20, 19):
        return False, "Node %s is too old for chrome-devtools-mcp (needs >= 20.19)" % v
    return True, v


@contextlib.asynccontextmanager
async def devtools_session(chromium: str | None = None, headless: bool = True):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    args, env = devtools_command(chromium, headless)
    params = StdioServerParameters(command=args[0], args=args[1:], env=env)
    errlog = open(runtime.sub("logs") / "chrome-devtools-mcp.log", "w", encoding="utf-8")
    try:
        async with stdio_client(params, errlog=errlog) as (r, w):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                bus.info("chrome-devtools-mcp %s ready" % (getattr(init.serverInfo, "version", "?")), src="agent")
                yield s
    finally:
        errlog.close()


# ---------------------------------------------------------------------- a stand-in when Node is not available
class PlaywrightDevtools:
    """The subset of chrome-devtools-mcp the agent uses, on the lab's own headless Chromium (lab.start_browser()).

    Same tool names and argument names, so the agent cannot tell the difference. Used when Node/npx is missing
    (devtools="auto") or asked for (devtools="playwright"), and by the tests. The real server has many more tools.
    """

    SCHEMAS = {
        "navigate_page": ("Go to a URL in the selected page.", {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}),
        "list_console_messages": ("List console messages of the selected page since the last navigation.", {"type": "object", "properties": {}}),
        "evaluate_script": ("Run a JavaScript function in the page and return its JSON result.",
                            {"type": "object", "properties": {"function": {"type": "string", "description": "e.g. () => document.title"}},
                             "required": ["function"]}),
        "take_screenshot": ("Screenshot of the page.", {"type": "object", "properties": {}}),
        "performance_start_trace": ("Start a performance trace; reload=true reloads the page first.",
                                    {"type": "object", "properties": {"reload": {"type": "boolean"}, "autoStop": {"type": "boolean"}}}),
        "performance_stop_trace": ("Stop the trace and summarise it.", {"type": "object", "properties": {}}),
    }

    def __init__(self, hb, out_dir: Path):
        self.hb, self.dir = hb, Path(out_dir)
        self.page = None
        self.console: list = []
        self.tracing = False
        self.n_traces = 0

    async def list_tools(self):
        from types import SimpleNamespace as NS
        return NS(tools=[NS(name=n, description=d, inputSchema=sc) for n, (d, sc) in self.SCHEMAS.items()])

    async def _page(self):
        if self.page is None or self.page.is_closed():
            self.page = await self.hb._ctx.new_page()
            self.page.on("console", lambda m: self.console.append((m.type, m.text)))
            self.page.on("pageerror", lambda e: self.console.append(("error", "Uncaught %s" % e)))
        return self.page

    async def _settle(self, page, timeout=60):
        try:
            await page.wait_for_function("() => window.__x3dlab_result !== undefined", timeout=timeout * 1000)
        except Exception:
            pass

    async def call_tool(self, name: str, args: dict):
        from types import SimpleNamespace as NS
        page = await self._page()
        text, err, image = "", False, None
        try:
            if name == "navigate_page":
                self.console.clear()
                r = await page.goto(str(args["url"]), wait_until="load", timeout=60000)
                await self._settle(page)
                text = "Navigated to %s (HTTP %s). Title: %s" % (page.url, r.status if r else "?", await page.title())
            elif name == "list_console_messages":
                text = "\n".join("msgid=%d [%s] %s" % (i + 1, t, m) for i, (t, m) in enumerate(self.console)) or "<no console messages>"
            elif name == "evaluate_script":
                text = json.dumps(await page.evaluate(str(args["function"])), default=str)[:8000]
            elif name == "take_screenshot":
                image = await page.screenshot(type="png")
                text = "Took a screenshot of the current page."
            elif name == "performance_start_trace":
                await self.hb._browser.start_tracing(page=page, screenshots=False,
                                                     categories=["devtools.timeline", "blink.user_timing", "v8.execute",
                                                                 "disabled-by-default-devtools.timeline"])
                self.tracing = True
                if args.get("reload"):
                    await page.evaluate("() => { delete window.__x3dlab_result; }")
                    await page.reload(wait_until="load")
                    await self._settle(page)
                text = "Trace started%s." % (" and the page reloaded" if args.get("reload") else "")
                if args.get("autoStop"):
                    return await self.call_tool("performance_stop_trace", {})
            elif name == "performance_stop_trace":
                if not self.tracing:
                    raise RuntimeError("no trace is running")
                from .browser import trace_summary
                data = await self.hb._browser.stop_tracing()
                self.tracing = False
                self.n_traces += 1
                fn = "agent_trace_%d.json" % self.n_traces
                (self.dir / fn).write_bytes(data)
                s = trace_summary(data)
                res = await page.evaluate("() => window.__x3dlab_result || null")
                text = ("Trace saved (%s, %d events). x3d.lab user timings: %s. Longest main-thread tasks (ms): %s. "
                        "Tasks over 50 ms: %d. Viewer result: %s") % (fn, s["events"], ", ".join(sorted(set(s["measures"]))) or "none",
                                                                     s.get("long_tasks_ms"), s.get("tasks_over_50ms", 0), json.dumps(res))
            else:
                text, err = "Tool %s is not available in the Playwright stand-in." % name, True
        except Exception as exc:
            text, err = "%s: %s" % (type(exc).__name__, str(exc)[:400]), True
        content = [NS(type="text", text=text)]
        if image is not None:
            content.append(NS(type="image", data=base64.b64encode(image).decode(), mimeType="image/png"))
        return NS(content=content, isError=err)


# ---------------------------------------------------------------------- the toolbox both servers live in
class Toolbox:
    """Namespaced tools from x3d_mcp ('x3d_'), chrome-devtools-mcp ('devtools_') and the lab ('lab_')."""

    def __init__(self, x3d_client, x3d_session, devtools, stage_dir: Path, view_base: str, secrets_: list[str], job: str,
                 stage_job: str):
        self.x3d, self.xs, self.dt = x3d_client, x3d_session, devtools
        self.dir, self.view_base, self.secrets, self.job, self.stage_job = Path(stage_dir), view_base.rstrip("/"), \
            [s for s in secrets_ if s], job, stage_job
        self.final: dict | None = None
        self.staged: dict = {}
        self.steps: list[Step] = []
        self._specs: list | None = None

    async def specs(self) -> list:
        if self._specs is None:
            out = []
            for t in (await self.xs.list_tools()).tools:
                if t.name in X3D_TOOLS:
                    out.append(_spec("x3d_" + t.name, t.description, t.inputSchema))
            if self.dt is not None:
                for t in (await self.dt.list_tools()).tools:
                    if t.name in DEVTOOLS_TOOLS:
                        out.append(_spec("devtools_" + t.name, t.description, t.inputSchema))
            self._specs = out + LOCAL_TOOLS
        return self._specs

    # lab:// <-> real URL
    def to_real(self, v):
        if isinstance(v, str):
            return re.sub(r"lab://view/([A-Za-z0-9_-]{1,40})", lambda m: "%s/view?src=out/%s/%s.x3d" % (self.view_base, self.stage_job, m.group(1)), v)
        if isinstance(v, dict):
            return {k: self.to_real(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self.to_real(x) for x in v]
        return v

    def mask(self, text: str) -> str:
        pat = re.escape(self.view_base) + r"/view\?src=out/" + re.escape(self.stage_job) + r"/([A-Za-z0-9_-]{1,40})\.x3d"
        text = re.sub(pat, r"lab://view/\1", text)
        for s in self.secrets:
            text = text.replace(s, "<hidden>")
        return text

    async def call(self, name: str, args: dict) -> tuple[str, bool]:
        t0 = time.perf_counter()
        server, ok, text = name.split("_", 1)[0], True, ""
        try:
            if name == "lab_stage_scene":
                text = self._stage(args)
            elif name == "lab_finish":
                self.final = {k: args.get(k) for k in ("verdict", "summary", "first_frame_ms", "console_errors", "final_scene")}
                text = "Report received."
            elif name.startswith("x3d_") and name[4:] in X3D_TOOLS:
                a = {k: v for k, v in args.items() if k not in HIDDEN_ARGS}
                r = await self.x3d.call(name[4:], a, self.xs, track="Agent", job=self.job)
                text, ok = r.text, not r.is_error
            elif name.startswith("devtools_") and self.dt is not None and name[9:] in DEVTOOLS_TOOLS:
                with bus.span("devtools %s" % name[9:], track="Agent", src="agent", job=self.job):
                    res = await self.dt.call_tool(name[9:], self.to_real(args))
                text, ok = self._devtools_text(res, name[9:]), not res.isError
            else:
                text, ok = "Unknown tool %r." % name, False
        except Exception as exc:
            text, ok = "%s: %s" % (type(exc).__name__, str(exc)[:500]), False
        text = self.mask(text)
        if len(text) > 12000:
            text = text[:12000] + "\n…(truncated, %d chars)" % len(text)
        st = Step(len(self.steps) + 1, server, name, _short_args(args), ok, round((time.perf_counter() - t0) * 1000, 1), len(text),
                  text[:240])
        self.steps.append(st)
        bus.emit("agent %s %s%s" % (name, "ok" if ok else "ERROR", " · " + text[:100].replace("\n", " ") if not ok else ""),
                 level="INFO" if ok else "WARN", src="agent", kind="agent_step", job=self.job, data=asdict(st))
        return text, ok

    def _stage(self, args: dict) -> str:
        name = re.sub(r"[^A-Za-z0-9_-]", "_", str(args.get("name") or "scene"))[:40] or "scene"
        xml = str(args.get("xml") or "")
        if not xml.strip().startswith(("<?xml", "<X3D", "<!DOCTYPE")):
            raise ValueError("xml must be an X3D XML document")
        p = self.dir / ("%s.x3d" % name)
        p.write_text(xml, encoding="utf-8")
        self.staged[name] = str(p)
        return json.dumps({"view_url": "lab://view/%s" % name, "bytes": len(xml.encode())})

    def _devtools_text(self, res, tool: str) -> str:
        parts = []
        for c in res.content:
            if getattr(c, "type", "") == "text":
                parts.append(c.text)
            elif getattr(c, "type", "") == "image":
                fn = "shot_%d.png" % (len(self.steps) + 1)
                (self.dir / fn).write_bytes(base64.b64decode(c.data))
                parts.append("(screenshot saved in the lab as %s; you cannot see images here)" % fn)
            else:
                parts.append("(%s content)" % getattr(c, "type", "other"))
        return "\n".join(parts) or "(no output)"


def _spec(name: str, desc: str | None, schema: dict) -> dict:
    schema = json.loads(json.dumps(schema or {"type": "object", "properties": {}}))
    props = schema.get("properties", {})
    for k in HIDDEN_ARGS:
        props.pop(k, None)
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r in props]
    return {"name": name[:64], "description": (desc or "")[:900], "input_schema": schema}


def _short_args(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        s = v if isinstance(v, (int, float, bool)) or v is None else str(v)
        out[k] = s if not isinstance(s, str) or len(s) <= 120 else s[:117] + "…(%d chars)" % len(s)
    return out


# ---------------------------------------------------------------------- the loop
async def arun(lab, scene: str, provider: str = "auto", model: str | None = None, max_calls: int = 24, devtools="auto",
               chromium: str | None = None, job: str | None = None, task: str = TASK) -> AgentResult:
    """devtools: "npx" (chrome-devtools-mcp), "playwright" (the stand-in on lab.browser), "auto" (npx when Node is
    new enough, else the stand-in), None (x3d tools only), or an open session object with list_tools/call_tool."""
    job = job or "agent-" + uuid.uuid4().hex[:8]
    d = runtime.sub("out", job)
    try:
        provider, key, model = keys.pick(provider, model)
    except Exception as exc:                      # no key yet: a result that says so, not a crashed cell
        res = AgentResult(job, provider, model or "", error=str(exc))
        bus.error("agent: %s" % exc, src="agent", job=job)
        return res
    res = AgentResult(job, provider, model)
    t0 = time.time()
    view_base = "http://127.0.0.1:%d/k/%s" % (lab.gateway_port, lab.dashboard_key)
    stack = contextlib.AsyncExitStack()
    try:
        async with stack:
            dt = None
            mode = devtools
            if mode == "auto":
                ok, why = node_ok()
                mode = "npx" if ok else "playwright"
                if not ok:
                    bus.warn("%s: using the Playwright stand-in for chrome-devtools-mcp" % why, src="agent", job=job)
            if mode == "npx":
                from . import browser as browser_mod
                dt = await stack.enter_async_context(devtools_session(chromium or browser_mod.chromium_path()))
                res.devtools = "chrome-devtools-mcp@%s" % DEVTOOLS_PIN
            elif mode == "playwright":
                if getattr(lab, "browser", None) is None:
                    raise RuntimeError("the Playwright stand-in needs lab.start_browser() first")
                dt = PlaywrightDevtools(lab.browser, d)
                res.devtools = "playwright stand-in"
            elif mode is not None:
                dt = mode
                res.devtools = type(mode).__name__
            xs = await stack.enter_async_context(lab.client.session())
            tb = Toolbox(lab.client, xs, dt, d, view_base, [lab.dashboard_key, lab.mcp_token, lab.blob_token], job, job)
            specs = await tb.specs()
            system = (await lab.client.instructions()) + "\n\nYou also drive a real Chrome through devtools_* tools."
            conv = llm.conversation(provider, key, model, system=system, tools=specs)
            bus.emit("agent %s started: %s / %s, %d tools" % (job, provider, model, len(specs)), src="agent", kind="agent_start", job=job,
                     data={"tools": [s["name"] for s in specs]})
            reply = await asyncio.to_thread(conv.send, task.format(scene=scene, max_calls=max_calls))
            while reply.tool_calls and tb.final is None and res.tool_calls < max_calls:
                results = []
                for call in reply.tool_calls:
                    res.tool_calls += 1
                    text, _ = await tb.call(call.name, call.args or {})
                    results.append((call, text))
                if tb.final is not None:
                    break
                with bus.span("%s agent turn %d" % (provider, len(tb.steps)), track="LLM", src="llm", job=job):
                    reply = await asyncio.to_thread(conv.send_tool_results, results)
            res.final = tb.final or {"verdict": "no_report", "summary": (reply.text or "")[:1000]}
            res.transcript = [asdict(s) for s in tb.steps]
            res.usage = dict(conv.usage)
            res.files = {"staged": {k: "out/%s/%s.x3d" % (job, k) for k in tb.staged}}
    except Exception as exc:
        leaf = _leaf(exc)
        res.error = "%s: %s" % (type(leaf).__name__, str(leaf)[:500])
        bus.error("agent %s failed: %s" % (job, res.error), src="agent", job=job)
    res.seconds = round(time.time() - t0, 1)
    (d / "agent.json").write_text(json.dumps(asdict(res), indent=1, default=str), encoding="utf-8")
    res.files["agent.json"] = "out/%s/agent.json" % job
    bus.emit("agent %s: %s in %.0f s, %d tool calls" % (job, res.final.get("verdict", res.error), res.seconds, res.tool_calls),
             src="agent", kind="agent_end", job=job, data=res.summary())
    return res


def _leaf(exc: BaseException) -> BaseException:
    """The first real error inside (nested) ExceptionGroups, which anyio task groups wrap everything in."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def run(lab, scene: str, **kw) -> AgentResult:
    from . import aio
    return aio.run(arun(lab, scene, **kw), 1800)


def transcript_html(res: AgentResult) -> str:
    import html as H
    col = {"x3d": "#1d6b3a", "devtools": "#1a5fb4", "lab": "#6a1b9a"}
    rows = "".join("<tr><td>%d</td><td style='color:%s'><b>%s</b></td><td><code>%s</code></td><td>%s</td><td align=right>%.0f</td>"
                   "<td style='color:#666;font-size:11px'>%s</td></tr>"
                   % (s["n"], col.get(s["server"], "#333"), H.escape(s["tool"]), H.escape(json.dumps(s["args"])[:160]),
                      "ok" if s["ok"] else "<b style='color:#c62828'>error</b>", s["ms"], H.escape(s["preview"][:160]))
                   for s in res.transcript)
    f = res.final or {}
    return ("<div style='font:14px system-ui'><b>%s</b> · %s / %s · %d tool calls · %.0f s%s<br><b>%s</b> — %s%s</div>"
            "<table style='font-size:12px'><tr><th>#</th><th>tool</th><th>args</th><th></th><th>ms</th><th>result</th></tr>%s</table>") % (
        H.escape(res.job), H.escape(res.provider), H.escape(res.model), res.tool_calls, res.seconds,
        (" · <span style='color:#c62828'>%s</span>" % H.escape(res.error)) if res.error else "",
        H.escape(str(f.get("verdict"))), H.escape(str(f.get("summary") or ""))[:600],
        (" · first frames %s ms" % f["first_frame_ms"]) if f.get("first_frame_ms") is not None else "", rows)
