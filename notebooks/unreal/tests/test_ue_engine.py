import gzip
import hashlib
import http.server
import io
import json
import os
import shutil
import stat
import tarfile
import threading
import zipfile
from pathlib import Path

import pytest

from x3due import engine as E
from x3due import oci

ROOT = "Linux_Unreal_Engine_5.8.4/"
BUILD = {"MajorVersion": 5, "MinorVersion": 8, "PatchVersion": 4, "Changelist": 45123456, "BranchName": "++UE5+Release-5.8"}


def fake_zip(path, root=ROOT, exec_bit=True):
    """A tiny stand-in for Linux_Unreal_Engine_5.8.4.zip: the files the checks need, plus what trimming drops."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        def add(name, data, mode=0o644):
            i = zipfile.ZipInfo(root + name)
            i.external_attr = (stat.S_IFREG | mode) << 16
            z.writestr(i, data)
        add("Engine/Binaries/Linux/UnrealEditor", b"\x7fELF editor", 0o755 if exec_bit else 0o644)
        add("Engine/Binaries/Linux/UnrealEditor-Cmd", b"\x7fELF cmd", 0o755 if exec_bit else 0o644)
        add("Engine/Binaries/Linux/UnrealEditor.debug", b"D" * 50000)
        add("Engine/Binaries/Linux/libUnrealEditor-Core.so", b"so")
        add("Engine/Binaries/Linux/libUnrealEditor-Core.sym", b"S" * 20000)
        add("Engine/Build/Build.version", json.dumps(BUILD).encode())
        add("Engine/Plugins/Experimental/PythonScriptPlugin/PythonScriptPlugin.uplugin", b"{}")
        add("Engine/Plugins/MovieScene/MovieRenderPipeline/MovieRenderPipeline.uplugin", b"{}")
        add("Engine/Config/VulkanPC/DataDrivenPlatformInfo.ini", b"[ShaderPlatform VULKAN_SM6]\nLanguage=Vulkan\nBindlessSupport=Disabled\n")
        add("Engine/Config/Linux/BaseLinuxEngine.ini", b"[/Script/LinuxTargetPlatform.LinuxTargetSettings]\nX=1\n")
        add("Templates/TP_Blank/TP_Blank.uproject", b"T" * 30000)
        add("FeaturePacks/TP_Blank.upack", b"F" * 1000)
        link = zipfile.ZipInfo(root + "Engine/Binaries/Linux/libfoo.so.1")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(link, "libUnrealEditor-Core.so")


def test_zip_plan_and_strategy(tmp_path):
    z = tmp_path / "Linux_Unreal_Engine_5.8.4.zip"
    fake_zip(z)
    p = E.zip_plan(z)
    assert p["ok"] and p["root"] == ROOT and p["version"] == "5.8.4"
    assert p["keep_files"] == p["files"] - 4                    # .debug, .sym, Templates, FeaturePacks
    assert p["keep_bytes"] < p["full_bytes"] - 100000
    plan = {"zip_gb": 39.0, "keep_gb": 40.0}
    assert E.choose_strategy(plan, 200)["mode"] == "copy_then_extract"
    assert E.choose_strategy(plan, 70)["mode"] == "extract_from_drive"
    assert E.choose_strategy(plan, 30)["mode"] == "none"
    assert E.find_zips(tmp_path) == [str(z)]


def test_zip_without_engine(tmp_path):
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("readme.txt", "no")
    assert not E.zip_plan(z)["ok"]


def test_extract_trim_permissions_symlink_rerun(tmp_path):
    z = tmp_path / "Linux_Unreal_Engine_5.8.4.zip"
    fake_zip(z)
    dest = tmp_path / "engine"
    st = E.extract_zip(z, dest, log=lambda *a: None)
    assert (dest / "Engine/Binaries/Linux/UnrealEditor").stat().st_mode & 0o111
    assert not (dest / "Engine/Binaries/Linux/UnrealEditor.debug").exists() and not (dest / "Templates").exists()
    assert os.readlink(dest / "Engine/Binaries/Linux/libfoo.so.1") == "libUnrealEditor-Core.so"
    assert st["symlinks"] == 1
    again = E.extract_zip(z, dest, log=lambda *a: None)
    assert again["files"] == 0 and again["skipped_existing"] > 0
    chk = E.check_engine(dest)
    assert chk["ok"] and chk["version"] == "5.8.4" and chk["changelist"] == 45123456
    assert E.find_plugin(dest, "MovieRenderPipeline")


def test_fix_exec_bits(tmp_path):
    z = tmp_path / "e.zip"
    fake_zip(z, exec_bit=False)
    dest = tmp_path / "engine"
    E.extract_zip(z, dest, log=lambda *a: None)
    cmd = dest / "Engine/Binaries/Linux/UnrealEditor-Cmd"
    assert not cmd.stat().st_mode & 0o111            # a zip made on Windows: no executable bits
    assert E.fix_exec_bits(dest) >= 1
    assert cmd.stat().st_mode & 0o111


def test_vulkan_rt_config_edit_is_idempotent_and_reversible(tmp_path):
    z = tmp_path / "e.zip"
    fake_zip(z)
    dest = tmp_path / "engine"
    E.extract_zip(z, dest, log=lambda *a: None)
    ddpi = dest / "Engine/Config/VulkanPC/DataDrivenPlatformInfo.ini"
    before = ddpi.read_text()
    r1 = E.enable_vulkan_rt(dest)
    assert all(v["changed"] for v in r1.values())
    txt = ddpi.read_text()
    assert "bSupportsPathTracing=true" in txt and "BindlessSupport=RayTracingOnly" in txt and "Language=Vulkan" in txt
    assert txt.count("[ShaderPlatform VULKAN_SM6]") == 1 and "BindlessSupport=Disabled" not in txt
    assert "[SF_VULKAN_SM6]" in (dest / "Engine/Config/Linux/BaseLinuxEngine.ini").read_text()
    assert not any(v["changed"] for v in E.enable_vulkan_rt(dest).values())
    E.enable_vulkan_rt(dest, bindless="AllShaderTypes")
    assert "BindlessSupport=AllShaderTypes" in ddpi.read_text()
    E.disable_vulkan_rt(dest)
    assert ddpi.read_text() == before


def test_archive_round_trip(tmp_path):
    src = tmp_path / "UE_5.8.4"
    (src / "Engine").mkdir(parents=True)
    (src / "Engine/a.txt").write_text("hello")
    arc = tmp_path / ("UE.tar.zst" if E.tool("zstd") else "UE.tar.gz")
    assert E.run_logged(E.archive_cmd(src, arc), lambda *a: None)["rc"] == 0
    out = tmp_path / "back"
    out.mkdir()
    assert E.run_logged(E.unarchive_cmd(arc, out), lambda *a: None)["rc"] == 0
    assert (out / "UE_5.8.4/Engine/a.txt").read_text() == "hello"


# ---------------------------------------------------------------------- the registry client against a fake ghcr
def _tar(members):
    """members: [(name, bytes or None for dir, mode, kind)] -> gzip-compressed tar bytes."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        for name, data, mode, kind in members:
            ti = tarfile.TarInfo(name)
            ti.mode = mode
            if kind == "dir":
                ti.type = tarfile.DIRTYPE
                tf.addfile(ti)
            elif kind == "sym":
                ti.type, ti.linkname = tarfile.SYMTYPE, data
                tf.addfile(ti)
            elif kind == "big":                  # a header that claims a huge file (the UBA file-map), no body read
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
            else:
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
    return gzip.compress(raw.getvalue())


