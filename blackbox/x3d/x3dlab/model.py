"""A small, schema-typed X3D scene builder (stdlib only).

Every field is formatted from its X3D type (looked up in schema/x3d40.json, generated from X3DUOM 4.0),
so a typo'd field name or a wrong arity fails when the scene is built, not when a browser loads it.

    from x3dlab.model import N, Doc, Route, Field
    doc = Doc("Interchange", title="hello")
    doc.add(N("Shape", N("Appearance", N("Material", diffuseColor=(1, .3, .1))), N("Sphere", radius=2)))
    doc.xml()      # XML encoding (.x3d)
    doc.vrml()     # ClassicVRML encoding (.x3dv)
    doc.json()     # JSON encoding (.x3dj)

Three encodings, one tree. X_ITE reads all three.
"""
from __future__ import annotations

import json
import math
from typing import Any, Iterable
from xml.sax.saxutils import escape, quoteattr

from .schema import SCHEMA, field_spec, node_spec

# Fields whose X3D type is an MF of tuples: how many numbers make one value.
TUPLE = {"SFVec2f": 2, "SFVec2d": 2, "SFVec3f": 3, "SFVec3d": 3, "SFVec4f": 4, "SFVec4d": 4, "SFColor": 3,
         "SFColorRGBA": 4, "SFRotation": 4, "SFMatrix3f": 9, "SFMatrix4f": 16}
MF_TUPLE = {"MF" + k[2:]: v for k, v in TUPLE.items()}


def _num(v: float) -> str:
    if isinstance(v, bool):
        raise TypeError("bool where a number was expected")
    if isinstance(v, int):
        return str(v)
    if math.isfinite(v) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    s = "%.6g" % v
    return s


def _flat(v) -> list:
    out = []
    for x in v:
        if isinstance(x, (list, tuple)):
            out.extend(_flat(x))
        else:
            out.append(x)
    return out


class BuildError(ValueError):
    pass


