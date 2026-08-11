#!/usr/bin/env python3
"""Live TUI dashboard — real-time spot, IV, Greeks, VRP streaming.

Uses rich for terminal rendering, ThetaData for live data.
Ctrl+C to exit. Auto-refreshes every 5 seconds."""
import os, sys, time, signal
from datetime import datetime
from typing import Dict, Any, List, Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from shared.thetadata import ThetaDataController
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.console import Console
from rich.text import Text
from rich import box

# Default watchlist — overridable via DASHBOARD_TICKERS env var
DEFAULT_TICKERS = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT", "AMD", "MU", "TSLA", "META", "GOOGL"]


def _color_iv(iv: float, avg: float = 60) -> str:
    """Color-code IV: green < avg, yellow 1-1.5x avg, red > 1.5x avg."""
    if iv <= avg * 1.0:
        return "green"
    elif iv <= avg * 1.5:
        return "yellow"
    return "red"


def _fetch_ticker_data(td: ThetaDataController, ticker: str) -> Dict[str, Any]:
    """Fetch live data for one ticker. Returns dict with keys or error string."""
    try:
        spot = td.fetch_spot_price(ticker)
        if not spot or float(spot) <= 0:
            return {"ticker": ticker, "error": "no spot"}

        exps = td.list_expirations(ticker)
        if not exps:
            return {"ticker": ticker, "spot": float(spot), "error": "no expirations"}

        from datetime import date
        today = date.today()
        parsed = []
        for e in exps:
            try:
                d = datetime.strptime(str(e), "%Y%m%d").date()
                parsed.append((abs((d - today).days), str(e)))
            except ValueError:
                continue
        if not parsed:
            return {"ticker": ticker, "spot": float(spot), "error": "can't parse expirations"}

        parsed.sort()
        _, exp_str = parsed[0]

        rows = td.option_bulk_greeks(ticker, exp_str)
        atm_iv = None
        call_iv = None
        put_iv = None
        atm_strike = None
        best_diff = float("inf")
        for row in (rows or []):
            try:
                k = float(row.get("strike", 0)) / 1000.0
                diff = abs(k - float(spot))
                if diff < best_diff:
                    best_diff = diff
                    atm_strike = k
                    civ = row.get("implied_vol") or row.get("impliedVolatility")
                    piv = row.get("implied_vol") or row.get("impliedVolatility")
                    if civ:
                        call_iv = float(civ) * 100
                    right = str(row.get("right", "")).upper()[:1]
                    if right == "P":
                        if piv:
                            put_iv = float(piv) * 100
            except (TypeError, ValueError):
                continue

        # Average call/put ATM IV
        ivs = [v for v in [call_iv, put_iv] if v is not None]
        atm_iv = sum(ivs) / len(ivs) if ivs else None

        return {
            "ticker": ticker,
            "spot": float(spot),
            "atm_iv": atm_iv,
            "atm_strike": atm_strike,
            "expiry_days": parsed[0][0] if parsed else None,
            "expiry": exp_str,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)[:40]}


def build_dashboard(tickers: List[str]):
    """Main TUI loop with rich Live display."""
    console = Console()
    td = ThetaDataController()

    try:
        with Live(console=console, refresh_per_second=4, screen=True) as live:
            while True:
                now = datetime.now().strftime("%H:%M:%S")
                tickers_env = os.environ.get("DASHBOARD_TICKERS", "")
                if tickers_env:
                    tickers = [t.strip().upper() for t in tickers_env.split(",") if t.strip()]

                # Layout
                layout = Layout()
                layout.split(
                    Layout(name="header", size=3),
                    Layout(name="body"),
                    Layout(name="footer", size=3),
                )

                # Header
                header_text = Text()
                header_text.append("📊  ", style="bold cyan")
                header_text.append("Live Quant Dashboard", style="bold white")
                header_text.append(f"   │   {now}", style="dim")
                header_text.append(f"   │   {len(tickers)} tickers", style="dim")
                layout["header"].update(Panel(header_text, box=box.HEAVY, border_style="blue"))

                # Body — main table
                table = Table(box=box.SIMPLE_HEAVY, expand=True, header_style="bold cyan")
                table.add_column("Ticker", style="bold white", width=8)
                table.add_column("Spot", justify="right", width=10)
                table.add_column("ATM IV", justify="right", width=10)
                table.add_column("Strike", justify="right", width=10)
                table.add_column("DTE", justify="right", width=6)
                table.add_column("Status", width=20)

                for ticker in tickers:
                    data = _fetch_ticker_data(td, ticker)
                    if "error" in data:
                        table.add_row(
                            ticker, "—", "—", "—", "—",
                            f"[red]{data['error']}[/red]"
                        )
                    else:
                        spot_str = f"${data['spot']:,.2f}"
                        iv = data.get("atm_iv")
                        iv_str = f"{iv:.1f}%" if iv is not None else "—"
                        iv_color = _color_iv(iv or 50)
                        strike_str = f"${data.get('atm_strike', 0):,.0f}" if data.get("atm_strike") else "—"
                        dte_str = str(data.get("expiry_days", "—"))
                        table.add_row(
                            ticker,
                            spot_str,
                            f"[{iv_color}]{iv_str}[/{iv_color}]",
                            strike_str,
                            dte_str,
                            "[green]live[/green]",
                        )

                layout["body"].update(Panel(table, box=box.ROUNDED, border_style="blue"))

                # Footer
                footer = Text()
                ok = sum(1 for t in tickers if "error" not in _fetch_ticker_data(td, t))
                footer.append(f"  {ok}/{len(tickers)} live  ", style="green")
                footer.append("│  Ctrl+C to exit  ", style="dim")
                footer.append("│  DASHBOARD_TICKERS=SYM1,SYM2 to customize  ", style="dim")
                layout["footer"].update(Panel(footer, box=box.HEAVY, border_style="blue"))

                live.update(layout)
                time.sleep(5)

    except KeyboardInterrupt:
        pass
    finally:
        td.close()
        console.print("\n[dim]Dashboard closed.[/dim]")


if __name__ == "__main__":
    tickers = DEFAULT_TICKERS
    env = os.environ.get("DASHBOARD_TICKERS", "")
    if env:
        tickers = [t.strip().upper() for t in env.split(",") if t.strip()]
    build_dashboard(tickers)