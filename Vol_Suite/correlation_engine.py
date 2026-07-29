#!/usr/bin/env python3
# correlation_engine.py
# Correlation, covariance, and basket statistics engine.

import math
import warnings
import os
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from thetadata_client import ThetaDataController
import index_membership as idxmem

warnings.filterwarnings("ignore", category=FutureWarning, module="pandas")

DEFAULT_A = 252
RISK_FREE_RATE = 0.05

# Try to import scipy for p-values; fallback if not available
try:
    from scipy import stats as scipy_stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


@dataclass
class CorrelationResult:
    ticker1: str
    ticker2: str
    correlation: float
    covariance: float
    beta: float
    p_value: float
    n_observations: int


@dataclass
class BasketStats:
    tickers: List[str]
    weights: np.ndarray
    correlation_matrix: np.ndarray
    covariance_matrix: np.ndarray
    basket_vol: float
    basket_beta: float
    basket_expected_return: float
    basket_sharpe: float
    basket_alpha: float
    individual_vols: Dict[str, float]
    individual_returns: Dict[str, float]
    individual_betas: Dict[str, float]
    correlation_pairs: List[CorrelationResult]
    dispersion_score: float
    diversification_ratio: float
    dropped_tickers: List[str] = None  # tickers that were requested but had no usable price history


# ---------- Price history (ThetaData first, yfinance fallback only) ----------

def _parse_period_years(period: str) -> float:
    period = (period or "2y").strip().lower()
    try:
        if period.endswith("y"):
            return float(period[:-1])
        if period.endswith("mo"):
            return float(period[:-2]) / 12.0
        if period.endswith("d"):
            return float(period[:-1]) / 365.25
    except ValueError:
        pass
    return 2.0


def _rows_to_close_series(rows: List[dict]) -> Optional[pd.Series]:
    """Parse ThetaData hist rows into a date-indexed close-price Series.

    Confirmed live response shape for /api/theta/hist/stock/eod (2026-07-19):
    ["created","last_trade","open","high","low","close","volume","count",
     "bid_size","bid_exchange","bid","bid_condition","ask_size","ask_exchange",
     "ask","ask_condition"]
    There is no "date" field -- "created" is a full timestamp string like
    "2026-07-01T17:15:06.172" and is the field to use. Still falls back to
    date/Date/datetime/last_trade in case the proxy varies this by root or
    account tier.
    """
    if not rows:
        return None
    dates, closes = [], []
    for row in rows:
        date_val = (row.get('created') or row.get('date') or row.get('Date')
                    or row.get('datetime') or row.get('last_trade'))
        close_val = row.get('close') or row.get('Close') or row.get('c')
        if date_val is None or close_val is None:
            continue
        try:
            date_str = str(int(date_val)) if not isinstance(date_val, str) else date_val
            if len(date_str) == 8 and date_str.isdigit():
                d = datetime.strptime(date_str, "%Y%m%d")
            else:
                d = pd.to_datetime(date_str).normalize()
            dates.append(d)
            closes.append(float(close_val))
        except (ValueError, TypeError):
            continue
    if not dates:
        return None
    s = pd.Series(closes, index=pd.DatetimeIndex(dates)).sort_index()
    return s[~s.index.duplicated(keep='last')]


def fetch_price_history(tickers: List[str], period: str = "2y") -> pd.DataFrame:
    """Fetch daily close price history for `tickers`. ThetaData is the primary
    source (per project convention); yfinance is used only as a per-ticker
    fallback if ThetaData is unavailable or doesn't cover a given ticker."""
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=int(_parse_period_years(period) * 365.25))
    end_str, start_str = end_date.strftime("%Y%m%d"), start_date.strftime("%Y%m%d")

    series: Dict[str, pd.Series] = {}
    try:
        td = ThetaDataController()
        for t in tickers:
            try:
                rows = td.hist_stock_eod(t, start_str, end_str)
                s = _rows_to_close_series(rows)
                if s is not None and len(s) > 5:
                    series[t] = s
                else:
                    print(f"  [ThetaData] No usable history for {t}.")
            except Exception as e:
                print(f"  [ThetaData] History fetch failed for {t}: {e}")
        td.close()
    except Exception as e:
        print(f"  [ThetaData] Client unavailable ({e}). Falling back to yfinance for all tickers.")

    # yahoo purged: PotatoHedge/ThetaData EOD history is the sole source. Any
    # tickers ThetaData couldn't cover are simply reported missing (the paginated
    # hist_stock_eod fix in thetadata_client made this path reliable on its own).
    missing = [t for t in tickers if t not in series]
    if missing:
        print(f"  [ThetaData] No history for {len(missing)} ticker(s): {', '.join(missing)} (yahoo fallback removed).")

    if not series:
        raise ValueError(f"Could not fetch price history for any of {tickers} from PotatoHedge/ThetaData.")

    df = pd.DataFrame(series).dropna(axis=1, how='all').ffill().dropna()
    # Sort ascending by date before returning. Every consumer computes log
    # returns as log(p_t / p_t-1), which is silently wrong if rows arrive out of
    # order -- and out-of-order returns destroy volatility clustering, which is
    # exactly what a GARCH fit depends on. Cheap to enforce, hard to notice when
    # it's missing.
    df = df.sort_index()
    return df


