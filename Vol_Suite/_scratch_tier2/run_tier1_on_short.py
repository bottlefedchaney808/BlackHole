#!/usr/bin/env python3
"""Run the unchanged corrected Tier 1 driver against the short-DTE scratch seed data."""
import os
import sys

# Ensure Vol_Suite is on sys.path so `import expiry_book_exposure` works.
# __file__ is Vol_Suite/_scratch_tier2/run_tier1_on_short.py
# parent of __file__ = Vol_Suite/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Redirect the seed loader to the scratch dir BEFORE importing the driver module.
import expiry_book_exposure as ebe
scratch_dir = os.path.dirname(os.path.abspath(__file__))
ebe._SEED_DIR = scratch_dir

# Now import the unchanged driver and run it.
import run_expiry_tier1
run_expiry_tier1.main()
