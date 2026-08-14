"""Driver: run the full expiry-book-exposure falsifier over the whole offline seed corpus.

Launches the pre-registered Phase-6 gate (GEX primary flow->forward returns, plus the vanna-lead,
event-window, and accumulated-overlay arms) at n_perms=500 across all seed tickers, with BH
multiplicity control and the SPY/QQQ sign-consistency rule. Network-free. Writes a human-readable
report + JSON verdict to Vol_Suite/_expiry_falsifier_cache/.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import expiry_book_exposure as ebe

def main():
    tickers = ebe.seed_corpus_tickers()
    print(f"[driver] seed corpus tickers ({len(tickers)}): {tickers}", flush=True)

    t0 = time.time()
    signals = {}
    for tk in tickers:
        try:
            signals[tk] = ebe.build_daily_signals_from_seed(tk)
            print(f"[driver] loaded {tk}: {len(signals[tk].dates)} days", flush=True)
        except Exception as exc:
            print(f"[driver] SKIP {tk}: {type(exc).__name__}: {exc}", flush=True)

    print(f"[driver] running falsifier at n_perms={ebe._N_PERMS} corr_threshold="
          f"{ebe._CORR_THRESHOLD_DEFAULT} over {len(signals)} tickers...", flush=True)
    run = ebe.run_expiry_falsifier_cached(
        signals, n_perms=ebe._N_PERMS, corr_threshold=ebe._CORR_THRESHOLD_DEFAULT,
        force_recompute=True)
    elapsed = time.time() - t0
    print(f"[driver] done in {elapsed:.1f}s", flush=True)

    # ---- human report ----
    lines = []
    lines.append("# Expiry Book Exposure — Phase 6 Falsifier Result (full corpus)")
    lines.append("")
    lines.append(f"- Run: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- Tickers: {run.tickers}")
    lines.append(f"- n_perms: {ebe._N_PERMS}, corr_threshold: {ebe._CORR_THRESHOLD_DEFAULT}, "
                 f"perm p<{ebe._PERM_P_THRESHOLD}")
    lines.append(f"- Elapsed: {elapsed:.1f}s")
    lines.append(f"- OVERALL VERDICT: **{run.overall}**")
    lines.append(f"- SPY/QQQ sign-consistent: {run.spy_qqq_sign_consistent}")
    lines.append("")
    lines.append("## Primary GEX channel (flow -> forward 1d returns)")
    lines.append("| Ticker | n_days | corr | block_perm_p | q | verdict |")
    lines.append("|---|---|---|---|---|---|")
    for tk, v in sorted(run.primary_gex.items()):
        lines.append(f"| {tk} | {v.n_days} | {v.corr:.3f} | {v.block_perm_p:.4f} | "
                     f"{v.q_value:.4f} | {v.verdict} |")
    lines.append("")
    lines.append("## All channels")
    lines.append("| Ticker | channel | n_days | corr | block_perm_p | q | verdict |")
    lines.append("|---|---|---|---|---|---|---|")
    for key, v in sorted(run.channels.items()):
        lines.append(f"| {key} | {v.n_days} | {v.corr:.3f} | {v.block_perm_p:.4f} | "
                     f"{v.q_value:.4f} | {v.verdict} |")
    if run.arms:
        lines.append("")
        lines.append("## Arms")
        for k, a in run.arms.items():
            lines.append(f"- {k}: {a}")
    if run.notes:
        lines.append("")
        lines.append("## Notes")
        for n in run.notes:
            lines.append(f"- {n}")

    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "_expiry_falsifier_cache", "expiry_falsifier_FULL_12T_report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"[driver] report written to {report_path}", flush=True)
    print("\n".join(lines), flush=True)

if __name__ == "__main__":
    main()
