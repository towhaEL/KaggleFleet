"""
Entry point for running KaggleFleet directly via:
    python -m kaggle_fleet ...   (or the `kfleet` console script)
"""

from __future__ import annotations

import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main())
