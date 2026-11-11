"""VERBOSE / VISUAL: live log lines, the heartbeat while Unreal is quiet, the disk guard, new images shown as they appear,
and the hook-up through run_unreal."""
import sys
import time
from pathlib import Path

import pytest
from test_ue_pipeline import FakeUnreal, make_lab

from x3due import live, uerun

SCRIPT = r"""
import sys, time
print("[2026.11.11-08.00.00:000][  0]LogInit: Build: ++UE5+Release-5.4-CL-1", flush=True)
print("[2026.11.11-08.00.00:100][  0]LogVulkanRHI: Display: Found 1 device(s): NVIDIA L4", flush=True)
for i in range(3):
    print("LogShaderCompilers: Display: Worker (3/11) finished batch of 25 jobs", flush=True)
time.sleep(2.6)                                     # quiet, like a cold shader compile
print("LogShaderCompilers: Display: Shaders left to compile 300", flush=True)
print("LogPython: x3dlab: imported 18 meshes", flush=True)
print("LogWindows: Error: something broke", flush=True)
sys.exit(0)
"""


def collect():
    lines = []
    return lines, lines.append


def test_classify_count_and_rate_limit():
    got, out = collect()
    L = live.Live(verbose=True, visual=False, every=9999, out=out, stats=lambda: {})
    L.begin("smoke_cold")
    for _ in range(50):
        L.line("LogShaderCompilers: Display: Worker (1/11) finished batch of 10 jobs")
    L.line("LogShaderCompilers: Display: Shaders left to compile 1234")
    L.line("[2026.11.11-08.00.00:000][  0]LogVulkanRHI: Display: Using Device 0: NVIDIA L4 (Vulkan 1.3)")
    for i in range(60):
        L.line("LogTemp: Error: boom %d" % i)
    L.line("LogSomethingNoisy: Display: ignore me")
    assert L.jobs_done == 500 and L.jobs_left == 1234
    shader = [g for g in got if "finished batch" in g]
    assert len(shader) == 1                                    # 30 s between shader lines
    assert sum("Error: boom" in g for g in got) == 40          # errors capped per job
    assert any("NVIDIA L4" in g and "LogVulkanRHI" in g and "[2026" not in g for g in got)
    assert not any("ignore me" in g for g in got)


def test_quiet_flag_prints_nothing_but_still_counts():
    got, out = collect()
    L = live.Live(verbose=False, visual=False, out=out, stats=lambda: {})
    L.begin("x")
    L.line("LogShaderCompilers: Display: Worker (1/2) finished batch of 7 jobs")
    L.tick(100, None)
    assert L.jobs_done == 7 and got == []


def test_heartbeat_reports_rate_eta_memory_and_growth(monkeypatch):
    got, out = collect()
    stats = iter([{"unreal_rss_gb": 2.0, "workers": 11}, {"unreal_rss_gb": 5.5, "workers": 11}])
    L = live.Live(verbose=True, visual=False, every=20, out=out, stats=lambda: next(stats), disk_path="/")
    L.begin("smoke_cold")
    clock = [1000.0]
    monkeypatch.setattr(live.time, "time", lambda: clock[0])
    L.jobs_hist, L.rss_hist = [], []
    L.line("finished batch of 100 jobs")
    L.tick(20, {"mem_mb": 4096, "total_mb": 23034, "util": 37})
    clock[0] += 60
    L.line("finished batch of 100 jobs")
    L.line("Shaders left to compile 600")
    L.tick(41, {"mem_mb": 4096, "total_mb": 23034, "util": 37})
    beat = [g for g in got if "⏱" in g][-1]
    assert "200 jobs done (100/min)" in beat and "600 left" in beat and "~6:00 to go" in beat
    assert "11 workers" in beat and "Unreal 5.5 GB" in beat and "VRAM 4.0/22 GB · GPU 37%" in beat and "disk" in beat
    assert not any("⚠️" in g for g in got)                   # it grew, but shader jobs were finishing
    L.jobs_hist.append((clock[0] + 1, 200))
    L.rss_hist.append((clock[0] + 1, 9.0))
    L.jobs_hist.append((clock[0] + 2, 200))
    L.rss_hist = [(clock[0] + 1, 5.5), (clock[0] + 2, 9.0)]
    L.jobs_hist = [(clock[0] + 1, 200), (clock[0] + 2, 200)]
    assert "grew 3.5 GB" in L.growth_warning()


