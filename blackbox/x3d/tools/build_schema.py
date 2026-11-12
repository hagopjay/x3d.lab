"""Regenerate x3dlab/schema/x3d40.json from the Web3D x3d.py package (itself generated from X3DUOM 4.0).

    pip install x3d && python x3d/tools/build_schema.py

The runtime validator only reads the JSON, so x3dlab never needs x3d.py installed.
"""
import inspect
import json
import pathlib
import x3d

OUT = pathlib.Path(__file__).resolve().parents[1] / "x3dlab" / "schema" / "x3d40.json"
SKIP_FIELDS = {"DEF", "USE", "IS", "class_", "id_", "style_", "_parentNode", "_containerField"}


def default(v):
    if isinstance(v, tuple):
        return [default(x) for x in v]
    if isinstance(v, list):
        return [default(x) for x in v]
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


nodes = {}
statements = {}
for name, cls in inspect.getmembers(x3d, inspect.isclass):
    if not hasattr(cls, "FIELD_DECLARATIONS") or name.startswith("_"):
        continue
    try:
        decl = cls.FIELD_DECLARATIONS()
    except Exception:
        continue
    fields, container = {}, None
    for fname, fdefault, ftype, faccess, owner in decl:
        if fname == "_containerField":
            container = fdefault
            continue
        if fname in SKIP_FIELDS and owner in ("X3DNode", "_X3DNode", "_X3DStatement", "X3DStatement"):
            continue
        if fname in ("DEF", "USE", "IS", "_parentNode"):
            continue
        # x3d.py renames fields that clash with Python/HTML names (FontStyle.style -> style_)
        fields[fname.rstrip("_")] = [ftype.__name__, faccess.__name__, default(fdefault)]
    base = [b.__name__ for b in cls.__mro__[1:] if b.__name__.startswith("_X3D") or b.__name__.startswith("X3D")]
    is_statement = any(b.__name__ == "_X3DStatement" for b in cls.__mro__)
    entry = {"fields": fields, "containerField": container, "types": [b.lstrip("_") for b in base]}
    (statements if is_statement else nodes)[name] = entry

payload = {
    "source": "x3d.py %s (generated from X3DUOM 4.0)" % getattr(x3d, "__version__", "4.0.65.8"),
    "nodes": dict(sorted(nodes.items())),
    "statements": dict(sorted(statements.items())),
}
OUT.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=False))
print("nodes", len(nodes), "statements", len(statements), "->", OUT, OUT.stat().st_size, "bytes")
