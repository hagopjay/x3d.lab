"""The switch to Unreal 5.8 (12 Nov 2026, the owner's call): renderer features by engine version (Substrate from 5.7,
MegaLights from 5.8 where hardware ray tracing is on), async compute off by default (a device-lost crash faults Colab's
GPU for the rest of the runtime), Epic's download link straight into Drive without ever printing or saving it, what
Drive holds per version, and notebooks 07 and 08 defaulting to 5.8."""
import io
import json
import re
from pathlib import Path

import pytest
from test_ue_bringup import A100, NV
from test_ue_engine import fake_zip
from test_ue_pipeline import FakeUnreal
from test_ue_project_report import fake_engine

from x3due import bringup, engine as E, pipeline, project

UNREAL = Path(__file__).resolve().parents[1]
SIGNED = ("https://ucs-blob-store.s3-accelerate.amazonaws.com/blobs/aa/bb/cc?X-Amz-Algorithm=AWS4-HMAC-SHA256"
          "&X-Amz-Credential=AKIAEXAMPLEEXAMPLE%2F20261112%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Signature=deadbeef"
          "&response-content-disposition=attachment%3B%20filename%3D%22Linux_Unreal_Engine_5.8.4.zip%22")


def versioned_engine(root, version, plugins=()):
    eng = fake_engine(root, ("PythonScriptPlugin", "EditorScriptingUtilities", "SequencerScripting", "MovieRenderPipeline",
                             "Interchange") + tuple(plugins))
    major, minor, patch = (int(x) for x in version.split("."))
    (eng / "Engine/Build").mkdir(parents=True, exist_ok=True)
    (eng / "Engine/Build/Build.version").write_text(json.dumps({"MajorVersion": major, "MinorVersion": minor, "PatchVersion": patch}))
    return eng


def section(ini, name):
    return ini.split("[%s]" % name)[1].split("\n[")[0]


# ---------------------------------------------------------------------- features by version
def test_features_follow_the_engine_version():
    f54 = project.features_for("5.4.4")
    assert not f54["substrate"] and not f54["megalights"]
    assert f54["renderer"] == {"r.Substrate": "False"}               # 5.4 has no MegaLights setting at all: no line for it
    assert "from 5.7" in f54["why"]["substrate"] and "no megalights" in f54["why"]["megalights"]
    f58 = project.features_for("5.8.4")
    assert f58["substrate"] and not f58["megalights"]                 # software Lumen: MegaLights stays off
    assert "hardware ray tracing" in f58["why"]["megalights"]
    assert f58["renderer"] == {"r.Substrate": "True", "r.MegaLights.EnableForProject": "False"}
    f58rt = project.features_for("5.8.4", ray_tracing=True)
    assert f58rt["substrate"] and f58rt["megalights"]
    assert project.features_for("5.7.1")["substrate"] and not project.features_for("5.7.1", True)["megalights"]
    assert project.features_for("5.9.0", True)["megalights"]


def test_features_can_be_forced_and_unknown_versions_are_left_alone():
    assert project.features_for("5.4.4", substrate="on")["substrate"]          # experimental in 5.4, but asked for
    assert not project.features_for("5.8.4", substrate="off")["substrate"]
    assert project.features_for("5.8.4", megalights="on")["megalights"]         # forced even without hardware RT
    assert not project.features_for("5.4.4", megalights="on")["megalights"]     # 5.4 has none to force
    unknown = project.features_for(None)
    assert unknown["renderer"] == {} and not unknown["substrate"]
    assert project.features_for("5.2.0")["renderer"] == {"r.Substrate": "False"}
    with pytest.raises(ValueError, match="auto, on or off"):
        project.features_for("5.8.4", substrate="maybe")


