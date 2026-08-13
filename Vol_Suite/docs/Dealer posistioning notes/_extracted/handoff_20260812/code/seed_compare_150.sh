#!/usr/bin/env bash
# SEQUENTIAL 4-arm seed comparison (150d) over a list of tickers.
# OI-by-day is the proxy-fragile route and cannot handle concurrency, so
# tickers run ONE at a time (standalone is clean). 60s cooldown between to let
# the proxy reset.
# Usage: bash seed_compare_150.sh [TICKER ...]
set -u
cd "$(dirname "$0")"
if [ "$#" -gt 0 ]; then
  TICKERS=("$@")
else
  TICKERS=(MSFT META GOOGL AMZN NFLX JPM)
fi
for T in "${TICKERS[@]}"; do
  echo "===== $T $(date -u +%H:%M:%S) ====="
  env -u PYTHONPATH -u VIRTUAL_ENV Financial_Dev_Env/bin/python3 \
    Vol_Suite/seed_flip_compare.py "$T" 150 > /tmp/seed_compare_${T}.log 2>&1
  echo "exit=$? $(date -u +%H:%M:%S)"
  echo "--- 60s cooldown (proxy reset) ---"
  sleep 60
done
echo "ALL DONE $(date -u +%H:%M:%S)"