def qstr(s: str) -> str:
    """An X3D/VRML quoted string: only the double quote and the backslash are escaped (newlines stay raw)."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def format_value(ftype: str, value: Any, *, vrml: bool = False) -> str:
    """Format a Python value as the X3D text of a field of type ``ftype``."""
    if ftype in ("SFBool",):
        return ("TRUE" if value else "FALSE") if vrml else ("true" if value else "false")
    if ftype == "MFBool":
        return " ".join(("TRUE" if b else "FALSE") if vrml else ("true" if b else "false") for b in value)
    if ftype == "SFString":
        return qstr(value) if vrml else str(value)
    if ftype == "MFString":
        items = [value] if isinstance(value, str) else list(value)
        return " ".join(qstr(s) for s in items)
    if ftype in ("SFInt32", "SFFloat", "SFDouble", "SFTime"):
        return _num(value)
    if ftype in ("MFInt32", "MFFloat", "MFDouble", "MFTime"):
        return " ".join(_num(x) for x in _flat(value))
    if ftype in TUPLE:
        vals = _flat(value)
        if len(vals) != TUPLE[ftype]:
            raise BuildError("%s needs %d numbers, got %d" % (ftype, TUPLE[ftype], len(vals)))
        return " ".join(_num(x) for x in vals)
    if ftype in MF_TUPLE:
        k = MF_TUPLE[ftype]
        vals = _flat(value)
        if len(vals) % k:
            raise BuildError("%s needs a multiple of %d numbers, got %d" % (ftype, k, len(vals)))
        groups = [" ".join(_num(x) for x in vals[i:i + k]) for i in range(0, len(vals), k)]
        return (", " if not vrml else ", ").join(groups)
    if ftype == "SFImage":
        return " ".join(str(x) for x in _flat(value))
    raise BuildError("cannot format field type %s" % ftype)


class N:
    """One X3D node. Positional args are child nodes; keyword args are fields.

    ``DEF``/``USE`` name the node; ``cf`` overrides the containerField it is put into.
    Field names that clash with Python keywords take a trailing underscore (``class_``).
    """

    def __init__(self, type_: str, *children: "N | Field | Text", DEF: str | None = None, USE: str | None = None,
                 cf: str | None = None, IS: dict | None = None, **fields: Any):
        spec = node_spec(type_)
        if spec is None:
            import difflib
            close = difflib.get_close_matches(type_, list(SCHEMA["nodes"]), n=1)
            raise BuildError("unknown X3D node type %r%s" % (type_, (" (did you mean %r?)" % close[0]) if close else ""))
        self.type = type_
        self.DEF, self.USE, self.cf = DEF, USE, cf
        self.fields: dict[str, Any] = {}
        self.children: list = []
        self.decls: list[Field] = []   # <field> declarations (Script, ComposedShader, ShaderProgram)
        self.source: str | None = None  # CDATA body (Script url / ShaderPart)
        self.IS: dict[str, str] = dict(IS or {})  # nodeField -> protoField, inside a ProtoBody
        for k, v in fields.items():
            k = k.rstrip("_")
            if field_spec(type_, k) is None:
                import difflib
                close = difflib.get_close_matches(k, list(spec["fields"]), n=1)
                raise BuildError("%s has no field %r%s" % (type_, k, (" (did you mean %r?)" % close[0]) if close else ""))
            self.fields[k] = v
        for c in children:
            self.add(c)

    # -- tree ----------------------------------------------------------------------------------
    def add(self, *items) -> "N":
        for c in items:
            if c is None:
                continue
            if isinstance(c, Field):
                self.decls.append(c)
            elif isinstance(c, Text):
                self.source = c.body
            elif isinstance(c, (N, ProtoInstance)):
                self.children.append(c)
            elif isinstance(c, (list, tuple)):
                self.add(*c)
            else:
                raise BuildError("cannot add %r to %s" % (c, self.type))
        return self

    def container_of(self, child: "N") -> str:
        """The field ``child`` goes into: its explicit cf, else its default if this node has it, else the
        one SFNode/MFNode field of this node that accepts it."""
        if getattr(child, "cf", None):
            return child.cf
        if isinstance(child, ProtoInstance):
            return "children"
        default = SCHEMA["nodes"][child.type]["containerField"] or "children"
        if field_spec(self.type, default) is not None:
            return default
        for name, (ftype, _a, _d) in SCHEMA["nodes"][self.type]["fields"].items():
            if ftype in ("SFNode", "MFNode") and name != "metadata":
                return name
        raise BuildError("%s cannot hold a %s" % (self.type, child.type))

    def cdata(self) -> str:
        """Script source with its ``ecmascript:`` scheme, or ShaderPart GLSL as a ``data:`` URL (X_ITE's form)."""
        if self.type == "Script":
            return "ecmascript:" + self.source
        if self.type == "ShaderPart":
            return "data:x-shader/x-%s,%s" % (self.fields.get("type", "VERTEX").lower(), self.source)
        return self.source

    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, N):
                yield from c.walk()

    # -- encodings -----------------------------------------------------------------------------
    def to_xml(self, parent: "N | None" = None, depth: int = 0) -> str:
        pad = "  " * depth
        attrs = []
        if self.DEF:
            attrs.append("DEF=%s" % quoteattr(self.DEF))
        if self.USE:
            attrs.append("USE=%s" % quoteattr(self.USE))
        if parent is not None:
            cf = parent.container_of(self)
            if cf != (SCHEMA["nodes"][self.type]["containerField"] or "children"):
                attrs.append("containerField=%s" % quoteattr(cf))
        for k, v in self.fields.items():
            ftype = field_spec(self.type, k)[0]
            attrs.append("%s=%s" % (k, quoteattr(format_value(ftype, v))))
        head = "%s<%s%s" % (pad, self.type, (" " + " ".join(attrs)) if attrs else "")
        inner = []
        if self.IS:
            inner.append("%s  <IS>\n%s\n%s  </IS>" % (pad, "\n".join(
                "%s    <connect nodeField=%s protoField=%s/>" % (pad, quoteattr(a), quoteattr(b))
                for a, b in self.IS.items()), pad))
        inner += [d.to_xml(depth + 1) for d in self.decls]
        inner += [c.to_xml(self, depth + 1) if isinstance(c, N) else c.to_xml(depth + 1) for c in self.children]
        if self.source is not None:
            inner.append("%s  <![CDATA[%s]]>" % (pad, self.cdata().replace("]]>", "]]]]><![CDATA[>")))
        if not inner:
            return head + "/>"
        return head + ">\n" + "\n".join(inner) + "\n%s</%s>" % (pad, self.type)

    def to_vrml(self, depth: int = 0) -> str:
        pad = "  " * depth
        if self.USE:
            return "USE %s" % self.USE
        lines = []
        for d in self.decls:
            lines.append(pad + "  " + d.to_vrml())
        for a, b in self.IS.items():
            lines.append("%s  %s IS %s" % (pad, a, b))
        for k, v in self.fields.items():
            ftype = field_spec(self.type, k)[0]
            text = format_value(ftype, v, vrml=True)
            if ftype.startswith("MF") and ftype not in ("MFString",):
                text = "[ %s ]" % text
            elif ftype == "MFString":
                text = "[ %s ]" % text
            lines.append("%s  %s %s" % (pad, k, text))
        groups: dict[str, list[N]] = {}
        for c in self.children:
            groups.setdefault(self.container_of(c), []).append(c)
        for cf, kids in groups.items():
            ftype = field_spec(self.type, cf)[0]
            if ftype == "MFNode":
                body = "\n".join(pad + "    " + k.to_vrml(depth + 2) for k in kids)
                lines.append("%s  %s [\n%s\n%s  ]" % (pad, cf, body, pad))
            else:
                lines.append("%s  %s %s" % (pad, cf, kids[0].to_vrml(depth + 1)))
        if self.source is not None:
            lines.append("%s  url [ %s ]" % (pad, qstr(self.cdata())))
        name = ("DEF %s " % self.DEF) if self.DEF else ""
        if not lines:
            return "%s%s { }" % (name, self.type)
        return "%s%s {\n%s\n%s}" % (name, self.type, "\n".join(lines), pad)

    def to_json(self) -> dict:
        body: dict[str, Any] = {}
        if self.DEF:
            body["@DEF"] = self.DEF
        if self.USE:
            body["@USE"] = self.USE
        for k, v in self.fields.items():
            ftype = field_spec(self.type, k)[0]
            body["@" + k] = _json_value(ftype, v)
        if self.IS:
            body["IS"] = {"connect": [{"@nodeField": a, "@protoField": b} for a, b in self.IS.items()]}
        if self.decls:
            body["field"] = [d.to_json() for d in self.decls]
        groups: dict[str, list[N]] = {}
        for c in self.children:
            groups.setdefault(self.container_of(c), []).append(c)
        for cf, kids in groups.items():
            ftype = field_spec(self.type, cf)[0]
            enc = [k.to_json() for k in kids]
            body["-" + cf] = enc if ftype == "MFNode" else enc[0]
        if self.source is not None:
            body["#sourceCode"] = self.cdata().split("\n")
        return {self.type: body}


