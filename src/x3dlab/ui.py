"""Notebook-side display: the live log panel, verdict tables, an inline X_ITE viewer and the cell-1 card."""

from __future__ import annotations

import html
import json
import threading
import time
from collections import deque

from . import pins
from .events import DENSITIES, LEVELS, bus, format_event

COLORS = {"DEBUG": "#8a8f98", "INFO": "inherit", "WARN": "#b26a00", "ERROR": "#c62828"}


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def have_widgets() -> bool:
    try:
        import ipywidgets  # noqa: F401
        from IPython import get_ipython
        return get_ipython() is not None
    except Exception:
        return False


class LivePanel:
    """A scrolling, filterable view of the event stream that updates while other cells run."""

    def __init__(self, lines: int = 14, level: str | None = None, density: str | None = None):
        import ipywidgets as W
        self.lines = lines
        self.buf: deque = deque(maxlen=400)
        self.level = W.Dropdown(options=list(LEVELS), value=level or bus.screen_level, description="level",
                                layout=W.Layout(width="170px"))
        self.density = W.Dropdown(options=list(DENSITIES), value=density or bus.density, description="density",
                                  layout=W.Layout(width="200px"))
        self.size = W.IntSlider(value=lines, min=4, max=60, description="lines", layout=W.Layout(width="260px"))
        self.pause = W.ToggleButton(value=False, description="pause", icon="pause", layout=W.Layout(width="90px"))
        self.out = W.HTML()
        self.widget = W.VBox([W.HBox([self.level, self.density, self.size, self.pause]), self.out])
        self._dirty = True
        self._stop = False
        for w in (self.level, self.density, self.size):
            w.observe(self._changed, names="value")
        self._unsub = bus.subscribe(self._on)
        for ev in bus.last(200):
            self.buf.append(ev)
        threading.Thread(target=self._loop, daemon=True, name="x3dlab-panel").start()

    def _changed(self, _):
        bus.configure(level=self.level.value, density=self.density.value)
        self._dirty = True

    def _on(self, ev):
        self.buf.append(ev)
        self._dirty = True

    def _render(self) -> str:
        lvl = LEVELS[self.level.value]
        rows = [e for e in self.buf if LEVELS[e["level"]] >= lvl][-self.size.value:]
        body = "\n".join("<div style='color:%s'>%s</div>" % (COLORS[e["level"]], esc(format_event(e, self.density.value)))
                         for e in rows)
        return ("<div style='font:12px/1.45 ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;"
                "max-height:%dpx;overflow:auto;border:1px solid #8884;border-radius:6px;padding:6px 8px'>%s</div>"
                % (18 * self.size.value + 16, body or "<i>no events yet</i>"))

    def _loop(self):
        while not self._stop:
            if self._dirty and not self.pause.value:
                self._dirty = False
                try:
                    self.out.value = self._render()
                except Exception:
                    pass
            time.sleep(0.4)

    def close(self):
        self._stop = True
        self._unsub()


def verdict_html(v: dict) -> str:
    issues = v.get("issues", [])
    head = ("<b style='color:%s'>%s</b> &nbsp; XSD %s · %d error(s) · %d warning(s)%s"
            % ("#1d6b3a" if v.get("ok") else "#c62828", "VALID" if v.get("ok") else "NOT VALID",
               "ok" if v.get("xsd_valid") else "failed", v.get("errors", 0), v.get("warnings", 0),
               (" · %.0f ms" % v["ms"]) if v.get("ms") else ""))
    if not issues:
        return "<div>%s</div>" % head
    rows = "".join("<tr><td>%s</td><td>%s</td><td><code>%s</code></td><td>%s</td><td>%s</td></tr>"
                   % (esc(i["severity"]), esc(i["layer"]), esc(i["rule"]), esc(i.get("line") or ""), esc(i["message"][:240]))
                   for i in issues[:40])
    return ("<div>%s</div><table style='font-size:12px;border-collapse:collapse;margin-top:6px'>"
            "<tr><th align=left>severity</th><th align=left>layer</th><th align=left>rule</th><th>line</th>"
            "<th align=left>message</th></tr>%s</table>" % (head, rows))


def xite_viewer_html(xml: str, height: int = 360, version: str | None = None) -> str:
    """Self-contained X_ITE view of a scene for a notebook output cell (loads X_ITE from the CDN)."""
    src = pins.xite_url(version)
    payload = json.dumps(xml)
    vid = "x3dlab_%d" % int(time.time() * 1000)
    return """<div id="{vid}_wrap"><x3d-canvas id="{vid}" style="width:100%;height:{h}px;border-radius:8px;border:1px solid #8884"
  contentScale="auto"></x3d-canvas><div id="{vid}_msg" style="font:12px system-ui;color:#888"></div></div>
<script>
(function(){{
  function go(){{
    const c=document.getElementById("{vid}"), msg=document.getElementById("{vid}_msg");
    const b=X3D.getBrowser(c); const t0=performance.now();
    b.createX3DFromString({payload}).then(s=>{{b.replaceWorld(s); msg.textContent="X_ITE "+b.version+" · loaded in "+Math.round(performance.now()-t0)+" ms · drag to orbit";}})
     .catch(e=>{{msg.textContent="X_ITE could not load this scene: "+e;}});
  }}
  const ready=()=>customElements.whenDefined("x3d-canvas").then(go);
  if (window.X3D) ready(); else {{ const s=document.createElement("script"); s.src="{src}"; s.onload=ready;
    s.onerror=()=>{{document.getElementById("{vid}_msg").textContent="could not load X_ITE from the CDN";}}; document.head.appendChild(s); }}
}})();
</script>""".format(vid=vid, h=height, payload=payload, src=src)


