"""Receipt check on SYNTHETIC log lines (x3d.lab tests/fixtures/samples.py: Unreal log formats written from Epic forum posts,
not captured on the G4). They test the machinery; they say nothing about any real run."""
import copy, json
import pytest
import samples
import uerun_patched as uerun
import receipt, x3dtrace as T

LOG = samples.UE_LOG_VULKAN_OK
LOGNAME = "render_calib_high.0.log"


def make_card(log=LOG):
    proof = uerun.rhi_proof(log)
    wrote = uerun.wrote_lines(log)
    return {"renders": {"calib": {"log": LOGNAME, "proof": proof, "wrote": wrote,
                                  "stills": [{"job": "stilllife__CA_Camera__high", "seconds": 61.0}]}}}


def run(card, log=LOG, **kw):
    rep = receipt.verify_card(card, {LOGNAME: log}, **kw)
    return rep, {it.field.split(".")[-1] if "stills" not in it.field else "still_seconds": it for it in rep.items}


def test_evidence_lines_are_the_lines_the_values_came_from():
    proof = uerun.rhi_proof(LOG)
    lines = LOG.splitlines()
    assert set(proof["evidence"]) == {"device", "vendor_id", "api", "driver", "shader_platform", "engine_version"}
    for k, ev in proof["evidence"].items():
        assert lines[ev["line"] - 1].strip() == ev["text"], k
        assert str(proof[k]) in ev["text"], k


def test_wrote_lines_carry_job_files_seconds_and_the_line_number():
    w = uerun.wrote_lines(LOG)
    assert len(w) == 1 and w[0]["job"] == "stilllife__CA_Camera__high" and w[0]["files"] == 2 and w[0]["seconds"] == 61.0
    assert LOG.splitlines()[w[0]["line"] - 1].strip() == w[0]["text"]


def test_honest_card_all_verified_full_coverage():
    rep, by = run(make_card())
    assert rep.summary()["verified"] == 7 and rep.summary()["total"] == 7
    assert rep.coverage == 1.0 and rep.verified_coverage == 1.0
    ok, why = receipt.gate(rep)
    assert ok, why
    assert by["driver"].locus["line"] == 8                 # LOCATE: the line number comes back
    assert by["still_seconds"].locus["line"] == 11


@pytest.mark.parametrize("path,new,verdicts", [
    (("proof", "driver"), "580.82.7", ("reworded", "check")),        # one digit dropped
    (("proof", "driver"), "580.82.08", ("reworded", "check")),
    (("proof", "api"), "1.4.31", ("check",)),
    (("proof", "engine_version"), "5.8.4-45123457", ("reworded", "check")),
    (("proof", "device"), "NVIDIA RTX PRO 6000 Blackwell Server Edition", ("check", "reworded")),
])
def test_edited_card_value_is_flagged(path, new, verdicts):
    card = make_card()
    card["renders"]["calib"][path[0]][path[1]] = new
    rep, by = run(card)
    assert by[path[1]].verdict in verdicts, by[path[1]].verdict
    ok, why = receipt.gate(rep)
    assert not ok and any(path[1] in w for w in why)


def test_edited_still_seconds_is_flagged():
    card = make_card()
    card["renders"]["calib"]["stills"][0]["seconds"] = 16.0
    rep, by = run(card)
    assert by["still_seconds"].verdict == "check"


def test_edited_log_is_flagged_and_card_unchanged():
    log2 = LOG.replace("Driver Version: 580.82.07 (internal:580.82.07", "Driver Version: 580.82.08 (internal:580.82.08")
    rep, by = run(make_card(), log=log2)
    assert by["driver"].verdict == "check" and by["driver"].status == "not_found"


def test_missing_evidence_lowers_coverage_not_verdicts():
    card = make_card()
    del card["renders"]["calib"]["proof"]["evidence"]["driver"]
    rep, by = run(card)
    assert by["driver"].verdict == "unchecked"
    assert rep.coverage == pytest.approx(6 / 7, abs=1e-6)
    assert not receipt.gate(rep)[0]
    assert receipt.gate(rep, floor=0.8)[0]


def test_missing_log_is_unchecked_not_check():
    rep = receipt.verify_card(make_card(), {})
    assert all(it.verdict == "unchecked" for it in rep.items) and rep.coverage == 0.0


