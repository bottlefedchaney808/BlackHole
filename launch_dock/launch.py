from __future__ import annotations

import os
from typing import Any

from shared.config import robinhood_agentic_account

PY = r"E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe"
BLACKHOLE = r"E:/BlackHole_Investments/BlackHole"
EVENT_DESK = r"E:/BlackHole_Investments/BlackHole/Event_Desk"
KALSHI = {
    "BTC-PERP": "KXBTCPERP",
    "XRP-PERP": "KXXRPPERP",
    "SOL-PERP": "KXSOLPERP",
    "ETH-PERP": "KXETHPERP",
    "DOGE-PERP": "KXDOGEPERP",
    "ADA-PERP": "KXADAPERP",
    "LINK-PERP": "KXLINKPERP",
    "LTC-PERP": "KXLTCPERP",
    "BNB-PERP": "KXBNBPERP",
    "SUI-PERP": "KXSUIPERP",
    "HYPE-PERP": "KXHYPEPERP",
    "BCH-PERP": "KXBCHPERP",
    "GOLD": "KXGOLDPERP",
    "SILVER": "KXSILVERPERP",
}
DETACH = 0x00000200 | 0x00000008 | 0x08000000
# The one Robinhood account the stock sleeves trade ("Agentic"). The API
# client can trade any account, so this pin is the only thing keeping a
# sleeve out of Jason's own; `run_live_equity` refuses any other account too.
# Read from `.env` (ROBINHOOD_AGENTIC_ACCOUNT); unset means no stock launches.
AGENTIC_ACCOUNT = robinhood_agentic_account()


class LaunchRefused(RuntimeError):
    pass


def _busy(cards: list[dict], card: dict) -> None:
    for other in cards:
        if other.get("id") == card.get("id"):
            continue
        if other.get("state") not in ("running", "launching"):
            continue
        if card["seed"] == "event_desk" and other.get("seed") == "event_desk":
            raise LaunchRefused("event_desk watch already running")
        if (
            other.get("seed") == card.get("seed")
            and other.get("instrument") == card.get("instrument")
        ):
            raise LaunchRefused(f"{card['instrument']} already running")


def _over_allocated(cards: list[dict], card: dict) -> None:
    """Refuse a launch that would push the book past fully invested.

    Each sleeve sizes off its OWN share of live capital, so two sleeves at
    0.6 and 0.6 are a 1.2x book on a 1x account -- leverage arriving through
    the back door, which is exactly what `--leverage` exists to make explicit.
    Perps (Kalshi) and stocks (the agentic Robinhood account) are separate
    books, so each seed is summed on its own.
    """
    seed = card.get("seed")
    total = float(card.get("cap_pct") or 0.0)
    for other in cards:
        if other.get("id") == card.get("id"):
            continue
        if other.get("seed") != seed or other.get("state") not in ("running", "launching"):
            continue
        total += float(other.get("cap_pct") or 0.0)
    if total > 1.0001:
        what = "perps" if seed == "perp" else "stock sleeves"
        raise LaunchRefused(
            f"cap_pct across running {what} would be {total:.0%} of the book; "
            "cut a sleeve's share or stop one first"
        )


def perp_argv(
    card: dict,
    seed: dict,
    cards: list[dict] | None = None,
) -> list[str]:
    if cards:
        _busy(cards, card)

    # Per-card cap wins; the seed is the fallback for a card without one. It
    # used to be seed-only, so every perp shared one book: BTC and XRP both
    # traded $12 because that is what the seed said, while BTC's card still
    # carried a stale `--cap-dollars 200` from when the seed was 200. Two
    # instruments at different prices need different books -- and at $12 a
    # 20% scale-in unit is 0.8 contracts, so the five-step ladder that IS the
    # risk control rounds away to nothing.
    # `cap_pct` is the preferred form: a share of the sleeve's LIVE capital,
    # re-read every tick, so the book compounds as it goes up and shrinks as
    # it goes down. A fixed dollar cap does neither -- `full_contracts` takes
    # min(cap, capital), so a cap under equity freezes size and a cap over it
    # never contracts. Fractions across simultaneously running perps should
    # sum to 1.0 to be fully invested at 1x; `_over_allocated` refuses more.
    cap_pct = card.get("cap_pct")
    cap = card.get("cap_dollars")
    if cap is None:
        cap = seed.get("cap_dollars")
    if cap_pct is not None:
        if not 0.0 < float(cap_pct) <= 1.0:
            raise LaunchRefused(
                f"cap_pct for {card.get('id')} must be in (0, 1], got {cap_pct}"
            )
        if cards:
            _over_allocated(cards, card)
    elif not cap or float(cap) <= 0:
        raise LaunchRefused(
            f"no size for {card.get('id')}: set cap_pct, or cap_dollars on the "
            "card or the seed"
        )
    if card.get("leverage") is None:
        raise LaunchRefused("leverage is unset")

    instrument = card["instrument"].upper()
    if instrument not in KALSHI:
        raise LaunchRefused(f"no Kalshi map for {card['instrument']}")

    hours = float(seed.get("hours") or 0.0)
    argv = [
        PY,
        "-m",
        "chart_app.run_live_perp",
        "--ticker",
        instrument,
        "--interval",
        card.get("interval") or "15m",
        *(
            ["--cap-fraction", str(float(cap_pct))]
            if cap_pct is not None
            else ["--cap-dollars", str(float(cap))]
        ),
        "--leverage",
        str(float(card["leverage"])),
        "--hours",
        str(hours),
        "--subaccount",
        str(int(card.get("subaccount") or 0)),
    ]

    if card.get("pnl_since"):
        argv += ["--pnl-since", card["pnl_since"]]
        if card.get("base_capital") is None:
            raise LaunchRefused("pnl_since set but base_capital missing")
        argv += ["--base-capital", str(float(card["base_capital"]))]
    if card.get("live"):
        argv.append("--live")
    return argv


