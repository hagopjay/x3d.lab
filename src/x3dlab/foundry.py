"""The Foundry: model-written scenes in, tests for the engines that disagree about them out. It runs in rounds,
and each round starts from what the earlier ones learned (state.json).

One round
  target    which nodes to aim at: the ones the record knows least about (never tried, 4.1-only, x3d.loom 'todo' or
            'gap'), the ones the models keep getting wrong, the ones where engines disagreed last time
  prompt    one prompt per target node; the node must be used (its "focus")
  generate  x3dlab.duel: each contender writes each prompt, x3d_mcp's exact fixes run, one repair turn; the lessons
            learned in earlier rounds go into each contender's system prompt
  referee   three judges of validity that do not share code: the fork's verdict (x3d_mcp), lxml against the version's
            XSD, and the X3D object model (UOM) for the placement complaints where the XSD is known to be stricter
  engines   X_ITE (the reference renderer), x3d.loom (the Babylon bridge, picture against X_ITE's picture), Blender
            (import, X3D round trip, export validity) and glTF (Blender's export, the Khronos validator, X_ITE re-render)
  disagree  predicates over the results; each is a claim that can be re-checked ("the fork rejects a scene the XSD
            accepts", "the bridge draws a different silhouette than X_ITE")
  shrink    each new claim is shrunk to the smallest scene for which it is still true (x3dlab.shrink)
  write     loom fixtures (X_ITE's own pixels as the expectation, kept only where X_ITE reproduces them), x3d_mcp tests
            (strict xfail: they pass while the bug is there, and fail the day it is fixed), issue drafts
  learn     the errors each contender made become rules in its next system prompt; scoreboard, gallery, state.json

Nothing here needs a GPU. Chrome uses one when it can (X_ITE, the bridge); Cycles is off unless asked for.
"""

from __future__ import annotations

import base64
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import aio, bus, duel, formats, hog, runtime
from . import shrink as shrinker

ENGINES = ("xite", "loom", "blender", "gltf")
KEY = "1 0 1"                                   # the flat background every picture is taken on: magenta reads as "empty"
STATE_VERSION = 1

# Nodes the Foundry does not aim at: statements and structure, and nodes that only make sense with external files
# (the generation rules forbid those, so a prompt for them would ask for the impossible).
SKIP_NODES = {"X3D", "head", "Scene", "meta", "component", "unit", "ROUTE", "IS", "connect", "field", "fieldValue",
              "ProtoDeclare", "ProtoInterface", "ProtoBody", "ProtoInstance", "ExternProtoDeclare", "EXPORT", "IMPORT",
              "Inline", "InlineGeometry", "ImageTexture", "ImageTexture3D", "ImageCubeMapTexture", "MovieTexture",
              "AudioClip", "FontLibrary", "Script", "ShaderPart", "ShaderProgram", "PackagedShader", "ProgramShader",
              "MetadataBoolean", "MetadataDouble", "MetadataFloat", "MetadataInteger", "MetadataSet", "MetadataString",
              "DISEntityManager", "DISEntityTypeMapping", "GeoMetadata", "WorldInfo", "LoadSensor", "Anchor"}
BEHAVIOUR_BASES = {"X3DSensorNode", "X3DInterpolatorNode", "X3DSequencerNode", "X3DChaserNode", "X3DDamperNode",
                   "X3DTriggerNode", "X3DFollowerNode", "X3DTimeDependentNode"}
# What glTF has no place for and changes the picture: a low silhouette match is expected when one of these is used.
GLTF_VISUAL_LOSS = {"Text", "Billboard", "Switch", "LOD", "ParticleSystem", "EnvironmentLight", "Fog", "LocalFog",
                    "FogCoordinate", "MovieTexture", "PointSet", "IndexedLineSet", "LineSet", "Polyline2D", "Polypoint2D",
                    "Arc2D", "Circle2D", "ScreenGroup", "Layering", "LayerSet", "Layer", "LayoutLayer", "ViewpointGroup"}

NO_SHRINK = {"blender-unsupported", "blender-flat"}
CURVED = ("Sphere", "Cylinder", "Cone")
# What changes colours on the way to glTF by design (lighting from an environment, 3D and cube textures, shaders, fog);
# Phong (X3D) against PBR (glTF) alone moves colours by 30-50 of 255, under colour_kind's threshold.
GLTF_COLOUR_LOSS = {"EnvironmentLight", "ComposedTexture3D", "ImageTexture3D", "PixelTexture3D", "MovieTexture",
                    "GeneratedCubeMapTexture", "ComposedCubeMapTexture", "ImageCubeMapTexture", "TextureBackground", "Fog",
                    "LocalFog", "FogCoordinate", "MultiTexture", "ComposedShader", "PackagedShader", "ProgramShader",
                    "TextureCoordinateGenerator", "RenderedTexture"}   # smooth surfaces in X3D; the importer names their meshes Shape_<type>     # the importer names the node itself: nothing to find by shrinking
GLTF_MATCH = 0.7          # silhouette match (formats.match) below which the glTF trip lost part of the picture
LOOM_MATCH = 0.85         # and below which the bridge draws something else than X_ITE

TARGETS = {"x3d_mcp": "x3d_mcp (validator, hagopjay fork)", "xsd": "X3D XSD (Web3D Consortium schemas, as bundled by x3d_mcp)",
           "x_ite": "X_ITE", "blender_io": "x3d-blender-io (Blender add-on)",
           "blender_gltf": "Blender's glTF exporter (KhronosGroup/glTF-Blender-IO)", "loom": "x3d.loom (Babylon bridge)"}

# How expensive one re-check is, in rough seconds: cheap claims are shrunk first.
COST = {"fork-rejects-valid": 0.1, "fork-accepts-invalid": 0.1, "xsd-stricter-than-uom": 0.1, "xite-error": 0.6,
        "blender-import": 3, "blender-export-invalid": 4, "gltf-invalid": 5, "gltf-lost": 6, "gltf-colour": 6, "loom-error": 7, "loom-differs": 7, "loom-colour": 7}

RULE_TEXT = {
    "no-scene": "Reply with only the X3D document, starting with <?xml version=\"1.0\" encoding=\"UTF-8\"?>; no prose.",
    "not-xml": "Write well-formed XML: close every element, quote every attribute, escape & as &amp;, no markdown fences.",
    "boolean-case": "Booleans are lowercase: true and false, never True or False.",
    "unknown-field": "Use only field names that exist on that node in X3D {version}; when unsure, leave the field out.",
    "node-not-allowed": "Put each node only where a field accepts it (Appearance only in Shape, geometry only in "
                        "Shape.geometry, textures in Appearance.texture), and declare the <component> a node needs in <head>.",
    "value-format": "Give each field exactly the values its type needs (SFVec3f 3 numbers, SFRotation 4, SFColor 3 in 0..1) "
                    "and stay inside its allowed range.",
    "wrong-type": "Match field types: integers for SFInt32/MFInt32, numbers not words, MFString values each in quotes.",
    "bad-enum": "Enumerated fields take only their listed values, in the exact case.",
    "duplicate-def": "Each DEF name is defined once; reuse a node with USE.",
    "missing-attribute": "Give every required attribute (for example the name of a component, the url of a texture).",
    "missing-child": "Give nodes the children they require (a Shape needs geometry to be seen, a Layer its children).",
}


# ====================================================================== configuration and records
@dataclass
class FoundryConfig:
    contenders: list = field(default_factory=lambda: ["nvidia"])   # duel specs; "corpus" and "sample" need no key
    version: str = "4.0"                    # nodes new in 4.1 are always asked for in 4.1
    targets: int = 8                        # nodes per round
    nodes: list = field(default_factory=list)      # aim at these nodes instead of choosing
    repair_rounds: int = 1                  # model repair turns after x3d_mcp's exact fixes
    engines: list = field(default_factory=lambda: list(ENGINES))
    blender_versions: list = field(default_factory=lambda: ["X3D40"])
    formats: list = field(default_factory=lambda: ["glb"])
    cycles: bool = False                    # a Cycles picture per scene for the gallery (GPU minutes: off by default)
    render_samples: int = 16
    shrink: bool = True
    max_shrinks: int = 6                    # claims shrunk per round (cheap ones first)
    shrink_budget: int = 40                 # re-checks per claim
    shrink_seconds: float = 300.0           # and wall time per claim
    max_requests: int = 80                  # model requests per round, worst case; the round refuses to start above it
    parallel: int = 4
    learn: bool = True                      # turn this round's errors into rules for the next
    state: str = ""                         # state.json path; "" = <lab home>/foundry/state.json
    loom_ref: str = hog.LOOM_REF
    loom_dir: str = ""                      # an existing x3d.loom checkout; "" = clone loom_ref
    chrome_gpu: bool = True
    seed: int = 0                           # 0 = the round number, so a chain of rounds is repeatable
    name: str = ""


@dataclass
class Scene:
    id: str
    node: str                  # the focus node the prompt asked for
    version: str
    contender: str
    prompt: str
    xml: str = field(repr=False)
    draft: str = field(default="", repr=False)
    stage: str = ""            # where it became valid in the duel: draft | server | model | never
    focus_used: bool = False
    types: list = field(default_factory=list)
    referee: dict = field(default_factory=dict)     # {"final": judgement, "draft": judgement}
    sound: bool = False        # the XSD (or the UOM) accepts it and the fork's semantic layer has no error: engines must cope
    engines: dict = field(default_factory=dict)


@dataclass
class Claim:
    kind: str                  # fork-rejects-valid | ... | loom-differs  (see COST)
    target: str                # key of TARGETS
    scene: str
    node: str
    version: str
    signature: str             # what must stay true while shrinking, and the bucket across rounds
    summary: str
    which: str = "final"       # the draft or the final scene shows it
    evidence: dict = field(default_factory=dict)
    expected: str = ""         # non-empty: explained by a known limit (glTF has no text, the bridge has not started a node)
    known: bool = False        # seen in an earlier round, and reported there with its smallest scene
    same_as: str = ""          # shrank to the same scene as another claim of this round (that one carries the report)
    also: list = field(default_factory=list)        # other scenes with the same signature this round
    shrunk: dict | None = None
    artifacts: list = field(default_factory=list)


@dataclass
class Round:
    cfg: FoundryConfig
    id: str
    n: int
    name: str
    dir: Path
    started: float = field(default_factory=time.time)
    ended: float | None = None
    targets: list = field(default_factory=list)
    prompts: list = field(default_factory=list)
    duels: list = field(default_factory=list)
    scenes: list = field(default_factory=list)
    claims: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    scoreboard: dict = field(default_factory=dict)
    rules_used: dict = field(default_factory=dict)
    rules_next: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)

    def file_url(self, name: str) -> str:
        return "out/%s/%s" % (self.dir.name, name)

    def scene(self, sid: str) -> Scene | None:
        return next((s for s in self.scenes if s.id == sid), None)


