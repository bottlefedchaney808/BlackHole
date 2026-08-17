#!/usr/bin/env python3
"""Wrapper: run unchanged Tier 1 driver against scratch short-DTE data."""
import os
import sys

# Run from main repo Vol_Suite so its modules resolve.
sys.path.insert(0, r"C:\Users\bottl\FinancialDevelopment\Vol_Suite")

import expiry_book_exposure as ebe
scratch = r"C:\Users\bottl\FinancialDevelopment\.worktrees\dealer-exposure-dev\Vol_Suite\_scratch_tier2"
ebe._SEED_DIR = scratch
print(f"[wrapper] ebe._SEED_DIR -> {ebe._SEED_DIR}")
print(f"[wrapper] tickers found: {ebe.seed_corpus_tickers()}")

import run_expiry_tier1
run_expiry_tier1.main()
