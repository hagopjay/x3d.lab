"""What survives an X3D -> Blender -> X3D -> Blender round trip, measured beyond object counts.

Snapshots come from blender_worker.snapshot(). Objects are matched by name, then leftovers by type and order
(exporters may rename). Each comparison returns plain numbers plus a list of findings with tolerances applied.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

TOL = {"location": 1e-3, "scale": 1e-3, "rotation": 1e-3, "color": 2e-3, "energy_ratio": 1e-3, "fov": 1e-3, "bbox": 2e-3}
COUNT_KEYS = ("objects", "meshes", "materials", "lights", "cameras", "vertices", "faces")


@dataclass
class Fidelity:
    matched: int = 0
    missing: list = field(default_factory=list)
    extra: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)            # key -> [before, after] where they differ
    max_err: dict = field(default_factory=dict)           # location / scale / rotation / bbox / color / fov
    geometry_changed: list = field(default_factory=list)  # meshes whose set of vertex positions changed
    instancing: list = field(default_factory=list)        # [meshes before, meshes after] when shared meshes were split
    light_ratios: list = field(default_factory=list)      # energy after / before, per light
    findings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d


def _match(a: list[dict], b: list[dict]) -> list[tuple[dict, dict]]:
    by_name = {o["name"]: o for o in b}
    pairs, used = [], set()
    rest_a = []
    for o in a:
        m = by_name.get(o["name"])
        if m is not None and m["type"] == o["type"]:
            pairs.append((o, m))
            used.add(m["name"])
        else:
            rest_a.append(o)
    rest_b = [o for o in b if o["name"] not in used]
    # Exporters rename (Shape.001 -> group_ME_Shape_Box_001), so pair leftovers of the same type by position,
    # closest pairs first, with mesh size as a tie-breaker.
    for typ in sorted({o["type"] for o in rest_a}):
        xa = [o for o in rest_a if o["type"] == typ]
        xb = [o for o in rest_b if o["type"] == typ]
        cand = sorted((_dist(p, q), i, j) for i, p in enumerate(xa) for j, q in enumerate(xb))
        ta, tb = set(), set()
        for _, i, j in cand:
            if i not in ta and j not in tb:
                pairs.append((xa[i], xb[j]))
                ta.add(i); tb.add(j)
    return pairs


def _size(o):
    bb = o.get("bbox")
    return [hi - lo for lo, hi in zip(*bb)] if bb else [0, 0, 0]


def _dist(p: dict, q: dict) -> float:
    d = sum((x - y) ** 2 for x, y in zip(p["location"], q["location"])) ** 0.5
    return d + 0.1 * sum(abs(x - y) for x, y in zip(_size(p), _size(q)))


def _maxdiff(u, v) -> float:
    if u is None or v is None:
        return 0.0
    if u and isinstance(u[0], list):
        return max((_maxdiff(x, y) for x, y in zip(u, v)), default=0.0)
    return max((abs(x - y) for x, y in zip(u, v)), default=0.0)


def _rot(u, v) -> float:
    # q and -q are the same rotation
    return min(_maxdiff(u, v), _maxdiff(u, [-x for x in v])) if u and v else 0.0


def light_dir(q) -> list:
    """Where a light points: Blender lights shine down their local -Z axis. q is (w, x, y, z)."""
    w, x, y, z = q
    return [-(2 * (x * z + w * y)), -(2 * (y * z - w * x)), -(1 - 2 * (x * x + y * y))]


def compare(before: dict, after: dict, tol: dict | None = None) -> Fidelity:
    tol = {**TOL, **(tol or {})}
    f = Fidelity()
    for k in COUNT_KEYS:
        x, y = before["counts"].get(k), after["counts"].get(k)
        if x != y:
            f.counts[k] = [x, y]
    # Vertex/face counts rise when an exporter splits vertices at normal or UV seams: reported, not a finding.
    if f.counts.get("meshes") and f.counts.get("objects") is None and f.counts["meshes"][1] > f.counts["meshes"][0]:
        f.instancing = f.counts["meshes"]
        f.findings.append("instancing lost: %d shared mesh(es) became %d copies" % tuple(f.counts["meshes"]))
    structural = {k: v for k, v in f.counts.items() if k not in ("vertices", "faces", "meshes")}
    if structural:
        f.findings.append("counts changed: " + ", ".join("%s %s→%s" % (k, *v) for k, v in structural.items()))
    pairs = _match(before["objects"], after["objects"])
    f.matched = len(pairs)
    got_a = {id(p[0]) for p in pairs}
    got_b = {id(p[1]) for p in pairs}
    f.missing = [o["name"] for o in before["objects"] if id(o) not in got_a]
    f.extra = [o["name"] for o in after["objects"] if id(o) not in got_b]
    err = {"location": 0.0, "scale": 0.0, "rotation": 0.0, "bbox": 0.0, "fov": 0.0}
    for a, b in pairs:
        err["location"] = max(err["location"], _maxdiff(a["location"], b["location"]))
        err["scale"] = max(err["scale"], _maxdiff(a["scale"], b["scale"]))
        if a["type"] == "LIGHT":           # a spin about the beam axis changes nothing; point lights have no direction
            if a.get("light") in ("SUN", "SPOT"):
                err["rotation"] = max(err["rotation"], _maxdiff(light_dir(a["rotation"]), light_dir(b["rotation"])))
        else:
            err["rotation"] = max(err["rotation"], _rot(a["rotation"], b["rotation"]))
        if a["type"] == "MESH":
            err["bbox"] = max(err["bbox"], _maxdiff(a.get("bbox"), b.get("bbox")))
            if (a.get("uverts"), a.get("vhash")) != (b.get("uverts"), b.get("vhash")):   # unique positions: seam splits are not loss
                f.geometry_changed.append(a["name"])
        elif a["type"] == "LIGHT":
            ratio = (b["energy"] / a["energy"]) if a.get("energy") else (1.0 if not b.get("energy") else float("inf"))
            f.light_ratios.append({"light": a["name"], "kind": a.get("light"), "before": a.get("energy"), "after": b.get("energy"),
                                   "ratio": round(ratio, 4)})
        elif a["type"] == "CAMERA":
            err["fov"] = max(err["fov"], abs((a.get("fov") or 0) - (b.get("fov") or 0)))
    mats_b = {m["name"]: m for m in after["materials"]}
    cerr = 0.0
    for i, m in enumerate(before["materials"]):
        n = mats_b.get(m["name"]) or (after["materials"][i] if i < len(after["materials"]) else None)
        if n is not None:
            cerr = max(cerr, _maxdiff(m["base"], n["base"]))
    err["color"] = cerr
    f.max_err = {k: round(v, 6) for k, v in err.items()}
    for k, v in err.items():
        if v > tol[k]:
            f.findings.append("%s differs by up to %.4g (tolerance %.0e)" % (k, v, tol[k]))
    if f.missing or f.extra:
        f.findings.append("objects: %d missing, %d extra" % (len(f.missing), len(f.extra)))
    if f.geometry_changed:
        f.findings.append("geometry changed in %d mesh(es)" % len(f.geometry_changed))
    off = [r for r in f.light_ratios if abs(r["ratio"] - 1) > tol["energy_ratio"]]
    if off:
        f.findings.append("light energy changed for %d light(s): %s" % (
            len(off), ", ".join("%s ×%.3g" % (r["light"], r["ratio"]) for r in off[:4])))
    return f


def drift(roundtrip_result: dict) -> list[dict]:
    """Per-generation metrics from one multi-generation round trip, relative to generation 0."""
    gens = list(roundtrip_result.get("generations", []))
    snaps = [g["snapshot"] for g in gens if "snapshot" in g]
    if roundtrip_result.get("final"):
        snaps.append(roundtrip_result["final"]["snapshot"])
    if not snaps:
        return []
    base = snaps[0]
    rows = []
    for i, s in enumerate(snaps):
        f = compare(base, s)
        lights = [o for o in s["objects"] if o["type"] == "LIGHT"]
        rows.append({"gen": i, "vertices": s["counts"]["vertices"], "objects": s["counts"]["objects"], "meshes": s["counts"]["meshes"],
                     "materials": s["counts"]["materials"],
                     "light_energy_total": round(sum(o["energy"] for o in lights), 4),
                     "light_ratio_min": min((r["ratio"] for r in f.light_ratios), default=1.0),
                     "light_ratio_max": max((r["ratio"] for r in f.light_ratios), default=1.0),
                     "max_location_err": f.max_err.get("location", 0), "max_color_err": f.max_err.get("color", 0),
                     "geometry_changed": len(f.geometry_changed), "ok_vs_gen0": f.ok,
                     "bytes": gens[i].get("bytes") if i < len(gens) else None})
    return rows
