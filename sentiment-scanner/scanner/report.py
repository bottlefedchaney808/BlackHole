"""Scanner Report — PDF report generator matching Options_Suite brand style.

Generates a multi-page PDF report with summary tables, charts, and signals
from all 6 options scanners + sentiment narrative data.

Usage:
    from scanner.report import ScannerReport
    report = ScannerReport(ticker="SPY")
    report.add_gex(gex_scan)
    report.add_iv_rank(iv_scan)
    report.add_skew(skew_scan)
    report.add_max_pain(pain_scan)
    report.add_unusual_oi(oi_scan)
    report.add_dispersion(disp_scan)
    report.add_signals(composite_signals)
    pdf_path = report.save("outputs/")
"""

import os
import math
from datetime import datetime
from typing import Optional, Dict, List

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle, FancyBboxPatch
from matplotlib.colors import LinearSegmentedColormap
import matplotlib.ticker as mticker

# ── Brand palette (matches Options_Suite/reports.py) ──────────────────────
NAVY       = "#1b2a4a"
NAVY_LIGHT = "#2c4270"
ACCENT     = "#3d7ea6"
GOOD       = "#1e8f5f"
WARN       = "#c98a1b"
BAD        = "#c0392b"
GRID       = "#dfe3eb"
ROW_ALT    = "#f4f6fa"
MARKET_ROW = "#fff3d6"

# Scanner-specific colors
GEX_COLOR   = "#7a5195"
OI_COLOR    = "#ef5675"
IV_COLOR    = "#ffa600"
SKEW_COLOR  = "#003f5c"
PAIN_COLOR  = "#bc5090"
DISP_COLOR  = "#58508d"
SIGNAL_RED  = "#d62728"
SIGNAL_GRN  = "#2ca02c"
SIGNAL_AMB  = "#ff9800"

def _fmt(val, decimals=2):
    if val is None:
        return "N/A"
    if isinstance(val, float):
        return f"{val:,.{decimals}f}"
    return str(val)


