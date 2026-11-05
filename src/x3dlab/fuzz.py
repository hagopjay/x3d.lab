"""A grammar-aware differential fuzzer for X3D validators and viewers.

    cfg = fuzz.FuzzConfig(n=200, seed=7)
    oracles = [fuzz.MCPOracle(lab.client, "fork"), fuzz.MCPOracle(upstream, "consortium"),
               fuzz.XSDOracle(), fuzz.XITEOracle(hb)]
    fr = fuzz.run(cfg, oracles)                  # FuzzRun: cases, outcomes, buckets (minimised), matrices
    fr.write(lab.out_dir / "fuzz")               # fuzz_card.json, report.html, repro/*.x3d

How it works
  generate   random scenes from the X3DUOM: real node names, typed field values inside the UOM's ranges and enums,
             nodes placed only where a parent field accepts them, valid DEF/USE and ROUTEs. Expected: accept.
  mutate     27 operators. 20 break one rule on purpose (expected: reject); 7 are metamorphic rewrites that keep the
             meaning (reordered attributes, comments, explicit defaults, number spellings...; expected: accept).
  oracles    every case goes to every oracle: x3d_mcp (fork and consortium), lxml against the X3D 4.0 XSD, X_ITE in
             headless Chromium, optionally the Copilot validator and the Blender importer.
  findings   missed (a broken document accepted), false alarm (a good document rejected), crash/timeout,
             grouped into buckets by (kind, oracle, operator, signature), each bucket minimised with hierarchical
             delta debugging (whole subtrees first, then attributes) into a small reproducer.
"""

from __future__ import annotations

import asyncio
import copy
import html as H
import json
import math
import random
import re
import shutil
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import uom as uom_mod
from .events import bus

XSI = "http://www.w3.org/2001/XMLSchema-instance"
SAFE_GEOMETRY = ["Box", "Sphere", "Cone", "Cylinder", "Rectangle2D", "Disk2D", "Circle2D", "Arc2D", "ArcClose2D"]
LIGHTS = ["DirectionalLight", "PointLight", "SpotLight"]
VIEWERS = ("x_ite", "blender")
# Hints whose legal values the UOM does not describe (bboxSize: >= 0, or the "-1 -1 -1" sentinel): never generated.
NO_GENERATE = {"bboxSize", "bboxCenter"}
# Bounds 19775-1 states that the X3DUOM (and so the XSD) does not carry: values must be > 0.
SPEC_POSITIVE = {"Box": ("size",), "Sphere": ("radius",), "Cone": ("bottomRadius", "height"),
                 "Cylinder": ("radius", "height"), "TimeSensor": ("cycleInterval",)}


# ====================================================================== the case
@dataclass
class Case:
    id: str
    xml: str
    op: str                     # mutation operator, or "clean" / "seed:<name>"
    expect: str                 # accept | reject
    note: str = ""
    target: str = ""            # node type the operator touched (the signature of a "missed" finding)
    site: list | None = None    # child-index path of the touched element (protected while minimising)
    attr: str | None = None     # attribute the operator touched (protected while minimising)


@dataclass
class Outcome:
    oracle: str
    verdict: str                # accept | reject | crash | skip
    sig: str = ""               # normalised first complaint
    ms: float = 0.0
    detail: str = ""
    key: str = ""               # finer than sig (keeps element and attribute names): what the minimiser must preserve


def fine(msg: str, n: int = 220) -> str:
    """Like norm() but keeps quoted names: Element 'Material', attribute 'diffuseColor': [facet 'pattern'] ..."""
    s = re.sub(r"(?i)\bline \d+(, column \d+)?:?", "", msg or "")
    s = re.sub(r"(?i)(the value|value) '[^']*'", r"\1 '…'", s)
    s = re.sub(r"=\s*'[^']*'|=\s*\"[^\"]*\"", "='…'", s)
    s = re.sub(r"pattern '[^']*'", "pattern '…'", s)
    s = re.sub(r"[-+]?\d+(\.\d+)?([eE][-+]?\d+)?", "N", s)
    return re.sub(r"\s+", " ", s).strip(" .:-")[:n]


def norm(msg: str, n: int = 90) -> str:
    """A complaint with the specifics removed, so the same bug in two places lands in one bucket."""
    s = re.sub(r"(?i)\bline \d+(, column \d+)?:?", "", msg or "")
    s = re.sub(r"'[^']{0,80}'|\"[^\"]{0,80}\"|‘[^’]{0,80}’", "'…'", s)
    s = re.sub(r"[-+]?\d+(\.\d+)?([eE][-+]?\d+)?", "N", s)
    s = re.sub(r"\{[^}]*\}", "", s)
    return re.sub(r"\s+", " ", s).strip(" .:-")[:n]


# ====================================================================== typed values from the UOM
def _rnum(rnd, f: uom_mod.FieldSpec, integer: bool = False, default_span: float = 10.0):
    lo = f.min if f.min is not None else (-default_span if f.max is None or f.max > 0 else f.max - default_span)
    hi = f.max if f.max is not None else (lo + 2 * default_span if f.min is not None else default_span)
    if hi - lo > 1e6:
        hi = lo + 2 * default_span if f.min is not None else hi
        lo = hi - 2 * default_span if f.min is None else lo
    eps = (hi - lo) * 1e-3 or 1e-3
    lo, hi = lo + (eps if f.min_x else 0), hi - (eps if f.max_x else 0)
    if integer:
        return str(rnd.randint(int(math.ceil(lo)), int(math.floor(hi))) if hi >= lo else int(lo))
    return _fmt(rnd.uniform(lo, hi))


def _fmt(x: float) -> str:
    s = ("%.4f" % x).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def value_for(rnd: random.Random, f: uom_mod.FieldSpec) -> str | None:
    """A random value of the field's type inside its UOM range or enumeration; None if we don't generate this type."""
    t = f.type
    if f.enum and t in ("SFString", "MFString"):
        return rnd.choice(f.enum).strip('"') if t == "SFString" else rnd.choice(f.enum)
    if t == "SFBool":
        return rnd.choice(["true", "false"])
    if t == "SFInt32":
        return _rnum(rnd, f, integer=True)
    if t in ("SFFloat", "SFDouble", "SFTime"):
        return _rnum(rnd, f)
    if t in ("SFColor", "SFColorRGBA"):
        return " ".join(_fmt(rnd.random()) for _ in range(uom_mod.TUPLE[t]))
    if t == "SFRotation":
        v = [rnd.gauss(0, 1) for _ in range(3)]
        n = math.sqrt(sum(c * c for c in v)) or 1.0
        return " ".join(_fmt(c / n) for c in v) + " " + _fmt(rnd.uniform(-math.pi, math.pi))
    if t in ("SFVec2f", "SFVec2d", "SFVec3f", "SFVec3d", "SFVec4f", "SFVec4d"):
        return " ".join(_rnum(rnd, f) for _ in range(uom_mod.TUPLE[t]))
    return None


