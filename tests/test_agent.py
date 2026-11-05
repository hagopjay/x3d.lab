"""The two-MCP agent with a scripted model: the real x3d_mcp server, the real gateway viewer, and a real headless
Chromium behind the devtools tool names (the Playwright stand-in; chrome-devtools-mcp itself needs npx and network)."""

import json
import os
from pathlib import Path

import pytest

from x3dlab import agent, browser, keys, llm
from x3dlab.scenes import LANTERN

from test_quickstart import FakeConversation

BROKEN = LANTERN.replace('<Box size="1 1.4 1"/>', '<Box size="1 1.4 1" solid="True"/>')


def test_devtools_command_is_private_and_pinned():
    args, env = agent.devtools_command("/opt/chrome", headless=True)
    assert "chrome-devtools-mcp@%s" % agent.DEVTOOLS_PIN in args
    for flag in ("--isolated", "--no-usage-statistics", "--no-performance-crux", "--headless"):
        assert flag in args
    assert args[args.index("--executablePath") + 1] == "/opt/chrome"
    assert env["CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS"] == "1"
    assert not any(k.endswith("_API_KEY") for k in env)


def test_lab_urls_are_rewritten_and_masked(tmp_path):
    tb = agent.Toolbox(None, None, None, tmp_path, "http://127.0.0.1:8080/k/SECRETKEY", ["SECRETKEY"], "job1", "job1")
    real = tb.to_real({"url": "lab://view/fixed", "nested": ["lab://view/a_b"]})
    assert real["url"] == "http://127.0.0.1:8080/k/SECRETKEY/view?src=out/job1/fixed.x3d"
    back = tb.mask("Navigated to %s; also SECRETKEY elsewhere; a.x3d stays" % real["url"])
    assert "lab://view/fixed" in back and "SECRETKEY" not in back and "a.x3d stays" in back


@pytest.fixture()
def scripted(monkeypatch):
    holder = {}

    def make(script):
        def conversation(provider, key, model, **kw):
            c = FakeConversation(script, **{k: v for k, v in kw.items() if k in ("system", "tools")})
            holder["conv"] = c
            return c
        monkeypatch.setattr(llm, "conversation", conversation)
        monkeypatch.setattr(keys, "pick", lambda provider="auto", model_id=None: ("anthropic", "k", "fake-1"))
        return holder
    return make


@pytest.fixture()
def local_xite(lab):
    """Serve X_ITE from a local copy (X3DLAB_XITE_DIR) so the viewer works offline; skip without one or without Chromium."""
    d = os.environ.get("X3DLAB_XITE_DIR")
    if not d or not (Path(d) / "x_ite.min.js").exists() or browser.chromium_path() is None:
        pytest.skip("needs X3DLAB_XITE_DIR and a Chromium for Playwright")
    old = (lab.xite_src, lab.xite_css, lab.xite_dir)
    lab.xite_src, lab.xite_css, lab.xite_dir = "vendor/x_ite/x_ite.min.js", "vendor/x_ite/x_ite.css", Path(d)
    if lab.browser is None:
        lab.start_browser()
    yield lab
    lab.xite_src, lab.xite_css, lab.xite_dir = old


def test_agent_fixes_stages_opens_traces_and_reports(local_xite, scripted):
    lab = local_xite
    h = scripted([
        [("x3d_validate_x3d", {"content": BROKEN})],
        [("lab_stage_scene", {"name": "fixed", "xml": LANTERN})],
        [("devtools_navigate_page", {"url": "lab://view/fixed"}), ("devtools_list_console_messages", {})],
        [("devtools_performance_start_trace", {"reload": True}), ("devtools_performance_stop_trace", {})],
        [("lab_finish", {"verdict": "fixed_and_renders", "summary": "solid=True -> true", "first_frame_ms": 12, "console_errors": 0,
                         "final_scene": "lab://view/fixed"})],
    ])
    res = agent.run(lab, BROKEN, provider="anthropic", devtools="playwright")
    assert res.error is None, res.error
    assert res.final["verdict"] == "fixed_and_renders" and res.tool_calls == 7
    conv = h["conv"]
    names = {t["name"] for t in conv.tools}
    assert {"x3d_validate_x3d", "devtools_navigate_page", "lab_stage_scene", "lab_finish"} <= names
    assert all("path" not in t["input_schema"].get("properties", {}) for t in conv.tools)
    seen = json.dumps([[text for _, text in r] if isinstance(r, list) else r for r in conv.seen])
    assert "SFBool" in seen                                    # the validator's complaint reached the model
    assert '\\"ok\\": true' in seen or '"ok": true' in seen    # the viewer's console line: the scene loaded in Chrome
    assert "x3d.lab user timings: " in seen and "parse" in seen
    assert lab.dashboard_key not in seen and "lab://view/fixed" in seen
    servers = [s["server"] for s in res.transcript]
    assert servers.count("devtools") == 4 and servers.count("x3d") == 1
    assert (Path(lab.out_dir) / res.job / "agent.json").exists()
    assert "fixed_and_renders" in agent.transcript_html(res)


def test_viewer_and_nested_out_routes(local_xite, k):
    import httpx
    r = httpx.get(k + "view")
    assert r.status_code == 200 and "window.lab" in r.text and "vendor/x_ite/x_ite.min.js" in r.text
    d = Path(local_xite.out_dir) / "nestedjob" / "fuzz"
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.html").write_text("<p>hi</p>")
    assert httpx.get(k + "out/nestedjob/fuzz/report.html").status_code == 200
    assert httpx.get(k + "out/nestedjob/%2e%2e/nestedjob/fuzz/report.html").status_code == 404   # encoded: reaches the server
    assert httpx.get(k + "out/nestedjob/a/b/c/d.html").status_code == 404
