"""The engine cache in parallel parts (one Drive read stream ran at ~40-45 MB/s: the single 13.6 GB archive took 5-10 min
to restore), and an unmounted Drive said out loud instead of looking like an empty one."""
import json
import os
from pathlib import Path

import pytest
from test_ue_engine import fake_zip
from test_ue_pipeline import FakeUnreal, make_lab

from x3due import engine as E, pipeline


def _tree(root):
    (root / "Engine/Binaries/Linux").mkdir(parents=True)
    (root / "Engine/Binaries/Linux/UnrealEditor").write_bytes(os.urandom(300_000))
    os.chmod(root / "Engine/Binaries/Linux/UnrealEditor", 0o755)
    (root / "Engine/Content/empty").mkdir(parents=True)
    for i in range(40):
        (root / "Engine/Content" / ("f%02d.uasset" % i)).write_bytes(os.urandom(1000 * (i + 1)))
    os.symlink("UnrealEditor", root / "Engine/Binaries/Linux/link")


@pytest.mark.parametrize("how", ["zstd", "python"])
def test_parts_round_trip_keeps_files_dirs_links_and_modes(tmp_path, how):
    if how == "zstd" and not E.tool("zstd"):
        pytest.skip("no zstd program")
    src = tmp_path / "src"
    _tree(src)
    meta = E.write_parts(src, tmp_path / "parts", n=4, how=how, log=lambda *a: None)
    assert len(meta["parts"]) == 4 and json.loads((tmp_path / "parts" / "parts.json").read_text())["format"] == "x3due-parts-1"
    sizes = [p["bytes"] for p in meta["parts"]]
    assert max(sizes) < 3 * min(sizes)                                          # balanced by size
    got = E.extract_parts(tmp_path / "parts", tmp_path / "dst", how=how, log=lambda *a: None)
    dst = tmp_path / "dst"
    assert got["parts"] == 4
    for f in src.rglob("*"):
        g = dst / f.relative_to(src)
        assert g.exists() or g.is_symlink(), g
        if f.is_file() and not f.is_symlink():
            assert g.read_bytes() == f.read_bytes()
    assert (dst / "Engine/Content/empty").is_dir() and os.readlink(dst / "Engine/Binaries/Linux/link") == "UnrealEditor"
    assert os.stat(dst / "Engine/Binaries/Linux/UnrealEditor").st_mode & 0o111


def test_get_engine_prefers_parts_and_cache_writes_them_once(tmp_path):
    lab, drive = make_lab(tmp_path, FakeUnreal())
    first = pipeline.get_engine(lab, "auto", drive, want="newest")
    assert first["source"] == "drive zip"
    c = pipeline.cache_engine_parts(lab, drive, first["version"], n=3)
    assert c["parts"] == 3 and (drive / "cache" / ("UE_%s_parts" % first["version"]) / "parts.json").exists()
    assert pipeline.cache_engine_parts(lab, drive, first["version"])["skipped"] == "already cached in parts"
    import shutil
    shutil.rmtree(lab.engine_dir)
    for z in drive.glob("*.zip"):
        z.unlink()                                                              # only the parts can bring it back now
    again = pipeline.get_engine(lab, "auto", drive, want="newest")
    assert again["source"] == "drive cache" and again["parts"]["parts"] == 3 and again["ok"]


def test_unmounted_drive_is_named(tmp_path, monkeypatch):
    assert not pipeline.drive_mounted("/content/drive/MyDrive/x3dlab/unreal") or Path("/content/drive/MyDrive").is_dir()
    assert pipeline.drive_mounted(tmp_path / "drive" / "unreal") is False
    (tmp_path / "drive").mkdir()
    assert pipeline.drive_mounted(tmp_path / "drive" / "unreal")
    (tmp_path / "l").mkdir()
    lab, _ = make_lab(tmp_path / "l", FakeUnreal())
    monkeypatch.setattr(pipeline, "drive_mounted", lambda d: False)
    with pytest.raises(RuntimeError, match="not mounted"):
        pipeline.get_engine(lab, "auto", "/content/drive/MyDrive/x3dlab/unreal")
