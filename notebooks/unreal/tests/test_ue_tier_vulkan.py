import pytest
import samples as S

from x3due import tier, vulkan


def hw_for(key, mem="A100-80"):
    return tier.detect(S.SMI_QUERY[key], S.SMI_HEADER_A100, S.MEMINFO.get(mem, S.MEMINFO["A100-80"]), cpus=12, disk_path="/")


@pytest.mark.parametrize("key,kind,vram", [("A100-80", "A100", 80), ("A100-40", "A100", 40), ("T4", "T4", 15),
                                           ("L4", "L4", 22.5), ("G4", "G4", 95.6), ("H100", "H100", 79.6)])
def test_detect_kind_and_vram(key, kind, vram):
    hw = hw_for(key)
    assert hw["kind"] == kind and abs(hw["vram_gb"] - vram) < 0.1
    assert hw["cuda"] == "13.0" and hw["cpus"] == 12 and hw["ram_gb"] == pytest.approx(167.0, abs=0.1)


def test_no_gpu():
    hw = tier.detect("", "", S.MEMINFO["T4"], cpus=2, disk_path="/")
    assert hw["kind"] == "CPU" and hw["gpu"] is None


def vk(name, rt):
    return vulkan.probe(summary_text=S.VK_SUMMARY_NVIDIA.format(name=name), full_text=S.vk_full(rt))


def test_vulkan_parsing():
    v = vk("NVIDIA L4", True)
    assert [d["deviceName"] for d in v["devices"]] == ["NVIDIA L4", "llvmpipe (LLVM 15.0.7, 256 bits)"]
    assert v["nvidia_device"]["deviceName"] == "NVIDIA L4" and not v["nvidia_device"]["software"]
    assert v["devices"][1]["software"] and not v["software_only"]
    assert "VK_KHR_ray_tracing_pipeline" in v["nvidia_device"]["extensions"]
    assert v["devices"][1]["extensions"] == ["VK_KHR_16bit_storage", "VK_KHR_swapchain"]
    s = vulkan.probe(summary_text=S.VK_SUMMARY_SOFTWARE_ONLY, full_text="")
    assert s["nvidia_device"] is None and s["software_only"]


@pytest.mark.parametrize("key,rt_ext,force,hw_rt,status", [
    ("A100-80", False, False, False, "PASS"),      # no RT cores, no extensions: software Lumen, as expected
    ("A100-80", True, False, False, "WARN"),       # driver lists them anyway: still off (emulated would be slow)
    ("A100-80", True, True, True, "PASS"),         # unless forced
    ("L4", True, False, True, "PASS"),
    ("L4", False, False, False, "FAIL"),           # RT cores but Vulkan lacks the extensions: the unpack went wrong
    ("G4", True, False, True, "PASS"),
    ("T4", True, False, True, "PASS"),
])
def test_ray_tracing_decision(key, rt_ext, force, hw_rt, status):
    hw = hw_for(key)
    p = tier.plan(hw, vk(hw["gpu"]["name"], rt_ext), force_hwrt=force)
    assert p["ray_tracing"]["hw_rt"] is hw_rt and p["ray_tracing"]["status"] == status
    lad = p["ladder"]
    assert set(lad) >= {"preview", "high", "ultra"}
    assert lad["ultra"]["renderer"] == ("pathtracer" if hw_rt else "lumen")
    for r in lad.values():
        assert r["resolution"][0] / r["resolution"][1] == pytest.approx(16 / 9, abs=0.01)


def test_no_vulkan_device_means_no_rt():
    p = tier.plan(hw_for("L4"), {"nvidia_device": None})
    assert p["ray_tracing"]["status"] == "SKIP" and not p["ray_tracing"]["hw_rt"]


