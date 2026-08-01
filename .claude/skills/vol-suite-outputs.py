#!/usr/bin/env python3
"""Vol_Suite output viewer - display results from the most recent runs."""
import json
import sys
from pathlib import Path
from collections import defaultdict
import pandas as pd

VOL_SUITE_ROOT = Path(__file__).parent.parent.parent / "Vol_Suite"
OUTPUT_DIR = VOL_SUITE_ROOT / "vs_output"
LEGACY_OUTPUTS = VOL_SUITE_ROOT / "outputs"


def get_recent_runs(limit=5):
    """List recent output runs with metadata."""
    runs = []

    # Check vs_output (current run)
    if OUTPUT_DIR.exists():
        files = list(OUTPUT_DIR.glob("*"))
        if files:
            # Get the creation time from files
            creation_time = max(f.stat().st_mtime for f in files)
            run_info = _analyze_run_dir(OUTPUT_DIR)
            runs.append({
                "path": OUTPUT_DIR,
                "time": creation_time,
                "name": "vs_output (current)",
                "info": run_info,
            })

    # Check legacy timestamped outputs
    if LEGACY_OUTPUTS.exists():
        for run_dir in sorted(LEGACY_OUTPUTS.iterdir(), reverse=True)[:limit]:
            if run_dir.is_dir():
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
        "other_files": [],
    }

    for f in files:
        # Extract ticker from filename
        if "_" in f.name:
            ticker = f.name.split("_")[0]
            if ticker.isupper() and len(ticker) <= 5:
                info["tickers"].add(ticker)

        if f.suffix == ".csv":
            info["csv_files"].append(f.name)
        elif f.suffix in [".png", ".jpg", ".pdf"]:
            info["image_files"].append(f.name)
        else:
            info["other_files"].append(f.name)

    info["tickers"] = sorted(list(info["tickers"]))
    return info


def display_run_summary(run_path):
    """Display summary of a run."""
    run_info = _analyze_run_dir(run_path)

    print("\n" + "=" * 80)
    print(f"  RUN: {run_path.name}")
    print("=" * 80)

    print(f"\nTickers analyzed: {', '.join(run_info['tickers'])}")
    print(f"Total files: {run_info['total_files']}")

    if run_info["csv_files"]:
        print(f"\nCSV Outputs ({len(run_info['csv_files'])} files):")
        for csv_file in sorted(run_info["csv_files"]):
            print(f"  • {csv_file}")

    if run_info["image_files"]:
        print(f"\nVisualization Outputs ({len(run_info['image_files'])} files):")
        for img_file in sorted(run_info["image_files"]):
            print(f"  • {img_file}")


def display_csv_preview(csv_path, max_rows=5):
    """Display a preview of CSV file."""
    try:
        df = pd.read_csv(csv_path)
        print(f"\n{csv_path.name}:")
        print(f"  Shape: {df.shape[0]} rows, {df.shape[1]} columns")
        print(f"  Columns: {', '.join(df.columns[:5])}")
        if len(df.columns) > 5:
            print(f"           ... + {len(df.columns) - 5} more")
        if df.shape[0] > 0:
            print(f"\n  Sample data (first {min(max_rows, df.shape[0])} rows):")
            print("  " + "\n  ".join(str(df.head(max_rows)).split("\n")))
    except Exception as e:
        print(f"\n{csv_path.name}: Error reading - {e}")


def main():
    """Display Vol_Suite outputs."""
    runs = get_recent_runs(limit=5)

    if not runs:
        print("No Vol_Suite output directories found.")
        return

    print("\n" + "=" * 80)
    print("  VOL_SUITE OUTPUT RUNS")
    print("=" * 80)

    # Show recent runs list
    for i, run in enumerate(runs, 1):
        tickers = ", ".join(run["info"]["tickers"]) or "N/A"
        print(f"\n{i}. {run['name']}")
        print(f"   Tickers: {tickers}")
        print(f"   Files: {run['info']['total_files']} "
              f"({len(run['info']['csv_files'])} CSV, {len(run['info']['image_files'])} images)")

    # Display details of most recent run
    if runs:
        print("\n" + "-" * 80)
        print("MOST RECENT RUN DETAILS")
        print("-" * 80)

        most_recent = runs[0]
        display_run_summary(most_recent["path"])

        # Show CSV previews
        print("\n" + "-" * 80)
        print("CSV PREVIEWS")
        print("-" * 80)

        # Sort CSVs by type
        csv_files = sorted(most_recent["path"].glob("*.csv"))

        # Group by ticker
        by_ticker = defaultdict(list)
        for csv_file in csv_files:
            ticker = csv_file.name.split("_")[0]
            by_ticker[ticker].append(csv_file)

        for ticker in sorted(by_ticker.keys()):
            print(f"\n{ticker} Outputs:")
            for csv_file in sorted(by_ticker[ticker]):
                display_csv_preview(csv_file, max_rows=3)

        # Show summary files
        print("\n" + "-" * 80)
        print("IMAGE OUTPUTS (Generated visualizations)")
        print("-" * 80)

        image_files = sorted(most_recent["path"].glob("*.png"))
        for img_file in image_files:
            print(f"  • {img_file.name}")


if __name__ == "__main__":
    main()
