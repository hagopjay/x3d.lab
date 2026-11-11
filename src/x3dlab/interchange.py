"""Interchange: the other direction. Khronos's own glTF sample models go into X3D two independent ways, and X_ITE judges
both against the original; the same run takes the reference stills other engines (Unreal) are compared with.

For each sample (formats.SAMPLES: 32 small models, one glTF feature each):

  validate   the Khronos glTF Validator on the source (the samples should be clean; a warning is worth knowing)
  reference  X_ITE draws the glTF through Inline under a camera computed from the file's own bounds
             (formats.reference_camera): one still on the magenta key for shape comparisons, one on mid grey for eyes,
             1280 x 720, with a manifest (camera, bounds, resolution, X_ITE version, sample-assets commit)
  X_ITE      X_ITE's own converter: it opens the glTF and writes it back as X3D (createX3DFromURL + toXMLString).
             The X3D is judged (x3d_mcp, lxml + XSD, object model) and drawn again under the same camera
  Blender    Blender's glTF importer + x3d-blender-io's X3D 4.0 exporter: the X3D is judged and drawn the same way;
             the same Blender scene is also written as USD and checked with Pixar's compliance checker
  claims     what each converter lost (shapes: formats.match; colours: formats.colour_kind; validity), node types each
             one used (the gap map: what X3D 4 can hold vs what each tool writes), issue drafts

Two converters, three pictures: when X_ITE's X3D matches the glTF and Blender's does not, the loss is Blender's leg;
when neither matches, X3D (or X_ITE) cannot yet hold that feature.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import aio, bus, formats, hog, pins, runtime

STILL_SIZE = (1280, 720)
KEY, GREY = "1 0 1", "0.5 0.5 0.5"
formats.COLOUR_DELTA.setdefault("x3d_from_gltf_vs_gltf_in_x_ite", 64.0)    # both drawn by X_ITE: PhysicalMaterial vs glTF PBR


@dataclass
class InterchangeConfig:
    samples: list = field(default_factory=list)           # names from formats.SAMPLES; empty = all of them
    size: list = field(default_factory=lambda: list(STILL_SIZE))
    xite_converter: bool = True
    blender: bool = True
    blender_versions: list = field(default_factory=lambda: ["X3D40"])
    usd: bool = True
    chrome_gpu: bool = True
    name: str = ""


@dataclass
class InterchangeRun:
    cfg: InterchangeConfig
    id: str
    name: str
    dir: Path
    started: float = field(default_factory=time.time)
    ended: float | None = None
    results: dict = field(default_factory=dict)            # sample -> {...}
    claims: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)

    def file_url(self, name: str) -> str:
        return "out/%s/%s" % (self.dir.name, name)


def _types(xml: str) -> list:
    return hog._node_types(xml)


def _version_of(xml: str) -> str:
    m = re.search(r"<X3D[^>]*\bversion=['\"]([0-9.]+)", xml or "")
    return m.group(1) if m and m.group(1) in ("4.0", "4.1") else "4.0"


def _still(hb, xml: str, size, background: str, path: Path):
    r = aio.run(hb.aload(xml, screenshot=True, size=tuple(size), background=background, frames=3, timeout=120), 240)
    if r.png:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.png)
    return r


def run(lab, cfg: InterchangeConfig | None = None, **kw) -> InterchangeRun:
    """Blocking. Needs lab = x3dlab.up(); lab.setup_blender() for the Blender column (skipped without it)."""
    from . import foundry
    cfg = cfg or InterchangeConfig(**kw)
    rid = uuid.uuid4().hex[:8]
    r = InterchangeRun(cfg, rid, cfg.name or "interchange-%s" % rid, runtime.sub("out", "interchange_" + rid))
    names = [n for n in (cfg.samples or list(formats.SAMPLES)) if n in formats.SAMPLES]

    def stage(name, fn):
        t0 = time.time()
        try:
            with bus.span("interchange: %s" % name, src="interchange", job=rid):
                fn()
        except Exception as exc:
            r.errors[name] = "%s: %s" % (type(exc).__name__, str(exc)[:400])
            bus.error("interchange %s failed: %s" % (name, r.errors[name]), src="interchange", job=rid)
        r.timings[name] = round(time.time() - t0, 1)
        write(r)

    src = {}

    def st_fetch():
        root = formats.fetch_samples(names)
        r.env["samples"] = {"repo": pins.get("gltf_samples", "repo"), "ref": pins.get("gltf_samples", "ref")}
        for n in names:
            f = formats.sample_file(root, n)
            if f is None:
                r.results[n] = {"error": "not in the sample assets checkout"}
                continue
            dst = r.dir / "src" / n
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(f.parent, dst)
            src[n] = dst / f.name
            r.results[n] = {"probes": formats.SAMPLES[n][1], "file": os.path.relpath(src[n], r.dir),
                            "bounds": formats.gltf_bounds(src[n])}
            r.results[n]["camera"] = formats.reference_camera(r.results[n]["bounds"])
        reps = formats.validate_gltf(list(src.values()))
        for n, f in src.items():
            rep = reps.get(str(f), {})
            r.results[n]["validator"] = {"ok": rep.get("ok"), "errors": rep.get("errors"), "warnings": rep.get("warnings"),
                                         "codes": formats.error_codes(rep), "info": rep.get("info")}

    def st_reference():
        if lab.browser is None or (cfg.chrome_gpu and not lab.browser.gpu_requested):
            lab.start_browser(gpu=cfg.chrome_gpu)
        hb = lab.browser
        hb.assets = r.dir
        r.env["x_ite"] = getattr(hb, "info", None)
        for n, f in src.items():
            res = r.results[n]
            xml = formats.framed_scene(res["file"], res["camera"])
            k = _still(hb, xml, cfg.size, KEY, r.dir / "stills" / ("%s.key.png" % n))
            _still(hb, xml, cfg.size, GREY, r.dir / "stills" / ("%s.grey.png" % n))
            res["reference"] = {"ok": k.ok, "errors": k.errors[:3], "coverage": hog._coverage(k.png) if k.png else None}
            res["reference"]["empty"] = (res["reference"]["coverage"] or 0) < 0.002     # nothing to compare shapes with
            manifest = {"sample": n, "file": res["file"], "probes": res["probes"], "bounds": res["bounds"], "camera": res["camera"],
                        "size": list(cfg.size), "backgrounds": {"key": KEY, "grey": GREY}, "headlight": True,
                        "x_ite": (r.env.get("x_ite") or {}).get("xite"), "chrome": (r.env.get("x_ite") or {}).get("chromium"),
                        "renderer": (r.env.get("x_ite") or {}).get("webgl"), "sample_assets": r.env.get("samples"),
                        "stills": {"key": "stills/%s.key.png" % n, "grey": "stills/%s.grey.png" % n}, "scene": xml}
            (r.dir / "stills" / ("%s.manifest.json" % n)).write_text(json.dumps(manifest, indent=1))

    def judge_and_draw(n: str, xml: str, path: Path, column: str):
        res = r.results[n]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(xml, encoding="utf-8")
        version = _version_of(xml)
        j = aio.run(foundry.judge(lab.client, xml, version, rid), 300)
        out = {"file": os.path.relpath(path, r.dir), "bytes": len(xml.encode("utf-8")), "version": version,
               "types": _types(xml), "fork_ok": j["fork"]["ok"], "fork_kinds": j["fork"]["kinds"][:8],
               "xsd_ok": j["xsd"]["ok"], "xsd_first": j["xsd"]["first"][:240], "uom_allows": j["uom"]["allows"],
               "effective": j["effective"]}
        draw = _still(lab.browser, formats.framed_scene(out["file"], res["camera"]), cfg.size, KEY,
                      r.dir / "stills" / ("%s.%s.png" % (n, column)))
        out["draw"] = {"ok": draw.ok, "errors": draw.errors[:3]}
        ref = r.dir / "stills" / ("%s.key.png" % n)
        if draw.png and ref.exists():
            out["compare"] = formats.compare_pngs(ref.read_bytes(), draw.png)
            out["colour_kind"] = formats.colour_kind(out["compare"], against="x3d_from_gltf_vs_gltf_in_x_ite")
        res[column] = out

    def st_xite():
        hb = lab.browser
        for n in src:
            e = aio.run(hb.aexport_url(r.results[n]["file"]), 240)
            if not e["ok"] or "<X3D" not in e["xml"]:
                r.results[n]["xite"] = {"error": "; ".join(e["errors"][:2]) or "no X3D"}
                continue
            judge_and_draw(n, e["xml"], r.dir / "conv" / ("%s.xite.x3d" % n), "xite")
            r.results[n]["xite"]["ms"] = e["ms"]

    def st_blender():
        if lab.blender is None:
            raise RuntimeError("no Blender: run lab.setup_blender() first, or set blender=False")
        tasks = [{"kind": "ingest", "src": str(f), "versions": list(cfg.blender_versions), "usd": bool(cfg.usd),
                  "out_prefix": str(r.dir / "conv" / n), "_item": n} for n, f in src.items()]
        (r.dir / "conv").mkdir(exist_ok=True)
        for t, res in hog._run_chunks(lab.blender, tasks, hog.HogConfig(), rid):
            n = t["_item"]
            out = res.get("result") or {}
            err = out.get("error") or ("" if res.get("ok") else "failed")
            b = {"error": err[:300], "counts": out.get("counts"), "shared_meshes": out.get("shared_meshes"),
                 "smooth_faces": out.get("smooth_faces"), "actions": out.get("actions"), "armatures": out.get("armatures"),
                 "shape_keys": out.get("shape_keys"), "log": [ln for ln in res.get("log", []) if ln.strip()][:5]}
            r.results[n]["blender_import"] = b
            for bv in cfg.blender_versions:
                o = (out.get("outputs") or {}).get(bv) or {}
                if o.get("output") and os.path.exists(o["output"]):
                    judge_and_draw(n, Path(o["output"]).read_text(encoding="utf-8", errors="replace"), Path(o["output"]),
                                   "blender_" + bv.lower())
                else:
                    r.results[n]["blender_" + bv.lower()] = {"error": o.get("error") or err or "no output"}
            u = (out.get("outputs") or {}).get("USD") or {}
            if u.get("output") and "usd_core" not in r.env:
                r.env["usd_core"] = formats.ensure_usd()
            if u.get("output") and os.path.exists(u["output"]):
                chk = formats.usd_check(u["output"])
                r.results[n]["usd"] = {"file": os.path.relpath(u["output"], r.dir), "bytes": u.get("bytes"), **chk}
            elif cfg.usd:
                r.results[n]["usd"] = {"error": u.get("error") or "no output"}

    stage("fetch", st_fetch)
    if src:
        stage("reference", st_reference)
        if cfg.xite_converter and lab.browser is not None:
            stage("xite", st_xite)
        if cfg.blender:
            stage("blender", st_blender)
    r.claims = find_claims(r)
    write_issues(r)
    try:
        from . import toolchain
        r.env["toolchain"] = toolchain.report(lab)
    except Exception:
        pass
    r.ended = time.time()
    write(r)
    bus.emit("interchange %s: %d samples, %d claims" % (r.name, len(src), len(r.claims)), src="interchange", kind="interchange_end",
             job=rid, data={"report": r.file_url("report.html"), "errors": r.errors})
    return r


# ====================================================================== claims
GLTF_ONLY = {   # sample features X3D 4 has no node for, so a lost picture or colour there is expected
    "SimpleSkin": "glTF skins: X3D has HAnim, not general skins",
    "MorphPrimitivesTest": "morph targets: no X3D node blends shapes this way (a CoordinateInterpolator could bake them)",
    "SimpleInstancing": "EXT_mesh_gpu_instancing: X3D 4 has no instancing node (InstancedShape is X3D 4.1 CAD)",
    "EmissiveStrengthTest": "emission above 1: X3D colours stop at 1",
}


def find_claims(r: InterchangeRun) -> list:
    out = []

    def claim(kind, target, n, summary, evidence=None, expected=""):
        out.append({"kind": kind, "target": target, "sample": n, "summary": summary, "evidence": evidence or {}, "expected": expected})
    for n, res in r.results.items():
        v = res.get("validator") or {}
        if v.get("errors"):
            claim("gltf-source-invalid", "gltf_samples", n, "the Khronos validator reports errors on the sample: %s" % ", ".join(v.get("codes") or []))
        ref = res.get("reference") or {}
        if ref and (not ref.get("ok") or ref.get("errors")):
            claim("xite-gltf-error", "x_ite", n, "X_ITE cannot draw the glTF sample: %s" % "; ".join(ref.get("errors") or ["load failed"])[:200])
            continue
        for col, target, who in (("xite", "x_ite", "X_ITE's glTF → X3D converter"), ("blender_x3d40", "blender_io", "Blender + x3d-blender-io"),
                                 ("blender_x3d41", "blender_io", "Blender + x3d-blender-io (4.1)")):
            c = res.get(col)
            if not c:
                continue
            if c.get("error"):
                claim("%s-failed" % col.split("_")[0], target, n, "%s fails: %s" % (who, c["error"][:200]))
                continue
            if c.get("xsd_ok") is False and not c.get("uom_allows"):
                ext = extension_field(c.get("xsd_first", ""), c.get("version", "4.0"))
                claim("%s-x3d-invalid" % col.split("_")[0], target, n, "%s writes X3D %s the XSD rejects: %s%s" % (
                    who, c["version"], c["xsd_first"][:160], (" (%s)" % ext) if ext else ""), {"kinds": c.get("fork_kinds"), "extension": ext})
            if ref.get("empty"):
                continue                         # the reference still shows nothing: no shape or colour verdict
            cm = c.get("compare") or {}
            exp = GLTF_ONLY.get(n, "")
            if cm.get("match") is not None and cm["match"] < foundry_match() and (cm.get("cov_original") or 0) > 0.002:
                claim("%s-x3d-lost" % col.split("_")[0], target, n, "%s loses part of the picture (silhouette match %.2f)" % (who, cm["match"]),
                      {"compare": cm, "types": c.get("types")}, exp)
            elif c.get("colour_kind"):
                claim("%s-x3d-colour" % col.split("_")[0], target, n, "%s keeps the shapes but not the colours (%s: mean change %.0f of 255)"
                      % (who, c["colour_kind"], cm.get("colour_delta") or 0), {"compare": cm, "types": c.get("types")}, exp)
        u = res.get("usd") or {}
        if u.get("ok") is False:
            claim("usd-invalid", "blender_usd", n, "Blender's USD export of the sample fails Pixar's compliance checker: %s"
                  % "; ".join(u.get("failed", [])[:2] or u.get("errors", [])[:2])[:240])
    return out


def extension_field(message: str, version: str) -> str:
    """'' or a note when an XSD 'attribute not allowed' complaint names a field the X3D object model lacks too:
    then the writer used an implementation's own extension field, not a field the XSD forgot."""
    import re as _re
    m = _re.search(r"Element '([A-Za-z0-9]+)', attribute '([A-Za-z0-9_]+)': The attribute '[^']+' is not allowed", message or "")
    if not m:
        return ""
    node, fld = m.groups()
    spec = hog._uom(version).nodes.get(node)
    if spec is None or fld in spec.fields:
        return ""
    return "%s.%s is not in the X3D %s object model either: an extension field of the writer, not a gap in the XSD" % (node, fld, version)