def test_the_project_carries_the_features_for_its_engine(tmp_path):
    e58 = versioned_engine(tmp_path / "e58", "5.8.4")
    r = project.write(tmp_path / "p58", e58)
    ini = (tmp_path / "p58/Config/DefaultEngine.ini").read_text()
    rs = section(ini, "/Script/Engine.RendererSettings")
    assert "r.Substrate=True" in rs and "r.MegaLights.EnableForProject=False" in rs and "{{" not in ini
    assert "r.Vulkan.AllowAsyncCompute=0" in section(ini, "SystemSettings")
    assert r["version"] == "5.8.4" and r["features"]["substrate"] and r["system_cvars"]["r.Vulkan.AllowAsyncCompute"] == 0
    rt = project.write(tmp_path / "p58rt", e58, ray_tracing=True)
    assert rt["features"]["megalights"] and "r.MegaLights.EnableForProject=True" in (tmp_path / "p58rt/Config/DefaultEngine.ini").read_text()
    nosm6 = project.write(tmp_path / "p58sm5", e58, ray_tracing=True, sm6=False)
    assert not nosm6["features"]["megalights"]                                   # MegaLights needs SM6
    e54 = versioned_engine(tmp_path / "e54", "5.4.4")
    project.write(tmp_path / "p54", e54)
    ini54 = (tmp_path / "p54/Config/DefaultEngine.ini").read_text()
    assert "r.Substrate=False" in ini54 and not re.search(r"^r\.MegaLights", ini54, re.M)
    on = project.write(tmp_path / "p54on", e54, features={"substrate": "on", "async_compute": True})
    ini_on = (tmp_path / "p54on/Config/DefaultEngine.ini").read_text()
    assert "r.Substrate=True" in ini_on and "AllowAsyncCompute" not in ini_on and on["features"]["why"]["substrate"] == "forced on"


def test_an_explicit_cvar_wins_over_the_default(tmp_path):
    e58 = versioned_engine(tmp_path / "e", "5.8.4")
    project.write(tmp_path / "p", e58, extra_cvars={"r.Vulkan.AllowAsyncCompute": 1, "r.Streaming.PoolSize": 9000})
    sysset = section((tmp_path / "p/Config/DefaultEngine.ini").read_text(), "SystemSettings")
    assert "r.Vulkan.AllowAsyncCompute=1" in sysset and "r.Vulkan.AllowAsyncCompute=0" not in sysset
    assert "r.Streaming.PoolSize=9000" in sysset


def test_a_retry_that_turns_ray_tracing_off_also_turns_megalights_off(tmp_path):
    lab_dir = versioned_engine(tmp_path / "e", "5.8.4")
    lab = pipeline.Lab(work=tmp_path / "w", engine_dir=lab_dir, hw={"cpus": 12}, plan={})
    lab.settings.update(ray_tracing=True, engine_version="5.8.4", features={"substrate": "auto", "megalights": "auto"})
    assert lab.write_project()["features"]["megalights"]
    lab.settings["ray_tracing"] = False                                          # what the bindless_rt retry does
    p = lab.write_project()
    assert not p["features"]["megalights"] and p["features"]["substrate"]


def test_epics_mcp_plugin_is_recorded_not_relied_on(tmp_path):
    e58 = versioned_engine(tmp_path / "e", "5.8.4", ("ModelContextProtocol", "ToolsetRegistry", "MCPToolsets", "NotAnMCPlugin"))
    r = project.write(tmp_path / "p", e58)
    assert r["mcp_plugins"] == ["MCPToolsets", "ModelContextProtocol", "ToolsetRegistry"]
    names = {p["Name"] for p in json.loads((tmp_path / "p/X3DLab.uproject").read_text())["Plugins"]}
    assert not names & set(r["mcp_plugins"])                                     # the .uproject leaves them alone
    assert project.mcp_plugins(None) == [] and project.mcp_plugins(tmp_path / "nowhere") == []