def _json_value(ftype: str, v):
    if ftype in ("SFBool",):
        return bool(v)
    if ftype == "MFBool":
        return [bool(x) for x in v]
    if ftype == "SFString":
        return str(v)
    if ftype == "MFString":
        return [v] if isinstance(v, str) else list(v)
    if ftype.startswith("SF") and ftype not in TUPLE:
        return v
    return _flat(v) if isinstance(v, (list, tuple)) else v


class Field:
    """A user-defined field: ``<field name type accessType value/>`` on Script, ComposedShader or a ProtoInterface."""

    def __init__(self, name: str, type_: str, access: str, value: Any = None, *nodes: N):
        self.name, self.type, self.access, self.value, self.nodes = name, type_, access, value, list(nodes)

    def to_xml(self, depth: int) -> str:
        pad = "  " * depth
        a = "name=%s type=%s accessType=%s" % (quoteattr(self.name), quoteattr(self.type), quoteattr(self.access))
        if self.value is not None and self.type not in ("SFNode", "MFNode"):
            a += " value=%s" % quoteattr(format_value(self.type, self.value))
        if self.nodes:
            inner = "\n".join(n.to_xml(None, depth + 1) for n in self.nodes)
            return "%s<field %s>\n%s\n%s</field>" % (pad, a, inner, pad)
        return "%s<field %s/>" % (pad, a)

    def to_vrml(self) -> str:
        val = ""
        if self.value is not None and self.type not in ("SFNode", "MFNode"):
            val = " " + format_value(self.type, self.value, vrml=True)
            if self.type.startswith("MF"):
                val = " [%s ]" % val
        if self.nodes:
            val = " " + " ".join(n.to_vrml() for n in self.nodes)
        return "%s %s %s%s" % (self.access, self.type, self.name, val)

    def to_json(self) -> dict:
        d = {"@name": self.name, "@type": self.type, "@accessType": self.access}
        if self.value is not None and self.type not in ("SFNode", "MFNode"):
            d["@value"] = _json_value(self.type, self.value)
        if self.nodes:
            d["-children"] = [n.to_json() for n in self.nodes]
        return d


