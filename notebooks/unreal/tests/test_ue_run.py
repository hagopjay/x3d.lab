import os
import sys

import pytest
import samples as S

from x3due import uerun


def test_proof_vulkan_on_nvidia():
    p = uerun.rhi_proof(S.UE_LOG_VULKAN_OK)
    assert p["verdict"] == "PASS" and p["device"] == "NVIDIA A100-SXM4-80GB" and p["vendor_id"] == "0x10de"
    assert p["shader_platform"] == "VULKAN_SM6" and p["driver"] == "580.82.07" and p["api"] == "1.4.312"
    assert p["engine_version"].startswith("5.8.4") and not p["crashes"]


@pytest.mark.parametrize("log,why", [(S.UE_LOG_NULLRHI, "NullRHI"), (S.UE_LOG_LLVMPIPE, "software"), ("LogInit: hello", "no Vulkan")])
def test_proof_fails(log, why):
    p = uerun.rhi_proof(log)
    assert p["verdict"] == "FAIL" and why in p["why"]


def test_proof_empty_and_crashes():
    assert uerun.rhi_proof("")["verdict"] == "SKIP"
    assert uerun.rhi_proof(S.UE_LOG_ROOT)["crashes"][0]["signature"] == "root"
    d = uerun.rhi_proof(S.UE_LOG_DEVICE_LOST)
    assert d["verdict"] == "WARN" and "device_lost" in {c["signature"] for c in d["crashes"]}
    b = uerun.rhi_proof(S.UE_LOG_BINDLESS)
    assert "bindless_rt" in {c["signature"] for c in b["crashes"]}


def test_retry_ladder_order():
    d = uerun.rhi_proof(S.UE_LOG_DEVICE_LOST)
    tried = []
    seq = []
    while True:
        nxt = uerun.next_attempt(d, tried)
        if not nxt:
            break
        tried.append(nxt[0])
        seq.append(nxt)
    assert [s[0] for s in seq] == ["async compute off", "no ray tracing", "Shader Model 5"]
    assert seq[0][1] == {"cvars": {"r.Vulkan.AllowAsyncCompute": 0}}
    b = uerun.rhi_proof(S.UE_LOG_BINDLESS)
    assert uerun.next_attempt(b, [])[0] == "bindless for all shader types"


def test_excerpt_and_timing():
    ex = uerun.excerpt(S.UE_LOG_VULKAN_OK)
    assert "LogVulkanRHI" in ex and "--- last lines ---" in ex
    t = uerun.first_render_seconds(S.UE_LOG_VULKAN_OK)
    assert t["to_first_output_s"] == pytest.approx(37 * 60 + 57 - 0.0, abs=1) and t["shader_log_lines"] == 1


def test_commands():
    r = uerun.render_cmd("/e", "/p/X.uproject", "/Game/X3DLab/s/Map_s", "/j.json", sm6=True, resolution=(1280, 720))
    assert r[0] == "/e/Engine/Binaries/Linux/UnrealEditor" and "-game" in r and "-RenderOffscreen" in r and "-vulkan" in r
    assert "-ExecutorPythonClass=/Engine/PythonTypes.X3DLabExecutor" in r and "-unattended" in r and "-sm6" in r
    assert "-X3DLabJob=/j.json" in r and "-nullrhi" not in r
    c = uerun.commandlet_cmd("/e", "/p/X.uproject", "/s.py", "/j.json")
    assert c[0].endswith("UnrealEditor-Cmd") and "-run=pythonscript" in c and "-script=/s.py" in c


def test_env_pins_the_nvidia_icd(tmp_path):
    icd = tmp_path / "nvidia_icd.json"
    icd.write_text("{}")
    e = uerun.env("/home/ue", "/w/ddc", icd=str(icd), extra={"SDL_VIDEODRIVER": "offscreen"})
    assert e["VK_ICD_FILENAMES"] == str(icd) and e["HOME"] == "/home/ue" and e["UE-LocalDataCachePath"] == "/w/ddc"
    assert e["SDL_VIDEODRIVER"] == "offscreen" and e["UE-SharedDataCachePath"] == "None"


def test_ensure_user_dry_run():
    cmds = uerun.ensure_user("x3due_nobody_here", ["/w"], dry_run=True)
    assert ["useradd", "-m", "-s", "/bin/bash", "x3due_nobody_here"] in cmds and ["chown", "-R", "x3due_nobody_here:x3due_nobody_here", "/w"] in cmds


def test_run_streams_log_and_returns(tmp_path):
    log = tmp_path / "a.log"
    r = uerun.run([sys.executable, "-c", "print('LogRHI: Using Default RHI: Vulkan'); print('done')"], log, timeout=30)
    assert r["rc"] == 0 and r["killed"] is None and "Vulkan" in log.read_text()


def test_run_kills_on_timeout_only_its_own_group(tmp_path):
    r = uerun.run([sys.executable, "-c", "import time; print('x', flush=True); time.sleep(60)"], tmp_path / "b.log", timeout=2)
    assert r["killed"].startswith("timeout") and r["seconds"] < 30


def test_run_stops_a_lingering_process_after_done_file(tmp_path):
    done = tmp_path / "rep.done"
    code = "import time,pathlib; pathlib.Path(%r).write_text('1'); time.sleep(60)" % str(done)
    r = uerun.run([sys.executable, "-c", code], tmp_path / "c.log", timeout=50, done_file=str(done), done_grace=1)
    assert r["killed"] and "did not exit" in r["killed"]
