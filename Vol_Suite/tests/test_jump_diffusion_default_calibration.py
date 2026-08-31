def test_calibrate_default_jump_model_returns_none_on_failure(monkeypatch):
    """A chain-fetch failure (bad ticker, no listed expiry, ThetaData down,
    etc.) must return None, never raise -- callers in _run_core_analysis
    depend on this to keep a jump-diffusion outage from aborting the run."""
    import thetadata_client
    import volatility_suite

    class _BoomController:
        def fetch_spot_price(self, ticker):
            raise RuntimeError("ThetaData unavailable")

    # _calibrate_default_jump_model does `from thetadata_client import
    # ThetaDataController` inside its own body (a fresh lookup on every
    # call), so patching the name on thetadata_client itself -- not on
    # volatility_suite -- is what actually intercepts the constructor call.
    monkeypatch.setattr(thetadata_client, "ThetaDataController", _BoomController)

    result = volatility_suite._calibrate_default_jump_model("SPY", "20270101", 0.5)

    assert result is None
