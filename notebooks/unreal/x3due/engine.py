"""Get Unreal Engine onto the Colab SSD, from what *you* downloaded with your Epic account, and cache it in your Drive.

Unreal binaries are not in this repository and are not fetched from it (Epic's EULA: each user downloads the engine
with their own Epic account). Three sources, tried in this order:

  cache   a trimmed engine archive this notebook wrote to your Drive on an earlier run (fastest)
  zip     Linux_Unreal_Engine_5.x.y.zip that you placed in your Drive (from https://www.unrealengine.com/linux)
  ghcr    Epic's container image ghcr.io/epicgames/unreal-engine:dev-slim-5.x.y, unpacked without Docker (oci.py)

Trimming drops debug symbols (*.debug, *.sym), template projects, feature packs and samples: the same things Epic's
own dev-slim image leaves out. Nothing else is changed except, when the GPU can ray trace, the two engine config
files Epic's hardware ray tracing page says to edit for Vulkan (see enable_vulkan_rt; the originals are kept).
"""

from __future__ import annotations

import concurrent.futures
import fnmatch
import glob
import json
import os
import re
import shutil
import stat
import subprocess
import time
import zipfile
from pathlib import Path

TRIM_SAFE = ("*.debug", "*.sym", "*.pdb", "Templates/*", "FeaturePacks/*", "Samples/*", "*/.epic/UnrealBuildAccelerator/*")
TRIM_AGGRESSIVE = TRIM_SAFE + ("Engine/Documentation/*", "Engine/Extras/*", "Engine/Source/*")   # unverified: editor may want some
EDITOR_REL = "Engine/Binaries/Linux/UnrealEditor"


# ---------------------------------------------------------------------- what is where
def find_engine_root(names: list[str]) -> str | None:
    """The archive prefix that holds Engine/Binaries/Linux/UnrealEditor ('' or 'Linux_Unreal_Engine_5.8.4/')."""
    for n in names:
        n = n.replace("\\", "/")
        if n.endswith(EDITOR_REL):
            return n[: -len(EDITOR_REL)]
    return None


def build_version(engine_dir) -> dict:
    """Engine/Build/Build.version -> {"version": "5.8.4", "changelist": ..., "branch": ...}."""
    p = Path(engine_dir) / "Engine/Build/Build.version"
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return {"version": "%s.%s.%s" % (d.get("MajorVersion"), d.get("MinorVersion"), d.get("PatchVersion")),
            "changelist": d.get("Changelist"), "compatible_changelist": d.get("CompatibleChangelist"),
            "branch": d.get("BranchName")}


def version_from_name(name: str) -> str | None:
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", os.path.basename(name))
    return m.group(1) if m else None


def vkey(v) -> tuple:
    """'5.4.4' -> (5, 4, 4); None -> ()."""
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def is_preview(name: str) -> bool:
    return bool(re.search(r"preview|[-_.]p\d|early.?access", os.path.basename(name), re.I))


def pick_version(paths, want="5.4") -> list[str]:
    """Paths whose version (from the file name) matches `want`, oldest first.
    want: "5.4" (any 5.4.x), "5.4.4" (exact), "newest" (newest that is not a preview, else the newest preview)."""
    paths = sorted(paths, key=lambda f: vkey(version_from_name(f)))
    if not want or want == "newest":
        stable = [f for f in paths if not is_preview(f)]
        return stable or paths
    w = vkey(want)
    return [f for f in paths if vkey(version_from_name(f))[:len(w)] == w]


def default_sm6(version) -> bool:
    """Ask for Vulkan Shader Model 6 by default only on 5.4 and newer. UE 5.2 on Linux has no SF_VULKAN_SM6 shader
    format and segfaults in ShaderFormatVersion when a project targets it (seen on Colab, 11 Nov 2026). The 5.4 cut-off
    is a guess; the retry ladder (uerun.RETRY "shader_format") falls back to SM5 if a newer engine lacks it too."""
    return vkey(version)[:2] >= (5, 4) if version else True


