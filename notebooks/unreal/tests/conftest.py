import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # notebooks/unreal: the x3due package
sys.path.insert(0, str(HERE / "fixtures"))    # samples