# ====================================================================== state across rounds
def state_path(cfg: FoundryConfig) -> Path:
    return Path(cfg.state) if cfg.state else runtime.sub("foundry") / "state.json"


def load_state(path: Path) -> dict:
    try:
        st = json.loads(Path(path).read_text())
        if st.get("version") == STATE_VERSION:
            return st
    except Exception:
        pass
    return {"version": STATE_VERSION, "rounds": [], "nodes": {}, "rules": {}, "signatures": {}}


def save_state(path: Path, st: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, default=str))
    tmp.replace(path)


# ====================================================================== targets and prompts
def _uom(version: str):
    return hog._uom(version)


def node_catalogue() -> dict:
    """Concrete nodes the Foundry can aim at: {name: {component, level, v41, visual, behaviour, kind}}."""
    u40, u41 = _uom("4.0"), _uom("4.1")          # 4.1 holds every 4.0 node and the new ones
    out = {}
    for name, s in u41.nodes.items():
        if name in SKIP_NODES:
            continue
        anc = set(s.ancestors)
        behaviour = bool(anc & BEHAVIOUR_BASES)
        kind = ("behaviour" if behaviour else "geometry" if "X3DGeometryNode" in anc else "light" if "X3DLightNode" in anc
                else "texture" if anc & {"X3DTextureNode", "X3DTexture2DNode", "X3DTexture3DNode", "X3DEnvironmentTextureNode"}
                else "material" if anc & {"X3DMaterialNode", "X3DAppearanceChildNode"} else "bindable" if "X3DBindableNode" in anc
                else "group" if "X3DGroupingNode" in anc else "other")
        out[name] = {"component": s.component, "level": s.level, "v41": name not in u40.nodes,
                     "visual": s.component in hog.VISUAL_COMPONENTS or kind in ("geometry", "light", "texture", "material"),
                     "behaviour": behaviour, "kind": kind}
    return out


def loom_status(loom_dir: Path | None) -> dict:
    try:
        nodes = json.loads((Path(loom_dir) / "x3d.webgpu" / "bridge" / "NODES.json").read_text())["nodes"]
        return {k: v.get("status", "") for k, v in nodes.items()}
    except Exception:
        return {}


def choose_targets(cfg: FoundryConfig, st: dict, loom: dict, rng: random.Random) -> list[dict]:
    """Score every node, take the best `cfg.targets`, at most two per component (a round should spread out).
    novelty 3/(1+tried) · 4.1-only +2 · loom todo/gap +2 (claimed +1) · disagreements last time +1.5 each (max 3) ·
    models usually fail it +1.5 · a little noise, so ties do not always break the same way."""
    cat = node_catalogue()
    if cfg.nodes:
        return [dict(node=n, version="4.1" if cat.get(n, {}).get("v41") else cfg.version, score=None, why="asked for",
                     **{k: v for k, v in cat.get(n, {}).items() if k != "v41"}) for n in cfg.nodes]
    rows = []
    for n, info in cat.items():
        rec = st["nodes"].get(n, {})
        tried, valid, dis = rec.get("tried", 0), rec.get("valid", 0), rec.get("disagreements_last", 0)
        why, score = [], 3.0 / (1 + tried)
        if not tried:
            why.append("never tried")
        if info["v41"]:
            score += 2
            why.append("new in 4.1")
        ls = loom.get(n, "")
        if ls in ("todo", "gap"):
            score += 2
            why.append("loom: %s" % ls)
        elif ls == "claimed":
            score += 1
            why.append("loom: claimed")
        if dis:
            score += min(3.0, 1.5 * dis)
            why.append("%d disagreement(s) last time" % dis)
        if tried >= 2 and valid / tried < 0.5:
            score += 1.5
            why.append("models fail it (%d of %d valid)" % (valid, tried))
        score += rng.random() * 0.5
        rows.append({"node": n, "version": "4.1" if info["v41"] else cfg.version, "score": round(score, 2),
                     "why": ", ".join(why) or "-", **{k: v for k, v in info.items() if k != "v41"}})
    rows.sort(key=lambda r: -r["score"])
    out, per = [], Counter()
    for r in rows:
        if per[r["component"]] >= 2:
            continue
        out.append(r)
        per[r["component"]] += 1
        if len(out) >= cfg.targets:
            break
    return out


_IMMERSIVE: dict = {}


def _in_immersive(node: str, version: str) -> bool:
    if version not in _IMMERSIVE:
        from . import uom as uom_mod
        _IMMERSIVE[version] = uom_mod.profile_nodes("Immersive", version)
    return not _IMMERSIVE[version] or node in _IMMERSIVE[version]


def prompt_for(t: dict, rng: random.Random) -> dict:
    """One prompt that makes the model use the node, with a few of its fields away from their defaults."""
    node, version = t["node"], t["version"]
    spec = _uom(version).nodes.get(node)
    fields = []
    if spec is not None:
        fields = sorted(f.name for f in spec.fields.values()
                        if f.settable and not f.type.endswith("Node") and f.name not in ("description", "bboxCenter", "bboxSize",
                                                                                            "visible", "bboxDisplay", "enabled"))
    pick = rng.sample(fields, min(4, len(fields))) if fields else []
    purpose = {
        "behaviour": "wired with ROUTEs so it visibly changes something in the scene (no Script)",
        "geometry": "as the geometry of a Shape with a Material, large enough to fill the middle of the view",
        "light": "lighting a few simple shapes so its effect is plain to see (turn the headlight off)",
        "texture": "on a Box or a quad; give image data inline (PixelTexture style data, or data: URLs), no external files",
        "material": "on two or three simple shapes, so the material's look is plain to see",
        "bindable": "bound at load, with simple shapes around it so its effect is visible",
        "group": "holding two or three simple coloured shapes",
    }.get(t.get("kind", ""), "used in a small scene of simple coloured shapes")
    lines = ["Write a small X3D %s scene whose centrepiece is a %s node (%s component), %s." % (version, node, t.get("component", "?"), purpose)]
    if pick:
        lines.append("Set these %s fields to sensible non-default values: %s." % (node, ", ".join(pick)))
    lines.append("Frame it for a Viewpoint at position 0 0 10 looking at the origin; keep everything within 3 m of the origin.")
    if not _in_immersive(node, version) and spec is not None:
        lines.append('%s is not in the Immersive profile: add <component name="%s" level="%d"/> to <head>.' % (node, spec.component, spec.level))
    return {"id": node, "focus": node, "trap": "focus: %s (%s)" % (node, t.get("why", "")), "text": " ".join(lines),
            "version": version}


# ====================================================================== the offline contender
def _defect(xml: str, kind: str) -> str:
    if kind == "boolean-case":
        return re.sub(r'="(true|false)"', lambda m: '="%s"' % m.group(1).capitalize(), xml, count=1)
    if kind == "field-typo":
        for a, b in (("diffuseColor=", "diffuseColour="), ("translation=", "translate="), ("position=", "positon=")):
            if a in xml:
                return xml.replace(a, b, 1)
    return xml


class _CorpusConversation:
    """Stands in for a model: the known-good scene for the focus node (x3d.loom fixtures, the 4.1 examples), with a
    defect in two of three, so the referee, the repair turn and every engine have something real to chew on."""
    provider = "corpus"

    def __init__(self, xml: str | None, defect: str):
        self.xml, self.defect = xml, defect
        self.usage = {"input": 0, "output": 0, "calls": 0}

    def send(self, text: str):
        from . import llm
        self.usage["calls"] += 1
        if self.xml is None:
            return llm.Reply("I have no scene for that node.", [], {"input": 0, "output": 0}, "corpus", "stop")
        xml = _defect(self.xml, self.defect) if self.usage["calls"] == 1 else self.xml
        return llm.Reply(xml, [], {"input": 0, "output": 0}, "corpus", "stop")


_CORPUS: dict = {}


def corpus_scenes(loom_dir: Path | None) -> dict:
    """{(node, version): xml} from x3d.loom's fixtures and the 4.1 examples."""
    if not _CORPUS:
        for it in hog.corpus(hog.HogConfig(versions=["4.0", "4.1"], include_lab_scenes=False), loom_dir):
            _CORPUS.setdefault((it.node, it.version), it.xml)
    return _CORPUS


def _corpus_factory(cfg, index, prompt):
    xml = _CORPUS.get((prompt.get("focus"), cfg.version)) or _CORPUS.get((prompt.get("focus"), "4.1"))
    return _CorpusConversation(xml, ("clean", "boolean-case", "field-typo")[index % 3])


duel.OFFLINE["corpus"] = ("x3d.loom fixtures and the 4.1 examples", _corpus_factory)


# ====================================================================== referee
_ORACLES: dict = {}


def _oracle(version: str):
    if version not in _ORACLES:
        _ORACLES[version] = hog._xsd_oracle(version)
    return _ORACLES[version]


async def judge(client, xml: str, version: str, job: str = "") -> dict:
    """Three judges: the fork (XSD layer + semantic layer), lxml with the version's XSD, and the UOM for placements."""
    out = {}
    if "<X3D" not in (xml or ""):
        return {"fork": {"ok": False, "xsd_valid": False, "kinds": ["no-scene"], "xsd_errors": [], "semantic": []},
                "xsd": {"ok": False, "key": "no-scene", "first": ""}, "uom": {"allows": None, "why": ""}, "effective": False}
    v = await client.verdict(xml, job=job)
    errs = list(v.errors)
    out["fork"] = {"ok": v.ok, "xsd_valid": v.xsd_valid, "kinds": duel.kinds_of(v),
                   "xsd_errors": [_fine(e.message) for e in errs if e.layer in ("xsd", "xml")][:6],
                   "semantic": [duel.classify(e) for e in errs if e.layer == "semantic"],
                   "messages": [e.message[:240] for e in errs[:6]]}
    o = _oracle(version)
    if o is None:
        out["xsd"] = {"ok": None, "key": "", "first": "no XSD for %s" % version}
    else:
        r = await o(xml)
        out["xsd"] = {"ok": r.verdict == "accept", "key": r.key, "first": (r.detail or "")[:300]}
    allows, why = (None, "")
    if out["xsd"]["ok"] is False:
        allows, why = hog.uom_allows(xml, version, out["xsd"]["first"])
    out["uom"] = {"allows": allows, "why": why}
    out["effective"] = bool(out["xsd"]["ok"] or allows)          # valid by the XSD, or by the model where the XSD is stricter
    return out


def _fine(msg: str) -> str:
    from .fuzz import fine
    return fine(msg or "")


def _norm(msg: str) -> str:
    from .fuzz import norm
    return norm(msg or "", 120)


# ====================================================================== engines
def _safe(s: str) -> str:
    return formats.safe_name(s)


def _png_data_url(data: str | None) -> bytes | None:
    if not data or not isinstance(data, str) or "base64," not in data:
        return None
    return base64.b64decode(data.split("base64,", 1)[1])


