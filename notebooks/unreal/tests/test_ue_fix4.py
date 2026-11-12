"""Fixes from the 11 Nov 2026 A100 and L4 runs: the Nanite pool under Vulkan's 2 GB allocation, samples that accumulate,
the Zen shader cache on Drive, OpenImageDenoise instead of NNEDenoiser."""
import tarfile

from x3due import pipeline, project, tier, uerun

A100_CRASH = """[2026.11.11-19.46.22:357][  0]LogCore: Error: appError called: Fatal error: [File:./Runtime/Engine/Private/Rendering/NaniteStreamingManager.cpp] [Line: 839]
Streaming pool size (3276MB) must be smaller than the largest allocation supported by the graphics hardware (2048MB)
Caught signal 11 Segmentation fault"""


def test_nanite_pool_stays_under_vulkans_2gb_on_every_gpu():
    for vram in (16, 24, 40, 80, 96, 141):
        for rung in tier.ladder("A100", vram, False).values():
            pool = rung["cvars"].get("r.Nanite.Streaming.StreamingPoolSize")
            assert pool is None or pool < 2048, (vram, pool)
    assert tier.ladder("L4", 22, True)["high"]["cvars"]["r.Nanite.Streaming.StreamingPoolSize"] == int(0.04 * 22 * 1024)


def test_samples_are_spatial():
    for kind, vram, rt in (("A100", 80, False), ("L4", 22, True), ("G4", 96, True), ("T4", 15, True)):
        for name, r in tier.ladder(kind, vram, rt).items():
            assert r["temporal_samples"] == 1, (kind, name)
    assert tier.ladder("A100", 80, False)["ultra"]["spatial_samples"] == 128      # 2 x 64 before


def test_nanite_crash_is_named_and_retried_with_a_smaller_pool():
    proof = uerun.rhi_proof(A100_CRASH)
    sigs = [c["signature"] for c in proof["crashes"]]
    assert "nanite_pool" in sigs
    label, change = uerun.next_attempt(proof, [])
    assert change == {"cvars": {"r.Nanite.Streaming.StreamingPoolSize": 1000}}


def test_project_uses_openimagedenoise(tmp_path):
    eng = tmp_path / "engine"
    for p in ("PythonScriptPlugin", "MovieRenderPipeline", "OpenImageDenoise", "NNEDenoiser"):
        (eng / "Engine/Plugins" / p).mkdir(parents=True)
        (eng / "Engine/Plugins" / p / (p + ".uplugin")).write_text("{}")
    r = project.write(tmp_path / "proj", eng, ray_tracing=True)
    names = {p["Name"]: p["Enabled"] for p in r["plugins"]}
    assert names.get("OpenImageDenoise") is True and "NNEDenoiser" not in names


class _Lab:
    def __init__(self, root):
        self.home, self.ddc = root / "home/ue", root / "content/x3due/ddc"
        self.project_dir, self.work, self.user = root / "content/x3due/X3DLab", root / "work", "nobody-x3dlab-test"
        self.work.mkdir(parents=True)
        self.log = print


def test_shader_cache_round_trip_keeps_the_zen_store(tmp_path):
    root = tmp_path / "root"
    lab = _Lab(root)
    zen = lab.home / ".config/Epic/UnrealEngine/Common/Zen/Data"
    zen.mkdir(parents=True)
    (zen / "cas.ucas").write_bytes(b"z" * 5000)
    (lab.project_dir / "DerivedDataCache").mkdir(parents=True)
    drive = tmp_path / "drive"
    s = pipeline.ddc_save(lab, drive, "5.4.4", root=root)
    assert "error" not in s and s["archive"].endswith(("shaders_5.4.4.tar.zst", "shaders_5.4.4.tar.gz"))
    assert "home/ue" in s["paths"] and "content/x3due/X3DLab/DerivedDataCache" in s["paths"]
    (zen / "cas.ucas").unlink()
    r = pipeline.ddc_restore(lab, drive, "5.4.4", root=root)
    assert r["rc"] == 0 and (zen / "cas.ucas").read_bytes() == b"z" * 5000
    assert pipeline.ddc_restore(lab, drive, "9.9", root=root)["restored"] == s["archive"]   # newest archive when no exact one
    assert "skipped" in pipeline.ddc_restore(lab, tmp_path / "empty", root=root)


def test_old_ddc_archive_still_restores(tmp_path):
    lab = _Lab(tmp_path / "root")
    src = tmp_path / "src" / "ddc"
    src.mkdir(parents=True)
    (src / "a").write_text("old")
    (tmp_path / "drive" / "cache").mkdir(parents=True)
    with tarfile.open(tmp_path / "drive" / "cache" / "ddc.tar.gz", "w:gz") as t:
        t.add(src, arcname="ddc")
    r = pipeline.ddc_restore(lab, tmp_path / "drive")
    assert r["rc"] == 0 and (lab.ddc / "a").read_text() == "old"


L4_FAULTED = """LogVulkanRHI: Display: Found 6 Queue Families
LogLinux: Warning: MessageBox: Cannot create a Vulkan device. Try updating your video driver to a more recent version.
: Vulkan device creation failed: :
LogCore: FUnixPlatformMisc::RequestExit(bForce=true, ReturnCode=1, CallSite=<NoCallSiteInfo>)"""


def test_a_faulted_gpu_stops_every_later_job(tmp_path):
    """11 Nov 2026, L4: a device-lost render left the GPU refusing new Vulkan devices; every later job died in 5 s
    with rc 1 and the notebook went on with no stills."""
    from test_ue_pipeline import FakeUnreal, make_lab
    S = __import__("samples")
    fake = FakeUnreal([S.UE_LOG_VULKAN_OK, L4_FAULTED, L4_FAULTED])
    lab, drive = make_lab(tmp_path, fake)
    pipeline.get_engine(lab, "zip", drive)
    lab.write_project()
    first = pipeline.smoke(lab, "smoke_cold")
    assert first["proof"]["verdict"] == "PASS" and lab.gpu_worked
    second = pipeline.smoke(lab, "smoke_warm")
    assert "gpu_faulted" in [c["signature"] for c in second["proof"]["crashes"]] and "faulted" in lab.gpu_faulted
    n = len(fake.calls)
    third = pipeline.smoke(lab, "again")
    assert third["killed"] == "gpu faulted" and len(fake.calls) == n          # not even launched


def test_cannot_create_device_on_a_fresh_runtime_is_not_called_faulted(tmp_path):
    from test_ue_pipeline import FakeUnreal, make_lab
    fake = FakeUnreal([L4_FAULTED] * 5)
    lab, drive = make_lab(tmp_path, fake)
    pipeline.get_engine(lab, "zip", drive)
    lab.write_project()
    pipeline.smoke(lab, "smoke_cold")
    assert not getattr(lab, "gpu_faulted", None)                # never worked here: a setup problem, not a fault
