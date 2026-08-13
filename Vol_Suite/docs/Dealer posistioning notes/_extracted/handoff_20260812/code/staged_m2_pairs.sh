#!/usr/bin/env bash
# Staged-pairs multi-ticker pooled M2-joint backtest (120d, book_b vs M2).
# Avoids the PotatoHedge proxy saturation that 4-concurrent whole-chain
# fan-outs trigger (breaker opens ~6 consecutive failures). Runs tickers 2 at a
# time, each a SEPARATE process, THETADATA_HIST_CONCURRENCY=6 (documented clean).
# The proxy breaker needs a cooldown after the last saturation event, so we
# sleep a warm-up before the first pair.
#
# Usage: bash staged_m2_pairs.sh
set -u
cd "$(dirname "$0")/Vol_Suite"

PY=../Financial_Dev_Env/bin/python3
LOOKBACK="${BT_LOOKBACK_DAYS:-120}"
CONC="${THETADATA_HIST_CONCURRENCY:-6}"
WARMUP="${M2_WARMUP_S:-60}"

echo "[staged-m2] lookback=${LOOKBACK}d concurrency=${CONC} warmup=${WARMUP}s $(date '+%H:%M:%S')"
echo "[staged-m2] waiting ${WARMUP}s for proxy breaker cooldown..."
sleep "$WARMUP"

run_pair() {
  local a="$1" b="$2"
  echo "[staged-m2] >>> pair: $a + $b $(date '+%H:%M:%S')"
  env -u PYTHONPATH -u VIRTUAL_ENV BT_LOOKBACK_DAYS="$LOOKBACK" \
      THETADATA_HIST_CONCURRENCY="$CONC" \
      "$PY" pooled_panel_backtest.py "$a" "$b" 2>&1 | tee "/tmp/m2_joint_${a}_${b}.log"
  echo "[staged-m2] <<< pair done: $a + $b exit=${PIPESTATUS[0]} $(date '+%H:%M:%S')"
  # gentle gap between pairs so the proxy isn't hammered back-to-back
  sleep 20
}

run_pair SPY QQQ
run_pair AAPL NVDA
run_pair AMD TSLA
echo "[staged-m2] ALL PAIRS COMPLETE $(date '+%H:%M:%S')"
echo "[staged-m2] logs: /tmp/m2_joint_*.log"