def foundry_match() -> float:
    from .foundry import GLTF_MATCH
    return GLTF_MATCH


def gap_map(r: InterchangeRun) -> list:
    """Per sample: node types only X_ITE's converter used, only Blender's, and both."""
    rows = []
    for n, res in r.results.items():
        a = set((res.get("xite") or {}).get("types") or [])
        b = set((res.get("blender_x3d40") or {}).get("types") or [])
        if a or b:
            rows.append({"sample": n, "probes": res.get("probes"), "only_xite": sorted(a - b), "only_blender": sorted(b - a),
                         "both": sorted(a & b)})
    return rows


# ====================================================================== files
TARGETS = {"x_ite": "X_ITE (glTF loader and X3D writer)", "blender_io": "x3d-blender-io (Blender add-on, X3D export)",
           "blender_usd": "Blender's USD exporter", "gltf_samples": "Khronos glTF Sample Assets"}


def write_issues(r: InterchangeRun) -> None:
    d = r.dir / "kit" / "issues"
    k = 0
    for c in r.claims:
        if c["expected"]:
            continue
        k += 1
        d.mkdir(parents=True, exist_ok=True)
        res = r.results.get(c["sample"], {})
        col = {"xite": "xite", "blender": "blender_x3d40"}.get(c["kind"].split("-")[0])
        conv = res.get(col) or {}
        lines = ["# %s (%s)" % (c["summary"][:120], c["sample"]), "", "**For:** %s  " % TARGETS.get(c["target"], c["target"]),
                 "**Sample:** Khronos glTF Sample Assets `%s` (%s) at %s  " % (c["sample"], res.get("probes", ""), (r.env.get("samples") or {}).get("ref", "")[:12]),
                 "**What it probes:** %s" % res.get("probes", ""), "",
                 "## What happened", "", c["summary"], ""]
        if conv.get("file"):
            lines += ["The converted file is `%s` (%s bytes, X3D %s). Node types: %s." % (conv["file"], conv.get("bytes"), conv.get("version"),
                                                                                            ", ".join(conv.get("types") or []) or "none"), ""]
        lines += ["## Pictures", "", "Reference (X_ITE drawing the glTF) `stills/%s.key.png`; the converted X3D drawn under the same camera: "
                  "`stills/%s.%s.png`. Camera and setup: `stills/%s.manifest.json`." % (c["sample"], c["sample"], col or "?", c["sample"]), ""]
        if c["evidence"]:
            lines += ["<details><summary>Evidence</summary>", "", "```json", json.dumps(c["evidence"], indent=1, default=str)[:2500], "```", "</details>", ""]
        lines += ["## How it was found", "", "x3d.lab interchange run %s: the sample through X_ITE's converter and through Blender + "
                  "x3d-blender-io, each judged by x3d_mcp, lxml + XSD and the X3D object model, and drawn by X_ITE %s." % (
                      r.id, (r.env.get("x_ite") or {}).get("xite", ""))]
        f = d / ("%02d-%s-%s-%s.md" % (k, c["target"], c["kind"], c["sample"]))
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        c["issue"] = os.path.relpath(f, r.dir)