def compute_log_returns(prices: pd.DataFrame) -> pd.DataFrame:
    return np.log(prices / prices.shift(1)).dropna()


def compute_correlation_matrix(returns: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, int]:
    n = len(returns)
    corr = returns.corr().values
    cov = returns.cov().values * DEFAULT_A
    return corr, cov, n


def compute_correlation_pairs(returns: pd.DataFrame, corr_matrix: np.ndarray,
                              cov_matrix: np.ndarray) -> List[CorrelationResult]:
    """Per-pair correlation/covariance/beta are read directly off the matrix
    already computed by compute_correlation_matrix (pandas .corr()/.cov()) --
    NOT recomputed by hand. Only the p-value (and observation count, since
    p-value needs the pairwise-non-NaN mask) genuinely require going back to
    the raw return series; everything else was previously being computed
    twice, independently, via two different code paths that happened to
    agree."""
    tickers = returns.columns.tolist()
    results = []
    for i, t1 in enumerate(tickers):
        for j, t2 in enumerate(tickers):
            if i >= j:
                continue
            r1 = returns[t1].values
            r2 = returns[t2].values
            mask = ~(np.isnan(r1) | np.isnan(r2))
            n = int(mask.sum())
            if n < 10:
                continue

            corr = float(corr_matrix[i, j])
            cov_annual = float(cov_matrix[i, j])
            var1_annual = float(cov_matrix[i, i])
            beta = cov_annual / var1_annual if var1_annual > 0 else 0.0

            if HAS_SCIPY:
                _, p_value = scipy_stats.pearsonr(r1[mask], r2[mask])
            else:
                t_stat = corr * math.sqrt((n - 2) / (1 - corr * corr)) if abs(corr) < 1 else 0.0
                p_value = min(1.0, 2.0 / (1.0 + abs(t_stat))) if abs(t_stat) > 0 else 1.0

            results.append(CorrelationResult(
                ticker1=t1, ticker2=t2,
                correlation=corr, covariance=cov_annual,
                beta=beta, p_value=float(p_value),
                n_observations=n
            ))
    return results


