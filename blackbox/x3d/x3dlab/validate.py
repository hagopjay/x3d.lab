"""Structural X3D 4.0 validator (XML encoding), stdlib only.

What it checks, with a line number on every issue:

* well-formed XML; X3D root, profile and version; head (component / meta / unit)
* every element is a known node or statement (with a "did you mean" for typos)
* every attribute is a field of that node, and its text parses as the field's X3D type (arity, numbers,
  booleans, colour ranges)
* DEF names are unique in their scope; USE refers to an earlier DEF of the same node type
* each child lands in an SFNode/MFNode field of its parent (explicit or default containerField), and an
  SFNode field holds one node
* ROUTEs: both ends exist, both fields are events in the right direction (including Script / ComposedShader
  <field>s and PROTO interfaces), and the types match
* geometry consistency: coordIndex / colorIndex / index ranges, LineSet vertexCount, per-vertex colour counts,
  ElevationGrid height count, IndexedQuadSet multiples of four, TextureCoordinate counts
* PROTO instances name a declared PROTO and only set interface fields

It does not replace the Web3D schema/Schematron validators; it is fast, local, and tuned to what breaks the
X3D → Blender → glTF → Unreal chain.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass, field as dfield
from xml.parsers import expat

from .schema import SCHEMA, event_type
from .model import TUPLE, MF_TUPLE

PROFILES = {"Core", "Interchange", "CADInterchange", "Interactive", "Immersive", "MedicalInterchange",
            "MPEG4Interactive", "Full"}
VERSIONS = {"3.0", "3.1", "3.2", "3.3", "4.0", "4.1"}
HTML_ATTRS = {"class", "id", "style", "DEF", "USE", "containerField"}
NUM = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
INT = re.compile(r"[+-]?(?:0[xX][0-9a-fA-F]+|\d+)$")
STATEMENTS = set(SCHEMA["statements"]) | {"X3D", "head", "Scene", "fieldValue"}


@dataclass
class El:
    tag: str
    attrs: dict
    line: int
    col: int
    parent: "El | None" = None
    children: list = dfield(default_factory=list)
    text: str = ""

    def path(self) -> str:
        parts = []
        e = self
        while e is not None and e.tag != "X3D":
            name = e.tag + (("#" + e.attrs["DEF"]) if "DEF" in e.attrs else "")
            parts.append(name)
            e = e.parent
        return "/".join(reversed(parts))


def parse(text: str) -> El:
    root = El("#document", {}, 0, 0)
    stack = [root]
    p = expat.ParserCreate()

    def start(tag, attrs):
        el = El(tag, attrs, p.CurrentLineNumber, p.CurrentColumnNumber, stack[-1])
        stack[-1].children.append(el)
        stack.append(el)

    def end(tag):
        stack.pop()

    def chars(data):
        stack[-1].text += data

    p.StartElementHandler = start
    p.EndElementHandler = end
    p.CharacterDataHandler = chars
    p.Parse(text, True)
    return root


class Report:
    def __init__(self):
        self.issues = []

    def add(self, level, code, el, msg):
        self.issues.append({"level": level, "code": code, "line": el.line if el else 0,
                            "path": el.path() if el else "", "message": msg})

    def error(self, code, el, msg):
        self.add("error", code, el, msg)

    def warn(self, code, el, msg):
        self.add("warning", code, el, msg)

    def info(self, code, el, msg):
        self.add("info", code, el, msg)


def tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[\s,]+", s.strip()) if t]


def parse_mfstring(s: str) -> list[str] | None:
    s = s.strip()
    if not s:
        return []
    if not s.startswith('"'):
        return [s]  # a bare SFString-looking MFString: browsers accept it, the spec wants quotes
    out, i = [], 0
    while i < len(s):
        while i < len(s) and s[i] in " \t\r\n,":
            i += 1
        if i >= len(s):
            break
        if s[i] != '"':
            return None
        i += 1
        buf = []
        while i < len(s) and s[i] != '"':
            if s[i] == "\\" and i + 1 < len(s):
                i += 1
            buf.append(s[i])
            i += 1
        if i >= len(s):
            return None
        i += 1
        out.append("".join(buf))
    return out


def check_value(rep: Report, el: El, name: str, ftype: str, text: str):
    """Parse ``text`` as ``ftype``; report problems. Returns the parsed value (list/number/bool/str) or None."""
    if ftype == "SFBool":
        if text not in ("true", "false"):
            rep.error("E_BOOL", el, "%s=%r: XML booleans are lowercase true/false" % (name, text))
            return None
        return text == "true"
    if ftype == "MFBool":
        vals = tokens(text)
        bad = [v for v in vals if v not in ("true", "false")]
        if bad:
            rep.error("E_BOOL", el, "%s: %r is not true/false" % (name, bad[0]))
        return [v == "true" for v in vals]
    if ftype in ("SFString",):
        return text
    if ftype == "MFString":
        v = parse_mfstring(text)
        if v is None:
            rep.error("E_MFSTRING", el, "%s: unbalanced quotes in MFString" % name)
        elif text.strip() and not text.strip().startswith('"'):
            rep.warn("W_MFSTRING_QUOTES", el, '%s: MFString values should be quoted ("…")' % name)
        return v
    if ftype in ("SFInt32", "MFInt32", "SFImage", "MFImage"):
        vals = tokens(text)
        bad = [v for v in vals if not INT.match(v)]
        if bad:
            rep.error("E_INT", el, "%s: %r is not an integer" % (name, bad[0]))
            return None
        if ftype == "SFInt32" and len(vals) != 1:
            rep.error("E_ARITY", el, "%s: SFInt32 takes one value, got %d" % (name, len(vals)))
        ints = [int(v, 0) for v in vals]
        return ints[0] if ftype == "SFInt32" and ints else ints
    if ftype in ("SFNode", "MFNode"):
        rep.error("E_NODE_ATTR", el, "%s is a node field; put the node in as a child element" % name)
        return None
    vals = tokens(text)
    bad = [v for v in vals if not NUM.match(v)]
    if bad:
        rep.error("E_NUMBER", el, "%s: %r is not a number" % (name, bad[0]))
        return None
    nums = [float(v) for v in vals]
    if ftype in ("SFFloat", "SFDouble", "SFTime"):
        if len(nums) != 1:
            rep.error("E_ARITY", el, "%s: %s takes one number, got %d" % (name, ftype, len(nums)))
            return None
        return nums[0]
    if ftype in ("MFFloat", "MFDouble", "MFTime"):
        return nums
    k = TUPLE.get(ftype) or MF_TUPLE.get(ftype)
    if k is None:
        return nums
    if ftype in TUPLE and len(nums) != k:
        rep.error("E_ARITY", el, "%s: %s takes %d numbers, got %d" % (name, ftype, k, len(nums)))
        return None
    if ftype in MF_TUPLE and len(nums) % k:
        rep.error("E_ARITY", el, "%s: %s needs a multiple of %d numbers, got %d" % (name, ftype, k, len(nums)))
        return None
    if ftype in ("SFColor", "MFColor", "SFColorRGBA", "MFColorRGBA"):
        out = [x for x in nums if x < 0 or x > 1]
        if out:
            rep.error("E_COLOR_RANGE", el, "%s: colour component %g outside [0, 1]" % (name, out[0]))
    groups = [nums[i:i + k] for i in range(0, len(nums), k)]
    return groups[0] if ftype in TUPLE else groups


class Scope:
    def __init__(self, parent=None):
        self.defs: dict[str, El] = {}
        self.parent = parent


def validate_text(text: str, *, filename: str = "<string>") -> dict:
    rep = Report()
    try:
        doc = parse(text)
    except expat.ExpatError as e:
        return {"file": filename, "ok": False, "errors": 1, "warnings": 0,
                "issues": [{"level": "error", "code": "E_XML", "line": e.lineno, "path": "",
                            "message": "not well-formed XML: %s" % expat.ErrorString(e.code)}],
                "stats": {}}
    roots = [c for c in doc.children]
    if not roots or roots[0].tag != "X3D":
        rep.error("E_ROOT", roots[0] if roots else None, "root element must be <X3D>")
        return finish(rep, filename, {})
    x3d = roots[0]
    prof, ver = x3d.attrs.get("profile"), x3d.attrs.get("version")
    if prof not in PROFILES:
        rep.error("E_PROFILE", x3d, "profile %r is not an X3D profile (%s)" % (prof, ", ".join(sorted(PROFILES))))
    if ver not in VERSIONS:
        rep.error("E_VERSION", x3d, "version %r is not an X3D version" % ver)
    scene = None
    for c in x3d.children:
        if c.tag == "head":
            check_head(rep, c)
        elif c.tag == "Scene":
            scene = c
        else:
            rep.error("E_X3D_CHILD", c, "<X3D> holds only <head> and <Scene>, not <%s>" % c.tag)
    if scene is None:
        rep.error("E_NO_SCENE", x3d, "no <Scene>")
        return finish(rep, filename, {})

    ctx = Ctx(rep)
    ctx.walk_children(scene, Scope())
    ctx.check_routes()
    stats = {
        "profile": prof, "version": ver,
        "nodes": sum(ctx.counts.values()),
        "byType": dict(sorted(ctx.counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "defs": len(ctx.all_defs),
        "routes": len(ctx.routes),
        "protos": sorted(ctx.protos),
        "protoInstances": ctx.proto_instances,
        "components": [(c.attrs.get("name"), c.attrs.get("level")) for h in x3d.children if h.tag == "head"
                       for c in h.children if c.tag == "component"],
    }
    stats["facts"] = ctx.facts
    return finish(rep, filename, stats)


def finish(rep: Report, filename: str, stats: dict) -> dict:
    errors = sum(1 for i in rep.issues if i["level"] == "error")
    warnings = sum(1 for i in rep.issues if i["level"] == "warning")
    rep.issues.sort(key=lambda i: ({"error": 0, "warning": 1, "info": 2}[i["level"]], i["line"]))
    return {"file": filename, "ok": errors == 0, "errors": errors, "warnings": warnings,
            "issues": rep.issues, "stats": stats}


def check_head(rep: Report, head: El):
    for c in head.children:
        if c.tag == "component":
            if not c.attrs.get("name"):
                rep.error("E_COMPONENT", c, "component needs a name")
            lvl = c.attrs.get("level", "")
            if not lvl.isdigit():
                rep.error("E_COMPONENT", c, "component level must be an integer, got %r" % lvl)
        elif c.tag == "meta":
            if "name" not in c.attrs or "content" not in c.attrs:
                rep.warn("W_META", c, "meta should have name and content")
        elif c.tag == "unit":
            pass
        else:
            rep.error("E_HEAD_CHILD", c, "<head> holds component, unit and meta, not <%s>" % c.tag)


class Ctx:
    def __init__(self, rep: Report):
        self.rep = rep
        self.counts: Counter = Counter()
        self.all_defs: dict[str, El] = {}
        self.routes: list[tuple[El, Scope]] = []
        self.protos: dict[str, dict] = {}
        self.proto_instances = 0
        self.user_fields: dict[int, dict] = {}  # id(El) -> {name: (type, access)}
        self.facts: dict = {"elevationGrids": [], "colourMidtones": 0, "specular": 0, "gradientBackground": False,
                            "lineProperties": 0, "pointProperties": 0, "twoSidedBack": 0, "scriptsDriveGeometry": 0,
                            "transparency": 0, "animatedTransforms": [], "lineSets": 0, "colouredLineSets": 0}

    # ── tree ───────────────────────────────────────────────────────────────────────────────────
    def walk_children(self, parent: El, scope: Scope):
        for c in parent.children:
            self.element(c, parent, scope)

    def element(self, el: El, parent: El, scope: Scope):
        tag = el.tag
        if tag == "ROUTE":
            self.routes.append((el, scope))
            return
        if tag == "ProtoDeclare":
            self.proto_declare(el, scope)
            return
        if tag == "ExternProtoDeclare":
            name = el.attrs.get("name")
            self.protos[name] = {"fields": {f.attrs.get("name"): (f.attrs.get("type"), f.attrs.get("accessType"))
                                            for f in el.children if f.tag == "field"}}
            return
        if tag == "ProtoInstance":
            self.proto_instance(el, parent, scope)
            return
        if tag in ("IS", "field", "fieldValue", "connect", "Comment", "EXPORT", "IMPORT", "unit", "meta", "component"):
            return  # handled by their owners
        spec = SCHEMA["nodes"].get(tag)
        if spec is None:
            close = difflib.get_close_matches(tag, list(SCHEMA["nodes"]) + list(STATEMENTS), n=1)
            self.rep.error("E_UNKNOWN_NODE", el, "unknown element <%s>%s" % (tag, (" (did you mean <%s>?)" % close[0]) if close else ""))
            return
        self.counts[tag] += 1
        self.containment(el, parent, spec)
        # DEF / USE
        if "USE" in el.attrs:
            name = el.attrs["USE"]
            target = self.lookup(scope, name)
            if target is None:
                self.rep.error("E_USE_UNDEFINED", el, "USE=%r before (or without) a DEF of that name" % name)
            elif target.tag != tag:
                self.rep.error("E_USE_TYPE", el, "USE=%r is a %s, used as a %s" % (name, target.tag, tag))
            extra = [a for a in el.attrs if a not in ("USE", "containerField", "class", "id")]
            if extra or el.children:
                self.rep.warn("W_USE_EXTRA", el, "a USE node takes no fields or children (ignored: %s)" %
                              ", ".join(extra + [c.tag for c in el.children]))
            return
        if "DEF" in el.attrs:
            name = el.attrs["DEF"]
            if name in scope.defs:
                self.rep.error("E_DEF_DUP", el, "DEF=%r already used on line %d" % (name, scope.defs[name].line))
            if not re.match(r"^[^\d\s+\-'\",#.\[\]\\{}\x00-\x20][^\s'\",#.\[\]\\{}\x00-\x20]*$", name):
                self.rep.error("E_DEF_NAME", el, "DEF=%r is not a legal X3D name" % name)
            scope.defs[name] = el
            self.all_defs[name] = el
        # fields
        values = {}
        has_is = any(c.tag == "IS" for c in el.children)
        for a, text in el.attrs.items():
            if a in ("DEF", "USE", "containerField") or (a in ("class", "id") and a not in spec["fields"]):
                continue
            if a == "style" and "style" not in spec["fields"]:
                continue
            f = spec["fields"].get(a)
            if f is None:
                close = difflib.get_close_matches(a, list(spec["fields"]), n=1)
                self.rep.error("E_UNKNOWN_FIELD", el, "<%s> has no field %r%s" % (tag, a, (" (did you mean %r?)" % close[0]) if close else ""))
                continue
            values[a] = check_value(self.rep, el, a, f[0], text)
        # user fields (Script, ComposedShader, ShaderProgram, PackagedShader)
        if any(c.tag == "field" for c in el.children):
            decl = {}
            for f in el.children:
                if f.tag != "field":
                    continue
                n, t, acc = f.attrs.get("name"), f.attrs.get("type"), f.attrs.get("accessType")
                if not n or not t or not acc:
                    self.rep.error("E_FIELD_DECL", f, "<field> needs name, type and accessType")
                    continue
                if acc not in ("initializeOnly", "inputOnly", "outputOnly", "inputOutput"):
                    self.rep.error("E_FIELD_DECL", f, "accessType %r is not one of initializeOnly/inputOnly/outputOnly/inputOutput" % acc)
                if "value" in f.attrs:
                    check_value(self.rep, f, n, t, f.attrs["value"])
                decl[n] = (t, acc)
                for node_child in f.children:
                    self.element(node_child, f, scope)
            self.user_fields[id(el)] = decl
        self.node_rules(el, tag, values, has_is)
        self.walk_children(el, scope)

    def containment(self, el: El, parent: El, spec):
        if parent.tag in ("Scene", "ProtoBody", "field", "fieldValue"):
            if parent.tag == "Scene" and "X3DChildNode" not in spec["types"] and not el.tag.endswith("Metadata") \
                    and not any(t in spec["types"] for t in ("X3DMetadataObject",)):
                self.rep.warn("W_NOT_CHILD", el, "<%s> is not an X3DChildNode; it cannot sit directly in the Scene" % el.tag)
            return
        pspec = SCHEMA["nodes"].get(parent.tag)
        if pspec is None:
            return
        cf = el.attrs.get("containerField") or spec["containerField"] or "children"
        f = pspec["fields"].get(cf)
        if f is None or f[0] not in ("SFNode", "MFNode"):
            nodefields = [k for k, v in pspec["fields"].items() if v[0] in ("SFNode", "MFNode")]
            self.rep.error("E_CONTAINER", el, "<%s> goes into %s.%s, which is not a node field of %s (node fields: %s)" % (
                el.tag, parent.tag, cf, parent.tag, ", ".join(nodefields) or "none"))
            return
        if f[0] == "SFNode":
            same = [c for c in parent.children if c.tag not in ("IS", "field", "ROUTE", "Comment")
                    and (c.attrs.get("containerField") or (SCHEMA["nodes"].get(c.tag) or {}).get("containerField")
                         or "children") == cf]
            if len(same) > 1 and same[0] is not el and same.index(el) == 1:
                self.rep.error("E_SFNODE_MULTI", el, "%s.%s is an SFNode field and already holds a <%s>" % (
                    parent.tag, cf, same[0].tag))

    def lookup(self, scope: Scope, name: str):
        while scope is not None:
            if name in scope.defs:
                return scope.defs[name]
            scope = scope.parent
        return None

    # ── protos ─────────────────────────────────────────────────────────────────────────────────
    def proto_declare(self, el: El, scope: Scope):
        name = el.attrs.get("name")
        if not name:
            self.rep.error("E_PROTO", el, "ProtoDeclare needs a name")
            return
        if name in self.protos:
            self.rep.error("E_PROTO_DUP", el, "PROTO %r declared twice" % name)
        fields = {}
        body = None
        for c in el.children:
            if c.tag == "ProtoInterface":
                for f in c.children:
                    if f.tag == "field":
                        fields[f.attrs.get("name")] = (f.attrs.get("type"), f.attrs.get("accessType"))
                        if "value" in f.attrs and f.attrs.get("type"):
                            check_value(self.rep, f, f.attrs.get("name"), f.attrs["type"], f.attrs["value"])
            elif c.tag == "ProtoBody":
                body = c
        self.protos[name] = {"fields": fields, "line": el.line}
        if body is None:
            self.rep.error("E_PROTO", el, "PROTO %r has no ProtoBody" % name)
            return
        inner = Scope()  # PROTO bodies have their own DEF namespace
        self.walk_children(body, inner)
        # IS/connect references must name interface fields
        for node in iter_tree(body):
            for c in node.children:
                if c.tag == "IS":
                    for k in c.children:
                        pf = k.attrs.get("protoField")
                        if pf not in fields:
                            self.rep.error("E_IS", k, "connect protoField=%r is not in PROTO %s's interface" % (pf, name))

    def proto_instance(self, el: El, parent: El, scope: Scope):
        self.proto_instances += 1
        name = el.attrs.get("name")
        p = self.protos.get(name)
        if p is None:
            self.rep.error("E_PROTO_UNDECLARED", el, "ProtoInstance of undeclared PROTO %r" % name)
        if "DEF" in el.attrs:
            scope.defs[el.attrs["DEF"]] = el
            self.all_defs[el.attrs["DEF"]] = el
        for fv in el.children:
            if fv.tag != "fieldValue":
                continue
            n = fv.attrs.get("name")
            if p is not None and n not in p["fields"]:
                self.rep.error("E_FIELDVALUE", fv, "PROTO %s has no field %r" % (name, n))
            elif p is not None and "value" in fv.attrs:
                check_value(self.rep, fv, n, p["fields"][n][0], fv.attrs["value"])
            for c in fv.children:
                self.element(c, fv, scope)

    # ── ROUTEs ─────────────────────────────────────────────────────────────────────────────────
    def event(self, el: El, field: str, direction: str):
        if el.tag == "ProtoInstance":
            p = self.protos.get(el.attrs.get("name"))
            if not p:
                return None
            base = field
            if direction == "in" and field.startswith("set_"):
                base = field[4:]
            if direction == "out" and field.endswith("_changed"):
                base = field[:-8]
            for cand in (field, base):
                if cand in p["fields"]:
                    t, acc = p["fields"][cand]
                    ok = acc == "inputOutput" or (direction == "in" and acc == "inputOnly") or (direction == "out" and acc == "outputOnly")
                    return t if ok else None
            return None
        user = self.user_fields.get(id(el), {})
        if field in user:
            t, acc = user[field]
            ok = acc == "inputOutput" or (direction == "in" and acc == "inputOnly") or (direction == "out" and acc == "outputOnly")
            return t if ok else None
        return event_type(el.tag, field, direction)

    def check_routes(self):
        for r, scope in self.routes:
            a = {k: r.attrs.get(k) for k in ("fromNode", "fromField", "toNode", "toField")}
            if not all(a.values()):
                self.rep.error("E_ROUTE", r, "ROUTE needs fromNode, fromField, toNode and toField")
                continue
            src, dst = self.lookup(scope, a["fromNode"]), self.lookup(scope, a["toNode"])
            if src is None:
                self.rep.error("E_ROUTE_NODE", r, "ROUTE fromNode %r has no DEF" % a["fromNode"])
            if dst is None:
                self.rep.error("E_ROUTE_NODE", r, "ROUTE toNode %r has no DEF" % a["toNode"])
            if src is None or dst is None:
                continue
            t_out = self.event(src, a["fromField"], "out")
            t_in = self.event(dst, a["toField"], "in")
            if t_out is None:
                self.rep.error("E_ROUTE_FIELD", r, "%s.%s is not an output event of <%s>" % (a["fromNode"], a["fromField"], src.tag))
            if t_in is None:
                self.rep.error("E_ROUTE_FIELD", r, "%s.%s is not an input event of <%s>" % (a["toNode"], a["toField"], dst.tag))
            if dst.tag == "Transform" and a["toField"] in ("set_translation", "set_rotation", "set_scale", "translation",
                                                           "rotation", "scale") and src.tag.endswith("Interpolator"):
                if a["toNode"] not in self.facts["animatedTransforms"]:
                    self.facts["animatedTransforms"].append(a["toNode"])
            if t_out and t_in and t_out != t_in:
                self.rep.error("E_ROUTE_TYPE", r, "ROUTE %s.%s (%s) → %s.%s (%s): types differ" % (
                    a["fromNode"], a["fromField"], t_out, a["toNode"], a["toField"], t_in))

    # ── node-specific consistency ──────────────────────────────────────────────────────────────
    def child(self, el: El, *tags):
        for c in el.children:
            if c.tag in tags:
                if "USE" in c.attrs:
                    return self.all_defs.get(c.attrs["USE"], c)
                return c
        return None

    def count_of(self, node: El | None, fieldname: str, k: int) -> int | None:
        if node is None or fieldname not in node.attrs:
            return 0 if node is not None else None
        return len(tokens(node.attrs[fieldname])) // k

    def node_rules(self, el: El, tag: str, v: dict, has_is: bool):
        rep, F = self.rep, self.facts
        if tag in ("Material", "TwoSidedMaterial", "UnlitMaterial"):
            for name in ("diffuseColor", "emissiveColor", "baseColor"):
                c = v.get(name)
                if isinstance(c, list) and any(0.02 < x < 0.98 for x in c):
                    F["colourMidtones"] += 1
                    break
            sc = v.get("specularColor")
            if isinstance(sc, list) and max(sc) > 0:
                F["specular"] += 1
            if (v.get("transparency") or 0) > 0:
                F["transparency"] += 1
            if tag == "TwoSidedMaterial":
                rep.info("I_DEPRECATED", el, "TwoSidedMaterial is deprecated in X3D 4.0: use Appearance.backMaterial")
                if v.get("separateBackColor"):
                    F["twoSidedBack"] += 1
        if tag == "Background":
            if len(v.get("skyColor") or []) > 1 or len(v.get("groundColor") or []) > 0:
                F["gradientBackground"] = True
            sky, ang = v.get("skyColor") or [], v.get("skyAngle") or []
            if sky and len(ang) != len(sky) - 1:
                rep.error("E_BG_ANGLES", el, "skyAngle needs one value fewer than skyColor (%d colours, %d angles)" % (len(sky), len(ang)))
            gnd, gang = v.get("groundColor") or [], v.get("groundAngle") or []
            if gnd and len(gang) != len(gnd) - 1:
                rep.error("E_BG_ANGLES", el, "groundAngle needs one value fewer than groundColor")
        if tag in ("IndexedLineSet", "LineSet", "PointSet"):
            F["lineSets"] += 1
            if self.child(el, "Color", "ColorRGBA") is not None:
                F["colouredLineSets"] += 1
        if tag == "LineProperties":
            F["lineProperties"] += 1
        if tag == "PointProperties":
            F["pointProperties"] += 1
        if tag in ("IndexedFaceSet", "IndexedLineSet", "IndexedTriangleSet", "IndexedQuadSet"):
            coord = self.child(el, "Coordinate", "CoordinateDouble")
            n = self.count_of(coord, "point", 3)
            idx_name = "index" if tag in ("IndexedTriangleSet", "IndexedQuadSet") else "coordIndex"
            idx = v.get(idx_name) or []
            if n is not None and idx:
                hi = max(idx)
                if hi >= n:
                    rep.error("E_INDEX_RANGE", el, "%s refers to point %d but the Coordinate has %d points" % (idx_name, hi, n))
                if min(idx) < -1:
                    rep.error("E_INDEX_RANGE", el, "%s holds %d (only -1 separates faces/polylines)" % (idx_name, min(idx)))
            if tag == "IndexedQuadSet" and len(idx) % 4:
                rep.error("E_QUADS", el, "IndexedQuadSet index needs a multiple of 4 values, got %d" % len(idx))
            col = self.child(el, "Color", "ColorRGBA")
            if col is not None and n:
                k = 3 if col.tag == "Color" else 4
                nc = self.count_of(col, "color", k) or 0
                ci = v.get("colorIndex") or []
                per_vertex = v.get("colorPerVertex", True)
                if ci:
                    if max(ci) >= nc:
                        rep.error("E_INDEX_RANGE", el, "colorIndex refers to colour %d but there are %d" % (max(ci), nc))
                elif per_vertex and nc < n:
                    rep.error("E_COLOR_COUNT", el, "%d colours for %d points (colorPerVertex, no colorIndex)" % (nc, n))
            tc = self.child(el, "TextureCoordinate")
            if tc is not None and n and not v.get("texCoordIndex") and tag != "IndexedQuadSet":
                nt = self.count_of(tc, "point", 2) or 0
                if nt < n:
                    rep.error("E_TEXCOORD_COUNT", el, "%d texture coordinates for %d points (no texCoordIndex)" % (nt, n))
        if tag in ("LineSet",):
            coord = self.child(el, "Coordinate", "CoordinateDouble")
            n = self.count_of(coord, "point", 3) or 0
            vc = v.get("vertexCount") or []
            if any(c < 2 for c in vc):
                rep.error("E_VERTEXCOUNT", el, "every LineSet vertexCount must be ≥ 2")
            if sum(vc) > n:
                rep.error("E_VERTEXCOUNT", el, "vertexCount sums to %d but the Coordinate has %d points" % (sum(vc), n))
            col = self.child(el, "Color", "ColorRGBA")
            if col is not None:
                nc = self.count_of(col, "color", 3 if col.tag == "Color" else 4) or 0
                if nc < sum(vc):
                    rep.error("E_COLOR_COUNT", el, "%d colours for %d line vertices" % (nc, sum(vc)))
        if tag == "PointSet":
            coord = self.child(el, "Coordinate", "CoordinateDouble")
            n = self.count_of(coord, "point", 3) or 0
            col = self.child(el, "Color", "ColorRGBA")
            if col is not None:
                nc = self.count_of(col, "color", 3 if col.tag == "Color" else 4) or 0
                if nc != n:
                    rep.error("E_COLOR_COUNT", el, "PointSet has %d points and %d colours" % (n, nc))
        if tag == "ElevationGrid":
            xd, zd = int(v.get("xDimension") or 0), int(v.get("zDimension") or 0)
            h = v.get("height") or []
            entry = {"DEF": el.attrs.get("DEF"), "line": el.line, "expected": xd * zd, "given": len(h)}
            F["elevationGrids"].append(entry)
            if len(h) > xd * zd:
                rep.error("E_ELEVATION", el, "%d heights for a %d × %d grid" % (len(h), xd, zd))
            elif len(h) < xd * zd:
                rep.warn("W_ELEVATION_UNDERFILLED", el,
                         "%d of %d heights in the file (X_ITE draws the rest as 0 until something sets them; "
                         "importers see a flat or partial grid)" % (len(h), xd * zd))
            col = self.child(el, "Color", "ColorRGBA")
            if col is not None and len(h) == xd * zd:
                nc = self.count_of(col, "color", 3 if col.tag == "Color" else 4) or 0
                need = xd * zd if v.get("colorPerVertex", True) else (xd - 1) * (zd - 1)
                if nc and nc != need:
                    rep.error("E_COLOR_COUNT", el, "ElevationGrid needs %d colours, has %d" % (need, nc))
        if tag == "Script":
            body = el.text.strip()
            if not body and "url" not in el.attrs:
                rep.error("E_SCRIPT", el, "Script has neither a url nor inline source")
            if body and not re.match(r"^(ecmascript|javascript|vrmlscript):", body):
                rep.warn("W_SCRIPT_SCHEME", el, "inline Script source should start with ecmascript:")
            if "set_height" in body or "set_point" in body or ".point" in body or "set_coordIndex" in body:
                F["scriptsDriveGeometry"] += 1
        if tag == "ShaderPart":
            t = el.attrs.get("type", "VERTEX")
            if t not in ("VERTEX", "FRAGMENT", "GEOMETRY", "TESS_CONTROL", "TESS_EVALUATION"):
                rep.error("E_SHADERPART", el, "ShaderPart type %r" % t)
            if not el.text.strip() and "url" not in el.attrs:
                rep.error("E_SHADERPART", el, "ShaderPart has no source")
        if tag == "ComposedShader" and el.attrs.get("language", "") not in ("GLSL", ""):
            rep.warn("W_SHADER_LANG", el, "X_ITE runs GLSL ComposedShaders; language=%r" % el.attrs.get("language"))
        if tag in ("OrientationInterpolator", "PositionInterpolator", "ColorInterpolator", "ScalarInterpolator",
                   "CoordinateInterpolator", "NormalInterpolator", "PositionInterpolator2D"):
            key = v.get("key") or []
            kv = v.get("keyValue") or []
            if key and any(b < a for a, b in zip(key, key[1:])):
                rep.error("E_KEY_ORDER", el, "key must be non-decreasing")
            if key and kv and tag not in ("CoordinateInterpolator", "NormalInterpolator") and len(kv) != len(key):
                rep.error("E_KEYVALUE", el, "%d keys but %d keyValues" % (len(key), len(kv)))
            if key and kv and tag in ("CoordinateInterpolator", "NormalInterpolator") and len(kv) % len(key):
                rep.error("E_KEYVALUE", el, "keyValue (%d) is not a whole multiple of key (%d)" % (len(kv), len(key)))
        if tag == "Viewpoint":
            fov = v.get("fieldOfView")
            if fov is not None and not (0 < fov < 3.14159):
                rep.error("E_FOV", el, "fieldOfView %.3f must be in (0, π)" % fov)


def iter_tree(el: El):
    yield el
    for c in el.children:
        yield from iter_tree(c)


def validate_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return validate_text(f.read(), filename=path)
