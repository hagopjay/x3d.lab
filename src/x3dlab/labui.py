"""Notebook widgets for the 02 Lab: the stress-test control panel and the results view."""

from __future__ import annotations

import json

from . import scenegen, stress
from .ui import esc, show

VERSIONS = ["X3D33", "X3D40", "X3D41"]
# The 02 panel edits the five lab tiers; the research tiers and presets belong to 03 (lab.research()).
LAB_TIERS = ["validate", "roundtrip", "drift", "render", "load"]
LAB_PRESETS = ["smoke", "standard", "heavy"]


def _ints(text: str) -> list[int]:
    return [int(x) for x in text.replace(";", ",").split(",") if x.strip()]


def _res(text: str) -> list[list[int]]:
    out = []
    for part in text.replace(";", ",").split(","):
        if "x" in part:
            w, h = part.lower().split("x")
            out.append([int(w), int(h)])
    return out


class Panel:
    """Every RunConfig knob as a widget. `panel.config()` returns the RunConfig the widgets describe."""

    def __init__(self, cfg: stress.RunConfig | None = None):
        import ipywidgets as W
        self.W = W
        self.cfg = cfg or stress.RunConfig.preset_for("standard")
        L = lambda w="420px": W.Layout(width=w)  # noqa: E731
        st = {"description_width": "140px"}
        self.preset = W.Dropdown(options=LAB_PRESETS + ([self.cfg.preset] if self.cfg.preset not in LAB_PRESETS else []), value=self.cfg.preset, description="preset", style=st, layout=L("300px"))
        self.seed = W.IntText(value=self.cfg.seed, description="seed", style=st, layout=L("260px"))
        self.name = W.Text(value=self.cfg.name, placeholder="(from the seed)", description="run name", style=st, layout=L("360px"))
        self.families = W.SelectMultiple(options=list(scenegen.FAMILIES), description="scene families", style=st, layout=L("360px"), rows=6)
        self.sizes = W.Text(description="sizes", style=st, layout=L("360px"))
        self.workers = W.IntSlider(min=1, max=12, description="Blender workers", style=st, layout=L("360px"))
        self.on = {t: W.Checkbox(description=t, indent=False, layout=L("120px")) for t in LAB_TIERS}
        self.rt_versions = W.SelectMultiple(options=VERSIONS, description="round-trip versions", style=st, rows=3, layout=L("360px"))
        self.dr_families = W.SelectMultiple(options=list(scenegen.FAMILIES), description="drift families", style=st, rows=6, layout=L("360px"))
        self.dr_versions = W.SelectMultiple(options=VERSIONS, description="drift versions", style=st, rows=3, layout=L("360px"))
        self.dr_gens = W.IntSlider(min=1, max=20, description="generations", style=st, layout=L("360px"))
        self.r_backend = W.Dropdown(options=["auto", "OPTIX", "CUDA", "HIP", "CPU"], description="render backend", style=st, layout=L("300px"))
        self.r_samples = W.Text(description="samples ladder", style=st, layout=L("420px"))
        self.r_res = W.Text(description="resolution ladder", style=st, layout=L("420px"))
        self.r_geom = W.Text(description="geometry ladder", style=st, layout=L("420px"))
        self.r_scene = W.IntText(description="ladder scene size", style=st, layout=L("260px"))
        self.r_denoise = W.Checkbox(description="denoise", indent=False)
        self.r_cpu = W.Checkbox(description="also on CPU (speed-up)", indent=False)
        self.l_sessions = W.IntSlider(min=1, max=128, description="MCP sessions", style=st, layout=L("360px"))
        self.l_calls = W.IntSlider(min=1, max=100, description="calls each", style=st, layout=L("360px"))
        self.json = W.Textarea(layout=W.Layout(width="100%", height="160px"))
        b_show = W.Button(description="Show JSON")
        b_load = W.Button(description="Load JSON")
        self.msg = W.HTML()
        b_show.on_click(lambda _: setattr(self.json, "value", self.config().to_json()))
        b_load.on_click(self._load_json)
        self.preset.observe(lambda c: self._fill(stress.RunConfig.preset_for(c["new"], seed=self.seed.value)), names="value")
        self._fill(self.cfg)
        tiers = W.HBox(list(self.on.values()))
        self.widget = W.Tab(children=[
            W.VBox([W.HBox([self.preset, self.seed]), self.name, tiers, self.families, self.sizes, self.workers]),
            W.VBox([self.rt_versions, W.HTML("<b>drift</b>"), self.dr_families, self.dr_versions, self.dr_gens]),
            W.VBox([self.r_backend, self.r_samples, self.r_res, self.r_geom, self.r_scene, W.HBox([self.r_denoise, self.r_cpu])]),
            W.VBox([self.l_sessions, self.l_calls]),
            W.VBox([W.HBox([b_show, b_load]), self.json, self.msg]),
        ])
        for i, t in enumerate(["run", "round trip & drift", "render", "load", "JSON"]):
            self.widget.set_title(i, t)

    def _fill(self, cfg: stress.RunConfig):
        t = cfg.tiers
        self.families.value = tuple(cfg.families)
        self.sizes.value = ", ".join(map(str, cfg.sizes))
        self.workers.value = cfg.workers
        for k, w in self.on.items():
            w.value = bool(t.get(k, {}).get("on"))
        self.rt_versions.value = tuple(t["roundtrip"]["versions"])
        self.dr_families.value = tuple(t["drift"]["families"])
        self.dr_versions.value = tuple(t["drift"]["versions"])
        self.dr_gens.value = int(t["drift"]["generations"])
        r = t["render"]
        self.r_backend.value = r.get("backend", "auto")
        self.r_samples.value = ", ".join(map(str, r["samples"]))
        self.r_res.value = ", ".join("%dx%d" % tuple(x) for x in r["resolutions"])
        self.r_geom.value = ", ".join(map(str, r["geometry"]))
        self.r_scene.value = r["scene_size"]
        self.r_denoise.value = bool(r.get("denoise"))
        self.r_cpu.value = bool(r.get("compare_cpu"))
        self.l_sessions.value = int(t["load"]["sessions"])
        self.l_calls.value = int(t["load"]["calls"])
        if cfg.notes:
            self.msg.value = "<small>%s</small>" % esc(cfg.notes)

    def _load_json(self, _):
        try:
            cfg = stress.RunConfig.from_json(self.json.value)
            self.preset.value = cfg.preset
            self.seed.value, self.name.value = cfg.seed, cfg.name
            self._fill(cfg)
            self.msg.value = "<span style='color:#1d6b3a'>loaded</span>"
        except Exception as exc:
            self.msg.value = "<span style='color:#c62828'>%s</span>" % esc(exc)

    def config(self) -> stress.RunConfig:
        c = stress.RunConfig(preset=self.preset.value, seed=self.seed.value, name=self.name.value.strip(),
                             families=list(self.families.value), sizes=_ints(self.sizes.value), workers=self.workers.value,
                             tiers={
                                 "validate": {"on": self.on["validate"].value},
                                 "roundtrip": {"on": self.on["roundtrip"].value, "versions": list(self.rt_versions.value)},
                                 "drift": {"on": self.on["drift"].value, "families": list(self.dr_families.value),
                                           "versions": list(self.dr_versions.value), "generations": self.dr_gens.value},
                                 "render": {"on": self.on["render"].value, "backend": self.r_backend.value, "scene_size": self.r_scene.value,
                                            "samples": _ints(self.r_samples.value), "resolutions": _res(self.r_res.value),
                                            "geometry": _ints(self.r_geom.value), "denoise": self.r_denoise.value, "compare_cpu": self.r_cpu.value},
                                 "load": {"on": self.on["load"].value, "sessions": self.l_sessions.value, "calls": self.l_calls.value}})
        return c

    def display(self):
        from IPython.display import display
        display(self.widget)
        return self


