# Traceability: every number in a run card should name the log line it came from

Status: a plan and a tested prototype. Nothing here is wired into the lab, the notebooks or CI yet.
Read at x3d.lab `a7af249`. Grades: Confirmed = read in the code or run; Likely = follows from code read, not run on real data.

## The idea in one paragraph

The method comes from Umair Ali Khan's article "How I Added Traceability in Structured Data Extraction with AI" and the MIT-licensed
GAIK toolkit. The writer of a value copies the exact quote it came from. Plain code then checks three things with no model and no
network: FIND (is the quote in the source), COMPARE (is the value in the quote), LOCATE (line and column). The result is one of four
verdicts, `verified`, `reworded`, `check`, `unchecked`, and a verified quote caps a claim at Confirmed, never above.
[x3d.ML](https://github.com/hagopjay/x3d.ML) holds an independent implementation, `src/x3dtrace.py` (standard library only, sha256
prefix `dcba70d7` at the time of writing), and records its results in the same hash chain as `x3dprov`.

For this repo the "source" is not a prompt: it is the log of a run. The lab's standing rule is that every render claim names the patch
and the rig it was taken on. A card that stores `driver = "580.82.07"` states the rule; a card that stores the log line beside it
makes the rule checkable by anyone who holds the log.

## What a card holds today (Confirmed)

None of the logs behind a card is committed; they live on Drive (`DRIVE_DIR`) or in `WORK/out`.

| Card | Written by | Numbers it holds |
|---|---|---|
| Unreal run card | `notebooks/unreal/src/07_unreal.py` (`CARD`, `CARD["renders"]`), `x3due/report.py` `run_card` | `renders[scene].proof`, `stills[].seconds`, `seconds`, `gpu`, `attempts`, `smoke[name].proof` |
| Tier-1 card | `notebooks/unreal/colab_g4/tier1_proof_cells.py` | FLIP, SSIM, K, `peak_vram_gb`, `xite`, `rig` |
| Stress card | `src/x3dlab/stress.py` `write_card` | `pins`, `runtime`, `rows`, `drift`, `renders` |

`report.run_card` adds the environment and writes JSON; it does not look at the numbers.

Where each number comes from decides what can be said about it:

| Number | Source | Class | A quote can verify it? |
|---|---|---|---|
| `proof.device`, `vendor_id`, `api`, `driver`, `shader_platform`, `engine_version` | one log line each, by regex in `uerun.rhi_proof` | log quote | yes |
| `stills[].seconds` | the line `X3DLAB: wrote <job> (<n> files, <s> s)` written by `x3dlab_render.py` | log quote of a derived number | yes |
| `proof.verdict`, `why`, `vulkan`, `null_rhi`, `software`, `ray_tracing` | a rule over several lines | derived | no; the rule can be re-run on the log |
| `first_render_seconds` | difference of two log timestamps | derived from two quotes | two quotes |
| `seconds` (whole run) | wall clock around the process | not in the log | no |
| `gpu`, `gpu_series`, `peak_vram_gb` | sampled from `nvidia-smi` | sampled | no; keep the sample file |
| FLIP, SSIM, K | computed from pictures | recompute | no; store the inputs' hashes and re-run |
| `lab_commit` | `git rev-parse --short HEAD` | command output | no |

Two further facts matter for the rule about naming the patch: `pins.toml` holds X_ITE 16.7.0 while the x3d.demo site vendors 16.6.0
(two pins, two different truths), and `proof.engine_version` is read from a log line the card does not name.

## The prototype (tested on synthetic logs)

`docs/traceability/prototype/` holds three things; read its README to run them.

1. **`uerun-evidence.diff`**: 25 added lines in `notebooks/unreal/x3due/uerun.py`. `rhi_proof` also returns
   `evidence = {field: {line, text}}`, and a new `wrote_lines(text)` parses the `X3DLAB: wrote` lines. No existing return value changes.
   One name clash was found on the way: `rhi_proof` already binds `hit` further down, so the helper is called `first_hit`.
2. **`receipt.py`**: `verify_card(card, logs)` turns every value that carries evidence into a claim `{field, value, quote, source}` and runs
   the engine under the strict policy. A value with no evidence is a claim with an empty quote: `unchecked`, counted against coverage, so a
   card that lost its evidence drops coverage instead of turning red. `gate(report, floor)` fails on any `check` and on coverage below the
   floor. The CLI `python -m x3dlab receipt <card> --logs <dir>` exits 1 on failure and writes `<card>.trace.json`.
3. **`tests/` and `mutation_check.py`**: 28 tests plus one expected failure, and 15 exact-string mutants of `receipt.py` and the patched
   `uerun.py`, each of which must make a test fail.

