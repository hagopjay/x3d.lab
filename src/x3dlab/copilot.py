"""Load the private Copilot Validator Demo build and serve it at /k/<key>/copilot/.

The build ships the upstream X3D Copilot author's icons, award images and provider names, so it is never
part of the public x3d.lab repo. It comes from somewhere only you can read:

    source="drive"   a zip in your Google Drive (default path below)
    source="github"  a release asset in a private repo (needs GITHUB_TOKEN in Colab Secrets)
    source="upload"  pick the zip from your computer
    source="path"    a zip or folder already on this machine

In the page, window.__x3dcopilot exposes {viewer, editor, validator, runPipeline, execute, copilot, tools,
inspector}: try it from the Chrome DevTools console.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

from . import runtime
from .events import bus
from .install import github_token

DEFAULT_DRIVE_PATH = "/content/drive/MyDrive/x3d.lab/Copilot_Validator_Demo.zip"


class CopilotError(RuntimeError):
    pass


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_extract(zip_path: Path, dest: Path) -> int:
    """Unzip, refusing absolute paths, '..' and links. Returns the file count."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    root = dest.resolve()
    n = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename
            if name.startswith(("/", "\\")) or ".." in Path(name).parts or ":" in name:
                raise CopilotError("unsafe path in zip: %r" % name)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise CopilotError("symbolic link in zip: %r" % name)
            target = (dest / name).resolve()
            if root not in target.parents and target != root:
                raise CopilotError("path escapes the folder: %r" % name)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            n += 1
    return n


def _site_root(folder: Path) -> Path:
    """The folder that holds index.html (zips sometimes wrap everything in one top folder)."""
    if (folder / "index.html").is_file():
        return folder
    hits = sorted(folder.glob("*/index.html"))
    if hits:
        return hits[0].parent
    raise CopilotError("no index.html in the Copilot build")


def _from_github(repo: str, tag: str, asset: str, dest: Path) -> Path:
    tok = github_token()
    if not tok:
        raise CopilotError("source='github' needs GITHUB_TOKEN in Colab Secrets (a token that can read %s)" % repo)
    hdr = {"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json", "User-Agent": "x3d.lab"}
    req = urllib.request.Request("https://api.github.com/repos/%s/releases/tags/%s" % (repo, tag), headers=hdr)
    rel = json.load(urllib.request.urlopen(req, timeout=30))
    match = [a for a in rel.get("assets", []) if a.get("name") == asset]
    if not match:
        raise CopilotError("release %s of %s has no asset %r (has: %s)" % (tag, repo, asset,
                           ", ".join(a.get("name", "?") for a in rel.get("assets", []))))
    req = urllib.request.Request(match[0]["url"], headers={**hdr, "Accept": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as out:
        shutil.copyfileobj(r, out)
    return dest


def load(source: str = "drive", *, path: str | None = None, repo: str | None = None, tag: str = "copilot-demo",
         asset: str = "Copilot_Validator_Demo.zip") -> Path:
    """Fetch and unpack the build; returns the folder to serve."""
    private = runtime.sub("private")
    dest = private / "copilot"
    zpath = private / "copilot.zip"
    if source == "drive":
        p = Path(path or DEFAULT_DRIVE_PATH)
        if not p.exists():
            try:
                from google.colab import drive
                drive.mount("/content/drive")
            except Exception as exc:
                raise CopilotError("could not mount Google Drive: %s" % exc)
        if not p.exists():
            raise CopilotError("not found in Drive: %s (put the zip there or set COPILOT_PATH)" % p)
        shutil.copyfile(p, zpath)
    elif source == "github":
        if not repo:
            raise CopilotError("source='github' needs repo='owner/name'")
        _from_github(repo, tag, asset, zpath)
    elif source == "upload":
        try:
            from google.colab import files
        except Exception:
            raise CopilotError("source='upload' only works in Colab; use source='path' elsewhere")
        got = files.upload()
        if not got:
            raise CopilotError("nothing uploaded")
        name, data = next(iter(got.items()))
        zpath.write_bytes(data)
    elif source == "path":
        if not path:
            raise CopilotError("source='path' needs path=")
        p = Path(path)
        if p.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(p, dest)
            root = _site_root(dest)
            bus.info("Copilot build loaded from folder %s" % p, src="copilot")
            return root
        shutil.copyfile(p, zpath)
    else:
        raise CopilotError("source must be drive, github, upload or path")
    digest = _sha256(zpath)
    n = safe_extract(zpath, dest)
    root = _site_root(dest)
    bus.info("Copilot build loaded: %d files, sha256 %s…" % (n, digest[:12]), src="copilot",
             data={"files": n, "sha256": digest, "source": source})
    return root