def find_zips(drive_dir) -> list[str]:
    pats = ["Linux_Unreal_Engine_*.zip", "Linux_Unreal_Engine*.zip", "*Unreal*Engine*.zip"]
    hits = []
    for p in pats:
        hits += glob.glob(os.path.join(str(drive_dir), p))
    return sorted(set(hits), key=lambda f: [int(x) for x in re.findall(r"\d+", os.path.basename(f))] or [0])


def excluded(rel: str, patterns) -> bool:
    rel = rel.replace("\\", "/").lstrip("/")
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch("/" + rel, "*/" + p) for p in patterns)


def zip_plan(zip_path, exclude=TRIM_SAFE) -> dict:
    """Read only the zip's index: the engine prefix, file count, sizes with and without trimming, the version."""
    with zipfile.ZipFile(zip_path) as z:
        infos = z.infolist()
    names = [i.filename for i in infos]
    root = find_engine_root(names)
    if root is None:
        return {"ok": False, "why": "no %s in %s: is this the Linux engine zip?" % (EDITOR_REL, os.path.basename(zip_path))}
    keep = [i for i in infos if i.filename.startswith(root) and not excluded(i.filename[len(root):], exclude)]
    return {"ok": True, "root": root, "files": len(infos), "keep_files": len(keep),
            "zip_gb": round(os.path.getsize(zip_path) / 2**30, 2),
            "full_gb": round(sum(i.file_size for i in infos) / 2**30, 2),
            "keep_gb": round(sum(i.file_size for i in keep) / 2**30, 2),
            "full_bytes": sum(i.file_size for i in infos), "keep_bytes": sum(i.file_size for i in keep),
            "version": version_from_name(zip_path)}


def choose_strategy(plan: dict, free_gb: float, margin_gb: float = 15.0) -> dict:
    """Copy the zip to the local SSD first (fast random reads) when there is room for zip + engine; otherwise
    extract straight from the Drive mount (slower, needs only the engine's room)."""
    need_copy = plan["zip_gb"] + plan["keep_gb"] + margin_gb
    need_direct = plan["keep_gb"] + margin_gb
    if free_gb >= need_copy:
        return {"mode": "copy_then_extract", "need_gb": round(need_copy, 1), "free_gb": free_gb}
    if free_gb >= need_direct:
        return {"mode": "extract_from_drive", "need_gb": round(need_direct, 1), "free_gb": free_gb,
                "why": "not enough local disk to hold the zip as well (%.0f GB free, %.0f GB needed)" % (free_gb, need_copy)}
    return {"mode": "none", "need_gb": round(need_direct, 1), "free_gb": free_gb,
            "why": "not enough local disk: %.0f GB free, the trimmed engine needs %.0f GB" % (free_gb, need_direct)}


def copy_with_progress(src, dst, chunk=64 << 20, log=print):
    total = os.path.getsize(src)
    done, t0, last = 0, time.time(), 0
    with open(src, "rb") as a, open(dst, "wb") as b:
        while True:
            buf = a.read(chunk)
            if not buf:
                break
            b.write(buf)
            done += len(buf)
            if time.time() - last > 20:
                last = time.time()
                log("  copied %.1f / %.1f GB (%.0f MB/s)" % (done / 2**30, total / 2**30, done / 2**20 / max(1e-3, time.time() - t0)))
    return {"gb": round(total / 2**30, 2), "seconds": round(time.time() - t0, 1)}


