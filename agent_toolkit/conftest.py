"""Makes the scripts in this folder importable as plain modules from
tests/, regardless of where pytest is invoked from. These scripts aren't
a package (no __init__.py, no pip install) -- they're meant to be run
directly with `python script.py`, so tests need this to import them.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