# ---------------------------------------------------------------------- Epic's link into Drive
class FakeResponse(io.BytesIO):
    def __init__(self, data, headers):
        super().__init__(data)
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_link_filename_reads_the_presigned_name_and_refuses_paths():
    assert E.link_filename(SIGNED) == "Linux_Unreal_Engine_5.8.4.zip"
    assert E.link_filename("https://x.example/a/Linux_Unreal_Engine_5.8.4.zip?sig=1") == "Linux_Unreal_Engine_5.8.4.zip"
    assert E.link_filename("https://x.example/blob?sig=1", 'attachment; filename="Linux_Unreal_Engine_5.8.3.zip"') == \
        "Linux_Unreal_Engine_5.8.3.zip"
    assert E.link_filename("https://x.example/blob?response-content-disposition=attachment%3B%20filename%3D%22..%2F..%2Fetc%2Fpasswd%22") == "passwd"
    assert E.link_filename("https://x.example/blob?response-content-disposition=attachment%3B%20filename%3D%22%2E%2E%22") is None
    assert E.link_filename("https://x.example/") is None


def test_download_link_never_prints_the_link(tmp_path):
    data = b"PK" + b"z" * 5000
    lines = []
    got = E.download_link(SIGNED, tmp_path, log=lines.append, chunk=700, every=0.0,
                          opener=lambda u: FakeResponse(data, {"Content-Length": str(len(data))}))
    assert got["name"] == "Linux_Unreal_Engine_5.8.4.zip" and got["version"] == "5.8.4" and got["bytes"] == len(data)
    assert (tmp_path / got["name"]).read_bytes() == data and not list(tmp_path.glob("*.part"))
    text = json.dumps(got) + "\n".join(lines)
    assert not re.search(r"X-Amz|AKIA|deadbeef|ucs-blob|amazonaws", text)
    assert any("GB" in l for l in lines)                                        # progress lines appear


def test_a_short_download_raises_and_keeps_the_part(tmp_path):
    with pytest.raises(RuntimeError, match="incomplete: 100 of 900 bytes") as ei:
        E.download_link(SIGNED, tmp_path, log=lambda *a: None, opener=lambda u: FakeResponse(b"x" * 100, {"Content-Length": "900"}))
    assert "X-Amz" not in str(ei.value)
    assert (tmp_path / "Linux_Unreal_Engine_5.8.4.zip.part").exists() and not (tmp_path / "Linux_Unreal_Engine_5.8.4.zip").exists()


def test_an_expired_link_says_so_without_echoing_it(tmp_path):
    import urllib.error

    def expired(u):
        raise urllib.error.HTTPError(u, 403, "Forbidden", {}, None)
    with pytest.raises(RuntimeError) as ei:
        E.download_link(SIGNED, tmp_path, log=lambda *a: None, opener=expired)
    msg = str(ei.value)
    assert "HTTPError 403" in msg and "fresh one" in msg and "X-Amz" not in msg and ei.value.__cause__ is None
    assert ei.value.__suppress_context__


def test_a_link_for_another_file_is_flagged(tmp_path):
    lines = []
    url = SIGNED.replace("Linux_Unreal_Engine_5.8.4.zip", "Linux_Fab_5.9.0_0.0.22_preview-1.zip")
    got = E.download_link(url, tmp_path, log=lines.append, opener=lambda u: FakeResponse(b"x", {}))
    assert got["name"] == "Linux_Fab_5.9.0_0.0.22_preview-1.zip" and any("not Linux_Unreal_Engine" in l for l in lines)