class Text:
    """CDATA body: Script source or ShaderPart GLSL."""

    def __init__(self, body: str):
        self.body = body


class Route:
    def __init__(self, from_node: str, from_field: str, to_node: str, to_field: str):
        self.f = (from_node, from_field, to_node, to_field)

    def to_xml(self, depth: int) -> str:
        a, b, c, d = self.f
        return '%s<ROUTE fromNode=%s fromField=%s toNode=%s toField=%s/>' % (
            "  " * depth, quoteattr(a), quoteattr(b), quoteattr(c), quoteattr(d))

    def to_vrml(self) -> str:
        a, b, c, d = self.f
        return "ROUTE %s.%s TO %s.%s" % (a, b, c, d)

    def to_json(self) -> dict:
        a, b, c, d = self.f
        return {"ROUTE": {"@fromNode": a, "@fromField": b, "@toNode": c, "@toField": d}}


class Proto:
    """ProtoDeclare with an interface and a body; instantiate with ``Proto.instance(DEF=..., **fieldValues)``."""

    def __init__(self, name: str, interface: Iterable[Field], body: Iterable, appinfo: str = ""):
        self.name, self.interface, self.body, self.appinfo = name, list(interface), list(body), appinfo
        self._types = {f.name: f.type for f in self.interface}

    def to_xml(self, depth: int) -> str:
        pad = "  " * depth
        iface = "\n".join(f.to_xml(depth + 2) for f in self.interface)
        body = "\n".join(_item_xml(b, depth + 2) for b in self.body)
        app = (" appinfo=%s" % quoteattr(self.appinfo)) if self.appinfo else ""
        return ("%s<ProtoDeclare name=%s%s>\n%s  <ProtoInterface>\n%s\n%s  </ProtoInterface>\n"
                "%s  <ProtoBody>\n%s\n%s  </ProtoBody>\n%s</ProtoDeclare>") % (
            pad, quoteattr(self.name), app, pad, iface, pad, pad, body, pad, pad)

    def to_vrml(self) -> str:
        iface = "\n".join("  " + f.to_vrml() for f in self.interface)
        body = "\n".join("  " + _item_vrml(b, 1) for b in self.body)
        return "PROTO %s [\n%s\n] {\n%s\n}" % (self.name, iface, body)

    def to_json(self) -> dict:
        return {"ProtoDeclare": {"@name": self.name, **({"@appinfo": self.appinfo} if self.appinfo else {}),
                                 "ProtoInterface": {"field": [f.to_json() for f in self.interface]},
                                 "ProtoBody": {"-children": [_item_json(b) for b in self.body]}}}

    def instance(self, DEF: str | None = None, **values) -> "ProtoInstance":
        for k in values:
            if k not in self._types:
                raise BuildError("PROTO %s has no field %r" % (self.name, k))
        return ProtoInstance(self, DEF, values)


class ProtoInstance:
    def __init__(self, proto: Proto, DEF, values, cf=None):
        self.proto, self.DEF, self.values, self.cf = proto, DEF, values, cf

    def to_xml(self, depth: int) -> str:
        pad = "  " * depth
        d = (" DEF=%s" % quoteattr(self.DEF)) if self.DEF else ""
        fv = "\n".join('%s  <fieldValue name=%s value=%s/>' % (
            pad, quoteattr(k), quoteattr(format_value(self.proto._types[k], v))) for k, v in self.values.items())
        return "%s<ProtoInstance name=%s%s>\n%s\n%s</ProtoInstance>" % (pad, quoteattr(self.proto.name), d, fv, pad)

    def to_vrml(self, depth: int = 0) -> str:
        d = ("DEF %s " % self.DEF) if self.DEF else ""
        parts = []
        for k, v in self.values.items():
            t = self.proto._types[k]
            text = format_value(t, v, vrml=True)
            parts.append("%s %s" % (k, ("[ %s ]" % text) if t.startswith("MF") else text))
        return "%s%s { %s }" % (d, self.proto.name, " ".join(parts))

    def to_json(self) -> dict:
        body = {"@name": self.proto.name}
        if self.DEF:
            body["@DEF"] = self.DEF
        body["fieldValue"] = [{"@name": k, "@value": _json_value(self.proto._types[k], v)} for k, v in self.values.items()]
        return {"ProtoInstance": body}


