"""bringup.up(): notebook 07's steps 3-6 in one call (for notebook 08), with a fake GPU, Vulkan and Unreal."""
import samples as S
from test_ue_engine import fake_zip
from test_ue_pipeline import FakeUnreal

from x3due import bringup, tier

A100 = lambda: tier.detect(S.SMI_QUERY["A100-80"], S.SMI_HEADER_A100, S.MEMINFO["A100-80"], cpus=12, disk_path="/")
NV = {"deviceName": "NVIDIA A100-SXM4-80GB", "vendorID": "0x10de", "apiVersion": "1.4.312", "software": False, "extensions": []}


def test_up_brings_unreal_to_a_warm_start(tmp_path):
    drive = tmp_path / "drive"
    drive.mkdir()
    fake_zip(drive / "Linux_Unreal_Engine_5.8.4.zip")
    lines = []
    up = bringup.up(tmp_path / "work", drive, "auto", "newest", verbose=False, visual=False, gentle_mode=False, log=lines.append,
                    detect=A100, enable=lambda: {"driver": "580.82.07"}, probe=lambda user: {"devices": [NV], "nvidia_device": NV},
                    runner=FakeUnreal())
    assert up["ok"], up["why"]
    assert up["engine"]["version"] == "5.8.4" and up["lab"].settings["sm6"] and not up["lab"].settings["ray_tracing"]
    ini = (up["lab"].project_dir / "Config/DefaultEngine.ini").read_text()
    assert "r.Nanite.Streaming.StreamingPoolSize=2000" in ini                    # 80 GB: capped under Vulkan's 2 GB
    assert any(l.startswith("start    : rc 0") for l in lines)


def test_up_says_what_to_do(tmp_path):
    no_vk = bringup.up(tmp_path / "w", tmp_path, verbose=False, visual=False, gentle_mode=False, log=lambda *a: None, detect=A100,
                       enable=lambda: {}, probe=lambda user: {"devices": [], "nvidia_device": None}, runner=FakeUnreal())
    assert not no_vk["ok"] and "4b" in no_vk["why"]
    crash = bringup.up(tmp_path / "w2", tmp_path / "d", verbose=False, visual=False, gentle_mode=False, log=lambda *a: None,
                       detect=A100, enable=lambda: {}, probe=lambda user: {"devices": [NV], "nvidia_device": NV},
                       runner=FakeUnreal([S.UE_LOG_DEVICE_LOST] * 5))
    assert not crash["ok"] and ("no engine" in crash["why"] or "6c" in crash["why"])