class ScannerReport:
    """Multi-page PDF report for one scan pass across tickers."""

    def __init__(self, title: str = "Options Scanner Report", tickers: Optional[list] = None):
        self.title = title
        self.tickers = tickers or []
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Per-ticker scanner results
        self._results: Dict[str, dict] = {}
        self._signals: Dict[str, list] = {}

    def add_ticker_results(self, ticker: str, scanner_name: str, data: dict):
        """Store one scanner's output for a ticker."""
        if ticker not in self._results:
            self._results[ticker] = {}
        self._results[ticker][scanner_name] = data

    def add_signals(self, ticker: str, signals: list, severity: str = "LOW"):
        self._signals[ticker] = {"signals": signals, "severity": severity}

    # ── Page builders ─────────────────────────────────────────────────

    def _build_cover_page(self, fig):

        ax = fig.add_subplot(111)
        ax.axis("off")

        # Navy header band
        ax.add_patch(Rectangle((0, 0.72), 1, 0.28,
                                transform=ax.transAxes, facecolor=NAVY, edgecolor="none", zorder=0))

        ax.text(0.05, 0.88, self.title, transform=ax.transAxes,
                fontsize=26, fontweight="bold", color="white", va="center")
        ax.text(0.05, 0.78, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                transform=ax.transAxes, fontsize=12, color="#c8d3e8", va="center")

        # Summary box
        n_tickers = len(self.tickers) or "—"
        n_scanners = sum(len(v) for v in self._results.values())
        n_signals = sum(1 for v in self._signals.values() if v["signals"])

        box_y = 0.55
        ax.add_patch(FancyBboxPatch((0.05, box_y - 0.08), 0.9, 0.13,
                                     boxstyle="round,pad=0.04",
                                     facecolor=ROW_ALT, edgecolor=GRID, linewidth=1,
                                     transform=ax.transAxes, zorder=1))

        metrics = [
            ("Tickers Scanned", str(n_tickers)),
            ("Scanner Modules", str(n_scanners)),
            ("Composite Signals", str(n_signals)),
            ("Run ID", self.run_id),
        ]
        for i, (label, value) in enumerate(metrics):
            x = 0.12 + i * 0.22
            ax.text(x, box_y + 0.025, value, transform=ax.transAxes,
                    fontsize=18, fontweight="bold", color=NAVY, ha="center")
            ax.text(x, box_y - 0.045, label, transform=ax.transAxes,
                    fontsize=8.5, color="#666", ha="center")

        # Footer
        ax.text(0.05, 0.03, "Data: ThetaData via api.potatohedge.com  |  "
                            "Scanners: GEX · OI · IV Rank · Skew · Max Pain · Dispersion",
                transform=ax.transAxes, fontsize=8, color="#999", style="italic")

    def _build_scanner_dashboard(self, fig, ticker: str, data: dict):
        """Dashboard for one ticker: summary cards + charts."""
        gs = fig.add_gridspec(3, 2, height_ratios=[0.12, 0.44, 0.44], hspace=0.35, wspace=0.30)

        # ── Header ──
        ax_h = fig.add_subplot(gs[0, :])
        ax_h.axis("off")
        ax_h.add_patch(Rectangle((0, 0), 1, 1, transform=ax_h.transAxes,
                                  facecolor=NAVY, edgecolor="none", zorder=0))
        ax_h.text(0.03, 0.5, f"{ticker}", transform=ax_h.transAxes,
                  fontsize=22, fontweight="bold", color="white", va="center")

        # Severity badge
        sig = self._signals.get(ticker, {"signals": [], "severity": "LOW"})
        sev = sig["severity"]
        sev_color = { "HIGH": BAD, "MEDIUM": WARN, "LOW": GOOD }.get(sev, "#888")
        ax_h.text(0.97, 0.5, f"Severity: {sev}", transform=ax_h.transAxes,
                  fontsize=13, fontweight="bold", color=sev_color, va="center", ha="right")

        # ── GEX gauge ──
        ax1 = fig.add_subplot(gs[1, 0])
        gex = data.get("gex", {})
        if gex and not gex.get("error"):
            self._draw_gex_card(ax1, gex)
        else:
            self._draw_empty_card(ax1, "GEX", gex.get("error", "no_data"))

        # ── IV Rank card ──
        ax2 = fig.add_subplot(gs[1, 1])
        iv = data.get("iv_rank", {})
        if iv and not iv.get("error"):
            self._draw_iv_card(ax2, iv)
        else:
            self._draw_empty_card(ax2, "IV Rank", iv.get("error", "no_data"))

        # ── Skew + OI card ──
        ax3 = fig.add_subplot(gs[2, 0])
        skew = data.get("skew", {})
        oi = data.get("unusual_oi", {})
        self._draw_skew_oi_card(ax3, ticker, skew, oi)

        # ── Max Pain + Dispersion card ──
        ax4 = fig.add_subplot(gs[2, 1])
        pain = data.get("max_pain", {})
        disp = data.get("dispersion", {})
        self._draw_pain_disp_card(ax4, ticker, pain, disp)

    def _draw_gex_card(self, ax, gex: dict):
        ax.axis("off")
        ax.add_patch(Rectangle((0, 0), 1, 1, transform=ax.transAxes,
                                facecolor="white", edgecolor=GRID, linewidth=1, zorder=0))
        ax.text(0.05, 0.88, "Gamma Exposure (GEX)", transform=ax.transAxes,
                fontsize=11, fontweight="bold", color=NAVY)

        dg = gex.get("total_net_dollar_gamma", 0) or 0
        regime = gex.get("regime", "AMPLIFYING" if dg < 0 else "DAMPENING")
        flip = gex.get("gamma_flip_level", 0)
        hi_g = gex.get("highest_gamma_strike", 0)
        spot = gex.get("spot", 0)
        n_exp = gex.get("num_expiries", 0)

        # Net gamma bar
        ax.text(0.05, 0.72, f"Net $Gamma:", transform=ax.transAxes,
                fontsize=9, color="#555")
        ax.text(0.35, 0.72, f"${dg:+,.0f}", transform=ax.transAxes,
                fontsize=11, fontweight="bold",
                color=BAD if dg < 0 else GOOD)

        # Regime badge
        ax.add_patch(FancyBboxPatch((0.65, 0.68), 0.30, 0.12,
                                     boxstyle="round,pad=0.02",
                                     facecolor=BAD if dg < 0 else GOOD,
                                     edgecolor="none",
                                     transform=ax.transAxes))
        ax.text(0.80, 0.74, regime, transform=ax.transAxes,
                fontsize=8, fontweight="bold", color="white", ha="center")

        # Metrics
        y = 0.55
        for label, val in [
            ("Gamma Flip", f"${flip:.2f}" if flip else "—"),
            ("Hi-Gamma Strike", f"${hi_g:.2f}" if hi_g else "—"),
            ("Spot", f"${spot:.2f}" if spot else "—"),
            ("Expiries", str(n_exp)),
        ]:
            ax.text(0.05, y, label, transform=ax.transAxes, fontsize=8.5, color="#666")
            ax.text(0.55, y, val, transform=ax.transAxes,
                    fontsize=9, fontweight="bold", color=NAVY)
            y -= 0.09

        # Mini bar showing distance from spot to gamma flip
        if spot and flip and flip > 0:
            ax2 = ax.inset_axes([0.05, 0.05, 0.90, 0.06])
            ax2.axis("off")
            lo = min(spot, flip) * 0.97
            hi = max(spot, flip) * 1.03
            norm = (flip - lo) / (hi - lo) if hi != lo else 0.5
            ax2.barh(0, 1, height=0.6, color=GRID, edgecolor="none")
            ax2.barh(0, norm, height=0.6, color=GEX_COLOR, edgecolor="none")
            ax2.text(0.02, 0, "Spot", fontsize=7, color=NAVY, va="center_baseline")
            ax2.text(0.98, 0, f"Flip ${flip:.0f}", fontsize=7, color=GEX_COLOR,
                     va="center_baseline", ha="right")

    def _draw_iv_card(self, ax, iv: dict):
        ax.axis("off")
        ax.add_patch(Rectangle((0, 0), 1, 1, transform=ax.transAxes,
                                facecolor="white", edgecolor=GRID, linewidth=1, zorder=0))
        ax.text(0.05, 0.88, "Volatility Regime (IV Rank)", transform=ax.transAxes,
                fontsize=11, fontweight="bold", color=NAVY)

        atm = iv.get("atm_iv_pct", 0)
        regime = iv.get("regime", "UNKNOWN")
        vrp = iv.get("vrp_pct", 0)
        rv30 = iv.get("rv_30_pct", 0)
        rv60 = iv.get("rv_60_pct", 0)
        rv90 = iv.get("rv_90_pct", 0)
        garch = iv.get("garch_cond_vol_pct", 0)

        # ATM IV big number
        ax.text(0.05, 0.72, "ATM IV", transform=ax.transAxes, fontsize=9, color="#555")
        ax.text(0.05, 0.58, f"{atm:.1f}%", transform=ax.transAxes,
                fontsize=24, fontweight="bold", color=NAVY)

        # Regime badge
        reg_color = {"RICH": BAD, "CHEAP": GOOD, "FAIR": WARN}.get(regime, "#888")
        ax.add_patch(FancyBboxPatch((0.45, 0.62), 0.28, 0.12,
                                     boxstyle="round,pad=0.02",
                                     facecolor=reg_color, edgecolor="none",
                                     transform=ax.transAxes))
        ax.text(0.59, 0.68, regime, transform=ax.transAxes,
                fontsize=8, fontweight="bold", color="white", ha="center")

        # VRP
        vrp_color = BAD if vrp > 4 else (GOOD if vrp < -2 else "#666")
        ax.text(0.78, 0.64, f"VRP", transform=ax.transAxes, fontsize=8, color="#555")
        ax.text(0.78, 0.55, f"{vrp:+.1f}pp", transform=ax.transAxes,
                fontsize=10, fontweight="bold", color=vrp_color)

        # RV bars
        y = 0.42
        for label, val in [
            ("RV(30d)", rv30), ("RV(60d)", rv60), ("RV(90d)", rv90),
        ]:
            ax.text(0.05, y, label, transform=ax.transAxes, fontsize=8.5, color="#666")
            ax.barh(y + 0.01, val / (max(atm, 1) * 1.5), height=0.035,
                    color=IV_COLOR, alpha=0.7, transform=ax.transAxes)
            ax.text(0.55, y, f"{val:.1f}%", transform=ax.transAxes,
                    fontsize=9, fontweight="bold", color=NAVY)
            y -= 0.08

        if garch:
            ax.text(0.05, 0.08, f"GARCH cond: {garch:.1f}%",
                    transform=ax.transAxes, fontsize=8, color="#888", style="italic")

    def _draw_skew_oi_card(self, ax, ticker, skew: dict, oi: dict):
        ax.axis("off")
        ax.add_patch(Rectangle((0, 0), 1, 1, transform=ax.transAxes,
                                facecolor="white", edgecolor=GRID, linewidth=1, zorder=0))

        # Left: Skew
        ax.text(0.05, 0.88, "Skew", transform=ax.transAxes,
                fontsize=11, fontweight="bold", color=NAVY)
        skew_pts = skew.get("put_skew_pts", 0) if skew else 0
        sig = skew.get("skew_signal", "UNKNOWN") if skew else "UNKNOWN"
        sig_color = {"PUT_SKEW_EXTREME": BAD, "PUT_SKEW_ELEVATED": WARN,
                      "FLAT": GOOD, "CALL_SKEWED": ACCENT}.get(sig, "#888")
        ax.text(0.05, 0.74, f"{skew_pts:+.1f} pts", transform=ax.transAxes,
                fontsize=16, fontweight="bold", color=sig_color)
        ax.text(0.05, 0.64, sig.replace("_", " ").title(), transform=ax.transAxes,
                fontsize=8, color="#888")

        # SABR params
        rho = skew.get("sabr_rho") if skew else None
        nu = skew.get("sabr_nu") if skew else None
        if rho is not None:
            ax.text(0.05, 0.52, f"SABR ρ={rho:.2f}  ν={nu:.2f}",
                    transform=ax.transAxes, fontsize=8, color="#555")

        # Right: OI
        ax.text(0.55, 0.88, "Open Interest", transform=ax.transAxes,
                fontsize=11, fontweight="bold", color=NAVY)
        if oi and not oi.get("error"):
            total = oi.get("current_total_oi", 0)
            change = oi.get("oi_change_pct", 0)
            surge = oi.get("surge_detected", False)
            ax.text(0.55, 0.74, f"{total:,}", transform=ax.transAxes,
                    fontsize=16, fontweight="bold", color=NAVY)
            chg_color = BAD if surge else "#666"
            suffix = " ⚡ SURGE" if surge else ""
            ax.text(0.55, 0.64, f"{change:+.1f}%{suffix}", transform=ax.transAxes,
                    fontsize=9, color=chg_color, fontweight="bold" if surge else "normal")

            # Top strikes
            top = oi.get("top_strikes", [])
            y = 0.52
            for s in top[:3]:
                ax.text(0.55, y, f"${s.get('strike',0):.0f}{s.get('right','')}  "
                                 f"({s.get('oi',0):,})",
                        transform=ax.transAxes, fontsize=8, color="#555")
                y -= 0.07
        else:
            ax.text(0.55, 0.74, "—", transform=ax.transAxes,
                    fontsize=16, fontweight="bold", color="#ccc")

    def _draw_pain_disp_card(self, ax, ticker, pain: dict, disp: dict):
        ax.axis("off")
        ax.add_patch(Rectangle((0, 0), 1, 1, transform=ax.transAxes,
                                facecolor="white", edgecolor=GRID, linewidth=1, zorder=0))

        # Left: Max Pain
        ax.text(0.05, 0.88, "Max Pain", transform=ax.transAxes,
                fontsize=11, fontweight="bold", color=NAVY)
        if pain and not pain.get("error"):
            mp = pain.get("max_pain_strike", 0)
            spot = pain.get("spot", 0)
            near = pain.get("near_pin", False)
            val = pain.get("max_pain_value", 0)
            ax.text(0.05, 0.74, f"${mp:.2f}", transform=ax.transAxes,
                    fontsize=18, fontweight="bold", color=PAIN_COLOR)
            pin = " ◀ PIN" if near else ""
            ax.text(0.05, 0.62, f"Spot ${spot:.2f}  ({pain.get('price_vs_pain_pct',0):+.1f}%){pin}",
                    transform=ax.transAxes, fontsize=8.5, color="#555")
            ax.text(0.05, 0.53, f"Pain value: ${val:,.0f}",
                    transform=ax.transAxes, fontsize=8, color="#888")

            # Distance bar
            dist = abs(pain.get("price_vs_pain_pct", 0))
            ax2 = ax.inset_axes([0.05, 0.05, 0.40, 0.06])
            ax2.axis("off")
            norm_dist = min(dist / 10.0, 1.0)
            ax2.barh(0, 1, height=0.5, color=GRID, edgecolor="none")
            ax2.barh(0, norm_dist, height=0.5,
                     color=GOOD if near else WARN, edgecolor="none")
            ax2.text(0.5, 0, "Dist from Pain", fontsize=6.5, color="#888",
                     ha="center", va="center_baseline")
        else:
            ax.text(0.05, 0.74, "—", transform=ax.transAxes,
                    fontsize=18, fontweight="bold", color="#ccc")

        # Right: Vol Dispersion
        ax.text(0.55, 0.88, "Vol Dispersion", transform=ax.transAxes,
                fontsize=11, fontweight="bold", color=NAVY)
        if disp and not disp.get("error"):
            stock_iv = disp.get("stock_iv_pct", 0)
            bmk_iv = disp.get("benchmark_iv_pct", 0)
            spread = disp.get("iv_spread_pts", 0)
            z = disp.get("iv_spread_z", 0)
            dsig = disp.get("dispersion_signal", "NEUTRAL")
            beta = disp.get("beta")
            bmk = disp.get("benchmark", "SPY")

            dsig_color = { "DISPERSION_SETUP": BAD, "CONTRACTION": GOOD, "NEUTRAL": "#888" }.get(dsig, "#888")
            ax.text(0.55, 0.74, f"{stock_iv:.1f}% vs {bmk_iv:.1f}%",
                    transform=ax.transAxes, fontsize=13, fontweight="bold", color=NAVY)
            ax.text(0.55, 0.64, f"Spread {spread:+.1f}pp  z={z:+.1f}",
                    transform=ax.transAxes, fontsize=9, color="#555")
            ax.text(0.55, 0.56, dsig.replace("_", " ").title(),
                    transform=ax.transAxes, fontsize=9, fontweight="bold",
                    color=dsig_color)
            if beta is not None:
                ax.text(0.55, 0.48, f"β={beta:.2f} to {bmk}",
                        transform=ax.transAxes, fontsize=8, color="#888")

            # Mini bar comparing IVs
            ax2 = ax.inset_axes([0.55, 0.05, 0.40, 0.08])
            ax2.axis("off")
            max_iv = max(stock_iv, bmk_iv, 1)
            ax2.barh(0.3, bmk_iv / max_iv, height=0.3, color=ACCENT, label=bmk)
            ax2.barh(0.7, stock_iv / max_iv, height=0.3, color=DISP_COLOR, label=ticker[:6])
            ax2.text(0.5, 1.0, f"{bmk}: {bmk_iv:.0f}%", fontsize=6.5, color=ACCENT, ha="center", va="bottom")
            ax2.text(0.5, 0.28, f"{ticker[:6]}: {stock_iv:.0f}%", fontsize=6.5,
                     color=DISP_COLOR, ha="center", va="top")
        else:
            ax.text(0.55, 0.74, "—", transform=ax.transAxes,
                    fontsize=18, fontweight="bold", color="#ccc")

    def _draw_empty_card(self, ax, name: str, error: str):
        ax.axis("off")
        ax.add_patch(Rectangle((0, 0), 1, 1, transform=ax.transAxes,
                                facecolor="#fafafa", edgecolor=GRID, linewidth=1, zorder=0))
        ax.text(0.5, 0.55, name, transform=ax.transAxes,
                fontsize=12, fontweight="bold", color="#ccc", ha="center")
        ax.text(0.5, 0.40, f"⚠ {error}", transform=ax.transAxes,
                fontsize=8, color="#ccc", ha="center", style="italic")

    def _build_signals_page(self, fig):
        """Composite signals summary across all tickers."""
        gs = fig.add_gridspec(2, 1, height_ratios=[0.12, 0.88], hspace=0.25)

        ax_h = fig.add_subplot(gs[0])
        ax_h.axis("off")
        ax_h.add_patch(Rectangle((0, 0), 1, 1, transform=ax_h.transAxes,
                                  facecolor=NAVY, edgecolor="none", zorder=0))
        ax_h.text(0.03, 0.5, "Composite Signals", transform=ax_h.transAxes,
                  fontsize=18, fontweight="bold", color="white", va="center")

        ax = fig.add_subplot(gs[1])
        ax.axis("off")

        if not self._signals:
            ax.text(0.5, 0.5, "No composite signals generated.",
                    transform=ax.transAxes, fontsize=12, color="#999", ha="center")
            return

        tickers_sorted = sorted(self._signals.keys())
        rows_data = []
        for t in tickers_sorted:
            sig = self._signals[t]
            sev = sig["severity"]
            sigs = sig["signals"]
            rows_data.append((t, sev, "; ".join(sigs) if sigs else "—"))

        n_rows = len(rows_data)
        col_labels = ["Ticker", "Severity", "Signals"]
        col_widths = [0.10, 0.10, 0.80]
        cell_text = [[r[0], r[1], r[2]] for r in rows_data]

        tbl = ax.table(cellText=cell_text, colLabels=col_labels,
                       cellLoc="center", loc="center", colWidths=col_widths)
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(9)
        tbl.scale(1.0, 1.6)

        sev_colors = {"HIGH": BAD, "MEDIUM": WARN, "LOW": GOOD}
        for (row_i, col_i), cell in tbl.get_celld().items():
            cell.set_edgecolor(GRID)
            if row_i == 0:
                cell.set_text_props(weight="bold", color="white")
                cell.set_facecolor(NAVY_LIGHT)
                continue
            if col_i == 1:
                val = cell_text[row_i - 1][1]
                cell.set_text_props(color=sev_colors.get(val, "#888"), weight="bold")
            elif col_i == 0:
                cell.set_text_props(weight="bold", color=NAVY)
            bg = ROW_ALT if row_i % 2 == 0 else "#ffffff"
            cell.set_facecolor(bg)

    # ── Save ──────────────────────────────────────────────────────────

    def save(self, out_dir: str = "outputs") -> str:
        """Generate the full multi-page PDF report. Returns the file path."""
        os.makedirs(out_dir, exist_ok=True)
        fname = os.path.join(out_dir, f"scanner_report_{self.run_id}.pdf")

        n_tickers = len(self.tickers)
        n_pages = 1 + n_tickers + 1  # cover + 1 per ticker + signals

        with PdfPages(fname) as pdf:
            # Cover page
            fig = plt.figure(figsize=(8.5, 11), facecolor="white")
            self._build_cover_page(fig)
            pdf.savefig(fig, bbox_inches="tight", facecolor="white", dpi=150)
            plt.close(fig)

            # One page per ticker with scanner dashboard
            for ticker in self.tickers:
                data = self._results.get(ticker, {})
                fig = plt.figure(figsize=(8.5, 11), facecolor="white")
                self._build_scanner_dashboard(fig, ticker, data)
                pdf.savefig(fig, bbox_inches="tight", facecolor="white", dpi=150)
                plt.close(fig)

            # Composite signals page
            fig = plt.figure(figsize=(8.5, 11), facecolor="white")
            self._build_signals_page(fig)
            pdf.savefig(fig, bbox_inches="tight", facecolor="white", dpi=150)
            plt.close(fig)

        print(f"  Report saved: {os.path.abspath(fname)}")
        return os.path.abspath(fname)