def test_swapped_evidence_lines_are_flagged():
    card = make_card()
    ev = card["renders"]["calib"]["proof"]["evidence"]
    ev["driver"], ev["api"] = ev["api"], ev["driver"]
    rep, by = run(card)
    assert by["driver"].verdict in ("check", "reworded") and by["api"].verdict in ("check", "reworded")


def test_verified_is_not_the_right_line():
    """The article's stated limit, kept as a test: a quote from another line that also holds the value passes."""
    card = make_card()
    wrong = next(l for l in LOG.splitlines() if "Built with Vulkan header version 1.4.312" in l)
    card["renders"]["calib"]["proof"]["evidence"]["api"] = {"line": 4, "text": wrong.strip()}
    rep, by = run(card)
    assert by["api"].verdict == "verified"            # wrong place, right number: FIND and COMPARE cannot see it
    assert by["api"].locus["line"] == 4                # but LOCATE names the line, so a reader can


def test_report_is_deterministic():
    a = run(make_card())[0].sha256()
    b = run(make_card())[0].sha256()
    assert a == b


def test_cli_exit_codes(tmp_path):
    logs = tmp_path / "logs"; logs.mkdir()
    (logs / LOGNAME).write_text(LOG)
    good = tmp_path / "card.json"; good.write_text(json.dumps(make_card()))
    assert receipt.main([str(good), "--logs", str(logs)]) == 0
    assert (tmp_path / "card.trace.json").exists()
    bad_card = make_card(); bad_card["renders"]["calib"]["proof"]["driver"] = "580.82.08"
    bad = tmp_path / "bad.json"; bad.write_text(json.dumps(bad_card))
    assert receipt.main([str(bad), "--logs", str(logs)]) == 1


@pytest.mark.parametrize("value,line,expect", [
    ("5.8.4", "LogInit: Engine Version: 5.8.4-58902052+++UE5+Release-5.8", "verified"),     # a build id after the version is fine
    ("5.8.4", "LogInit: Engine Version: 4.8.5-58902052", "check"),                          # permuted parts: x3dtrace 0.1 says verified (date)
    ("5.8.4", "LogInit: Engine Version: 5.8.40-1", "check"),
    ("5.8.4", "LogInit: Engine Version: 15.8.4-1", "check"),
    ("5.8.4", "LogInit: Engine Version: 5.8.4.1", "check"),
    ("5.8.4", "LogInit: Engine Version: 1.5.8.4", "check"),
    ("16.7.0", "browser X_ITE v16.7.0 in Chromium", "verified"),                           # x3dtrace 0.1 says check (v glued to 16)
    ("16.7.0", "browser X_ITE v7.16.0 in Chromium", "check"),
    ("580.82.07", "LogRHI: Driver Version: 580.82.07 (internal:580.82.07, unified:)", "verified"),
    ("580.82.07", "driverVersion = 580.82.7.0", "check"),                                  # Vulkan's packed form is not the same string
    ("5.2.3", "pip install bpy==3.2.5", "check"),
])
def test_versions_are_strings(value, line, expect):
    card = {"renders": {"x": {"log": "x.log", "proof": {"engine_version": value, "evidence": {"engine_version": {"line": 1, "text": line}}}}}}
    rep = receipt.verify_card(card, {"x.log": line + "\n"})
    assert rep.items[0].verdict == expect, rep.items[0].verdict


@pytest.mark.xfail(strict=True, reason="the x3dtrace lexer glues the v of 'v16.7.0' to the digits: a correct version claim reads 'check'")
def test_version_with_v_prefix_is_verified():
    src = T.Source("s", "browser X_ITE v16.7.0 in Chromium", "text")
    it = T.check_claims([{"field": "x_ite", "value": "16.7.0", "quote": "X_ITE v16.7.0", "source": "s", "ftype": "identifier"}], [src]).items[0]
    assert it.verdict == "verified"


def test_version_is_not_read_as_a_date():
    src = T.Source("s", "UE released 5 Aug 04 for Linux", "text")
    it = T.check_claims([{"field": "engine_version", "value": "5.8.4", "quote": "released 5 Aug 04", "source": "s", "ftype": "identifier"}], [src]).items[0]
    assert it.verdict != "verified"
