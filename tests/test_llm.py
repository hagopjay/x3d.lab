"""Provider adapters against scripted HTTP (no network, no keys). Request bodies are checked against each
provider's documented shapes; responses are minimal real-world shapes."""

import json

import httpx
import pytest

from x3dlab import llm

TOOLS = [{"name": "describe_node", "description": "Describe a node",
          "input_schema": {"type": "object", "title": "x", "properties": {
              "node_type": {"type": "string", "title": "Node Type"},
              "component": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}},
              "required": ["node_type"]}}]


class Script:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        status, body = self.responses.pop(0)
        return httpx.Response(status, json=body)

    def body(self, i):
        return json.loads(self.requests[i].content)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def test_openai_tool_round_trip():
    s = Script(
        (200, {"model": "gpt-x-mini", "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
               "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "describe_node", "arguments": "{\"node_type\": \"Box\"}"}}]}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 5}}),
        (200, {"model": "gpt-x-mini", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "<X3D/>"}}],
               "usage": {"prompt_tokens": 20, "completion_tokens": 7}}))
    c = llm.conversation("openai", "sk-test", "gpt-x-mini", system="sys", tools=TOOLS, transport=httpx.MockTransport(s))
    r = c.send("hi")
    assert r.tool_calls[0].name == "describe_node" and r.tool_calls[0].args == {"node_type": "Box"}
    b0 = s.body(0)
    assert s.requests[0].headers["authorization"] == "Bearer sk-test"
    assert b0["messages"][0] == {"role": "system", "content": "sys"} and "max_completion_tokens" in b0
    assert b0["tools"][0]["type"] == "function" and "title" not in json.dumps(b0["tools"])
    r2 = c.send_tool_results([(r.tool_calls[0], "Box has size")])
    b1 = s.body(1)
    assert b1["messages"][-1] == {"role": "tool", "tool_call_id": "call_1", "content": "Box has size"}
    assert b1["messages"][-2]["tool_calls"][0]["id"] == "call_1"
    assert r2.text == "<X3D/>" and c.usage == {"input": 30, "output": 12, "calls": 2}


def test_anthropic_tool_round_trip():
    s = Script(
        (200, {"model": "claude-x", "stop_reason": "tool_use", "usage": {"input_tokens": 11, "output_tokens": 3},
               "content": [{"type": "text", "text": "Let me look."},
                           {"type": "tool_use", "id": "toolu_1", "name": "describe_node", "input": {"node_type": "Box"}}]}),
        (200, {"model": "claude-x", "stop_reason": "end_turn", "usage": {"input_tokens": 30, "output_tokens": 9},
               "content": [{"type": "text", "text": "<X3D/>"}]}))
    c = llm.conversation("anthropic", "sk-ant", "claude-x", system="sys", tools=TOOLS, transport=httpx.MockTransport(s))
    r = c.send("hi")
    h = s.requests[0].headers
    assert h["x-api-key"] == "sk-ant" and h["anthropic-version"] == llm.ANTHROPIC_VERSION
    b0 = s.body(0)
    assert b0["system"] == "sys" and b0["max_tokens"] > 0 and b0["tools"][0]["input_schema"]["required"] == ["node_type"]
    assert r.text == "Let me look." and r.tool_calls[0].id == "toolu_1"
    r2 = c.send_tool_results([(r.tool_calls[0], "fields...")])
    b1 = s.body(1)
    assert b1["messages"][-2]["role"] == "assistant" and b1["messages"][-2]["content"][1]["type"] == "tool_use"
    assert b1["messages"][-1] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "fields..."}]}
    assert r2.text == "<X3D/>"


