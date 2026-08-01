#!/usr/bin/env python3
"""
Vol-Suite Skill: Run volatility analysis and auto-display interactive dashboard.

Usage:
  python vol-suite.py run TICKER ARGS...     - Run analysis and show results
  python vol-suite.py show [RUN_NAME]        - Display results from run
  python vol-suite.py list [LIMIT]           - List recent runs
"""
import json
import sys
import os
from pathlib import Path
import subprocess
import pandas as pd
from datetime import datetime

VOL_SUITE_ROOT = Path(__file__).parent.parent.parent / "Vol_Suite"
OUTPUT_DIR = VOL_SUITE_ROOT / "vs_output"
LEGACY_OUTPUTS = VOL_SUITE_ROOT / "outputs"


def get_recent_runs(limit=10):
    """List recent output runs."""
    runs = []

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


def extract_metrics_from_run(run_path):
    """Extract key metrics from CSV files."""
    metrics = {}

    # Try to read variance swap summaries
    for summary_file in run_path.glob("*variance_swap_summary*.csv"):
        try:
            df = pd.read_csv(summary_file)
            ticker = summary_file.name.split("_")[0]
            ticker_metrics = dict(zip(df["Metric"], df["Value"]))
            metrics[ticker] = ticker_metrics
        except:
            pass

    return metrics