def stocks_argv(card: dict, cards: list[dict] | None = None) -> list[str]:
    """The equity sleeve runner on the agentic Robinhood account.

    Stock cards used to launch `Direction.whale_scanner` -- a scanner, not a
    trader. Long-only is enforced inside the runner; shorts wait until the
    account can short (~$2k).
    """
    if not AGENTIC_ACCOUNT:
        raise LaunchRefused("ROBINHOOD_AGENTIC_ACCOUNT is not set in .env")
    if cards:
        _busy(cards, card)
    cap_pct = card.get("cap_pct")
    cap = card.get("cap_dollars")
    if cap_pct is not None:
        if not 0.0 < float(cap_pct) <= 1.0:
            raise LaunchRefused(f"cap_pct for {card.get('id')} must be in (0, 1], got {cap_pct}")
        if cards:
            _over_allocated(cards, card)
        size = ["--cap-fraction", str(float(cap_pct))]
    elif cap and float(cap) > 0:
        size = ["--cap-dollars", str(float(cap))]
    else:
        raise LaunchRefused(f"no size for {card.get('id')}: set its Share %")
    argv = [
        PY,
        "-m",
        "chart_app.run_live_equity",
        "--ticker",
        card["instrument"].upper(),
        "--interval",
        card.get("interval") or "5m",
        *size,
        "--account",
        AGENTIC_ACCOUNT,
    ]
    if card.get("pnl_since"):
        argv += ["--pnl-since", card["pnl_since"]]
        if card.get("base_capital") is not None:
            argv += ["--base-capital", str(float(card["base_capital"]))]
    if card.get("live"):
        argv.append("--live")
    return argv


def event_desk_argv() -> list[str]:
    return ["py", "-3.11", "event_desk/churn.py", "watch"]


def apply_session(
    card: dict,
    session: dict,
    *,
    saved_at: str | None = None,
) -> dict:
    card = dict(card)
    card["pnl_since"] = session["pnl_since"]
    card["base_capital"] = float(session["equity"])
    card["running_saved_at"] = saved_at
    card["state"] = "running"
    return card


def python_cmdlines() -> list[str]:
    """Command lines of python.exe on this machine. Empty on failure."""
    import subprocess
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Select-Object -ExpandProperty CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [ln.strip() for ln in (result.stdout or "").splitlines() if ln.strip()]


def ticker_from_perp_cmdline(line: str) -> str | None:
    if "run_live_perp" not in line:
        return None
    parts = line.replace("=", " ").split()
    for i, part in enumerate(parts):
        token = part.strip(chr(34) + chr(39))
        if token == "--ticker" and i + 1 < len(parts):
            return parts[i + 1].strip(chr(34) + chr(39)).upper()
    return None


def os_perp_tickers(cmdlines: list[str] | None = None) -> set[str]:
    """Tickers that already have a run_live_perp process, regardless of dock cards."""
    lines = python_cmdlines() if cmdlines is None else cmdlines
    out: set[str] = set()
    for line in lines:
        ticker = ticker_from_perp_cmdline(line)
        if ticker:
            out.add(ticker)
    return out


def refuse_duplicate_runner(instrument: str, cmdlines: list[str] | None = None) -> None:
    ticker = str(instrument or "").upper()
    if ticker and ticker in os_perp_tickers(cmdlines):
        raise LaunchRefused(f"{ticker} already has a runner")