# ---------------------------------------------------------------------- what Drive holds
def test_drive_engines_lists_parts_archives_and_zips_by_version(tmp_path):
    d = tmp_path / "unreal"
    (d / "cache/UE_5.4.4_parts").mkdir(parents=True)
    (d / "cache/UE_5.4.4_parts/parts.json").write_text("{}")
    (d / "cache/UE_5.8.4_parts").mkdir(parents=True)                           # no parts.json yet: an unfinished split
    (d / "cache/UE_5.4.4_trimmed.tar.zst").write_bytes(b"z")
    (d / "cache/UE_5.4.4_trimmed.json").write_text("{}")
    for n in ("Linux_Unreal_Engine_5.4.4.zip", "Linux_Unreal_Engine_5.8.4.zip", "Linux_Unreal_Engine_5.9.zip"):
        (d / n).write_bytes(b"x")
    every = pipeline.drive_engines(d, settle=False)
    assert [(f["kind"], f["version"]) for f in every] == [("parts", "5.4.4"), ("archive", "5.4.4"), ("zip", "5.4.4"),
                                                          ("zip", "5.8.4"), ("zip", "5.9")]
    assert [(f["kind"], f["version"]) for f in pipeline.drive_engines(d, "5.8", settle=False)] == [("zip", "5.8.4")]
    assert [f["kind"] for f in pipeline.drive_engines(d, "5.4", settle=False)] == ["parts", "archive", "zip"]
    assert pipeline.drive_engines(d, "5.7", settle=False) == [] and pipeline.drive_engines(tmp_path / "none", "5.8") == []


# ---------------------------------------------------------------------- bring-up and notebooks
def test_bringup_on_58_compiles_substrate_once_and_reports_it(tmp_path):
    drive = tmp_path / "drive"
    drive.mkdir()
    fake_zip(drive / "Linux_Unreal_Engine_5.8.4.zip")
    lines = []
    up = bringup.up(tmp_path / "work", drive, "auto", "5.8", verbose=False, visual=False, gentle_mode=False, log=lines.append,
                    detect=A100, enable=lambda: {"driver": "580.82.07"}, probe=lambda user: {"devices": [NV], "nvidia_device": NV},
                    runner=FakeUnreal(), fast_cache=False)
    assert up["ok"], up["why"]
    assert up["features"]["substrate"] and not up["features"]["megalights"]
    ini = (up["lab"].project_dir / "Config/DefaultEngine.ini").read_text()
    assert "r.Substrate=True" in ini and "r.Vulkan.AllowAsyncCompute=0" in ini
    assert any(l.startswith("features : Substrate on") and "async compute off" in l for l in lines)
    off = bringup.up(tmp_path / "work2", drive, "auto", "5.8", verbose=False, visual=False, gentle_mode=False, log=lambda *a: None,
                     detect=A100, enable=lambda: {}, probe=lambda user: {"devices": [NV], "nvidia_device": NV},
                     runner=FakeUnreal(), fast_cache=False, substrate="off", async_compute=True)
    ini2 = (off["lab"].project_dir / "Config/DefaultEngine.ini").read_text()
    assert "r.Substrate=False" in ini2 and "AllowAsyncCompute" not in ini2


def test_bringup_without_58_in_drive_says_how_to_get_it(tmp_path):
    drive = tmp_path / "drive"
    drive.mkdir()
    fake_zip(drive / "Linux_Unreal_Engine_5.4.4.zip")
    up = bringup.up(tmp_path / "w", drive, "auto", "5.8", verbose=False, visual=False, gentle_mode=False, log=lambda *a: None,
                    detect=A100, enable=lambda: {}, probe=lambda user: {"devices": [NV], "nvidia_device": NV}, runner=FakeUnreal())
    assert not up["ok"] and up["why"].startswith("no engine") and "5.8" in up["why"]


def cell(src, title):
    return src.split("#@title %s" % title)[1].split("# %%")[0]


def test_notebook_08_defaults_to_58_and_never_keeps_the_link():
    src = (UNREAL / "src/08_gobbler.py").read_text()
    assert re.search(r'^ENGINE_VERSION = "5\.8"', src, re.M)
    assert 'SUBSTRATE = "auto"' in src and 'MEGALIGHTS = "auto"' in src
    c3a = cell(src, "3a")
    assert "getpass(" in c3a and "del _url" in c3a and "engine.download_link(_url" in c3a
    assert not re.search(r"print\([^)]*_url", c3a) and "_url)" not in c3a.replace("download_link(_url", "")
    assert "substrate=SUBSTRATE, megalights=MEGALIGHTS" in cell(src, "3 · Bring Unreal up")
    assert src.index("#@title 3a") < src.index("#@title 3 · Bring Unreal up")


