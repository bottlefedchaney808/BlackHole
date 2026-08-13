#!/usr/bin/env bash
# Fetch + save seed payloads to disk for a list of tickers (sequential, OI-route
# proxy-safe). Usage: bash save_seed_data.sh [TICKER ...]
set -u
cd "$(dirname "$0")/Vol_Suite"
if [ "$#" -gt 0 ]; then
  TICKERS=("$@")
else
  TICKERS=(QQQ AAPL NVDA AMD TSLA MSFT META GOOGL AMZN NFLX JPM)
fi
mkdir -p seed_data
for T in "${TICKERS[@]}"; do
  echo "===== $T $(date -u +%H:%M:%S) ====="
  env -u PYTHONPATH -u VIRTUAL_ENV ../Financial_Dev_Env/bin/python3 \
    seed_data_maker.py "$T" 150 seed_data > /tmp/sdm_${T}.log 2>&1
  echo "exit=$? $(date -u +%H:%M:%S)"
  grep -E "saved|manifest" /tmp/sdm_${T}.log | head -1
  echo "--- 45s cooldown ---"
  sleep 45
done
echo "ALL DONE $(date -u +%H:%M:%S)"