def card(r: InterchangeRun) -> dict:
    return {"id": r.id, "name": r.name, "started": r.started, "ended": r.ended, "cfg": asdict(r.cfg), "env": r.env,
            "timings": r.timings, "errors": r.errors, "results": r.results, "claims": r.claims, "gap_map": gap_map(r)}


def write(r: InterchangeRun) -> None:
    r.dir.mkdir(parents=True, exist_ok=True)
    (r.dir / "interchange_card.json").write_text(json.dumps(card(r), indent=1, default=str))
    (r.dir / "report.html").write_text("<!doctype html><meta charset=utf-8><title>%s</title><body style='font:14px system-ui;"
                                       "max-width:1300px;margin:16px auto'>%s</body>" % (_esc(r.name), html(r)), encoding="utf-8")
    idx = {n: "stills/%s.manifest.json" % n for n in r.results if (r.dir / "stills" / ("%s.manifest.json" % n)).exists()}
    if idx:
        (r.dir / "stills" / "index.json").write_text(json.dumps({"size": list(r.cfg.size), "samples": idx}, indent=1))


def _esc(s) -> str:
    import html as h
    return h.escape(str(s if s is not None else ""))


def _img(r: InterchangeRun, rel: str, title: str) -> str:
    import base64
    p = r.dir / rel
    if not p.exists():
        return "<div style='width:240px;height:135px;background:#eee;color:#999;font-size:11px;display:flex;align-items:center;" \
               "justify-content:center'>%s</div>" % _esc(title)
    try:
        from PIL import Image
        import io
        im = Image.open(p)
        im.thumbnail((240, 135))
        b = io.BytesIO()
        im.save(b, format="PNG")
        data = b.getvalue()
    except Exception:
        data = p.read_bytes()
    return "<img title='%s' src='data:image/png;base64,%s' style='width:240px;border:1px solid #ddd'>" % (_esc(title), base64.b64encode(data).decode())


