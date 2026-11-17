# Receipt prototype

A tested sketch of the plan in [../../TRACEABILITY.md](../../TRACEABILITY.md). Not imported by the lab, not collected by pytest
(`testpaths = ["tests"]`), not run in CI.

| File | What it is |
|---|---|
| `uerun-evidence.diff` | patch for `notebooks/unreal/x3due/uerun.py`: `rhi_proof` records the log line each value came from; new `wrote_lines` |
| `receipt.py` | would become `src/x3dlab/receipt.py`: `verify_card`, `gate`, a CLI |
| `tests/` | 28 tests on synthetic Unreal log lines, one expected failure (the engine does not split the `v` of `v16.7.0`) |
| `mutation_check.py` | 15 exact-string mutants of `receipt.py` and the patched `uerun.py`; each must make a test fail |
| `run.py` | rebuilds the scratch layout from a lab checkout and runs the tests |

## Run it

```
python3 -I docs/traceability/prototype/run.py --lab . --engine <path to x3dtrace.py> [--mutants]
```

`x3dtrace.py` is the single standard-library file `src/x3dtrace.py` of hagopjay/x3d.ML. `run.py` copies `uerun.py` and
`tests/fixtures/samples.py` into a temporary directory and applies the diff there; nothing under the checkout changes. It needs `git`
and `pytest`.

Last result (Python 3.13, engine sha256 prefix `dcba70d7`): `28 passed, 1 xfailed`; `15 of 15 real mutants killed`.

## Limits

* The fixtures are synthetic. They test the machinery, not any real run.
* `verified` says the card and the log agree on a value; it does not say the regex chose the right line.
* FLIP, SSIM, K, wall-clock seconds and sampled GPU numbers have no log line; see the table in TRACEABILITY.md.