# ====================================================================== the generator
class Gen:
    """Random scenes from the X3DUOM. Everything it writes is allowed by the UOM, so the expectation is 'accept'."""

    def __init__(self, u: uom_mod.UOM, rnd: random.Random, wild: float = 0.25, field_p: float = 0.5):
        self.u, self.rnd, self.wild, self.field_p = u, rnd, wild, field_p
        self.n = 0
        geo = set(u.accepts("X3DGeometryNode"))
        self.geometry = [g for g in SAFE_GEOMETRY if g in geo]
        kids = u.accepts(u.nodes["Transform"].fields["children"].accepts)
        # wild children: anything the grammar allows under Transform whose required content we can omit
        self.wild_children = [k for k in kids if not any(f.type in ("SFNode", "MFNode") for f in u.nodes[k].fields.values()
                                                         if f.name not in ("children", "metadata"))
                              and u.nodes[k].component not in ("Scripting", "Networking", "Sound")]

    def _def(self, prefix: str) -> str:
        self.n += 1
        return "%s%d" % (prefix, self.n)

    def node(self, name: str, fields_p: float | None = None, **attrs) -> ET.Element:
        e = ET.Element(name)
        spec = self.u.nodes.get(name)
        p = self.field_p if fields_p is None else fields_p
        if spec:
            for f in spec.fields.values():
                if f.name in NO_GENERATE:
                    continue
                if f.settable and self.rnd.random() < p:
                    v = value_for(self.rnd, f)
                    if v is not None:
                        e.set(f.name, v)
        for k in SPEC_POSITIVE.get(name, ()):
            if k in e.attrib:
                e.set(k, " ".join(_fmt(self.rnd.uniform(0.2, 4)) for _ in e.get(k).split()))
        for k, v in attrs.items():
            e.set(k, v)
        return e

    def shape(self) -> ET.Element:
        s = ET.Element("Shape", DEF=self._def("S"))
        app = ET.SubElement(s, "Appearance")
        app.append(self.node("Material", DEF=self._def("M")))
        if self.rnd.random() < 0.2:
            s.append(self.ifs())
        else:
            s.append(self.node(self.rnd.choice(self.geometry)))
        return s

    def ifs(self) -> ET.Element:
        n = self.rnd.randint(3, 8)
        pts = " ".join("%s %s %s" % (_fmt(math.cos(2 * math.pi * i / n)), _fmt(math.sin(2 * math.pi * i / n)), _fmt(self.rnd.uniform(-0.2, 0.2)))
                       for i in range(n))
        e = ET.Element("IndexedFaceSet", coordIndex=" ".join(str(i) for i in range(n)) + " -1", solid="false")
        ET.SubElement(e, "Coordinate", point=pts)
        return e

    def group(self, depth: int) -> ET.Element:
        g = self.node(self.rnd.choice(["Transform", "Transform", "Group"]), fields_p=0.6, DEF=self._def("T"))
        if g.tag == "Transform" and "scale" in g.attrib:
            g.set("scale", " ".join(_fmt(self.rnd.uniform(0.2, 3)) for _ in range(3)))
        for _ in range(self.rnd.randint(1, 3)):
            r = self.rnd.random()
            if depth > 0 and r < 0.3:
                g.append(self.group(depth - 1))
            elif r < 0.85 or not self.wild_children:
                g.append(self.shape())
            elif self.rnd.random() < self.wild:
                g.append(self.node(self.rnd.choice(self.wild_children)))
            else:
                g.append(self.node(self.rnd.choice(LIGHTS)))
        return g

    def scene(self, depth: int = 3, groups: int = 3, animate: bool | None = None) -> ET.Element:
        root = ET.Element("X3D", profile="Full", version="4.0")
        head = ET.SubElement(root, "head")
        ET.SubElement(head, "meta", name="generator", content="x3d.lab fuzz")
        sc = ET.SubElement(root, "Scene")
        ET.SubElement(sc, "Viewpoint", description="fuzz", position="0 2 14", orientation="1 0 0 -0.15")
        sc.append(self.node("NavigationInfo", fields_p=0.3))
        if self.rnd.random() < 0.5:
            sc.append(self.node("DirectionalLight"))
        for _ in range(self.rnd.randint(1, groups)):
            sc.append(self.group(self.rnd.randint(0, depth)))
        if animate if animate is not None else self.rnd.random() < 0.5:
            tr = next((e for e in sc.iter("Transform") if e.get("DEF")), None)
            if tr is not None:
                ts, pi = self._def("Clock"), self._def("Mover")
                sc.append(ET.Element("TimeSensor", DEF=ts, cycleInterval=_fmt(self.rnd.uniform(1, 8)), loop="true"))
                sc.append(ET.Element("PositionInterpolator", DEF=pi, key="0 0.5 1", keyValue="0 0 0 0 1 0 0 0 0"))
                sc.append(ET.Element("ROUTE", fromNode=ts, fromField="fraction_changed", toNode=pi, toField="set_fraction"))
                sc.append(ET.Element("ROUTE", fromNode=pi, fromField="value_changed", toNode=tr.get("DEF"), toField="set_translation"))
        if self.rnd.random() < 0.4:                    # a USE of an existing Shape, after its DEF in document order
            order = {e: i for i, e in enumerate(sc.iter())}
            pm = _parent_map(sc)
            shapes = [e for e in sc.iter("Shape") if e.get("DEF")]
            if shapes:
                s = self.rnd.choice(shapes)
                anc, a = set(), s
                while a in pm:
                    a = pm[a]
                    anc.add(a)
                groups_ = [e for e in sc.iter() if e.tag in ("Transform", "Group") and (order[e] > order[s] or e in anc)]
                if groups_:
                    self.rnd.choice(groups_).append(ET.Element("Shape", USE=s.get("DEF")))
        return root


# ====================================================================== serialisation
def to_xml(root: ET.Element) -> str:
    attrs = "".join(' %s="%s"' % (k, H.escape(v, quote=True)) for k, v in root.attrib.items() if not k.startswith("{"))
    inner = "".join(ET.tostring(c, encoding="unicode") for c in root)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<X3D%s xmlns:xsd="%s" xsd:noNamespaceSchemaLocation='
            '"https://www.web3d.org/specifications/x3d-%s.xsd">%s</X3D>\n') % (attrs, XSI, root.get("version", "4.0"), inner)


def parse(xml: str) -> ET.Element:
    xml = re.sub(r"<!DOCTYPE[^>]*>", "", xml, count=1)
    return ET.fromstring(xml.encode("utf-8") if xml.lstrip().startswith("<?xml") else xml)


def path_of(root: ET.Element, target: ET.Element) -> list | None:
    def walk(e, p):
        if e is target:
            return p
        for i, c in enumerate(list(e)):
            r = walk(c, p + [i])
            if r is not None:
                return r
        return None
    return walk(root, [])


def at_path(root: ET.Element, path: list) -> ET.Element | None:
    e = root
    for i in path:
        kids = list(e)
        if i >= len(kids):
            return None
        e = kids[i]
    return e


def _scene(root):
    return root.find("Scene")


def _parent_map(root):
    return {c: p for p in root.iter() for c in p}