def compute_basket_stats(tickers: List[str], weights: Optional[List[float]] = None,
                         market_ticker: str = "SPY", period: str = "2y") -> BasketStats:
    all_tickers = list(dict.fromkeys(tickers + [market_ticker]))
    prices = fetch_price_history(all_tickers, period=period)
    returns = compute_log_returns(prices)

    # FIX 2: Filter once at the top into a single source of truth, used everywhere below.
    valid_tickers = [t for t in tickers if t in returns.columns]
    dropped = [t for t in tickers if t not in valid_tickers]
    if dropped:
        print(f"  [correlation_engine] Dropping {len(dropped)} ticker(s) with no usable "
              f"price history: {dropped}")
    if not valid_tickers:
        raise ValueError("No tickers in basket have usable price history.")

    # Re-filter weights positionally against the ORIGINAL tickers/weights pairing,
    # then renormalize over the survivors.
    if weights is None:
        valid_weights = np.ones(len(valid_tickers)) / len(valid_tickers)
    else:
        weights_arr = np.array(weights)
        keep_mask = [t in valid_tickers for t in tickers]
        valid_weights = weights_arr[keep_mask]
        valid_weights = valid_weights / np.sum(valid_weights)

    basket_returns = returns[valid_tickers].dropna()
    market_returns = returns[market_ticker].dropna()
    common_idx = basket_returns.index.intersection(market_returns.index)
    basket_returns = basket_returns.loc[common_idx]
    market_returns = market_returns.loc[common_idx]

    corr_matrix, cov_matrix, _ = compute_correlation_matrix(basket_returns)

    individual_vols = {}
    individual_returns = {}
    individual_betas = {}

    # Use valid_tickers instead of the original tickers list
    for t in valid_tickers:
        r = basket_returns[t].values
        individual_vols[t] = float(np.std(r, ddof=1) * math.sqrt(DEFAULT_A))
        individual_returns[t] = float(np.mean(r) * DEFAULT_A)
        market_r = market_returns.values.flatten()
        joint = ~(np.isnan(r) | np.isnan(market_r))
        r_clean = r[joint]
        m_clean = market_r[joint]
        if len(r_clean) > 10:
            beta = np.cov(r_clean, m_clean, ddof=1)[0, 1] / np.var(m_clean, ddof=1)
            individual_betas[t] = float(beta)
        else:
            individual_betas[t] = 0.0

    basket_returns_series = basket_returns @ valid_weights
    basket_vol = float(np.std(basket_returns_series, ddof=1) * math.sqrt(DEFAULT_A))
    basket_expected_return = float(np.mean(basket_returns_series) * DEFAULT_A)

    joint = ~(np.isnan(basket_returns_series.values) | np.isnan(market_returns.values.flatten()))
    br_clean = basket_returns_series.values[joint]
    mr_clean = market_returns.values[joint].flatten()
    if len(br_clean) > 10:
        basket_beta = float(np.cov(br_clean, mr_clean, ddof=1)[0, 1] / np.var(mr_clean, ddof=1))
    else:
        basket_beta = 0.0

    market_ret = float(np.mean(market_returns.values) * DEFAULT_A)

    excess_return = basket_expected_return - RISK_FREE_RATE
    basket_sharpe = excess_return / basket_vol if basket_vol > 0 else 0.0

    if basket_beta != 0:
        expected_market_return = RISK_FREE_RATE + basket_beta * (market_ret - RISK_FREE_RATE)
        basket_alpha = basket_expected_return - expected_market_return
    else:
        basket_alpha = 0.0

    correlation_pairs = compute_correlation_pairs(basket_returns, corr_matrix, cov_matrix)
    avg_corr = np.mean([p.correlation for p in correlation_pairs]) if correlation_pairs else 0.0

    # Use valid_tickers and valid_weights instead of original lists
    weighted_avg_vol = np.sum([w * individual_vols[t] for w, t in zip(valid_weights, valid_tickers)])
    diversification_ratio = weighted_avg_vol / basket_vol if basket_vol > 0 else 1.0

    return BasketStats(
        tickers=valid_tickers, weights=valid_weights,
        correlation_matrix=corr_matrix, covariance_matrix=cov_matrix,
        basket_vol=basket_vol, basket_beta=basket_beta,
        basket_expected_return=basket_expected_return,
        basket_sharpe=basket_sharpe, basket_alpha=basket_alpha,
        individual_vols=individual_vols,
        individual_returns=individual_returns,
        individual_betas=individual_betas,
        correlation_pairs=correlation_pairs,
        dispersion_score=avg_corr,
        diversification_ratio=diversification_ratio,
        dropped_tickers=dropped
    )


def find_correlated_clusters(tickers: List[str], threshold: float = 0.7,
                              min_cluster_size: int = 2) -> List[List[str]]:
    prices = fetch_price_history(tickers, period="2y")
    returns = compute_log_returns(prices)
    corr, _, _ = compute_correlation_matrix(returns)
    ticker_list = returns.columns.tolist()
    n = len(ticker_list)
    adj = {t: set() for t in ticker_list}
    for i in range(n):
        for j in range(i + 1, n):
            if abs(corr[i, j]) >= threshold:
                adj[ticker_list[i]].add(ticker_list[j])
                adj[ticker_list[j]].add(ticker_list[i])
    visited = set()
    clusters = []
    for t in ticker_list:
        if t not in visited:
            cluster = []
            queue = [t]
            while queue:
                node = queue.pop(0)
                if node not in visited:
                    visited.add(node)
                    cluster.append(node)
                    queue.extend(adj[node] - visited)
            if len(cluster) >= min_cluster_size:
                clusters.append(cluster)
    return clusters


