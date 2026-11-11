"""Pull a container image's files without Docker: a minimal OCI / Docker registry v2 client (standard library only).

Epic publishes Unreal Engine development images at ghcr.io/epicgames/unreal-engine (tags such as dev-slim-5.8.4;
"dev-slim excludes template projects and debug symbols", Epic's containers overview). Pulling needs a GitHub account
linked to an Epic account and a GitHub personal access token with read:packages. This module streams each layer
(gzip or zstd tar), applies OCI whiteouts, keeps only the files under one path prefix (the engine), skips excluded
paths, and never writes a file bigger than `max_member_gb` (the 5.8.1 dev-slim image shipped a 1 TB UBA file-map
file, forum report Aug 2026, fixed by 5.8.3). It is resumable: finished layers are recorded and skipped on re-run.

    reg = oci.Registry("ghcr.io", user, token)
    m = reg.manifest("epicgames/unreal-engine", "dev-slim-5.8.4")
    oci.extract_image(reg, "epicgames/unreal-engine", m, "/content/ue/5.8.4", keep_prefix="home/ue4/UnrealEngine/")
"""

from __future__ import annotations

import base64
import fnmatch
import gzip
import io
import json
import os
import shutil
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"])
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json")
UBA = "*/.epic/UnrealBuildAccelerator/*"
DEFAULT_EXCLUDE = (UBA, "*.debug", "*.sym", "*.pdb")


class RegistryError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Registry:
    def __init__(self, host="ghcr.io", user=None, token=None, scheme="https", timeout=60):
        self.host, self.user, self.secret, self.scheme, self.timeout = host, user, token, scheme, timeout
        self.bearer = {}
        self.opener = urllib.request.build_opener(_NoRedirect)

    def _url(self, path):
        return "%s://%s%s" % (self.scheme, self.host, path)

    def _token(self, repo):
        if repo in self.bearer:
            return self.bearer[repo]
        q = urllib.parse.urlencode({"scope": "repository:%s:pull" % repo, "service": self.host})
        req = urllib.request.Request(self._url("/token?" + q))
        if self.user and self.secret:
            req.add_header("Authorization", "Basic " + base64.b64encode(("%s:%s" % (self.user, self.secret)).encode()).decode())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                tok = json.loads(r.read()).get("token")
        except urllib.error.HTTPError as e:
            raise RegistryError("token request for %s failed: HTTP %s %s. Is the GitHub token a classic PAT with "
                                "read:packages, and is your GitHub account linked to Epic (and the EpicGames "
                                "organisation invitation accepted)?" % (repo, e.code, e.reason)) from None
        self.bearer[repo] = tok
        return tok

    def _open(self, path, repo, accept=None, stream=False):
        req = urllib.request.Request(self._url(path))
        req.add_header("Authorization", "Bearer %s" % self._token(repo))
        if accept:
            req.add_header("Accept", accept)
        try:
            r = self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                loc = urllib.parse.urljoin(self._url(path), e.headers["Location"])
                # the blob store (a signed CDN URL) must not get our registry token
                return urllib.request.urlopen(urllib.request.Request(loc), timeout=self.timeout)
            if e.code == 429:
                raise RegistryError("ghcr.io rate limit (HTTP 429): wait a few minutes, and make sure the token is set "
                                    "(anonymous pulls are limited much harder)") from None
            if e.code in (401, 403):
                raise RegistryError("HTTP %d for %s: the token cannot read this package (needs read:packages and access "
                                    "to the EpicGames organisation's packages)" % (e.code, path)) from None
            if e.code == 404:
                raise RegistryError("HTTP 404 for %s: no such tag or blob" % path) from None
            raise RegistryError("HTTP %d %s for %s" % (e.code, e.reason, path)) from None
        return r

    def manifest(self, repo, ref, platform=("linux", "amd64")):
        with self._open("/v2/%s/manifests/%s" % (repo, ref), repo, ACCEPT) as r:
            m = json.loads(r.read())
            mt = r.headers.get("Content-Type", m.get("mediaType", ""))
        if m.get("mediaType", mt) in INDEX_TYPES or "manifests" in m:
            pick = next((x for x in m["manifests"] if x.get("platform", {}).get("os") == platform[0]
                         and x.get("platform", {}).get("architecture") == platform[1]), None)
            if pick is None:
                raise RegistryError("no %s/%s image in %s:%s" % (platform + (repo, ref)))
            return self.manifest(repo, pick["digest"], platform)
        return m

    def blob(self, repo, digest):
        return self._open("/v2/%s/blobs/%s" % (repo, digest), repo)


def _decompressor(media_type, raw):
    if media_type.endswith("zstd"):
        try:
            import zstandard
        except ImportError as e:
            raise RegistryError("layer is zstd-compressed: pip install zstandard") from e
        return zstandard.ZstdDecompressor().stream_reader(raw)
    if media_type.endswith("gzip") or media_type.endswith(".tar.gzip") or "gzip" in media_type:
        return gzip.GzipFile(fileobj=raw, mode="rb")
    return raw


def _excluded(path, patterns):
    p = "/" + path.lstrip("/")
    return any(fnmatch.fnmatch(p, pat if pat.startswith(("*", "/")) else "*/" + pat) for pat in patterns)