def test_run_streams_ticks_while_quiet_and_keeps_the_log(tmp_path):
    lines, ticks = [], []
    log = tmp_path / "logs" / "smoke.0.log"
    t0 = time.time()
    res = uerun.run([sys.executable, "-c", SCRIPT], log, on_line=lines.append, on_tick=lambda e, g: ticks.append(e), min_free_gb=0)
    assert res["rc"] == 0 and res["killed"] is None and "gpu_series" in res
    assert len(lines) == 8 and lines[-1].endswith("something broke")
    assert log.read_text().count("\n") == 8
    quiet = [e for e in ticks if 0.5 < e < 2.6 + 0.4]
    assert len(quiet) >= 2, ticks                              # ticks kept coming while the process printed nothing
    assert time.time() - t0 < 15


def test_disk_guard_stops_the_job(tmp_path):
    res = uerun.run([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path / "l.log", min_free_gb=1e9)
    assert res["killed"].startswith("disk nearly full") and res["seconds"] < 10


def test_callbacks_cannot_break_a_run(tmp_path):
    def bad(*a):
        raise RuntimeError("printing failed")
    res = uerun.run([sys.executable, "-c", "print('hi')"], tmp_path / "l.log", on_line=bad, on_tick=bad, min_free_gb=0)
    assert res["rc"] == 0


def test_visual_shows_each_new_image_once(tmp_path):
    shown = []
    d = tmp_path / "stills"
    d.mkdir()
    (d / "old.png").write_bytes(b"x")
    past = time.time() - 3600
    import os
    os.utime(d / "old.png", (past, past))
    L = live.Live(verbose=False, visual=True, out=lambda *a: None, show_image=lambda f, c: shown.append((f, c)))
    L.begin("render_demo_high", [d])
    (d / "Hero.png").write_bytes(b"png")
    (d / "Hero.exr").write_bytes(b"exr")
    L.tick(1, None)                      # first look: size noted
    assert shown == []
    L.tick(2, None)                      # second look, same size: shown
    L.end({"rc": 0, "seconds": 3})
    assert [Path(f).name for f, _ in shown] == ["Hero.png"] and "render_demo_high" in shown[0][1]
    assert str(d / "Hero.png") in L.shown


def test_run_unreal_hands_progress_to_live(tmp_path):
    fake = FakeUnreal()
    lab, drive = make_lab(tmp_path, fake)
    from x3due import pipeline
    pipeline.get_engine(lab, "zip", drive)
    lab.write_project()
    got = []
    lab.live = live.Live(verbose=True, visual=False, out=got.append, stats=lambda: {})
    r = pipeline.smoke(lab, "smoke_cold")
    assert r["proof"]["verdict"] == "PASS"
    assert any(g.startswith("▶ smoke_cold") for g in got) and any(g.startswith("■ smoke_cold: rc") for g in got)


def test_notebook_has_the_switches():
    src = (Path(__file__).resolve().parents[1] / "src" / "07_unreal.py").read_text()
    for k in ('VERBOSE = True  #@param', 'VISUAL = True  #@param', "HEARTBEAT_S", "LAB.live = live.Live("):
        assert k in src


def test_cell2_clones_a_private_repo_with_the_token_as_a_header():
    src = (Path(__file__).resolve().parents[1] / "src" / "07_unreal.py").read_text()
    cell = src.split("#@title 2 · Get the helpers")[1].split("# %%")[0]
    assert 'userdata.get("GITHUB_TOKEN")' in cell and "extraheader" in cell and "x-access-token:" in cell
    assert 'git(["clone"' in cell and 'git(["-C"' in cell
    assert "https://%s@" not in cell and "print(token" not in cell          # never in a URL, never printed


@pytest.mark.parametrize("n,txt", [(59, "0:59"), (3725, "1:02:05")])
def test_mmss(n, txt):
    assert live.mmss(n) == txt
