"""x3dlab.graphics: pick exactly the driver's version of libnvidia-gl, never a near miss; do nothing without a GPU."""

from x3dlab import browser, graphics

MADISON = """libnvidia-gl-580 | 580.95.05-0ubuntu1 | https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64  Packages
libnvidia-gl-580 | 580.82.07-0ubuntu1 | https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64  Packages
libnvidia-gl-580 | 580.82.071-1 | http://example/odd Packages
"""
SERVER = "libnvidia-gl-580-server | 580.82.07-0ubuntu0.24.04.1 | http://archive.ubuntu.com/ubuntu noble-updates/restricted amd64 Packages\n"


def test_parse_madison():
    rows = graphics.parse_madison(MADISON + SERVER + "garbage line\n")
    assert rows[0] == ("libnvidia-gl-580", "580.95.05-0ubuntu1") and len(rows) == 4


def test_pick_exact_version_only():
    rows = graphics.parse_madison(MADISON + SERVER)
    assert graphics.pick_package("580.82.07", rows) == ("libnvidia-gl-580", "580.82.07-0ubuntu1")     # not the -server one, not 580.82.071
    assert graphics.pick_package("580.82.07", graphics.parse_madison(SERVER)) == ("libnvidia-gl-580-server", "580.82.07-0ubuntu0.24.04.1")
    assert graphics.pick_package("580.82.09", rows) is None                                           # a near miss is never installed
    assert graphics.pick_package("525.147.05", [("libnvidia-gl-525", "1:525.147.05-0ubuntu1")]) == ("libnvidia-gl-525", "1:525.147.05-0ubuntu1")


def test_enable_without_gpu_changes_nothing(monkeypatch):
    monkeypatch.setattr(graphics, "driver_version", lambda: None)
    called = []
    monkeypatch.setattr(graphics, "_run", lambda *a, **k: called.append(a))
    rep = graphics.enable()
    assert rep["skipped"] == "no NVIDIA GPU" and rep["installed"] is None and not called


def test_enable_refuses_without_exact_match(monkeypatch):
    monkeypatch.setattr(graphics, "driver_version", lambda: "580.82.07")
    monkeypatch.setattr(graphics, "vulkan_devices", lambda: [])
    monkeypatch.setattr(graphics.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(graphics.os, "geteuid", lambda: 0)
    cmds = []

    class R:
        returncode, stderr = 0, ""
        stdout = "libnvidia-gl-580 | 580.95.05-0ubuntu1 | x Packages\n"

    def fake(cmd, timeout=0):
        cmds.append(cmd)
        return R()
    monkeypatch.setattr(graphics, "_run", fake)
    rep = graphics.enable()
    assert rep["installed"] is None and "exactly 580.82.07" in rep["skipped"]
    assert not [c for c in cmds if c[:2] == ["apt-get", "install"]]


def test_gpu_args_have_no_surface_flag():
    assert "--disable-vulkan-surface" in browser.GPU_ARGS and "--use-angle=vulkan" in browser.GPU_ARGS
    assert "--disable-vulkan-surface" not in browser.CHROME_ARGS


def test_unpack_downloads_the_graphics_trio_only(monkeypatch, tmp_path):
    calls = []

    class R:
        returncode, stdout, stderr = 0, "", ""

    def fake_sub_run(cmd, cwd=None, capture_output=True, text=True, timeout=0):
        calls.append(cmd)
        if cmd[:2] == ["apt-get", "download"]:
            (tmp_path / (cmd[2].replace("=", "_") + ".deb")).write_text("x")
        return R()
    monkeypatch.setattr(graphics.subprocess, "run", fake_sub_run)
    monkeypatch.setattr(graphics, "_run", lambda cmd, timeout=0: (calls.append(cmd), R())[1])
    out = graphics.unpack(("libnvidia-gl-580", "580.82.07-0ubuntu1"), str(tmp_path))
    dl = [c[2] for c in calls if c[:2] == ["apt-get", "download"]]
    assert dl == ["libnvidia-gl-580=580.82.07-0ubuntu1", "libnvidia-gpucomp-580=580.82.07-0ubuntu1",
                  "libnvidia-common-580=580.82.07-0ubuntu1"]
    assert not [c for c in calls if "install" in c or any("compute" in str(x) or "cfg1" in str(x) for x in c)]   # never CUDA's packages
    assert sum(1 for c in calls if c[:2] == ["dpkg", "-x"]) == 3 and ["ldconfig"] in calls and len(out["unpacked"]) == 3
    calls.clear()
    graphics.unpack(("libnvidia-gl-580-server", "580.82.07-0ubuntu0.24.04.1"), str(tmp_path))
    assert [c[2].split("=")[0] for c in calls if c[:2] == ["apt-get", "download"]] == [
        "libnvidia-gl-580-server", "libnvidia-gpucomp-580-server", "libnvidia-common-580-server"]
