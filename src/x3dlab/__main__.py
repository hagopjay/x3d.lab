"""python -m x3dlab <command>

  ci      the regression gate (see x3dlab.regress): run the ci preset, compare with the committed baseline
  fuzz    a standalone differential fuzz run: python -m x3dlab fuzz --n 200 --seed 7 --oracles fork,lxml-xsd
  pins    print the pinned versions every result is tied to
"""

import argparse
import json
import sys


def _fuzz(argv):
    ap = argparse.ArgumentParser(prog="python -m x3dlab fuzz")
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--oracles", default="fork,lxml-xsd")
    ap.add_argument("--out", default="fuzz_out")
    ap.add_argument("--no-minimize", action="store_true")
    a = ap.parse_args(argv)
    import x3dlab
    from x3dlab import fuzz, research
    names = [o.strip() for o in a.oracles.split(",") if o.strip()]
    lab = x3dlab.up(tunnel="none", panel=False, log_level="WARN")
    try:
        if "consortium" in names:
            lab.start_upstream()
        if "x_ite" in names:
            lab.start_browser()
        orcs, _ = research.oracles(lab, names)
        fr = fuzz.run(fuzz.FuzzConfig(n=a.n, seed=a.seed, minimize=not a.no_minimize), orcs)
        path = fr.write(a.out)
    finally:
        lab.down()
    print(json.dumps(fr.confusion(), indent=1))
    print("%d buckets · %s" % (len(fr.buckets), path))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv.pop(0) if argv else "help"
    if cmd == "ci":
        from x3dlab import regress
        return regress.main(argv)
    if cmd == "fuzz":
        return _fuzz(argv)
    if cmd == "pins":
        from x3dlab import pins
        print(json.dumps(pins.summary(), indent=1))
        return 0
    print(__doc__)
    return 0 if cmd in ("help", "-h", "--help") else 2


if __name__ == "__main__":
    sys.exit(main())
