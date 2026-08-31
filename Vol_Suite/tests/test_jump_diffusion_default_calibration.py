def test_calibrate_default_jump_model_returns_error_dict_on_failure(monkeypatch):
    """A chain-fetch failure (bad ticker, no listed expiry, ThetaData down,
    etc.) must return an error dict, never raise -- callers in
    _run_core_analysis depend on this to keep a jump-diffusion outage from
    aborting the run. Errors carry a reason (not bare None) since a live
    orchestrator run only persists a successful suite's stdout tail --
    without a reason in the artifact, a failure here was unrecoverable
    after the fact (confirmed live 2026-08-31)."""
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

    assert result["status"] == "error"
    assert "ThetaData unavailable" in result["error"]
