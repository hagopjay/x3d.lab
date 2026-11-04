"""OpenAI, Anthropic (Claude) and Gemini behind one small interface, over plain HTTPS.

No provider SDKs: each adapter speaks the documented REST API with httpx, so a Colab image with
different SDK versions cannot break it. Model lists come live from each provider's models endpoint,
so the dropdowns never offer a retired name.

    conv = llm.conversation("anthropic", key, model, system="...", tools=[...])
    reply = conv.send("make a red cube")          # Reply(text, tool_calls, usage)
    reply = conv.send_tool_results([(call, "result text"), ...])
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import httpx

from .events import bus

PROVIDERS = ("openai", "anthropic", "gemini")
ENV_KEYS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
SECRET_ALIASES = {"openai": ["OPENAI_API_KEY"], "anthropic": ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
                  "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"]}
LABELS = {"openai": "OpenAI", "anthropic": "Claude (Anthropic)", "gemini": "Gemini (Google)"}

OPENAI_URL = "https://api.openai.com/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"

TIMEOUT = httpx.Timeout(connect=15.0, read=240.0, write=60.0, pool=15.0)


class LLMError(RuntimeError):
    def __init__(self, provider: str, status: int | None, message: str):
        super().__init__("%s: %s%s" % (provider, ("HTTP %s: " % status) if status else "", message))
        self.provider, self.status, self.message = provider, status, message


@dataclass
class ModelInfo:
    id: str
    label: str = ""
    created: float = 0.0


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Reply:
    text: str
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""
    stop: str = ""


# ---------------------------------------------------------------------- HTTP with retries
def _client(transport=None) -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, transport=transport)


def _request(provider: str, method: str, url: str, *, headers: dict, json_body=None, params=None, transport=None,
             retries: int = 2) -> dict:
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            with _client(transport) as h:
                r = h.request(method, url, headers=headers, json=json_body, params=params)
        except httpx.HTTPError as exc:
            if attempt < retries:
                time.sleep(delay); delay *= 2
                continue
            raise LLMError(provider, None, "%s: %s" % (type(exc).__name__, exc)) from exc
        if r.status_code in (429, 500, 502, 503, 504, 529) and attempt < retries:
            wait = float(r.headers.get("retry-after", delay) or delay)
            bus.warn("%s answered %d; retrying in %.0f s" % (provider, r.status_code, min(wait, 30)), src="llm")
            time.sleep(min(wait, 30)); delay *= 2
            continue
        if r.status_code >= 400:
            raise LLMError(provider, r.status_code, _error_text(r))
        try:
            return r.json()
        except ValueError as exc:
            raise LLMError(provider, r.status_code, "response was not JSON: %s" % r.text[:200]) from exc
    raise LLMError(provider, None, "no response")  # pragma: no cover


def _error_text(r: httpx.Response) -> str:
    try:
        d = r.json()
        err = d.get("error", d)
        if isinstance(err, dict):
            return str(err.get("message") or err)[:400]
        return str(err)[:400]
    except ValueError:
        return r.text[:400]


# ---------------------------------------------------------------------- schema shaping
def gemini_schema(schema: dict) -> dict:
    """Reduce a JSON Schema to the OpenAPI subset Gemini's functionDeclarations accept."""
    if not isinstance(schema, dict):
        return {"type": "string"}
    s = dict(schema)
    if "anyOf" in s:
        types = [x for x in s["anyOf"] if isinstance(x, dict) and x.get("type") != "null"]
        nullable = len(types) != len(s["anyOf"])
        base = gemini_schema(types[0]) if types else {"type": "string"}
        if nullable:
            base["nullable"] = True
        if "description" in s:
            base["description"] = s["description"]
        return base
    out = {}
    t = s.get("type")
    if isinstance(t, list):
        nn = [x for x in t if x != "null"]
        out["type"] = (nn[0] if nn else "string").upper()
        if "null" in t:
            out["nullable"] = True
    elif t:
        out["type"] = str(t).upper()
    for k in ("description", "enum", "format"):
        if k in s:
            out[k] = s[k]
    if "properties" in s:
        out["type"] = "OBJECT"
        out["properties"] = {k: gemini_schema(v) for k, v in s["properties"].items()}
        req = [r for r in s.get("required", []) if r in s["properties"]]
        if req:
            out["required"] = req
    if "items" in s:
        out["items"] = gemini_schema(s["items"])
    if out.get("type") == "OBJECT" and "properties" not in out:
        out["properties"] = {}
    return out or {"type": "STRING"}


def plain_schema(schema: dict) -> dict:
    """JSON Schema without titles/defaults (OpenAI and Anthropic accept the rest as is)."""
    if isinstance(schema, dict):
        return {k: plain_schema(v) for k, v in schema.items() if k not in ("title", "default", "$schema")}
    if isinstance(schema, list):
        return [plain_schema(v) for v in schema]
    return schema


# ---------------------------------------------------------------------- model lists
_SKIP_OPENAI = ("embedding", "whisper", "tts", "dall-e", "image", "audio", "realtime", "transcribe", "moderation",
                "search", "codex", "computer-use", "instruct", "deep-research")
