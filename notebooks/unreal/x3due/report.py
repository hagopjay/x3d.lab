"""Scorecard (PASS / WARN / FAIL / SKIP, a reason on every row), the glTF-in vs Unreal-out accounting, and the run card.

    sc = report.Scorecard()
    sc.add("2 Vulkan", "Unreal sees the NVIDIA device", "PASS", "NVIDIA A100-SXM4-80GB, Vulkan 1.4.312")
    sc.html(); sc.counts()
    rows = report.account(glb_counts, unreal_report)       # glTF in vs Unreal out, row by row
    report.run_card(path, {...})
"""

from __future__ import annotations

import html as _html
import json
import platform
import time
from pathlib import Path

STATUSES = ("PASS", "WARN", "FAIL", "SKIP")
COLORS = {"PASS": "#1d6b3a", "WARN": "#9a5b00", "FAIL": "#c62828", "SKIP": "#888"}


class Scorecard:
    def __init__(self):
        self.rows = []

    def add(self, part, check, status, reason, **data):
        if status not in STATUSES:
            raise ValueError("status must be one of %s" % (STATUSES,))
        if not str(reason).strip():
            raise ValueError("every row needs a reason")
        self.rows = [r for r in self.rows if (r["part"], r["check"]) != (part, check)]
        self.rows.append({"part": part, "check": check, "status": status, "reason": str(reason)[:400], **data})
        return status

    def counts(self):
        return {s: sum(r["status"] == s for r in self.rows) for s in STATUSES}

    def word(self):
        c = self.counts()
        return "some checks FAILED" if c["FAIL"] else "passed, with warnings" if c["WARN"] else "everything that ran passed"

    def text(self):
        return "\n".join("%-14s %-4s %-46s %s" % (r["part"], r["status"], r["check"][:46], r["reason"]) for r in self.rows)

    def html(self):
        c = self.counts()
        head = " · ".join("<b style='color:%s'>%d %s</b>" % (COLORS[s], n, s) for s, n in c.items() if n)
        body = "".join("<tr><td>%s</td><td style='color:%s'><b>%s</b></td><td>%s</td><td style='color:#555'>%s</td></tr>"
                       % (_html.escape(r["part"]), COLORS[r["status"]], r["status"], _html.escape(r["check"]), _html.escape(r["reason"]))
                       for r in self.rows)
        return ("<div style='font:14px system-ui;margin:4px 0'>%s</div><table style='font-size:12.5px'><tr><th>part</th>"
                "<th>status</th><th align=left>check</th><th align=left>reason</th></tr>%s</table>" % (head, body))


def _cmp(label, gl, ue, rule="equal", note=""):
    if gl is None or ue is None:
        return {"what": label, "gltf": gl, "unreal": ue, "status": "SKIP", "reason": note or "not measured"}
    if rule == "equal":
        st = "PASS" if gl == ue else "WARN"
        reason = note or ("same" if gl == ue else "%s -> %s" % (gl, ue))
    elif rule == "atleast":
        st = "PASS" if ue >= gl else "WARN"
        reason = note or ("%s -> %s" % (gl, ue))
    else:   # tolerance (relative)
        tol = rule
        ok = abs(ue - gl) <= tol * max(1, abs(gl))
        st = "PASS" if ok else "WARN"
        reason = note or "%s -> %s (tolerance %.0f%%)" % (gl, ue, tol * 100)
    return {"what": label, "gltf": gl, "unreal": ue, "status": st, "reason": reason}


def account(glb: dict, ue: dict, manifest: dict | None = None, placed: dict | None = None) -> list:
    """glTF in (gltfstat.stats counts) vs Unreal out (x3dlab_import.py report["imported"]), row by row.
    Instancing: glTF meshes used by several nodes should arrive as one StaticMesh used by several actors."""
    gc = glb.get("counts", {})
    imp = (ue or {}).get("imported") or {}
    by_asset = imp.get("assets_by_class", {})
    by_class = imp.get("actors_by_class", {})
    rows = [
        _cmp("mesh nodes -> StaticMeshActors", gc.get("mesh_nodes"), imp.get("mesh_actors")),
        _cmp("meshes -> StaticMesh assets used", gc.get("meshes"), imp.get("static_meshes_used"),
             note="" if gc.get("meshes") == imp.get("static_meshes_used") else
             "Interchange may split multi-primitive meshes or merge per material: %s -> %s" % (gc.get("meshes"), imp.get("static_meshes_used"))),
        _cmp("shared meshes (instancing) kept", gc.get("shared_meshes"), imp.get("shared_static_meshes")),
        _cmp("triangles", gc.get("triangles"), imp.get("triangles"), rule=0.02),
        _cmp("materials", gc.get("materials"), (by_asset.get("MaterialInstanceConstant", 0) + by_asset.get("Material", 0)) or None,
             rule="atleast"),
        _cmp("textures", gc.get("textures"), by_asset.get("Texture2D"), rule="atleast"),
        _cmp("lights (KHR_lights_punctual) imported", gc.get("lights"),
             sum(by_class.get(c, 0) for c in ("DirectionalLight", "PointLight", "SpotLight", "RectLight")) if by_class else None),
        _cmp("cameras imported", gc.get("cameras"), sum(v for k, v in by_class.items() if "Camera" in k) if by_class else None),
    ]
    ax = (ue or {}).get("axes") or {}
    if ax:
        st = "PASS" if ax.get("fits") and not ax.get("ambiguous") else "WARN"
        rows.append({"what": "axes and units (X3D m, Y up -> Unreal cm, Z up)", "gltf": "Y up, m", "unreal": ax.get("axes"),
                     "status": st, "reason": "mean centre error %s cm%s" % (ax.get("errors_cm", {}).get(ax.get("axes")),
                                                                          "; ambiguous (symmetric scene)" if ax.get("ambiguous") else "")})
    if placed is not None and manifest is not None:
        nvp = sum(1 for v in manifest["viewpoints"] if v["type"] == "Viewpoint")
        rows.append(_cmp("X3D viewpoints -> CineCameras", nvp, len(placed.get("cameras_placed", []))))
        rows.append(_cmp("X3D lights -> Unreal lights (manifest)", manifest["counts"]["lights"] - manifest["counts"]["lights_by_type"].get("EnvironmentLight", 0),
                         len(placed.get("lights_placed", [])) if "lights_placed" in placed else None))
    return rows


def environment() -> dict:
    return {"python": platform.python_version(), "platform": platform.platform(terse=True), "time": time.strftime("%Y-%m-%d %H:%M:%S %Z")}


def run_card(path, card: dict) -> str:
    card = dict(card)
    card.setdefault("environment", environment())
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(card, indent=1, default=str), encoding="utf-8")
    return str(path)