def extract_zip(zip_path, dest, exclude=TRIM_SAFE, threads=None, log=print) -> dict:
    """Extract the engine (prefix removed) with Unix permissions and symlinks, in parallel threads (zlib releases
    the GIL). Safe to re-run: files already present with the right size are skipped."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    plan = zip_plan(zip_path, exclude)
    if not plan["ok"]:
        raise RuntimeError(plan["why"])
    root = plan["root"]
    with zipfile.ZipFile(zip_path) as z:
        infos = [i for i in z.infolist() if i.filename.startswith(root) and not excluded(i.filename[len(root):], exclude)]
    files = [i for i in infos if not i.is_dir()]
    dest_abs = dest.resolve()
    t0 = time.time()
    stats = {"files": 0, "skipped_existing": 0, "symlinks": 0, "bytes": 0}

    def one(batch):
        out = {"files": 0, "skipped_existing": 0, "symlinks": 0, "bytes": 0}
        with zipfile.ZipFile(zip_path) as z:
            for i in batch:
                rel = i.filename[len(root):]
                target = Path(os.path.normpath(os.path.join(str(dest_abs), rel)))   # the leaf is not resolved:
                if dest_abs not in target.parents:                                  # it may be a symlink we made
                    continue                                  # path traversal guard
                mode = (i.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    link = z.read(i).decode("utf-8")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.is_symlink() or target.exists():
                        target.unlink()
                    os.symlink(link, target)
                    out["symlinks"] += 1
                    continue
                if target.is_symlink():
                    target.unlink()
                elif target.exists() and target.stat().st_size == i.file_size:
                    out["skipped_existing"] += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(i) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 8 << 20)
                perm = mode & 0o7777
                os.chmod(target, perm if perm else 0o644)
                out["files"] += 1
                out["bytes"] += i.file_size
        return out

    threads = threads or min(16, (os.cpu_count() or 2) * 2)
    batches = [files[k::threads] for k in range(threads)]
    with concurrent.futures.ThreadPoolExecutor(threads) as ex:
        for r in ex.map(one, batches):
            for k in stats:
                stats[k] += r[k]
    editor = dest / EDITOR_REL
    if editor.exists():
        os.chmod(editor, os.stat(editor).st_mode | 0o111)
    stats.update(seconds=round(time.time() - t0, 1), gb=round(stats["bytes"] / 2**30, 2), excluded=plan["files"] - plan["keep_files"])
    log("extracted %d files (%.1f GB) in %.0f s" % (stats["files"], stats["gb"], stats["seconds"]))
    return stats


def fix_exec_bits(engine_dir):
    """Zips written on Windows lose the executable bit: set it on Linux binaries and scripts that need it."""
    n = 0
    e = Path(engine_dir) / "Engine"
    for pat in ("Binaries/Linux/*", "Binaries/ThirdParty/**/Linux/**/*", "Plugins/**/Binaries/Linux/*",
                "Build/BatchFiles/Linux/*.sh", "Build/BatchFiles/*.sh"):
        for p in e.glob(pat):
            if p.is_file() and not p.is_symlink() and (p.suffix in ("", ".sh") or ".so" in p.name):
                m = p.stat().st_mode
                if not m & 0o111:
                    os.chmod(p, m | 0o755)
                    n += 1
    return n


def check_engine(engine_dir) -> dict:
    e = Path(engine_dir)
    need = [EDITOR_REL, "Engine/Binaries/Linux/UnrealEditor-Cmd", "Engine/Build/Build.version",
            "Engine/Plugins/Experimental/PythonScriptPlugin" if (e / "Engine/Plugins/Experimental/PythonScriptPlugin").exists()
            else "Engine/Plugins/Editor/PythonScriptPlugin" if (e / "Engine/Plugins/Editor/PythonScriptPlugin").exists()
            else "Engine/Plugins/**/PythonScriptPlugin.uplugin"]
    missing = []
    for rel in need:
        if "*" in rel:
            if not list(e.glob(rel)):
                missing.append(rel)
        elif not (e / rel).exists():
            missing.append(rel)
    return {"ok": not missing, "missing": missing, **build_version(e)}


def find_plugin(engine_dir, name) -> str | None:
    hits = list(Path(engine_dir, "Engine", "Plugins").glob("**/%s.uplugin" % name))
    return str(hits[0]) if hits else None


# ---------------------------------------------------------------------- Drive cache
def tool(name):
    return shutil.which(name)


def archive_cmd(engine_dir, archive_path) -> list:
    """tar + zstd (multi-threaded) of the trimmed engine; gzip when zstd is missing."""
    parent, base = os.path.dirname(str(engine_dir).rstrip("/")), os.path.basename(str(engine_dir).rstrip("/"))
    if tool("zstd"):
        return ["tar", "--use-compress-program", "zstd -T0 -3", "-cf", str(archive_path), "-C", parent, base]
    return ["tar", "-czf", str(archive_path), "-C", parent, base]


def unarchive_cmd(archive_path, dest_parent) -> list:
    if str(archive_path).endswith(".zst"):
        return ["tar", "--use-compress-program", "zstd -d -T0", "-xf", str(archive_path), "-C", str(dest_parent)]
    return ["tar", "-xzf", str(archive_path), "-C", str(dest_parent)]


# ---------------------------------------------------------------------- zstd that always works
def ensure_zstd(log=print, run=subprocess.run) -> str:
    """'zstd' when the zstd program is (or could be made) available, else 'python' (the zstandard package, a dependency of
    x3dlab), else 'none'. 11 Nov 2026, Colab T4: `apt-get install zstd` failed silently, the cache restore died in a
    second and step 5 fell back to an 18-minute zip extract. Now: install, check, update the package lists and retry,
    then fall back to Python. Says what happened."""
    if tool("zstd"):
        return "zstd"
    for cmd in (["apt-get", "install", "-y", "-qq", "zstd"], ["apt-get", "update", "-qq"], ["apt-get", "install", "-y", "-qq", "zstd"]):
        try:
            r = run(cmd, capture_output=True, text=True, timeout=600)
            if cmd[1] == "install" and r.returncode and r.stderr:
                log("  %s: %s" % (" ".join(cmd[:3]), r.stderr.strip().splitlines()[-1][:160]))
        except Exception as exc:
            log("  %s: %s" % (" ".join(cmd[:3]), exc))
        if tool("zstd"):
            log("zstd installed")
            return "zstd"
    try:
        import zstandard  # noqa: F401
        log("the zstd program is not available here: using Python's zstandard instead (a bit slower, same archives)")
        return "python"
    except ImportError:
        return "none"


def _pipe(producer: list, consumer: list, cwd=None) -> int:
    """producer | consumer, both as argv lists; returns the first non-zero return code."""
    a = subprocess.Popen(producer, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    b = subprocess.Popen(consumer, stdin=a.stdout, stderr=subprocess.PIPE, cwd=cwd)
    a.stdout.close()
    eb = b.communicate()[1]
    ea = a.communicate()[1]
    if a.returncode or b.returncode:
        raise RuntimeError("%s | %s failed: %s %s" % (producer[0], consumer[0], (ea or b"")[-300:].decode(errors="replace"),
                                                      (eb or b"")[-300:].decode(errors="replace")))
    return 0


def zstd_into_tar(src, tar_args: list, how: str) -> None:
    """Decompress `src` (a .tar.zst, read straight from Drive) into `tar -x -f - <tar_args>`."""
    if how == "zstd":
        _pipe(["zstd", "-d", "-c", "-q", str(src)], ["tar", "-x", "-f", "-"] + tar_args)
        return
    import zstandard
    p = subprocess.Popen(["tar", "-x", "-f", "-"] + tar_args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with open(src, "rb") as f, zstandard.ZstdDecompressor().stream_reader(f, read_size=8 << 20, read_across_frames=True) as r:
            shutil.copyfileobj(r, p.stdin, 8 << 20)
    finally:
        p.stdin.close()
    err = p.stderr.read()
    if p.wait():
        raise RuntimeError("tar -x failed: %s" % err[-300:].decode(errors="replace"))


def tar_into_zstd(tar_args: list, out, how: str, level=3) -> None:
    """`tar -c -f - <tar_args>` compressed into `out`."""
    if how == "zstd":
        _pipe(["tar", "-c", "-f", "-"] + tar_args, ["zstd", "-T0", "-%d" % level, "-q", "-f", "-o", str(out)])
        return
    import zstandard
    p = subprocess.Popen(["tar", "-c", "-f", "-"] + tar_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with open(out, "wb") as f, zstandard.ZstdCompressor(level=level, threads=-1).stream_writer(f, closefd=False) as w:
        shutil.copyfileobj(p.stdout, w, 8 << 20)
    err = p.stderr.read()
    if p.wait():
        raise RuntimeError("tar -c failed: %s" % err[-300:].decode(errors="replace"))


def extract_archive(arc, dest_parent, how=None, log=print) -> dict:
    """One .tar.zst / .tar.gz into dest_parent, whatever zstd tool there is."""
    t0 = time.time()
    Path(dest_parent).mkdir(parents=True, exist_ok=True)
    if str(arc).endswith(".zst"):
        zstd_into_tar(arc, ["-C", str(dest_parent)], how or ensure_zstd(log))
    else:
        r = subprocess.run(["tar", "-xzf", str(arc), "-C", str(dest_parent)], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("tar -xzf failed: %s" % r.stderr[-300:])
    return {"rc": 0, "seconds": round(time.time() - t0, 1)}


# ---------------------------------------------------------------------- the parallel Drive cache
PARTS = 8


def plan_parts(root, n=PARTS) -> list[list[str]]:
    """Every entry under `root` (relative paths; directories and symlinks too) in `n` lists of about equal size. The
    biggest files go first, each to the lightest list. Directories go to list 0 so empty ones survive."""
    root = Path(root)
    files, dirs = [], []
    for dp, dns, fns in os.walk(root):
        rel = os.path.relpath(dp, root)
        if rel != ".":
            dirs.append(rel)
        for d in dns:
            full = os.path.join(dp, d)
            if os.path.islink(full):
                files.append((os.path.relpath(full, root), 0))
        for f in fns:
            full = os.path.join(dp, f)
            try:
                size = os.lstat(full).st_size
            except OSError:
                continue
            files.append((os.path.relpath(full, root), size))
    bins = [[] for _ in range(max(1, n))]
    load = [0] * len(bins)
    for rel, size in sorted(files, key=lambda x: -x[1]):
        i = load.index(min(load))
        bins[i].append(rel)
        load[i] += size
    bins[0] = sorted(dirs) + bins[0]
    return [b for b in bins if b]


def write_parts(root, out_dir, n=PARTS, how=None, log=print, level=3) -> dict:
    """The tree at `root` as `n` part-XX.tar.zst in `out_dir` plus parts.json. Paths are relative to `root`."""
    how = how or ensure_zstd(log)
    if how == "none":
        raise RuntimeError("no zstd program and no zstandard package")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    parts = []
    for i, entries in enumerate(plan_parts(root, n)):
        lst = out_dir / ("part-%02d.list" % i)
        lst.write_text("\n".join(entries) + "\n")
        arc = out_dir / ("part-%02d.tar.zst" % i)
        tar_into_zstd(["-C", str(root), "--no-recursion", "-T", str(lst)], arc, how, level)
        lst.unlink()
        parts.append({"name": arc.name, "entries": len(entries), "bytes": arc.stat().st_size})
        log("  part %d/%d: %d entries, %.2f GB (%.0f s)" % (i + 1, n, len(entries), arc.stat().st_size / 2**30, time.time() - t0))
    meta = {"format": "x3due-parts-1", "parts": parts, "tool": how, "seconds": round(time.time() - t0, 1)}
    (out_dir / "parts.json").write_text(json.dumps(meta, indent=1))
    return meta


def extract_parts(parts_dir, dest, workers=None, how=None, log=print) -> dict:
    """All parts of a parts_dir (on Drive) into `dest`, `workers` at once, each streamed straight from Drive through zstd
    into tar: no local copy, and several Drive reads in flight at once (one read stream ran at ~40-45 MB/s on Colab)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    parts_dir, dest = Path(parts_dir), Path(dest)
    meta = json.loads((parts_dir / "parts.json").read_text())
    how = how or ensure_zstd(log)
    dest.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    total = sum(p["bytes"] for p in meta["parts"])
    done = {"bytes": 0, "n": 0}

    def one(p):
        zstd_into_tar(parts_dir / p["name"], ["-C", str(dest)], how)
        return p

    with ThreadPoolExecutor(max_workers=workers or len(meta["parts"])) as ex:
        for fut in as_completed([ex.submit(one, p) for p in meta["parts"]]):
            p = fut.result()
            done["bytes"] += p["bytes"]
            done["n"] += 1
            el = time.time() - t0
            log("  part %d/%d done · %.1f of %.1f GB read · %.0f MB/s from Drive" % (
                done["n"], len(meta["parts"]), done["bytes"] / 2**30, total / 2**30, done["bytes"] / 2**20 / max(el, 0.1)))
    el = time.time() - t0
    return {"parts": len(meta["parts"]), "gb": round(total / 2**30, 2), "seconds": round(el, 1),
            "mb_s": round(total / 2**20 / max(el, 0.1)), "tool": how}


