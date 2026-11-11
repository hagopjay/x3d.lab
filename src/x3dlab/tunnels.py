"""Public URLs for the gateway: a Cloudflare quick tunnel (no account) or ngrok (token, optional fixed domain).

Colab's own port proxy is always available too, but it only works inside your signed-in browser, so your
phone and remote MCP clients (Claude Code, Cursor) need one of these.
"""

from __future__ import annotations

import os
import re
import stat
import time
import urllib.request
from pathlib import Path

from . import pins, procs, runtime
from .events import bus

TRYCF = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com")


class TunnelError(RuntimeError):
    pass


def cloudflared_binary() -> Path:
    exe = runtime.sub("bin") / "cloudflared"
    if exe.is_file() and os.access(exe, os.X_OK):
        return exe
    url = pins.get("cloudflared", "url").format(version=pins.get("cloudflared", "version"))
    with bus.span("download cloudflared %s" % pins.get("cloudflared", "version"), src="tunnel"):
        tmp = exe.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.chmod(tmp.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        tmp.rename(exe)
    return exe


def find_trycloudflare_url(text: str) -> str | None:
    m = TRYCF.search(text or "")
    return m.group(0) if m else None


def cloudflared(port: int, timeout: float = 45) -> str:
    """Start a quick tunnel to 127.0.0.1:port. Its log goes to a file (a pipe left unread would stall it)."""
    exe = cloudflared_binary()
    m = procs.start("cloudflared", [str(exe), "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:%d" % port])
    deadline = time.time() + timeout
    while time.time() < deadline:
        url = find_trycloudflare_url(m.log_path.read_text(errors="replace"))
        if url:
            bus.info("Cloudflare tunnel up: %s" % url, src="tunnel")
            return url
        if not m.alive():
            break
        time.sleep(0.5)
    raise TunnelError("cloudflared gave no URL in %.0f s. Log tail:\n%s" % (timeout, m.tail(20)))


def ngrok_token() -> str | None:
    for name in ("NGROK_AUTHTOKEN", "NGROK_TOKEN", "NGROK"):
        v = os.environ.get(name)
        if v:
            return v
    try:
        from google.colab import userdata
        for name in ("NGROK_AUTHTOKEN", "NGROK_TOKEN", "NGROK"):   # the old notebook used "NGROK"
            try:
                v = userdata.get(name)
            except Exception:
                v = None
            if v:
                return v
    except Exception:
        pass
    return None


def ngrok_connect(port, domain=""):
    """Open an ngrok http tunnel, on a fixed domain if one is given, under any pyngrok.
    pyngrok 9 talks to ngrok's /api/endpoints, which wants url="https://<domain>"; older pyngrok uses /api/tunnels,
    which wants domain="<domain>". Each refuses the other's field ("field domain not found in type config.Endpoint"),
    so send the one this pyngrok expects and fall back to the other once."""
    from pyngrok import conf, ngrok
    d = (domain or "").strip().removeprefix("https://").removeprefix("http://").strip("/")
    if not d:
        return ngrok.connect(port, "http")
    tries = [{"url": "https://" + d}, {"domain": d}]
    if getattr(conf.get_default(), "config_version", None) != "3":
        tries.reverse()
    for i, kw in enumerate(tries):
        try:
            return ngrok.connect(port, "http", **kw)
        except Exception as e:
            if i or not re.search(r"field (url|domain) not found", str(e)):
                raise


def ngrok(port: int, domain: str | None = None) -> str:
    try:
        from pyngrok import conf, ngrok as _ngrok
    except ImportError:
        from .install import pip
        pip(["pyngrok>=7"])
        from pyngrok import conf, ngrok as _ngrok
    tok = ngrok_token()
    if not tok:
        raise TunnelError("ngrok needs an auth token: add NGROK_AUTHTOKEN in Colab Secrets (free at ngrok.com), "
                          "or use TUNNEL='cloudflared', which needs no account.")
    bus.add_secret(tok)
    conf.get_default().auth_token = tok
    for t in _ngrok.get_tunnels():
        try:
            _ngrok.disconnect(t.public_url)
        except Exception:
            pass
    t = ngrok_connect(port, domain)
    url = t.public_url.replace("http://", "https://")
    procs.register_inprocess(-port, lambda: _ngrok.disconnect(t.public_url))
    bus.info("ngrok tunnel up: %s" % url, src="tunnel")
    return url


def stop_all() -> None:
    procs.stop("cloudflared")
    try:
        from pyngrok import ngrok as _ngrok
        _ngrok.kill()
    except Exception:
        pass