PFX = "home/ue4/UnrealEngine/"
LAYERS = [
    _tar([("home", None, 0o755, "dir"), (PFX + "Engine/Binaries/Linux/UnrealEditor", b"ELF", 0o755, "file"),
          (PFX + "Engine/Binaries/Linux/UnrealEditor-Cmd", b"ELF", 0o755, "file"),
          (PFX + "Engine/Build/Build.version", json.dumps(BUILD).encode(), 0o644, "file"),
          (PFX + "Engine/Plugins/Experimental/PythonScriptPlugin/PythonScriptPlugin.uplugin", b"{}", 0o644, "file"),
          (PFX + "Engine/old.txt", b"old", 0o644, "file"), (PFX + "Engine/Junk/a", b"a", 0o644, "file"),
          (PFX + "Engine/Binaries/Linux/x.debug", b"D" * 100, 0o644, "file"),
          (PFX + "Engine/link", "/home/ue4/UnrealEngine/Engine/Build/Build.version", 0o777, "sym"),
          ("etc/passwd", b"root", 0o644, "file")]),
    _tar([(PFX + "Engine/.wh.old.txt", b"", 0o644, "file"), (PFX + "Engine/Junk/.wh..wh..opq", b"", 0o644, "file"),
          (PFX + "Engine/new.txt", b"new", 0o644, "file")]),
    _tar([("home/ue4/.epic/UnrealBuildAccelerator/uba_filemap/uba_02", b"\0" * 4096, 0o644, "big"),
          (PFX + "Engine/after_uba.txt", b"lost", 0o644, "file")]),
]
DIGESTS = ["sha256:" + hashlib.sha256(b).hexdigest() for b in LAYERS]