_SKIP_GEMINI = ("image", "tts", "live", "audio", "embedding", "aqa", "computer-use", "robotics", "imagen", "veo", "learnlm")


def list_models(provider: str, key: str, transport=None) -> list[ModelInfo]:
    if provider == "openai":
        d = _request(provider, "GET", OPENAI_URL + "/models", headers={"Authorization": "Bearer " + key}, transport=transport)
        out = [ModelInfo(m["id"], m["id"], float(m.get("created") or 0)) for m in d.get("data", [])
               if re.match(r"^(gpt-|o\d|chatgpt)", m["id"]) and not any(s in m["id"] for s in _SKIP_OPENAI)]
    elif provider == "anthropic":
        d = _request(provider, "GET", ANTHROPIC_URL + "/models", params={"limit": 100},
                     headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}, transport=transport)
        out = []
        for m in d.get("data", []):
            created = 0.0
            try:
                from datetime import datetime
                created = datetime.fromisoformat(str(m.get("created_at", "")).replace("Z", "+00:00")).timestamp()
            except Exception:
                pass
            out.append(ModelInfo(m["id"], m.get("display_name") or m["id"], created))
    elif provider == "gemini":
        d = _request(provider, "GET", GEMINI_URL + "/models", params={"pageSize": 1000},
                     headers={"x-goog-api-key": key}, transport=transport)
        out = []
        for m in d.get("models", []):
            mid = m.get("name", "").replace("models/", "")
            if "generateContent" not in (m.get("supportedGenerationMethods") or []) or not mid.startswith("gemini"):
                continue
            if any(s in mid for s in _SKIP_GEMINI):
                continue
            out.append(ModelInfo(mid, m.get("displayName") or mid, 0.0))
    else:
        raise ValueError("unknown provider %r" % provider)
    return sorted(out, key=lambda m: (m.created, m.id), reverse=True)


def _gemini_rank(mid: str) -> tuple:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", mid)
    v = float(m.group(1)) if m else 0.0
    stable = not any(w in mid for w in ("preview", "exp"))
    return (v, stable, -len(mid))


def default_model(provider: str, models: list[ModelInfo]) -> str | None:
    """A sensible everyday model: OpenAI's newest mini, Claude's newest Sonnet, Gemini's newest Flash."""
    ids = [m.id for m in models]
    if not ids:
        return None
    if provider == "openai":
        minis = [m for m in models if m.id.startswith("gpt-") and "mini" in m.id and "nano" not in m.id]
        gpts = [m for m in models if m.id.startswith("gpt-")]
        pool = minis or gpts or models
        return max(pool, key=lambda m: (m.created, m.id)).id
    if provider == "anthropic":
        sonnets = [m for m in models if "sonnet" in m.id]
        pool = sonnets or models
        return max(pool, key=lambda m: (m.created, m.id)).id
    if provider == "gemini":
        flash = [i for i in ids if "flash" in i and "lite" not in i and "thinking" not in i]
        return max(flash or ids, key=_gemini_rank)
    return ids[0]


# ---------------------------------------------------------------------- conversations
class Conversation:
    provider = ""

    def __init__(self, key: str, model: str, system: str = "", tools: list | None = None, max_tokens: int = 16000,
                 transport=None):
        self.key, self.model, self.system = key, model, system
        self.tools = tools or []      # [{"name", "description", "input_schema"}]
        self.max_tokens = max_tokens
        self.transport = transport
        self.messages: list = []
        self.usage = {"input": 0, "output": 0, "calls": 0}

    def _count(self, inp: int, out: int) -> dict:
        self.usage["input"] += int(inp or 0)
        self.usage["output"] += int(out or 0)
        self.usage["calls"] += 1
        return {"input": int(inp or 0), "output": int(out or 0)}

    def send(self, text: str) -> Reply:
        raise NotImplementedError

    def send_tool_results(self, results: list) -> Reply:
        raise NotImplementedError


