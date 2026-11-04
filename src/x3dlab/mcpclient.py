"""A small client for the x3d_mcp server, plus the one verdict every notebook uses.

A scene is OK when the XSD check passes (validate_x3d) and the semantic check reports no errors
(validate_semantic). Warnings and info never fail a scene. Documents above ~3.5 MB go through the
server's blob store instead of the 4 MiB-capped /mcp request body (the old notebook returned HTTP 500 there).
"""

from __future__ import annotations

import json
import re
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field

import httpx

from . import aio
from .events import bus

INLINE_LIMIT = 3_500_000  # bytes; /mcp refuses request bodies over 4 MiB


@dataclass
class Issue:
    layer: str      # xml | xsd | semantic | input
    severity: str   # error | warning | info
    rule: str
    message: str
    line: int | None = None


@dataclass
class Verdict:
    ok: bool
    xsd_valid: bool
    issues: list = field(default_factory=list)
    schema_version: str | None = None
    ms: float = 0.0

    @property
    def errors(self) -> list:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list:
        return [i for i in self.issues if i.severity == "warning"]

    def layers(self) -> list[str]:
        """Which layers raised an error, in pipeline order."""
        order = ["input", "xml", "xsd", "semantic"]
        found = {i.layer for i in self.errors}
        return [x for x in order if x in found]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["errors"] = len(self.errors)
        d["warnings"] = len(self.warnings)
        d["layers"] = self.layers()
        return d

    def report(self, limit: int = 12) -> str:
        """Plain-text complaint list for an LLM repair prompt."""
        lines = []
        for i in self.errors[:limit]:
            where = (" (line %d)" % i.line) if i.line else ""
            lines.append("- [%s/%s]%s %s" % (i.layer, i.rule, where, i.message))
        return "\n".join(lines) or "no errors"


_LINE = re.compile(r"^Line (\d+):\s*")
_SEM = re.compile(r"^- \*\*\[([^\]]+)\]\*\*\s*(.*)$")


def parse_xsd(payload: dict) -> list[Issue]:
    out = []
    for e in payload.get("errors") or []:
        e = str(e)
        if e.startswith("XML parse error"):
            out.append(Issue("xml", "error", "parse", e))
        elif e.startswith("Provide either"):
            out.append(Issue("input", "error", "empty", e))
        else:
            m = _LINE.match(e)
            out.append(Issue("xsd", "error", "schema", _LINE.sub("", e), int(m.group(1)) if m else None))
    return out


def parse_semantic(md: str) -> list[Issue]:
    """Parse validate_semantic's markdown report into issues."""
    out: list[Issue] = []
    head = (md or "").strip().splitlines()[:1]
    if head and head[0].startswith("# Semantic Check:") and "Report" not in head[0]:
        kind = head[0].split(":", 1)[1].strip()
        if kind.lower() == "all clear":
            return out
        body = md.strip().split("\n", 1)[1].strip() if "\n" in md.strip() else kind
        layer = "input" if kind == "Input Error" else ("xml" if kind == "Parse Error" else "semantic")
        out.append(Issue(layer, "error", kind.lower().replace(" ", "-"), body))
        return out
    section = None
    for line in (md or "").splitlines():
        s = line.strip()
        if s.startswith("## "):
            name = s[3:].strip().lower()
            section = "error" if name.startswith("error") else "warning" if name.startswith("warn") else "info" if name.startswith("info") else None
            continue
        m = _SEM.match(s)
        if m and section:
            msg = m.group(2).strip()
            mm = re.search(r"\bline (\d+)\b", msg)
            out.append(Issue("semantic", section, m.group(1), msg, int(mm.group(1)) if mm else None))
    return out


def combine(xsd_payload: dict, semantic_md: str, ms: float = 0.0) -> Verdict:
    xsd_issues = parse_xsd(xsd_payload)
    sem = parse_semantic(semantic_md)
    # The parse error shows up in both reports; keep one.
    if any(i.layer in ("xml", "input") for i in xsd_issues):
        sem = [i for i in sem if i.layer not in ("xml", "input")]
    issues = xsd_issues + sem
    xsd_valid = bool(xsd_payload.get("valid"))
    ok = xsd_valid and not any(i.severity == "error" for i in sem)
    return Verdict(ok, xsd_valid, issues, xsd_payload.get("schema_version"), ms)


@dataclass
class ToolResult:
    text: str
    is_error: bool
    ms: float

    def json(self):
        return json.loads(self.text)