class FakeGhcr(http.server.BaseHTTPRequestHandler):
    seen_cdn_auth = []

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path
        if p.startswith("/token"):
            if "Basic" not in (self.headers.get("Authorization") or ""):
                return self._send(401, b"{}")
            return self._send(200, json.dumps({"token": "T0K"}).encode())
        if p.startswith("/cdn/"):
            FakeGhcr.seen_cdn_auth.append(self.headers.get("Authorization"))
            i = DIGESTS.index(p[5:])
            return self._send(200, LAYERS[i], "application/octet-stream")
        if self.headers.get("Authorization") != "Bearer T0K":
            return self._send(401, b"{}")
        if p == "/v2/epicgames/unreal-engine/manifests/dev-slim-5.8.4":
            idx = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
                   "manifests": [{"digest": "sha256:arm", "platform": {"os": "linux", "architecture": "arm64"}},
                                 {"digest": "sha256:amd", "platform": {"os": "linux", "architecture": "amd64"}}]}
            return self._send(200, json.dumps(idx).encode(), "application/vnd.oci.image.index.v1+json")
        if p == "/v2/epicgames/unreal-engine/manifests/sha256:amd":
            m = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                 "layers": [{"digest": d, "size": len(b), "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip"}
                            for d, b in zip(DIGESTS, LAYERS)]}
            return self._send(200, json.dumps(m).encode(), "application/vnd.oci.image.manifest.v1+json")
        if p.startswith("/v2/epicgames/unreal-engine/blobs/"):
            return self._send(307, b"", headers={"Location": "/cdn/" + p.rsplit("/", 1)[1]})
        if "/manifests/" in p:
            return self._send(404, b"{}")
        return self._send(404)


@pytest.fixture
def ghcr():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGhcr)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


def test_registry_pull_without_docker(ghcr, tmp_path):
    reg = oci.Registry(ghcr, "me", "pat", scheme="http")
    m = reg.manifest("epicgames/unreal-engine", "dev-slim-5.8.4")
    assert len(m["layers"]) == 3
    dest = tmp_path / "engine"
    rep = oci.extract_image(reg, "epicgames/unreal-engine", m, dest, max_member_gb=1e-6, log=lambda *a: None)
    assert (dest / "Engine/Binaries/Linux/UnrealEditor").stat().st_mode & 0o111
    assert not (dest / "etc").exists() and not (dest / "Engine/Binaries/Linux/x.debug").exists()
    assert not (dest / "Engine/old.txt").exists() and (dest / "Engine/new.txt").read_text() == "new"     # whiteout
    assert (dest / "Engine/Junk").is_dir() and not (dest / "Engine/Junk/a").exists()                     # opaque dir
    assert os.readlink(dest / "Engine/link") == "Build/Build.version"                                    # absolute link rewritten
    assert rep["aborted_layers"] == [DIGESTS[2]] and not (dest / "Engine/after_uba.txt").exists()
    assert all(a is None for a in FakeGhcr.seen_cdn_auth)                   # the CDN never sees the registry token
    from x3due import engine
    assert engine.check_engine(dest)["ok"]
    again = oci.extract_image(reg, "epicgames/unreal-engine", m, dest, log=lambda *a: None)
    assert all(l.get("resumed") for l in again["per_layer"])


def test_registry_errors(ghcr):
    with pytest.raises(oci.RegistryError, match="read:packages"):
        oci.Registry(ghcr, None, None, scheme="http").manifest("epicgames/unreal-engine", "dev-slim-5.8.4")
    with pytest.raises(oci.RegistryError, match="404"):
        oci.Registry(ghcr, "me", "pat", scheme="http").manifest("epicgames/unreal-engine", "nope")