class Bench:
    """Every engine, one scene at a time: what the shrinker re-checks with. Counts its calls."""

    def __init__(self, lab, rnd: Round, loom_dir: Path | None):
        self.lab, self.rnd, self.loom_dir = lab, rnd, loom_dir
        self.calls = Counter()
        self.work = rnd.dir / "bench"
        self.work.mkdir(exist_ok=True)

    def judge(self, xml: str, version: str) -> dict:
        self.calls["referee"] += 1
        return aio.run(judge(self.lab.client, xml, version, self.rnd.id), 120)

    def xsd_ok(self, xml: str, version: str) -> bool:
        o = _oracle(version)
        if o is None:
            return True
        self.calls["xsd"] += 1
        return aio.run(o(xml), 60).verdict == "accept"

    def xite(self, xml: str):
        self.calls["xite"] += 1
        self.lab.browser.assets = self.rnd.dir
        return aio.run(self.lab.browser.aload(xml, screenshot=True, background=KEY, frames=2, timeout=45), 90)

    def blender(self, task: dict) -> dict:
        self.calls["blender"] += 1
        r = self.lab.blender.run([task], timeout=600, job=self.rnd.id, label="foundry")[0]
        res = r.get("result") or {}
        if not r.get("ok") and not res.get("error"):
            res = dict(res, error="blender: %s" % (r.get("error") or "failed"))
        return res

    def _file(self, xml: str, suffix: str = ".x3d") -> Path:
        f = self.work / ("t%05d%s" % (sum(self.calls.values()), suffix))
        f.write_text(xml, encoding="utf-8")
        return f

    def convert(self, xml: str, fmt: str = "glb") -> tuple[dict, dict]:
        src = self._file(xml)
        res = self.blender({"kind": "convert", "src": str(src), "format": fmt, "out": str(src.with_suffix("." + fmt))})
        rep = formats.validate_gltf([res["output"]]).get(res.get("output"), {}) if res.get("output") else {}
        return res, rep

    def loom(self, xmls: dict) -> dict:
        """{name: xml} -> {name: {ok, ours, xite, png_ours, png_xite, compare}}: one runner call, a whole picture each."""
        self.calls["loom"] += 1
        return loom_shots(self.loom_dir, xmls)


def _fixtures_dir(loom_dir: Path) -> Path:
    d = Path(loom_dir) / "x3d.webgpu" / "bridge" / "fixtures" / "foundry"
    d.mkdir(parents=True, exist_ok=True)
    return d


def loom_run(loom_dir: Path, fixtures: dict) -> dict:
    """Write {name: (xml, spec)} under fixtures/foundry/, run them through the bridge's own runner, remove them again."""
    d = _fixtures_dir(loom_dir)
    for name, (xml, spec) in fixtures.items():
        (d / (name + ".x3d")).write_text(xml, encoding="utf-8")
        (d / (name + ".test.json")).write_text(json.dumps(spec, indent=1), encoding="utf-8")
    try:
        out = hog.loom_suite(Path(loom_dir), ["foundry/" + n for n in fixtures], timeout=300 + 60 * len(fixtures))
    finally:
        for name in fixtures:
            for ext in (".x3d", ".test.json"):
                (d / (name + ext)).unlink(missing_ok=True)
    res = out.get("results") or {}
    return {n: res.get("foundry/" + n, {"ok": False, "msg": "no result"}) for n in fixtures} | {"_env": out.get("env", {})}


# X_ITE's "Loading 1 file" toast is drawn into the runner's X_ITE picture (its x3d-canvas keeps notifications on): a
# scene with nothing but an EnvironmentLight then "covers" the top right corner. Turn them off on the X_ITE page only.
QUIET_XITE = {"js": ["(() => { const c = document.querySelector('#c'); X3D.getBrowser(c).setBrowserOption('Notifications', false); "
                      "for (const r of [document, c.shadowRoot].filter(Boolean)) r.querySelectorAll('.x_ite-private-notification')"
                      ".forEach((e) => { e.style.display = 'none'; }); return 1; })()", "$any"], "only": "xite"}


def loom_shots(loom_dir: Path, xmls: dict) -> dict:
    """Both engines of the bridge's runner take one whole 320 x 240 picture of each scene on the key background."""
    spec = lambda n: {"node": n, "mode": "scene", "note": "x3d.lab Foundry probe: one picture per engine",   # noqa: E731
                      "steps": [QUIET_XITE, {"settle": 1}, {"shot": 1}]}
    res = loom_run(loom_dir, {n: (hog_flat(x), spec(n)) for n, x in xmls.items()})
    out = {"_env": res.pop("_env", {})}
    for n, r in res.items():
        po, px = _png_data_url((r.get("recordedOurs") or [None])[-1]), _png_data_url((r.get("recordedXite") or [None])[-1])
        err = "" if str(r.get("ours", "")).startswith("pass") else str(r.get("ours") or r.get("msg") or "")[:300]
        if r.get("pageErrors"):
            err = (err + " | " if err else "") + "; ".join(r["pageErrors"])[:300]
        out[n] = {"ours": str(r.get("ours", ""))[:300], "xite": str(r.get("xite", ""))[:300], "error": err,
                  "png_ours": po, "png_xite": px, "compare": formats.compare_pngs(px, po) if (po and px) else {"iou": None}}
    return out


def hog_flat(xml: str) -> str:
    from .browser import flat_background
    return flat_background(xml, KEY)


def _engines(lab, rnd: Round, loom_dir: Path | None, bench: Bench) -> None:
    cfg = rnd.cfg
    run_scenes = [s for s in rnd.scenes if s.referee.get("final", {}).get("effective")]
    if not run_scenes:
        return
    cat = node_catalogue()
    hcfg = hog.HogConfig(versions=sorted({s.version for s in run_scenes}), blender_versions=list(cfg.blender_versions),
                         formats=list(cfg.formats) if "gltf" in cfg.engines else [], render_samples=cfg.render_samples,
                         name=rnd.name)
    hrun = hog.HogRun(hcfg, rnd.id, rnd.name, rnd.dir)
    items = [hog.Item(s.id, s.node, s.version, "foundry", s.xml, cat.get(s.node, {}).get("component", ""), bool(cfg.cycles), "", "valid")
             for s in run_scenes]

    def stage(name, fn):
        t0 = time.time()
        try:
            with bus.span("foundry: %s" % name, src="foundry", job=rnd.id):
                fn()
        except Exception as exc:
            rnd.errors[name] = "%s: %s" % (type(exc).__name__, str(exc)[:400])
            bus.error("foundry %s failed: %s" % (name, rnd.errors[name]), src="foundry", job=rnd.id)
        rnd.timings[name] = round(time.time() - t0, 1)

    def xite():
        if lab.browser is None or (cfg.chrome_gpu and not lab.browser.gpu_requested):
            lab.start_browser(gpu=cfg.chrome_gpu)
        rnd.env["x_ite"] = getattr(lab.browser, "info", None)
        aio.run(hog._xite_all(lab.browser, items, hrun, assets=rnd.dir, shots=rnd.dir / "shots"), 3600)

    def blender():
        if lab.blender is None:
            raise RuntimeError("no Blender: run lab.setup_blender() first, or leave 'blender' and 'gltf' out of engines")
        hog._blender_all(lab, items, hcfg, hrun)
        aio.run(hog._validate_exports(lab, hrun, hcfg), 1800)
        for m in hrun.matrix.values():                 # the XSD's word on Blender's export too (the fork alone can be wrong)
            for bv in hcfg.blender_versions:
                e = m.get("blender_rt_" + bv)
                if e and e.get("file"):
                    xml = (rnd.dir / e["file"]).read_text(encoding="utf-8", errors="replace")
                    ver = "4.1" if bv.endswith("41") else "4.0"
                    o = _oracle(ver)
                    if o is not None:
                        r = aio.run(o(xml), 60)
                        e["xsd"] = {"ok": r.verdict == "accept", "key": r.key, "first": (r.detail or "")[:240]}

    def gltf():
        glbs = {sid: m["convert_glb"] for sid, m in hrun.matrix.items() if (m.get("convert_glb") or {}).get("file")}
        reps = formats.validate_gltf([rnd.dir / c["file"] for c in glbs.values()])
        for sid, c in glbs.items():
            rep = reps.get(str(rnd.dir / c["file"]), {})
            c["validator"] = {k: rep.get(k) for k in ("ok", "errors", "warnings", "infos", "hints", "info", "crashed")}
            c["validator"]["codes"] = formats.error_codes(rep)
            c["validator"]["messages"] = [m for m in rep.get("messages", []) if m.get("severity", 3) <= 1][:12]
        if "xite" in cfg.engines and lab.browser is not None:
            for sid, c in glbs.items():
                s = rnd.scene(sid)
                orig = hrun.matrix[sid].get("x_ite", {})
                if not orig.get("png"):
                    continue
                r = bench.xite(formats.inline_wrapper(s.xml, c["file"]))
                if r.png:
                    f = rnd.dir / "shots" / ("%s.gltf.png" % _safe(sid))
                    f.write_bytes(r.png)
                    c["xite_png"] = os.path.relpath(f, rnd.dir)
                c["xite"] = {"ok": r.ok, "errors": r.errors[:3]}
                c["compare"] = formats.compare_pngs((rnd.dir / orig["png"]).read_bytes(), r.png)
                c["losses"] = formats.losses(s.types, c)

    def loom():
        if loom_dir is None:
            raise RuntimeError("no x3d.loom checkout")
        shots = bench.loom({_safe(s.id): s.xml for s in run_scenes})
        rnd.env["loom"] = shots.pop("_env", {})
        for s in run_scenes:
            r = shots.get(_safe(s.id))
            if r is None:
                continue
            for k, suffix in (("png_ours", "loom"), ("png_xite", "loomxite")):
                if r.get(k):
                    f = rnd.dir / "shots" / ("%s.%s.png" % (_safe(s.id), suffix))
                    f.parent.mkdir(exist_ok=True)
                    f.write_bytes(r[k])
                    r[k] = os.path.relpath(f, rnd.dir)
            hrun.matrix.setdefault(s.id, {})["loom"] = r

    if "xite" in cfg.engines:
        stage("xite", xite)
    if "blender" in cfg.engines or "gltf" in cfg.engines:
        stage("blender", blender)
    if "gltf" in cfg.engines:
        stage("gltf", gltf)
    if "loom" in cfg.engines:
        stage("loom", loom)
    for s in run_scenes:
        s.engines = hrun.matrix.get(s.id, {})


# ====================================================================== claims (disagreements)
def _sig(kind: str, text: str) -> str:
    return "%s|%s" % (kind, text)


