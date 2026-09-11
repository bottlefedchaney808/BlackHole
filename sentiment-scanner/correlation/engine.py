"""Correlation engine: war score vs options OI changes + 6 new scanner inputs.

Extended from the original single-signal engine to consume all 6 options
scanners (GEX, Unusual OI, IV Rank, Skew, Max Pain, Vol Dispersion) and
generate composite signals that cross-reference narrative (CNS) against
options flow.
"""

from typing import Dict, List, Optional
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np

# ---- Re-export the scanner result types (lazy imports to avoid circular
#      deps at module load time; they're only needed when the correlation
#      methods actually fire, which is during scan_trending). ----
def _gex_module():
    from scanner.gex_scanner import GexScan
    return GexScan

def _oi_module():
    from scanner.unusual_oi_scanner import UnusualOiScan
    return UnusualOiScan

def _iv_module():
    from scanner.iv_rank_scanner import IvRankScan
    return IvRankScan

def _skew_module():
    from scanner.skew_scanner import SkewScan
    return SkewScan

def _pain_module():
    from scanner.max_pain_scanner import MaxPainScan
    return MaxPainScan

def _disp_module():
    from scanner.vol_dispersion_scanner import VolDispersionScan
    return VolDispersionScan

def _earnings_module():
    from scanner.earnings_scanner import EarningsResult, HIGH_PREMIUM_THRESHOLD
    return EarningsResult, HIGH_PREMIUM_THRESHOLD


