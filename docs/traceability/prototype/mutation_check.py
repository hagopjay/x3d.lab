"""Exact-string mutants of receipt.py and the patched uerun.py; each must make at least one test fail. Run on COPIES."""
import os, shutil, subprocess, sys, tempfile
B = sys.argv[1]
MUT = [
 ("receipt.py", "import argparse", "import argparse", "CONTROL: identity mutant (must stay ALIVE)"),
 ("receipt.py", 'FAIL_ON = ("check",)', 'FAIL_ON = ()', "no verdict fails the gate"),
 ("receipt.py", 'return (not why), why', 'return True, why', "gate always passes"),
 ("receipt.py", 'if cov is not None and cov < floor:', 'if False:', "coverage floor ignored"),
 ("receipt.py", '"quote": ev[k]["text"] if k in ev else ""', '"quote": ev[k]["text"] if k in ev else proof[k] and str(proof[k])', "missing evidence replaced by the value itself"),
 ("receipt.py", 'ftype": "version" if k in VERSIONY else ""', 'ftype": ""', "version fields not typed"),
 ("receipt.py", '(?<![0-9A-Za-z.])v?', '(?<![0-9A-Za-z])v?', "a dot before the version allowed (15.8.4 holds 5.8.4)"),
 ("receipt.py", '(?![0-9A-Za-z]|\\.\\d)', '(?![0-9A-Za-z])', "a further .digit after the version allowed (5.8.4.1)"),
 ("receipt.py", 'if c.get("ftype") == "version" and it.status in ("found", "moved"):', 'if False:', "engine date reading decides versions"),
 ("receipt.py", 'if c["source"] not in have:\n            c["quote"] = ""', 'if False:\n            c["quote"] = ""', "claim without its log still checked"),
 ("receipt.py", 'T.TracePolicy.strict()', 'T.TracePolicy()', "default policy instead of strict"),
 ("receipt.py", 'by_job = {w["job"]: w for w in wrote or []}', 'by_job = {}', "stills never matched to their line"),
 ("uerun_patched.py", '"line": h[0], "text": h[1].strip()[:400]}', '"line": h[0] + 1, "text": h[1].strip()[:400]}', "evidence line number off by one"),
 ("uerun_patched.py", '("driver", "driver"),', '("driver", "api"),', "driver evidence taken from the api line"),
 ("uerun_patched.py", 'for n, l in enumerate(text.splitlines(), 1):\n        m = WROTE.search(l)', 'for n, l in enumerate(text.splitlines(), 0):\n        m = WROTE.search(l)', "wrote line numbers 0-based"),
 ("uerun_patched.py", 'float(m.group(3))', 'float(m.group(2))', "wrote seconds read from the file count"),
]
def tests(d):
    env = dict(os.environ, LABDEMO=d)
    return subprocess.run([sys.executable, "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", "-c", "/dev/null", "--rootdir", d + "/proto", d + "/proto/tests"],
                          env=env, capture_output=True, text=True).returncode
if tests(B) != 0:
    sys.exit("baseline is red: refusing to run mutants")
killed = 0
for fn, old, new, label in MUT:
    d = tempfile.mkdtemp(prefix="mut-")
    shutil.copytree(B + "/proto", d + "/proto"); shutil.copytree(B + "/engine", d + "/engine"); shutil.copytree(B + "/lab_copy", d + "/lab_copy")
    p = d + "/proto/" + fn
    s = open(p).read()
    assert s.count(old) == 1, (fn, old, s.count(old))
    open(p, "w").write(s.replace(old, new))
    env = dict(os.environ, LABDEMO=d)
    # conftest adds LABDEMO/proto first, then the engine and fixtures from the real scratch dir
    r = subprocess.run([sys.executable, "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", "-c", "/dev/null", "--rootdir", d + "/proto", d + "/proto/tests"],
                       env=env, capture_output=True, text=True)
    dead = r.returncode != 0
    killed += dead
    print("%-6s %s" % ("KILLED" if dead else "ALIVE", label))
    shutil.rmtree(d, ignore_errors=True)
print("%d of %d real mutants killed (control excluded)" % (killed, len(MUT) - 1))