def test_gemini_tool_round_trip_echoes_signature():
    part = {"functionCall": {"name": "describe_node", "args": {"node_type": "Box"}}, "thoughtSignature": "SIG=="}
    s = Script(
        (200, {"modelVersion": "gemini-x-flash", "candidates": [{"finishReason": "STOP", "content": {"role": "model", "parts": [part]}}],
               "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 2}}),
        (200, {"candidates": [{"content": {"role": "model", "parts": [{"text": "<X3D/>"}]}}]}))
    c = llm.conversation("gemini", "AIza", "gemini-x-flash", system="sys", tools=TOOLS, transport=httpx.MockTransport(s))
    r = c.send("hi")
    req = s.requests[0]
    assert req.headers["x-goog-api-key"] == "AIza" and req.url.path.endswith("/models/gemini-x-flash:generateContent")
    decl = s.body(0)["tools"][0]["functionDeclarations"][0]
    assert decl["parameters"]["type"] == "OBJECT" and decl["parameters"]["properties"]["component"]["nullable"] is True
    assert "title" not in json.dumps(decl) and "anyOf" not in json.dumps(decl)
    r2 = c.send_tool_results([(r.tool_calls[0], "fields...")])
    b1 = s.body(1)
    assert b1["contents"][1]["parts"][0]["thoughtSignature"] == "SIG=="       # model turn echoed verbatim
    assert b1["contents"][2]["parts"][0]["functionResponse"] == {"name": "describe_node", "response": {"result": "fields..."}}
    assert r2.text == "<X3D/>"


def test_errors_and_retries():
    s = Script((429, {"error": {"message": "slow down"}}), (401, {"error": {"message": "invalid x-api-key"}}))
    with pytest.raises(llm.LLMError) as e:
        llm.list_models("anthropic", "bad", transport=httpx.MockTransport(s))
    assert e.value.status == 401 and "invalid x-api-key" in str(e.value) and len(s.requests) == 2


def test_model_lists_and_defaults():
    oa = Script((200, {"data": [{"id": "gpt-9", "created": 3}, {"id": "gpt-9-mini", "created": 5}, {"id": "gpt-8-mini", "created": 1},
                                {"id": "text-embedding-3", "created": 9}, {"id": "gpt-9-realtime", "created": 8}, {"id": "dall-e-3", "created": 2}]}))
    ms = llm.list_models("openai", "k", transport=httpx.MockTransport(oa))
    assert [m.id for m in ms] == ["gpt-9-mini", "gpt-9", "gpt-8-mini"]
    assert llm.default_model("openai", ms) == "gpt-9-mini"
    an = Script((200, {"data": [{"id": "claude-haiku-9", "created_at": "2026-05-01T00:00:00Z"},
                                {"id": "claude-sonnet-9", "created_at": "2026-04-01T00:00:00Z"},
                                {"id": "claude-sonnet-8", "created_at": "2025-01-01T00:00:00Z"}]}))
    assert llm.default_model("anthropic", llm.list_models("anthropic", "k", transport=httpx.MockTransport(an))) == "claude-sonnet-9"
    ge = Script((200, {"models": [{"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
                                  {"name": "models/gemini-3.8-flash-lite", "supportedGenerationMethods": ["generateContent"]},
                                  {"name": "models/gemini-3.9-flash-preview", "supportedGenerationMethods": ["generateContent"]},
                                  {"name": "models/gemini-3.9-flash-image", "supportedGenerationMethods": ["generateContent"]},
                                  {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]}]}))
    gm = llm.list_models("gemini", "k", transport=httpx.MockTransport(ge))
    assert "gemini-3.9-flash-image" not in [m.id for m in gm]
    assert llm.default_model("gemini", gm) == "gemini-3.9-flash-preview"


@pytest.mark.parametrize("text,start", [
    ("<?xml version='1.0'?><X3D/>", "<?xml"),
    ("Sure!\n```xml\n<?xml version='1.0'?><X3D></X3D>\n```\nEnjoy", "<?xml"),
    ("```\n<X3D></X3D>\n```", "<X3D"),
    ("Here: <X3D profile='Core'></X3D> thanks", "<X3D"),
    ("I cannot do that.", "I cannot"),
    ("", ""),
])
def test_extract_xml(text, start):
    out = llm.extract_xml(text)
    assert out.startswith(start)
    if "</X3D>" in text:
        assert out.endswith("</X3D>")


def test_model_id_cannot_reach_other_paths():
    for bad in ("../../v1/files", "gemini/../x", "", "a b"):
        with pytest.raises(ValueError):
            llm.conversation("gemini", "k", bad)
    assert llm.conversation("gemini", "k", "gemini-3.8-flash").model == "gemini-3.8-flash"


# ---------------------------------------------------------------------- NVIDIA (build.nvidia.com)
NV_MODELS = {"object": "list", "data": [
    {"id": "meta/llama-3.3-70b-instruct", "object": "model", "owned_by": "meta"},
    {"id": "nvidia/nemotron-3-super-120b-a12b", "object": "model", "owned_by": "nvidia"},
    {"id": "nvidia/nv-embedqa-e5-v5", "object": "model"},
    {"id": "nvidia/llama-3.1-nemoguard-8b-content-safety", "object": "model"},
    {"id": "nvidia/llama-3.1-nemotron-70b-reward", "object": "model"},
    {"id": "baai/bge-m3", "object": "model"},
    {"id": "../../evil", "object": "model"},
    {"id": "mistralai/mistral-small-24b-instruct", "object": "model"}]}


@pytest.fixture()
def nv_unpaced(monkeypatch):
    monkeypatch.setenv("X3DLAB_NVIDIA_RPM", "0")


def test_nvidia_tool_round_trip(nv_unpaced):
    s = Script(
        (200, {"model": "meta/llama-3.3-70b-instruct", "choices": [{"finish_reason": "tool_calls", "message": {
               "role": "assistant", "content": None, "tool_calls": [{"id": "chatcmpl-tool-1", "type": "function",
               "function": {"name": "describe_node", "arguments": "{\"node_type\": \"Box\"}"}}]}}],
               "usage": {"prompt_tokens": 12, "completion_tokens": 4}}),
        (200, {"model": "meta/llama-3.3-70b-instruct", "choices": [{"finish_reason": "stop", "message": {
               "role": "assistant", "content": "<think>The user wants a box.</think>\n<X3D/>"}}],
               "usage": {"prompt_tokens": 30, "completion_tokens": 9}}))
    c = llm.conversation("nvidia", "nvapi-test", "meta/llama-3.3-70b-instruct", system="sys", tools=TOOLS,
                         transport=httpx.MockTransport(s))
    r = c.send("hi")
    assert str(s.requests[0].url) == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert s.requests[0].headers["authorization"] == "Bearer nvapi-test"
    b0 = s.body(0)
    assert b0["model"] == "meta/llama-3.3-70b-instruct" and b0["max_tokens"] == 8192 and "max_completion_tokens" not in b0
    assert b0["tools"][0]["function"]["name"] == "describe_node" and "title" not in json.dumps(b0["tools"])
    assert r.tool_calls[0].args == {"node_type": "Box"}
    r2 = c.send_tool_results([(r.tool_calls[0], "Box has size")])
    assert s.body(1)["messages"][-1] == {"role": "tool", "tool_call_id": "chatcmpl-tool-1", "content": "Box has size"}
    assert r2.text == "<X3D/>"                       # the <think> block never reaches the scene text
    assert c.usage == {"input": 42, "output": 13, "calls": 2}


def test_nvidia_halves_max_tokens_once_when_refused(nv_unpaced):
    s = Script((400, {"detail": "max_tokens must be less than or equal to 4096"}),
               (200, {"choices": [{"message": {"role": "assistant", "content": "<X3D/>"}, "finish_reason": "stop"}]}))
    c = llm.conversation("nvidia", "k", "meta/llama-3.3-70b-instruct", transport=httpx.MockTransport(s))
    assert c.send("hi").text == "<X3D/>"
    assert s.body(0)["max_tokens"] == 8192 and s.body(1)["max_tokens"] == 4096


def test_nvidia_retries_429_more_patiently(nv_unpaced):
    s = Script(*[(429, {"detail": "Too Many Requests"})] * 5, (200, {"data": NV_MODELS["data"][:1]}))
    ms = llm.list_models("nvidia", "k", transport=httpx.MockTransport(s))
    assert [m.id for m in ms] == ["meta/llama-3.3-70b-instruct"] and len(s.requests) == 6


def test_nvidia_model_list_filters_and_default(nv_unpaced):
    ms = llm.list_models("nvidia", "k", transport=httpx.MockTransport(Script((200, NV_MODELS))))
    ids = [m.id for m in ms]
    assert ids == ["meta/llama-3.3-70b-instruct", "mistralai/mistral-small-24b-instruct", "nvidia/nemotron-3-super-120b-a12b"]
    assert llm.default_model("nvidia", ms) == "nvidia/nemotron-3-super-120b-a12b"
    assert llm.nvidia_default(["meta/llama-3.3-70b-instruct", "x/y-chat"]) == "meta/llama-3.3-70b-instruct"
    assert llm.nvidia_default(["x/only-model"]) == "x/only-model" and llm.nvidia_default([]) is None


def test_nvidia_model_ids():
    for ok in ("meta/llama-3.3-70b-instruct", "nvidia/nemotron-3-super-120b-a12b", "qwen/qwen3-235b-a22b:fp8"):
        assert llm.conversation("nvidia", "k", ok).model == ok
    for bad in ("../x", "a/../b", "a/b/c", "/abs", "a b/c", ""):
        with pytest.raises(ValueError):
            llm.conversation("nvidia", "k", bad)
    with pytest.raises(ValueError):                   # slashes stay forbidden where the id goes into a URL path
        llm.conversation("gemini", "k", "meta/llama-3.3-70b-instruct")


def test_pacing_spaces_requests(monkeypatch):
    slept, clock = [], [100.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(round(s, 2)))
    monkeypatch.setenv("X3DLAB_NVIDIA_RPM", "60")
    llm._next_slot.pop("nvidia", None)
    for _ in range(3):
        llm._pace("nvidia")
    assert slept == [1.0, 2.0]                         # one per second at 60 rpm, no wait for the first
    monkeypatch.setenv("X3DLAB_NVIDIA_RPM", "0")
    llm._pace("nvidia")
    assert slept == [1.0, 2.0]
    assert llm._rpm("openai") is None


def test_keys_know_nvidia(monkeypatch):
    from x3dlab import keys
    monkeypatch.setenv("NGC_API_KEY", "nvapi-from-env")
    try:
        assert keys.get_key("nvidia") == "nvapi-from-env" and "nvidia" in keys.available()
        assert keys.HINTS["nvidia"] == "nvapi-..." and keys.public_status()["nvidia"]["has_key"]
        assert "nvapi-from-env" not in json.dumps(keys.public_status())
    finally:
        keys.set_key("nvidia", None)
