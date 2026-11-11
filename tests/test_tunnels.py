"""Tunnel plumbing without opening a public tunnel: a stand-in cloudflared prints a URL the way the real one
does, and the tunnel self-test runs against the local gateway address."""

import os
import stat
import textwrap

from x3dlab import hosting, procs, runtime, tunnels

CF_LOG = textwrap.dedent("""\
    2026-11-04T20:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...
    2026-11-04T20:00:01Z INF +--------------------------------------------------------------------------------------------+
    2026-11-04T20:00:01Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
    2026-11-04T20:00:01Z INF |  https://brave-otter-lamp-quiet.trycloudflare.com                                          |
    2026-11-04T20:00:01Z INF +--------------------------------------------------------------------------------------------+
""")


def test_find_url():
    assert tunnels.find_trycloudflare_url(CF_LOG) == "https://brave-otter-lamp-quiet.trycloudflare.com"
    assert tunnels.find_trycloudflare_url("INF Requesting new quick Tunnel") is None


def test_cloudflared_process_and_url(monkeypatch):
    fake = runtime.sub("bin") / "cloudflared"
    fake.write_text("#!/bin/sh\ncat >&2 <<'EOF'\n%sEOF\nexec sleep 30\n" % CF_LOG)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    try:
        url = tunnels.cloudflared(9, timeout=10)
        assert url == "https://brave-otter-lamp-quiet.trycloudflare.com"
        m = procs.get("cloudflared")
        assert m.alive() and "--url" in m.cmd and "http://127.0.0.1:9" in m.cmd
    finally:
        procs.stop("cloudflared")
        os.remove(fake)


def test_tunnel_selftest_runs_the_same_checks(lab):
    lab.urls["tunnel"] = lab.urls["local"].replace("127.0.0.1", "localhost")
    try:
        checks = hosting.selftest(lab, "tunnel")
        assert [c for c, ok, _ in checks if not ok] == []
        assert len(checks) == 6
        assert lab.dashboard_url() .startswith("http://localhost"), "the tunnel is the preferred route when present"
        cfg = lab.client_configs()
        assert lab.mcp_url() in cfg["Claude Code"] and "Bearer " + lab.mcp_token in cfg["Claude Code"]
    finally:
        lab.urls.pop("tunnel")


def test_ngrok_token_names(monkeypatch):
    for name in ("NGROK_AUTHTOKEN", "NGROK_TOKEN", "NGROK"):
        monkeypatch.delenv(name, raising=False)
    assert tunnels.ngrok_token() is None
    monkeypatch.setenv("NGROK", "old-notebook-name")
    assert tunnels.ngrok_token() == "old-notebook-name"
    monkeypatch.setenv("NGROK_AUTHTOKEN", "documented-name")
    assert tunnels.ngrok_token() == "documented-name"


def _fake_pyngrok(monkeypatch, config_version, accepts):
    """A stand-in pyngrok whose connect() accepts only one of url=/domain=, like the real agent APIs."""
    import sys, types
    calls = []
    class Conf:
        pass
    c = Conf()
    if config_version is not None:
        c.config_version = config_version
    conf = types.SimpleNamespace(get_default=lambda: c)
    def connect(port, proto, **kw):
        calls.append(kw)
        bad = [k for k in kw if k in ("url", "domain") and k != accepts]
        if bad:
            raise RuntimeError('API returned 400: field %s not found in type config.Endpoint' % bad[0])
        return types.SimpleNamespace(public_url="https://x.ngrok-free.app")
    ngrok = types.SimpleNamespace(connect=connect)
    pkg = types.ModuleType("pyngrok"); pkg.conf = conf; pkg.ngrok = ngrok
    monkeypatch.setitem(sys.modules, "pyngrok", pkg)
    return calls


def test_ngrok_connect_pyngrok9_sends_url(monkeypatch):
    from x3dlab.tunnels import ngrok_connect
    calls = _fake_pyngrok(monkeypatch, "3", "url")
    ngrok_connect(8765, "https://my-name.ngrok-free.app/")
    assert calls == [{"url": "https://my-name.ngrok-free.app"}]


def test_ngrok_connect_old_pyngrok_sends_domain(monkeypatch):
    from x3dlab.tunnels import ngrok_connect
    calls = _fake_pyngrok(monkeypatch, None, "domain")
    ngrok_connect(8765, "my-name.ngrok-free.app")
    assert calls == [{"domain": "my-name.ngrok-free.app"}]


def test_ngrok_connect_falls_back_once_when_the_guess_is_wrong(monkeypatch):
    from x3dlab.tunnels import ngrok_connect
    calls = _fake_pyngrok(monkeypatch, "3", "domain")      # says v3 but its agent still wants domain=
    ngrok_connect(8765, "my-name.ngrok-free.app")
    assert calls == [{"url": "https://my-name.ngrok-free.app"}, {"domain": "my-name.ngrok-free.app"}]


def test_ngrok_connect_without_domain_sends_neither(monkeypatch):
    from x3dlab.tunnels import ngrok_connect
    calls = _fake_pyngrok(monkeypatch, "3", "url")
    ngrok_connect(8765, "")
    assert calls == [{}]
