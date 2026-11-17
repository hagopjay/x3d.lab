import os, sys
B = os.environ["LABDEMO"]          # scratch: engine, patched uerun, fixtures, receipt
for p in (B + "/proto", B + "/engine", B + "/lab_copy"):
    sys.path.insert(0, p)
