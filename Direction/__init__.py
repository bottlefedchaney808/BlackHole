"""Direction -- 5-input market-direction signal package (ThetaData-backed).

See Direction/README.md for the full signal catalog, conviction rules, and
Tools/ plugin wiring. `generate` is re-exported here so `from Direction
import generate` works without reaching into the submodule directly.
"""
from .signal_generator import generate

__all__ = ["generate"]
