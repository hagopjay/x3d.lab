"""One event stream for everything the lab does.

Every step, check, log line and timing goes through `bus.emit` (or the `bus.span` timer) and fans out to:
  * the notebook's live panel (filtered by LOG_LEVEL and LOG_DENSITY, both changeable while running),
  * a JSONL file per session (everything, DEBUG included),
  * the gateway's SSE stream, which the dashboard turns into browser console output and
    custom tracks in the Chrome DevTools Performance panel.

Secrets registered with `bus.add_secret` (API keys, tokens) are replaced before any sink sees an event.
"""

from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
DENSITIES = ("compact", "rich", "firehose")
REDACTED = "[redacted]"

# DevTools custom-track names used by the dashboard (Performance panel > custom tracks).
TRACKS = ("Validator", "LLM", "MCP", "Blender", "X_ITE", "Gateway", "Lab")


def _norm_level(level: str) -> str:
    level = (level or "INFO").upper()
    return "WARN" if level == "WARNING" else (level if level in LEVELS else "INFO")


class Bus:
    def __init__(self, capacity: int = 5000):
        self._lock = threading.RLock()
        self._ring: deque = deque(maxlen=capacity)
        self._seq = itertools.count(1)
        self._subs: list[Callable[[dict], None]] = []
        self._secrets: set[str] = set()
        self._jsonl_path: Path | None = None
        self._jsonl_fh = None
        self.screen_level = "INFO"
        self.density = "compact"
        self.to_screen = True
        self.screen: Callable[[dict], None] | None = _print_sink

    # ------------------------------------------------------------------ configuration
    def configure(self, level: str | None = None, density: str | None = None, to_screen: bool | None = None,
                  jsonl: str | Path | None = None) -> None:
        with self._lock:
            if level:
                self.screen_level = _norm_level(level)
            if density:
                if density not in DENSITIES:
                    raise ValueError("density must be one of %s" % (DENSITIES,))
                self.density = density
            if to_screen is not None:
                self.to_screen = bool(to_screen)
            if jsonl:
                if self._jsonl_fh:
                    self._jsonl_fh.close()
                self._jsonl_path = Path(jsonl)
                self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
                self._jsonl_fh = open(self._jsonl_path, "a", encoding="utf-8")

    @property
    def jsonl_path(self) -> Path | None:
        return self._jsonl_path

    def add_secret(self, value: str | None) -> None:
        """Never let this value reach a log, the dashboard or a file. Short values are ignored."""
        if value and len(value) >= 8:
            with self._lock:
                self._secrets.add(value)

    def redact(self, obj):
        if not self._secrets:
            return obj
        if isinstance(obj, str):
            for s in self._secrets:
                if s in obj:
                    obj = obj.replace(s, REDACTED)
            return obj
        if isinstance(obj, dict):
            return {k: self.redact(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.redact(v) for v in obj]
        return obj

    # ------------------------------------------------------------------ emitting
    def emit(self, msg: str, *, level: str = "INFO", src: str = "lab", kind: str = "log", data: dict | None = None,
             span: dict | None = None, job: str | None = None) -> dict:
        ev = {"seq": 0, "ts": time.time(), "level": _norm_level(level), "src": src, "kind": kind, "msg": str(msg)}
        if data:
            ev["data"] = data
        if span:
            ev["span"] = span
        if job:
            ev["job"] = job
        ev = self.redact(ev)
        with self._lock:
            ev["seq"] = next(self._seq)
            self._ring.append(ev)
            subs = list(self._subs)
            if self._jsonl_fh:
                try:
                    self._jsonl_fh.write(json.dumps(ev, default=str) + "\n")
                    self._jsonl_fh.flush()
                except Exception:
                    pass
        if self.to_screen and self.screen and LEVELS[ev["level"]] >= LEVELS[self.screen_level]:
            if ev["level"] != "DEBUG" or self.density == "firehose" or self.screen_level == "DEBUG":
                try:
                    self.screen(ev)
                except Exception:
                    pass
        for fn in subs:
            try:
                fn(ev)
            except Exception:
                pass
        return ev

    def debug(self, msg, **kw):
        return self.emit(msg, level="DEBUG", **kw)

    def info(self, msg, **kw):
        return self.emit(msg, level="INFO", **kw)

    def warn(self, msg, **kw):
        return self.emit(msg, level="WARN", **kw)

    def error(self, msg, **kw):
        return self.emit(msg, level="ERROR", **kw)

    @contextmanager
    def span(self, name: str, *, track: str = "Lab", src: str = "lab", job: str | None = None, data: dict | None = None,
             level: str = "INFO"):
        """Time a block. Emits one event at the end with span={track,start,end} for the DevTools timeline.

        The yielded dict can be filled in by the block (status, extra data)."""
        info: dict = {"status": "ok"}
        if data:
            info.update(data)
        t0 = time.time()
        try:
            yield info
        except BaseException as exc:
            info["status"] = "error"
            info["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
            raise
        finally:
            t1 = time.time()
            lvl = "ERROR" if info.get("status") == "error" else level
            self.emit("%s (%.0f ms)" % (name, (t1 - t0) * 1000), level=lvl, src=src, kind="span", job=job,
                      data=info, span={"name": name, "track": track, "start": t0, "end": t1})

    # ------------------------------------------------------------------ reading
    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)

        def unsubscribe():
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)
        return unsubscribe

    def since(self, seq: int = 0, limit: int = 2000) -> list[dict]:
        with self._lock:
            items = [e for e in self._ring if e["seq"] > seq]
        return items[-limit:]

    def latest_seq(self) -> int:
        with self._lock:
            return self._ring[-1]["seq"] if self._ring else 0

    def last(self, n: int = 50) -> list[dict]:
        with self._lock:
            return list(self._ring)[-n:]

    def clear(self) -> None:
        with self._lock:
            self._ring.clear()


# ---------------------------------------------------------------------- formatting
def _short(v, n=80) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def format_event(ev: dict, density: str = "compact") -> str:
    t = time.strftime("%H:%M:%S", time.localtime(ev["ts"]))
    head = "%s %-5s %-9s %s" % (t, ev["level"], ev["src"][:9], ev["msg"])
    if density == "compact" or not ev.get("data"):
        return head
    if density == "rich":
        pairs = ["%s=%s" % (k, _short(v, 60)) for k, v in list(ev["data"].items())[:6]]
        return head + "  | " + " ".join(pairs)
    return head + "\n    " + json.dumps(ev.get("data"), default=str, indent=None)[:4000]


def _print_sink(ev: dict) -> None:
    print(format_event(ev, bus.density), flush=True)


class BusHandler(logging.Handler):
    """Route standard `logging` records (uvicorn, httpx, mcp) into the bus."""

    def __init__(self, src: str = "py"):
        super().__init__()
        self.src = src

    def emit(self, record: logging.LogRecord) -> None:
        try:
            bus.emit(record.getMessage(), level=record.levelname, src=self.src, data={"logger": record.name})
        except Exception:
            pass


bus = Bus()