def show(html_str: str) -> None:
    try:
        from IPython.display import HTML, display
        display(HTML(html_str))
    except Exception:
        print(html_str)


def qr_svg(text: str, scale: int = 4) -> str:
    try:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=scale, border=2)
        svg = img.to_string(encoding="unicode")
        return svg.replace("<svg ", "<svg style='background:#fff;border-radius:6px' ", 1)
    except Exception:
        return ""


def copy_block(label: str, text: str, secret: bool = False) -> str:
    """A <pre> with a Copy button. Secret blocks start blurred (click to reveal)."""
    bid = "cb%d" % abs(hash((label, text, time.time())))
    blur = "filter:blur(5px);cursor:pointer" if secret else ""
    return ("<div style='margin:6px 0'><div style='font-size:12px;color:#888'>%s "
            "<button onclick=\"navigator.clipboard.writeText(document.getElementById('%s').innerText)\" "
            "style='font-size:11px;margin-left:6px'>copy</button>%s</div>"
            "<pre id='%s' onclick=\"this.style.filter='none'\" style='margin:2px 0;padding:8px;border:1px solid #8884;"
            "border-radius:6px;white-space:pre-wrap;word-break:break-all;font-size:12px;%s'>%s</pre></div>"
            % (esc(label), bid, " <span style='font-size:11px'>(click to reveal)</span>" if secret else "", bid, blur, esc(text)))


# ---------------------------------------------------------------------- notebook views used by the Quickstart
def result_html(res, lab=None) -> str:
    """Summary, steps, verdict and an inline X_ITE view of a SceneResult."""
    v = res.verdict or {}
    head = ("<div style='font:14px system-ui'><b>%s</b> · %s / %s / %s mode · %.1f s · first draft %s · %d repair round(s)%s%s</div>"
            % ("VALID" if res.valid else ("FAILED: " + esc(res.error) if res.error else "NOT VALID"), esc(res.provider), esc(res.model),
               esc(res.mode), res.seconds, {True: "valid", False: "not valid", None: "—"}[res.first_pass_valid], res.rounds,
               (" · %d tool calls" % res.tool_calls) if res.tool_calls else "",
               (" · tokens in/out %s/%s" % (res.usage.get("input"), res.usage.get("output"))) if res.usage else ""))
    steps = "".join("<li>%s <span style='color:#888'>%.1fs</span></li>" % (esc(s["step"]), s["t"]) for s in res.steps)
    body = head + ("<ol style='font-size:12.5px;margin:4px 0'>%s</ol>" % steps if steps else "")
    if v:
        body += verdict_html(v)
    if res.xml:
        body += xite_viewer_html(res.xml, height=380)
    if lab is not None:
        body += "<div style='font-size:12px;margin-top:4px'><a href='%s' target='_blank'>open on the dashboard</a></div>" % esc(lab.dashboard_url())
    return body


def show_result(res, lab=None) -> None:
    show(result_html(res, lab))


def show_client_configs(lab) -> None:
    if "tunnel" not in lab.urls:
        show("<p>Remote MCP clients need a public URL. Rerun cell 1 with <code>TUNNEL = \"cloudflared\"</code> (no account) "
             "or <code>\"ngrok\"</code>, then run this cell again. Colab's own proxy works only inside your browser.</p>")
        return
    if not lab.expose_mcp:
        show("<p>EXPOSE_MCP is off in cell 1.</p>")
        return
    parts = ["<p>Your x3d_mcp server is reachable at <code>%s</code> with the bearer token below. The token changes every "
             "time cell 1 runs; the endpoint stops when this Colab session ends.</p>" % esc(lab.mcp_url())]
    parts.append(copy_block("Bearer token", lab.mcp_token, secret=True))
    for name, text in lab.client_configs().items():
        parts.append("<h4 style='margin:10px 0 2px'>%s</h4>%s" % (esc(name), copy_block(name, text, secret=True)))
    parts.append("<p style='font-size:12px;color:#888'>Then ask your client something like: <i>use the x3d tools to describe "
                 "OrientationInterpolator, then write and validate a spinning cube scene</i>. Every call it makes appears in "
                 "this notebook's live panel and on the dashboard.</p>")
    show("".join(parts))
