"""Receipt check: every number a run card reports is tied to a quoted log line, and a plain-code check says so.

    from x3dlab import receipt
    rep = receipt.verify_card(card, logs={"render_calib.0.log": text})   # no model, no network
    rep.summary()   # verified / check / reworded / unchecked, coverage
    receipt.main(["run_card_<RUN_ID>.json", "--logs", "<run folder>/logs"])   # exit 1 on a check

The card's `proof` blocks carry `evidence` (uerun.rhi_proof): for each value, the log line it was read from. A claim is
{field, value, quote = that line, source = the log}. x3dtrace then runs FIND (is the line in the log, as an unbroken run of
tokens), COMPARE (is the value in the line; versions and identifiers as strings, numbers as numbers) and LOCATE (line,
column). What this proves: the card and the log agree, and the card was not edited afterwards. What it cannot prove: that
the regex picked the right line (verified is not the right place), or anything about numbers that never came from a log
line (FLIP, SSIM, K, peak VRAM: they are computed from pictures or sampled; see RECOMPUTE in the design notes).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VERSIONY = ("driver", "api", "engine_version", "x_ite", "chromium")   # fields compared as identifiers, never as numbers or dates
PROOF_FIELDS = ("device", "vendor_id", "api", "driver", "shader_platform", "engine_version")
FAIL_ON = ("check",)          # the strict policy already reports a reworded value as check


def _engine():
    try:
        import x3dtrace
    except ImportError as exc:   # pragma: no cover
        raise RuntimeError("x3dtrace is not installed: pin it in pins.toml [x3d_ml] (single stdlib file src/x3dtrace.py)") from exc
    return x3dtrace


def proof_claims(proof: dict, prefix: str, source: str) -> tuple[list, list]:
    """(claims, expected_fields) from one rhi_proof dict. A field with a value but no evidence is expected and uncovered."""
    claims, expected = [], []
    ev = (proof or {}).get("evidence") or {}
    for k in PROOF_FIELDS:
        if (proof or {}).get(k) in (None, ""):
            continue
        fld = "%s.%s" % (prefix, k)
        expected.append(fld)
        claims.append({"field": fld, "value": proof[k], "quote": ev[k]["text"] if k in ev else "", "source": source,
                       "ftype": "version" if k in VERSIONY else "", "line": ev[k].get("line", 0) if k in ev else 0})
    return claims, expected


def still_claims(stills: list, wrote: list, prefix: str, source: str) -> tuple[list, list]:
    """Each still's `seconds` against the `X3DLAB: wrote <job> (<n> files, <s> s)` line that reported it."""
    by_job = {w["job"]: w for w in wrote or []}
    claims, expected = [], []
    for s in stills or []:
        if s.get("seconds") is None:
            continue
        fld = "%s.stills[%s].seconds" % (prefix, s.get("job"))
        expected.append(fld)
        w = by_job.get(s.get("job"))
        claims.append({"field": fld, "value": s["seconds"], "quote": w["text"] if w else "", "source": source, "line": w["line"] if w else 0})
    return claims, expected


def collect(card: dict) -> tuple[list, list, list]:
    """Walk a run card: (claims, expected fields, sources wanted as [(source id, log file name)])."""
    claims, expected, wanted = [], [], []
    for scene, r in sorted((card.get("renders") or {}).items()):
        log = r.get("log") or ""
        pre = "renders.%s" % scene
        c, e = proof_claims(r.get("proof"), pre + ".proof", log)
        claims += c
        expected += e
        c, e = still_claims(r.get("stills"), r.get("wrote"), pre, log)
        claims += c
        expected += e
        if log:
            wanted.append((log, log))
    for name, s in sorted((card.get("smoke") or {}).items()):
        log = s.get("log") or ""
        c, e = proof_claims(s.get("proof"), "smoke.%s.proof" % name, log)
        claims += c
        expected += e
        if log:
            wanted.append((log, log))
    return claims, expected, sorted(set(wanted))


def version_in_quote(value: str, quote: str) -> bool:
    """A version is a string, not a number and not a date: `value` must stand in `quote` as a whole dotted token (an optional
    leading v, nothing alphanumeric or dotted-digit around it). 5.8.4 is in 5.8.4-58902052 and v5.8.4, not in 5.8.40, 15.8.4,
    5.8.4.1 or 4.8.5; 580.82.07 is not 580.82.7. The engine now compares a dotted triple with no four-digit year as text, but it
    does not split the v of v16.7.0 (a correct claim reads `check`), so versions are decided here until it has a `version` type."""
    return re.search(r"(?<![0-9A-Za-z.])v?%s(?![0-9A-Za-z]|\.\d)" % re.escape(value.strip()), quote) is not None


def verify_card(card: dict, logs: dict, policy=None):
    """logs: {log file name: text}. Returns an x3dtrace TraceReport (items carry verdict, locus, selectors)."""
    T = _engine()
    claims, expected, wanted = collect(card)
    sources = [T.Source(sid, logs[name], "text") for sid, name in wanted if name in logs]
    have = {s.id for s in sources}
    for c in claims:                       # a claim whose log was not supplied has no source: unchecked, not "check"
        if c["source"] not in have:
            c["quote"] = ""
    if not sources:
        sources = [T.Source("none", "", "text")]
    rep = T.check_claims(claims, sources, policy or T.TracePolicy.strict(), expected_fields=expected)
    for c, it in zip(claims, rep.items):
        if c.get("ftype") == "version" and it.status in ("found", "moved"):
            ok = version_in_quote(str(c["value"]), c["quote"])
            it.value_status, it.derivation = ("match", ["version"]) if ok else ("mismatch", [])
            it.verdict = "verified" if ok else "check"
    return rep


def gate(report, floor: float = 1.0) -> tuple[bool, list]:
    """(ok, reasons). Fails on any check/reworded verdict, or coverage below the floor (default: every field)."""
    why = ["%s: %s" % (it.field, it.verdict) for it in report.items if it.verdict in FAIL_ON]
    cov = report.coverage
    if cov is not None and cov < floor:
        why.append("coverage %.3f < %.3f" % (cov, floor))
    return (not why), why


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m x3dlab receipt")
    ap.add_argument("card")
    ap.add_argument("--logs", required=True, help="folder holding the logs the card names")
    ap.add_argument("--floor", type=float, default=1.0, help="least share of expected fields that must carry a quote")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    card = json.loads(Path(a.card).read_text(encoding="utf-8"))
    logs = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in Path(a.logs).glob("*.log")}
    rep = verify_card(card, logs)
    out = Path(a.out or re.sub(r"\.json$", "", a.card) + ".trace.json")
    out.write_text(rep.canonical(), encoding="utf-8")
    sm = rep.summary()
    print("receipt: %(verified)d verified, %(check)d check, %(reworded)d reworded, %(unchecked)d unchecked of %(total)d; "
          "coverage %(coverage_ppm)s ppm" % sm)
    ok, why = gate(rep, a.floor)
    for w in why:
        print("  FAIL", w)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
