# Notebook 07 on the Colab G4: fix the blown-out stills. Paste after step 9 (or after stopping it).
# Uses only notebook 07's own globals: LAB, IMP, SCN, PLAN, LOOK, MAX_VIEWPOINTS, pipeline, staging. Never overwrites a step 9 still
# (new job names: *_bracket, *_keeper).

#@title 9X · Exposure bracket: 6 exposures per scene, 1 viewpoint, 1280x720 Lumen, one Unreal start per scene
# Weight: about 1.5 to 2 min per scene.
import numpy as np
from PIL import Image, ImageDraw
from IPython.display import display
EV0 = float(getattr(LAB.mapping, "ev100", 0.0))
OFFSETS = [0, -2, -4, -6, -8, -10]
def lstats(png):
    a = np.asarray(Image.open(png).convert("RGB"), dtype=np.float32) / 255.0
    y = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    return {"median": float(np.median(y)), "clip": float((y > 0.98).mean()), "black": float((y < 0.02).mean())}
def badness(s):   # want a mid-grey median, little clipped white, not crushed to black
    return abs(s["median"] - 0.38) + 2.5 * max(0.0, s["clip"] - 0.02) + max(0.0, s["black"] - 0.25)
BEST, BRK = {}, {}
for n, res in IMP.items():
    if n == "calib" or n.endswith("_usd") or not res["report"].get("ok"):
        continue
    cams = staging.pick(sorted(res["report"]["sequences"]), SCN.get(n, {}).get("framing", {}), 1)
    grades = {"ev_m%d" % -o: {"post": {"auto_exposure_bias": EV0 + o}} for o in OFFSETS}
    out = pipeline.render_grades(LAB, n, cams, "preview", PLAN["ladder"]["preview"], grades, look=LOOK, res_override=(1280, 720), label="bracket")
    rows = []
    for o in OFFSETS:
        png = out["stills"].get(("ev_m%d" % -o, cams[0]))
        if png:
            s = lstats(png); rows.append((badness(s), o, png, s))
    if not rows:
        print(f"{n}: no bracket stills\n{out.get('excerpt', '')[-1500:]}"); continue
    BRK[n] = sorted(rows, key=lambda r: r[1], reverse=True)
    best = min(rows); BEST[n] = best[1]
    print(f"{n} ({cams[0]}): " + " | ".join(f"EV{o:+d} med {s['median']:.2f} clip {s['clip']*100:.0f}%" for _, o, _, s in BRK[n]) + f"   → best EV{best[1]:+d}")
    tw = 427; th = 240; sheet = Image.new("RGB", (tw * len(BRK[n]), th + 22), (12, 12, 14)); d = ImageDraw.Draw(sheet)
    for i, (_, o, png, s) in enumerate(BRK[n]):
        sheet.paste(Image.open(png).convert("RGB").resize((tw, th)), (i * tw, 22))
        d.text((i * tw + 6, 5), f"EV {o:+d}{'  ← pick' if o == best[1] else ''}", fill=(255, 220, 120) if o == best[1] else (220, 220, 220))
    display(sheet)
print("BEST =", BEST, "(edit it by hand if another tile looks better, e.g. BEST['odyssey'] = -6)")

#@title 9Y · Keepers: every scene at its best exposure, 4K, all viewpoints, filmic bloom + light vignette
# FINAL = "ultra_lumen": HW Lumen hit lighting, Likely 1 to 3 min per still.
# FINAL = "ultra": the path tracer, also renders EV-1 and EV+1 (Lumen and path tracer can differ by about one stop); far slower.
FINAL = "ultra_lumen"  #@param ["ultra_lumen", "ultra", "high"]
KEEP = {}
for n, off in BEST.items():
    res = IMP[n]
    cams = staging.pick(sorted(res["report"]["sequences"]), SCN.get(n, {}).get("framing", {}), MAX_VIEWPOINTS)
    evs = [off - 1, off, off + 1] if FINAL == "ultra" else [off]
    grades = {"keep_m%d" % -e: {"post": {"auto_exposure_bias": EV0 + e, "bloom_intensity": 0.5, "bloom_threshold": 1.0, "vignette_intensity": 0.25}} for e in evs}
    out = pipeline.render_grades(LAB, n, cams, FINAL, PLAN["ladder"][FINAL], grades, look=LOOK, res_override=(3840, 2160), label="keeper")
    KEEP[n] = out["stills"]
    gpu = out.get("gpu") or {}
    print(f"{n}: {sum(1 for v in out['stills'].values() if v)}/{len(out['stills'])} stills, {out.get('seconds', 0):.0f} s, peak VRAM {(gpu.get('peak_mem_mb') or 0)/1024:.1f} GB")
    for (g, cam), png in out["stills"].items():
        if png:
            print("  ", g, cam, png); display(Image.open(png).convert("RGB").resize((1280, 720)))
CARD["keepers"] = {n: {f"{g}|{c}": p for (g, c), p in v.items()} for n, v in KEEP.items()}
