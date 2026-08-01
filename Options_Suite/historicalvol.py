"""30-day annualized realized volatility -- now sourced from PotatoHedge/ThetaData
EOD history instead of yahoo (yfinance purged). Standalone helper; not on the main
comparison path. Returns a 0.30 fallback if PotatoHedge history is unavailable."""
from datetime import datetime


def get_30d_vol(ticker, target_date=None):
    """Compute 30-day annualized historical volatility for `ticker`.

    `target_date` is accepted for backwards-compatible signature but PotatoHedge's
    realized-vol helper always uses the most recent window; a future/None date is
    treated as "latest". Returns a decimal (e.g. 0.42), or 0.30 on any failure.
    """
    try:
        from thetadata_controller import ThetaDataController
    except Exception:
        return 0.3
    try:
        td = ThetaDataController()
        try:
            vol = td.fetch_realized_vol(ticker, window=30)
        finally:
            td.close()
        if vol is not None and vol > 0:
            return max(float(vol), 0.01)
    except Exception:
        pass
    return 0.3
