"""Model duel: the same prompts to several models, with x3d_mcp as the referee.

    cfg = duel.DuelConfig(contenders=["nvidia", "anthropic", "nvidia:meta/llama-3.3-70b-instruct"], prompts=8)
    print(duel.estimate(cfg))        # model requests, worst case, before anything is spent
    run = lab.duel(cfg)              # or duel.run(lab, cfg)
    duel.show(run)                   # scorecard, error profile, prompt-by-prompt grid
    run.card_path                    # JSON: every scene, verdict, error kind, tokens and timing

Every scene goes through the same four steps:
  1 draft    the model writes the whole document from the prompt
  2 check    validate_x3d + validate_semantic; each error gets a kind (boolean-case, unknown-field, ...)
  3 server   repair_x3d's deterministic fixes (no model), then check again
  4 model    up to `rounds` turns: the model gets the validator's report and exact-fix hints, then check again

Error kinds come from the validator's own messages, so they mean the same thing for every model: a model that
keeps writing solid="True" shows up as boolean-case, whichever provider it runs on. The `sample` contender needs
no key: it answers with the lantern scene plus one known defect per prompt, so the whole pipeline can be seen and
tested for free.
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import aio, breakit, keys, llm, runtime
from .events import bus
from .quickstart import REPAIR, SYSTEM, _hints_text
from .scenes import LANTERN

# ---------------------------------------------------------------------- prompts
# Each prompt leans on one thing models get wrong in X3D; `trap` says which.
PROMPTS = [
    {"id": "lantern", "trap": "ROUTE + interpolator lengths",
     "text": "A paper lantern that spins slowly forever: a glowing orange box body with a red cone roof, animated with a "
             "TimeSensor and an OrientationInterpolator."},
    {"id": "orrery", "trap": "DEF/USE and nested transforms",
     "text": "A small orrery: a yellow sun in the middle and three planets on nested rotating arms. Define the planet "
             "geometry once and reuse it with USE for the other two."},
    {"id": "pbr", "trap": "PhysicalMaterial needs its component",
     "text": "A polished metal sphere and a rough stone cube on a dark floor, using physically based materials "
             "(PhysicalMaterial with metallic and roughness)."},
    {"id": "lights", "trap": "light fields and value ranges",
     "text": "A white sculpture lit by a warm spot light from the left, a cool blue point light from the right and a dim "
             "directional fill light."},
    {"id": "text", "trap": "Text/FontStyle fields, Billboard",
     "text": "The words 'Hello X3D' in large bold sans-serif letters on a Billboard that always faces the viewer, above a "
             "thin pedestal."},
    {"id": "pyramid", "trap": "IndexedFaceSet indices and colours",
     "text": "A square pyramid built with an IndexedFaceSet, each of the five faces a different colour "
             "(colorPerVertex false)."},
    {"id": "colour-wheel", "trap": "colour ranges and counts",
     "text": "Twelve small boxes in a ring, coloured like a colour wheel: red, orange, yellow through to magenta."},
    {"id": "staircase", "trap": "repetition and arithmetic",
     "text": "A spiral staircase of 16 steps around a central pole, each step rotated 22.5 degrees and raised 0.2 m."},
    {"id": "traffic-light", "trap": "Switch + IntegerSequencer ROUTE types",
     "text": "A traffic light that cycles red, yellow, green every few seconds using a TimeSensor, an IntegerSequencer "
             "and a Switch (no Script node)."},
    {"id": "terrain", "trap": "ElevationGrid sizes, Fog, NavigationInfo",
     "text": "Rolling hills made with an ElevationGrid (8 by 8 heights), light fog, and walking navigation."},
    {"id": "touch", "trap": "sensor events without Script",
     "text": "A button you can click: a box with a TouchSensor; clicking it starts a one-shot colour animation from "
             "grey to bright green (no Script node)."},
    {"id": "gallery", "trap": "several Viewpoints, descriptions",
     "text": "A small gallery room with three framed panels on the walls and three named viewpoints, one in front of each "
             "panel."},
]
BY_PROMPT = {p["id"]: p for p in PROMPTS}

BARE_SYSTEM = "You write X3D {version} scenes in the XML encoding. Reply with only the XML document."

# The sample contender: one known defect per prompt, cycling, so the profile has something in every column.
SAMPLE_DEFECTS = ["control", "python_bool", "field_typo", "missing_def", "key_mismatch", "pbr_no_component",
                  "short_colour", "route_types", "unclosed_tag", "duplicate_def", "use_before_def", "unknown_node"]

# ---------------------------------------------------------------------- error kinds
KIND_TITLES = {
    "no-scene": "no X3D in the reply",
    "not-xml": "not well-formed XML",
    "boolean-case": "True/False instead of true/false",
    "unknown-field": "field that does not exist",
    "node-not-allowed": "unknown node, or a node in the wrong place",
    "value-format": "wrong number of values, or out of range",
    "wrong-type": "value of the wrong type",
    "bad-enum": "value not in the allowed list",
    "duplicate-def": "same DEF name twice",
    "missing-attribute": "required attribute missing",
    "missing-child": "required child node missing",
    "schema-other": "other schema error",
}
_XSD_KINDS = [
    (re.compile(r"'(True|False)' is not a valid value of the atomic type 'SFBool'"), "boolean-case"),
    (re.compile(r"The attribute '[^']+' is not allowed"), "unknown-field"),
    (re.compile(r"This element is not expected"), "node-not-allowed"),
    (re.compile(r"\[facet '(pattern|minInclusive|maxInclusive|minExclusive|maxExclusive|length|maxLength|minLength)'\]"),
     "value-format"),
    (re.compile(r"is not an element of the set"), "bad-enum"),
    (re.compile(r"atomic type 'xs:ID'"), "duplicate-def"),
    (re.compile(r"is required but missing"), "missing-attribute"),
    (re.compile(r"Missing child element"), "missing-child"),
    (re.compile(r"is not a valid value of the (atomic|list|union) type"), "wrong-type"),
]


def classify(issue) -> str:
    """One stable kind per validator error: XSD messages by pattern, semantic errors by their rule id."""
    if issue.layer == "input":
        return "no-scene"
    if issue.layer == "xml":
        return "not-xml"
    if issue.layer == "semantic":
        return issue.rule or "semantic-other"
    for pat, kind in _XSD_KINDS:
        if pat.search(issue.message or ""):
            return kind
    return "schema-other"


def kinds_of(verdict) -> list[str]:
    return [classify(i) for i in verdict.errors]


def kind_title(kind: str) -> str:
    return KIND_TITLES.get(kind, "semantic: " + kind)


# ---------------------------------------------------------------------- config and results
@dataclass
class DuelConfig:
    contenders: list = field(default_factory=lambda: ["auto"])   # "provider" or "provider:model"; "sample" needs no key
    prompts: object = 6                    # a number (the first N built-in prompts), a list of ids, or a list of texts
    version: str = "4.0"
    system: str = "coached"                # coached = the Quickstart rules; bare = one line, measures the model alone
    rounds: int = 1                        # model repair turns after the server's exact fixes (0 = none)
    server_fixes: bool = True
    parallel: int = 4                      # scenes in flight at once (NVIDIA is also paced to its per-minute limit)
    max_requests: int = 200                # refuse to start if the worst case needs more model requests than this
    name: str = ""
    rules: dict = field(default_factory=dict)   # extra system-prompt lines: key "*", a provider, or a contender spec/label

    def prompt_list(self) -> list[dict]:
        p = self.prompts
        if isinstance(p, int):
            return [dict(x) for x in PROMPTS[:max(1, min(p, len(PROMPTS)))]]
        out = []
        for i, x in enumerate(p or []):
            if isinstance(x, dict):
                out.append({"id": str(x.get("id") or "p%d" % (i + 1)), "trap": x.get("trap", ""), "text": str(x["text"]),
                            **({"focus": x["focus"]} if x.get("focus") else {})})
            elif x in BY_PROMPT:
                out.append(dict(BY_PROMPT[x]))
            else:
                out.append({"id": "p%d" % (i + 1), "trap": "", "text": str(x)})
        return out


@dataclass
class Contender:
    spec: str
    provider: str
    model: str
    label: str
    key: str = field(default="", repr=False)


@dataclass
class Attempt:
    contender: str
    provider: str
    model: str
    prompt_id: str
    draft_ok: bool = False
    draft_kinds: list = field(default_factory=list)
    server_changes: int = 0
    server_ok: bool = False
    final_ok: bool = False
    final_kinds: list = field(default_factory=list)
    rounds: int = 0
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    seconds: float = 0.0
    model_seconds: float = 0.0
    error: str | None = None
    files: dict = field(default_factory=dict)

    @property
    def stage(self) -> str:
        """Where this scene became valid: draft, server, model, or never."""
        if self.draft_ok:
            return "draft"
        if self.server_ok:
            return "server"
        if self.final_ok:
            return "model"
        return "never"


@dataclass
class DuelRun:
    cfg: DuelConfig
    id: str
    name: str
    contenders: list
    prompts: list
    attempts: list = field(default_factory=list)
    started: float = field(default_factory=time.time)
    ended: float | None = None
    dir: Path | None = None
    card_path: Path | None = None
    skipped: dict = field(default_factory=dict)        # contender spec -> why it did not run

    def summary(self) -> list[dict]:
        out = []
        for c in self.contenders:
            rows = [a for a in self.attempts if a.contender == c.label]
            n = len(rows)
            if not n:
                continue
            def ok(test, rows=rows):
                return sum(1 for a in rows if test(a))
            kinds = Counter(k for a in rows for k in a.draft_kinds)
            out.append({
                "contender": c.label, "provider": c.provider, "model": c.model, "scenes": n,
                "draft_valid": ok(lambda a: a.draft_ok), "after_server": ok(lambda a: a.server_ok),
                "final_valid": ok(lambda a: a.final_ok), "server_rescues": ok(lambda a: not a.draft_ok and a.server_ok),
                "model_rescues": ok(lambda a: not a.server_ok and a.final_ok), "errors": ok(lambda a: a.error),
                "draft_errors": sum(len(a.draft_kinds) for a in rows), "kinds": dict(kinds.most_common()),
                "median_model_s": round(statistics.median([a.model_seconds for a in rows]), 2),
                "requests": sum(a.requests for a in rows), "tokens_in": sum(a.tokens_in for a in rows),
                "tokens_out": sum(a.tokens_out for a in rows)})
        return out

    def file_url(self, name: str) -> str:
        return "out/%s/%s" % (self.dir.name if self.dir else self.id, name)


def parse_contender(spec: str) -> tuple[str, str | None]:
    spec = (spec or "").strip()
    if ":" in spec and spec.split(":", 1)[0] in llm.PROVIDERS:
        p, m = spec.split(":", 1)
        return p, (m.strip() or None)
    return spec, None


def resolve(cfg: DuelConfig) -> tuple[list[Contender], dict]:
    """Turn specs into contenders with keys and models. Specs without a key are skipped, with the reason."""
    out, skipped, seen = [], {}, set()
    for spec in cfg.contenders:
        provider, model = parse_contender(spec)
        if provider in OFFLINE:
            c = Contender(spec, provider, OFFLINE[provider][0], provider)
        else:
            try:
                provider, key, model = keys.pick(provider, model)
            except Exception as exc:
                skipped[spec] = str(exc)
                continue
            if not llm.valid_model_id(provider, model):
                skipped[spec] = "not a model id: %r" % model
                continue
            c = Contender(spec, provider, model, "%s · %s" % (provider, model), key)
        if c.label in seen:
            skipped[spec] = "duplicate of %s" % c.label
            continue
        seen.add(c.label)
        out.append(c)
    return out, skipped


def estimate(cfg: DuelConfig) -> dict:
    """Worst-case model requests (every draft fails and uses every repair round). Nothing is sent."""
    n_prompts = len(cfg.prompt_list())
    paid = [s for s in cfg.contenders if parse_contender(s)[0] not in OFFLINE]
    per_scene = 1 + max(0, int(cfg.rounds))
    worst = len(paid) * n_prompts * per_scene
    nv = sum(1 for s in paid if parse_contender(s)[0] == "nvidia") * n_prompts * per_scene
    rpm = llm._rpm("nvidia")
    return {"contenders": len(cfg.contenders), "prompts": n_prompts, "scenes": len(cfg.contenders) * n_prompts,
            "requests_best": len(paid) * n_prompts, "requests_worst": worst, "nvidia_requests_worst": nv,
            "nvidia_minutes_at_limit": round(nv / rpm, 1) if (nv and rpm) else 0.0, "max_requests": cfg.max_requests,
            "within_budget": worst <= cfg.max_requests}


# ---------------------------------------------------------------------- the run
class _SampleConversation:
    """Stands in for a model: the lantern with one known defect; its 'repair' returns the clean lantern."""
    provider = "sample"

    def __init__(self, defect: str):
        self.defect = defect
        self.usage = {"input": 0, "output": 0, "calls": 0}

    def send(self, text: str):
        self.usage["calls"] += 1
        first = self.usage["calls"] == 1
        xml = breakit.BY_ID[self.defect].apply(LANTERN) if first else LANTERN
        return llm.Reply(xml, [], {"input": 0, "output": 0}, "built-in", "stop")


# Contenders that need no key or network: provider -> (description, factory(cfg, index, prompt) -> conversation).
# The Foundry adds "corpus" (known-good scenes for the focus node, some with a defect) so a round runs offline.
OFFLINE = {"sample": ("built-in defects", lambda cfg, index, prompt: _SampleConversation(SAMPLE_DEFECTS[index % len(SAMPLE_DEFECTS)]))}


def _conversation(c: Contender, cfg: DuelConfig, index: int, prompt: dict | None = None):
    if c.provider in OFFLINE:
        return OFFLINE[c.provider][1](cfg, index, prompt or {})
    system = (SYSTEM if cfg.system == "coached" else BARE_SYSTEM).format(version=cfg.version)
    extra = [cfg.rules.get(k) for k in ("*", c.provider, c.spec, c.label) if cfg.rules.get(k)]
    if extra:
        system += "\n\nLessons from earlier rounds (mistakes you made before; do not repeat them):\n" + "\n".join(extra)
    return llm.conversation(c.provider, c.key, c.model, system=system)


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:80] or "x"


async def _one(client, run: DuelRun, c: Contender, prompt: dict, index: int, sem: asyncio.Semaphore) -> Attempt:
    cfg = run.cfg
    a = Attempt(c.label, c.provider, c.model, prompt["id"])
    folder = run.dir / "scenes" / _slug(c.label)
    folder.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    async def ask(conv, text):
        t = time.time()
        with bus.span("%s %s" % (c.provider, prompt["id"]), track="LLM", src="duel", job=run.id) as info:
            reply = await asyncio.to_thread(conv.send, text)
            info.update(model=c.model, tokens_in=reply.usage.get("input"), tokens_out=reply.usage.get("output"))
        a.model_seconds += time.time() - t
        a.requests += 1
        a.tokens_in += int(reply.usage.get("input") or 0)
        a.tokens_out += int(reply.usage.get("output") or 0)
        return llm.extract_xml(reply.text)

    async with sem:
        try:
            conv = _conversation(c, cfg, index, prompt)
            xml = await ask(conv, prompt["text"])
            (folder / ("%s.draft.x3d" % prompt["id"])).write_text(xml, encoding="utf-8")
            a.files["draft"] = "scenes/%s/%s.draft.x3d" % (_slug(c.label), prompt["id"])
            if "<X3D" not in xml:
                v = None
                a.draft_kinds = ["no-scene"]
            else:
                v = await client.verdict(xml, job=run.id)
                a.draft_ok, a.draft_kinds = v.ok, kinds_of(v)
            a.server_ok = a.draft_ok
            if v is not None and not v.ok and cfg.server_fixes:
                fixed, changes, _ = await client.repair(xml, job=run.id)
                a.server_changes = len(changes)
                if changes and fixed != xml:
                    xml = fixed
                    v = await client.verdict(xml, job=run.id)
                    a.server_ok = v.ok
            ok = a.server_ok
            while not ok and a.rounds < cfg.rounds:
                a.rounds += 1
                report = v.report() if v is not None else "- your reply contained no X3D document"
                hints = await client.hints(xml, job=run.id) if v is not None else []
                xml = await ask(conv, REPAIR.format(report=report, hints=_hints_text(hints)))
                if "<X3D" not in xml:
                    v = None
                    continue
                v = await client.verdict(xml, job=run.id)
                ok = v.ok
                if not ok and cfg.server_fixes:
                    fixed, changes, _ = await client.repair(xml, job=run.id)
                    if changes and fixed != xml:
                        xml, v = fixed, await client.verdict(fixed, job=run.id)
                        ok = v.ok
            a.final_ok = bool(ok)
            a.final_kinds = kinds_of(v) if v is not None else ["no-scene"]
            (folder / ("%s.final.x3d" % prompt["id"])).write_text(xml, encoding="utf-8")
            a.files["final"] = "scenes/%s/%s.final.x3d" % (_slug(c.label), prompt["id"])
        except Exception as exc:
            a.error = "%s: %s" % (type(exc).__name__, str(exc)[:300])
            bus.warn("duel %s / %s failed: %s" % (c.label, prompt["id"], a.error), src="duel", job=run.id)
    a.seconds = round(time.time() - t0, 2)
    a.model_seconds = round(a.model_seconds, 2)
    bus.emit("duel %s · %s: %s" % (c.label, prompt["id"], "error" if a.error else "valid at " + a.stage),
             level="WARN" if a.error else "INFO", src="duel", kind="duel", job=run.id,
             data={"contender": c.label, "prompt": prompt["id"], "stage": a.stage, "draft_kinds": a.draft_kinds,
                   "requests": a.requests, "seconds": a.seconds})
    return a


async def run_async(client, cfg: DuelConfig) -> DuelRun:
    contenders, skipped = resolve(cfg)
    est = estimate(DuelConfig(**{**asdict(cfg), "contenders": [c.spec for c in contenders]}))
    if not est["within_budget"]:
        raise RuntimeError("this duel could need up to %d model requests, above max_requests=%d. Use fewer prompts, "
                           "contenders or rounds, or raise max_requests." % (est["requests_worst"], cfg.max_requests))
    if not contenders:
        raise RuntimeError("no contender can run: %s" % ("; ".join("%s: %s" % kv for kv in skipped.items()) or "none given"))
    rid = uuid.uuid4().hex[:8]
    run = DuelRun(cfg, rid, cfg.name or "duel-%s" % rid, contenders, cfg.prompt_list(), skipped=skipped)
    run.dir = runtime.sub("out", "duel_" + rid)
    bus.emit("duel %s started: %d contenders x %d prompts" % (run.name, len(contenders), len(run.prompts)), src="duel",
             kind="duel_start", job=rid, data={"contenders": [c.label for c in contenders], "skipped": skipped,
                                               "estimate": est})
    sem = asyncio.Semaphore(max(1, int(cfg.parallel)))
    tasks = [_one(client, run, c, p, i, sem) for c in contenders for i, p in enumerate(run.prompts)]
    run.attempts = list(await asyncio.gather(*tasks))
    run.ended = time.time()
    write_card(run)
    bus.emit("duel %s finished" % run.name, src="duel", kind="duel_end", job=rid,
             data={"summary": run.summary(), "card": run.file_url("duel_card.json")})
    return run


def run(lab_or_client, cfg: DuelConfig | None = None, **kw) -> DuelRun:
    """Blocking. Accepts the Lab handle from x3dlab.up() or an X3DMCP client."""
    cfg = cfg or DuelConfig(**kw)
    client = getattr(lab_or_client, "client", None) or lab_or_client
    return aio.run(run_async(client, cfg))


# ---------------------------------------------------------------------- card, report, display
def card(run: DuelRun) -> dict:
    return {"kind": "x3dlab.duel", "id": run.id, "name": run.name, "started": run.started, "ended": run.ended,
            "seconds": round((run.ended or time.time()) - run.started, 1), "config": asdict(run.cfg),
            "contenders": [{k: v for k, v in asdict(c).items() if k != "key"} for c in run.contenders],
            "skipped": run.skipped, "prompts": run.prompts, "summary": run.summary(),
            "attempts": [dict(asdict(a), stage=a.stage) for a in run.attempts]}


def write_card(run: DuelRun) -> Path:
    d = card(run)
    run.card_path = run.dir / "duel_card.json"
    run.card_path.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
    (run.dir / "report.html").write_text("<!doctype html><meta charset='utf-8'><title>%s</title>"
                                         "<body style='font:14px system-ui;margin:24px'>%s</body>" % (run.name, html(run)),
                                         encoding="utf-8")
    return run.card_path


def _pct(n: int, d: int) -> str:
    return "%d%%" % round(100 * n / d) if d else "–"


STAGE_GLYPH = {"draft": ("✓", "#1d6b3a", "valid as written"), "server": ("S", "#2f6db5", "valid after the server's exact fixes"),
               "model": ("M", "#9a5b00", "valid after the model repaired it"), "never": ("✗", "#c62828", "never valid")}


def html(run: DuelRun) -> str:
    from .ui import esc
    summ = run.summary()
    head = ("<div><b>%s</b> · %d contender(s) × %d prompt(s) · X3D %s · %s prompt · %d repair round(s) · %.0f s</div>"
            % (esc(run.name), len(run.contenders), len(run.prompts), esc(run.cfg.version), esc(run.cfg.system),
               run.cfg.rounds, (run.ended or time.time()) - run.started))
    if run.skipped:
        head += "<div style='color:#9a5b00;font-size:12px'>skipped: %s</div>" % esc("; ".join("%s (%s)" % kv for kv in run.skipped.items()))
    th = "".join("<th style='text-align:%s;padding:2px 8px'>%s</th>" % (al, t) for al, t in (
        ("left", "contender"), ("right", "valid as written"), ("right", "+ server fixes"), ("right", "+ model repair"),
        ("right", "errors in drafts"), ("right", "median model s"), ("right", "requests"), ("right", "tokens in / out")))
    rows = "".join(
        "<tr><td style='padding:2px 8px'>%s</td><td align=right>%s</td><td align=right>%s</td><td align=right>%s</td>"
        "<td align=right>%d</td><td align=right>%.1f</td><td align=right>%d</td><td align=right>%s / %s</td></tr>"
        % (esc(s["contender"]), _pct(s["draft_valid"], s["scenes"]), _pct(s["after_server"], s["scenes"]),
           _pct(s["final_valid"], s["scenes"]), s["draft_errors"], s["median_model_s"], s["requests"],
           format(s["tokens_in"], ","), format(s["tokens_out"], ",")) for s in summ)
    score = "<table style='border-collapse:collapse;font-size:13px;margin:8px 0'><tr>%s</tr>%s</table>" % (th, rows)
    # error profile: kinds x contenders (draft errors)
    kinds = sorted({k for s in summ for k in s["kinds"]}, key=lambda k: -sum(s["kinds"].get(k, 0) for s in summ))
    prof = ""
    if kinds:
        cols = "".join("<th style='padding:2px 8px;font-weight:500'>%s</th>" % esc(s["contender"]) for s in summ)
        body = ""
        for k in kinds:
            cells = ""
            for s in summ:
                n = s["kinds"].get(k, 0)
                shade = "background:rgba(198,40,40,%.2f)" % min(0.12 + 0.12 * n, 0.6) if n else ""
                cells += "<td align=center style='%s'>%s</td>" % (shade, n or "")
            body += "<tr><td style='padding:2px 8px' title='%s'>%s</td>%s</tr>" % (esc(kind_title(k)), esc(k), cells)
        prof = ("<div style='margin-top:10px'><b>Error profile</b> <span style='color:#666;font-size:12px'>(errors in the "
                "first drafts, by kind)</span></div><table style='border-collapse:collapse;font-size:12px'><tr><th></th>%s"
                "</tr>%s</table>" % (cols, body))
    # prompt grid
    grid_head = "".join("<th style='padding:2px 6px;font-weight:500'>%s</th>" % esc(p["id"]) for p in run.prompts)
    grid = ""
    for c in run.contenders:
        cells = ""
        for p in run.prompts:
            a = next((x for x in run.attempts if x.contender == c.label and x.prompt_id == p["id"]), None)
            if a is None:
                cells += "<td></td>"
                continue
            g, col, tip = ("!", "#888", a.error) if a.error else STAGE_GLYPH[a.stage]
            tip = "%s · %s" % (tip, ", ".join(a.draft_kinds) or "no errors") if not a.error else tip
            cells += "<td align=center style='color:%s;font-weight:600' title='%s'>%s</td>" % (col, esc(tip), g)
        grid += "<tr><td style='padding:2px 8px'>%s</td>%s</tr>" % (esc(c.label), cells)
    legend = " · ".join("<span style='color:%s;font-weight:600'>%s</span> %s" % (col, g, t) for g, col, t in STAGE_GLYPH.values())
    grid = ("<div style='margin-top:10px'><b>Prompt by prompt</b> <span style='color:#666;font-size:12px'>(%s · "
            "<b>!</b> error)</span></div><table style='border-collapse:collapse;font-size:12px'><tr><th></th>%s</tr>%s</table>"
            % (legend, grid_head, grid))
    return head + score + prof + grid


def show(run: DuelRun, lab=None) -> None:
    from .ui import esc, show as _show
    links = ""
    if lab is not None and hasattr(lab, "dashboard_url"):
        base = lab.dashboard_url()
        links = "<div style='font-size:12px'><a href='%s%s' target=_blank>report</a> · <a href='%s%s' target=_blank>duel card (JSON)</a></div>" % (
            esc(base), esc(run.file_url("report.html")), esc(base), esc(run.file_url("duel_card.json")))
    _show("<div style='font:14px system-ui'>%s%s</div>" % (html(run), links))