def _safe_join(root: Path, rel: str) -> Path | None:
    """root/rel without following a symlink at the leaf (a layer may replace a link with a file); None if outside root."""
    base = root.resolve()
    dest = Path(os.path.normpath(os.path.join(str(base), rel)))
    if dest != base and base not in dest.parents:
        return None
    if dest.parent != base and dest.parent.exists() and base not in dest.parent.resolve().parents and dest.parent.resolve() != base:
        return None                                   # a symlinked directory pointing out of the tree
    return dest


def extract_layer(stream, dest, keep_prefix="", exclude=DEFAULT_EXCLUDE, max_member_gb=64.0, progress=None) -> dict:
    """Unpack one layer tar stream into `dest` (only members under keep_prefix, prefix removed)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    st = {"files": 0, "bytes": 0, "skipped": 0, "whiteouts": 0, "excluded": 0, "oversize": [], "aborted": None}
    hardlinks = []
    with tarfile.open(fileobj=stream, mode="r|") as tf:
        for m in tf:
            name = m.name.lstrip("./").lstrip("/")
            base = os.path.basename(name)
            if _excluded(name, exclude):
                st["excluded"] += 1
                if m.isfile() and m.size > max_member_gb * 2**30 and _excluded(name, (UBA,)):
                    st["aborted"] = "layer holds %s (%.0f GB, the UBA file-map); rest of this layer skipped" % (name, m.size / 2**30)
                    break
                continue
            if keep_prefix and not (name + "/").startswith(keep_prefix):
                st["skipped"] += 1
                continue
            rel = name[len(keep_prefix):] if keep_prefix else name
            if base.startswith(".wh."):
                st["whiteouts"] += 1
                parent = _safe_join(dest, os.path.dirname(rel))
                if parent is None:
                    continue
                if base == ".wh..wh..opq":
                    if parent.is_dir():
                        for ch in parent.iterdir():
                            shutil.rmtree(ch) if ch.is_dir() and not ch.is_symlink() else ch.unlink()
                else:
                    victim = parent / base[4:]
                    if victim.is_dir() and not victim.is_symlink():
                        shutil.rmtree(victim)
                    elif victim.exists() or victim.is_symlink():
                        victim.unlink()
                continue
            if not rel:
                continue
            out = _safe_join(dest, rel)
            if out is None:
                st["skipped"] += 1
                continue
            if m.isdir():
                out.mkdir(parents=True, exist_ok=True)
                os.chmod(out, (m.mode & 0o7777) | 0o700)
            elif m.issym():
                out.parent.mkdir(parents=True, exist_ok=True)
                target = m.linkname
                if target.startswith("/" + keep_prefix):
                    target = os.path.relpath(dest / target[len(keep_prefix) + 1:], out.parent)
                if out.exists() or out.is_symlink():
                    out.unlink()
                os.symlink(target, out)
            elif m.islnk():
                hardlinks.append((m.linkname.lstrip("./").lstrip("/"), out))
            elif m.isfile():
                if m.size > max_member_gb * 2**30:
                    st["oversize"].append((rel, m.size))
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                if out.is_symlink() or out.exists():
                    out.unlink()
                src = tf.extractfile(m)
                with open(out, "wb") as fh:
                    shutil.copyfileobj(src, fh, 8 << 20)
                os.chmod(out, m.mode & 0o7777)
                st["files"] += 1
                st["bytes"] += m.size
                if progress and st["files"] % 2000 == 0:
                    progress(st)
    for target, out in hardlinks:
        if keep_prefix and not target.startswith(keep_prefix):
            continue
        src = _safe_join(dest, target[len(keep_prefix):])
        if src is not None and src.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.exists():
                out.unlink()
            try:
                os.link(src, out)
            except OSError:
                shutil.copy2(src, out)
            st["files"] += 1
    return st


def extract_image(reg, repo, manifest, dest, keep_prefix="home/ue4/UnrealEngine/", exclude=DEFAULT_EXCLUDE,
                  max_member_gb=64.0, log=print) -> dict:
    """Every layer in order into `dest`. State in dest/.x3due_layers.json makes a re-run skip finished layers."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    state_path = dest / ".x3due_layers.json"
    done = json.loads(state_path.read_text()) if state_path.exists() else {}
    layers = manifest.get("layers", [])
    total = sum(l.get("size", 0) for l in layers)
    rep = {"layers": len(layers), "compressed_gb": round(total / 2**30, 2), "per_layer": []}
    for i, l in enumerate(layers):
        d = l["digest"]
        if d in done:
            rep["per_layer"].append(dict(done[d], resumed=True))
            continue
        t0 = time.time()
        log("layer %d/%d %s (%.2f GB compressed)" % (i + 1, len(layers), d[:19], l.get("size", 0) / 2**30))
        with reg.blob(repo, d) as raw:
            stream = _decompressor(l.get("mediaType", "gzip"), raw)
            st = extract_layer(stream, dest, keep_prefix, exclude, max_member_gb)
        st.update(digest=d, seconds=round(time.time() - t0, 1), compressed=l.get("size", 0))
        if st["aborted"]:
            log("  " + st["aborted"])
        done[d] = st
        state_path.write_text(json.dumps(done, default=str))
        rep["per_layer"].append(st)
    rep["files"] = sum(x.get("files", 0) for x in rep["per_layer"])
    rep["bytes"] = sum(x.get("bytes", 0) for x in rep["per_layer"])
    rep["aborted_layers"] = [x["digest"] for x in rep["per_layer"] if x.get("aborted")]
    return rep