def build_index_basket(index_ticker: str, top_n: int = 10, include_index: bool = False) -> List[str]:
    """Build a basket from an index/sector ETF's real, weighted constituents
    (via index_membership.py), for dispersion modeling against that index.

    Replaces the old auto_populate_tickers, which greedily picked the least-
    correlated names from a fixed, asset-class-mixed universe (SPY, TLT, GLD,
    USO, mega-caps, ...) starting from an arbitrary seed ticker -- not tied to
    any real index, and not useful for a dispersion trade specifically, which
    needs an index's actual constituents at their actual weights.
    """
    constituents = idxmem.get_index_constituents(index_ticker, top_n=top_n)
    tickers = [sym for sym, _weight in constituents]
    if include_index and index_ticker.upper() not in tickers:
        tickers = [index_ticker.upper()] + tickers
    return tickers


def print_basket_report(stats: BasketStats):
    print("\n" + "=" * 90)
    print(f"BASKET STATISTICS REPORT — {' | '.join(stats.tickers)}")
    print("=" * 90)
    print(f"\n--- Basket Summary ---")
    print(f"  Weights:              {' | '.join([f'{w:.2%}' for w in stats.weights])}")
    print(f"  Basket Vol (ann.):    {stats.basket_vol:.2%}")
    print(f"  Basket Beta (vs SPY): {stats.basket_beta:.3f}")
    print(f"  Basket Exp Return:    {stats.basket_expected_return:.2%}")
    print(f"  Basket Sharpe:        {stats.basket_sharpe:.3f}")
    print(f"  Basket Alpha (ann.):  {stats.basket_alpha:.2%}")
    print(f"  Dispersion Score:     {stats.dispersion_score:.3f} (avg pairwise corr)")
    print(f"  Diversification Ratio:{stats.diversification_ratio:.3f}")
    print(f"\n--- Individual Ticker Stats ---")
    print(f"{'Ticker':<10} {'Weight':<10} {'Vol (ann)':<12} {'Return (ann)':<15} {'Beta vs SPY':<12}")
    print("-" * 60)
    for i, t in enumerate(stats.tickers):
        print(f"{t:<10} {stats.weights[i]:<10.2%} {stats.individual_vols[t]:<12.2%} {stats.individual_returns[t]:<15.2%} {stats.individual_betas.get(t, 0):<12.3f}")
    print(f"\n--- Correlation Matrix ---")
    print(f"{'':<12}", end="")
    for t in stats.tickers:
        print(f"{t:<12}", end="")
    print()
    for i, t1 in enumerate(stats.tickers):
        print(f"{t1:<12}", end="")
        for j in range(len(stats.tickers)):
            print(f"{stats.correlation_matrix[i,j]:<12.4f}", end="")
        print()
    print(f"\n--- Pairwise Correlations (sorted by |corr|) ---")
    sorted_pairs = sorted(stats.correlation_pairs, key=lambda p: abs(p.correlation), reverse=True)
    print(f"{'Pair':<25} {'Correlation':<15} {'Covariance':<15} {'Beta':<12} {'p-value':<12}")
    print("-" * 80)
    for p in sorted_pairs:
        print(f"{p.ticker1} vs {p.ticker2:<15} {p.correlation:<15.4f} {p.covariance:<15.6f} {p.beta:<12.3f} {p.p_value:<12.6f}")
    print(f"\n--- Trading Implications ---")
    if not stats.correlation_pairs:
        # avg pairwise correlation over an empty pair set comes out as 0.0, which
        # would otherwise print as "LOW CORRELATION -> good for dispersion". That
        # is arithmetic, not a market observation.
        print("  No pairwise correlations available (basket has fewer than 2 usable names).")
        print("  Dispersion score of 0.00 above is an empty-set artifact, not a low-correlation")
        print("  reading -- no dispersion conclusion can be drawn from this run.")
    elif stats.dispersion_score < 0.3:
        print(f"  LOW CORRELATION ({stats.dispersion_score:.2f}) -> Good environment for DISPERSION TRADING")
    elif stats.dispersion_score < 0.6:
        print(f"  MODERATE CORRELATION ({stats.dispersion_score:.2f}) -> Mixed environment for dispersion")
    else:
        print(f"  HIGH CORRELATION ({stats.dispersion_score:.2f}) -> Weak dispersion setup (constituents move together)")
    if stats.basket_beta > 1.0:
        print(f"\n  HIGH BETA BASKET ({stats.basket_beta:.2f}) -> Consider portfolio insurance")
    print("=" * 90)