Results (Confirmed, run in a scratch copy against the engine `dcba70d7`, Python 3.13): `28 passed, 1 xfailed`; `15 of 15 real mutants
killed` (an identity control stays alive, as it should). The fixtures are the lab's own `UE_LOG_VULKAN_OK` plus a few written for the
tests. No real G4 log was available, so how the regexes behave on a real log is unproven (Suspected for any log that differs from the
fixture).

### What `verified` does and does not mean here

* It means the card and the log agree on the value, the quote is an unbroken run of tokens in the log, and the card was not edited
  afterwards. Edit the card's value, edit a log line, or swap two evidence lines, and the verdict turns `check`.
* It does not mean the regex picked the right line. A log with two adapters verifies the first `DeviceName`; a `driver` taken from a
  second `Driver Version` line verifies. `test_verified_is_not_the_right_line` pins this. A `path`, prefix or suffix hint in the claim
  disambiguates; the strict policy turns an ambiguous quote into `check`.
* It cannot reach a number that never came from a log line (the lower rows of the table above).

### Versions are strings

An earlier engine read a dotted triple such as `16.7.0` as a date, so `version = "7.16.0"` verified it. The engine in x3d.ML now compares
a triple with no four-digit year as text. It still does not split the `v` of `v16.7.0`, so a correct claim about `X_ITE v16.7.0` reads
`check`; `receipt.version_in_quote` decides version fields until the engine has a `version` type (an `xfail` test records this and turns red
when it is fixed).

## Proposals, ranked

Value 1 to 5; size S or M. P1 and P2 are built in the prototype; the rest are not.

| id | what | where | size | value |
|---|---|---|---|---|
| P1 | Evidence in `rhi_proof` and `wrote_lines`, carried on the card | `uerun.py`; `07_unreal.py` (one key `wrote` beside `stills`); then `python tools/build_notebooks.py` so the generated notebook follows (CI checks this) | S | 5 |
| P2 | `x3dlab.receipt`, the CLI and the tests, with the engine reached through the pin | `src/x3dlab/receipt.py`, `tests/test_receipt.py`, one subparser in `__main__.py` | M | 5 |
| P3 | Save the Unreal logs (compressed) next to the card, with each log's sha-256 in the card | notebook cells, Drive layout | S | 5: without a log the receipt has nothing to check |
| P4 | A `version` type in the engine (string compare, strips a leading v) | x3d.ML `src/x3dtrace.py` | S | 4 |
| P5 | A RECOMPUTE class: the card stores the inputs of FLIP and SSIM (PNG sha-256, tool version) so a later run can recompute them | `tier1_proof_cells.py` | M | 3 |
| P6 | CI runs `notebooks/unreal/tests` | `.github/workflows/tests.yml`, or `testpaths` in `pyproject.toml` | S | 3 |
| P7 | A render claim must name the patch and the rig: the receipt fails when it lacks `engine_version`, `device`, `driver` or `lab_commit` | `receipt.py` | S | 3 |

How the engine reaches the lab: `tests.yml` already installs the pinned `x3d_mcp` fork. The fork's PR "scene_evidence" vendors the same
single file as `x3d_utils/x3dtrace.py`; once `pins.toml [x3d_mcp] ref` reaches a commit that has it (one PR per pin change, as the file
asks), the receipt needs no new dependency.

### The CI gap (Confirmed)

`.github/workflows/tests.yml` runs `pytest -q` at the repo root, and `pyproject.toml` sets `testpaths = ["tests"]`. No workflow names
`notebooks/unreal`, so the 25 modules under `notebooks/unreal/tests/` (the `uerun`, `pipeline`, render-script and Colab-cell tests) never run
in CI. The 59 tests that touch `rhi_proof` passed against the patched copy; the full 25 modules were not run, so whether they are offline
and need no GPU is unchecked (P6 starts there).

## Open questions

1. Should `receipt` be a CI gate on committed example cards, or only a notebook cell after a run?
2. Are the Unreal logs worth keeping (P3)? Without them the receipt step has nothing to check.
3. Credit wording for the method on public pages: the article and GAIK are credited in x3d.ML's `NOTICE-x3dtrace.md`.

## Sources

* Umair Ali Khan, "How I Added Traceability in Structured Data Extraction with AI" (Medium).
* GAIK toolkit, `github.com/GAIK-project/gaik-toolkit`, MIT, commit `394a94b`: the method and test vectors; the engine here is independent.
* x3d.ML `docs/TRACE.md` and `NOTICE-x3dtrace.md`.
