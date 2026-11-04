"""The gateway against the real x3d_mcp server: authentication, input handling, the MCP proxy, the event
stream. Several cases are the 500s the old x3d_duo coordinator returned; here each must be a 4xx."""

import time

import httpx
import pytest

from x3dlab.mcpclient import X3DMCP
from x3dlab.scenes import LANTERN


def test_key_routes(lab, base, k):
    with httpx.Client() as h:
        assert h.get(base + "/healthz").json() == {"ok": True}
        assert h.get(k + "api/health").json()["mcp"] == "up"
        assert h.get(base + "/api/health", params={"key": lab.dashboard_key}).status_code == 200
        assert h.get(base + "/api/health", headers={"x-lab-key": lab.dashboard_key}).status_code == 200
        r = h.get(base + "/k/" + lab.dashboard_key)
        assert r.status_code == 307 and r.headers["location"].endswith("/")


@pytest.mark.parametrize("path", ["/api/health", "/k/nope/api/health", "/k/%C3%A9/api/health", "/k/" + "a" * 10000 + "/",
                                  "/api/health?key=%C3%A9", "/", "/events"])
def test_wrong_keys_are_401_never_500(base, path):
    assert httpx.get(base + path).status_code == 401


def test_bad_input_is_4xx(lab, base, k):
    with httpx.Client() as h:
        post = lambda p, **kw: h.post(k + p, **kw)
        assert post("api/jobs", content=b"{nope", headers={"content-type": "application/json"}).status_code == 422
        assert post("api/jobs", json=[1, 2]).status_code == 422
        assert post("api/jobs", json={"kind": "rm -rf"}).status_code == 422
        assert post("api/validate", json={"xml": ""}).status_code == 422
        assert post("api/breakit", json={"defect": "not-a-defect"}).status_code == 400
        assert h.get(base + "/k/%s/out/abc/scene.x3d%%00.png" % lab.dashboard_key).status_code == 400
        for path in ("out/..%2f..%2fetc/passwd", "out/%2e%2e/%2e%2e/etc/passwd", "out/x/..%2f..%2f..%2fetc%2fpasswd",
                     "static/../../../../etc/passwd", "copilot/../../etc/passwd"):
            r = h.get(k + path)       # httpx collapses literal ../ before sending, so some arrive keyless: 401
            assert r.status_code in (400, 401, 404) and "root:" not in r.text
        assert h.get(k + "mcp").status_code == 404, "MCP must not be reachable with the dashboard key alone"


def test_server_timing_and_validate(k):
    r = httpx.post(k + "api/validate", json={"xml": LANTERN}, timeout=60)
    assert r.status_code == 200 and r.json()["ok"] is True
    st = r.headers["server-timing"]
    assert "gateway;dur=" in st and "validate;dur=" in st


def test_jobs_and_files(lab, k):
    with httpx.Client(timeout=60) as h:
        r = h.post(k + "api/jobs", json={"kind": "scene", "params": {"provider": "sample", "prompt": "x"}})
        assert r.status_code == 202
        jid = r.json()["job"]
        for _ in range(100):
            j = h.get(k + "api/jobs/" + jid).json()
            if j["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert j["status"] == "done" and j["result"]["valid"] is True and "_obj" not in j["result"]
        f = h.get(k + j["result"]["files"]["scene.x3d"])
        assert f.status_code == 200 and "<X3D" in f.text
        assert h.get(k + "api/jobs/../../x").status_code == 404


def test_event_stream_delivers(lab, k):
    from x3dlab.events import bus
    got = []
    with httpx.Client(timeout=15) as h:
        with h.stream("GET", k + "events", params={"since": 10**9}) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            bus.info("hello-from-test-%d" % id(got), src="test")
            deadline = time.time() + 10
            for line in r.iter_lines():
                if "hello-from-test" in line:
                    got.append(line)
                    break
                if time.time() > deadline:
                    break
    assert got


def test_mcp_proxy(lab, base):
    assert httpx.post(base + "/mcp", json={}).status_code == 401
    assert httpx.post(base + "/mcp", json={}, headers={"authorization": "Bearer " + lab.blob_token}).status_code == 401, \
        "the server's internal token must not open the public endpoint"
    c = X3DMCP(base + "/mcp", token=lab.mcp_token)
    tools = c.run(c.list_tools())
    assert len(tools) > 40
    v = c.check(LANTERN)
    assert v.ok


def test_beacon_and_copilot_absent(lab, k):
    assert httpx.post(k + "api/beacon", json={"type": "hello", "data": {"route": "test"}}).status_code == 204
    assert httpx.get(k + "copilot/").status_code == 404