def drive_free_gb(path) -> float | None:
    try:
        st = os.statvfs(path)
        return round(st.f_bavail * st.f_frsize / 2**30, 1)
    except OSError:
        return None


def du_gb(path) -> float:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return round(total / 2**30, 2)


def run_logged(cmd, log=print, timeout=7200) -> dict:
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        log("command failed (%d): %s\n%s" % (r.returncode, " ".join(cmd), (r.stderr or r.stdout)[-800:]))
    return {"rc": r.returncode, "seconds": round(time.time() - t0, 1)}


# ---------------------------------------------------------------------- hardware ray tracing on Vulkan
VULKAN_RT_DDPI = """[ShaderPlatform VULKAN_SM6]
bSupportsRayTracingShaders = true
bSupportsPathTracing = true
bSupportsRayTracingCallableShaders = true
bSupportsRayTracingProceduralPrimitive = true
BindlessSupport={bindless}
"""
VULKAN_RT_LINUX = """[SF_VULKAN_SM6]
BindlessResources=Enabled
BindlessSamplers=Enabled
"""
MARK = "; --- x3d.lab: Vulkan ray tracing (Epic: Hardware Ray Tracing in Unreal Engine, 'Vulkan') ---"


def _set_section(text: str, section: str, kv: dict) -> str:
    """Set key=value pairs inside [section] of an ini text (adds the section if missing). Existing keys are replaced."""
    lines = text.splitlines()
    out, i, found = [], 0, False
    head = re.compile(r"^\s*\[(.+?)\]\s*$")
    while i < len(lines):
        m = head.match(lines[i])
        out.append(lines[i])
        if m and m.group(1).strip() == section:
            found = True
            i += 1
            body = []
            while i < len(lines) and not head.match(lines[i]):
                body.append(lines[i])
                i += 1
            keys = {k.lower() for k in kv}
            body = [l for l in body if l.split("=", 1)[0].strip().lower() not in keys]
            while body and not body[-1].strip():
                body.pop()
            out += body + ["%s=%s" % (k, v) for k, v in kv.items()] + [""]
            continue
        i += 1
    if not found:
        out += ["", MARK, "[%s]" % section] + ["%s=%s" % (k, v) for k, v in kv.items()]
    return "\n".join(out).rstrip("\n") + "\n"