def find_claims(rnd: Round, loom: dict) -> list[Claim]:
    out = []
    for s in rnd.scenes:
        for which in ("draft", "final"):
            j = s.referee.get(which) or {}
            f, x, u = j.get("fork") or {}, j.get("xsd") or {}, j.get("uom") or {}
            if not f or "no-scene" in f.get("kinds", []) or (x.get("key") or "").startswith("xml:"):
                continue
            if f.get("xsd_valid") is False and x.get("ok") is True:
                first = (f.get("xsd_errors") or [""])[0]
                out.append(Claim("fork-rejects-valid", "x3d_mcp", s.id, s.node, s.version, _sig("fork-rejects-valid", first),
                                 "x3d_mcp rejects a scene x3d-%s.xsd accepts: %s" % (s.version, first[:160]), which,
                                 {"fork": f.get("messages", [])[:3]}))
            elif f.get("xsd_valid") is True and x.get("ok") is False and u.get("allows") is not True:
                out.append(Claim("fork-accepts-invalid", "x3d_mcp", s.id, s.node, s.version, _sig("fork-accepts-invalid", x.get("key", "")),
                                 "x3d_mcp accepts a scene x3d-%s.xsd rejects: %s" % (s.version, x.get("first", "")[:160]), which,
                                 {"xsd": x.get("first")}))
            if x.get("ok") is False and u.get("allows") is True:
                out.append(Claim("xsd-stricter-than-uom", "xsd", s.id, s.node, s.version, _sig("xsd-stricter-than-uom", x.get("key", "")),
                                 "x3d-%s.xsd rejects a placement the X3D object model allows: %s" % (s.version, x.get("first", "")[:160]),
                                 which, {"xsd": x.get("first"), "uom": u.get("why")}))
        if not s.sound or not s.engines:
            continue
        e = s.engines
        xi = e.get("x_ite") or {}
        if xi and (not xi.get("ok") or xi.get("errors")):
            msg = (xi.get("errors") or ["load failed"])[0]
            out.append(Claim("xite-error", "x_ite", s.id, s.node, s.version, _sig("xite-error", _norm(msg)),
                             "X_ITE: %s" % msg[:200], evidence={"x_ite": xi}))
            continue                                   # the other engines are judged against X_ITE: not when it failed
        bi = e.get("blender_import") or {}
        if bi and not bi.get("ok"):
            out.append(Claim("blender-import", "blender_io", s.id, s.node, s.version, _sig("blender-import", _norm(bi.get("error", ""))),
                             "Blender import fails: %s" % bi.get("error", "")[:200], evidence={"blender_import": bi}))
        dropped = bi.get("unsupported") or []
        for t in [t for t in dropped if t not in formats.EXPECTED_LOSS]:   # skipping a sensor is fine; a geometry is not
            out.append(Claim("blender-unsupported", "blender_io", s.id, s.node, s.version, _sig("blender-unsupported", t),
                             "the X3D importer skips %s (\"unsupported type\"): the scene loses it in Blender and everything after"
                             % t, evidence={"log": bi.get("log"), "counts": bi.get("counts")}))
        for bv in rnd.cfg.blender_versions:
            rt = e.get("blender_rt_" + bv) or {}
            if rt.get("file") and rt.get("valid") is False and (rt.get("xsd") or {}).get("ok") is False:
                out.append(Claim("blender-export-invalid", "blender_io", s.id, s.node, s.version,
                                 _sig("blender-export-invalid", "%s %s" % (bv, rt["xsd"].get("key", ""))),
                                 "Blender's %s export is invalid: %s" % (bv, rt["xsd"].get("first", "")[:200]),
                                 evidence={"export": rt.get("file"), "fork": rt.get("kinds"), "xsd": rt["xsd"].get("first")}))
        cv = e.get("convert_glb") or {}
        flat = sorted({t for t in CURVED for n in cv.get("flat_meshes") or [] if t in n})
        if flat:
            out.append(Claim("blender-flat", "blender_io", s.id, s.node, s.version, _sig("blender-flat", ",".join(flat)),
                             "the X3D importer shades %s flat (X3D draws them smooth); Blender shows facets and every format "
                             "exported after it gets per-face normals" % ", ".join(flat), evidence={"flat_meshes": cv.get("flat_meshes"),
                                                                                                  "smooth_faces": cv.get("smooth_faces")}))
        val = cv.get("validator") or {}
        if val.get("errors"):
            out.append(Claim("gltf-invalid", "blender_gltf", s.id, s.node, s.version, _sig("gltf-invalid", ",".join(val.get("codes") or [])),
                             "Blender's glTF export fails the Khronos validator: %s" % ", ".join(val.get("codes") or []),
                             evidence={"messages": val.get("messages")}))
        cmp_, lo = cv.get("compare") or {}, cv.get("losses") or {}
        if lo.get("unexpected") or (cmp_.get("match") is not None and cmp_["match"] < GLTF_MATCH and (cmp_.get("cov_original") or 0) > 0.002):
            visual_loss = sorted(set(s.types) & GLTF_VISUAL_LOSS)
            what = ", ".join(lo.get("unexpected") or []) or "the picture (silhouette match %.2f)" % cmp_["match"]
            why = ("the X3D importer skips %s" % ", ".join(dropped)) if dropped else \
                  ("glTF has no place for %s" % ", ".join(visual_loss)) if visual_loss and not lo.get("unexpected") else ""
            out.append(Claim("gltf-lost", "blender_io", s.id, s.node, s.version,
                             _sig("gltf-lost", ",".join(sorted(x.split()[0] for x in lo.get("unexpected") or [])) or "silhouette " + s.node),
                             "X3D -> Blender -> glTF loses %s" % what, evidence={"compare": cmp_, "losses": lo}, expected=why))
        elif cmp_.get("match") is not None and cmp_["match"] >= GLTF_MATCH and formats.colour_kind(cmp_):
            ck = formats.colour_kind(cmp_)
            lost = sorted(set(s.types) & GLTF_COLOUR_LOSS)
            out.append(Claim("gltf-colour", "blender_io", s.id, s.node, s.version, _sig("gltf-colour", "%s %s" % (ck, s.node)),
                             "X3D -> Blender -> glTF keeps the shapes but not the colours (%s: mean change %.0f of 255, brightness x%.2f)"
                             % (ck, cmp_["colour_delta"], cmp_.get("luma_ratio") or 0), evidence={"compare": cmp_},
                             expected=("glTF (or the importer) has no place for %s" % ", ".join(lost)) if lost else ""))
        lm = e.get("loom") or {}
        if lm.get("error"):
            out.append(Claim("loom-error", "loom", s.id, s.node, s.version, _sig("loom-error", _norm(lm["error"])),
                             "the bridge fails: %s" % lm["error"][:200], evidence={"ours": lm.get("ours")}))
        elif (lm.get("compare") or {}).get("match") is not None and lm["compare"]["match"] < LOOM_MATCH and (lm["compare"].get("cov_original") or 0) > 0.002:
            todo = sorted(t for t in s.types if loom.get(t) in ("todo", "blocked"))
            out.append(Claim("loom-differs", "loom", s.id, s.node, s.version, _sig("loom-differs", ",".join(todo) or s.node),
                             "the bridge draws a different picture than X_ITE (silhouette match %.2f, IoU %.2f)" % (
                                 lm["compare"]["match"], lm["compare"]["iou"]),
                             evidence={"compare": lm["compare"]},
                             expected=("the bridge has not started %s" % ", ".join(todo)) if todo else ""))
        elif formats.colour_kind(lm.get("compare") or {}):
            ck = formats.colour_kind(lm["compare"])
            out.append(Claim("loom-colour", "loom", s.id, s.node, s.version, _sig("loom-colour", ck),
                             "the bridge draws the right shapes in the wrong colours (%s: mean change %.0f of 255, brightness x%.2f)" % (
                                 ck, lm["compare"]["colour_delta"], lm["compare"].get("luma_ratio") or 0),
                             evidence={"compare": lm["compare"]}))
    # one claim per signature; the rest are noted on it
    by, merged = {}, []
    for c in out:
        if c.signature in by:
            if c.scene != by[c.signature].scene and c.scene not in by[c.signature].also:
                by[c.signature].also.append(c.scene)
        else:
            by[c.signature] = c
            merged.append(c)
    return merged


def keep_for(c: Claim, bench: Bench, rnd: Round):
    """(keep, valid) for the shrinker: keep(xml) is True while the claim still holds; valid(xml) guards the cheap part."""
    v = c.version
    if c.kind == "fork-rejects-valid":
        want = c.signature.split("|", 1)[1]
        def keep(x):
            j = bench.judge(x, v)
            return j["xsd"]["ok"] is True and j["fork"]["xsd_valid"] is False and want in j["fork"]["xsd_errors"]
        return keep, None
    if c.kind == "fork-accepts-invalid":
        want = c.signature.split("|", 1)[1]
        def keep(x):
            j = bench.judge(x, v)
            return j["fork"]["xsd_valid"] is True and j["xsd"]["ok"] is False and j["xsd"]["key"] == want
        return keep, None
    if c.kind == "xsd-stricter-than-uom":
        want = c.signature.split("|", 1)[1]
        def keep(x):
            j = bench.judge(x, v)
            return j["xsd"]["ok"] is False and j["xsd"]["key"] == want and j["uom"]["allows"] is True
        return keep, None
    def valid(x):                     # every cut stays a scene the engines owe an answer: XSD (or UOM) valid, no semantic error
        j = bench.judge(x, v)
        return bool(j["effective"] and not j["fork"]["semantic"])
    if c.kind == "xite-error":
        want = c.signature.split("|", 1)[1]
        def keep(x):
            r = bench.xite(x)
            return any(_norm(m) == want for m in (r.errors or ["load failed"] if not r.ok else r.errors))
        return keep, valid
    if c.kind == "blender-import":
        want = c.signature.split("|", 1)[1]
        def keep(x):
            res = bench.blender({"kind": "import", "src": str(bench._file(x))})
            return bool(res.get("error")) and _norm(res["error"]) == want
        return keep, valid
    if c.kind == "blender-export-invalid":
        bv, want = c.signature.split("|", 1)[1].split(" ", 1)
        def keep(x):
            src = bench._file(x)
            res = bench.blender({"kind": "roundtrip", "src": str(src), "version": bv, "generations": 1, "out_prefix": str(src.with_suffix(""))})
            out = ((res.get("generations") or [{}])[0]).get("output")
            if not out or not os.path.exists(out):
                return False
            o = _oracle("4.1" if bv.endswith("41") else "4.0")
            return aio.run(o(Path(out).read_text(encoding="utf-8", errors="replace")), 60).key == want
        return keep, valid
    if c.kind == "gltf-invalid":
        codes = set(c.signature.split("|", 1)[1].split(","))
        def keep(x):
            res, rep = bench.convert(x)
            return bool(codes & set(formats.error_codes(rep)))
        return keep, valid
    if c.kind == "gltf-colour":
        ck = c.signature.split("|", 1)[1].split(" ", 1)[0]
        def keep(x):
            res, _ = bench.convert(x)
            if res.get("error"):
                return False
            a = bench.xite(x)
            b = bench.xite(formats.inline_wrapper(x, os.path.relpath(res["output"], rnd.dir)))
            cmp_ = formats.compare_pngs(a.png, b.png)
            return (cmp_.get("match") or 0) >= GLTF_MATCH and formats.colour_kind(cmp_) == ck
        return keep, valid
    if c.kind == "gltf-lost":
        what = c.signature.split("|", 1)[1]
        def keep(x):
            res, _ = bench.convert(x)
            if res.get("error"):
                return False
            if not what.startswith("silhouette"):
                lo = formats.losses(hog._node_types(x), res)
                return set(what.split(",")) <= {u.split()[0] for u in lo["unexpected"]}
            a = bench.xite(x)
            b = bench.xite(formats.inline_wrapper(x, os.path.relpath(res["output"], rnd.dir)))
            cmp_ = formats.compare_pngs(a.png, b.png)
            return cmp_.get("match") is not None and cmp_["match"] < GLTF_MATCH and (cmp_.get("cov_original") or 0) > 0.002
        return keep, valid
    if c.kind in ("loom-error", "loom-differs", "loom-colour"):
        want = c.signature.split("|", 1)[1]
        def keep(x):
            r = bench.loom({"shrink": x}).get("shrink") or {}
            if c.kind == "loom-error":
                return bool(r.get("error")) and _norm(r["error"]) == want
            if c.kind == "loom-colour":
                cm = r.get("compare") or {}
                return not r.get("error") and (cm.get("match") or 0) >= LOOM_MATCH and formats.colour_kind(cm) == want
            cm = r.get("compare") or {}
            return not r.get("error") and cm.get("match") is not None and cm["match"] < LOOM_MATCH and (cm.get("cov_original") or 0) > 0.002
        return keep, valid
    raise ValueError(c.kind)