def generate_dashboard_html(run_path):
    """Generate interactive dashboard HTML from analysis results."""
    metrics = extract_metrics_from_run(run_path)
    run_info = _analyze_run_dir(run_path)

    # Extract key values
    intc_data = metrics.get("INTC", {})
    qqq_data = metrics.get("QQQ", {})

    intc_fair_vol = intc_data.get("Fair_Vol_%", "N/A")
    qqq_fair_vol = qqq_data.get("Fair_Vol_%", "N/A")

    try:
        vol_spread = float(intc_fair_vol) - float(qqq_fair_vol)
    except:
        vol_spread = "N/A"

    # Read correlation data
    corr_pairs = []
    try:
        corr_file = list(run_path.glob("correlation_pairs*.csv"))[0]
        df = pd.read_csv(corr_file)
        df["abs_corr"] = df["Correlation"].abs()
        corr_pairs = df.nlargest(10, "abs_corr").to_dict("records")
    except:
        pass

    # Read gamma records
    gamma_records = []
    try:
        gamma_file = list(run_path.glob("*gamma_records*.csv"))[0]
        df = pd.read_csv(gamma_file)
        if "DollarGamma" in df.columns:
            df["abs_dollar_gamma"] = df["DollarGamma"].abs()
            gamma_records = df.nlargest(8, "abs_dollar_gamma").to_dict("records")
    except:
        pass

    # Generate HTML
    html = f"""
    <div style="padding: 1.5rem 0;">
      <style>
        .dashboard {{ font-family: var(--font-sans); color: var(--text-primary); }}
        .tabs {{ display: flex; gap: 8px; margin-bottom: 1.5rem; border-bottom: 0.5px solid var(--border); }}
        .tab {{ padding: 12px 16px; cursor: pointer; background: transparent; border: none; font-size: 14px; font-weight: 500; color: var(--text-secondary); border-bottom: 2px solid transparent; }}
        .tab.active {{ color: var(--text-primary); border-bottom-color: var(--fill-accent); }}
        .tab:hover {{ color: var(--text-primary); }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
        .metric-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 1.5rem; }}
        .metric-card {{ background: var(--surface-1); border-radius: var(--radius); padding: 12px; border: 0.5px solid var(--border); }}
        .metric-label {{ font-size: 12px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
        .metric-value {{ font-size: 20px; font-weight: 500; color: var(--text-primary); }}
        .metric-unit {{ font-size: 13px; color: var(--text-secondary); margin-left: 4px; }}
        .table-container {{ overflow-x: auto; margin-bottom: 1.5rem; }}
        table {{ width: 100%; font-size: 13px; border-collapse: collapse; }}
        th {{ text-align: left; padding: 8px; background: var(--surface-1); font-weight: 500; border-bottom: 0.5px solid var(--border); }}
        td {{ padding: 8px; border-bottom: 0.5px solid var(--border); }}
        tr:hover {{ background: var(--surface-1); }}
        .positive {{ color: var(--text-success); font-weight: 500; }}
        .negative {{ color: var(--text-danger); font-weight: 500; }}
        .insight-box {{ background: var(--surface-1); border-left: 2px solid var(--fill-accent); padding: 12px 16px; margin: 1rem 0; border-radius: var(--radius); }}
        .file-list {{ list-style: none; padding: 0; margin: 1rem 0; }}
        .file-list li {{ padding: 6px 0; font-size: 13px; }}
      </style>

      <div class="dashboard">
        <div style="margin-bottom: 1.5rem;">
          <h2 style="margin: 0 0 8px; font-size: 18px; font-weight: 500;">Analysis Results</h2>
          <p style="margin: 0; font-size: 13px; color: var(--text-secondary);">Run: {run_path.name}</p>
        </div>

        <div class="tabs">
          <button class="tab active" onclick="switchTab(event, 'summary')">Summary</button>
          <button class="tab" onclick="switchTab(event, 'gamma')">Dealer Gamma</button>
          <button class="tab" onclick="switchTab(event, 'correlation')">Correlations</button>
          <button class="tab" onclick="switchTab(event, 'files')">Files</button>
        </div>

        <div id="summary" class="tab-content active">
          <div class="metric-grid">
            <div class="metric-card">
              <div class="metric-label">{list(metrics.keys())[0] if metrics else 'TICKER'} Fair Vol</div>
              <div class="metric-value"><span class="positive">{intc_fair_vol if intc_fair_vol != 'N/A' else 'N/A'}</span></div>
            </div>
            <div class="metric-card">
              <div class="metric-label">{list(metrics.keys())[1] if len(metrics) > 1 else 'INDEX'} Fair Vol</div>
              <div class="metric-value">{qqq_fair_vol if qqq_fair_vol != 'N/A' else 'N/A'}</div>
            </div>
            <div class="metric-card">
              <div class="metric-label">Vol Spread</div>
              <div class="metric-value"><span class="positive">{vol_spread if isinstance(vol_spread, str) else f'+{vol_spread:.1f} pts' if vol_spread > 0 else f'{vol_spread:.1f} pts'}</span></div>
            </div>
            <div class="metric-card">
              <div class="metric-label">Basket Tickers</div>
              <div class="metric-value">{len(run_info['tickers']) - 2}</div>
            </div>
            <div class="metric-card">
              <div class="metric-label">CSV Outputs</div>
              <div class="metric-value">{len(run_info['csv_files'])}</div>
            </div>
            <div class="metric-card">
              <div class="metric-label">Images</div>
              <div class="metric-value">{len(run_info['image_files'])}</div>
            </div>
          </div>

          <div class="insight-box">
            <strong>Analysis Complete:</strong> Variance swap analysis, correlation matrix, and dealer gamma positioning generated. Check the Gamma and Correlation tabs for detailed findings.
          </div>
        </div>

        <div id="gamma" class="tab-content">
          <div class="insight-box">
            <strong>Dealer Positioning:</strong> Review gamma exposure by strike to understand dealer hedging.
          </div>
          <h3 style="font-size: 14px; font-weight: 500; margin: 1.5rem 0 12px;">Top Gamma Strikes</h3>
          <div class="table-container">
            <table>
              <tr>
                <th>Strike</th>
                <th>Exp</th>
                <th>Right</th>
                <th>OI</th>
                <th>Gamma</th>
                <th style="text-align: right;">$ Gamma</th>
              </tr>
              {chr(10).join(f'''<tr>
                <td>{r.get("Strike", "N/A")}</td>
                <td>{r.get("Expiry", "N/A")}</td>
                <td>{r.get("Right", "N/A")}</td>
                <td>{int(r.get("OI", 0)):,}</td>
                <td>{r.get("Gamma", 0):.4f}</td>
                <td style="text-align: right;" class="positive">${r.get("DollarGamma", 0):,.0f}</td>
              </tr>''' for r in gamma_records[:8])}
            </table>
          </div>
        </div>

        <div id="correlation" class="tab-content">
          <div class="insight-box">
            <strong>Basket Correlation:</strong> Top pairs ranked by absolute correlation value.
          </div>
          <h3 style="font-size: 14px; font-weight: 500; margin: 1.5rem 0 12px;">Top 10 Correlations</h3>
          <div class="table-container">
            <table>
              <tr>
                <th>Pair</th>
                <th>Correlation</th>
              </tr>
              {chr(10).join(f'''<tr>
                <td>{r.get("Ticker1", "")} vs {r.get("Ticker2", "")}</td>
                <td>{r.get("Correlation", 0):.4f}</td>
              </tr>''' for r in corr_pairs[:10])}
            </table>
          </div>
        </div>

        <div id="files" class="tab-content">
          <h3 style="font-size: 14px; font-weight: 500; margin-bottom: 12px;">CSV Outputs ({len(run_info['csv_files'])} files)</h3>
          <ul class="file-list">
            {chr(10).join(f'<li>{f}</li>' for f in sorted(run_info['csv_files'])[:10])}
          </ul>

          <h3 style="font-size: 14px; font-weight: 500; margin-bottom: 12px;">Visualizations ({len(run_info['image_files'])} files)</h3>
          <ul class="file-list">
            {chr(10).join(f'<li>{f}</li>' for f in sorted(run_info['image_files'])[:10])}
          </ul>

          <div class="insight-box" style="margin-top: 1.5rem;">
            <code style="background: var(--surface-0); padding: 4px 8px; border-radius: 4px; font-family: var(--font-mono); font-size: 12px;">{run_path}</code>
          </div>
        </div>
      </div>

      <script>
        function switchTab(e, tabName) {{
          document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
          document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
          document.getElementById(tabName).classList.add('active');
          e.target.classList.add('active');
        }}
      </script>
    </div>
    """

    return html


