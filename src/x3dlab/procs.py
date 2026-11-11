"""Start, watch and stop the lab's background processes.

Fixes the trap in the old x3d_duo notebook: a server from an earlier run kept port 8000, the new one exited
with code 3 (uvicorn's startup failure), and the health check still passed because the *old* server answered.
Here a start first frees the port, then health only counts if the answering process is the one we started.
Subprocesses use their own session, so they survive a kernel restart; `free_port` cleans those up next time.
"""

from __future__ import annotations

import os
import signal
import re
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import runtime
from .events import bus


class ProcError(RuntimeError):
    pass


# In-process servers (the gateway runs in a kernel thread) register a stopper for their port here,
# so freeing the port never kills the notebook kernel itself.
_inproc: dict[int, Callable[[], None]] = {}


def register_inprocess(port: int, stopper: Callable[[], None]) -> None:
    _inproc[port] = stopper


def unregister_inprocess(port: int) -> None:
    _inproc.pop(port, None)


def listeners(port: int) -> list[int]:
    """PIDs listening on a TCP port (empty if none or unknown)."""
    pids: set[int] = set()
    try:
        import psutil
        for c in psutil.net_connections(kind="tcp"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid:
                pids.add(c.pid)
    except Exception:
        pass
    if not pids:
        try:
            out = subprocess.run(["ss", "-ltnpH", "sport = :%d" % port], capture_output=True, text=True, timeout=5).stdout
            for tok in out.replace(",", " ").split():
                if tok.startswith("pid="):
                    pids.add(int(tok[4:]))
        except Exception:
            pass
    return sorted(pids)


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _signal(pid: int, sig: int) -> None:
    """Signal the process's own group when it has one; never our group (that would include this kernel)."""
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return
    if pgid != os.getpgid(0) and pgid == pid:
        try:
            os.killpg(pgid, sig)
            return
        except OSError:
            pass
    try:
        os.kill(pid, sig)
    except OSError:
        pass


def _kill(pid: int, grace: float = 4.0) -> None:
    if pid == os.getpid():
        return
    _signal(pid, signal.SIGTERM)
    t = time.time() + grace
    while time.time() < t:
        if not _alive(pid):
            return
        time.sleep(0.1)
    _signal(pid, signal.SIGKILL)


def _alive(pid: int) -> bool:
    try:                                   # our own child: reap it, or it lingers as a zombie that "exists"
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass
    except OSError:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        import psutil
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except Exception:
        return True


# Services of the machine we run on. Colab's web app (/datalab/web/app.js) listens on 8080 and Jupyter on 9000;
# stopping either disconnects the runtime, so free_port refuses instead.
HOST_SERVICES = ("/datalab/", "jupyter", "colab_kernel_launcher", "ipykernel")


def _is_host_service(pid: int) -> bool:
    try:
        import psutil
        cmd = " ".join(psutil.Process(pid).cmdline())
    except Exception:
        return False
    return any(k in cmd for k in HOST_SERVICES)


def free_port(port: int) -> list[int]:
    """Stop whatever listens on `port`. Returns the PIDs that were stopped."""
    stopped = []
    if port in _inproc:
        try:
            _inproc[port]()
        except Exception:
            pass
        unregister_inprocess(port)
        time.sleep(0.3)
    me = os.getpid()
    for pid in listeners(port):
        if pid == me:
            bus.warn("port %d is held by this kernel but no in-process server is registered; leaving it" % port, src="procs")
            continue
        if _is_host_service(pid):
            raise RuntimeError("port %d belongs to the host (pid %d, e.g. Colab's web app on 8080); "
                               "choose another port instead of stopping it" % (port, pid))
        bus.info("freeing port %d: stopping pid %d (left over from an earlier run)" % (port, pid), src="procs")
        _kill(pid)
        stopped.append(pid)
    deadline = time.time() + 5
    while port_open(port) and time.time() < deadline:
        time.sleep(0.1)
    return stopped


def mcp_log_classifier() -> Callable[[str], str]:
    """Levels for x3d_mcp's log: per-request chatter is DEBUG, and so is the traceback mcp 1.x prints when a
    client simply closes its session ('Error in standalone SSE writer' ... ClosedResourceError).

    The server logs through rich, which wraps the message into a narrow column ("Error in standalone SSE" /
    "writer") and draws the traceback as an indented box, so: match the start of the phrase, keep everything until
    the closing ClosedResourceError line or the next log record (a line that starts with a level name) as DEBUG,
    and never swallow more than MAX_BENIGN lines."""
    state = {"benign": 0}
    chatter = ("Processing request of type", "Created new transport", "Terminating session", "StreamableHTTP session manager")
    start = re.compile(r"Error in standalone SSE")
    # rich prints "[time] LEVEL message", blanks a repeated time with spaces, and indents continuation lines
    record = re.compile(r"^\s*(\[[^\]]*\]\s*)?(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s")
    MAX_BENIGN = 400

    def classify(line: str) -> str:
        if start.search(line):
            state["benign"] = 1
            return "DEBUG"
        if state["benign"]:
            stripped = line.strip()
            if record.match(line) and not start.search(line):
                state["benign"] = 0                       # a new log record: classify it normally below
            else:
                state["benign"] += 1
                if stripped.endswith("ClosedResourceError") or state["benign"] > MAX_BENIGN:
                    state["benign"] = 0
                return "DEBUG"
        if any(c in line for c in chatter):
            return "DEBUG"
        return _guess_level(line)
    return classify


def _guess_level(line: str) -> str:
    low = line.lower()
    if "traceback" in low or low.startswith("error") or " error" in low[:40] or "exception" in low[:60]:
        return "ERROR"
    if "warn" in low[:40]:
        return "WARN"
    if line.startswith("INFO:") or '" 200' in line or '" 202' in line:
        return "DEBUG"
    return "INFO"


@dataclass
class Managed:
    name: str
    cmd: list
    port: int | None
    log_path: Path
    proc: subprocess.Popen
    started: float = field(default_factory=time.time)
    classify: Callable[[str], str] | None = None

    def alive(self) -> bool:
        return self.proc.poll() is None

    def tail(self, n: int = 40) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-n:])
        except Exception:
            return ""

    def stop(self, grace: float = 4.0) -> None:
        if self.alive():
            _kill(self.proc.pid, grace)
        try:
            self.proc.wait(timeout=2)
        except Exception:
            pass

    def status(self) -> dict:
        return {"name": self.name, "pid": self.proc.pid, "alive": self.alive(), "exit": self.proc.poll(),
                "port": self.port, "uptime_s": round(time.time() - self.started), "log": str(self.log_path)}


