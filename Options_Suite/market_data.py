"""
Macro / underlying market inputs (spot, risk-free rate, dividend yield, seed vol).

PURGED OF YAHOO: this module used to pull everything from yfinance. It is now
backed entirely by PotatoHedge / ThetaData (the same proxy thetadata_controller.py
already talks to), which is the project's single live data source. The public
interface (class name + method signatures) is unchanged, so main.py, vol_manager.py
and MCHestonLSM.py keep working without edits -- they just now get a consistent
ThetaData-sourced spot, which is what makes the model rows and the ThetaData
"Market" greek row finally share the same underlying price (the root cause of the
delta/vega/rho/theta gaps in the comparison report was the old yahoo spot
disagreeing with ThetaData's snapshot spot by several percent).

Every PotatoHedge value is validated/clamped; on any failure this raises
RuntimeError rather than silently degrading to a constant fallback (removed --
a stale hard-coded r/q feeding pricing unnoticed is worse than a loud failure).
yfinance can be reintroduced later as an explicit *backup* source, but it is
deliberately not in this path anymore.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Union

try:
    from . import data_source_config
except ImportError:
    import data_source_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from thetadata_controller import ThetaDataController
    _THETADATA_AVAILABLE = True
except Exception:  # pragma: no cover - import guard
    _THETADATA_AVAILABLE = False


class MarketDataController:
    # No equity/ETF realistically sustains a dividend yield above this. Used to
    # reject obviously-corrupted values rather than feeding them into pricing.
    _MAX_PLAUSIBLE_DIV_YIELD = 0.20

    def __init__(self):
        # Nothing to hold; each call opens/closes a short-lived client so a dropped
        # connection never poisons later calls.
        pass

    def _client(self) -> Optional["ThetaDataController"]:
        if not _THETADATA_AVAILABLE:
            return None
        try:
            return ThetaDataController()
        except Exception as e:
            logger.warning(f"[MarketData] PotatoHedge client unavailable: {e}")
            return None

    def fetch_spot_price(self, ticker: str) -> float:
        """Spot from PotatoHedge snapshot quote. Spot is essential -- if it can't
        be sourced there is no sensible fallback, so this raises (previously yahoo
        was the fallback; that path is intentionally gone)."""
        td = self._client()
        if td is not None:
            try:
                val = td.fetch_spot_price(ticker)
                if val and val > 0:
                    return float(val)
            except Exception as e:
                logger.error(f"[MarketData] spot fetch error: {e}")
            finally:
                td.close()
        raise ValueError(
            f"Could not fetch spot for '{ticker}' from PotatoHedge -- tried both the live "
            f"bid/ask quote and the last trade print, neither returned a usable (>0) price. "
            f"Most likely the symbol is invalid or not covered (a stray character in the "
            f"ticker will do this); if other symbols also fail, then check ThetaData "
            f"credentials / connectivity."
        )

    def fetch_risk_free_rate(self, proxy: str = 'long_term', T: float = 1.0) -> float:
        """Risk-free rate from PotatoHedge yield curve for tenor ~T. `proxy` is
        accepted for signature compatibility with the old yahoo version but is no
        longer used (the curve is queried by tenor). No constant fallback: a rate
        that silently falls back to a stale hard-coded number is worse than a loud
        failure, so this raises RuntimeError if PotatoHedge can't be reached.
        """
        if not getattr(data_source_config, 'USE_POTATOHEDGE_RATE', True):
            raise RuntimeError(
                "[MarketData] USE_POTATOHEDGE_RATE is disabled and no fallback is available."
            )
        td = self._client()
        if td is not None:
            try:
                rate = td.fetch_risk_free_rate(T=T)
                if rate is not None:
                    return float(rate)
            except Exception as e:
                raise RuntimeError(f"[MarketData] Failed to fetch risk-free rate: {e}. No fallback available.")
            finally:
                td.close()
        raise RuntimeError(
            "[MarketData] Failed to fetch risk-free rate: PotatoHedge client unavailable. No fallback available."
        )

    def fetch_dividend_yield(self, ticker: str) -> float:
        """Trailing dividend yield from PotatoHedge. Same sanity clamp the old
        yahoo version used (0 <= q < 20%). No constant fallback: raises
        RuntimeError if PotatoHedge can't be reached or returns an implausible
        value, rather than silently feeding pricing a stale hard-coded number."""
        if not getattr(data_source_config, 'USE_POTATOHEDGE_DIVIDEND', True):
            raise RuntimeError(
                "[MarketData] USE_POTATOHEDGE_DIVIDEND is disabled and no fallback is available."
            )
        td = self._client()
        if td is not None:
            try:
                spot = None
                try:
                    spot = td.fetch_spot_price(ticker)
                except Exception:
                    spot = None
                q = td.fetch_dividend_yield(ticker, spot=spot)
                if q is not None and 0.0 <= q < self._MAX_PLAUSIBLE_DIV_YIELD:
                    return float(q)
                raise RuntimeError(
                    f"[MarketData] Dividend yield {q!r} for '{ticker}' is missing or implausible "
                    f"(must be in [0, {self._MAX_PLAUSIBLE_DIV_YIELD}))."
                )
            except Exception as e:
                raise RuntimeError(f"[MarketData] Failed to fetch dividend yield: {e}. No fallback available.")
            finally:
                td.close()
        raise RuntimeError(
            "[MarketData] Failed to fetch dividend yield: PotatoHedge client unavailable. No fallback available."
        )

    def fetch_beta(self, ticker: str) -> Optional[float]:
        """Trailing equity beta vs SPY, computed from PotatoHedge daily EOD
        closes (see ThetaDataController.fetch_beta). Display-only in main.py's
        startup printout -- never feeds pricing -- so this returns None, not a
        fabricated 1.0, when there isn't enough history to compute a real
        number. Previously this hard-coded 1.0 for every ticker regardless of
        its actual market sensitivity; main.py now prints 'N/A' when this is
        None instead of a number that looked real but wasn't."""
        td = self._client()
        if td is not None:
            try:
                beta = td.fetch_beta(ticker)
                if beta is not None:
                    return float(beta)
            except Exception as e:
                logger.warning(f"[MarketData] beta fetch error: {e}")
            finally:
                td.close()
        return None

    def validate_strike(
        self, ticker: str, strike: float,
        target_years: Optional[float] = None,
        expiration_date: Optional[str] = None,
    ) -> Dict[str, Union[bool, float]]:
        """Validate a strike against PotatoHedge's listed strikes for the expiry
        the IV solve will *actually* use -- the listed expiry nearest to the
        requested target maturity -- not just ``exps[0]`` (the shortest-dated
        listing, nearest to today). A strike that exists on a near-dated weekly
        (e.g. 482.5) is frequently absent from a 3-month-out monthly chain, so
        validating against the wrong expiry returned a "closest" strike with no
        market price and the NO-FALLBACKS IV solve then raised. If no target
        maturity is supplied, falls back to the nearest-to-today expiry (prior
        behaviour) so callers that don't know the target still work. If
        listings can't be fetched, accept the strike as-is (don't block a run
        on a validation-only lookup)."""
        td = self._client()
        if td is None:
            return {'valid': True, 'closest': strike}
        try:
            exps = td.list_expirations(ticker)
            if not exps:
                return {'valid': True, 'closest': strike}
            nearest_exp = self._resolve_target_expiry(exps, target_years, expiration_date)
            strikes = td.list_strikes(ticker, nearest_exp)
            if not strikes:
                return {'valid': True, 'closest': strike}
            if strike in strikes:
                return {'valid': True, 'closest': strike}
            return {'valid': False, 'closest': min(strikes, key=lambda x: abs(x - strike))}
        except Exception:
            return {'valid': True, 'closest': strike}
        finally:
            td.close()

    def _resolve_target_expiry(
        self, exps, target_years: Optional[float] = None,
        expiration_date: Optional[str] = None,
    ) -> Optional[str]:
        """Pick the listed expiry nearest to the requested maturity, matching
        how ``ThetaDataController.fetch_option_iv`` resolves the contract (so
        strike validation and the IV solve agree on one expiry). Falls back to
        the nearest-to-today expiry when no target is given."""
        def _days_from(target_dt):
            return lambda e: abs((datetime.strptime(str(e), "%Y%m%d") - target_dt).days)
        if expiration_date:
            digits = ''.join(ch for ch in str(expiration_date) if ch.isdigit())
            try:
                target = datetime.strptime(digits, "%Y%m%d")
                return min(exps, key=_days_from(target))
            except Exception:
                pass
        if target_years is not None:
            try:
                target = datetime.now() + timedelta(days=int(target_years * 365))
                return min(exps, key=_days_from(target))
            except Exception:
                pass
        # No target: nearest-to-today expiry (matches prior exps[0] intent).
        return min(exps, key=_days_from(datetime.now()))

    def get_pricing_parameters(self, ticker: str, strike: float, time_to_maturity: float,
                               proxy: str = 'long_term') -> Dict[str, Union[float, str]]:
        """Bundle of pricing inputs, all from PotatoHedge. `sigma` here is a
        realized-vol *seed* only (e.g. Heston V0 initialization); it falls back to
        the configured constant if EOD history is unavailable."""
        try:
            S = self.fetch_spot_price(ticker)
            q = self.fetch_dividend_yield(ticker)
            r = self.fetch_risk_free_rate(proxy, T=time_to_maturity)
            sigma = None
            td = self._client()
            if td is not None:
                try:
                    sigma = td.fetch_realized_vol(ticker, window=30)
                except Exception:
                    sigma = None
                finally:
                    td.close()
            if sigma is None:
                sigma = getattr(data_source_config, 'FALLBACK_VOL', 0.30)
            return {'S': S, 'K': strike, 'T': time_to_maturity, 'r': r, 'q': q,
                    'sigma': sigma, 'ticker': ticker}
        except Exception as e:
            raise ValueError(f"Market data error: {e}")