def _cell(c: dict | None) -> str:
    if not c:
        return "<td style='color:#999'>-</td>"
    if c.get("error"):
        return "<td style='color:#a33'>%s</td>" % _esc(c["error"][:60])
    cm = c.get("compare") or {}
    ok = "✓" if c.get("xsd_ok") or c.get("uom_allows") else "✗"
    return "<td>%s XSD · match %s · Δcol %s%s</td>" % (ok, cm.get("match"), cm.get("colour_delta"),
                                                      (" · <b>%s</b>" % _esc(c["colour_kind"])) if c.get("colour_kind") else "")


def html(r: InterchangeRun) -> str:
    out = ["<h2 style='margin:4px 0'>Interchange <span style='font-weight:normal;color:#666'>%s</span></h2>" % _esc(r.name)]
    if r.errors:
        out.append("<div style='color:#a33'>%s</div>" % "<br>".join("%s: %s" % (_esc(k), _esc(v)) for k, v in r.errors.items()))
    out.append("<div style='color:#555'>%d samples · %d claims (%d expected) · %s</div>" % (
        len(r.results), len(r.claims), sum(1 for c in r.claims if c["expected"]), " · ".join("%s %ss" % kv for kv in r.timings.items())))
    out.append("<table style='border-collapse:collapse;font-size:12px;margin-top:8px'><tr><th align=left>sample</th><th align=left>probes</th>"
               "<th align=left>X_ITE converter</th><th align=left>Blender X3D 4.0</th><th align=left>USD</th></tr>")
    for n, res in r.results.items():
        u = res.get("usd") or {}
        out.append("<tr style='border-top:1px solid #eee'><td><b>%s</b></td><td style='color:#666'>%s</td>%s%s<td>%s</td></tr>" % (
            _esc(n), _esc(res.get("probes", "")), _cell(res.get("xite")), _cell(res.get("blender_x3d40")),
            ("✓" if u.get("ok") else "✗" if u.get("ok") is False else "-") + (" %s" % _esc(u.get("stage", {}).get("types", "")) if u.get("stage") else "")))
    out.append("</table>")
    if r.claims:
        out.append("<h3>Claims</h3><ul>%s</ul>" % "".join("<li>%s<b>%s</b> · %s: %s%s</li>" % (
            "<i>expected</i> · " if c["expected"] else "", _esc(c["sample"]), _esc(TARGETS.get(c["target"], c["target"])), _esc(c["summary"]),
            (" <span style='color:#777'>(%s)</span>" % _esc(c["expected"])) if c["expected"] else "") for c in r.claims))
    gm = gap_map(r)
    if gm:
        out.append("<h3>Gap map: X3D node types each converter used</h3><table style='font-size:12px'>%s</table>" % "".join(
            "<tr><td><b>%s</b></td><td>only X_ITE: %s</td><td>only Blender: %s</td></tr>" % (
                _esc(g["sample"]), _esc(", ".join(g["only_xite"]) or "-"), _esc(", ".join(g["only_blender"]) or "-")) for g in gm))
    out.append("<h3>Pictures: glTF in X_ITE · X_ITE's X3D · Blender's X3D</h3>")
    for n in r.results:
        out.append("<div style='margin:6px 0'><b>%s</b><div style='display:flex;gap:6px'>%s%s%s</div></div>" % (
            _esc(n), _img(r, "stills/%s.key.png" % n, "glTF in X_ITE"), _img(r, "stills/%s.xite.png" % n, "X_ITE's X3D"),
            _img(r, "stills/%s.blender_x3d40.png" % n, "Blender's X3D")))
    return "".join(out)


def show(r: InterchangeRun, lab=None) -> None:
    from .ui import show as _show
    _show("<div style='font:14px system-ui'>%s</div>" % html(r))