_registry: dict[str, Managed] = {}


def _follow(m: Managed) -> None:
    """Copy the process log into the event bus, line by line, until the process ends."""
    pos = 0
    while True:
        try:
            with open(m.log_path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(pos)
                chunk = fh.read()
                pos = fh.tell()
        except FileNotFoundError:
            chunk = ""
        for line in chunk.splitlines():
            if line.strip():
                bus.emit(line[:2000], level=(m.classify or _guess_level)(line), src=m.name, kind="proclog")
        if not m.alive() and not chunk:
            code = m.proc.poll()
            if _registry.get(m.name) is m:
                bus.emit("%s exited with code %s" % (m.name, code), level="INFO" if code in (0, -15) else "ERROR",
                         src="procs", data={"name": m.name, "exit": code})
            return
        time.sleep(0.25)


def start(name: str, cmd: list, *, env: dict | None = None, cwd: str | None = None, port: int | None = None,
          health: str | None = None, timeout: float = 90.0, classify: Callable[[str], str] | None = None) -> Managed:
    """Start a managed subprocess. With `port` and `health`, wait until *this* process answers."""
    if name in _registry:
        _registry[name].stop()
    if port:
        free_port(port)
    log_path = runtime.sub("logs") / ("%s.log" % name)
    fh = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env, cwd=cwd, start_new_session=True)
    fh.close()
    m = Managed(name, list(cmd), port, log_path, proc, classify=classify)
    _registry[name] = m
    (runtime.sub("run") / ("%s.pid" % name)).write_text(str(proc.pid))
    threading.Thread(target=_follow, args=(m,), daemon=True, name="follow-" + name).start()
    bus.info("started %s (pid %d)" % (name, proc.pid), src="procs", data={"cmd": " ".join(map(str, cmd))[:300]})
    if health:
        _wait_healthy(m, health, timeout)
    return m


def _wait_healthy(m: Managed, url: str, timeout: float) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        if not m.alive():
            raise ProcError("%s exited with code %s before it was healthy. Log tail:\n%s" % (m.name, m.proc.poll(), m.tail()))
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    owners = listeners(m.port) if m.port else []
                    if owners and m.proc.pid not in owners and not _is_child(owners, m.proc.pid):
                        raise ProcError("port %d answers but is held by pid %s, not %s (pid %d)"
                                        % (m.port, owners, m.name, m.proc.pid))
                    bus.info("%s is healthy at %s" % (m.name, url), src="procs")
                    return
        except ProcError:
            raise
        except Exception as exc:
            last = str(exc)
        time.sleep(0.5)
    raise ProcError("%s did not become healthy in %.0f s (%s). Log tail:\n%s" % (m.name, timeout, last, m.tail()))


def _is_child(pids: list[int], parent: int) -> bool:
    try:
        import psutil
        kids = {c.pid for c in psutil.Process(parent).children(recursive=True)}
        return any(p in kids for p in pids)
    except Exception:
        return False


def get(name: str) -> Managed | None:
    return _registry.get(name)


def stop(name: str) -> None:
    m = _registry.pop(name, None)
    if m:
        m.stop()
        bus.info("stopped %s" % name, src="procs")


def stop_all() -> None:
    for name in list(_registry):
        stop(name)
    for port, fn in list(_inproc.items()):
        try:
            fn()
        except Exception:
            pass
        unregister_inprocess(port)


def status() -> list[dict]:
    return [m.status() for m in _registry.values()]
