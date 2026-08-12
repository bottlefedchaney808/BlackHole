#!/usr/bin/env bash
set -u

cd "$(dirname "$0")/.." || exit 2

PY=".venv/Scripts/python.exe"
SUITE="${1:-all}"
FAILED=0

echo "== burst checkpoint: diff --stat (whole working tree) =="
git --no-pager diff --stat
echo

run_vol() {
    echo "== narrowest affected-suite: Vol_Suite dealer/scanner/backtest =="
    env -u PYTHONPATH -u VIRTUAL_ENV PYTHONPATH="Vol_Suite:." "$PY" -m pytest \
        Vol_Suite/tests/test_dealer_positioning_sign_model.py \
        Vol_Suite/tests/test_options_chain_scanner.py \
        Vol_Suite/tests/test_vol_surface_reference.py \
        Vol_Suite/tests/test_run_modes_smoke.py \
        Vol_Suite/tests/test_backtest_stage3.py \
        --import-mode=importlib -q --no-header -p no:cacheprovider || FAILED=1
}

run_sentiment() {
    echo "== narrowest affected-suite: sentiment + bridge/worker =="
    env -u PYTHONPATH -u VIRTUAL_ENV PYTHONPATH="sentiment-scanner:." "$PY" -m pytest \
        sentiment-scanner/tests/test_main.py \
        sentiment-scanner/tests/test_max_pain_scanner.py \
        --import-mode=importlib -q --no-header -p no:cacheprovider || FAILED=1

    env -u PYTHONPATH -u VIRTUAL_ENV PYTHONPATH="." "$PY" -m pytest \
        tests/test_quant_bridge.py \
        tests/test_worker_broker.py \
        --import-mode=importlib -q --no-header -p no:cacheprovider || FAILED=1
}

case "$SUITE" in
    vol) run_vol ;;
    sentiment) run_sentiment ;;
    all) run_vol; run_sentiment ;;
    *) echo "unknown suite: $SUITE (vol|sentiment|all)" >&2; exit 2 ;;
esac

echo
if [ "$FAILED" -ne 0 ]; then
    echo "== burst checkpoint: FAILED (see above) ==" >&2
    exit 1
fi
echo "== burst checkpoint: OK =="
