"""The prompt -> validate -> repair loop and the agent loop, with a scripted model and the real x3d_mcp server."""

import pytest

from x3dlab import keys, llm, quickstart
from x3dlab.scenes import LANTERN

BROKEN = LANTERN.replace('<Box size="1 1.4 1"/>', '<Box size="1 1.4 1" solid="True"/>')


class FakeConversation(llm.Conversation):
    """Replies from a script. Each item is text, or a list of (tool name, args) calls."""
    provider = "fake"

    def __init__(self, script, *a, **kw):
        super().__init__("k", "fake-1", *a, **kw)
        self.script = list(script)
        self.seen = []

    def _next(self):
        item = self.script.pop(0)
        self._count(100, 50)
        if isinstance(item, list):
            calls = [llm.ToolCall("c%d" % i, name, args) for i, (name, args) in enumerate(item)]
            return llm.Reply("", calls, {"input": 100, "output": 50}, "fake-1")
        return llm.Reply(item, [], {"input": 100, "output": 50}, "fake-1")

    def send(self, text):
        self.seen.append(text)
        return self._next()

    def send_tool_results(self, results):
        self.seen.append(results)
        return self._next()


@pytest.fixture()
def fake(monkeypatch):
    holder = {}

    def make(script):
        holder["script"] = script

        def conversation(provider, key, model, **kw):
            c = FakeConversation(holder["script"], **{k: v for k, v in kw.items() if k in ("system", "tools")})
            holder.setdefault("convs", []).append(c)
            return c
        monkeypatch.setattr(llm, "conversation", conversation)
        monkeypatch.setattr(keys, "pick", lambda provider="auto", model_id=None: ("anthropic", "k", "fake-1"))
        return holder
    return make


def test_simple_mode_repairs_from_the_report(lab, fake):
    h = fake(["Here you go:\n```xml\n" + BROKEN + "\n```", LANTERN])
    res = lab.client.run(quickstart.make_scene(lab.client, "a lantern", provider="anthropic", rounds=2))
    assert res.valid and res.first_pass_valid is False and res.rounds == 1
    repair_prompt = h["convs"][0].seen[1]
    assert "SFBool" in repair_prompt and "Validator report" in repair_prompt
    assert res.usage["calls"] == 2 and res.files["scene.x3d"].endswith("scene.x3d")


def test_simple_mode_gives_up_after_rounds(lab, fake):
    fake([BROKEN, BROKEN, BROKEN])
    res = lab.client.run(quickstart.make_scene(lab.client, "a lantern", provider="anthropic", rounds=2))
    assert not res.valid and res.rounds == 2 and res.error is None
    assert res.verdict["layers"] == ["xsd"]


def test_agent_mode_uses_mcp_tools_then_submits(lab, fake):
    h = fake([
        [("describe_node", {"node_type": "OrientationInterpolator"})],
        [("validate_x3d", {"content": BROKEN}), ("validate_semantic", {"content": BROKEN})],
        [("submit_scene", {"xml": LANTERN})],
    ])
    res = lab.client.run(quickstart.make_scene(lab.client, "a lantern", provider="anthropic", mode="agent"))
    assert res.valid and res.tool_calls == 4, res
    conv = h["convs"][0]
    names = [t["name"] for t in conv.tools]
    assert "submit_scene" in names and "describe_node" in names and "render_image" not in names
    assert all("path" not in t["input_schema"].get("properties", {}) for t in conv.tools)
    first_results = conv.seen[1]
    assert '"name": "OrientationInterpolator"' in first_results[0][1]
    assert "SFBool" in conv.seen[2][0][1]          # the model saw the real validator's complaint
    assert "describe_node" in conv.system           # the server's own instructions are the system prompt


def test_sample_mode_without_keys(lab, monkeypatch):
    monkeypatch.setattr(keys, "available", lambda: [])
    res = lab.client.run(quickstart.make_scene(lab.client, "anything"))
    assert res.provider == "sample" and res.valid
