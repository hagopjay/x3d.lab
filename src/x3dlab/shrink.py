"""Shrink an X3D scene to the smallest one that still shows a behaviour (delta debugging for XML).

    res = shrink.shrink(xml, keep=lambda x: blender_drops_it(x), valid=lambda x: lab.check(x).ok, budget=80)
    res.xml                      # the minimal scene; res.checks, res.before / res.after (bytes, nodes)

A candidate is the scene with one thing taken out: a whole node (with everything inside it), then an attribute, then
the tail half of a long list value. It is accepted when `valid` (if given) and `keep` both still say yes. Big cuts are
tried before small ones, and passes repeat until nothing more can go or the budget of `keep` calls is spent. The X3D
root, head and Scene elements stay; `valid` rejects anything else that would break the document (a USE whose DEF
went, a ROUTE to a node that went), so the result is always a scene the referee accepts when `valid` is given.

`valid` is cheap (the validator, ~20 ms); `keep` may be expensive (a Blender import, a browser load), so the budget
counts `keep` calls. Each call sees a complete document string.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from lxml import etree

KEEP_ALWAYS = {"X3D", "Scene", "head"}
# attributes whose removal never makes a smaller *repro*: removing them only changes how the file declares itself
STRUCTURAL_ATTRS = {"profile", "version", "{http://www.w3.org/2001/XMLSchema-instance}noNamespaceSchemaLocation"}


@dataclass
class ShrinkResult:
    xml: str
    checks: int = 0                 # keep() calls
    valid_checks: int = 0
    seconds: float = 0.0
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)   # what was removed, in order
    exhausted: bool = False         # stopped by the budget, not because nothing more could go

    @property
    def ratio(self) -> float:
        b = self.before.get("bytes") or 1
        return round(self.after.get("bytes", b) / b, 3)


def _parse(xml: str):
    return etree.fromstring(xml.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=True))


def _ser(root) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + etree.tostring(root, encoding="unicode", pretty_print=True)


def _size(root) -> dict:
    return {"bytes": len(_ser(root).encode("utf-8")),
            "nodes": sum(1 for e in root.iter() if isinstance(e.tag, str) and e.tag not in KEEP_ALWAYS and e.tag != "meta")}


def _path(root, el) -> list[int]:
    p = []
    while el is not root:
        par = el.getparent()
        p.append(par.index(el))
        el = par
    return p[::-1]


def _at(root, path):
    el = root
    for i in path:
        el = el[i]
    return el


def _label(el) -> str:
    d = el.get("DEF") or el.get("USE")
    return "%s%s" % (el.tag, " %s=%s" % ("DEF" if el.get("DEF") else "USE", d) if d else "")


def _attached(root, el) -> bool:
    while el is not None:
        if el is root:
            return True
        el = el.getparent()
    return False


def _candidates(root):
    """Edits on live elements, biggest first: whole nodes (shallow before deep, larger subtrees first), then wrappers
    replaced by their children (a Group or Transform that only carries the culprit), then attributes, then the tail half
    of long list values, and last the head's meta lines (they describe the scene before it was cut down)."""
    nodes = [e for e in root.iter() if isinstance(e.tag, str) and e.tag not in KEEP_ALWAYS and e is not root and e.tag != "meta"]
    nodes.sort(key=lambda e: (len(_path(root, e)), -sum(1 for _ in e.iter())))
    out = [("node", e, None, "remove %s" % _label(e)) for e in nodes]
    out += [("hoist", e, None, "unwrap %s" % _label(e)) for e in nodes if any(isinstance(c.tag, str) for c in e)]
    for e in nodes:
        out += [("attr", e, a, "drop %s@%s" % (e.tag, a)) for a in e.attrib
                if a not in STRUCTURAL_ATTRS and a not in ("DEF", "USE", "containerField")]
    for e in nodes:
        out += [("half", e, a, "halve %s@%s (%d values)" % (e.tag, a, len(v.split()))) for a, v in e.attrib.items() if len(v.split()) >= 8]
    out += [("node", e, None, "remove meta %s" % (e.get("name") or "")) for e in root.iter("meta")]
    return out


def _halved(value: str) -> str | None:
    parts = value.split()
    keep = len(parts) // 2
    for arity in (3, 4, 2):                       # keep whole tuples (vectors, rotations, colours with alpha, 2D points)
        if len(parts) % arity == 0:
            keep -= keep % arity
            break
    return " ".join(parts[:keep]) if keep > 0 else None


def _mutate(el, kind, attr) -> bool:
    if kind == "node":
        par = el.getparent()
        if par is None:
            return False
        par.remove(el)
    elif kind == "hoist":
        par = el.getparent()
        kids = [c for c in el if isinstance(c.tag, str)]
        if par is None or not kids:
            return False
        i = par.index(el)
        for k, c in enumerate(kids):
            par.insert(i + k, c)                  # lxml moves c out of el
        par.remove(el)
    elif kind == "attr":
        if attr not in el.attrib:
            return False
        del el.attrib[attr]
    else:
        v = _halved(el.get(attr, ""))
        if v is None:
            return False
        el.set(attr, v)
    return True


def _trial(root, el, kind, attr):
    """The document with this one edit applied, on a copy; None if the edit no longer applies."""
    if not _attached(root, el):
        return None
    path = _path(root, el)
    copy = etree.fromstring(etree.tostring(root))
    target = copy
    for i in path:
        target = target[i]
    return copy if _mutate(target, kind, attr) else None


def shrink(xml: str, keep: Callable[[str], bool], valid: Callable[[str], bool] | None = None, budget: int = 80,
           time_budget: float = 600.0) -> ShrinkResult:
    """Smallest scene for which keep() stays true (and valid() too, when given). keep(xml) should be true at the start."""
    t0 = time.time()
    root = _parse(xml)
    res = ShrinkResult(_ser(root), before=_size(root))
    progress = True
    while progress and not res.exhausted:
        progress = False
        for kind, el, attr, label in _candidates(root):
            if res.checks >= budget or time.time() - t0 > time_budget:
                res.exhausted = True
                break
            cand = _trial(root, el, kind, attr)
            if cand is None:
                continue
            text = _ser(cand)
            if valid is not None:
                res.valid_checks += 1
                try:
                    if not valid(text):
                        continue
                except Exception:
                    continue
            res.checks += 1
            try:
                ok = keep(text)
            except Exception:
                ok = False
            if ok:
                _mutate(el, kind, attr)          # same edit on the live tree: the other candidates stay valid
                res.steps.append(label)
                progress = True
    res.xml = _ser(root)
    res.after = _size(root)
    res.seconds = round(time.time() - t0, 2)
    return res
