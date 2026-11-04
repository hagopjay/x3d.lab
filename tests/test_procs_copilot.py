import os
import socket
import stat
import subprocess
import sys
import time
import zipfile

import pytest

from x3dlab import copilot, procs


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_free_port_clears_a_leftover_server():
    port = _free_port()
    stale = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(50):
        if procs.port_open(port):
            break
        time.sleep(0.1)
    assert procs.port_open(port)
    assert stale.pid in procs.free_port(port)
    assert not procs.port_open(port)


def test_health_only_counts_our_process():
    """The old trap: a second server fails to bind (exit code), the first one still answers /pulse."""
    port = _free_port()
    first = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        for _ in range(50):
            if procs.port_open(port):
                break
            time.sleep(0.1)
        m = procs.Managed("dup", [], port, procs.runtime.sub("logs") / "dup.log",
                          subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]))
        with pytest.raises(procs.ProcError, match="held by pid"):
            procs._wait_healthy(m, "http://127.0.0.1:%d/" % port, 5)
        m.stop()
    finally:
        first.kill()


def _zip(path, entries):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries:
            z.writestr(name, data)


def test_copilot_zip_is_extracted_safely(tmp_path):
    good = tmp_path / "good.zip"
    _zip(good, [("site/index.html", "<p>hi"), ("site/assets/a.js", "1")])
    root = copilot.load("path", path=str(good))
    assert (root / "index.html").read_text() == "<p>hi"
    for bad_name in ("../escape.txt", "/abs.txt", "a/../../b.txt"):
        bad = tmp_path / "bad.zip"
        _zip(bad, [("index.html", "x"), (bad_name, "y")])
        with pytest.raises(copilot.CopilotError):
            copilot.load("path", path=str(bad))
    link = tmp_path / "link.zip"
    with zipfile.ZipFile(link, "w") as z:
        z.writestr("index.html", "x")
        info = zipfile.ZipInfo("evil")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "/etc/passwd")
    with pytest.raises(copilot.CopilotError, match="link"):
        copilot.load("path", path=str(link))
