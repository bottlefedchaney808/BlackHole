"""
Options Strategy Recommender

Recommends multi-leg options strategies based on:
- Volatility regime (RICH/CHEAP/FAIR) from smile-fit analysis
- Edge strikes (BUY/SELL candidates) with IV deviations
- Greeks positioning (delta, gamma, theta, vega, vanna)
- Dealer flow (vanna concentration, gamma sign)

Outputs structured strategy artifacts for Options Suite consumption.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class StrategyLeg:
    """Single leg of a multi-leg options strategy."""

    instrument_type: str  # 'call' or 'put'
    strike: float
    quantity: int  # positive = long, negative = short
    delta_target: float = None


@dataclass
class StrategyRecommendation:
    """Complete strategy recommendation."""

    strategy_type: str  # 'call_spread', 'straddle', 'iron_condor', etc.
    legs: list[StrategyLeg]
    vol_regime: str  # 'RICH', 'CHEAP', 'FAIR'
    rationale: str
    edge_strikes_used: list[float]
    greeks_summary: dict[str, float]  # net delta, gamma, theta, vega
    rank_score: float = 0.0


class StrategyRecommender:
    """
    Recommends options strategies based on chain scan results.

    Attributes:
        chain_data: Greeks + liquidity data by strike
        edge_strikes: List of identified rich/cheap strikes with deviations
        vol_regime: 'RICH', 'CHEAP', or 'FAIR'
        current_price: Spot price
        expiry_days: Days to expiration

    Liquidity Configuration:
        MIN_OPEN_INTEREST: Minimum 50 contracts to avoid stale quotes
        MAX_BID_ASK_SPREAD: Maximum $0.50 spread to limit transaction costs
    """

    # LIQUIDITY THRESHOLDS (configurable, with rationale)
    MIN_OPEN_INTEREST = 50  # Minimum 50 contracts open interest to avoid stale quotes
    MAX_BID_ASK_SPREAD = (
        0.5  # Maximum $0.50 spread; wider spreads increase transaction costs
    )

    def __init__(
        self,
        chain_data: dict[str, list[float]],
        edge_strikes: list[dict[str, Any]],
        vol_regime: str,
        current_price: float,
        expiry_days: float,
    ):
        self.chain_data = chain_data
        self.edge_strikes = edge_strikes
        normalized_regime = (
            vol_regime.strip().upper() if isinstance(vol_regime, str) else vol_regime
        )
        if normalized_regime not in ("RICH", "CHEAP", "FAIR"):
            raise ValueError(
                f"vol_regime must be one of 'RICH', 'CHEAP', 'FAIR' (case-insensitive); got {vol_regime!r}"
            )
        self.vol_regime = normalized_regime
        self.current_price = current_price
        self.expiry_days = expiry_days

        # Build lookup dict for fast strike-to-Greeks access
        self._strike_greeks = self._build_strike_greeks_lookup()

    def _build_strike_greeks_lookup(self) -> dict[float, dict[str, float]]:
        """Create strike -> Greeks dict for fast lookup."""
        lookup = {}
        strikes = self.chain_data["strikes"]
        for i, strike in enumerate(strikes):
            lookup[strike] = {
                "delta": self.chain_data["delta"][i],
                "gamma": self.chain_data["gamma"][i],
                "theta": self.chain_data["theta"][i],
                "vega": self.chain_data["vega"][i],
                "vanna": self.chain_data["vanna"][i],
                "bid_ask_spread": self.chain_data["bid_ask_spread"][i],
                "open_interest": self.chain_data["open_interest"][i],
            }
        return lookup

    def recommend(self) -> list[dict[str, Any]]:
        """
        Generate strategy recommendations based on vol regime and edge strikes.

        Returns:
            List of strategy dicts, each with type, legs, rationale, greeks_summary, rank_score
        """
        if not self.edge_strikes:
            return []

        # Step 1: Select strategy types based on vol regime
        strategy_types = self._select_strategies_for_regime()

        # Step 2: For each strategy type, build candidate strategies
        candidates = []
        for strategy_type in strategy_types:
            for edge in self.edge_strikes:
                strategy = self._build_strategy(strategy_type, edge)
                if strategy:
                    candidates.append(strategy)

        # Step 3: Rank candidates by Greeks alignment
        candidates = self._rank_strategies(candidates)

        # Step 4: Serialize to JSON-compatible dicts
        return [self._strategy_to_dict(s) for s in candidates]

    def _select_strategies_for_regime(self) -> list[str]:
        """Map vol regime to recommended strategy types."""
        regime_map = {
            "RICH": [
                "call_spread",  # Sell OTM calls
                "put_spread",  # Sell OTM puts
                "iron_condor",  # Sell both sides
                "collar",  # Sell calls, buy puts
                "ratio_call_spread",  # Sell more calls than buy
            ],
            "CHEAP": [
                "call_spread",  # Buy ATM/ITM calls
                "put_spread",  # Buy ATM/ITM puts
                "straddle",  # Buy both call + put ATM
                "strangle",  # Buy call + put OTM
                "reverse_strangle",  # Long gamma bet
            ],
            "FAIR": [
                "straddle",  # Gamma play
                "strangle",  # Directional gamma
                "call_spread",  # Neutral with delta management
                "put_spread",  # Neutral with delta management
            ],
        }
        return regime_map.get(self.vol_regime, ["call_spread", "put_spread"])

    def _build_strategy(
        self, strategy_type: str, edge: dict[str, Any]
    ) -> StrategyRecommendation:
        """Build a concrete strategy recommendation for a given edge."""
        edge_strike = edge["strike"]
        edge_type = edge["edge_type"]  # 'BUY' or 'SELL'

        if strategy_type == "call_spread":
            return self._build_call_spread(edge_strike, edge_type)
        elif strategy_type == "put_spread":
            return self._build_put_spread(edge_strike, edge_type)
        elif strategy_type == "iron_condor":
            return self._build_iron_condor(edge_strike, edge_type)
        elif strategy_type == "collar":
            return self._build_collar(edge_strike, edge_type)
        elif strategy_type == "straddle":
            return self._build_straddle(edge_strike, edge_type)
        elif strategy_type == "strangle":
            return self._build_strangle(edge_strike, edge_type)
        else:
            return None

    def _build_call_spread(
        self, edge_strike: float, edge_type: str
    ) -> StrategyRecommendation:
        """Build call spread strategy."""
        # If SELL edge (rich): sell call at edge, buy higher call
        # If BUY edge (cheap): buy call at edge, sell higher call

        if edge_type == "SELL":
            # Sell the rich call, buy protection higher
            short_strike = edge_strike
            long_strike = self._find_nearby_strike(
                edge_strike, direction="above", num_steps=2
            )
            short_qty = -1
            long_qty = 1
            rationale = (
                f"Sell rich {short_strike} call, buy {long_strike} call for protection"
            )
        else:
            # Buy the cheap call, sell higher
            long_strike = edge_strike
            short_strike = self._find_nearby_strike(
                edge_strike, direction="above", num_steps=2
            )
            long_qty = 1
            short_qty = -1
            rationale = f"Buy cheap {long_strike} call, sell {short_strike} call for cost reduction"

        legs = [
            StrategyLeg(
                instrument_type="call", strike=short_strike, quantity=short_qty
            ),
            StrategyLeg(instrument_type="call", strike=long_strike, quantity=long_qty),
        ]

        greeks = self._compute_net_greeks(legs)

        return StrategyRecommendation(
            strategy_type="call_spread",
            legs=legs,
            vol_regime=self.vol_regime,
            rationale=rationale,
            edge_strikes_used=[edge_strike],
            greeks_summary=greeks,
        )

    def _build_put_spread(
        self, edge_strike: float, edge_type: str
    ) -> StrategyRecommendation:
        """Build put spread strategy."""
        if edge_type == "SELL":
            # Sell rich put, buy lower put
            short_strike = edge_strike
            long_strike = self._find_nearby_strike(
                edge_strike, direction="below", num_steps=2
            )
            short_qty = -1
            long_qty = 1
            rationale = (
                f"Sell rich {short_strike} put, buy {long_strike} put for protection"
            )
        else:
            # Buy cheap put, sell lower
            long_strike = edge_strike
            short_strike = self._find_nearby_strike(
                edge_strike, direction="below", num_steps=2
            )
            long_qty = 1
            short_qty = -1
            rationale = f"Buy cheap {long_strike} put, sell {short_strike} put for cost reduction"

        legs = [
            StrategyLeg(instrument_type="put", strike=short_strike, quantity=short_qty),
            StrategyLeg(instrument_type="put", strike=long_strike, quantity=long_qty),
        ]

        greeks = self._compute_net_greeks(legs)

        return StrategyRecommendation(
            strategy_type="put_spread",
            legs=legs,
            vol_regime=self.vol_regime,
            rationale=rationale,
            edge_strikes_used=[edge_strike],
            greeks_summary=greeks,
        )

    def _build_iron_condor(
        self, edge_strike: float, edge_type: str
    ) -> StrategyRecommendation:
        """Build iron condor (sell both call and put spreads)."""
        if edge_type != "SELL":
            return None  # Iron condor is premium-selling, only use SELL edges

        # Sell call spread above edge
        call_short = edge_strike
        call_long = self._find_nearby_strike(
            edge_strike, direction="above", num_steps=2
        )

        # Sell put spread below edge
        put_short = self._find_nearby_strike(
            edge_strike, direction="below", num_steps=1
        )
        put_long = self._find_nearby_strike(edge_strike, direction="below", num_steps=2)

        legs = [
            StrategyLeg(instrument_type="call", strike=call_short, quantity=-1),
            StrategyLeg(instrument_type="call", strike=call_long, quantity=1),
            StrategyLeg(instrument_type="put", strike=put_short, quantity=-1),
            StrategyLeg(instrument_type="put", strike=put_long, quantity=1),
        ]

        greeks = self._compute_net_greeks(legs)

        return StrategyRecommendation(
            strategy_type="iron_condor",
            legs=legs,
            vol_regime=self.vol_regime,
            rationale=f"Iron condor selling premium on both sides: short {call_short} call, {put_short} put",
            edge_strikes_used=[edge_strike],
            greeks_summary=greeks,
        )

    def _build_collar(
        self, edge_strike: float, edge_type: str
    ) -> StrategyRecommendation:
        """Build collar (sell calls, buy puts)."""
        if edge_type != "SELL":
            return None

        call_strike = edge_strike
        put_strike = self._find_nearby_strike(
            edge_strike, direction="below", num_steps=1
        )

        legs = [
            StrategyLeg(instrument_type="call", strike=call_strike, quantity=-1),
            StrategyLeg(instrument_type="put", strike=put_strike, quantity=1),
        ]

        greeks = self._compute_net_greeks(legs)

        return StrategyRecommendation(
            strategy_type="collar",
            legs=legs,
            vol_regime=self.vol_regime,
            rationale=f"Collar: sell {call_strike} call, buy {put_strike} put for downside protection",
            edge_strikes_used=[edge_strike],
            greeks_summary=greeks,
        )

    def _build_straddle(
        self, edge_strike: float, edge_type: str
    ) -> StrategyRecommendation:
        """Build straddle (long call + put ATM)."""
        atm_strike = self._find_atm_strike()

        # Long straddle for cheap regime, short for rich
        qty = 1 if edge_type == "BUY" else -1

        legs = [
            StrategyLeg(instrument_type="call", strike=atm_strike, quantity=qty),
            StrategyLeg(instrument_type="put", strike=atm_strike, quantity=qty),
        ]

        greeks = self._compute_net_greeks(legs)
        direction = "long" if qty > 0 else "short"

        return StrategyRecommendation(
            strategy_type="straddle",
            legs=legs,
            vol_regime=self.vol_regime,
            rationale=f"{direction.capitalize()} straddle at {atm_strike} for gamma exposure",
            edge_strikes_used=[atm_strike],
            greeks_summary=greeks,
        )

    def _build_strangle(
        self, edge_strike: float, edge_type: str
    ) -> StrategyRecommendation:
        """Build strangle (long call + put OTM)."""
        call_strike = self._find_nearby_strike(
            self.current_price, direction="above", num_steps=1
        )
        put_strike = self._find_nearby_strike(
            self.current_price, direction="below", num_steps=1
        )

        qty = 1 if edge_type == "BUY" else -1

        legs = [
            StrategyLeg(instrument_type="call", strike=call_strike, quantity=qty),
            StrategyLeg(instrument_type="put", strike=put_strike, quantity=qty),
        ]

        greeks = self._compute_net_greeks(legs)
        direction = "long" if qty > 0 else "short"

        return StrategyRecommendation(
            strategy_type="strangle",
            legs=legs,
            vol_regime=self.vol_regime,
            rationale=f"{direction.capitalize()} strangle: {put_strike} put / {call_strike} call",
            edge_strikes_used=[edge_strike],
            greeks_summary=greeks,
        )

    def _get_strike_by_delta_target(
        self, delta_target: float, direction: str = "call"
    ) -> float:
        """
        Find the strike closest to a target delta.

        Args:
            delta_target: Target delta (e.g., 0.25, 0.5, 0.75)
            direction: 'call' (positive delta) or 'put' (negative delta)

        Returns:
            Strike closest to the target delta

        Note: Assumes call deltas are positive, put deltas are negative (standard market convention)
        """
        deltas = np.array(self.chain_data["delta"])

        if direction == "put":
            delta_target = -delta_target

        idx = np.argmin(np.abs(deltas - delta_target))
        return float(self.chain_data["strikes"][idx])

    def _filter_by_liquidity(
        self, strikes: list[float], min_oi: int = None, max_spread: float = None
    ) -> list[float]:
        """
        Filter strikes by minimum open interest and max bid-ask spread.

        Args:
            strikes: List of strikes to filter
            min_oi: Minimum open interest (default: MIN_OPEN_INTEREST=50)
            max_spread: Maximum bid-ask spread in dollars (default: MAX_BID_ASK_SPREAD=0.5)

        Returns:
            Filtered list of liquid strikes. If all filtered out, returns original list with warning.
        """
        if min_oi is None:
            min_oi = self.MIN_OPEN_INTEREST
        if max_spread is None:
            max_spread = self.MAX_BID_ASK_SPREAD

        filtered = []
        for strike in strikes:
            if strike not in self._strike_greeks:
                continue

            greeks = self._strike_greeks[strike]
            if (
                greeks["open_interest"] >= min_oi
                and greeks["bid_ask_spread"] <= max_spread
            ):
                filtered.append(strike)

        if not filtered:
            print(
                f"[Warning] No strikes passed liquidity filter (OI>={min_oi}, spread<={max_spread}); using all strikes"
            )
            return strikes

        return filtered

    def _cluster_nearby_edges(
        self, max_distance: float = 5.0
    ) -> list[list[dict[str, Any]]]:
        """
        Group nearby edge strikes into clusters for combined strategy recommendations.

        Args:
            max_distance: Maximum strike distance to cluster (in dollars, e.g., 5.0)

        Returns:
            List of edge clusters, each cluster is a list of edge dicts
        """
        if not self.edge_strikes:
            return []

        sorted_edges = sorted(self.edge_strikes, key=lambda e: e["strike"])
        clusters = []
        current_cluster = [sorted_edges[0]]

        for edge in sorted_edges[1:]:
            if abs(edge["strike"] - current_cluster[-1]["strike"]) <= max_distance:
                current_cluster.append(edge)
            else:
                clusters.append(current_cluster)
                current_cluster = [edge]

        clusters.append(current_cluster)
        return clusters

    def _find_nearby_strike(
        self, anchor: float, direction: str, num_steps: int, min_liquidity: bool = True
    ) -> float:
        """
        Find a nearby strike in the given direction, preferring liquid strikes.

        Args:
            anchor: Reference strike price
            direction: 'above' or 'below'
            num_steps: Number of strikes to move (typically 1-2)
            min_liquidity: If True, filter to liquid strikes first (default True)

        Returns:
            Strike price
        """
        strikes = sorted(self.chain_data["strikes"])

        if min_liquidity:
            liquid_strikes = self._filter_by_liquidity(strikes)
        else:
            liquid_strikes = strikes

        # Find starting index closest to anchor
        idx = len(liquid_strikes) - 1
        for i, s in enumerate(liquid_strikes):
            if s >= anchor:
                idx = i
                break

        if direction == "above":
            target_idx = min(idx + num_steps, len(liquid_strikes) - 1)
        else:  # 'below'
            target_idx = max(idx - num_steps, 0)

        return float(liquid_strikes[target_idx])

    def _find_atm_strike(self) -> float:
        """Find the ATM strike closest to current price."""
        strikes = self.chain_data["strikes"]
        return min(strikes, key=lambda s: abs(s - self.current_price))

    def _compute_net_greeks(self, legs: list[StrategyLeg]) -> dict[str, float]:
        """Sum Greeks across all legs."""
        net = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "vanna": 0.0}

        for leg in legs:
            if leg.strike not in self._strike_greeks:
                continue

            greeks = self._strike_greeks[leg.strike]
            net["delta"] += leg.quantity * greeks["delta"]
            net["gamma"] += leg.quantity * greeks["gamma"]
            net["theta"] += leg.quantity * greeks["theta"]
            net["vega"] += leg.quantity * greeks["vega"]
            net["vanna"] += leg.quantity * greeks["vanna"]

        return net

    def _rank_strategies(
        self, strategies: list[StrategyRecommendation]
    ) -> list[StrategyRecommendation]:
        """
        Score and rank strategies by Greeks alignment with vol regime.

        Scoring Philosophy:
        - RICH regime (high IV): Prefer collecting theta (short premium) while limiting risk (low gamma/vega).
          Score = 2.0 * theta - |gamma| - |vega|
          Rationale: High IV is expected to compress; selling premium harvests vol mean-reversion.
          Theta decay accelerates closer to expiration, so positive theta scores high.

        - CHEAP regime (low IV): Prefer directional vega (long premium) and gamma (convexity).
          Score = 1.5 * gamma + 1.5 * vega - |theta|
          Rationale: Low IV is expected to expand; buying premium benefits from vol expansion.
          Positive gamma profits from realized moves; positive vega profits from IV increase.

        - FAIR regime: Balanced approach, emphasize gamma exposure (straddles/strangles).
          Score = |gamma| - |theta|
          Rationale: When IV is fair, gamma strategy is more attractive than pure theta decay.

        Ranking: Higher score = better alignment with vol regime.
        Top-ranked strategies are recommended first.
        """
        for strategy in strategies:
            score = 0.0

            # RICH regime: prefer high theta (short premium), low gamma (limited risk)
            if self.vol_regime == "RICH":
                score += strategy.greeks_summary["theta"] * 2.0  # Reward theta
                score -= abs(strategy.greeks_summary["gamma"])  # Penalize gamma
                score -= abs(strategy.greeks_summary["vega"])  # Penalize vega

            # CHEAP regime: prefer high gamma, high vega (long premium)
            elif self.vol_regime == "CHEAP":
                score += strategy.greeks_summary["gamma"] * 1.5  # Reward gamma
                score += strategy.greeks_summary["vega"] * 1.5  # Reward vega
                score -= abs(strategy.greeks_summary["theta"])  # Penalize theta

            # FAIR regime: prefer balanced Greeks
            else:
                score += abs(strategy.greeks_summary["gamma"])
                score -= abs(strategy.greeks_summary["theta"])

            strategy.rank_score = score

        # Sort by rank score descending
        return sorted(strategies, key=lambda s: s.rank_score, reverse=True)

    def _strategy_to_dict(self, strategy: StrategyRecommendation) -> dict[str, Any]:
        """Convert StrategyRecommendation to JSON-serializable dict."""
        return {
            "strategy_type": strategy.strategy_type,
            "legs": [
                {
                    "instrument_type": leg.instrument_type,
                    "strike": float(leg.strike),
                    "quantity": int(leg.quantity),
                }
                for leg in strategy.legs
            ],
            "vol_regime": strategy.vol_regime,
            "rationale": strategy.rationale,
            "edge_strikes_used": [float(s) for s in strategy.edge_strikes_used],
            "greeks_summary": {
                k: float(v) if v is not None else None
                for k, v in strategy.greeks_summary.items()
            },
            "rank_score": float(strategy.rank_score),
        }


def format_strategies_artifact(
    strategies: list[dict[str, Any]],
    chain_verdict: str,
    vol_regime: str,
    current_price: float,
    expiration_date: str,
) -> dict[str, Any]:
    """
    Format strategy recommendations as a shareable artifact.

    Args:
        strategies: List of strategy dicts from StrategyRecommender.recommend()
        chain_verdict: The chain scan verdict ('EDGE DETECTED' / 'NO CLEAR EDGE')
        vol_regime: Vol regime string
        current_price: Current spot price
        expiration_date: ISO format expiration date

    Returns:
        Dict conforming to shared context pipeline format
    """
    return {
        "version": "1.0",
        "timestamp": pd.Timestamp.now("UTC").isoformat(),
        "chain_verdict": chain_verdict,
        "vol_regime": vol_regime,
        "current_price": float(current_price),
        "expiration_date": expiration_date,
        "strategies": strategies,
        "summary": {
            "total_recommendations": len(strategies),
            "by_type": {},
            "by_regime": vol_regime,
        },
    }
