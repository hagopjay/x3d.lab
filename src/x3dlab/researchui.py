"""Notebook views for the 03 notebook: fuzz scores and buckets, the oracle matrix, the cross-renderer gallery, scale
curves, trace links, the regression gate. Plain HTML (works in Colab, Jupyter and exported notebooks)."""

from __future__ import annotations

import html as H
import json

from .ui import show

V = {"accept": ("#1d6b3a", "✓"), "reject": ("#c62828", "✗"), "crash": ("#6a1b9a", "💥"), "skip": ("#888", "–")}


def e(s) -> str:
    return H.escape(str(s))


def _pct(x):
    return "–" if x is None else "%.0f%%" % (100 * x)


def fuzz_html(fr, limit: int = 40) -> str:
    card = fr.card()
    conf = "".join("<tr><td><b>%s</b></td><td>%s</td><td>%s</td><td>%d</td><td>%d</td><td>%d</td><td>%s</td></tr>" % (
        e(n), _pct(c.get("recall")), _pct(c.get("specificity")), c.get("missed", 0), c.get("false_alarm", 0), c.get("crash", 0),
        c.get("mean_ms")) for n, c in card["confusion"].items())
    ag = " · ".join("%s κ=%.2f" % (e(k), v["kappa"]) for k, v in card["agreement"].items())
    reprs = {b.key: b.repro for b in fr.buckets.values()}
    col = {"crash": "#6a1b9a", "missed": "#9a5b00", "false_alarm": "#c62828"}
    bk = "".join("<details><summary><b style='color:%s'>%s</b> · %s · <code>%s</code> · %s · ×%d%s</summary><pre style='white-space:pre-wrap;"
                 "font-size:11px;background:#f6f6f6;padding:6px'>%s</pre></details>" % (
                     col.get(b["kind"], "#333"), e(b["kind"]), e(b["oracle"]), e(b["op"]), e(b["sig"][:90]), b["count"],
                     " · %s→%s B" % (b["orig_bytes"], b["repro_bytes"]) if b.get("repro_bytes") else "", e(reprs.get(b["key"]) or "(not minimised)"))
                 for b in card["buckets"][:limit])
    return ("<div style='font:14px system-ui'><b>%d cases</b> · %d operators · %.0f s · seed %s</div>"
            "<table style='font-size:12.5px'><tr><th>oracle</th><th>recall</th><th>specificity</th><th>missed</th><th>false alarms</th>"
            "<th>crashes</th><th>mean ms</th></tr>%s</table><div style='font-size:12px;color:#555'>agreement: %s</div>"
            "<h4 style='margin:8px 0 2px'>%d buckets</h4>%s") % (card["cases"], len(card["ops"]), card["seconds"], card["config"]["seed"],
                                                               conf, ag, len(card["buckets"]), bk)


def show_fuzz(fr) -> None:
    show(fuzz_html(fr))


def operator_heatmap(fr) -> None:
    """Share of cases where each oracle did what was expected, per operator (matplotlib)."""
    import matplotlib.pyplot as plt
    import numpy as np
    t = fr.by_operator()
    ops, names = list(t), [o.name for o in fr.oracles]
    m = np.array([[t[op].get(n) if t[op].get(n) is not None else np.nan for n in names] for op in ops], dtype=float)
    fig, ax = plt.subplots(figsize=(1.6 + 1.1 * len(names), 0.32 * len(ops) + 1.2))
    im = ax.imshow(m, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(names)), names, rotation=20)
    ax.set_yticks(range(len(ops)), ops, fontsize=8)
    for i in range(len(ops)):
        for j in range(len(names)):
            if not np.isnan(m[i, j]):
                ax.text(j, i, "%.0f" % (100 * m[i, j]), ha="center", va="center", fontsize=7)
    ax.set_title("expected verdict, % of cases")
    fig.colorbar(im, ax=ax, fraction=0.03)
    plt.tight_layout()
    plt.show()


def matrix_html(run) -> str:
    m = run.research.get("matrix")
    if not m:
        return "<i>no matrix in this run</i>"
    names = m["oracles"]
    rows = []
    for c in m["cases"]:
        cells = m["cells"][c["id"]]
        tds = []
        for n in names:
            cell = cells.get(n)
            if not cell:
                tds.append("<td></td>")
                continue
            colr, mark = V.get(cell["v"], ("#333", "?"))
            ok = cell["v"] == c["expect"]
            tds.append("<td title='%s' style='text-align:center;color:%s;background:%s'>%s<span style='font-size:10px;color:#666'> %s</span></td>" % (
                e(cell["sig"]), colr, "#eef7ee" if ok else "#fdeeee", mark, e(cell["sig"].split(":")[0]) if cell["sig"] else ""))
        rows.append("<tr><td><code>%s</code></td><td style='color:#666'>%s</td><td>%s</td>%s</tr>" % (
            e(c["id"]), e(c["layer"]), e(c["expect"]), "".join(tds)))
    return ("<table style='font-size:12px;border-collapse:collapse'><tr><th align=left>case</th><th>layer</th><th>expect</th>%s</tr>%s</table>"
            "<div style='font-size:11.5px;color:#666'>✓ accepted · ✗ rejected (layer in grey) · green = as expected · hover a cell for the "
            "oracle's complaint</div>") % ("".join("<th>%s</th>" % e(n) for n in names), "".join(rows))


