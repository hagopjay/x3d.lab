"""Engine versions and the first live Colab run (L4, 11 Nov 2026): UE 5.2 segfaulted in ShaderFormatVersion because the
project targeted Vulkan SM6, which 5.2's Linux build does not have. Picking the engine by version, the SM5 default before
5.4, the automatic SM5 retry, and the sanitised 001 notebook."""
import json
import re
import shutil
from pathlib import Path

import pytest
import samples as S
import test_ue_engine
from test_ue_pipeline import FakeUnreal, make_lab

from x3due import engine as E, pipeline, project, uerun

UNREAL = Path(__file__).resolve().parents[1]

# Unreal's own crash lines from that run (the call stack it printed into the log), trimmed to the frames that matter.
UE52_SM6_CRASH = """[2026.11.11-07.58.34:484][  0]LogInit: Build: ++UE5+Release-5.2-CL-25360045
[2026.11.11-07.58.34:540][  0]LogInit: Command Line: /Engine/Maps/Entry -game -RenderOffscreen -vulkan -windowed -sm6
Caught signal 11 Segmentation fault
0x00007dbe00063793 libUnrealEditor-TargetPlatform.so!FTargetPlatformManagerModule::ShaderFormatVersion(FName) [/mnt/horde/++UE5/Sync/Engine/Source/./Developer/TargetPlatform/Private/TargetPlatformManagerModule.cpp:653]
0x00007dbefb3a8b47 libUnrealEditor-Engine.so!GetGlobalShaderMapKeyString(FGlobalShaderMapId const&, EShaderPlatform, TArray<FShaderTypeDependency, TSizedDefaultAllocator<32> > const&) [/mnt/horde/++UE5/Sync/Engine/Source/./Runtime/Engine/Private/ShaderCompiler/ShaderCompiler.cpp:7375]
0x00007dbefb3a6ac5 libUnrealEditor-Engine.so!CompileGlobalShaderMap(EShaderPlatform, ITargetPlatform const*, bool) [/mnt/horde/++UE5/Sync/Engine/Source/./Runtime/Engine/Private/ShaderCompiler/ShaderCompiler.cpp:7713]
[2026.11.11-07.58.46:763][  0]LogExit: Executing StaticShutdownAfterError
Engine crash handling finished; re-raising signal 11 for the default handler. Good bye.
"""

# The owner's Drive folder on that run.
DRIVE_NAMES = ["Linux_Bridge_5.9.0_2025.0.1_preview-1.zip", "Linux_Fab_5.9.0_0.0.22_preview-1.zip",
               "Linux_Unreal_Engine_5.2.zip", "Linux_Unreal_Engine_5.4.4.zip", "Linux_Unreal_Engine_5.9.zip"]


def test_find_and_pick_versions_from_the_owners_drive(tmp_path):
    for n in DRIVE_NAMES:
        (tmp_path / n).write_bytes(b"x")
    zips = [Path(z).name for z in E.find_zips(tmp_path)]
    assert zips == ["Linux_Unreal_Engine_5.2.zip", "Linux_Unreal_Engine_5.4.4.zip", "Linux_Unreal_Engine_5.9.zip"]
    pick = lambda w: [Path(z).name for z in E.pick_version(E.find_zips(tmp_path), w)]
    assert pick("5.4") == ["Linux_Unreal_Engine_5.4.4.zip"]
    assert pick("5.4.4") == ["Linux_Unreal_Engine_5.4.4.zip"]
    assert pick("5.2") == ["Linux_Unreal_Engine_5.2.zip"]
    assert pick("5.8") == []
    assert pick("newest")[-1] == "Linux_Unreal_Engine_5.9.zip"          # its name lost "preview": newest is 5.9
    assert E.pick_version(["a/Linux_Unreal_Engine_5.8.4.zip", "a/Linux_Unreal_Engine_5.9.0_preview-1.zip"], "newest")[-1].endswith("5.8.4.zip")
    assert E.pick_version(["c/UE_5.2.0_trimmed.tar.zst"], "5.4") == []
    assert E.pick_version(["c/UE_5.4.4_trimmed.tar.zst"], "5.4") == ["c/UE_5.4.4_trimmed.tar.zst"]


def test_sm6_only_from_5_4():
    assert not E.default_sm6("5.2.0") and not E.default_sm6("5.3.2")
    assert E.default_sm6("5.4.4") and E.default_sm6("5.8.4") and E.default_sm6("5.9.0")
    assert E.default_sm6(None)


def test_the_52_crash_is_named_and_retried_with_sm5():
    proof = uerun.rhi_proof(UE52_SM6_CRASH)
    sigs = [c["signature"] for c in proof["crashes"]]
    assert proof["verdict"] == "FAIL" and "shader_format" in sigs
    assert uerun.next_attempt(proof, []) == ("Shader Model 5, no ray tracing", {"sm6": False, "ray_tracing": False})
    assert uerun.next_attempt(proof, ["Shader Model 5, no ray tracing"]) is None


