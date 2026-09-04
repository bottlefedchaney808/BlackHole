"""module_registry.py (VaR_Tools_Simulations)

Phase 2 of Widget-Native Quant Console: expose every var_engine module as a
ModuleSpec so the unified registry can run them.

Each runner below is a thin adapter that:
  * reads vol/correlation from the Context Store when a scope is supplied,
  * falls back to values passed directly in the context,
  * otherwise uses the same defaults as VaR_Tools_Simulations/main.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_VAR_DIR = Path(__file__).resolve().parent
if str(_VAR_DIR) not in sys.path:
    sys.path.insert(0, str(_VAR_DIR))

import numpy as np

from shared.module_registry import ArchiveHint, InputSpec, ModuleResult, ModuleSpec


def _failed(exc: Exception) -> ModuleResult:
    return ModuleResult(status="failed", artifacts=[], metrics={"error": str(exc)}, context_patch=None)


def _context_store() -> Any:
    from shared.context_store import ContextStore
    return ContextStore()


def _resolve_vol(context: dict[str, Any], tickers: list[str]) -> np.ndarray:
    """Resolve volatilities: context_store -> context['volatilities'] -> default 0.25."""
    vols = context.get("volatilities") or context.get("vols")
    if vols is not None:
        return np.asarray(vols, dtype=float)
    store = _context_store()
    scope: dict[str, Any] = {}
    if len(tickers) == 1:
        scope["ticker"] = tickers[0]
    else:
        scope["basket"] = tickers
    stored = store.get(scope, "garch_conditional_vol")
    if isinstance(stored, (int, float)) and not isinstance(stored, bool) and stored > 0:
        return np.full(len(tickers), float(stored), dtype=float)
    return np.full(len(tickers), 0.25, dtype=float)


def _resolve_corr(context: dict[str, Any], tickers: list[str]) -> np.ndarray:
    """Resolve correlation matrix: context['corr_matrix'] -> context_store -> identity."""
    corr = context.get("corr_matrix") or context.get("correlation_matrix")
    if corr is not None:
        return np.asarray(corr, dtype=float)
    store = _context_store()
    scope: dict[str, Any] = {"basket": tickers} if len(tickers) > 1 else {"ticker": tickers[0]}
    stored = store.get(scope, "correlation_matrix")
    if stored is not None:
        return np.asarray(stored, dtype=float)
    return np.eye(len(tickers))


def _extract_tickers(context: dict[str, Any]) -> list[str]:
    focus = context.get("focus") or {}
    tickers_raw = context.get("tickers") or focus.get("tickers") or context.get("ticker") or focus.get("ticker")
    if isinstance(tickers_raw, str):
        return [t.strip().upper() for t in tickers_raw.split(",") if t.strip()]
    if isinstance(tickers_raw, list):
        return [str(t).strip().upper() for t in tickers_raw if t]
    return []


def _extract_positions(context: dict[str, Any], n: int) -> np.ndarray:
    focus = context.get("focus") or {}
    positions = context.get("positions") or focus.get("positions")
    if positions is not None:
        return np.asarray(positions, dtype=float)
    return np.ones(n, dtype=float)


def _run_corr_sim(context: dict[str, Any]) -> ModuleResult:
    try:
        from var_engine import corr_sim
        from var_engine.corr_sim import CorrSimInputs
        tickers = _extract_tickers(context)
        if len(tickers) < 2:
            raise ValueError("corr_sim requires at least 2 tickers")
        positions = _extract_positions(context, len(tickers))
        vols = _resolve_vol(context, tickers)
        corr = _resolve_corr(context, tickers)
        res = corr_sim(
            CorrSimInputs(
                current_prices=np.ones(len(tickers), dtype=float),
                n_shares=positions[: len(tickers)],
                volatilities=vols[: len(tickers)],
                corr_matrix=corr,
                var_days=float(context.get("horizon_days") or 10.0),
                trading_days=252.0,
                confidence=float(context.get("confidence") or 0.99),
                n_sims=int(context.get("n_sims") or 10_000),
                seed=int(context.get("seed") or 42),
                asset_names=tickers,
            )
        )
        return ModuleResult(status="ok", artifacts=[], metrics={"var": res.var, "cvar": res.cvar, "tickers": tickers}, context_patch=None)
    except Exception as exc:
        return _failed(exc)


def _run_mc_sim(context: dict[str, Any]) -> ModuleResult:
    try:
        from var_engine import mc_sim
        from var_engine.mc_sim import MCSimInputs, Position
        tickers = _extract_tickers(context)
        if not tickers:
            raise ValueError("mc_sim requires context['tickers']")
        positions = _extract_positions(context, len(tickers))
        vols = _resolve_vol(context, tickers)
        corr = _resolve_corr(context, tickers)
        res = mc_sim(
            MCSimInputs(
                market_ids=tickers,
                spot_prices=np.ones(len(tickers), dtype=float),
                volatilities=vols[: len(tickers)],
                corr_matrix=corr,
                risk_free=float(context.get("risk_free") or context.get("r") or 0.05),
                trading_days=252.0,
                var_days=float(context.get("horizon_days") or 10.0),
                confidence=float(context.get("confidence") or 0.99),
                n_sims=int(context.get("n_sims") or 50_000),
                seed=int(context.get("seed") or 42),
                positions=[Position(pos_type=1, market_id=t, quantity=float(positions[i])) for i, t in enumerate(tickers)],
            )
        )
        return ModuleResult(status="ok", artifacts=[], metrics={"var": res.var_full, "cvar": res.cvar_full, "tickers": tickers}, context_patch=None)
    except Exception as exc:
        return _failed(exc)


def _run_hist_sim(context: dict[str, Any]) -> ModuleResult:
    try:
        from var_engine.hist_sim import HistSimInputs, run as hist_sim_run
        tickers = _extract_tickers(context)
        if not tickers:
            raise ValueError("hist_sim requires context['tickers']")
        positions = _extract_positions(context, len(tickers))
        res = hist_sim_run(
            HistSimInputs(
                tickers=tickers,
                position_vals=positions[: len(tickers)],
                var_days=float(context.get("horizon_days") or 10.0),
                trading_days=252.0,
                confidence=float(context.get("confidence") or 0.99),
                method=str(context.get("method") or "basic"),
                returns_dict=context.get("returns_dict"),
            )
        )
        return ModuleResult(status="ok", artifacts=[], metrics={"var": res.var, "cvar": res.cvar, "tickers": tickers, "method": res.method}, context_patch=None)
    except Exception as exc:
        return _failed(exc)


def _run_copulas(context: dict[str, Any]) -> ModuleResult:
    try:
        from var_engine import copula_var
        from var_engine.copulas import CopulaInputs
        tickers = _extract_tickers(context)
        if not tickers:
            raise ValueError("copulas requires context['tickers']")
        n = len(tickers)
        positions = _extract_positions(context, n)
        vols = _resolve_vol(context, tickers)
        corr = _resolve_corr(context, tickers)
        res = copula_var(
            CopulaInputs(
                tickers=tickers,
                position_vals=positions[:n],
                volatilities=vols[:n],
                corr_matrix=corr,
                copula_type=str(context.get("copula_type") or "student_t"),
                var_days=float(context.get("horizon_days") or 10.0),
                trading_days=252.0,
                confidence=float(context.get("confidence") or 0.99),
                n_sims=int(context.get("n_sims") or 50_000),
                seed=int(context.get("seed") or 42),
                spot_prices=np.ones(n, dtype=float),
            )
        )
        return ModuleResult(status="ok", artifacts=[], metrics={"var": res.var, "cvar": res.cvar, "copula_type": res.copula_type}, context_patch=None)
    except Exception as exc:
        return _failed(exc)


def _run_forex_var(context: dict[str, Any]) -> ModuleResult:
    try:
        from var_engine import forex_var
        from var_engine.forex_var import ForexVaRInputs
        tickers = _extract_tickers(context)
        if not tickers:
            raise ValueError("forex_var requires context['tickers']")
        n = len(tickers)
        positions = _extract_positions(context, n)
        vols = _resolve_vol(context, tickers)
        corr = _resolve_corr(context, tickers)
        is_fx = np.asarray(context.get("is_fx") or [False] * n, dtype=bool)
        res = forex_var(
            ForexVaRInputs(
                asset_names=tickers,
                volatilities=vols[:n],
                corr_matrix=corr,
                positions_home=positions[:n],
                is_fx=is_fx[:n],
                var_days=float(context.get("horizon_days") or 10.0),
                trading_days=252.0,
                confidence=float(context.get("confidence") or 0.99),
            )
        )
        return ModuleResult(status="ok", artifacts=[], metrics={"total_var": res.total_var, "equity_var": res.equity_var, "fx_var": res.fx_var, "pairs": tickers}, context_patch=None)
    except Exception as exc:
        return _failed(exc)


def _run_cashflow_map(context: dict[str, Any]) -> ModuleResult:
    try:
        from var_engine import cashflow_map
        from var_engine.cashflow_map import CashFlowMapInputs, Vertex, CashFlow
        vertices = context.get("vertices") or []
        cashflows = context.get("cashflows") or []
        corr_matrix = np.asarray(context.get("corr_matrix") or [[1.0]], dtype=float)
        verts = [Vertex(**v) for v in vertices]
        cfs = [CashFlow(**c) for c in cashflows]
        inp = CashFlowMapInputs(vertices=verts, cashflows=cfs, corr_matrix=corr_matrix)
        res = cashflow_map(inp)
        return ModuleResult(status="ok", artifacts=[], metrics={"portfolio_var": res.portfolio_var, "vertex_totals": res.vertex_totals.tolist()}, context_patch=None)
    except Exception as exc:
        return _failed(exc)


def _run_stress_test(context: dict[str, Any]) -> ModuleResult:
    try:
        from var_engine import stress_test as st
        from var_engine.stress_test import StressTestInputs
        names = context.get("asset_names") or _extract_tickers(context) or []
        if not names:
            raise ValueError("stress_test requires context['asset_names'] or context['tickers']")
        n = len(names)
        positions = np.asarray(context.get("positions") or [1.0] * n, dtype=float)
        base_vols = np.asarray(context.get("base_vols") or [0.2] * n, dtype=float)
        base_corr = np.asarray(context.get("base_corr") or np.eye(n), dtype=float)
        vol_shocks = np.asarray(context.get("vol_shocks") or [0.0] * n, dtype=float)
        corr_shocks = np.asarray(context.get("corr_shocks") or np.zeros((n, n)), dtype=float)
        inp = StressTestInputs(
            asset_names=names,
            positions=positions,
            base_vols=base_vols,
            base_corr=base_corr,
            vol_shocks=vol_shocks,
            corr_shocks=corr_shocks,
            var_days=float(context.get("horizon_days") or 10.0),
            trading_days=252.0,
            confidence=float(context.get("confidence") or 0.99),
        )
        res = st(inp)
        return ModuleResult(status="ok", artifacts=[], metrics={"base_var": res.base_var, "stress_var": res.stress_var}, context_patch=None)
    except Exception as exc:
        return _failed(exc)


def _build_synthetic_returns(n: int, vols: np.ndarray | None = None, corr: np.ndarray | None = None, T: int = 252) -> np.ndarray:
    rng = np.random.default_rng(42)
    if vols is None:
        vols = np.full(n, 0.02, dtype=float)
    if corr is None:
        corr = np.eye(n)
    # Cholesky of correlation, scale by vols to get covariance-consistent series.
    L = np.linalg.cholesky(corr)
    z = rng.normal(0, 1, size=(T, n))
    rets = z @ L.T
    rets = rets * vols
    return rets


def _run_var_agg(context: dict[str, Any]) -> ModuleResult:
    try:
        from var_engine.var_agg import VaRAggInputs, run as var_agg_run
        positions = np.asarray(context.get("positions") or [1.0], dtype=float)
        n = len(positions)
        vols = np.asarray(context.get("volatilities") or [0.2] * n, dtype=float)
        corr = np.asarray(context.get("corr_matrix") or np.eye(n), dtype=float)
        group_mask = np.asarray(context.get("group_mask") or [0] * n, dtype=int)
        res = var_agg_run(
            VaRAggInputs(
                asset_names=[f"Asset{i}" for i in range(n)],
                positions=positions,
                group_mask=group_mask,
                returns=_build_synthetic_returns(n, vols, corr),
                var_days=float(context.get("horizon_days") or 10.0),
                trading_days=252.0,
                confidence=float(context.get("confidence") or 0.99),
            )
        )
        return ModuleResult(status="ok", artifacts=[], metrics={"var": res.total_var, "cvar": res.total_cvar}, context_patch=None)
    except Exception as exc:
        return _failed(exc)


def _run_hedge_optimizer(context: dict[str, Any]) -> ModuleResult:
    try:
        from var_engine import hedge_optimizer as ho
        from var_engine.hedge_optimizer import HedgeOptimizerInputs, HedgeInstrument
        tickers = _extract_tickers(context)
        if not tickers:
            raise ValueError("hedge_optimizer requires context['tickers']")
        n = len(tickers)
        positions = np.asarray(context.get("positions") or [1.0] * n, dtype=float)
        vols = _resolve_vol(context, tickers)
        corr = _resolve_corr(context, tickers)
        cov = np.diag(vols) @ corr @ np.diag(vols)
        instruments = [
            HedgeInstrument(
                name=t,
                volatility=float(vols[i]),
                correlation_to_positions=np.array([corr[i, 0]]),
                beta=1.0,
            )
            for i, t in enumerate(tickers)
        ]
        res = ho.min_var_hedge(
            HedgeOptimizerInputs(
                positions=positions,
                cov_matrix=cov,
                hedge_instruments=instruments,
                var_horizon=float(context.get("horizon_days") or 10.0),
                trading_days=252.0,
                confidence=float(context.get("confidence") or 0.99),
            )
        )
        return ModuleResult(status="ok", artifacts=[], metrics={"optimal_weights": res.optimal_weights.tolist(), "base_var": res.base_var, "hedged_var": res.hedged_var}, context_patch=None)
    except Exception as exc:
        return _failed(exc)


def _run_price_dist(context: dict[str, Any]) -> ModuleResult:
    try:
        from var_engine import price_dist
        prices = np.asarray(context.get("prices") or [], dtype=float)
        if len(prices) < 3:
            raise ValueError("price_dist requires context['prices'] array with at least 3 points")
        res = price_dist.price_distribution(prices)
        return ModuleResult(status="ok", artifacts=[], metrics={
            "annual_vol": res.annual_vol, "skewness": res.skewness,
            "excess_kurtosis": res.excess_kurtosis, "n": res.n,
        }, context_patch=None)
    except Exception as exc:
        return _failed(exc)


MODULES: list[ModuleSpec] = [
    ModuleSpec(
        name="Historical Simulation",
        slug="hist_sim",
        suite="var_tools",
        category="var",
        run=_run_hist_sim,
        cli_entry=None,
        default_selected=True,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_only"),
        description="Historical simulation VaR/CVaR for a basket of tickers.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"tickers": ["SPY", "AAPL", "TSLA"], "positions": [100000, -50000, 20000], "confidence": 0.99, "horizon_days": 10},
    ),
    ModuleSpec(
        name="Monte Carlo Simulation",
        slug="mc_sim",
        suite="var_tools",
        category="var",
        run=_run_mc_sim,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_only"),
        description="Parametric Monte Carlo VaR/CVaR with EWMA volatilities.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"tickers": ["SPY", "AAPL", "TSLA"], "positions": [100000, -50000, 20000], "confidence": 0.99, "horizon_days": 10},
    ),
    ModuleSpec(
        name="Correlation Simulation",
        slug="corr_sim",
        suite="var_tools",
        category="var",
        run=_run_corr_sim,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_only"),
        description="Multi-factor correlation model VaR for equity baskets.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"tickers": ["SPY", "AAPL", "TSLA"], "positions": [100000, -50000, 20000], "confidence": 0.99, "horizon_days": 10},
    ),
    ModuleSpec(
        name="Copula VaR",
        slug="copulas",
        suite="var_tools",
        category="var",
        run=_run_copulas,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_only"),
        description="Gaussian, Student-T and Clayton copula VaR with fat-tailed marginals.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"tickers": ["SPY", "AAPL", "TSLA"], "positions": [100000, -50000, 20000], "copula_type": "student_t", "confidence": 0.99, "horizon_days": 10},
    ),
    ModuleSpec(
        name="Forex VaR",
        slug="forex_var",
        suite="var_tools",
        category="var",
        run=_run_forex_var,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_only"),
        description="Currency-pair VaR with USD reference.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"tickers": ["EURUSD", "GBPUSD", "JPYUSD"], "positions": [100000, -50000, 20000], "confidence": 0.99, "horizon_days": 10},
    ),
    ModuleSpec(
        name="Cash Flow Mapping",
        slug="cashflow_map",
        suite="var_tools",
        category="var",
        run=_run_cashflow_map,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="global"),
        description="Map arbitrary cash flows to rate vertices and compute parametric VaR.",
        inputs=InputSpec(ticker="none"),
        output_kind="metrics",
        sample={"vertices": [{"maturity": 0.25, "zero_rate": 0.045, "vol": 0.0096}, {"maturity": 0.5, "zero_rate": 0.05, "vol": 0.016}], "cashflows": [{"maturity": 0.3, "amount": 120000}], "corr_matrix": [[1.0, 0.9], [0.9, 1.0]]},
    ),
    ModuleSpec(
        name="Stress Test",
        slug="stress_test",
        suite="var_tools",
        category="var",
        run=_run_stress_test,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_only"),
        description="Base vs stressed VaR/CVaR with user-defined vol/correlation shocks.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"asset_names": ["SPY", "AAPL"], "positions": [100000, -50000], "base_vols": [0.2, 0.3], "base_corr": [[1.0, 0.5], [0.5, 1.0]], "vol_shocks": [0.05, 0.0], "corr_shocks": [[0.0, 0.1], [0.1, 0.0]]},
    ),
    ModuleSpec(
        name="VaR Aggregation",
        slug="var_agg",
        suite="var_tools",
        category="var",
        run=_run_var_agg,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_only"),
        description="Parametric portfolio VaR/CVaR aggregation from positions, vols and correlations.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"positions": [100000, -50000, 20000], "volatilities": [0.2, 0.3, 0.25], "corr_matrix": [[1.0, 0.5, 0.3], [0.5, 1.0, 0.2], [0.3, 0.2, 1.0]]},
    ),
    ModuleSpec(
        name="Hedge Optimizer",
        slug="hedge_optimizer",
        suite="var_tools",
        category="var",
        run=_run_hedge_optimizer,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_only"),
        description="Minimum-variance hedge ratios for a basket of exposures.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"tickers": ["SPY", "AAPL"], "positions": [100000, -50000], "volatilities": [0.2, 0.3], "corr_matrix": [[1.0, 0.5], [0.5, 1.0]]},
    ),
    ModuleSpec(
        name="Price Distribution",
        slug="price_dist",
        suite="var_tools",
        category="var",
        run=_run_price_dist,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_only"),
        description="Return skewness/kurtosis/normality tests on a price series.",
        inputs=InputSpec(ticker="none"),
        output_kind="chart",
        sample={"prices": [100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0]},
    ),
]