def main():
    print("=" * 60)
    print("CORRELATION & COVARIANCE ENGINE")
    print("=" * 60)
    print("\nChoose mode:")
    print("  1. Build basket from an index/sector ETF's real constituents")
    print("  2. Enter custom tickers")
    print("  3. Find correlated clusters from a universe")
    mode = input("\nSelect mode (1/2/3): ").strip()
    if mode == "1":
        idx_input = input("Index/sector ETF ticker (e.g. SPY, QQQ, XLK): ").strip().upper() or "SPY"
        count_input = input("Number of constituents to include (default 10): ").strip()
        count = int(count_input) if count_input else 10
        tickers = build_index_basket(idx_input, top_n=count)
        print(f"\nBasket from {idx_input} (top {count} by weight): {', '.join(tickers)}")
    elif mode == "2":
        ticker_input = input("Enter tickers (comma-separated): ").strip()
        tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
    elif mode == "3":
        ticker_input = input("Enter universe of tickers (comma-separated): ").strip()
        universe = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
        threshold_input = input("Correlation threshold (default 0.7): ").strip()
        threshold = float(threshold_input) if threshold_input else 0.7
        clusters = find_correlated_clusters(universe, threshold=threshold)
        print(f"\nFound {len(clusters)} correlated clusters:")
        for i, cluster in enumerate(clusters, 1):
            print(f"  Cluster {i}: {', '.join(cluster)}")
        if clusters:
            select = input("\nEnter cluster number to analyze (or 'all'): ").strip()
            if select.lower() == 'all':
                tickers = [t for cluster in clusters for t in cluster]
            else:
                try:
                    idx = int(select) - 1
                    tickers = clusters[idx]
                except (ValueError, IndexError):
                    tickers = []
        else:
            tickers = []
    else:
        print("Invalid mode.")
        return
    if not tickers or len(tickers) < 2:
        print("Need at least 2 tickers for correlation analysis.")
        return
    tickers = list(dict.fromkeys(tickers))
    weight_input = input("\nCustom weights? (comma-separated, or 'equal'): ").strip()
    if weight_input and weight_input.lower() != 'equal':
        try:
            weights = [float(w.strip()) for w in weight_input.split(",")]
            if len(weights) != len(tickers):
                print(f"  Weight count mismatch. Using equal weights.")
                weights = None
        except ValueError:
            print(f"  Invalid weights. Using equal weights.")
            weights = None
    else:
        weights = None
    market_input = input("Market benchmark for beta/alpha (default SPY): ").strip().upper()
    market = market_input if market_input else "SPY"
    print(f"\nFetching data for {len(tickers)} tickers...")
    stats = compute_basket_stats(tickers, weights=weights, market_ticker=market)
    print_basket_report(stats)
    choice = input("\nExport basket report to CSV? (y/n): ").strip().lower()
    if choice == 'y':
        out_dir = os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        corr_df = pd.DataFrame(stats.correlation_matrix, index=stats.tickers, columns=stats.tickers)
        corr_file = os.path.join(out_dir, f"correlation_matrix_{timestamp}.csv")
        corr_df.to_csv(corr_file)
        print(f"  Correlation matrix: {corr_file}")
        pair_data = [{"Ticker1": p.ticker1, "Ticker2": p.ticker2,
                       "Correlation": round(p.correlation, 4),
                       "Covariance": round(p.covariance, 6),
                       "Beta": round(p.beta, 4),
                       "p_value": round(p.p_value, 6)} for p in stats.correlation_pairs]
        pair_df = pd.DataFrame(pair_data)
        pair_file = os.path.join(out_dir, f"correlation_pairs_{timestamp}.csv")
        pair_df.to_csv(pair_file, index=False)
        print(f"  Pair correlations: {pair_file}")
        sum_data = {"Metric": ["Basket_Vol", "Basket_Beta", "Basket_Exp_Return",
                                "Basket_Sharpe", "Basket_Alpha",
                                "Dispersion_Score", "Diversification_Ratio"],
                     "Value": [round(stats.basket_vol, 4), round(stats.basket_beta, 4),
                                round(stats.basket_expected_return, 4),
                                round(stats.basket_sharpe, 4), round(stats.basket_alpha, 4),
                                round(stats.dispersion_score, 4),
                                round(stats.diversification_ratio, 4)]}
        sum_df = pd.DataFrame(sum_data)
        sum_file = os.path.join(out_dir, f"basket_summary_{timestamp}.csv")
        sum_df.to_csv(sum_file, index=False)
        print(f"  Basket summary: {sum_file}")
    print("\nDone.")