class X3DMCP:
    """Client for one x3d_mcp server. Async methods; `.run(coro)` runs one from sync code."""

    def __init__(self, url: str = "http://127.0.0.1:8000/mcp", token: str | None = None, timeout: float = 120.0):
        self.url = url
        self.base = url.rsplit("/mcp", 1)[0]
        self.token = token
        self.timeout = timeout
        self._instructions: str | None = None

    @property
    def headers(self) -> dict:
        return {"Authorization": "Bearer " + self.token} if self.token else {}

    @asynccontextmanager
    async def session(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
        async with create_mcp_http_client(headers=self.headers, timeout=httpx.Timeout(30, read=self.timeout)) as http:
            async with streamable_http_client(self.url, http_client=http) as (r, w, _):
                async with ClientSession(r, w) as s:
                    init = await s.initialize()
                    self._instructions = init.instructions
                    yield s

    async def call(self, tool: str, args: dict, session=None, track: str = "MCP", job: str | None = None) -> ToolResult:
        t0 = time.time()
        with bus.span("mcp %s" % tool, track=track, src="mcp", job=job, level="DEBUG") as info:
            if session is None:
                async with self.session() as s:
                    res = await s.call_tool(tool, args)
            else:
                res = await session.call_tool(tool, args)
            text = "\n".join(c.text for c in res.content if getattr(c, "text", None))
            info.update(tool=tool, chars=len(text), is_error=bool(res.isError))
        return ToolResult(text, bool(res.isError), (time.time() - t0) * 1000)

    async def list_tools(self) -> list:
        async with self.session() as s:
            return (await s.list_tools()).tools

    async def instructions(self) -> str:
        if self._instructions is None:
            async with self.session():
                pass
        return self._instructions or ""

    # ------------------------------------------------------------------ blobs for big documents
    async def put_blob(self, data: bytes, media_type: str = "model/x3d+xml") -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as h:
            r = await h.put(self.base + "/blob", content=data, headers={**self.headers, "content-type": media_type})
            r.raise_for_status()
            return r.json()["blob_id"]

    async def doc_args(self, xml: str) -> dict:
        raw = xml.encode("utf-8")
        if len(raw) <= INLINE_LIMIT:
            return {"content": xml}
        bid = await self.put_blob(raw)
        bus.info("document of %.1f MB sent through the blob store" % (len(raw) / 1e6), src="mcp", data={"blob_id": bid})
        return {"blob_id": bid}

    # ------------------------------------------------------------------ the checks the lab uses
    async def verdict(self, xml: str, job: str | None = None) -> Verdict:
        t0 = time.time()
        with bus.span("validate", track="Validator", src="validator", job=job) as info:
            args = await self.doc_args(xml)
            async with self.session() as s:
                x = await self.call("validate_x3d", args, s, track="Validator", job=job)
                m = await self.call("validate_semantic", args, s, track="Validator", job=job)
            try:
                payload = x.json()
            except ValueError:
                payload = {"valid": False, "errors": [x.text[:400]]}
            v = combine(payload, m.text, (time.time() - t0) * 1000)
            info.update(ok=v.ok, errors=len(v.errors), warnings=len(v.warnings), layers=v.layers())
        return v

    async def repair(self, xml: str, job: str | None = None) -> tuple[str, list, dict]:
        """The server's deterministic fixes (no model). Returns (fixed, changes, remaining)."""
        with bus.span("repair_x3d", track="Validator", src="validator", job=job) as info:
            args = await self.doc_args(xml)
            r = await self.call("repair_x3d", args, track="Validator", job=job)
            try:
                d = r.json()
            except ValueError:
                info["status"] = "error"
                return xml, [], {"error": r.text[:300]}
            fixed = d.get("fixed") or xml
            info.update(changes=len(d.get("changes") or []))
        return fixed, d.get("changes") or [], d.get("remaining") or {}

    async def hints(self, xml: str, job: str | None = None) -> list:
        r = await self.call("exact_fix_hints", await self.doc_args(xml), track="Validator", job=job)
        try:
            return r.json().get("hints") or []
        except ValueError:
            return []

    # ------------------------------------------------------------------ sync helpers for notebook cells
    def run(self, coro, timeout: float | None = None):
        return aio.run(coro, timeout)

    def check(self, xml: str) -> Verdict:
        return self.run(self.verdict(xml))

    def tool(self, name: str, **args):
        """Call one tool from a notebook cell and return its JSON (or text)."""
        r = self.run(self.call(name, args))
        try:
            return r.json()
        except ValueError:
            return r.text
