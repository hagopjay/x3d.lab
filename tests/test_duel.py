"""The model duel: error kinds, budget, contenders, and full runs against the real x3d_mcp server
(the `sample` contender and a scripted stand-in model, so no key and no network are needed)."""

import json

import pytest

from x3dlab import duel, keys, llm
from x3dlab.mcpclient import Issue


@pytest.mark.parametrize("layer,rule,message,kind", [
    ("xsd", "schema", "Element 'Box', attribute 'solid': 'True' is not a valid value of the atomic type 'SFBool'.", "boolean-case"),
    ("xsd", "schema", "Element 'Material', attribute 'difuseColor': The attribute 'difuseColor' is not allowed.", "unknown-field"),
    ("xsd", "schema", "Element 'Banana': This element is not expected.", "node-not-allowed"),
    ("xsd", "schema", "Element 'Material', attribute 'diffuseColor': [facet 'pattern'] The value '0.95 0.55' is not accepted", "value-format"),
    ("xsd", "schema", "Element 'Material', attribute 'DEF': 'Glow' is not a valid value of the atomic type 'xs:ID'.", "duplicate-def"),
    ("xsd", "schema", "Element 'X3D': The attribute 'profile' is required but missing.", "missing-attribute"),
    ("xsd", "schema", "Element 'NavigationInfo', attribute 'transitionType': [facet 'enumeration'] The value 'FAST' is not an element of the set", "bad-enum"),
    ("xsd", "schema", "Element 'Shape', attribute 'bboxSize': 'a b c' is not a valid value of the list type 'SFVec3f'.", "wrong-type"),
    ("xsd", "schema", "something new", "schema-other"),
    ("xml", "parse", "XML parse error: Opening and ending tag mismatch", "not-xml"),
    ("input", "empty", "Provide either `content` ...", "no-scene"),
    ("semantic", "use-undefined-def", "USE='Bodyy' has no DEF", "use-undefined-def"),
])
def test_classify(layer, rule, message, kind):
    assert duel.classify(Issue(layer, "error", rule, message)) == kind


def test_prompts_and_estimate():
    cfg = duel.DuelConfig(contenders=["sample", "nvidia", "anthropic:claude-x"], prompts=5, rounds=2)
    assert [p["id"] for p in cfg.prompt_list()] == [p["id"] for p in duel.PROMPTS[:5]]
    e = duel.estimate(cfg)
    assert (e["scenes"], e["requests_best"], e["requests_worst"], e["nvidia_requests_worst"]) == (15, 10, 30, 15)
    assert e["within_budget"]
    assert not duel.estimate(duel.DuelConfig(contenders=["nvidia"] * 3, prompts=12, rounds=8, max_requests=100))["within_budget"]
    mixed = duel.DuelConfig(prompts=["orrery", "a blue torus", {"id": "mine", "text": "two cubes"}]).prompt_list()
    assert [p["id"] for p in mixed] == ["orrery", "p2", "mine"] and mixed[1]["text"] == "a blue torus"


def test_contenders_without_keys_are_skipped(monkeypatch):
    for p in llm.PROVIDERS:
        for name in llm.SECRET_ALIASES[p]:
            monkeypatch.delenv(name, raising=False)
        keys.set_key(p, None)
    cs, skipped = duel.resolve(duel.DuelConfig(contenders=["sample", "nvidia:meta/llama-3.3-70b-instruct", "sample"]))
    assert [c.label for c in cs] == ["sample"]
    assert "No NVIDIA" in skipped["nvidia:meta/llama-3.3-70b-instruct"] and "duplicate" in skipped["sample"]
    assert duel.parse_contender("nvidia:meta/llama-3.3-70b-instruct") == ("nvidia", "meta/llama-3.3-70b-instruct")
    assert duel.parse_contender("anthropic") == ("anthropic", None)


def test_budget_is_enforced_before_anything_runs(lab, monkeypatch):
    monkeypatch.setattr(duel.keys, "pick", lambda p, m=None: ("nvidia", "nvapi-test", m))
    monkeypatch.setattr(duel.llm, "conversation", lambda *a, **k: pytest.fail("a model was called"))
    with pytest.raises(RuntimeError, match="max_requests"):
        lab.duel(duel.DuelConfig(contenders=["nvidia:x/y"], prompts=12, rounds=5, max_requests=10))


def test_sample_duel_end_to_end(lab):
    run = lab.duel(duel.DuelConfig(contenders=["sample"], prompts=12, rounds=1, name="t-sample"))
    by = {a.prompt_id: a for a in run.attempts}
    assert len(run.attempts) == 12 and not [a for a in run.attempts if a.error]
    assert by["lantern"].stage == "draft" and by["lantern"].draft_kinds == []                     # control
    assert "boolean-case" in by["orrery"].draft_kinds                                              # python_bool
    assert "unknown-field" in by["pbr"].draft_kinds                                                # field_typo
    assert "use-undefined-def" in by["lights"].draft_kinds                                         # missing_def
    assert by["traffic-light"].draft_kinds == ["not-xml"]                                          # unclosed_tag
    assert "duplicate-def" in by["terrain"].draft_kinds                                            # duplicate_def
    assert all(a.final_ok for a in run.attempts)       # the sample 'model' always repairs to the clean lantern
    s = run.summary()[0]
    assert s["scenes"] == 12 and s["draft_valid"] == 1 and s["final_valid"] == 12 and s["requests"] >= 12
    assert s["server_rescues"] + s["model_rescues"] == 11
    card = json.loads(run.card_path.read_text())
    assert card["kind"] == "x3dlab.duel" and len(card["attempts"]) == 12 and card["summary"][0]["kinds"]
    assert (run.dir / "report.html").read_text().count("<tr>") > 12
    assert (run.dir / by["orrery"].files["draft"]).read_text().count('solid="True"') == 1


class _Scripted:
    """A stand-in model: first reply has no X3D, the repair turn answers with a valid scene."""
    provider = "nvidia"

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.usage = {"input": 0, "output": 0, "calls": 0}

    def send(self, text):
        self.sent.append(text)
        return llm.Reply(self.replies.pop(0), [], {"input": 100, "output": 50}, "stub/model", "stop")


def test_model_contender_repairs_after_no_scene(lab, monkeypatch):
    from x3dlab.scenes import LANTERN
    made = []

    def fake_conversation(provider, key, model, **kw):
        c = _Scripted(["I'm sorry, I can only describe the scene.", "```xml\n%s\n```" % LANTERN])
        made.append((provider, model, kw.get("system", ""), c))
        return c

    monkeypatch.setattr(duel.keys, "pick", lambda p, m=None: ("nvidia", "nvapi-test", m or "stub/model"))
    monkeypatch.setattr(duel.llm, "conversation", fake_conversation)
    run = lab.duel(duel.DuelConfig(contenders=["nvidia:stub/model"], prompts=["lantern"], rounds=1, system="bare"))
    a = run.attempts[0]
    assert (a.draft_kinds, a.stage, a.requests, a.tokens_in, a.tokens_out) == (["no-scene"], "model", 2, 200, 100)
    provider, model, system, conv = made[0]
    assert (provider, model) == ("nvidia", "stub/model") and system.startswith("You write X3D 4.0") and "Rules" not in system
    assert "no X3D document" in conv.sent[1]
    assert "nvapi-test" not in run.card_path.read_text()          # keys never reach the card