def enable_vulkan_rt(engine_dir, bindless="RayTracingOnly", dry_run=False) -> dict:
    """The two engine config edits Epic documents for Vulkan ray tracing (experimental). Originals kept as *.x3due.orig.
    bindless="AllShaderTypes" is the fallback a forum user needed on 5.5 ('ray tracing pipelines are only supported in
    bindless')."""
    e = Path(engine_dir) / "Engine/Config"
    targets = {e / "VulkanPC/DataDrivenPlatformInfo.ini": ("ShaderPlatform VULKAN_SM6",
                                                          {"bSupportsRayTracingShaders": "true", "bSupportsPathTracing": "true",
                                                           "bSupportsRayTracingCallableShaders": "true",
                                                           "bSupportsRayTracingProceduralPrimitive": "true",
                                                           "BindlessSupport": bindless}),
               e / "Linux/BaseLinuxEngine.ini": ("SF_VULKAN_SM6", {"BindlessResources": "Enabled", "BindlessSamplers": "Enabled"})}
    rep = {}
    for path, (section, kv) in targets.items():
        old = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        new = _set_section(old, section, kv)
        rep[str(path)] = {"exists": path.exists(), "changed": new != old}
        if not dry_run and new != old:
            path.parent.mkdir(parents=True, exist_ok=True)
            orig = path.with_name(path.name + ".x3due.orig")
            if path.exists() and not orig.exists():
                shutil.copy2(path, orig)
            path.write_text(new, encoding="utf-8")
    return rep


def disable_vulkan_rt(engine_dir) -> dict:
    """Put the originals back."""
    rep = {}
    for orig in Path(engine_dir, "Engine/Config").glob("**/*.x3due.orig"):
        target = orig.with_name(orig.name[: -len(".x3due.orig")])
        shutil.copy2(orig, target)
        rep[str(target)] = "restored"
    return rep