def panel(cfg: stress.RunConfig | None = None) -> Panel:
    return Panel(cfg).display()


# ---------------------------------------------------------------------- results
COL = {"PASS": "#1d6b3a", "WARN": "#9a5b00", "FAIL": "#c62828", "SKIP": "#888"}


def show_run(run: stress.StressRun, lab=None, tiers: list | None = None) -> None:
    summ = run.summary()
    head = "<div style='font:15px system-ui'><b>%s</b> · %s preset · seed %d · %.0f s</div><div style='font:13px system-ui'>%s</div>" % (
        esc(run.name), esc(run.cfg.preset), run.cfg.seed, (run.ended or 0) - run.started,
        " · ".join("%s: %s" % (t, ", ".join("<b style='color:%s'>%d %s</b>" % (COL[s], n, s) for s, n in c.items() if n)) for t, c in summ.items()))
    rows = "".join("<tr><td>%s</td><td style='color:%s'>%s</td><td>%s</td><td align=right>%s</td><td style='color:#666'>%s</td></tr>"
                   % (esc(r.tier), COL[r.status], r.status, esc(r.case), "" if r.ms is None else "%.0f" % r.ms, esc(r.detail[:200]))
                   for r in run.rows if tiers is None or r.tier in tiers)
    link = ""
    if lab is not None:
        base = lab.dashboard_url()
        link = "<div style='font-size:12px'><a href='%s%s' target=_blank>full report</a> · <a href='%s%s' target=_blank>run card (JSON)</a></div>" % (
            esc(base), esc(run.file_url("report.html")), esc(base), esc(run.file_url("run_card.json")))
    show(head + link + "<table style='font-size:12px'><tr><th>tier</th><th>status</th><th align=left>case</th><th>ms</th><th align=left>detail</th></tr>%s</table>" % rows)


