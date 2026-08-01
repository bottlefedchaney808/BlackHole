#!/usr/bin/env python3
"""
Vol-Suite Output Viewer: Display comprehensive volatility analysis results.

Commands:
  python vol-suite-viewer.py outputs [LIMIT]     - List recent runs
  python vol-suite-viewer.py show [RUN_NAME]     - Display detailed results
  python vol-suite-viewer.py images TICKER [RUN] - Show visualization outputs
"""
import json
import sys
import os
from pathlib import Path
from collections import defaultdict
import subprocess
import pandas as pd
from datetime import datetime

VOL_SUITE_ROOT = Path(__file__).parent.parent.parent / "Vol_Suite"
OUTPUT_DIR = VOL_SUITE_ROOT / "vs_output"
LEGACY_OUTPUTS = VOL_SUITE_ROOT / "outputs"


def get_recent_runs(limit=10):
    """List recent output runs with metadata."""
    runs = []

    # Check vs_output (current run)
    if OUTPUT_DIR.exists():
        files = list(OUTPUT_DIR.glob("*"))
        if files:
            creation_time = max(f.stat().st_mtime for f in files)
            run_info = _analyze_run_dir(OUTPUT_DIR)
            runs.append({
                "path": OUTPUT_DIR,
                "time": creation_time,
                "name": "vs_output [CURRENT]",
                "info": run_info,
            })

    # Check legacy timestamped outputs
    if LEGACY_OUTPUTS.exists():
        for run_dir in sorted(LEGACY_OUTPUTS.iterdir(), reverse=True)[:limit]:
            if run_dir.is_dir() and not run_dir.name.startswith("_"):
                creation_time = run_dir.stat().st_mtime
                run_info = _analyze_run_dir(run_dir)
                runs.append({
                    "path": run_dir,
                    "time": creation_time,
                    "name": run_dir.name,
                    "info": run_info,
                })

    return sorted(runs, key=lambda x: x["time"], reverse=True)


def _analyze_run_dir(run_path):
    """Extract metadata from a run directory."""
    files = list(run_path.glob("*"))
    info = {
        "total_files": len(files),
        "tickers": set(),
        "csv_files": [],
        "image_files": [],
        "json_files": [],
    }

    for f in files:
        if "_" in f.name:
            ticker = f.name.split("_")[0]
            if ticker.isupper() and len(ticker) <= 5:
                info["tickers"].add(ticker)

        if f.suffix == ".csv":
            info["csv_files"].append(f.name)
        elif f.suffix in [".png", ".jpg", ".pdf"]:
            info["image_files"].append(f.name)
        elif f.suffix == ".json":
            info["json_files"].append(f.name)

    info["tickers"] = sorted(list(info["tickers"]))
    return info


def display_run_summary(run_path):
    """Display summary of a run."""
    run_info = _analyze_run_dir(run_path)

    print("\n" + "=" * 90)
    print(f"  VOLATILITY ANALYSIS RUN: {run_path.name}")
    print("=" * 90)

    tickers_str = ", ".join(run_info["tickers"]) if run_info["tickers"] else "N/A"
    print(f"\n  Tickers analyzed: {tickers_str}")
    print(f"  Total outputs:   {run_info['total_files']} files")

    if run_info["csv_files"]:
        print(f"\n  CSV Data ({len(run_info['csv_files'])} files):")
        for csv_file in sorted(run_info["csv_files"]):
            print(f"      {csv_file}")

    if run_info["image_files"]:
        print(f"\n  Visualizations ({len(run_info['image_files'])} files):")
        for img_file in sorted(run_info["image_files"]):
            print(f"      {img_file}")


def display_csv_preview(csv_path, max_rows=3):
    """Display a preview of CSV file."""
    try:
        df = pd.read_csv(csv_path)
        print(f"\n  {csv_path.name}:")
        print(f"    Shape: {df.shape[0]} rows x {df.shape[1]} cols")

        if df.shape[0] > 0:
            print(f"\n    First {min(max_rows, df.shape[0])} rows:")
            pd.set_option("display.max_columns", None)
            pd.set_option("display.width", 120)
            for line in str(df.head(max_rows)).split("\n"):
                print(f"      {line}")

    except Exception as e:
        print(f"\n  {csv_path.name}: Error - {e}")