def run_correlation_engine(tickers: List[str], weights: Optional[List[float]] = None,
                           market: str = "SPY", period: str = "2y",
                           output_dir: Optional[str] = None, save_csv: bool = True) -> Tuple[List[str], str]:
    """Programmatic runner for the correlation engine. Returns (file_paths, interpretation_text)."""
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out_dir, exist_ok=True)
    stats = compute_basket_stats(tickers, weights=weights, market_ticker=market, period=period)
    print_basket_report(stats)
    files = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    corr_df = pd.DataFrame(stats.correlation_matrix, index=stats.tickers, columns=stats.tickers)
    corr_file = os.path.join(out_dir, f"correlation_matrix_{timestamp}.csv")
    corr_df.to_csv(corr_file)
    files.append(corr_file)
    pair_data = [{"Ticker1": p.ticker1, "Ticker2": p.ticker2,
                  "Correlation": round(p.correlation, 4), "Covariance": round(p.covariance, 6),
                  "Beta": round(p.beta, 4), "p_value": round(p.p_value, 6)} for p in stats.correlation_pairs]
    pair_file = os.path.join(out_dir, f"correlation_pairs_{timestamp}.csv")
    pd.DataFrame(pair_data).to_csv(pair_file, index=False)
    files.append(pair_file)

    try:
        fig, ax = plt.subplots(figsize=(8, 6))
        im = ax.imshow(stats.correlation_matrix, cmap='RdBu_r', vmin=-1, vmax=1)
        ax.set_xticks(range(len(stats.tickers)))
        ax.set_yticks(range(len(stats.tickers)))
        ax.set_xticklabels(stats.tickers, rotation=45, ha='right')
        ax.set_yticklabels(stats.tickers)
        plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        ax.set_title('Correlation Matrix')
        heat_file = os.path.join(out_dir, f"correlation_heatmap_{timestamp}.png")
        plt.tight_layout()
        fig.savefig(heat_file, dpi=150, bbox_inches='tight')
        plt.close(fig)
        files.append(heat_file)
    except Exception as e:
        print(f"  Correlation heatmap failed: {e}")

    try:
        corr_df = pd.DataFrame(stats.correlation_matrix, index=stats.tickers, columns=stats.tickers)
        fig, ax = plt.subplots(figsize=(8, max(2, len(stats.tickers) * 0.3)))
        ax.axis('off')
        tbl = ax.table(cellText=corr_df.round(4).values, colLabels=corr_df.columns, rowLabels=corr_df.index, loc='center')
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        tbl.scale(1, 1.1)
        table_file = os.path.join(out_dir, f"correlation_matrix_table_{timestamp}.png")
        plt.tight_layout()
        fig.savefig(table_file, dpi=150, bbox_inches='tight')
        plt.close(fig)
        files.append(table_file)
    except Exception as e:
        print(f"  Correlation matrix table image failed: {e}")

    try:
        interp_lines = [f"Basket vol (ann): {stats.basket_vol:.2%}",
                        f"Basket beta: {stats.basket_beta:.3f}",
                        f"Dispersion score (avg pair corr): {stats.dispersion_score:.3f}"]
        interp_text = ' | '.join(interp_lines)
        fig, ax = plt.subplots(figsize=(8, 2))
        ax.axis('off')
        ax.text(0.01, 0.5, interp_text, fontsize=10, va='center')
        text_file = os.path.join(out_dir, f"correlation_summary_{timestamp}.png")
        plt.tight_layout()
        fig.savefig(text_file, dpi=150, bbox_inches='tight')
        plt.close(fig)
        files.append(text_file)
    except Exception as e:
        print(f"  Correlation summary image failed: {e}")

    try:
        interp_lines = [
            f"Tickers: {', '.join(stats.tickers)}",
            f"Basket vol (ann): {stats.basket_vol:.2%}",
            f"Basket beta: {stats.basket_beta:.3f}",
            f"Dispersion score (avg pair corr): {stats.dispersion_score:.3f}",
        ]
    except Exception:
        interp_lines = ["Correlation analysis completed."]
    interp = "\n".join(interp_lines)
    return files, interp, stats


if __name__ == "__main__":
    main()
