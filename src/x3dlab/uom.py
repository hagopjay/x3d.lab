"""The X3D Unified Object Model (X3DUOM) as a small grammar: which nodes exist, their fields with types,
defaults and ranges, which node types each SFNode/MFNode field accepts, and default containerFields.

The X3DUOM XML is public Web3D Consortium data; it ships with the x3d_mcp checkout the lab already has.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


@dataclass
class FieldSpec:
    name: str
    type: str
    access: str
    default: str | None = None
    min: float | None = None
    max: float | None = None
    accepts: str | None = None            # for SFNode / MFNode: an abstract or concrete node type
    enum: list = field(default_factory=list)
    min_x: bool = False                   # the bound is exclusive (minExclusive / maxExclusive)
    max_x: bool = False

    @property
    def settable(self) -> bool:
        """Can appear as an XML attribute (inputOnly / outputOnly events cannot)."""
        return self.access in ("initializeOnly", "inputOutput")


@dataclass
class NodeSpec:
    name: str
    component: str
    level: int
    fields: dict
    container: str | None
    ancestors: set


SKIP_FIELDS = {"DEF", "USE", "class", "id", "style", "IS", "metadata", "containerField"}
TUPLE = {"SFVec2f": 2, "SFVec2d": 2, "SFVec3f": 3, "SFVec3d": 3, "SFColor": 3, "SFColorRGBA": 4, "SFRotation": 4,
         "SFVec4f": 4, "SFVec4d": 4, "SFMatrix3f": 9, "SFMatrix4f": 16, "SFMatrix3d": 9, "SFMatrix4d": 16}


class UOM:
    def __init__(self, path: str | Path):
        root = ET.parse(str(path)).getroot()
        parents: dict[str, list[str]] = {}
        for tag in ("AbstractObjectType", "AbstractNodeType", "ConcreteNode"):
            for n in root.iter(tag):
                idef = n.find("InterfaceDefinition")
                bases = []
                if idef is not None:
                    for t in ("Inheritance", "AdditionalInheritance"):
                        for inh in idef.findall(t):
                            if inh.get("baseType"):
                                bases.append(inh.get("baseType"))
                parents[n.get("name")] = bases
        self.nodes: dict[str, NodeSpec] = {}
        for n in root.iter("ConcreteNode"):
            idef = n.find("InterfaceDefinition")
            if idef is None:
                continue
            ci = idef.find("componentInfo")
            fields = {}
            for f in idef.findall("field"):
                if f.get("name") in SKIP_FIELDS:
                    continue
                enum = [e.get("value") for e in f.findall("enumeration") if e.get("value") is not None]
                lo, hi = _num(f.get("minInclusive")), _num(f.get("maxInclusive"))
                lox, hix = _num(f.get("minExclusive")), _num(f.get("maxExclusive"))
                fields[f.get("name")] = FieldSpec(f.get("name"), f.get("type", ""), f.get("accessType", ""), f.get("default"),
                                                  lo if lo is not None else lox, hi if hi is not None else hix,
                                                  f.get("acceptableNodeTypes"), enum, lo is None and lox is not None,
                                                  hi is None and hix is not None)
            cf = n.find("containerField")
            self.nodes[n.get("name")] = NodeSpec(n.get("name"), ci.get("name") if ci is not None else "", int(ci.get("level", 1)) if ci is not None else 1,
                                                 fields, cf.get("default") if cf is not None else None, _ancestors(n.get("name"), parents))
        self._accept_cache: dict[str, list[str]] = {}

    def accepts(self, accepts: str | None) -> list[str]:
        """Concrete node names acceptable for an SFNode/MFNode field (accepts may list several types with '|')."""
        if not accepts:
            return []
        if accepts not in self._accept_cache:
            want = set(accepts.replace("|", " ").split())
            self._accept_cache[accepts] = sorted(n for n, s in self.nodes.items() if s.ancestors & want or n in want)
        return self._accept_cache[accepts]


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _ancestors(name: str, parents: dict) -> set:
    out, todo = {name}, list(parents.get(name, []))
    while todo:
        p = todo.pop()
        if p not in out:
            out.add(p)
            todo.extend(parents.get(p, []))
    return out


def find_uom_file(version: str = "4.0") -> Path:
    """The X3DUOM shipped with the lab's x3d_mcp checkout."""
    from . import install
    src = install.x3d_mcp()
    p = Path(src) / "validation" / "schemas" / ("X3dUnifiedObjectModel-%s.xml" % version)
    if not p.exists():
        raise FileNotFoundError("X3DUOM not found at %s" % p)
    return p


def profile_nodes(profile: str = "Interchange", version: str = "4.0") -> set:
    """Element names a profile allows, from the X3D-Edit profile file next to the X3DUOM (empty if not found)."""
    p = find_uom_file(version).parent / ("x3d-%s.profile.xml" % version)
    if not p.exists():                                       # x3d_mcp ships the 4.0 file only; 4.1 kept the profiles
        p = find_uom_file("4.0").parent / "x3d-4.0.profile.xml"
    if not p.exists():
        return set()
    # the file pads some names ('ImmersiveProfileNodesLabel  "...'): any whitespace before the value
    m = re.search(r'<!ENTITY\s+%sProfileNodesLabel\s+"([^"]*)"' % re.escape(profile), p.read_text(encoding="utf-8", errors="replace"))
    return set(m.group(1).split()) if m else set()


@lru_cache(maxsize=2)
def load(version: str = "4.0") -> UOM:
    return UOM(find_uom_file(version))
