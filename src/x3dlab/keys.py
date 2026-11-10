"""API keys for OpenAI, Claude, Gemini and NVIDIA: entered in a password box or read from Colab Secrets.

Keys live only in this kernel (a dict and os.environ). They are registered with the event bus so they
are redacted from every log line, file and dashboard event, and the gateway never sends them to a browser.
"""

from __future__ import annotations

import os
import threading

from . import llm
from .events import bus

_keys: dict[str, str] = {}
_models: dict[str, list] = {}
_chosen: dict[str, str] = {}
_errors: dict[str, str] = {}
_lock = threading.RLock()

ORDER = ("anthropic", "openai", "gemini", "nvidia")   # preference for provider="auto"
HINTS = {"openai": "sk-...", "anthropic": "sk-ant-...", "gemini": "AIza...", "nvidia": "nvapi-..."}


def set_key(provider: str, key: str | None) -> None:
    if provider not in llm.PROVIDERS:
        raise ValueError("provider must be one of %s" % (llm.PROVIDERS,))
    key = (key or "").strip()
    with _lock:
        if not key:
            _keys.pop(provider, None)
            os.environ.pop(llm.ENV_KEYS[provider], None)
            _models.pop(provider, None)
            _chosen.pop(provider, None)
            return
        bus.add_secret(key)
        _keys[provider] = key
        os.environ[llm.ENV_KEYS[provider]] = key
        _models.pop(provider, None)
        _errors.pop(provider, None)
    bus.info("%s key set (ends ...%s)" % (llm.LABELS[provider], key[-4:]), src="keys")


def get_key(provider: str) -> str | None:
    with _lock:
        if provider in _keys:
            return _keys[provider]
    for name in llm.SECRET_ALIASES[provider]:
        v = os.environ.get(name)
        if v:
            bus.add_secret(v)
            return v
    return None


def available() -> list[str]:
    return [p for p in ORDER if get_key(p)]


def from_secrets() -> list[str]:
    """Copy keys from Colab's Secrets panel (key icon) when this notebook has access to them."""
    found = []
    try:
        from google.colab import userdata
    except Exception:
        return found
    for p in llm.PROVIDERS:
        for name in llm.SECRET_ALIASES[p]:
            try:
                v = userdata.get(name)
            except Exception:
                v = None
            if v:
                set_key(p, v)
                found.append(p)
                break
    return found


def forget() -> None:
    for p in llm.PROVIDERS:
        set_key(p, None)
    bus.info("all API keys forgotten in this kernel", src="keys")


def test(provider: str) -> dict:
    """Check a key by listing the provider's models. Fills the model choice."""
    key = get_key(provider)
    if not key:
        return {"provider": provider, "ok": False, "error": "no key"}
    try:
        models = llm.list_models(provider, key)
    except llm.LLMError as exc:
        with _lock:
            _errors[provider] = exc.message
        bus.warn("%s key test failed: %s" % (llm.LABELS[provider], exc), src="keys")
        return {"provider": provider, "ok": False, "error": str(exc)}
    with _lock:
        _models[provider] = models
        if _chosen.get(provider) not in [m.id for m in models]:
            d = llm.default_model(provider, models)
            if d:
                _chosen[provider] = d
    bus.info("%s key works: %d models, using %s" % (llm.LABELS[provider], len(models), _chosen.get(provider)), src="keys")
    return {"provider": provider, "ok": True, "models": len(models), "model": _chosen.get(provider)}


def models(provider: str) -> list:
    with _lock:
        return list(_models.get(provider, []))


def choose(provider: str, model: str) -> None:
    with _lock:
        _chosen[provider] = model
    bus.info("%s model: %s" % (llm.LABELS[provider], model), src="keys")


def model(provider: str) -> str | None:
    with _lock:
        if provider in _chosen:
            return _chosen[provider]
    if get_key(provider):
        test(provider)
    with _lock:
        return _chosen.get(provider)