def test_big_vram_buys_resolution_and_resident_pools():
    a = tier.plan(hw_for("A100-80"), vk("NVIDIA A100-SXM4-80GB", False))
    assert a["ladder"]["ultra"]["resolution"] == [7680, 4320] and a["ladder"]["ultra"]["tiles"] == 1
    assert a["ladder"]["ultra"]["texture_streaming"] == "fully_load"
    assert a["ladder"]["ultra"]["cvars"]["r.Streaming.PoolSize"] >= 20000
    assert any("no RT cores" in n for n in a["notes"])
    g = tier.plan(hw_for("G4"), vk("NVIDIA RTX PRO 6000 Blackwell Server Edition", True))
    assert g["ladder"]["ultra"]["renderer"] == "pathtracer" and g["ladder"]["ultra"]["spatial_samples"] >= 1024
    assert any("best Colab GPU" in n for n in g["notes"])
    t = tier.plan(hw_for("T4", "T4"), vk("Tesla T4", True))
    assert t["ladder"]["ultra"]["resolution"] == [2560, 1440] and t["ladder"]["ultra"]["texture_streaming"] == "default"


def test_shader_workers_capped_by_ram():
    assert tier.shader_workers(2, 12.7) == 1
    assert tier.shader_workers(12, 167) == 11
    assert tier.shader_workers(8, 53) == 7
    assert tier.shader_workers(8, 20) == 7
    assert tier.shader_workers(8, 12) == 4      # the 12 GB L4 of 11 Nov: 4 workers, not 1
    assert tier.shader_workers(8, 5) == 1


def test_capability_table_rows():
    t = tier.capability_table_markdown()
    assert "| A100 |" in t and "no RT cores" in t and "188 4th-gen RT cores" in t


# ---------------------------------------------------------------------- the unpack route
def test_unpack_plan_exact_version_only():
    p = vulkan.unpack_plan("580.82.07", S.MADISON_580)
    assert p["ok"]
    assert p["packages"] == {"libnvidia-gl-580": "580.82.07-0ubuntu1", "libnvidia-gpucomp-580": "580.82.07-0ubuntu1",
                             "libnvidia-common-580": "580.82.07-0ubuntu1"}
    cmds = [" ".join(c) for c in p["commands"]]
    assert "apt-get download libnvidia-gl-580=580.82.07-0ubuntu1 libnvidia-gpucomp-580=580.82.07-0ubuntu1 libnvidia-common-580=580.82.07-0ubuntu1" in cmds
    assert any(c.startswith("dpkg -x") for c in cmds) and "ldconfig" in cmds
    assert not any(c.startswith("apt-get install") and "libnvidia" in c for c in cmds)   # never apt-install the driver


def test_unpack_plan_refuses_a_near_miss():
    p = vulkan.unpack_plan("580.82.09", S.MADISON_580)
    assert not p["ok"] and not p["commands"] and "would break" in p["why"]


def test_unpack_plan_gpucomp_optional_and_server_flavour():
    m = {k: v for k, v in S.MADISON_580.items() if "gpucomp" not in k}
    assert vulkan.unpack_plan("580.82.07", m)["ok"]
    srv = {"libnvidia-gl-550-server": " libnvidia-gl-550-server | 550.54.15-0ubuntu0.22.04.1 | http://archive Packages\n",
           "libnvidia-common-550-server": " libnvidia-common-550-server | 550.54.15-0ubuntu0.22.04.1 | http://archive Packages\n"}
    p = vulkan.unpack_plan("550.54.15", srv)
    assert p["ok"] and set(p["packages"]) == {"libnvidia-gl-550-server", "libnvidia-common-550-server"}


def test_exact_version_patterns():
    assert vulkan.exact_version("580.82.07", ["580.82.07-0ubuntu1", "580.82.070-1"]) == "580.82.07-0ubuntu1"
    assert vulkan.exact_version("580.82.07", ["1:580.82.07-1"]) == "1:580.82.07-1"
    assert vulkan.exact_version("580.82.07", ["580.82.071"]) is None
