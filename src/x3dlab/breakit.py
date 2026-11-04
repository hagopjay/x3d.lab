"""Break it: inject one known defect into a valid scene and see which check catches it.

The layers, in the order a document meets them:
  xml       the XML parser (is it well-formed at all?)
  xsd       the X3D XML Schema for the declared version (names, types, value patterns)
  semantic  x3d_mcp's checks the schema cannot express (DEF/USE, ROUTE types, interpolators, profiles, ...)
  nobody    valid by every check, yet wrong on screen: only rendering it shows the problem

`expect` is what the pinned validator does today; `run_all()` re-measures it, so a change in the
validator shows up as a mismatch (handy as a tiny regression test).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from . import aio
from .events import bus
from .scenes import LANTERN

LAYER_NAMES = {"xml": "XML parser", "xsd": "XSD schema", "semantic": "Semantic check", "nobody": "Nobody (only rendering shows it)"}


@dataclass
class Defect:
    id: str
    title: str
    lesson: str
    expect: str           # xml | xsd | semantic | nobody

    def apply(self, xml: str) -> str:
        return _APPLY[self.id](xml)


def _sub(pattern, repl, xml, count=1):
    out, n = re.subn(pattern, repl, xml, count=count, flags=re.S)
    if n == 0:
        raise ValueError("defect could not be applied: pattern not found: %s" % pattern)
    return out


def _move_before_first_transform(xml: str, block_pat: str) -> str:
    m = re.search(block_pat, xml, re.S)
    if not m:
        raise ValueError("block not found")
    block = m.group(0)
    xml = xml.replace(block, "", 1)
    return xml.replace('<Transform DEF="Spinner"', block.strip() + '\n    <Transform DEF="Spinner"', 1)


_APPLY = {
    "python_bool": lambda x: _sub(r'<Box size="1 1.4 1"/>', '<Box size="1 1.4 1" solid="True"/>', x),
    "unknown_node": lambda x: _sub(r"</Scene>", "  <Banana/>\n  </Scene>", x),
    "field_typo": lambda x: _sub(r'diffuseColor="0.95 0.55 0.1"', 'difuseColor="0.95 0.55 0.1"', x),
    "short_colour": lambda x: _sub(r'diffuseColor="0.95 0.55 0.1"', 'diffuseColor="0.95 0.55"', x),
    "colour_range": lambda x: _sub(r'diffuseColor="0.95 0.55 0.1"', 'diffuseColor="1.8 0.55 0.1"', x),
    "missing_def": lambda x: _sub(r'<Shape USE="Body"/>', '<Shape USE="Bodyy"/>', x),
    "use_before_def": lambda x: _move_before_first_transform(x, r'\s*<Transform translation="2.5 0.7 0"[^>]*>\s*<Shape USE="Body"/>\s*</Transform>'),
    "route_types": lambda x: _sub(r'fromNode="Clock" fromField="fraction_changed" toNode="Turn" toField="set_fraction"',
                                  'fromNode="Clock" fromField="fraction_changed" toNode="Spinner" toField="set_rotation"', x),
    "route_first": lambda x: _move_before_first_transform(x, r'\s*<ROUTE fromNode="Turn"[^>]*/>'),
    "key_mismatch": lambda x: _sub(r'keyValue="0 1 0 0 0 1 0 3.14159 0 1 0 6.28318"', 'keyValue="0 1 0 0 0 1 0 3.14159"', x),
    "duplicate_def": lambda x: _sub(r'<Material diffuseColor="0.6 0.1 0.1"/>', '<Material DEF="Glow" diffuseColor="0.6 0.1 0.1"/>', x),
    "pbr_no_component": lambda x: _sub(r'<Material DEF="Glow" diffuseColor="0.95 0.55 0.1" emissiveColor="0.3 0.12 0"/>',
                                       '<PhysicalMaterial DEF="Glow" baseColor="0.95 0.55 0.1" metallic="0.2" roughness="0.4"/>', x),
    "wrong_container": lambda x: _sub(r'<Material DEF="Glow" diffuseColor="0.95 0.55 0.1" emissiveColor="0.3 0.12 0"/>',
                                      '<PhysicalMaterial DEF="Glow" baseColor="0.95 0.55 0.1"><ImageTexture url=\'"lantern.png"\'/></PhysicalMaterial>',
                                      x.replace('profile="Immersive"', 'profile="Full"')),
    "missing_profile": lambda x: _sub(r' profile="Immersive"', "", x),
    "unclosed_tag": lambda x: _sub(r"</Scene>", "", x),
    "invisible": lambda x: _sub(r'<Material DEF="Glow" diffuseColor="0.95 0.55 0.1" emissiveColor="0.3 0.12 0"/>',
                                '<Material DEF="Glow" diffuseColor="0.95 0.55 0.1" transparency="1"/>',
                                _sub(r'<Material diffuseColor="0.6 0.1 0.1"/>', '<Material diffuseColor="0.6 0.1 0.1" transparency="1"/>',
                                     _sub(r'<Material diffuseColor="0.2 0.5 0.25"/>', '<Material diffuseColor="0.2 0.5 0.25" transparency="1"/>', x))),
    "control": lambda x: x,
}

CATALOG = [
    Defect("python_bool", "Python-style boolean", "X3D booleans are lowercase: true/false, never True/False.", "xsd"),
    Defect("unknown_node", "A node that does not exist", "Models invent node names; the schema knows every real one.", "xsd"),
    Defect("field_typo", "Misspelled field", "difuseColor is not a field of Material. The schema lists the allowed attributes.", "xsd"),
    Defect("short_colour", "Colour with two numbers", "SFColor needs exactly three numbers; the schema checks the pattern.", "xsd"),
    Defect("colour_range", "Colour above 1.0", "Colour components must be in [0,1].", "xsd"),
    Defect("missing_def", "USE of a name nobody DEFined", "USE must point at an existing DEF. The schema can't see names; the semantic check can.", "semantic"),
    Defect("use_before_def", "USE before its DEF", "A USE must come after its DEF in document order.", "semantic"),
    Defect("route_types", "ROUTE between mismatched types", "fraction_changed is SFFloat, set_rotation wants SFRotation.", "semantic"),
    Defect("route_first", "ROUTE before the node it names", "A ROUTE must follow the DEFs it refers to.", "semantic"),
    Defect("key_mismatch", "Interpolator with too few values", "Three keys need three rotations (12 numbers), not two.", "semantic"),
    Defect("duplicate_def", "The same DEF name twice", "DEF names are XML IDs, so the schema rejects duplicates.", "xsd"),
    Defect("pbr_no_component", "PBR material outside the profile", "PhysicalMaterial is not in Immersive; declare the component (repair_x3d adds it).", "semantic"),
    Defect("wrong_container", "Texture in the wrong field", "A texture inside PhysicalMaterial needs containerField='baseTexture'.", "semantic"),
    Defect("missing_profile", "No profile attribute", "Every X3D document declares a profile.", "xsd"),
    Defect("unclosed_tag", "Unclosed tag", "Not even XML: the parser stops before any X3D check runs.", "xml"),
    Defect("invisible", "Valid but invisible", "Every material fully transparent: all checks pass and the screen shows nothing. Valid is not visible.", "nobody"),
    Defect("control", "No defect (control)", "Nothing was changed. A good test set includes clean controls.", "nobody"),
]
BY_ID = {d.id: d for d in CATALOG}


@dataclass
class Outcome:
    id: str
    title: str
    expect: str
    caught_by: str
    match: bool
    first_error: str
    rules: list
    repaired: bool | None
    repair_changes: int
    xml: str
    lesson: str

    def row(self) -> dict:
        d = asdict(self)
        d.pop("xml")
        return d


async def run_one(client, defect_id: str, base: str = LANTERN, repair: bool = True, job: str | None = None) -> Outcome:
    d = BY_ID[defect_id]
    xml = d.apply(base)
    v = await client.verdict(xml, job=job)
    layers = v.layers()
    caught = layers[0] if layers else "nobody"
    if caught == "input":
        caught = "xml"
    rules = sorted({i.rule for i in v.errors})
    repaired, n = None, 0
    if repair and not v.ok:
        fixed, changes, _ = await client.repair(xml, job=job)
        n = len(changes)
        repaired = (await client.verdict(fixed, job=job)).ok if changes else False
    out = Outcome(d.id, d.title, d.expect, caught, caught == d.expect, (v.errors[0].message if v.errors else "")[:200],
                  rules, repaired, n, xml, d.lesson)
    bus.emit("break-it %s: caught by %s%s" % (d.id, caught, "" if out.match else " (expected %s)" % d.expect),
             level="INFO" if out.match else "WARN", src="breakit", kind="breakit", job=job, data=out.row())
    return out


async def run_all_async(client, base: str = LANTERN, repair: bool = True) -> list[Outcome]:
    return [await run_one(client, d.id, base, repair) for d in CATALOG]


def run_all(client, base: str = LANTERN, repair: bool = True) -> list[Outcome]:
    return aio.run(run_all_async(client, base, repair))


# ---------------------------------------------------------------------- the notebook game
def game(lab):
    """Pick a defect, guess the layer, reveal. Plus a 'run all' table."""
    import ipywidgets as W
    from IPython.display import HTML, display

    from .ui import esc, xite_viewer_html
    pick = W.Dropdown(options=[(d.title, d.id) for d in CATALOG], description="defect", layout=W.Layout(width="420px"))
    guess = W.ToggleButtons(options=[(LAYER_NAMES[k].split(" (")[0], k) for k in ("xml", "xsd", "semantic", "nobody")],
                            description="your guess", style={"button_width": "130px"})
    go = W.Button(description="Inject & check", button_style="primary")
    every = W.Button(description="Run all defects")
    out = W.Output()
    score = {"right": 0, "tries": 0}

    def reveal(_):
        out.clear_output()
        o = lab.breakit(pick.value)
        score["tries"] += 1
        right = guess.value == o.caught_by
        score["right"] += right
        with out:
            display(HTML(
                "<div style='font:14px system-ui'><b style='color:%s'>%s</b> Caught by <b>%s</b>%s.<br>%s%s"
                "<div style='color:#888;font-size:12px'>score %d/%d · first error: %s</div></div>"
                % ("#1d6b3a" if right else "#c62828", "Right!" if right else "Not quite.", esc(LAYER_NAMES[o.caught_by]),
                   (" (" + esc(", ".join(o.rules)) + ")") if o.rules else "", esc(o.lesson),
                   " <i>repair_x3d fixes this one by itself.</i>" if o.repaired else "",
                   score["right"], score["tries"], esc(o.first_error[:160]) or "none")))
            if o.caught_by != "xml":
                display(HTML(xite_viewer_html(o.xml, height=260)))

    def run_all_(_):
        out.clear_output()
        rows = run_all(lab.client)
        with out:
            display(HTML("<table style='font-size:12.5px'><tr><th align=left>defect</th><th>caught by</th><th>expected</th>"
                         "<th>rules</th><th>repair_x3d fixes it</th></tr>" + "".join(
                             "<tr><td>%s</td><td>%s</td><td>%s</td><td><code>%s</code></td><td>%s</td></tr>"
                             % (esc(o.title), esc(o.caught_by), esc(o.expect) + ("" if o.match else " ⚠"), esc(", ".join(o.rules)),
                                {True: "yes", False: "no", None: "—"}[o.repaired]) for o in rows) + "</table>"))

    go.on_click(reveal)
    every.on_click(run_all_)
    display(W.VBox([pick, guess, W.HBox([go, every]), out]))