class OpenAIConversation(Conversation):
    provider = "openai"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.system:
            self.messages.append({"role": "system", "content": self.system})

    def _call(self) -> Reply:
        body = {"model": self.model, "messages": self.messages, "max_completion_tokens": self.max_tokens}
        if self.tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", "")[:1000],
                                                               "parameters": plain_schema(t["input_schema"])}} for t in self.tools]
        d = _request("openai", "POST", OPENAI_URL + "/chat/completions", headers={"Authorization": "Bearer " + self.key},
                     json_body=body, transport=self.transport)
        ch = (d.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        keep = {"role": "assistant", "content": msg.get("content")}
        if msg.get("tool_calls"):
            keep["tool_calls"] = msg["tool_calls"]
        self.messages.append(keep)
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {"_unparsed": fn.get("arguments")}
            calls.append(ToolCall(tc.get("id", ""), fn.get("name", ""), args))
        u = d.get("usage") or {}
        return Reply(msg.get("content") or "", calls, self._count(u.get("prompt_tokens"), u.get("completion_tokens")),
                     d.get("model", self.model), ch.get("finish_reason", ""))

    def send(self, text: str) -> Reply:
        self.messages.append({"role": "user", "content": text})
        return self._call()

    def send_tool_results(self, results: list) -> Reply:
        for call, out in results:
            self.messages.append({"role": "tool", "tool_call_id": call.id, "content": out})
        return self._call()


class AnthropicConversation(Conversation):
    provider = "anthropic"

    def _call(self) -> Reply:
        body = {"model": self.model, "max_tokens": self.max_tokens, "messages": self.messages}
        if self.system:
            body["system"] = self.system
        if self.tools:
            body["tools"] = [{"name": t["name"], "description": t.get("description", "")[:1000],
                              "input_schema": plain_schema(t["input_schema"])} for t in self.tools]
        d = _request("anthropic", "POST", ANTHROPIC_URL + "/messages",
                     headers={"x-api-key": self.key, "anthropic-version": ANTHROPIC_VERSION}, json_body=body,
                     transport=self.transport)
        content = d.get("content") or []
        self.messages.append({"role": "assistant", "content": content})
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        calls = [ToolCall(b.get("id", ""), b.get("name", ""), b.get("input") or {}) for b in content if b.get("type") == "tool_use"]
        u = d.get("usage") or {}
        return Reply(text, calls, self._count(u.get("input_tokens"), u.get("output_tokens")), d.get("model", self.model),
                     d.get("stop_reason", ""))

    def send(self, text: str) -> Reply:
        self.messages.append({"role": "user", "content": text})
        return self._call()

    def send_tool_results(self, results: list) -> Reply:
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call.id, "content": out} for call, out in results]})
        return self._call()


class GeminiConversation(Conversation):
    provider = "gemini"

    def _call(self) -> Reply:
        body = {"contents": self.messages, "generationConfig": {"maxOutputTokens": self.max_tokens}}
        if self.system:
            body["systemInstruction"] = {"parts": [{"text": self.system}]}
        if self.tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t.get("description", "")[:1000], "parameters": gemini_schema(t["input_schema"])}
                for t in self.tools]}]
        d = _request("gemini", "POST", "%s/models/%s:generateContent" % (GEMINI_URL, self.model),
                     headers={"x-goog-api-key": self.key}, json_body=body, transport=self.transport)
        cands = d.get("candidates") or []
        if not cands:
            fb = d.get("promptFeedback") or {}
            raise LLMError("gemini", None, "no candidates (blockReason=%s)" % fb.get("blockReason"))
        content = cands[0].get("content") or {"role": "model", "parts": []}
        content.setdefault("role", "model")
        # Echo the model turn back verbatim: Gemini needs its thought signatures on the next call.
        self.messages.append(content)
        parts = content.get("parts") or []
        text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
        calls = []
        for i, p in enumerate(parts):
            fc = p.get("functionCall")
            if fc:
                calls.append(ToolCall(fc.get("id") or "call_%d" % i, fc.get("name", ""), fc.get("args") or {}))
        u = d.get("usageMetadata") or {}
        return Reply(text, calls, self._count(u.get("promptTokenCount"), u.get("candidatesTokenCount")),
                     d.get("modelVersion", self.model), cands[0].get("finishReason", ""))

    def send(self, text: str) -> Reply:
        self.messages.append({"role": "user", "parts": [{"text": text}]})
        return self._call()

    def send_tool_results(self, results: list) -> Reply:
        parts = []
        for call, out in results:
            fr = {"name": call.name, "response": {"result": out}}
            if call.id and not call.id.startswith("call_"):
                fr["id"] = call.id
            parts.append({"functionResponse": fr})
        self.messages.append({"role": "user", "parts": parts})
        return self._call()


_CLASSES = {"openai": OpenAIConversation, "anthropic": AnthropicConversation, "gemini": GeminiConversation}


_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,120}$")


def conversation(provider: str, key: str, model: str, **kw) -> Conversation:
    if provider not in _CLASSES:
        raise ValueError("provider must be one of %s" % (PROVIDERS,))
    if not _MODEL_ID.match(model or ""):        # the id goes into a URL path (Gemini): no slashes, no '..'
        raise ValueError("not a model id: %r" % (model,))
    return _CLASSES[provider](key, model, **kw)


def ask(provider: str, key: str, model: str, prompt: str, system: str = "", **kw) -> Reply:
    """One-shot question."""
    return conversation(provider, key, model, system=system, **kw).send(prompt)


def extract_xml(text: str) -> str:
    """Pull an X3D document out of a model reply (fences, prose around it, missing declaration)."""
    m = re.search(r"```(?:xml|x3d)?\s*(.*?)```", text or "", re.S)
    if m:
        text = m.group(1)
    text = text or ""
    i = text.find("<?xml")
    if i < 0:
        i = text.find("<X3D")
    j = text.rfind("</X3D>")
    return text[i:j + 6].strip() if i >= 0 and j > i else text.strip()