def pick(provider: str = "auto", model_id: str | None = None) -> tuple[str, str, str]:
    """(provider, key, model) ready for llm.conversation. Raises if no key is set."""
    if provider == "auto":
        av = available()
        if not av:
            raise RuntimeError("No API key yet: run the keys cell (2) or add one in Colab Secrets.")
        provider = av[0]
    key = get_key(provider)
    if not key:
        raise RuntimeError("No %s key: run the keys cell (2)." % llm.LABELS.get(provider, provider))
    m = model_id or model(provider)
    if not m:
        raise RuntimeError("Could not list %s models: %s" % (provider, _errors.get(provider, "unknown error")))
    return provider, key, m


def public_status() -> dict:
    """What the dashboard may see: which providers have keys and which models. Never the keys."""
    out = {}
    for p in llm.PROVIDERS:
        with _lock:
            ms = [m.id for m in _models.get(p, [])]
            out[p] = {"label": llm.LABELS[p], "has_key": bool(get_key(p)), "models": ms[:60], "model": _chosen.get(p),
                      "error": _errors.get(p)}
    return out


# ---------------------------------------------------------------------- notebook widget
def widget():
    """Password boxes, a Test button and model dropdowns. Falls back to getpass without ipywidgets."""
    try:
        import ipywidgets as W
        from IPython.display import display
    except Exception:
        import getpass
        for p in llm.PROVIDERS:
            v = getpass.getpass("%s API key (Enter to skip): " % llm.LABELS[p])
            if v:
                set_key(p, v)
        for p in available():
            print(test(p))
        return None

    rows, boxes, drops, labels = [], {}, {}, {}
    for p in llm.PROVIDERS:
        boxes[p] = W.Password(placeholder=HINTS[p], description=llm.LABELS[p], continuous_update=False,
                              style={"description_width": "150px"}, layout=W.Layout(width="420px"))
        drops[p] = W.Dropdown(options=[], description="model", layout=W.Layout(width="330px"), disabled=True)
        labels[p] = W.HTML(value=_badge(p))
        rows.append(W.HBox([boxes[p], drops[p], labels[p]]))

        def on_key(change, p=p):
            set_key(p, change["new"])
            labels[p].value = _badge(p)

        def on_model(change, p=p):
            if change["new"]:
                choose(p, change["new"])
        boxes[p].observe(on_key, names="value")
        drops[p].observe(on_model, names="value")

    b_secrets = W.Button(description="Load from Colab Secrets", icon="key")
    b_test = W.Button(description="Test keys & list models", button_style="primary", icon="check")
    b_forget = W.Button(description="Forget keys", icon="trash")
    note = W.HTML("<small>Keys stay in this Colab kernel. They are redacted from logs and never sent to the dashboard "
                  "or a tunnel. Add them once under the key icon (Secrets) as OPENAI_API_KEY, ANTHROPIC_API_KEY, "
                  "GEMINI_API_KEY or NVIDIA_API_KEY to skip typing.</small>")

    def refresh():
        for p in llm.PROVIDERS:
            ms = [m.id for m in models(p)]
            drops[p].options = ms
            drops[p].disabled = not ms
            if ms and _chosen.get(p) in ms:
                drops[p].value = _chosen[p]
            labels[p].value = _badge(p)

    def do_secrets(_):
        got = from_secrets()
        note.value = "<small>Loaded from Secrets: %s</small>" % (", ".join(got) or "none found (or access not granted)")
        refresh()

    def do_test(_):
        for p in available():
            labels[p].value = "<span style='color:#888'>testing…</span>"
            test(p)
        refresh()

    def do_forget(_):
        forget()
        for p in llm.PROVIDERS:
            boxes[p].value = ""
        refresh()

    b_secrets.on_click(do_secrets)
    b_test.on_click(do_test)
    b_forget.on_click(do_forget)
    ui = W.VBox(rows + [W.HBox([b_secrets, b_test, b_forget]), note])
    display(ui)
    if from_secrets():
        do_test(None)
    refresh()
    return ui


def _badge(p: str) -> str:
    if not get_key(p):
        return "<span style='color:#888'>no key</span>"
    if _errors.get(p):
        return "<span style='color:#b3261e'>✗ %s</span>" % _errors[p][:60].replace("<", "&lt;")
    if _models.get(p):
        return "<span style='color:#1d6b3a'>✓ %d models</span>" % len(_models[p])
    return "<span style='color:#9a5b00'>key set, not tested</span>"
