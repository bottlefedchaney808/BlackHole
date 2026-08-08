#!/usr/bin/env python3
# Direction.py (repo root)
"""Direction -- unified market-direction signal CLI (ThetaData).

Thin launcher for the Direction package's unified signal generator:

    python Direction.py NVDA
    python Direction.py --ticker SPY

Equivalent to: python -m Direction.signal_generator --ticker NVDA
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Direction.signal_generator import main

if __name__ == "__main__":
    raise SystemExit(main())