def plot_drift(run: stress.StressRun) -> None:
    import matplotlib.pyplot as plt
    if not run.drift:
        print("no drift data in this run")
        return
    keys = sorted(run.drift)
    fig, axes = plt.subplots(1, 3, figsize=(14, 3.2))
    for k in keys:
        g = run.drift[k]
        if not g:
            continue
        gens = [x["gen"] for x in g]
        style = "--" if "X3D33" in k else "-"
        base_e = g[0]["light_energy_total"] or 1
        axes[0].plot(gens, [x["light_energy_total"] / base_e for x in g], style, marker="o", label=k)
        axes[1].plot(gens, [x["meshes"] / max(g[0]["meshes"], 1) for x in g], style, marker="o", label=k)
        sized = [x for x in g if x.get("bytes")]       # the final re-import writes no file
        axes[2].plot([x["gen"] for x in sized], [x["bytes"] / 1000 for x in sized], style, marker="o", label=k)
    axes[0].set_title("total light energy (× generation 0)")
    axes[1].set_title("mesh datablocks (× generation 0)")
    axes[2].set_title("exported file size (kB)")
    for a in axes:
        a.set_xlabel("generation")
        a.grid(alpha=0.3)
    axes[2].legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.02, 1))
    plt.tight_layout()
    plt.show()


def plot_render(run: stress.StressRun) -> None:
    import matplotlib.pyplot as plt
    if not run.renders:
        print("no renders in this run")
        return
    fig, axes = plt.subplots(1, 3, figsize=(14, 3.2))
    for ax, ladder, xkey, xlabel in ((axes[0], "samples", "samples", "samples per pixel"),
                                    (axes[1], "resolution", None, "megapixels"),
                                    (axes[2], "geometry", "vertices", "vertices")):
        for be in sorted({r["backend"] for r in run.renders}):
            pts = [r for r in run.renders if "· %s ·" % ladder in r["case"] and r["backend"] == be]
            if not pts:
                continue
            xs = [(r["resolution"][0] * r["resolution"][1] / 1e6) if xkey is None else r[xkey] for r in pts]
            ax.plot(xs, [r["seconds"] for r in pts], marker="o", label=be)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("seconds")
        ax.set_title("%s ladder" % ladder)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    plt.tight_layout()
    plt.show()


def gallery(run: stress.StressRun, n: int = 12) -> None:
    import base64
    from pathlib import Path
    figs = []
    for r in run.renders[-n:]:
        p = run.dir / Path(r["url"]).name
        if p.exists():
            figs.append("<figure style='display:inline-block;margin:4px;width:230px'><img src='data:image/png;base64,%s' style='width:100%%;border-radius:6px'>"
                        "<figcaption style='font-size:11px'>%s · %.2f s%s</figcaption></figure>"
                        % (base64.b64encode(p.read_bytes()).decode(), esc(r["case"]), r["seconds"],
                           (" · %.0f MB VRAM" % r["peak_mem_mb"]) if r.get("peak_mem_mb") else ""))
    show("".join(figs) or "<i>no renders</i>")


def compare(card_a: str, card_b: str) -> None:
    a, b = (json.load(open(p)) for p in (card_a, card_b))
    diffs = stress.compare_cards(a, b)
    rows = "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td style='color:#666'>%s</td></tr>" % (esc(d["tier"]), esc(d["case"]), esc(d["change"]), esc(d["now"]))
                   for d in diffs)
    show("<div style='font:14px system-ui'><b>%s</b> → <b>%s</b>: %d change(s)</div><table style='font-size:12px'>%s</table>"
         % (esc(a["name"]), esc(b["name"]), len(diffs), rows))
