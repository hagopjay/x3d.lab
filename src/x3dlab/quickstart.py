"""Prompt -> X3D -> validate -> repair -> view. The first loop a newcomer runs.

Two modes:
  simple  the model writes the whole document; x3d_mcp checks it; the server's own exact fixes run first
          (free, no model); then the model repairs from the validator's report, up to `rounds` times.
  agent   the model gets a handful of x3d_mcp tools (describe_node, where_can_place, validate_*, ...) and
          uses them while it writes, then submits the scene with a `submit_scene` tool. Same checks after.
Without any API key, provider="sample" runs the same checks on a built-in scene so the loop is still visible.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import keys, llm, runtime, scenes
from .events import bus
from .mcpclient import Verdict

SYSTEM = """You write X3D {version} scenes in the XML encoding. Reply with ONLY the XML document: no prose, no markdown fences.
Rules:
- Start with <?xml version="1.0" encoding="UTF-8"?> then <X3D profile="Immersive" version="{version}"> with <head> and <Scene>.
- Use only real X3D {version} nodes and exact field names. Booleans are lowercase true/false.
- Colours are three numbers in [0,1]. Vectors have exactly the right number of values.
- Include a NavigationInfo, a Viewpoint that frames the content, at least one light and a Background.
- Use Shape/Appearance/Material. If you use PhysicalMaterial, add <component name="Shape" level="2"/> to <head>.
- A USE must come after its DEF. ROUTEs go after the nodes they name and connect matching field types.
- Interpolators: keyValue has one value per key (e.g. 3 keys -> 3 SFRotations = 12 numbers).
- No Script nodes and no external files (no textures or Inline). Keep it under 200 lines."""

REPAIR = """The validator rejected your scene. Fix exactly what it names and nothing else; keep the design.
Reply with the complete corrected X3D XML document only.

Validator report:
{report}
{hints}"""

AGENT_TOOLS = ("describe_node", "list_nodes", "where_can_place", "x3d_animation_info",
               "validate_x3d", "validate_semantic", "exact_fix_hints", "repair_x3d")

SUBMIT_TOOL = {
    "name": "submit_scene",
    "description": "Submit your finished X3D XML document. Call this once, when validate_x3d and validate_semantic are clean.",
    "input_schema": {"type": "object", "properties": {"xml": {"type": "string", "description": "The complete X3D XML document"}},
                     "required": ["xml"]},
}

AGENT_TASK = """Build this scene as an X3D {version} XML document (profile Immersive unless a node needs more):

{prompt}