def display_gamma_analysis(run_path):
    """Display dealer gamma positioning."""
    print("\n" + "-" * 90)
    print("DEALER GAMMA ANALYSIS (Top 10 Strikes by Dollar Gamma)")
    print("-" * 90)

    gamma_files = list(run_path.glob("*gamma_records*.csv"))

    for gamma_file in sorted(gamma_files):
        ticker = gamma_file.name.split("_")[0]
        print(f"\n  {ticker}:")

        try:
            df = pd.read_csv(gamma_file)
            if "DollarGamma" in df.columns:
                df["abs_dollar_gamma"] = df["DollarGamma"].abs()
                top_10 = df.nlargest(10, "abs_dollar_gamma")

                print(f"    {'Strike':>8} {'Expiry':>10} {'Right':>5} {'OI':>8} {'Gamma':>10} {'$ Gamma':>16}")
                for _, row in top_10.iterrows():
                    sign = "+" if row["DollarGamma"] >= 0 else "-"
                    print(f"    {row['Strike']:>8.0f} {str(row['Expiry']):>10} {row['Right']:>5} "
                          f"{row['OI']:>8.0f} {row['Gamma']:>10.5f} {sign}${abs(row['DollarGamma']):>14,.0f}")

        except Exception as e:
            print(f"    Error: {e}")


def display_correlations(run_path):
    """Display correlation analysis."""
    print("\n" + "-" * 90)
    print("CORRELATION ANALYSIS")
    print("-" * 90)

    corr_files = list(run_path.glob("correlation_pairs*.csv"))

    for corr_file in sorted(corr_files):
        try:
            df = pd.read_csv(corr_file)
            print(f"\n  Top 10 correlations (by absolute value):")

            df["abs_corr"] = df["Correlation"].abs()
            df_sorted = df.nlargest(10, "abs_corr")

            print(f"    {'Pair':.<40} {'Correlation':>15} {'p-value':>12}")
            for _, row in df_sorted.iterrows():
                pair = f"{row['Ticker1']} vs {row['Ticker2']}"
                print(f"    {pair:.<40} {row['Correlation']:>15.4f} {row.get('p_value', 0):>12.2e}")

        except Exception as e:
            print(f"  Error: {e}")


def cmd_outputs(limit=10):
    """Show recent output runs."""
    runs = get_recent_runs(limit=limit)

    if not runs:
        print("No Vol_Suite output directories found.")
        return

    print("\n" + "=" * 90)
    print("RECENT VOLATILITY ANALYSIS RUNS")
    print("=" * 90)

    for i, run in enumerate(runs, 1):
        tickers = ", ".join(run["info"]["tickers"]) or "N/A"
        time_str = datetime.fromtimestamp(run["time"]).strftime("%Y-%m-%d %H:%M")

        print(f"\n  {i:2d}. [{time_str}] {run['name']}")
        print(f"      Tickers: {tickers}")
        print(f"      Files:   {run['info']['total_files']} total "
              f"({len(run['info']['csv_files'])} CSV, {len(run['info']['image_files'])} images)")


def cmd_show(run_name="latest"):
    """Display detailed results from a run."""
    runs = get_recent_runs()

    if not runs:
        print("No Vol_Suite output directories found.")
        return

    # Find the run
    if run_name == "latest":
        target_run = runs[0]
    else:
        matches = [r for r in runs if run_name.lower() in r["name"].lower()]
        if not matches:
            print(f"Run '{run_name}' not found. Available runs:")
            cmd_outputs(limit=5)
            return
        target_run = matches[0]

    run_path = target_run["path"]

    display_run_summary(run_path)
    display_gamma_analysis(run_path)
    display_correlations(run_path)

    # Show key CSVs
    print("\n" + "-" * 90)
    print("VARIANCE SWAP SUMMARIES")
    print("-" * 90)

    for csv_file in sorted(run_path.glob("*variance_swap_summary*.csv")):
        display_csv_preview(csv_file, max_rows=10)


def cmd_images(ticker="INTC", run_name="latest"):
    """Show image files from a run."""
    runs = get_recent_runs()

    if not runs:
        print("No Vol_Suite output directories found.")
        return

    if run_name == "latest":
        target_run = runs[0]
    else:
        matches = [r for r in runs if run_name.lower() in r["name"].lower()]
        if not matches:
            print(f"Run '{run_name}' not found.")
            return
        target_run = matches[0]

    run_path = target_run["path"]

    # Find images for ticker
    ticker_images = sorted(run_path.glob(f"{ticker}*.png"))

    if not ticker_images:
        # Fall back to correlation images
        ticker_images = sorted(run_path.glob("correlation*.png"))

    print(f"\n  {ticker} Visualization outputs in {target_run['name']}:")
    for img in ticker_images:
        print(f"    {img.name}")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        print("Examples:")
        print("  python vol-suite-viewer.py outputs")
        print("  python vol-suite-viewer.py show")
        print("  python vol-suite-viewer.py show 20260801")
        print("  python vol-suite-viewer.py images INTC")
        return

    cmd = sys.argv[1]

    if cmd == "outputs":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        cmd_outputs(limit=limit)

    elif cmd == "show":
        run_name = sys.argv[2] if len(sys.argv) > 2 else "latest"
        cmd_show(run_name=run_name)

    elif cmd == "images":
        ticker = sys.argv[2] if len(sys.argv) > 2 else "INTC"
        run_name = sys.argv[3] if len(sys.argv) > 3 else "latest"
        cmd_images(ticker=ticker, run_name=run_name)

    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