def shrink_claims(rnd: Round, bench: Bench) -> None:
    cfg = rnd.cfg
    todo = sorted([c for c in rnd.claims if not c.known and not c.expected and c.kind not in NO_SHRINK],
                  key=lambda c: (COST.get(c.kind, 9), -len(c.also)))
    for c in todo[:max(0, cfg.max_shrinks)]:
        s = rnd.scene(c.scene)
        xml = s.draft if c.which == "draft" else s.xml
        keep, valid = keep_for(c, bench, rnd)
        t0 = time.time()
        with bus.span("shrink %s (%s)" % (c.kind, s.node), src="foundry", job=rnd.id):
            try:
                if not keep(xml):                       # flaky or order-dependent: say so instead of shrinking noise
                    c.shrunk = {"error": "the claim did not hold when checked again"}
                    continue
                r = shrinker.shrink(xml, keep, valid=valid, budget=cfg.shrink_budget, time_budget=cfg.shrink_seconds)
                c.shrunk = {"xml": r.xml, "before": r.before, "after": r.after, "checks": r.checks, "seconds": round(r.seconds, 1),
                            "ratio": round(r.ratio, 3), "exhausted": r.exhausted}
            except Exception as exc:
                c.shrunk = {"error": "%s: %s" % (type(exc).__name__, str(exc)[:300])}
        bus.emit("shrunk %s: %s" % (c.kind, ("%d -> %d bytes" % (c.shrunk["before"]["bytes"], c.shrunk["after"]["bytes"]))
                                    if "xml" in c.shrunk else c.shrunk.get("error")),
                 src="foundry", kind="foundry_shrink", job=rnd.id, data={"seconds": round(time.time() - t0, 1)})
    # two claims that shrink to the same scene are one problem: a camera bug looks node-specific until it is shrunk
    first = {}
    for c in rnd.claims:
        if (c.shrunk or {}).get("xml"):
            c.shrunk["types"] = hog._node_types(c.shrunk["xml"])
            k = (c.kind, tuple(c.shrunk["types"]))
            if k in first:
                c.same_as = first[k].signature
                first[k].also += [c.scene] + [x for x in c.also if x not in first[k].also]
            else:
                first[k] = c