def test_notebook_07_defaults_to_58_and_sets_features_before_the_first_start():
    src = (UNREAL / "src/07_unreal.py").read_text()
    assert re.search(r'^ENGINE_VERSION = "5\.8"', src, re.M)
    c5a = cell(src, "5a")
    assert "engine.download_link(_url" in c5a and "del _url" in c5a and not re.search(r"print\([^)]*_url", c5a)
    c6 = cell(src, "6 · Cold and warm start")
    assert c6.index('LAB.settings["features"]') < c6.index("LAB.write_project()")


def test_built_notebooks_match_their_sources():
    """tools/build_notebooks.py --check covers this in CI; here the 5.8 strings must have reached the .ipynb files."""
    for nb in ("07_Unreal_Fidelity.ipynb", "08_Gobbler_Cinematic.ipynb"):
        raw = (UNREAL / nb).read_text()
        assert 'ENGINE_VERSION = \\"5.8\\"' in raw, nb
        assert not re.search(r"X-Amz-|ucs-blob-store|AKIA[0-9A-Z]{8}", raw), nb


def test_download_link_through_urllib_from_a_local_server(tmp_path):
    """The real urllib path, against a server on 127.0.0.1 that ignores the query (as S3 does for the signature here)."""
    import http.server
    import threading
    data = b"PK\x03\x04" + bytes(range(256)) * 300

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = "http://127.0.0.1:%d/blobs/ab/cd?X-Amz-Signature=feedface&response-content-disposition=%s" % (
            srv.server_address[1], "attachment%3B%20filename%3D%22Linux_Unreal_Engine_5.8.4.zip%22")
        lines = []
        got = E.download_link(url, tmp_path / "drive", log=lines.append, chunk=4096)
    finally:
        srv.shutdown()
    assert (tmp_path / "drive/Linux_Unreal_Engine_5.8.4.zip").read_bytes() == data and got["bytes"] == len(data)
    assert "feedface" not in "\n".join(lines) and "127.0.0.1:" not in "\n".join(lines)


def test_a_first_58_run_writes_only_the_parallel_parts_to_drive(tmp_path):
    """5.8's zip is 39 GB: the single trimmed archive and the parts together would need ~20 GB more Drive than the parts
    alone, and the parts are what the next runtime restores."""
    drive = tmp_path / "drive"
    drive.mkdir()
    fake_zip(drive / "Linux_Unreal_Engine_5.8.4.zip")
    up = bringup.up(tmp_path / "w", drive, "auto", "5.8", verbose=False, visual=False, gentle_mode=False, log=lambda *a: None,
                    detect=A100, enable=lambda: {}, probe=lambda user: {"devices": [NV], "nvidia_device": NV}, runner=FakeUnreal())
    assert up["ok"], up["why"]
    assert "engine_cache" not in up["steps"] and "engine_parts" in up["steps"]
    assert not list((drive / "cache").glob("UE_*_trimmed.tar*")) and list((drive / "cache").glob("UE_5.8.4_parts/parts.json"))
    nxt = bringup.up(tmp_path / "w2", drive, "auto", "5.8", verbose=False, visual=False, gentle_mode=False, log=lambda *a: None,
                     detect=A100, enable=lambda: {}, probe=lambda user: {"devices": [NV], "nvidia_device": NV}, runner=FakeUnreal())
    assert nxt["ok"] and nxt["engine"]["source"] == "drive cache" and nxt["engine"].get("parts")        # the next runtime
    assert "engine_parts" not in nxt["steps"] and "engine_cache" not in nxt["steps"]