class CorrelationEngine:
    """Aggregates narrative + options scanner signals per ticker.

    Stores historical CNS/GEX/OI snapshots so trends can be detected
    across scan cycles.
    """

    def __init__(self):
        self.history = defaultdict(list)
        self.oi_baselines = {}
        # Per-ticker: latest scanner results
        self._gex: Dict[str, object] = {}
        self._oi: Dict[str, object] = {}
        self._iv: Dict[str, object] = {}
        self._skew: Dict[str, object] = {}
        self._pain: Dict[str, object] = {}
        self._disp: Dict[str, object] = {}
        self._earnings: Dict[str, object] = {}

    # ---- Narrative recording (existing) ----
    def record_narrative(self, ticker: str, scores: Dict):
        self.history[ticker].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "war_score": scores["war_score"],
            "cns": scores["contested_narrative_score"],
            "volume": scores["volume"],
            "thesis_ratio": scores["thesis_ratio"],
            "pump_ratio": scores["pump_ratio"],
            "bullish_pct": scores["bullish_pct"],
            "bearish_pct": scores["bearish_pct"],
        })

    def set_oi_baseline(self, ticker: str, snapshot: Dict):
        self.oi_baselines[ticker] = snapshot

    def get_narrative_trend(self, ticker: str, lookback: int = 7) -> Dict:
        points = self.history.get(ticker, [])
        if len(points) < 2:
            return {"trend": "insufficient_data", "volatility": 0}
        recent = points[-lookback:]
        cns_vals = [p["cns"] for p in recent]
        war_vals = [p["war_score"] for p in recent]
        return {
            "cns_current": cns_vals[-1],
            "cns_mean": float(np.mean(cns_vals)),
            "cns_trend": cns_vals[-1] - cns_vals[0],
            "cns_volatility": float(np.std(cns_vals)),
            "war_current": war_vals[-1],
            "war_trend": war_vals[-1] - war_vals[0],
            "samples": len(recent),
        }

    # ---- Scanner result ingestion ----
    def record_gex(self, ticker: str, scan: object) -> None:
        self._gex[ticker] = scan

    def record_oi(self, ticker: str, scan: object) -> None:
        self._oi[ticker] = scan

    def record_iv(self, ticker: str, scan: object) -> None:
        self._iv[ticker] = scan

    def record_skew(self, ticker: str, scan: object) -> None:
        self._skew[ticker] = scan

    def record_pain(self, ticker: str, scan: object) -> None:
        self._pain[ticker] = scan

    def record_dispersion(self, ticker: str, scan: object) -> None:
        self._disp[ticker] = scan

    def record_earnings(self, ticker: str, scan: object) -> None:
        """Store the latest earnings-vol scan result for a ticker."""
        self._earnings[ticker] = scan

    # ---- Composite signal generation ----
    # The five original rules below all gate on a key of `oi_snapshot`, so an
    # empty snapshot silently disables every one of them and the result looks
    # identical to "these five rules evaluated and did not fire". Two of this
    # method's four call sites (the report pass and the composite-signals
    # summary in main.py) pass `{}` deliberately -- building a real snapshot
    # there means live option-chain pulls on a reporting path, which are
    # billed and which the repo's rate-limit discipline exists to avoid. The
    # degradation is legitimate; it being INVISIBLE was the finding. The
    # result now carries `oi_rules_evaluated` so a reader (and the report)
    # can tell a quiet rule set from an unfired one.
    _OI_RULE_KEYS = ("otm_oi_ratio", "convexity_premium", "oi_change_pct", "skew_vol_pts")

    def correlate_with_oi(self, ticker: str, oi_snapshot: Dict) -> Dict:
        """Original single-signal method — enhanced to include scanner data.

        `oi_snapshot` may be empty, which disables the five OI-dependent
        rules. The returned dict reports that explicitly under
        `oi_rules_evaluated` rather than presenting a partial rule set as a
        complete one.
        """
        trend = self.get_narrative_trend(ticker)
        oi_rules_evaluated = bool(oi_snapshot) and any(
            k in oi_snapshot for k in self._OI_RULE_KEYS
        )
        signals: List[str] = []
        severities: List[str] = []

        cns = trend.get("cns_current", 0)
        war = trend.get("war_current", 0)

        # --- Original rules ---
        if war > 0.3 and oi_snapshot.get("otm_oi_ratio", 0) > 0.4:
            signals.append("DIRECTIONAL_BET_FORMING")
            severities.append("HIGH")
        if war > 0.3 and oi_snapshot.get("convexity_premium", 0) > 3.0:
            signals.append("VOL_EVENT_DETECTED")
            severities.append("HIGH")
        if war < 0.15 and oi_snapshot.get("oi_change_pct", 0) > 20:
            signals.append("SMART_MONEY_POSITIONING")
            severities.append("MEDIUM")
        if trend.get("cns_trend", 0) > 20 and oi_snapshot.get("oi_change_pct", 0) > 30:
            signals.append("RETAIL_MOMENTUM")
            severities.append("MEDIUM")
        if oi_snapshot.get("skew_vol_pts", 0) > 5:
            signals.append("SKEW_WIDENING")
            severities.append("MEDIUM")

        # --- NEW: GEX signal ---
        gex = self._gex.get(ticker)
        if gex is not None and not getattr(gex, "error", None):
            gex_type = _gex_module()
            if isinstance(gex, gex_type):
                if gex.total_net_dollar_gamma < 0 and cns > 50:
                    signals.append("GAMMA_SQUEEZE_RISK")
                    severities.append("HIGH")
                elif gex.total_net_dollar_gamma < 0:
                    signals.append("NEGATIVE_GAMMA")
                    severities.append("MEDIUM")

        # --- NEW: Unusual OI signal ---
        oi = self._oi.get(ticker)
        if oi is not None and not getattr(oi, "error", None):
            oi_type = _oi_module()
            if isinstance(oi, oi_type):
                if oi.surge_detected and cns > 50:
                    signals.append("OI_SURGE_WITH_NARRATIVE")
                    severities.append("HIGH")
                elif oi.surge_detected:
                    signals.append("OI_SURGE")
                    severities.append("MEDIUM")

        # --- NEW: IV Rank signal ---
        iv = self._iv.get(ticker)
        if iv is not None and not getattr(iv, "error", None):
            iv_type = _iv_module()
            if isinstance(iv, iv_type):
                if iv.regime == "RICH" and cns > 50:
                    signals.append("RICH_VOL_PLUS_NARRATIVE")
                    severities.append("HIGH")
                if iv.regime == "CHEAP" and cns > 50:
                    signals.append("CHEAP_VOL_PLUS_NARRATIVE")
                    severities.append("MEDIUM")
                if iv.regime == "RICH":
                    signals.append("VOL_RICH")
                    severities.append("LOW")

        # --- NEW: Skew signal ---
        skew = self._skew.get(ticker)
        if skew is not None and not getattr(skew, "error", None):
            skew_type = _skew_module()
            if isinstance(skew, skew_type):
                if skew.skew_signal == "PUT_SKEW_EXTREME" and cns > 50:
                    signals.append("EXTREME_SKEW_PLUS_NARRATIVE")
                    severities.append("HIGH")
                elif skew.skew_signal == "PUT_SKEW_EXTREME":
                    signals.append("EXTREME_PUT_SKEW")
                    severities.append("MEDIUM")
                elif skew.skew_signal == "PUT_SKEW_ELEVATED":
                    signals.append("ELEVATED_PUT_SKEW")
                    severities.append("LOW")

        # --- NEW: Max Pain signal ---
        pain = self._pain.get(ticker)
        if pain is not None and not getattr(pain, "error", None):
            pain_type = _pain_module()
            if isinstance(pain, pain_type):
                if pain.near_pin and cns > 50:
                    signals.append("PIN_ACTION_WITH_NARRATIVE")
                    severities.append("HIGH")
                elif pain.near_pin:
                    signals.append("NEAR_MAX_PAIN")
                    severities.append("MEDIUM")
                if abs(pain.price_vs_pain_pct) > 5.0 and cns > 50:
                    signals.append("FAR_FROM_PAIN_PLUS_NARRATIVE")
                    severities.append("HIGH")

        # --- NEW: Vol Dispersion signal ---
        disp = self._disp.get(ticker)
        if disp is not None and not getattr(disp, "error", None):
            disp_type = _disp_module()
            if isinstance(disp, disp_type):
                if disp.dispersion_signal == "DISPERSION_SETUP" and cns > 50:
                    signals.append("DISPERSION_SETUP_PLUS_NARRATIVE")
                    severities.append("HIGH")
                elif disp.dispersion_signal == "DISPERSION_SETUP":
                    signals.append("DISPERSION_SETUP")
                    severities.append("MEDIUM")

        # --- NEW: Earnings-vol premium signal ---
        earn = self._earnings.get(ticker)
        if earn is not None and not getattr(earn, "error", None):
            earn_type, high_threshold = _earnings_module()
            if isinstance(earn, earn_type):
                if earn.premium_pct >= high_threshold and cns > 50:
                    signals.append("EARNINGS_VOL_PLUS_NARRATIVE")
                    severities.append("HIGH")
                elif earn.premium_pct >= high_threshold:
                    signals.append("EARNINGS_VOL_PREMIUM")
                    severities.append("MEDIUM")

        # --- Composite severity ---
        sev = "LOW"
        if "HIGH" in severities:
            sev = "HIGH"
        elif "MEDIUM" in severities:
            sev = "MEDIUM"

        return {
            "ticker": ticker,
            "narrative_trend": trend,
            "oi_snapshot": oi_snapshot,
            # False means the five OI-dependent rules did not run at all (no
            # snapshot supplied) -- NOT that they ran and found nothing.
            "oi_rules_evaluated": oi_rules_evaluated,
            "signals": signals,
            "severity": sev,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_scanner_summary(self, ticker: str) -> Dict:
        """Collect all scanner results for a ticker into one dict."""
        gex = self._gex.get(ticker)
        oi = self._oi.get(ticker)
        iv = self._iv.get(ticker)
        skew = self._skew.get(ticker)
        pain = self._pain.get(ticker)
        disp = self._disp.get(ticker)
        earn = self._earnings.get(ticker)

        gex_err = getattr(gex, "error", None)
        oi_err = getattr(oi, "error", None)
        iv_err = getattr(iv, "error", None)
        skew_err = getattr(skew, "error", None)
        pain_err = getattr(pain, "error", None)
        disp_err = getattr(disp, "error", None)
        earn_err = getattr(earn, "error", None)

        return {
            "gex": {
                "status": "ok" if gex and not gex_err else f"error: {gex_err}",
                "net_dollar_gamma": round(getattr(gex, "total_net_dollar_gamma", 0), 0) if gex else 0,
                "gamma_flip": round(getattr(gex, "gamma_flip_level", 0), 2) if gex else 0,
                "num_expiries": getattr(gex, "num_expiries", 0) if gex else 0,
            },
            "oi": {
                "status": "ok" if oi and not oi_err else f"error: {oi_err}",
                "total_oi": getattr(oi, "current_total_oi", 0) if oi else 0,
                "change_pct": getattr(oi, "oi_change_pct", 0.0) if oi else 0.0,
                "surge": getattr(oi, "surge_detected", False) if oi else False,
            },
            "iv_rank": {
                "status": "ok" if iv and not iv_err else f"error: {iv_err}",
                "atm_iv_pct": getattr(iv, "atm_iv_pct", 0.0) if iv else 0.0,
                "regime": getattr(iv, "regime", "UNKNOWN") if iv else "UNKNOWN",
                "rv_60_pct": getattr(iv, "rv_60_pct", 0.0) if iv else 0.0,
                "vrp": getattr(iv, "vrp_pct", 0.0) if iv else 0.0,
            },
            "skew": {
                "status": "ok" if skew and not skew_err else f"error: {skew_err}",
                "put_skew_pts": getattr(skew, "put_skew_pts", 0.0) if skew else 0.0,
                "signal": getattr(skew, "skew_signal", "UNKNOWN") if skew else "UNKNOWN",
                "sabr_rho": getattr(skew, "sabr_rho", None) if skew else None,
            },
            "max_pain": {
                "status": "ok" if pain and not pain_err else f"error: {pain_err}",
                "pain_strike": round(getattr(pain, "max_pain_strike", 0), 2) if pain else 0,
                "near_pin": getattr(pain, "near_pin", False) if pain else False,
                "price_vs_pain_pct": getattr(pain, "price_vs_pain_pct", 0.0) if pain else 0.0,
            },
            "dispersion": {
                "status": "ok" if disp and not disp_err else f"error: {disp_err}",
                "spread_pts": getattr(disp, "iv_spread_pts", 0.0) if disp else 0.0,
                "signal": getattr(disp, "dispersion_signal", "UNKNOWN") if disp else "UNKNOWN",
                "stock_iv": getattr(disp, "stock_iv_pct", 0.0) if disp else 0.0,
                "benchmark_iv": getattr(disp, "benchmark_iv_pct", 0.0) if disp else 0.0,
            },
            "earnings": {
                "status": "ok" if earn and not earn_err else f"error: {earn_err}",
                "premium_pct": getattr(earn, "premium_pct", 0.0) if earn else 0.0,
                "signal": getattr(earn, "signal", "UNKNOWN") if earn else "UNKNOWN",
                "earnings_date": getattr(earn, "earnings_date", "") if earn else "",
            },
        }