def test_smoke_recovers_from_the_52_crash(tmp_path):
    fake = FakeUnreal([UE52_SM6_CRASH, S.UE_LOG_VULKAN_OK])
    lab, drive = make_lab(tmp_path, fake, rt=True)
    pipeline.get_engine(lab, "zip", drive, want="5.8")
    E.enable_vulkan_rt(lab.engine_dir)
    lab.settings["ray_tracing"] = True
    lab.write_project()
    r = pipeline.smoke(lab)
    assert r["proof"]["verdict"] == "PASS" and r["attempts"][1]["change"] == "Shader Model 5, no ray tracing"
    ini = (lab.project_dir / "Config/DefaultEngine.ini").read_text()
    assert "+TargetedRHIs=SF_VULKAN_SM5" in ini and "r.RayTracing=False" in ini
    assert "-sm5" in fake.calls[1]["cmd"] and "-sm6" in fake.calls[0]["cmd"]
    ddpi = lab.engine_dir / "Engine/Config/VulkanPC/DataDrivenPlatformInfo.ini"     # engine config put back
    assert ddpi.read_text() == Path(str(ddpi) + ".x3due.orig").read_text()


def _zip(path, root, major, minor, patch):
    old = test_ue_engine.BUILD
    test_ue_engine.BUILD = {**old, "MajorVersion": major, "MinorVersion": minor, "PatchVersion": patch}
    try:
        test_ue_engine.fake_zip(path, root=root)
    finally:
        test_ue_engine.BUILD = old


def test_get_engine_switches_versions(tmp_path):
    lab, drive = make_lab(tmp_path, FakeUnreal())
    for f in drive.glob("*.zip"):
        f.unlink()
    _zip(drive / "Linux_Unreal_Engine_5.2.zip", "Linux_Unreal_Engine_5.2/", 5, 2, 0)
    _zip(drive / "Linux_Unreal_Engine_5.4.4.zip", "Linux_Unreal_Engine_5.4.4/", 5, 4, 4)
    assert pipeline.get_engine(lab, "zip", drive, want="5.2")["version"] == "5.2.0"
    assert pipeline.get_engine(lab, "zip", drive, want="5.2")["source"] == "already on disk"
    got = pipeline.get_engine(lab, "zip", drive, want="5.4")                     # replaces the 5.2 on disk
    assert got["version"] == "5.4.4" and got["source"] == "drive zip"
    shutil.rmtree(lab.engine_dir)
    with pytest.raises(RuntimeError, match=r"for 5\.8 .*Linux_Unreal_Engine_5\.4\.4\.zip"):
        pipeline.get_engine(lab, "zip", drive, want="5.8")


def test_alpha_is_a_console_variable_not_a_renderer_setting(tmp_path):
    ini = (UNREAL / "ue_project/Config/DefaultEngine.ini").read_text()
    renderer = ini.split("[/Script/Engine.RendererSettings]")[1].split("\n[")[0]
    assert "PropagateAlpha" not in renderer
    system = ini.split("[SystemSettings]")[1].split("\n[")[0]
    assert "r.PostProcessing.PropagateAlpha=1" in system


def test_crash_stack_reads_the_newest_crash_report(tmp_path):
    lab, _ = make_lab(tmp_path, FakeUnreal())
    d = lab.project_dir / "Saved/Crashes/crashinfo-X3DLab-pid-1"
    d.mkdir(parents=True)
    (d / "CrashContext.runtime-xml").write_text(
        "<FGenericCrashContext><CallStack>libUnrealEditor-TargetPlatform.so!FTargetPlatformManagerModule::ShaderFormatVersion(FName)"
        "\nGetGlobalShaderMapKeyString(FGlobalShaderMapId const&amp;)</CallStack></FGenericCrashContext>")
    st = pipeline.crash_stack(lab)
    assert "ShaderFormatVersion" in st and "const&)" in st


def test_the_001_notebook_holds_no_download_links():
    raw = (UNREAL / "07_Unreal_Fidelity.001.ipynb").read_text()
    assert not re.search(r"X-Amz-|ucs-blob-store|AKIA[0-9A-Z]{8}", raw)
    json.loads(raw)


def test_the_notebook_never_saves_or_prints_the_link():
    src = (UNREAL / "src/07_unreal.py").read_text()
    cell = src.split("#@title 5a")[1].split("# %%")[0]
    assert "getpass(" in cell and "print(_url" not in cell and "del _url" in cell
    assert "ENGINE_VERSION" in src and "want=ENGINE_VERSION" in src
    assert 'sys.path.insert(1, str(LAB_DIR / "src"))' in src