def cmd_run(args):
    """Run Vol_Suite and display dashboard."""
    os.chdir(VOL_SUITE_ROOT)

    # Run volatility suite
    result = subprocess.run([sys.executable, "volatility_suite.py"] + args)

    if result.returncode == 0:
        # Display dashboard
        runs = get_recent_runs(limit=1)
        if runs:
            dashboard_html = generate_dashboard_html(runs[0]["path"])
            print("\n" + "=" * 90)
            print("ANALYSIS RESULTS DASHBOARD")
            print("=" * 90)
            print(dashboard_html)

    return result.returncode


def cmd_show(run_name="latest"):
    """Display results from a run."""
    runs = get_recent_runs()

    if not runs:
        print("No Vol_Suite output directories found.")
        return 1

    if run_name == "latest":
        target_run = runs[0]
    else:
        matches = [r for r in runs if run_name.lower() in r["name"].lower()]
        if not matches:
            print(f"Run '{run_name}' not found.")
            return 1
        target_run = matches[0]

    dashboard_html = generate_dashboard_html(target_run["path"])
    print(dashboard_html)
    return 0


def cmd_list(limit=10):
    """List recent runs."""
    runs = get_recent_runs(limit=int(limit))

    print("\n" + "=" * 90)
    print("RECENT VOLATILITY ANALYSIS RUNS")
    print("=" * 90)

    for i, run in enumerate(runs, 1):
        tickers = ", ".join(run["info"]["tickers"]) or "N/A"
        time_str = datetime.fromtimestamp(run["time"]).strftime("%Y-%m-%d %H:%M")

        print(f"\n  {i:2d}. [{time_str}] {run['name']}")
        print(f"      Tickers: {tickers}")
        print(f"      Files:   {run['info']['total_files']} ({len(run['info']['csv_files'])} CSV, {len(run['info']['image_files'])} images)")

    return 0


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]

    if cmd == "run":
        return cmd_run(sys.argv[2:])
    elif cmd == "show":
        run_name = sys.argv[2] if len(sys.argv) > 2 else "latest"
        return cmd_show(run_name=run_name)
    elif cmd == "list":
        limit = sys.argv[2] if len(sys.argv) > 2 else "10"
        return cmd_list(limit=limit)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