# ====================================================================== mutation operators
@dataclass
class Mut:
    note: str
    target: ET.Element | None = None
    attr: str | None = None


class Mutators:
    """Each operator edits the tree in place and returns a Mut, or None when the scene offers it nothing to work on."""

    REJECT = ["range", "spec_range", "enum", "arity", "not_a_number", "nan_inf", "unknown_attr", "unknown_node", "wrong_child",
              "wrong_container", "dup_def", "dangling_use", "use_with_fields", "route_bad_field", "route_type",
              "event_attr", "profile", "bad_version", "ifs_index", "truncate"]
    ACCEPT = ["reorder_attrs", "comments", "explicit_defaults", "number_spelling", "wrap_group", "rename_defs", "whitespace"]
    LAYER = {"range": "semantic", "spec_range": "semantic", "enum": "xsd", "arity": "xsd", "not_a_number": "xsd", "nan_inf": "xsd", "unknown_attr": "xsd",
             "unknown_node": "xsd", "wrong_child": "xsd", "wrong_container": "semantic", "dup_def": "xsd", "dangling_use": "xsd",
             "use_with_fields": "semantic", "route_bad_field": "semantic", "route_type": "semantic", "event_attr": "xsd",
             "profile": "semantic", "bad_version": "xsd", "ifs_index": "semantic", "truncate": "xml"}
    TEXT = {"truncate", "whitespace"}           # operate on the serialised text, not the tree

    def __init__(self, u: uom_mod.UOM, rnd: random.Random):
        self.u, self.rnd = u, rnd

    def _known(self, root, pred=lambda e, s: True):
        return [e for e in root.iter() if e.tag in self.u.nodes and not e.get("USE") and pred(e, self.u.nodes[e.tag])]

    def _pick_field(self, root, fpred):
        cands = [(e, f) for e in self._known(root) for f in self.u.nodes[e.tag].fields.values() if f.settable and fpred(f)]
        return self.rnd.choice(cands) if cands else (None, None)

    # ---------------- break one rule
    def range(self, root):
        e, f = self._pick_field(root, lambda f: (f.min is not None or f.max is not None) and f.type in ("SFFloat", "SFDouble", "SFInt32", "SFTime"))
        if e is None:
            return None
        if f.max is not None and (f.min is None or self.rnd.random() < 0.5):
            v = f.max if f.max_x else f.max + max(1, abs(f.max))
        else:
            v = f.min if f.min_x else f.min - max(1, abs(f.min))
        e.set(f.name, str(int(v)) if f.type == "SFInt32" else _fmt(v))
        return Mut("%s.%s=%s outside [%s, %s]" % (e.tag, f.name, e.get(f.name), f.min, f.max), e, f.name)

    def spec_range(self, root):
        """A bound from 19775-1 that the X3DUOM and XSD do not encode (Box size, radii, heights > 0)."""
        cands = [(e, k) for e in root.iter() if e.tag in SPEC_POSITIVE and not e.get("USE") for k in SPEC_POSITIVE[e.tag]]
        if not cands:
            return None
        e, k = self.rnd.choice(cands)
        n = 3 if k == "size" else 1
        bad = self.rnd.choice(["0", "-1", "-0.5"])
        e.set(k, " ".join(["1"] * (n - 1) + [bad]))
        return Mut("%s.%s=%s (19775-1: must be > 0; not in the UOM)" % (e.tag, k, e.get(k)), e, k)

    def enum(self, root):
        e, f = self._pick_field(root, lambda f: bool(f.enum) and f.type == "SFString")
        if e is None:
            return None
        e.set(f.name, "NOT_AN_OPTION")
        return Mut("%s.%s is not one of %d allowed values" % (e.tag, f.name, len(f.enum)), e, f.name)

    def arity(self, root):
        e, f = self._pick_field(root, lambda f: f.type in uom_mod.TUPLE and uom_mod.TUPLE[f.type] <= 4)
        if e is None:
            return None
        k = uom_mod.TUPLE[f.type] - 1
        e.set(f.name, " ".join(["0.5"] * k))
        return Mut("%s.%s (%s) given %d numbers" % (e.tag, f.name, f.type, k), e, f.name)

    def not_a_number(self, root):
        e, f = self._pick_field(root, lambda f: f.type in ("SFFloat", "SFInt32", "SFVec3f", "SFColor", "SFRotation"))
        if e is None:
            return None
        e.set(f.name, "big")
        return Mut("%s.%s='big'" % (e.tag, f.name), e, f.name)

    def nan_inf(self, root):
        e, f = self._pick_field(root, lambda f: f.type in ("SFFloat", "SFVec3f", "SFColor"))
        if e is None:
            return None
        bad = self.rnd.choice(["NaN", "INF", "-INF"])
        n = uom_mod.TUPLE.get(f.type, 1)
        e.set(f.name, " ".join([bad] + ["0"] * (n - 1)))
        return Mut("%s.%s contains %s" % (e.tag, f.name, bad), e, f.name)

    def unknown_attr(self, root):
        es = self._known(root)
        if not es:
            return None
        e = self.rnd.choice(es)
        name = self.rnd.choice(["colour", "radiusX", "visibile", "onClick", "transparancy"])
        e.set(name, "1")
        return Mut("%s has unknown attribute %s" % (e.tag, name), e, name)

    def unknown_node(self, root):
        gs = [e for e in root.iter() if e.tag in ("Transform", "Group") and not e.get("USE")]
        if not gs:
            return None
        g = self.rnd.choice(gs)
        bad = ET.SubElement(g, self.rnd.choice(["Cube", "Mesh", "Light", "Transfrom", "Spere"]))
        return Mut("%s inside %s is not an X3D node" % (bad.tag, g.tag), bad)

    def wrong_child(self, root):
        gs = [e for e in root.iter() if e.tag in ("Transform", "Group") and not e.get("USE")]
        if not gs:
            return None
        g = self.rnd.choice(gs)
        bad = ET.SubElement(g, self.rnd.choice(["Material", "Box", "Coordinate", "Appearance"]))
        return Mut("%s placed directly in %s children" % (bad.tag, g.tag), bad)

    def wrong_container(self, root):
        ms = [e for e in root.iter("Material")] + [e for e in root.iter() if e.tag in SAFE_GEOMETRY]
        if not ms:
            return None
        e = self.rnd.choice(ms)
        e.set("containerField", "children" if e.tag == "Material" else "appearance")
        return Mut("%s containerField=%s" % (e.tag, e.get("containerField")), e, "containerField")

    def dup_def(self, root):
        ds = [e for e in root.iter() if e.get("DEF")]
        others = [e for e in self._known(root) if not e.get("DEF")]
        if len(ds) < 1 or not others:
            return None
        src, dst = self.rnd.choice(ds), self.rnd.choice(others)
        dst.set("DEF", src.get("DEF"))
        return Mut("DEF %s used twice (%s and %s)" % (src.get("DEF"), src.tag, dst.tag), dst, "DEF")

    def dangling_use(self, root):
        gs = [e for e in root.iter() if e.tag in ("Transform", "Group") and not e.get("USE")]
        if not gs:
            return None
        bad = ET.SubElement(self.rnd.choice(gs), "Shape", USE="NoSuchNode")
        return Mut("USE of an undefined name", bad, "USE")

    def use_with_fields(self, root):
        uses = [e for e in root.iter() if e.get("USE")]
        if not uses:
            shapes = [e for e in root.iter("Shape") if e.get("DEF")]
            gs = [e for e in root.iter() if e.tag in ("Transform", "Group") and not e.get("USE")]
            if not shapes or not gs:
                return None
            uses = [ET.SubElement(self.rnd.choice(gs), "Shape", USE=self.rnd.choice(shapes).get("DEF"))]
        e = self.rnd.choice(uses)
        e.set("bboxSize", "1 1 1")
        return Mut("USE node %s also sets bboxSize" % e.get("USE"), e, "bboxSize")

    def _route_pair(self, root):
        tr = [e for e in root.iter("Transform") if e.get("DEF")]
        if not tr:
            return None, None
        sc = _scene(root)
        ts = next((e for e in sc.iter("TimeSensor") if e.get("DEF")), None)
        if ts is None:
            ts = ET.SubElement(sc, "TimeSensor", DEF="FuzzClock", loop="true")
        return ts, self.rnd.choice(tr)

    def route_bad_field(self, root):
        ts, tr = self._route_pair(root)
        if ts is None:
            return None
        r = ET.SubElement(_scene(root), "ROUTE", fromNode=ts.get("DEF"), fromField="fraction_changed", toNode=tr.get("DEF"),
                          toField=self.rnd.choice(["set_position", "translation_changed_", "set_colour"]))
        return Mut("ROUTE to field %s.%s that does not exist" % (tr.tag, r.get("toField")), r, "toField")

    def route_type(self, root):
        ts, tr = self._route_pair(root)
        if ts is None:
            return None
        r = ET.SubElement(_scene(root), "ROUTE", fromNode=ts.get("DEF"), fromField="fraction_changed", toNode=tr.get("DEF"),
                          toField=self.rnd.choice(["set_translation", "set_rotation", "set_scale"]))
        return Mut("ROUTE SFFloat fraction_changed into %s.%s" % (tr.tag, r.get("toField")), r, "toField")

    def event_attr(self, root):
        cands = [(e, f) for e in self._known(root) for f in self.u.nodes[e.tag].fields.values()
                 if f.access in ("outputOnly", "inputOnly") and value_for(self.rnd, f) is not None]
        if not cands:
            return None
        e, f = self.rnd.choice(cands)
        e.set(f.name, value_for(self.rnd, f))
        return Mut("%s.%s is %s and cannot be an attribute" % (e.tag, f.name, f.access), e, f.name)

    def profile(self, root):
        allowed = uom_mod.profile_nodes("Interchange") if hasattr(uom_mod, "profile_nodes") else set()
        if not allowed:
            return None
        outside = ["Fog", "PointLight", "SpotLight", "Sound", "ProximitySensor", "Billboard", "Text", "Switch"]
        gs = [e for e in root.iter() if e.tag in ("Transform", "Group") and not e.get("USE")]
        if not gs:
            return None
        root.set("profile", "Interchange")
        name = self.rnd.choice([o for o in outside if o not in allowed])
        bad = ET.SubElement(self.rnd.choice(gs), name)
        return Mut("profile Interchange but uses %s" % name, bad)

    def bad_version(self, root):
        root.set("version", self.rnd.choice(["4.7", "5.0", "2.0"]))
        return Mut("X3D version=%s" % root.get("version"), root, "version")

    def ifs_index(self, root):
        ifs = [e for e in root.iter("IndexedFaceSet") if e.find("Coordinate") is not None]
        if not ifs:
            return None
        e = self.rnd.choice(ifs)
        e.set("coordIndex", e.get("coordIndex", "0 1 2 -1").replace("-1", "999 -1", 1))
        return Mut("IndexedFaceSet.coordIndex points past its Coordinate", e, "coordIndex")

    # ---------------- keep the meaning (metamorphic)
    def reorder_attrs(self, root):
        n = 0
        for e in root.iter():
            if len(e.attrib) > 1:
                items = list(e.attrib.items())
                self.rnd.shuffle(items)
                e.attrib.clear()
                e.attrib.update(items)
                n += 1
        return Mut("attributes shuffled on %d elements" % n) if n else None

    def comments(self, root):
        gs = [e for e in root.iter() if e.tag in ("Scene", "Transform", "Group", "Shape", "Appearance")]
        if not gs:
            return None
        for g in self.rnd.sample(gs, min(4, len(gs))):
            g.insert(self.rnd.randint(0, len(g)), ET.Comment(" fuzz note: a & b < c > d - e "))
        return Mut("comments inserted")

    def explicit_defaults(self, root):
        n = 0
        for e in self._known(root):
            for f in self.u.nodes[e.tag].fields.values():
                if f.settable and f.name not in e.attrib and f.default not in (None, "") and f.type not in ("SFNode", "MFNode") \
                        and self.rnd.random() < 0.3:
                    e.set(f.name, f.default)
                    n += 1
        return Mut("%d fields set explicitly to their UOM default" % n) if n else None

    def number_spelling(self, root):
        n = 0
        for e in self._known(root):
            spec = self.u.nodes[e.tag]
            for k, v in list(e.attrib.items()):
                f = spec.fields.get(k)
                if f and f.type in ("SFFloat", "SFVec3f", "SFColor", "SFRotation", "SFDouble", "SFVec2f"):
                    e.set(k, " ".join(_respell(tok, self.rnd) for tok in v.split()))
                    n += 1
        return Mut("%d numbers re-spelled (1 -> 1.0, 0.5 -> 5e-1, +0.25)" % n) if n else None

    def wrap_group(self, root):
        pm = _parent_map(root)
        shapes = [e for e in root.iter("Shape") if e in pm and pm[e].tag in ("Transform", "Group", "Scene")]
        if not shapes:
            return None
        s = self.rnd.choice(shapes)
        p = pm[s]
        i = list(p).index(s)
        p.remove(s)
        g = ET.Element("Group")
        g.append(s)
        p.insert(i, g)
        return Mut("a Shape wrapped in an extra Group")

    def rename_defs(self, root):
        names = {e.get("DEF") for e in root.iter() if e.get("DEF")}
        if not names:
            return None
        m = {n: "Renamed_%s_%d" % (n, self.rnd.randint(10, 99)) for n in names}
        for e in root.iter():
            for a in ("DEF", "USE", "fromNode", "toNode"):
                if e.get(a) in m:
                    e.set(a, m[e.get(a)])
        return Mut("%d DEF names renamed consistently" % len(m))

    # ---------------- text level
    def text(self, op: str, xml: str) -> tuple[str, str] | None:
        if op == "truncate":
            cut = self.rnd.randint(len(xml) // 3, len(xml) - 20)
            return xml[:cut], "document cut at byte %d of %d" % (cut, len(xml))
        if op == "whitespace":
            return re.sub(r"(<[A-Za-z][^>]*?) ([A-Za-z]+=)", lambda m: m.group(1) + "\n\t  " + m.group(2), xml), "line breaks and tabs inside tags"
        return None


def _respell(tok: str, rnd) -> str:
    try:
        x = float(tok)
    except ValueError:
        return tok
    choice = rnd.randint(0, 3)
    if choice == 0 and x == int(x):
        return "%d.0" % x
    if choice == 1 and x != 0:
        return "%.6e" % x
    if choice == 2 and x > 0:
        return "+" + tok.lstrip("+")
    return tok


# ====================================================================== oracles
class Oracle:
    name = "oracle"
    batch = False                       # batch oracles see all cases at once (Blender)

    async def __call__(self, xml: str) -> Outcome:          # pragma: no cover - interface
        raise NotImplementedError


class MCPOracle(Oracle):
    """x3d_mcp: validate_x3d (XSD) + validate_semantic. Rejects when either reports an error."""

    def __init__(self, client, name: str = "fork"):
        self.client, self.name = client, name

    async def __call__(self, xml: str) -> Outcome:
        t0 = time.perf_counter()
        try:
            v = await self.client.verdict(xml)
        except Exception as exc:
            return Outcome(self.name, "crash", norm("%s: %s" % (type(exc).__name__, exc)), (time.perf_counter() - t0) * 1000, str(exc)[:300])
        ms = (time.perf_counter() - t0) * 1000
        if v.ok:
            return Outcome(self.name, "accept", "", ms)
        e = v.errors[0] if v.errors else None
        generic = (None, "", "xsd", "schema", "xml", "error")
        sig = "%s:%s" % (e.layer, norm(e.rule if e.rule not in generic else e.message, 60)) if e else "?"
        return Outcome(self.name, "reject", sig, ms, v.report(3), "%s:%s:%s" % (e.layer, e.rule, fine(e.message)) if e else "?")


class XSDOracle(Oracle):
    """lxml against the X3D 4.0 XSD shipped with x3d_mcp: an independent engine for the schema layer."""

    name = "lxml-xsd"

    def __init__(self, xsd: str | Path | None = None):
        from lxml import etree
        self._etree = etree
        xsd = xsd or (uom_mod.find_uom_file().parent / "x3d-4.0.xsd")
        with bus.span("load the X3D 4.0 XSD into lxml", src="fuzz"):
            self.schema = etree.XMLSchema(etree.parse(str(xsd)))

    async def __call__(self, xml: str) -> Outcome:
        t0 = time.perf_counter()
        try:
            doc = self._etree.fromstring(xml.encode("utf-8"), self._etree.XMLParser(resolve_entities=False, no_network=True))
        except self._etree.XMLSyntaxError as exc:
            return Outcome(self.name, "reject", "xml:" + norm(str(exc), 60), (time.perf_counter() - t0) * 1000, str(exc)[:300],
                           "xml:" + fine(str(exc)))
        ok = self.schema.validate(doc)
        ms = (time.perf_counter() - t0) * 1000
        if ok:
            return Outcome(self.name, "accept", "", ms)
        err = self.schema.error_log[0]
        msg = re.sub(r"^Element '[^']*'(, attribute '[^']*')?: ", "", err.message)
        return Outcome(self.name, "reject", "xsd:" + norm(msg, 60), ms, err.message[:300], "xsd:" + fine(err.message))


class XITEOracle(Oracle):
    """X_ITE in headless Chromium. Rejects when loading fails or X_ITE logs an error while loading."""

    name = "x_ite"

    def __init__(self, browser, strict: bool = True):
        self.b, self.strict = browser, strict

    async def __call__(self, xml: str) -> Outcome:
        try:
            r = await self.b.aload(xml, timeout=30)
        except Exception as exc:
            return Outcome(self.name, "crash", norm(str(exc)), 0, str(exc)[:300])
        if r.errors and any(e.startswith("timeout") for e in r.errors):
            return Outcome(self.name, "crash", "timeout", r.ms, r.errors[0])
        if r.ok and (not self.strict or not r.errors):
            return Outcome(self.name, "accept", "", r.ms)
        first = r.errors[0] if r.errors else "load failed"
        return Outcome(self.name, "reject", norm(first, 70), r.ms, "; ".join(r.errors[:3])[:300], fine(first))


class CopilotOracle(Oracle):
    """The private Copilot validator (window.__x3dcopilot.validator) in the same headless browser."""

    name = "copilot"

    def __init__(self, browser):
        self.b = browser

    async def __call__(self, xml: str) -> Outcome:
        t0 = time.perf_counter()
        try:
            r = await self.b.acopilot(xml)
        except Exception as exc:
            return Outcome(self.name, "crash", norm(str(exc)), (time.perf_counter() - t0) * 1000, str(exc)[:300])
        ms = (time.perf_counter() - t0) * 1000
        if r is None:
            return Outcome(self.name, "skip", "not loaded", ms)
        return Outcome(self.name, "accept" if r["ok"] else "reject", "" if r["ok"] else norm(r["errors"][0] if r["errors"] else "?", 70), ms,
                       "; ".join(r["errors"][:3])[:300])


class BlenderOracle(Oracle):
    """The x3d-blender-io importer: one Blender process imports every case (a batch oracle)."""

    name = "blender"
    batch = True

    def __init__(self, bl, workdir: Path):
        self.bl, self.dir = bl, Path(workdir)

    def run_batch(self, cases: list[Case]) -> dict:
        from . import blender as blender_mod
        self.dir.mkdir(parents=True, exist_ok=True)
        tasks = []
        for c in cases:
            p = self.dir / ("%s.x3d" % c.id)
            p.write_text(c.xml, encoding="utf-8")
            tasks.append({"id": c.id, "kind": "import", "src": str(p)})
        out = {}
        for r in self.bl.run(tasks, timeout=max(300, 6 * len(tasks)), label="fuzz import"):
            res = r.get("result") or {}
            if r["ok"]:
                out[r["id"]] = Outcome(self.name, "accept", "", (r.get("seconds") or 0) * 1000)
            else:
                why = res.get("error") or blender_mod.reason("\n".join(r.get("log") or [])) or "import failed"
                kind = "crash" if "exited with code" in why or "timed out" in why else "reject"
                out[r["id"]] = Outcome(self.name, kind, norm(why, 70), (r.get("seconds") or 0) * 1000, why[:300])
        return out


# ====================================================================== the run
@dataclass
class FuzzConfig:
    n: int = 120                    # cases
    seed: int = 7
    clean: float = 0.2              # share of unmutated generated scenes (false-alarm hunting)
    metamorphic: float = 0.25       # share of meaning-preserving rewrites
    ops: list = field(default_factory=lambda: Mutators.REJECT + Mutators.ACCEPT)
    wild: float = 0.25              # chance a group child is any grammar-allowed node, not a Shape or light
    depth: int = 3
    groups: int = 3
    seeds: dict = field(default_factory=dict)    # name -> xml, extra known-good starting scenes (scenegen, LANTERN...)
    concurrency: int = 4
    minimize: bool = True
    min_budget: int = 60            # oracle calls per bucket while minimising
    minimize_viewer_misses: bool = False   # X_ITE / Blender accept most broken files by design; skip those buckets
    time_budget_s: float = 900


@dataclass
class Bucket:
    key: str
    kind: str                       # missed | false_alarm | crash
    oracle: str
    op: str
    sig: str
    cases: list = field(default_factory=list)
    repro: str | None = None
    repro_bytes: int | None = None
    orig_bytes: int | None = None
    min_calls: int = 0


class FuzzRun:
    def __init__(self, cfg: FuzzConfig, oracles: list):
        self.cfg, self.oracles = cfg, oracles
        self.cases: list[Case] = []
        self.out: dict = {}                      # case id -> {oracle: Outcome}
        self.buckets: dict = {}
        self.started, self.ended = time.time(), None

    # ------------------------------------------------------------------ analysis
    def classify(self, c: Case, o: Outcome) -> str | None:
        if o.verdict == "crash":
            return "crash"
        if o.verdict == "skip":
            return None
        if c.expect == "reject" and o.verdict == "accept":
            return "missed"
        if c.expect == "accept" and o.verdict == "reject":
            return "false_alarm"
        return None

    def bucket_all(self) -> None:
        self.buckets = {}
        for c in self.cases:
            for name, o in self.out.get(c.id, {}).items():
                kind = self.classify(c, o)
                if not kind:
                    continue
                sig = c.target if kind == "missed" else o.sig
                key = "%s|%s|%s|%s" % (kind, name, c.op, sig)
                b = self.buckets.setdefault(key, Bucket(key, kind, name, c.op, sig))
                b.cases.append(c.id)

    def confusion(self) -> dict:
        """Per oracle: caught / missed broken documents, passed / false-alarmed good ones, crashes, mean ms."""
        out = {}
        for o in self.oracles:
            d = Counter()
            ms = []
            for c in self.cases:
                r = self.out.get(c.id, {}).get(o.name)
                if r is None or r.verdict == "skip":
                    continue
                ms.append(r.ms)
                if r.verdict == "crash":
                    d["crash"] += 1
                elif c.expect == "reject":
                    d["caught" if r.verdict == "reject" else "missed"] += 1
                else:
                    d["passed" if r.verdict == "accept" else "false_alarm"] += 1
            bad = d["caught"] + d["missed"]
            good = d["passed"] + d["false_alarm"]
            out[o.name] = dict(d, recall=round(d["caught"] / bad, 3) if bad else None,
                               specificity=round(d["passed"] / good, 3) if good else None,
                               mean_ms=round(sum(ms) / len(ms), 1) if ms else None)
        return out

    def by_operator(self) -> dict:
        """op -> oracle -> share of cases where the oracle did what was expected."""
        t = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for c in self.cases:
            for name, r in self.out.get(c.id, {}).items():
                if r.verdict in ("skip", "crash"):
                    continue
                cell = t[c.op][name]
                cell[1] += 1
                cell[0] += (r.verdict == c.expect)
        return {op: {n: round(a / b, 2) if b else None for n, (a, b) in row.items()} for op, row in sorted(t.items())}

    def agreement(self) -> dict:
        """Pairwise agreement between oracles on accept/reject (Cohen's kappa and raw share)."""
        names = [o.name for o in self.oracles]
        res = {}
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                pairs = [(self.out[c.id][a].verdict, self.out[c.id][b].verdict) for c in self.cases
                         if a in self.out.get(c.id, {}) and b in self.out.get(c.id, {})
                         and self.out[c.id][a].verdict in ("accept", "reject") and self.out[c.id][b].verdict in ("accept", "reject")]
                if not pairs:
                    continue
                n = len(pairs)
                po = sum(x == y for x, y in pairs) / n
                pa = sum(x == "accept" for x, _ in pairs) / n
                pb = sum(y == "accept" for _, y in pairs) / n
                pe = pa * pb + (1 - pa) * (1 - pb)
                kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
                res["%s~%s" % (a, b)] = {"n": n, "agree": round(po, 3), "kappa": round(kappa, 3)}
        return res

    def disagreements(self, limit: int = 30) -> list:
        rows = []
        for c in self.cases:
            vs = {n: o.verdict for n, o in self.out.get(c.id, {}).items() if o.verdict in ("accept", "reject")}
            if len(set(vs.values())) > 1:
                rows.append({"case": c.id, "op": c.op, "expect": c.expect, "note": c.note[:100], **vs})
        return rows[:limit]

    # ------------------------------------------------------------------ output
    def card(self) -> dict:
        return {"kind": "fuzz", "config": {k: v for k, v in asdict(self.cfg).items() if k != "seeds"},
                "seeds": sorted(self.cfg.seeds), "oracles": [o.name for o in self.oracles],
                "started": self.started, "ended": self.ended, "seconds": round((self.ended or time.time()) - self.started, 1),
                "cases": len(self.cases), "ops": dict(Counter(c.op for c in self.cases)),
                "confusion": self.confusion(), "agreement": self.agreement(), "by_operator": self.by_operator(),
                "buckets": [{k: v for k, v in asdict(b).items() if k != "cases"} | {"count": len(b.cases), "first": b.cases[0]}
                            for b in sorted(self.buckets.values(), key=lambda b: (b.kind != "crash", -len(b.cases)))],
                "disagreements": self.disagreements(),
                "expect": {c.id: c.expect for c in self.cases},
                "verdicts": {c.id: {n: o.verdict for n, o in self.out.get(c.id, {}).items()} for c in self.cases}}

    def write(self, d: str | Path) -> Path:
        d = Path(d)
        for sub in ("repro", "cases"):                     # a rerun into the same folder must not mix in old files
            if (d / sub).is_dir():
                shutil.rmtree(d / sub)
        (d / "repro").mkdir(parents=True, exist_ok=True)
        (d / "cases").mkdir(exist_ok=True)
        card = self.card()
        for c in self.cases:
            (d / "cases" / ("%s.x3d" % c.id)).write_text(c.xml, encoding="utf-8")
        for i, b in enumerate(sorted(self.buckets.values(), key=lambda b: -len(b.cases))):
            if b.repro:
                (d / "repro" / ("%02d_%s_%s_%s.x3d" % (i, b.kind, b.oracle, b.op))).write_text(b.repro, encoding="utf-8")
        (d / "fuzz_card.json").write_text(json.dumps(card, indent=1, default=str), encoding="utf-8")
        (d / "report.html").write_text(report_html(card, {b.key: b.repro for b in self.buckets.values()}), encoding="utf-8")
        return d / "fuzz_card.json"


def make_cases(cfg: FuzzConfig, u: uom_mod.UOM | None = None) -> list[Case]:
    """Deterministic for a given seed: the same config always yields the same documents."""
    u = u or uom_mod.load()
    rnd = random.Random(cfg.seed)
    gen = Gen(u, rnd, wild=cfg.wild)
    mut = Mutators(u, rnd)
    reject_ops = [o for o in cfg.ops if o in Mutators.REJECT]
    accept_ops = [o for o in cfg.ops if o in Mutators.ACCEPT]
    seeds = list(cfg.seeds.items())
    cases = []
    tries = 0
    while len(cases) < cfg.n and tries < cfg.n * 20:
        tries += 1
        cid = "c%04d" % len(cases)
        if seeds and rnd.random() < 0.25:
            sname, sxml = rnd.choice(seeds)
            try:
                root, origin = parse(sxml), "seed:" + sname
            except ET.ParseError:
                continue
        else:
            root, origin = gen.scene(cfg.depth, cfg.groups), "gen"
        r = rnd.random()
        if r < cfg.clean or (not reject_ops and not accept_ops):
            cases.append(Case(cid, to_xml(root), "clean", "accept", origin))
            continue
        ops = accept_ops if (r < cfg.clean + cfg.metamorphic and accept_ops) or not reject_ops else reject_ops
        op = rnd.choice(ops)
        expect = "accept" if op in Mutators.ACCEPT else "reject"
        if op in Mutators.TEXT:
            res = mut.text(op, to_xml(root))
            if res:
                cases.append(Case(cid, res[0], op, expect, "%s · %s" % (origin, res[1]), "document"))
            continue
        m = getattr(mut, op)(root)
        if m is None:
            continue
        site = path_of(root, m.target) if m.target is not None else None
        cases.append(Case(cid, to_xml(root), op, expect, "%s · %s" % (origin, m.note),
                          m.target.tag if m.target is not None else "", site, m.attr))
    return cases


async def _arun(fr: FuzzRun) -> None:
    sem = asyncio.Semaphore(max(1, fr.cfg.concurrency))
    live = [o for o in fr.oracles if not o.batch]
    deadline = time.time() + fr.cfg.time_budget_s
    done = 0

    async def one(c: Case):
        nonlocal done
        if time.time() > deadline:
            return
        async with sem:
            res = await asyncio.gather(*(o(c.xml) for o in live), return_exceptions=True)
        row = {}
        for o, r in zip(live, res):
            row[o.name] = r if isinstance(r, Outcome) else Outcome(o.name, "crash", norm(repr(r)), 0, repr(r)[:300])
        fr.out[c.id] = row
        done += 1
        flags = [n for n, r in row.items() if fr.classify(c, r)]
        bus.emit("fuzz %s %-17s %s%s" % (c.id, c.op, " ".join("%s:%s" % (n, r.verdict[0].upper()) for n, r in row.items()),
                                          ("  ⚑ " + ",".join(flags)) if flags else ""),
                 level="INFO" if flags else "DEBUG", src="fuzz", kind="fuzz_case",
                 data={"case": c.id, "op": c.op, "expect": c.expect, "done": done, "of": len(fr.cases),
                       "verdicts": {n: r.verdict for n, r in row.items()}, "flags": flags})

    await asyncio.gather(*(one(c) for c in fr.cases))


def run(cfg: FuzzConfig, oracles: list, cases: list[Case] | None = None) -> FuzzRun:
    """Generate (or take) cases, ask every oracle, bucket the findings and minimise one reproducer per bucket."""
    from . import aio
    fr = FuzzRun(cfg, oracles)
    with bus.span("fuzz: generate %d cases (seed %d)" % (cfg.n, cfg.seed), src="fuzz", track="Fuzz"):
        fr.cases = cases if cases is not None else make_cases(cfg)
    with bus.span("fuzz: %d cases x %d oracles" % (len(fr.cases), len(oracles)), src="fuzz", track="Fuzz"):
        aio.run(_arun(fr), cfg.time_budget_s + 120)
        for o in oracles:
            if o.batch:
                with bus.span("fuzz: %s (batch)" % o.name, src="fuzz", track="Fuzz"):
                    for cid, r in o.run_batch([c for c in fr.cases if c.id in fr.out]).items():
                        fr.out[cid][o.name] = r
    fr.cases = [c for c in fr.cases if c.id in fr.out]
    fr.bucket_all()
    if cfg.minimize:
        by_id = {c.id: c for c in fr.cases}
        for b in sorted(fr.buckets.values(), key=lambda b: -len(b.cases)):
            o = next((x for x in oracles if x.name == b.oracle), None)
            if o is None or o.batch:
                continue
            if b.kind == "missed" and o.name in VIEWERS and not cfg.minimize_viewer_misses:
                continue
            c = by_id[b.cases[0]]
            refs = [x for x in oracles if not x.batch and x.name != o.name and fr.out[c.id].get(x.name)
                    and fr.out[c.id][x.name].verdict == "reject"] if b.kind == "missed" else []
            with bus.span("minimise %s" % b.key[:80], src="fuzz", track="Fuzz") as info:
                b.repro, b.min_calls = aio.run(minimise_bucket(c, b, o, refs[:1], cfg.min_budget, fr.out[c.id].get(o.name)), 600)
                b.orig_bytes, b.repro_bytes = len(c.xml), len(b.repro) if b.repro else None
                info.update(bytes_before=b.orig_bytes, bytes_after=b.repro_bytes, calls=b.min_calls)
    fr.ended = time.time()
    conf = fr.confusion()
    bus.emit("fuzz done: %d cases, %d buckets · %s" % (len(fr.cases), len(fr.buckets),
             " · ".join("%s recall %s spec %s" % (n, d.get("recall"), d.get("specificity")) for n, d in conf.items())),
             src="fuzz", kind="fuzz_end", data={"confusion": conf, "buckets": len(fr.buckets)})
    return fr


# ====================================================================== minimisation (hierarchical delta debugging)
async def ddmin(items: list, test, budget: list) -> list:
    """Smallest subset of `items` (1-minimal) for which `await test(subset)` is still True. budget[0] counts calls.
    Tries the empty subset first: in HDD a whole level of the tree often goes at once."""
    if items and budget[0] > 0:
        budget[0] -= 1
        if await test([]):
            return []
    n = 2
    while len(items) >= 2 and budget[0] > 0:
        chunk = max(1, len(items) // n)
        subsets = [items[i:i + chunk] for i in range(0, len(items), chunk)]
        reduced = False
        for i in range(len(subsets)):
            complement = [x for j, s in enumerate(subsets) if j != i for x in s]
            budget[0] -= 1
            if budget[0] < 0:
                return items
            if await test(complement):
                items, n, reduced = complement, max(n - 1, 2), True
                break
        if not reduced:
            if n >= len(items):
                break
            n = min(len(items), n * 2)
    return items


async def hdd(xml: str, test, budget: int = 60, site: list | None = None, attr: str | None = None) -> tuple[str, int]:
    """Hierarchical delta debugging over the X3D tree: drop subtrees level by level, then attributes.

    `test(xml) -> bool` must stay True. The element at `site`, its ancestors, its attributes and any DEF it refers to
    (USE / ROUTE ends) are never removed: a "nobody caught it" bucket has no oracle that could notice the defect leaving.
    Returns (smallest xml found, oracle calls used)."""
    try:
        root = parse(xml)
    except ET.ParseError:
        return xml, 0
    left = [budget]
    ids = {}
    for i, e in enumerate(root.iter()):
        ids[e] = i
    protected, site_id = set(), None
    if site is not None:
        e = root
        protected.add(ids[e])
        for k in site:
            kids = list(e)
            if k >= len(kids):
                break
            e = kids[k]
            protected.add(ids[e])
        site_id = ids[e]
        # what the defect is made of: the site's own attributes stay, and so does any DEF it points at
        refs = {e.get(k) for k in ("USE", "fromNode", "toNode") if e.get(k)}
        pm0 = _parent_map(root)
        for d in root.iter():
            if d.get("DEF") in refs:
                chain = [d]
                while chain[-1] in pm0:
                    chain.append(pm0[chain[-1]])
                protected |= {ids[x] for x in chain}
    for keep in ("head", "Scene"):
        if root.find(keep) is not None:
            protected.add(ids[root.find(keep)])

    by_id = {i: e for e, i in ids.items()}
    parent = _parent_map(root)

    def under(e, dropped_ids) -> bool:
        while e in parent:
            e = parent[e]
            if ids[e] in dropped_ids:
                return True
        return False

    def build(drop_elems: set, drop_attrs: set) -> str:
        r2 = copy.deepcopy(root)
        m = dict(zip(root.iter(), r2.iter()))
        pm = _parent_map(r2)
        for i in drop_elems:
            c = m[by_id[i]]
            if c in pm and c in list(pm[c]):
                pm[c].remove(c)
        for (i, a) in drop_attrs:
            m[by_id[i]].attrib.pop(a, None)
        return to_xml(r2)

    dropped: set = set()
    cache: dict = {}

    async def t(xml_: str) -> bool:
        if xml_ not in cache:
            cache[xml_] = bool(await test(xml_))
        return cache[xml_]

    depth_of = {}
    for e in root.iter():
        for c in e:
            depth_of[c] = depth_of.get(e, 0) + 1
    for level in range(1, max(depth_of.values(), default=0) + 1):
        alive = [ids[e] for e, d in depth_of.items() if d == level and ids[e] not in protected and e.tag != "meta"
                 and not under(e, dropped)]
        if not alive or left[0] <= 0:
            continue

        async def keep_test(keep: list, alive=alive):
            return await t(build(dropped | (set(alive) - set(keep)), set()))

        kept = await ddmin(alive, keep_test, left)
        dropped |= set(alive) - set(kept)
    # a DEF something points at stays: dropping it would turn the case into a different defect (a dangling USE)
    referenced = {e.get(k) for e in root.iter() for k in ("USE", "fromNode", "toNode") if e.get(k)}
    attrs = [(ids[e], a) for e in root.iter() if e is not root and ids[e] not in dropped and not under(e, dropped)
             for a in e.attrib if ids[e] != site_id and not a.startswith("{")
             and not (a == "DEF" and e.get("DEF") in referenced)]
    dropped_attrs: set = set()
    if attrs and left[0] > 0:
        async def attr_test(keep: list):
            return await t(build(dropped, set(attrs) - set(keep)))
        kept = await ddmin(attrs, attr_test, left)
        dropped_attrs = set(attrs) - set(kept)
    out = build(dropped, dropped_attrs)
    if not await t(out):                      # paranoia: never hand back something that no longer reproduces
        out = xml
    return out, budget - max(left[0], 0)


async def minimise_bucket(c: Case, b: Bucket, oracle, refs: list, budget: int, first: Outcome | None = None) -> tuple[str | None, int]:
    if c.op == "truncate":
        return c.xml, 0
    want = (first.key or first.sig) if first else b.sig

    async def still(xml: str) -> bool:
        o = await oracle(xml)
        if b.kind == "crash":
            return o.verdict == "crash" and (o.key or o.sig) == want
        if b.kind == "false_alarm":
            return o.verdict == "reject" and (o.key or o.sig) == want
        if o.verdict != "accept":
            return False
        for r in refs:                       # the document must still be broken in the eyes of an oracle that saw it
            if (await r(xml)).verdict != "reject":
                return False
        return True

    xml, calls = await hdd(c.xml, still, budget, c.site, c.attr)
    return xml, calls


# ====================================================================== report
def report_html(card: dict, repros: dict | None = None) -> str:
    e = lambda s: H.escape(str(s))  # noqa: E731
    repros = repros or {}
    names = card["oracles"]
    conf = "".join("<tr><td><b>%s</b></td>%s</tr>" % (e(n), "".join("<td align=right>%s</td>" % e(card["confusion"].get(n, {}).get(k, 0) or 0)
                   for k in ("caught", "missed", "passed", "false_alarm", "crash", "recall", "specificity", "mean_ms"))) for n in names)
    agree = "".join("<tr><td>%s</td><td align=right>%d</td><td align=right>%.3f</td><td align=right>%.3f</td></tr>"
                    % (e(k), v["n"], v["agree"], v["kappa"]) for k, v in card["agreement"].items())

    def cell(x):
        if x is None:
            return "<td></td>"
        hue = int(120 * x)
        return "<td style='background:hsl(%d,70%%,85%%)' align=right>%.2f</td>" % (hue, x)
    ops = "".join("<tr><td>%s</td>%s</tr>" % (e(op), "".join(cell(row.get(n)) for n in names)) for op, row in card["by_operator"].items())
    col = {"crash": "#c62828", "missed": "#9a5b00", "false_alarm": "#6a1b9a"}
    buckets = "".join("<details><summary><b style='color:%s'>%s</b> · %s · <code>%s</code> · %s · ×%d%s</summary><pre>%s</pre></details>"
                      % (col.get(b["kind"], "#333"), e(b["kind"]), e(b["oracle"]), e(b["op"]), e(b["sig"]), b["count"],
                         " · minimised %s→%s bytes" % (b["orig_bytes"], b["repro_bytes"]) if b.get("repro_bytes") else "",
                         e(repros.get(b["key"]) or "(no reproducer)")) for b in card["buckets"])
    return """<!doctype html><meta charset=utf-8><title>x3d.lab fuzz</title>
<style>body{font:14px system-ui;margin:20px;max-width:1200px}table{border-collapse:collapse;font-size:12.5px;margin:6px 0}
td,th{border-bottom:1px solid #ddd;padding:3px 7px}pre{background:#f6f6f6;padding:8px;font-size:11.5px;white-space:pre-wrap}
details{margin:4px 0}summary{cursor:pointer}</style>
<h1>Differential fuzz · %d cases · %s s</h1><p>seed %s · oracles: %s</p>
<h2>Per oracle</h2><table><tr><th></th><th>caught</th><th>missed</th><th>passed</th><th>false alarm</th><th>crash</th><th>recall</th><th>specificity</th><th>mean ms</th></tr>%s</table>
<h2>Agreement</h2><table><tr><th>pair</th><th>n</th><th>agree</th><th>κ</th></tr>%s</table>
<h2>Expected verdict by operator</h2><table><tr><th>operator</th>%s</tr>%s</table>
<h2>Buckets (%d)</h2>%s""" % (card["cases"], card["seconds"], card["config"]["seed"], e(", ".join(names)), conf, agree,
                               "".join("<th>%s</th>" % e(n) for n in names), ops, len(card["buckets"]), buckets)