def _item_xml(item, depth):
    if isinstance(item, N):
        return item.to_xml(None, depth)
    return item.to_xml(depth)


def _item_vrml(item, depth):
    if isinstance(item, N):
        return item.to_vrml(depth)
    return item.to_vrml()


def _item_json(item):
    return item.to_json()


class Doc:
    """An X3D 4.0 document: head (components, meta) + Scene items (nodes, ROUTEs, PROTOs, instances)."""

    def __init__(self, profile: str = "Interchange", *, title: str, description: str = "",
                 components: Iterable[tuple[str, int]] = (), meta: dict | None = None, version: str = "4.0"):
        self.profile, self.version = profile, version
        self.components = list(components)
        self.meta = {"title": title, "description": description, "generator": "x3dlab (BLACKBOX X3D Lab)",
                     "license": "CC0-1.0", **(meta or {})}
        self.items: list = []

    def add(self, *items) -> "Doc":
        for i in items:
            if isinstance(i, (list, tuple)):
                self.add(*i)
            elif i is not None:
                self.items.append(i)
        return self

    def nodes(self):
        """Every N in the scene, including inside PROTO bodies."""
        for i in self.items:
            if isinstance(i, N):
                yield from i.walk()
            elif isinstance(i, Proto):
                for b in i.body:
                    if isinstance(b, N):
                        yield from b.walk()

    def xml(self) -> str:
        head = ['<?xml version="1.0" encoding="UTF-8"?>',
                '<!DOCTYPE X3D PUBLIC "ISO//Web3D//DTD X3D 4.0//EN" "https://www.web3d.org/specifications/x3d-4.0.dtd">',
                '<X3D profile=%s version=%s xmlns:xsd="http://www.w3.org/2001/XMLSchema-instance" '
                'xsd:noNamespaceSchemaLocation="https://www.web3d.org/specifications/x3d-4.0.xsd">' % (
                    quoteattr(self.profile), quoteattr(self.version)),
                "  <head>"]
        for name, level in self.components:
            head.append('    <component name=%s level="%d"/>' % (quoteattr(name), level))
        for k, v in self.meta.items():
            if v:
                head.append("    <meta name=%s content=%s/>" % (quoteattr(k), quoteattr(str(v))))
        head.append("  </head>")
        head.append("  <Scene>")
        head += [_item_xml(i, 2) for i in self.items]
        head += ["  </Scene>", "</X3D>", ""]
        return "\n".join(head)

    def vrml(self) -> str:
        out = ["#X3D V%s utf8" % self.version, "", "PROFILE %s" % self.profile]
        for name, level in self.components:
            out.append("COMPONENT %s:%d" % (name, level))
        for k, v in self.meta.items():
            if v:
                out.append("META %s %s" % (json.dumps(k), json.dumps(str(v))))
        out.append("")
        out += [_item_vrml(i, 0) for i in self.items]
        return "\n".join(out) + "\n"

    def json(self) -> str:
        doc = {"X3D": {"encoding": "UTF-8", "@profile": self.profile, "@version": self.version,
                       "@xsd:noNamespaceSchemaLocation": "https://www.web3d.org/specifications/x3d-4.0.xsd",
                       "JSON schema": "https://www.web3d.org/specifications/x3d-4.0-JSONSchema.autogenerated.json",
                       "head": {"component": [{"@name": n, "@level": l} for n, l in self.components],
                                "meta": [{"@name": k, "@content": str(v)} for k, v in self.meta.items() if v]},
                       "Scene": {"-children": [_item_json(i) for i in self.items]}}}
        return json.dumps(doc, indent=1)
