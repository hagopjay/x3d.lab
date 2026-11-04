import json

import pytest

from x3dlab.events import Bus, format_event


def test_secrets_never_reach_sinks(tmp_path):
    b = Bus()
    b.configure(to_screen=False, jsonl=tmp_path / "e.jsonl")
    seen = []
    b.subscribe(seen.append)
    b.add_secret("sk-THIS-IS-SECRET-123")
    b.add_secret("short")            # too short to register: would redact ordinary words
    b.info("key is sk-THIS-IS-SECRET-123 ok", data={"nested": ["x sk-THIS-IS-SECRET-123 y"], "w": "short"})
    ev = seen[-1]
    assert "sk-THIS" not in json.dumps(ev)
    assert "[redacted]" in ev["msg"] and ev["data"]["w"] == "short"
    assert "sk-THIS" not in (tmp_path / "e.jsonl").read_text()


def test_since_and_ring():
    b = Bus(capacity=5)
    b.configure(to_screen=False)
    for i in range(8):
        b.info("e%d" % i)
    assert [e["msg"] for e in b.since(0)] == ["e3", "e4", "e5", "e6", "e7"]
    last = b.since(0)[-2]["seq"]
    assert [e["msg"] for e in b.since(last)] == ["e7"]


def test_span_records_timing_and_errors():
    b = Bus()
    b.configure(to_screen=False)
    seen = []
    b.subscribe(seen.append)
    with b.span("work", track="Validator") as info:
        info["n"] = 3
    with pytest.raises(ValueError):
        with b.span("boom", track="LLM"):
            raise ValueError("nope")
    ok, bad = seen[-2], seen[-1]
    assert ok["span"]["track"] == "Validator" and ok["span"]["end"] >= ok["span"]["start"] and ok["data"]["n"] == 3
    assert bad["level"] == "ERROR" and bad["data"]["status"] == "error" and "nope" in bad["data"]["error"]


def test_screen_filter_and_density(capsys):
    b = Bus()
    printed = []
    b.screen = printed.append
    b.configure(level="WARN")
    b.info("quiet")
    b.warn("loud", data={"a": 1})
    assert [e["msg"] for e in printed] == ["loud"]
    ev = printed[0]
    assert "a=1" in format_event(ev, "rich")
    assert '"a": 1' in format_event(ev, "firehose")
    assert format_event(ev, "compact").endswith("loud")
    with pytest.raises(ValueError):
        b.configure(density="loud")