def show_matrix(run) -> None:
    show(matrix_html(run))


def crossrender_html(run, lab=None) -> str:
    items = run.research.get("crossrender") or []
    base = lab.dashboard_url() if lab is not None else ""
    figs = []
    for it in items:
        imgs = "".join("<img src='%s%s' style='width:32%%;margin-right:1%%;border-radius:4px' title='%s'>" % (e(base), e(it[k]), k)
                       for k in ("xite", "cycles", "overlay"))
        figs.append("<figure style='margin:6px 0'><figcaption style='font:12px system-ui'><b>%s</b> · IoU <b>%.3f</b> · X_ITE only %.1f%% · "
                    "Cycles only %.1f%%</figcaption>%s</figure>" % (e(it["case"]), it["iou"], 100 * it["xite_only"], 100 * it["cycles_only"], imgs))
    return ("<div style='font:12px system-ui;color:#555'>left X_ITE (WebGL, flat magenta background) · middle Cycles (transparent film) · "
            "right overlay: white both, <span style='color:#e63c3c'>red X_ITE only</span>, <span style='color:#28c8dc'>cyan Cycles only</span></div>"
            + ("".join(figs) or "<i>no cross-renderer results</i>"))


def show_crossrender(run, lab=None) -> None:
    show(crossrender_html(run, lab))


def plot_scale(run) -> None:
    import matplotlib.pyplot as plt
    rows = run.research.get("scale") or []
    if not rows:
        print("no scale results")
        return
    v = [r["vertices"] for r in rows]
    series = {"validate (ms)": [r.get("validate_ms") for r in rows],
              "X_ITE parse (ms)": [(r.get("xite") or {}).get("parse") for r in rows],
              "X_ITE first frames (ms)": [(r.get("xite") or {}).get("frame") for r in rows],
              "Blender import (ms)": [(r.get("blender_import_s") or 0) * 1000 or None for r in rows],
              "Cycles render (ms)": [((r.get("render") or {}).get("seconds") or 0) * 1000 or None for r in rows]}
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 3.6))
    for name, ys in series.items():
        pts = [(x, y) for x, y in zip(v, ys) if y]
        if pts:
            a.plot(*zip(*pts), marker="o", label=name)
    a.set_xscale("log"), a.set_yscale("log"), a.set_xlabel("vertices"), a.set_title("time"), a.legend(fontsize=8)
    heap = [(x, (r.get("xite") or {}).get("heap_mb")) for x, r in zip(v, rows) if (r.get("xite") or {}).get("heap_mb")]
    vram = [(x, (r.get("render") or {}).get("peak_mem_mb")) for x, r in zip(v, rows) if (r.get("render") or {}).get("peak_mem_mb")]
    if heap:
        b.plot(*zip(*heap), marker="o", label="X_ITE JS heap (MB)")
    if vram:
        b.plot(*zip(*vram), marker="s", label="GPU memory peak (MB)")
    b.set_xscale("log"), b.set_xlabel("vertices"), b.set_title("memory"), b.legend(fontsize=8)
    plt.tight_layout()
    plt.show()


def traces_html(run, lab=None) -> str:
    items = run.research.get("traces") or []
    base = lab.dashboard_url() if lab is not None else ""
    rows = "".join("<tr><td><b>%s</b></td><td><a href='%s%s' target=_blank download>trace.json</a></td><td>%s</td><td>%s</td><td>%s</td>"
                   "<td>%s</td></tr>" % (e(t["case"]), e(base), e(t["url"]), f"{t['events'] or 0:,}", t["tasks_over_50ms"],
                                         (t["long_tasks_ms"] or ["–"])[0], t["js_heap_mb"]) for t in items)
    return ("<table style='font-size:12.5px'><tr><th>scene</th><th>file</th><th>events</th><th>tasks &gt; 50 ms</th><th>longest ms</th>"
            "<th>JS heap MB</th></tr>%s</table><div style='font:12px system-ui;color:#555'>Open Chrome DevTools › <b>Performance</b> › "
            "<b>Load profile…</b> and pick a trace.json: the <b>x3d.lab</b> track group shows parse / replaceWorld / first frames next to "
            "X_ITE's own JavaScript and the GPU work.</div>") % rows


def show_traces(run, lab=None) -> None:
    show(traces_html(run, lab))


def show_gate(md: str) -> None:
    """The regression report (markdown) rendered in the notebook."""
    try:
        from IPython.display import Markdown, display
        display(Markdown(md))
    except Exception:
        print(md)


def oracle_table(lab, bl=None) -> None:
    from . import research
    names = ["fork", "consortium", "lxml-xsd", "x_ite", "copilot", "blender"]
    orcs, skipped = research.oracles(lab, names, bl)
    have = {o.name for o in orcs}
    why = dict(skipped)
    rows = "".join("<tr><td><b>%s</b></td><td style='color:%s'>%s</td><td style='color:#666'>%s</td></tr>" % (
        e(n), "#1d6b3a" if n in have else "#999", "ready" if n in have else "off", e(why.get(n, ""))) for n in names)
    show("<table style='font-size:12.5px'><tr><th align=left>oracle</th><th></th><th align=left></th></tr>%s</table>" % rows)


def card_json(run) -> str:
    return json.dumps(run.research, indent=1, default=str)[:20000]