# ====================================================================== artifacts
def pick_points(png: bytes, n_cov: int = 10, n_bg: int = 4, step: int = 8) -> list:
    """Pixels to pin from X_ITE's picture: centres of flat 5 x 5 patches (no edges, so anti-aliasing cannot move them),
    spread over a 4 x 3 grid of regions; covered ones first, a few key-background ones to catch over-drawing."""
    import numpy as np
    from PIL import Image
    rgb = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    h, w, _ = rgb.shape
    key = np.array([255, 0, 255])
    cov, bg = {}, {}
    for y in range(4, h - 4, step):
        for x in range(4, w - 4, step):
            win = rgb[y - 2:y + 3, x - 2:x + 3].reshape(-1, 3)
            if (win.max(0) - win.min(0)).max() > 8:
                continue
            c = rgb[y, x]
            region = (min(3, x * 4 // w), min(2, y * 3 // h))
            (bg if np.abs(c - key).sum() <= 30 else cov).setdefault(region, []).append((x, y, [int(v) for v in c]))

    def spread(groups, n):
        out, lists = [], [g[len(g) // 2:] + g[:len(g) // 2] for _, g in sorted(groups.items())]
        while len(out) < n and any(lists):
            for g in lists:
                if g and len(out) < n:
                    out.append(g.pop(0))
        return out
    return [(x, y, c, 48) for x, y, c in spread(cov, n_cov)] + [(x, y, c, 24) for x, y, c in spread(bg, n_bg)]


def _fixture_name(rnd: Round, s: Scene, k: int) -> str:
    return "Foundry_%s_r%d%s" % (_safe(s.node), rnd.n, "abcdefghijklmnopqrstuvwxyz"[k % 26])


def write_loom_fixtures(rnd: Round, bench: Bench, loom: dict) -> list[dict]:
    """Bridge fixtures with X_ITE's pixels: one per loom claim (shrunk scene when there is one) and one per node the
    bridge has not covered yet where both engines agree. A calibration run drops pixels X_ITE does not reproduce."""
    if bench.loom_dir is None:
        return []
    picks, seen = [], set()
    for c in rnd.claims:
        if c.kind in ("loom-differs", "loom-error", "loom-colour") and c.scene not in seen:
            s = rnd.scene(c.scene)
            xml = (c.shrunk or {}).get("xml") or s.xml
            picks.append((s, xml, c))
            seen.add(c.scene)
    fixtures_have = {p.name[:-len(".test.json")] for p in (Path(bench.loom_dir) / "x3d.webgpu" / "bridge" / "fixtures").glob("*.test.json")}
    covered = set()
    for s in rnd.scenes:
        lm = (s.engines or {}).get("loom") or {}
        if s.id in seen or s.node in covered or not s.sound or not s.focus_used or lm.get("error"):
            continue
        if ((lm.get("compare") or {}).get("match") or 0) >= LOOM_MATCH and (s.node not in fixtures_have or loom.get(s.node) in ("todo", "gap", "claimed")):
            picks.append((s, s.xml, None))
            seen.add(s.id)
            covered.add(s.node)
    if not picks:
        return []
    names = {}
    for k, (s, xml, c) in enumerate(picks):
        names[_fixture_name(rnd, s, k)] = (s, xml, c)
    shots = bench.loom({n: xml for n, (s, xml, c) in names.items()})          # pictures of exactly what will be pinned
    specs = {}
    for n, (s, xml, c) in names.items():
        px = (shots.get(n) or {}).get("png_xite")
        pts = pick_points(px) if px else []
        if sum(1 for p in pts if p[3] == 48) < 3:
            continue
        specs[n] = {"node": s.node, "mode": "scene",
                    "note": "Written by the x3d.lab Foundry, round %d (%s): %s The background is the flat key colour (%s) so "
                            "coverage reads at a glance; the pixels are X_ITE's, kept only where X_ITE reproduces them."
                            % (rnd.n, s.contender, (s.prompt[:200] + " ") if s.prompt else "", KEY),
                    "foundry": {"round": rnd.n, "scene": s.id, "contender": s.contender, "claim": c.kind if c else "coverage"},
                    "steps": [QUIET_XITE, {"settle": 1}] + [{"px": [x, y, col, tol]} for x, y, col, tol in pts]}
    if not specs:
        return []
    cal = loom_run(bench.loom_dir, {n: (hog_flat(names[n][1]), sp) for n, sp in specs.items()})
    out = []
    for n, sp in specs.items():
        r = cal.get(n) or {}
        bad_x = {int(i) for i in re.findall(r"step (\d+):", str(r.get("xite", "")))}
        bad_o = {int(i) for i in re.findall(r"step (\d+):", str(r.get("ours", "")))} - bad_x
        steps = [st for i, st in enumerate(sp["steps"], 1) if i not in bad_x]
        if sum(1 for st in steps if "px" in st and st["px"][3] == 48) < 3:
            continue
        sp = dict(sp, steps=steps)
        npx = sum(1 for st in steps if "px" in st)
        status = "ours-fails" if (bad_o or str(r.get("ours", "")).startswith("ERROR")) else "agrees"
        sp["foundry"]["status"] = status
        if status == "ours-fails":
            sp["gaps"] = ["x3d.lab Foundry: the bridge differs from X_ITE at %d of %d pinned pixels (round %d); this fixture "
                          "lives in fixtures/foundry/ so the default run stays green until it passes" % (len(bad_o), npx, rnd.n)]
        s, xml, c = names[n]
        d = rnd.dir / "kit" / "x3d.loom" / "x3d.webgpu" / "bridge" / "fixtures" / "foundry"
        d.mkdir(parents=True, exist_ok=True)
        (d / (n + ".x3d")).write_text(hog_flat(xml), encoding="utf-8")
        (d / (n + ".test.json")).write_text(json.dumps(sp, indent=1), encoding="utf-8")
        a = {"kind": "loom-fixture", "target": "loom", "path": os.path.relpath(d / (n + ".test.json"), rnd.dir), "name": n,
             "node": s.node, "status": status, "pixels": npx, "dropped": len(bad_x), "ours_fails": len(bad_o)}
        out.append(a)
        if c is not None:
            c.artifacts.append(a["path"])
    return out


MCP_TEST_HEAD = '''"""Written by the x3d.lab Foundry, round %(n)d (%(when)s). Each case is a scene a model wrote, shrunk to the smallest
form that still shows the problem. A strict xfail passes while the bug is there and fails (XPASS) the day it is
fixed: then remove the marker and keep the test as a regression test."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from validation.validate import validate_xml
'''


def _test_name(rnd: Round, i: int, c: Claim) -> str:
    return "test_r%d_%02d_%s_%s" % (rnd.n, i, c.kind.replace("-", "_"), re.sub(r"\W", "_", c.node.lower()))


def mcp_tests(rnd: Round, claims: list) -> str:
    parts = [MCP_TEST_HEAD % {"n": rnd.n, "when": time.strftime("%Y-%m-%d")}]
    for i, c in enumerate(claims, 1):
        s = rnd.scene(c.scene)
        xml = (c.shrunk or {}).get("xml") or (s.draft if c.which == "draft" else s.xml)
        xml = xml.replace("'''", "' ''")
        name = _test_name(rnd, i, c)
        reason = c.summary.replace('"', "'")[:200]
        want = "not r[\"valid\"]" if c.kind == "fork-accepts-invalid" else "r[\"valid\"]"
        parts.append('\nCASE_%d = r\'\'\'%s\'\'\'\n\n\n@pytest.mark.xfail(strict=True, reason="%s")\ndef %s():\n'
                     '    r = validate_xml(CASE_%d, "%s")\n    assert %s, r.get("errors", [])[:3]\n'
                     % (i, xml.strip(), reason, name, i, c.version, want))
    return "\n".join(parts)


def write_mcp_tests(rnd: Round, mcp_dir: Path | None) -> list[dict]:
    """x3d_mcp tests in its own style for the validator claims; checked against the fork itself (each case must xfail)."""
    claims = [c for c in rnd.claims if c.kind in ("fork-rejects-valid", "fork-accepts-invalid", "xsd-stricter-than-uom")
              and not c.known and not c.same_as]
    if not claims:
        return []
    text = mcp_tests(rnd, claims)
    verified = {}
    if mcp_dir is not None and (Path(mcp_dir) / "tests").is_dir():
        probe = Path(mcp_dir) / "tests" / ("_foundry_check_%s.py" % rnd.id)
        probe.write_text(text, encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rxX", "-p", "no:cacheprovider", str(probe)],
                               cwd=mcp_dir, capture_output=True, text=True, timeout=600)
            for ln in r.stdout.splitlines():
                m = re.match(r"(XFAIL|XPASS|FAILED|PASSED|ERROR)\S*\s+\S*::(test_\w+)", ln.strip())
                if m:
                    verified[m.group(2)] = m.group(1)
            if not verified and r.returncode not in (0, 1):
                verified["_error"] = (r.stdout + r.stderr)[-400:]
        except Exception as exc:
            verified["_error"] = str(exc)[:300]
        finally:
            probe.unlink(missing_ok=True)
    if verified and "_error" not in verified:          # keep the cases that reproduce through validate_xml itself
        keep = [c for i, c in enumerate(claims, 1) if verified.get(_test_name(rnd, i, c)) == "XFAIL"]
    else:                                               # could not run pytest here: keep all, say they are unverified
        keep = list(claims)
    if not keep:
        return [{"kind": "x3d_mcp-tests", "target": "x3d_mcp", "path": "", "note": "no case reproduced through validate_xml",
                 "verified": verified}]
    text = mcp_tests(rnd, keep)
    d = rnd.dir / "kit" / "x3d_mcp" / "tests"
    d.mkdir(parents=True, exist_ok=True)
    f = d / ("test_foundry_r%d_%s.py" % (rnd.n, rnd.id))
    f.write_text(text, encoding="utf-8")
    for c in keep:
        c.artifacts.append(os.path.relpath(f, rnd.dir))
    return [{"kind": "x3d_mcp-tests", "target": "x3d_mcp", "path": os.path.relpath(f, rnd.dir), "cases": len(keep),
             "dropped": len(claims) - len(keep), "verified": verified}]


ISSUE_TITLES = {
    "fork-rejects-valid": "validate_x3d rejects a scene that x3d-{v}.xsd accepts ({node})",
    "fork-accepts-invalid": "validate_x3d accepts a scene that x3d-{v}.xsd rejects ({node})",
    "xsd-stricter-than-uom": "x3d-{v}.xsd rejects a {node} placement the X3D object model allows",
    "xite-error": "X_ITE fails on a valid scene ({node})",
    "blender-import": "Import fails on a valid X3D {v} scene ({node})",
    "blender-unsupported": "The X3D importer skips {what}",
    "blender-flat": "The X3D importer shades {what} flat",
    "blender-export-invalid": "X3D export of an imported scene is not valid X3D ({node})",
    "gltf-invalid": "glTF export of an imported X3D scene fails the Khronos glTF Validator ({node})",
    "gltf-lost": "X3D -> Blender -> glTF loses part of the picture ({node})",
    "gltf-colour": "X3D -> Blender -> glTF keeps the shapes but not the colours ({node})",
    "loom-error": "The bridge throws on a valid scene ({node})",
    "loom-differs": "The bridge draws a different picture than X_ITE ({node})",
    "loom-colour": "The bridge draws the right shapes in the wrong colours ({what})",
}


def issue_markdown(rnd: Round, c: Claim) -> str:
    s = rnd.scene(c.scene)
    xml = (c.shrunk or {}).get("xml") or (s.draft if c.which == "draft" else s.xml)
    sz = ""
    if c.shrunk and "xml" in c.shrunk:
        sz = " (shrunk from %d to %d bytes, %d to %d nodes, in %d re-checks)" % (
            c.shrunk["before"]["bytes"], c.shrunk["after"]["bytes"], c.shrunk["before"]["nodes"], c.shrunk["after"]["nodes"], c.shrunk["checks"])
    what = c.signature.split("|", 1)[1] if "|" in c.signature else ""
    lines = ["# %s" % ISSUE_TITLES.get(c.kind, c.kind).format(v=c.version, node=c.node, what=what), "",
             "**For:** %s  " % TARGETS.get(c.target, c.target), "**Claim:** %s" % c.summary, ""]
    if c.also:
        lines += ["The same thing happened in %d other scene(s) this round: %s." % (len(c.also), ", ".join(c.also[:6])), ""]
    lines += ["## Smallest scene that shows it%s" % sz, "", "```xml", xml.strip(), "```", "", "## What each judge said", ""]
    j = s.referee.get(c.which) or {}
    lines.append("- x3d_mcp (fork): %s" % ("valid" if j.get("fork", {}).get("ok") else "; ".join(j.get("fork", {}).get("messages", [])[:2]) or "invalid"))
    lines.append("- lxml + x3d-%s.xsd: %s" % (c.version, "valid" if j.get("xsd", {}).get("ok") else j.get("xsd", {}).get("first", "")[:300]))
    if j.get("uom", {}).get("why"):
        lines.append("- X3D object model: %s" % j["uom"]["why"])
    e = s.engines or {}
    if e.get("x_ite"):
        lines.append("- X_ITE %s: %s" % ((rnd.env.get("x_ite") or {}).get("version", "") if isinstance(rnd.env.get("x_ite"), dict) else "",
                                        "loads" if e["x_ite"].get("ok") and not e["x_ite"].get("errors") else "; ".join(e["x_ite"].get("errors") or ["fails"])))
    if e.get("blender_import"):
        lines.append("- Blender import: %s" % ("ok" if e["blender_import"].get("ok") else e["blender_import"].get("error")))
    if e.get("convert_glb"):
        cv = e["convert_glb"]
        lines.append("- glTF: validator %s; silhouette match with the X3D %s; %s" % (
            "clean" if (cv.get("validator") or {}).get("ok") else ", ".join((cv.get("validator") or {}).get("codes") or ["?"]),
            (cv.get("compare") or {}).get("match"), "; ".join((cv.get("losses") or {}).get("unexpected") or []) or "no unexpected loss"))
    if e.get("loom"):
        lines.append("- x3d.loom bridge: %s (silhouette match with X_ITE %s)" % (e["loom"].get("ours", "")[:160], (e["loom"].get("compare") or {}).get("match")))
    if c.evidence:
        lines += ["", "<details><summary>Evidence</summary>", "", "```json", json.dumps(c.evidence, indent=1, default=str)[:3000], "```", "</details>"]
    lines += ["", "## How it was found", "",
              "The x3d.lab Foundry, round %d: %s asked for a scene around **%s** (%s). Prompt: _%s_" % (
                  rnd.n, s.contender, s.node, c.which, s.prompt[:400]),
              "", "Reproduce: save the scene above and run it through the engine named in the title."]
    return "\n".join(lines) + "\n"


def write_issues(rnd: Round) -> list[dict]:
    out = []
    d = rnd.dir / "kit" / "issues"
    for k, c in enumerate([c for c in rnd.claims if not c.known and not c.expected and not c.same_as], 1):
        d.mkdir(parents=True, exist_ok=True)
        f = d / ("%02d-%s-%s-%s.md" % (k, c.target, c.kind, _safe(c.node)))
        f.write_text(issue_markdown(rnd, c), encoding="utf-8")
        c.artifacts.append(os.path.relpath(f, rnd.dir))
        out.append({"kind": "issue", "target": c.target, "path": os.path.relpath(f, rnd.dir), "title": f.read_text().splitlines()[0][2:]})
    return out


# ====================================================================== learning, scoreboard, state
def learn_rules(rnd: Round, st: dict) -> dict:
    """Errors in this round's drafts -> rules per contender. A rule stays while the mistake keeps happening; the
    newest and most frequent come first; at most eight per contender, so the system prompt stays short."""
    out = {}
    by = {}
    for s in rnd.scenes:
        dj = s.referee.get("draft") or {}
        j = dj.get("fork") or {}
        semantic = set(j.get("semantic") or [])
        for kind, msg in zip(j.get("kinds", []), j.get("messages", []) + [""] * 9):
            if dj.get("effective") and kind not in semantic:
                continue            # the XSD (or the object model) accepts the draft: the fork was wrong, not the model
            if kind in ("node-not-allowed", "missing-child") and msg and hog.uom_allows(s.draft, s.version, msg)[0] is True:
                continue            # this placement is allowed by the object model: the XSD is stricter, not the model wrong
            by.setdefault(s.contender, {}).setdefault(kind, []).append(msg)
    for label, kinds in by.items():
        old = dict(st["rules"].get(label, {}))
        for kind, msgs in kinds.items():
            base = RULE_TEXT.get(kind, "Avoid this mistake (%s)." % duel.kind_title(kind)).format(version=rnd.cfg.version)
            ex = next((m for m in msgs if m), "")
            text = base + (" Last time: \"%s\"" % ex[:140] if ex else "")
            prev = old.get(kind, {})
            old[kind] = {"text": text, "seen": prev.get("seen", 0) + len(msgs), "last_round": rnd.n}
        ranked = sorted(old.items(), key=lambda kv: (-kv[1]["last_round"], -kv[1]["seen"]))[:8]
        out[label] = dict(ranked)
    return out


def rules_for_duel(st_rules: dict) -> dict:
    return {label: "\n".join("- " + r["text"] for r in sorted(kinds.values(), key=lambda r: (-r["last_round"], -r["seen"])))
            for label, kinds in st_rules.items() if kinds}


def scoreboard(rnd: Round, st: dict) -> dict:
    rows = {}
    for s in rnd.scenes:
        r = rows.setdefault(s.contender, Counter())
        r["scenes"] += 1
        r["draft_valid"] += bool((s.referee.get("draft") or {}).get("fork", {}).get("ok"))
        r["final_valid"] += bool((s.referee.get("final") or {}).get("fork", {}).get("ok"))
        r["xsd_valid"] += bool((s.referee.get("final") or {}).get("xsd", {}).get("ok"))
        r["focus_used"] += s.focus_used
        r["sound"] += s.sound
    for c in rnd.claims:
        s = rnd.scene(c.scene)
        rows.setdefault(s.contender, Counter())["claims"] += 1 + len(c.also)
    prev = (st["rounds"][-1]["scoreboard"].get("contenders", {}) if st["rounds"] else {})
    contenders = {}
    for label, r in rows.items():
        n = max(1, r["scenes"])
        row = {k: r[k] for k in ("scenes", "draft_valid", "final_valid", "xsd_valid", "focus_used", "sound", "claims")}
        row["draft_rate"] = round(r["draft_valid"] / n, 3)
        p = prev.get(label)
        if p and p.get("scenes"):
            row["draft_rate_delta"] = round(row["draft_rate"] - p.get("draft_rate", 0), 3)
        row["rules_in_effect"] = len(rnd.rules_used.get(label, {}))
        contenders[label] = row
    engines = Counter()
    for s in rnd.scenes:
        e = s.engines or {}
        if e.get("x_ite"):
            engines["xite_run"] += 1
            engines["xite_ok"] += bool(e["x_ite"].get("ok") and not e["x_ite"].get("errors"))
        if e.get("blender_import"):
            engines["blender_run"] += 1
            engines["blender_ok"] += bool(e["blender_import"].get("ok"))
        if e.get("convert_glb"):
            engines["gltf_run"] += 1
            engines["gltf_valid"] += bool((e["convert_glb"].get("validator") or {}).get("ok"))
            iou = (e["convert_glb"].get("compare") or {}).get("match")
            engines["gltf_match"] += bool(iou is not None and iou >= GLTF_MATCH)
        if e.get("loom"):
            engines["loom_run"] += 1
            iou = (e["loom"].get("compare") or {}).get("match")
            engines["loom_match"] += bool(iou is not None and iou >= LOOM_MATCH and not e["loom"].get("error"))
    kinds = Counter(c.kind for c in rnd.claims)
    return {"contenders": contenders, "engines": dict(engines), "claims": dict(kinds),
            "new_claims": sum(1 for c in rnd.claims if not c.known and not c.expected and not c.same_as),
            "artifacts": dict(Counter(a["kind"] for a in rnd.artifacts))}


def update_state(rnd: Round, st: dict) -> None:
    dis = Counter()
    for c in rnd.claims:
        if not c.expected:
            dis[c.node] += 1
        rec = st["signatures"].setdefault(c.signature, {"kind": c.kind, "target": c.target, "first_round": rnd.n, "count": 0})
        rec["count"] += 1 + len(c.also)
        rec["last_round"] = rnd.n
        # reported = an issue went out with the smallest scene (or there is nothing to shrink): later rounds only count it
        rec["reported"] = bool(rec.get("reported") or (c.artifacts and ((c.shrunk or {}).get("xml") or c.kind in NO_SHRINK
                                                                          or not rnd.cfg.shrink)))
        if c.same_as:
            rec["same_as"] = c.same_as
    for s in rnd.scenes:
        rec = st["nodes"].setdefault(s.node, {})
        rec["tried"] = rec.get("tried", 0) + 1
        rec["valid"] = rec.get("valid", 0) + bool((s.referee.get("final") or {}).get("effective"))
        rec["focus_used"] = rec.get("focus_used", 0) + s.focus_used
        rec["last_round"] = rnd.n
    for t in rnd.targets:
        st["nodes"].setdefault(t["node"], {})["disagreements_last"] = dis.get(t["node"], 0)
    if rnd.cfg.learn:
        st["rules"] = {**st["rules"], **rnd.rules_next}
    st["rounds"].append({"n": rnd.n, "id": rnd.id, "name": rnd.name, "dir": str(rnd.dir), "started": rnd.started, "ended": rnd.ended,
                         "targets": [t["node"] for t in rnd.targets], "scoreboard": rnd.scoreboard, "errors": rnd.errors})


# ====================================================================== the round
def _mcp_dir(lab) -> Path | None:
    for p in (getattr(lab, "mcp_dir", None), runtime.home() / "repos" / "x3d_mcp"):
        if p and (Path(p) / "src" / "validation" / "validate.py").exists():
            return Path(p)
    return None


def run(lab, cfg: FoundryConfig | None = None, **kw) -> Round:
    """One round. Blocking. Needs lab = x3dlab.up(); lab.setup_blender() for the Blender and glTF engines."""
    cfg = cfg or FoundryConfig(**kw)
    sp = state_path(cfg)
    st = load_state(sp)
    n = len(st["rounds"]) + 1
    rid = uuid.uuid4().hex[:8]
    rnd = Round(cfg, rid, n, cfg.name or "foundry-r%d-%s" % (n, rid), runtime.sub("out", "foundry_r%d_%s" % (n, rid)))
    rng = random.Random(cfg.seed or n)
    bus.emit("foundry round %d started" % n, src="foundry", kind="foundry_start", job=rid)

    def stage(name, fn):
        t0 = time.time()
        try:
            with bus.span("foundry: %s" % name, src="foundry", job=rid):
                fn()
        except Exception as exc:
            rnd.errors[name] = "%s: %s" % (type(exc).__name__, str(exc)[:500])
            bus.error("foundry %s failed: %s" % (name, rnd.errors[name]), src="foundry", job=rid)
        rnd.timings[name] = round(time.time() - t0, 1) + rnd.timings.get(name, 0)
        write(rnd)

    loom_dir = {"p": None}
    loom_st = {}

    def st_target():
        corpus = any(duel.parse_contender(c)[0] == "corpus" for c in cfg.contenders)
        if "loom" in cfg.engines or not cfg.nodes or (corpus and not _CORPUS):     # the bridge, its NODES.json, its fixtures
            try:
                loom_dir["p"] = hog.loom_checkout(cfg.loom_ref, cfg.loom_dir)
            except Exception as exc:
                rnd.errors["loom checkout"] = str(exc)[:300]
        loom_st.update(loom_status(loom_dir["p"]))
        if corpus:
            corpus_scenes(loom_dir["p"])
        rnd.targets = choose_targets(cfg, st, loom_st, rng)
        rnd.prompts = [prompt_for(t, rng) for t in rnd.targets]

    def st_generate():
        rnd.rules_used = st["rules"] if cfg.learn else {}
        budget = cfg.max_requests
        for version in sorted({p["version"] for p in rnd.prompts}):
            ps = [p for p in rnd.prompts if p["version"] == version]
            dcfg = duel.DuelConfig(contenders=list(cfg.contenders), prompts=ps, version=version, rounds=cfg.repair_rounds,
                                   parallel=cfg.parallel, max_requests=budget, name="%s-%s" % (rnd.name, version),
                                   rules=rules_for_duel(rnd.rules_used))
            est = duel.estimate(dcfg)
            budget -= est["requests_worst"] if est["within_budget"] else 0
            drun = aio.run(duel.run_async(lab.client, dcfg), 7200)
            rnd.duels.append({"name": drun.name, "dir": str(drun.dir), "version": version, "summary": drun.summary(),
                              "skipped": drun.skipped, "estimate": est})
            by_prompt = {p["id"]: p for p in ps}
            for a in drun.attempts:
                if a.error and not a.files:
                    rnd.errors["generate %s %s" % (a.contender, a.prompt_id)] = a.error
                    continue
                read = lambda k: (drun.dir / a.files[k]).read_text(encoding="utf-8") if a.files.get(k) else ""   # noqa: E731
                xml, draft = read("final") or read("draft"), read("draft")
                p = by_prompt.get(a.prompt_id, {})
                types = hog._node_types(xml)
                s = Scene("%s.%s" % (a.prompt_id, _safe(a.contender)), p.get("focus", a.prompt_id), version, a.contender,
                          p.get("text", ""), xml, draft, a.stage, p.get("focus") in types, types)
                d = rnd.dir / "scenes"
                d.mkdir(exist_ok=True)
                (d / ("%s.final.x3d" % _safe(s.id))).write_text(xml, encoding="utf-8")
                if draft:
                    (d / ("%s.draft.x3d" % _safe(s.id))).write_text(draft, encoding="utf-8")
                rnd.scenes.append(s)

    def st_referee():
        async def go():
            for s in rnd.scenes:
                s.referee["final"] = await judge(lab.client, s.xml, s.version, rid)
                if s.draft and s.draft != s.xml:
                    s.referee["draft"] = await judge(lab.client, s.draft, s.version, rid)
                else:
                    s.referee["draft"] = s.referee["final"]
                f = s.referee["final"]
                s.sound = bool(f.get("effective") and not f["fork"].get("semantic"))
        aio.run(go(), 3600)

    bench = {"b": None}

    def st_engines():
        bench["b"] = Bench(lab, rnd, loom_dir["p"])
        _engines(lab, rnd, loom_dir["p"], bench["b"])

    def st_claims():
        rnd.claims = find_claims(rnd, loom_st)
        for c in rnd.claims:
            rec = st["signatures"].get(c.signature) or {}
            c.known = bool(rec.get("reported") or rec.get("same_as"))

    def st_shrink():
        if cfg.shrink:
            shrink_claims(rnd, bench["b"] or Bench(lab, rnd, loom_dir["p"]))

    def st_write():
        b = bench["b"] or Bench(lab, rnd, loom_dir["p"])
        if "loom" in cfg.engines:
            try:
                rnd.artifacts += write_loom_fixtures(rnd, b, loom_st)
            except Exception as exc:
                rnd.errors["write loom"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        rnd.artifacts += write_mcp_tests(rnd, _mcp_dir(lab))
        rnd.artifacts += write_issues(rnd)
        if (rnd.dir / "kit").is_dir():
            (rnd.dir / "kit" / "README.md").write_text(kit_readme(rnd), encoding="utf-8")
            zf = rnd.dir / ("foundry_kit_r%d_%s.zip" % (rnd.n, rid))
            with zipfile.ZipFile(zf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted((rnd.dir / "kit").rglob("*")):
                    if f.is_file():
                        z.write(f, os.path.relpath(f, rnd.dir / "kit"))
            rnd.env["kit"] = os.path.relpath(zf, rnd.dir)

    def st_learn():
        if cfg.learn:
            rnd.rules_next = learn_rules(rnd, st)

    for name, fn in (("target", st_target), ("generate", st_generate), ("referee", st_referee), ("engines", st_engines),
                     ("claims", st_claims), ("shrink", st_shrink), ("write", st_write), ("learn", st_learn)):
        stage(name, fn)
        if name == "generate" and not rnd.scenes:
            break
    rnd.ended = time.time()
    rnd.scoreboard = scoreboard(rnd, st)
    update_state(rnd, st)
    save_state(sp, st)
    write(rnd)
    if bench["b"] is not None:
        shutil.rmtree(bench["b"].work, ignore_errors=True)
    bus.emit("foundry round %d finished: %d scenes, %d claims (%d new), %d artifacts" % (
        n, len(rnd.scenes), len(rnd.claims), rnd.scoreboard.get("new_claims", 0), len(rnd.artifacts)),
        src="foundry", kind="foundry_end", job=rid, data={"report": rnd.file_url("report.html"), "errors": rnd.errors})
    return rnd


def rounds(lab, n: int = 2, cfg: FoundryConfig | None = None, **kw) -> list[Round]:
    """Several rounds in a row: each starts from the state the one before left (targets, rules, known claims)."""
    cfg = cfg or FoundryConfig(**kw)
    return [run(lab, cfg) for _ in range(max(1, n))]


def estimate(cfg: FoundryConfig | None = None, **kw) -> dict:
    """Worst-case model requests for one round, before anything is sent."""
    cfg = cfg or FoundryConfig(**kw)
    paid = [c for c in cfg.contenders if duel.parse_contender(c)[0] not in duel.OFFLINE]
    n = len(cfg.nodes) or cfg.targets
    worst = len(paid) * n * (1 + max(0, cfg.repair_rounds))
    return {"contenders": len(cfg.contenders), "targets": n, "requests_worst": worst, "max_requests": cfg.max_requests,
            "within_budget": worst <= cfg.max_requests}


# ====================================================================== files and display
def kit_readme(rnd: Round) -> str:
    lines = ["# x3d.lab Foundry kit, round %d (%s)" % (rnd.n, rnd.id), "",
             "Each folder mirrors a repository. Copy what you want into a branch of that repository and open a PR.", ""]
    by = Counter(a["kind"] for a in rnd.artifacts)
    if by.get("loom-fixture"):
        lines += ["## x3d.loom/", "", "Bridge fixtures under `x3d.webgpu/bridge/fixtures/foundry/`. The default run only reads",
                  "`fixtures/*.test.json`, so these do not turn CI red; run them by name:", "",
                  "    node tools/run-tests.js --babylon <babylon.js> foundry/<Name>", "",
                  "`foundry.status` in each spec: `agrees` (a new regression test) or `ours-fails` (a target for the bridge).", ""]
    if by.get("x3d_mcp-tests"):
        lines += ["## x3d_mcp/", "", "Strict-xfail tests: they pass while the validator bug is there. When one turns XPASS,",
                  "the bug is fixed: drop its marker and keep it as a regression test.", "",
                  "    python -m pytest tests/test_foundry_*.py -rxX", ""]
    if by.get("issue"):
        lines += ["## issues/", "", "Drafts, one per new claim, with the smallest scene that shows it. Read before filing:",
                  "a claim is a disagreement between engines, and the engine named in the title is the one the others",
                  "point at, not always the one at fault.", ""]
    return "\n".join(lines)


def card(rnd: Round) -> dict:
    return {"round": rnd.n, "id": rnd.id, "name": rnd.name, "started": rnd.started, "ended": rnd.ended, "cfg": asdict(rnd.cfg),
            "targets": rnd.targets, "prompts": rnd.prompts, "duels": rnd.duels, "env": rnd.env, "timings": rnd.timings,
            "errors": rnd.errors, "scoreboard": rnd.scoreboard, "rules_used": rnd.rules_used, "rules_next": rnd.rules_next,
            "scenes": [{**{k: v for k, v in asdict(s).items() if k not in ("xml", "draft")}} for s in rnd.scenes],
            "claims": [asdict(c) for c in rnd.claims], "artifacts": rnd.artifacts}


def write(rnd: Round) -> None:
    rnd.dir.mkdir(parents=True, exist_ok=True)
    (rnd.dir / "foundry_card.json").write_text(json.dumps(card(rnd), indent=1, default=str))
    (rnd.dir / "report.html").write_text("<!doctype html><meta charset=utf-8><title>%s</title><body style='font:14px system-ui;"
                                         "max-width:1200px;margin:16px auto'>%s</body>" % (_esc(rnd.name), html(rnd)), encoding="utf-8")


def _esc(s) -> str:
    import html as h
    return h.escape(str(s if s is not None else ""))


def _img(rnd: Round, rel: str | None, title: str = "") -> str:
    if not rel or not (rnd.dir / rel).exists():
        return "<div style='width:192px;height:108px;background:#eee;display:flex;align-items:center;justify-content:center;" \
               "color:#999;font-size:11px'>%s</div>" % _esc(title or "no picture")
    b64 = base64.b64encode((rnd.dir / rel).read_bytes()).decode()
    return "<img title='%s' src='data:image/png;base64,%s' style='width:192px;border:1px solid #ddd'>" % (_esc(title), b64)


def html(rnd: Round) -> str:
    sb = rnd.scoreboard or {}
    out = ["<h2 style='margin:4px 0'>Foundry round %d <span style='font-weight:normal;color:#666'>%s</span></h2>" % (rnd.n, _esc(rnd.name))]
    if rnd.errors:
        out.append("<div style='color:#a33'>%s</div>" % "<br>".join("%s: %s" % (_esc(k), _esc(v)) for k, v in rnd.errors.items()))
    out.append("<div style='color:#555;margin:4px 0 10px'>%d targets · %d scenes · %d claims (%d new) · %d artifacts · %s</div>" % (
        len(rnd.targets), len(rnd.scenes), len(rnd.claims), sb.get("new_claims", 0), len(rnd.artifacts),
        " · ".join("%s %ss" % (k, v) for k, v in rnd.timings.items())))
    if sb.get("contenders"):
        out.append("<table style='border-collapse:collapse;font-size:13px'><tr>%s</tr>" % "".join(
            "<th style='text-align:left;padding:2px 8px;border-bottom:1px solid #ccc'>%s</th>" % h for h in
            ("contender", "scenes", "draft valid", "Δ vs last", "final valid", "XSD valid", "used the node", "sound", "claims", "rules")))
        for label, r in sb["contenders"].items():
            d = r.get("draft_rate_delta")
            out.append("<tr>%s</tr>" % "".join("<td style='padding:2px 8px'>%s</td>" % _esc(v) for v in (
                label, r["scenes"], "%d (%.0f%%)" % (r["draft_valid"], 100 * r["draft_rate"]), "" if d is None else "%+.0f pts" % (100 * d),
                r["final_valid"], r["xsd_valid"], r["focus_used"], r["sound"], r["claims"], r["rules_in_effect"])))
        out.append("</table>")
    if sb.get("engines"):
        out.append("<div style='margin:8px 0;color:#333'>engines: %s</div>" % _esc(", ".join("%s %s" % kv for kv in sb["engines"].items())))
    if rnd.claims:
        out.append("<h3 style='margin:14px 0 4px'>Claims</h3><table style='border-collapse:collapse;font-size:13px'>")
        for c in rnd.claims:
            tag = "known" if c.known else "expected" if c.expected else ("same as above" if c.same_as else "NEW")
            sz = ("%d → %d bytes" % (c.shrunk["before"]["bytes"], c.shrunk["after"]["bytes"])) if (c.shrunk or {}).get("xml") else \
                 _esc((c.shrunk or {}).get("error", ""))
            out.append("<tr><td style='padding:2px 6px'><b>%s</b></td><td style='padding:2px 6px'>%s</td><td style='padding:2px 6px'>%s</td>"
                       "<td style='padding:2px 6px'>%s%s</td><td style='padding:2px 6px;color:#666'>%s</td></tr>" % (
                           tag, _esc(TARGETS.get(c.target, c.target)), _esc(c.node), _esc(c.summary),
                           (" <i style='color:#777'>(%s)</i>" % _esc(c.expected)) if c.expected else "", sz))
        out.append("</table>")
    if rnd.artifacts:
        out.append("<h3 style='margin:14px 0 4px'>Written</h3><ul style='margin:0'>%s</ul>" % "".join(
            "<li>%s: <code>%s</code> %s</li>" % (_esc(a["kind"]), _esc(a.get("path")), _esc(a.get("status") or a.get("title") or a.get("note") or ""))
            for a in rnd.artifacts))
        if rnd.env.get("kit"):
            out.append("<div>kit: <code>%s</code></div>" % _esc(rnd.env["kit"]))
    if rnd.rules_next:
        out.append("<h3 style='margin:14px 0 4px'>Rules for the next round</h3>")
        for label, kinds in rnd.rules_next.items():
            out.append("<div><b>%s</b><ul style='margin:2px 0'>%s</ul></div>" % (_esc(label), "".join(
                "<li>%s <span style='color:#888'>(seen %d)</span></li>" % (_esc(r["text"]), r["seen"]) for r in kinds.values())))
    gal = [s for s in rnd.scenes if s.engines]
    if gal:
        out.append("<h3 style='margin:14px 0 4px'>Gallery: X_ITE · bridge (Babylon) · glTF via X_ITE%s</h3>" % (" · Cycles" if rnd.cfg.cycles else ""))
        for s in gal:
            e = s.engines
            cells = [_img(rnd, (e.get("x_ite") or {}).get("png"), "X_ITE"), _img(rnd, (e.get("loom") or {}).get("png_ours"), "bridge"),
                     _img(rnd, (e.get("convert_glb") or {}).get("xite_png"), "glTF via X_ITE")]
            if rnd.cfg.cycles:
                f = "scenes/%s.cycles.png" % re.sub(r"[^A-Za-z0-9._@-]", "_", s.id)
                cells.append(_img(rnd, f, "Cycles"))
            lm, cv = (e.get("loom") or {}).get("compare") or {}, (e.get("convert_glb") or {}).get("compare") or {}
            out.append("<div style='margin:8px 0'><div><b>%s</b> · %s · %s · bridge match %s · glTF match %s</div><div style='display:flex;gap:6px'>%s</div></div>" % (
                _esc(s.node), _esc(s.contender), "uses the node" if s.focus_used else "<span style='color:#a33'>did not use the node</span>",
                lm.get("match"), cv.get("match"), "".join(cells)))
    return "".join(out)


def show(rnd: Round, lab=None) -> None:
    from .ui import show as _show
    links = ""
    if lab is not None and hasattr(lab, "dashboard_url"):
        base = lab.dashboard_url()
        files = ["report.html", "foundry_card.json"] + ([rnd.env["kit"]] if rnd.env.get("kit") else [])
        links = "<div style='font-size:12px;margin:6px 0'>%s</div>" % " · ".join(
            "<a href='%s%s' target=_blank>%s</a>" % (_esc(base), _esc(rnd.file_url(f)), _esc(f)) for f in files)
    _show("<div style='font:14px system-ui'>%s%s</div>" % (links, html(rnd)))