Work like this: look up any node you are unsure of with describe_node, write the document, check it with
validate_x3d AND validate_semantic, fix what they report (repair_x3d can apply exact fixes), and finally call
submit_scene with the complete document. Keep it under 200 lines; no Script nodes and no external files."""


@dataclass
class SceneResult:
    id: str
    prompt: str
    provider: str
    model: str
    mode: str
    valid: bool = False
    first_pass_valid: bool | None = None
    rounds: int = 0
    tool_calls: int = 0
    verdict: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0
    files: dict = field(default_factory=dict)
    error: str | None = None
    xml: str = ""

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("xml")
        d.pop("steps")
        return d


def out_dir(job_id: str) -> Path:
    return runtime.sub("out", job_id)


def _hints_text(hints: list, limit: int = 8) -> str:
    lines = [h.get("message") for h in hints[:limit] if h.get("message")]
    return ("\nExact fixes the validator suggests:\n" + "\n".join("- " + m for m in lines)) if lines else ""


async def make_scene(client, prompt: str, provider: str = "auto", model: str | None = None, mode: str = "simple",
                     rounds: int = 2, version: str = "4.0", job: str | None = None, max_tool_calls: int = 14) -> SceneResult:
    job = job or uuid.uuid4().hex[:8]
    t0 = time.time()
    if provider == "auto" and not keys.available():
        provider = "sample"
    if provider == "sample":
        res = SceneResult(job, prompt, "sample", "built-in", "sample")
    else:
        provider, key, model = keys.pick(provider, model)
        res = SceneResult(job, prompt, provider, model, mode)
    d = out_dir(job)

    def step(name, **kw):
        kw = {k: v for k, v in kw.items() if v is not None}
        res.steps.append(dict(step=name, t=round(time.time() - t0, 2), **kw))
        bus.emit(name, src="job", kind="step", job=job, data=kw)

    bus.emit("scene job started: %s / %s / %s" % (res.provider, res.model, res.mode), src="job", kind="job", job=job,
             data={"prompt": prompt[:300], "provider": res.provider, "model": res.model, "mode": res.mode})
    try:
        if res.provider == "sample":
            xml = scenes.LANTERN
            step("sample scene used (no API key)", chars=len(xml))
            v = await client.verdict(xml, job=job)
            res.first_pass_valid = v.ok
        elif mode == "agent":
            xml, v = await _agent(client, res, key, prompt, version, rounds, job, step, max_tool_calls)
        else:
            xml, v = await _simple(client, res, key, prompt, version, rounds, job, step)
        res.xml, res.valid, res.verdict = xml, v.ok, v.to_dict()
    except Exception as exc:
        res.error = "%s: %s" % (type(exc).__name__, str(exc)[:500])
        bus.error("scene job failed: %s" % res.error, src="job", job=job)
    res.seconds = round(time.time() - t0, 2)
    if res.xml:
        (d / "scene.x3d").write_text(res.xml, encoding="utf-8")
        res.files["scene.x3d"] = "out/%s/scene.x3d" % job
    (d / "result.json").write_text(json.dumps(res.summary(), indent=1, default=str), encoding="utf-8")
    res.files["result.json"] = "out/%s/result.json" % job
    bus.emit("scene %s: %s in %.1f s" % (job, "VALID" if res.valid else ("FAILED" if res.error else "not valid"), res.seconds),
             level="INFO" if res.valid else "WARN", src="job", kind="scene", job=job,
             data={"valid": res.valid, "files": res.files, "provider": res.provider, "model": res.model, "rounds": res.rounds,
                   "first_pass_valid": res.first_pass_valid, "errors": res.verdict.get("errors"), "usage": res.usage,
                   "issues": [dict(i) for i in res.verdict.get("issues", [])][:30]})
    return res


async def _send(conv, text: str, job: str, label: str):
    with bus.span("%s %s" % (conv.provider, label), track="LLM", src="llm", job=job) as info:
        reply = await asyncio.to_thread(conv.send, text)
        info.update(model=reply.model, tokens_in=reply.usage.get("input"), tokens_out=reply.usage.get("output"),
                    tool_calls=len(reply.tool_calls))
    return reply


async def _check_and_fix(client, xml: str, job: str, step) -> tuple[str, Verdict]:
    v = await client.verdict(xml, job=job)
    step("check", ok=v.ok, errors=len(v.errors), layers=v.layers())
    if not v.ok:
        fixed, changes, _ = await client.repair(xml, job=job)
        if changes and fixed != xml:
            xml = fixed
            v = await client.verdict(xml, job=job)
            step("x3d_mcp exact fixes", changes=[c.get("kind") for c in changes][:10], ok=v.ok, errors=len(v.errors))
    return xml, v


async def _simple(client, res: SceneResult, key: str, prompt: str, version: str, rounds: int, job: str, step):
    conv = llm.conversation(res.provider, key, res.model, system=SYSTEM.format(version=version))
    reply = await _send(conv, prompt, job, "write")
    xml = llm.extract_xml(reply.text)
    step("model wrote the scene", chars=len(xml))
    res.first_pass_valid = (await client.verdict(xml, job=job)).ok
    xml, v = await _check_and_fix(client, xml, job, step)
    while not v.ok and res.rounds < rounds:
        res.rounds += 1
        hints = await client.hints(xml, job=job)
        reply = await _send(conv, REPAIR.format(report=v.report(), hints=_hints_text(hints)), job, "repair %d" % res.rounds)
        xml = llm.extract_xml(reply.text)
        step("model repair round %d" % res.rounds, chars=len(xml))
        xml, v = await _check_and_fix(client, xml, job, step)
    res.usage = dict(conv.usage)
    return xml, v


async def _agent(client, res: SceneResult, key: str, prompt: str, version: str, rounds: int, job: str, step, max_calls: int):
    tools = await client.list_tools()
    chosen = [{"name": t.name, "description": (t.description or "")[:900], "input_schema": json.loads(json.dumps(t.inputSchema))}
              for t in tools if t.name in AGENT_TOOLS]
    for t in chosen:   # agents pass documents inline; hide the file/blob options that do not apply here
        props = t["input_schema"].get("properties", {})
        for k in ("path", "blob_id", "return_mode", "encoding", "save_path"):
            props.pop(k, None)
    chosen.append(SUBMIT_TOOL)
    system = (await client.instructions()) + "\n\nYou are writing one scene for a learner. Be efficient with tool calls."
    conv = llm.conversation(res.provider, key, res.model, system=system, tools=chosen)
    xml = None
    reply = await _send(conv, AGENT_TASK.format(version=version, prompt=prompt), job, "agent start")
    async with client.session() as s:
        while True:
            if not reply.tool_calls:
                xml = xml or llm.extract_xml(reply.text)
                break
            results = []
            for call in reply.tool_calls:
                res.tool_calls += 1
                if call.name == "submit_scene":
                    xml = str(call.args.get("xml", ""))
                    results.append((call, "Received. The lab will now run the final checks."))
                    step("agent submitted the scene", chars=len(xml))
                    continue
                if call.name not in AGENT_TOOLS:
                    results.append((call, "Unknown tool %r." % call.name))
                    continue
                r = await client.call(call.name, call.args, s, job=job)
                text = r.text if len(r.text) <= 9000 else r.text[:9000] + "\n…(truncated)"
                results.append((call, text))
                step("agent called %s" % call.name, args=list(call.args)[:4], chars=len(r.text), error=r.is_error or None)
            if xml is not None or res.tool_calls >= max_calls:
                if xml is None:
                    reply = await asyncio.to_thread(conv.send_tool_results, results)
                    xml = llm.extract_xml(reply.text)
                break
            with bus.span("%s agent turn" % res.provider, track="LLM", src="llm", job=job):
                reply = await asyncio.to_thread(conv.send_tool_results, results)
    if not xml:
        raise RuntimeError("the agent finished without a scene (%d tool calls)" % res.tool_calls)
    res.first_pass_valid = (await client.verdict(xml, job=job)).ok
    xml, v = await _check_and_fix(client, xml, job, step)
    while not v.ok and res.rounds < rounds:
        res.rounds += 1
        fix = llm.conversation(res.provider, key, res.model, system=SYSTEM.format(version=version))
        reply = await _send(fix, "Here is a scene:\n\n%s\n\n%s" % (xml, REPAIR.format(report=v.report(), hints="")), job,
                            "repair %d" % res.rounds)
        xml = llm.extract_xml(reply.text)
        step("model repair round %d" % res.rounds, chars=len(xml))
        xml, v = await _check_and_fix(client, xml, job, step)
        for k in ("input", "output", "calls"):
            conv.usage[k] += fix.usage[k]
    res.usage = dict(conv.usage)
    return xml, v
