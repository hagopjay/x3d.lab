"""The Drive heartbeat: beats, history, latest.json and the out/ mirror; data out only."""
import json
import time

from x3due import heartbeat


class Live:
    label, jobs_done, last_line = "smoke_cold", 12, "LogShaderCompilers: Display: Worker (1/8) finished"

    def __init__(self):
        self.t0 = time.time() - 30


class Lab:
    live = Live()


def test_beat_writes_status_history_and_mirror(tmp_path):
    out = tmp_path / "out"
    (out / "logs").mkdir(parents=True)
    (out / "logs" / "smoke.log").write_text("hello")
    (out / "big.bin").write_bytes(b"x" * (2 * 2**20))
    ctx = {"LAB": Lab(), "ENGINE": {"version": "5.4.4"}, "SMOKE": {"cold": {"rc": 0, "seconds": 900, "killed": None}}, "STAGE": "G1"}
    hb = heartbeat.Heartbeat(tmp_path / "drive", out, context=lambda: ctx, mirror_s=0, mirror_max_mb=1, run_id="run1")
    st = hb.beat()
    assert st["job"] == "smoke_cold" and st["shader_jobs_done"] == 12 and st["engine"] == "5.4.4" and st["stage"] == "G1"
    d = tmp_path / "drive" / "heartbeat"
    assert json.loads((d / "run1" / "heartbeat.json").read_text())["beat"] == 1
    assert "smoke_cold" in (d / "run1" / "heartbeat.log").read_text()
    assert json.loads((d / "latest.json").read_text())["run_id"] == "run1"
    assert (d / "run1" / "out" / "logs" / "smoke.log").read_text() == "hello"
    assert not (d / "run1" / "out" / "big.bin").exists()                      # over mirror_max_mb
    assert hb.mirror() == 0                                                     # nothing changed
    (out / "logs" / "smoke.log").write_text("hello again")
    time.sleep(1.1)
    (out / "logs" / "smoke.log").write_text("hello again!")
    assert hb.mirror() == 1


def test_background_thread_starts_and_stops(tmp_path):
    hb = heartbeat.Heartbeat(tmp_path / "drive", tmp_path / "out", beat_s=0.05, run_id="r").start()
    time.sleep(0.3)
    hb.stop()
    time.sleep(0.2)
    assert not hb.alive() and hb.beats >= 2
