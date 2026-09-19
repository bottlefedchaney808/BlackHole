/* Palette and scale constants. Kept in sync by hand with shared/chart_theme.py
   and static/css/app.css (no purple/violet anywhere). */

window.CT = (function () {
  const C = {
    bg: "#0e1117",
    text: "#f4f7ff",
    muted: "#a9b7d0",
    dim: "#64748b",
    grid: "#1b2433",
    axis: "#253044",
    up: "#38bdf8",
    down: "#f87171",
    long: "#34d399",
    short: "#fb7185",
    warn: "#f59e0b",
    ema20: "#fbbf24",
    ema50: "#60a5fa",
    ema200: "#94a3b8",
    vwap: "#34d399",
    band: "#475569",
    atrband: "#f472b6",
    pd: "#fbbf24",
    alma: "#22d3ee",
    entropy: "#f59e0b",
    liquidity: "#38bdf8",
    conviction: "#e2e8f0",
  };

  // How many bars to show on first paint, per interval. The old map was a
  // flat 80 for every intraday timeframe, which is a different amount of TIME
  // on each one: 80 bars is 4 hours on 3m and two and a half weeks on 4h. A
  // trader reads a chart in sessions, not bars, so these are sized in
  // sessions and converted. (Crypto is 24/7 and gets more bars per "session";
  // it simply shows a longer window, which is the right default there.)
  const BARS_PER_SESSION = {
    "3m": 130, "5m": 78, "10m": 39, "15m": 26,
    "30m": 13, "1h": 7, "4h": 2, "1d": 1,
  };
  const SESSIONS_VISIBLE = {
    "3m": 1, "5m": 2, "10m": 3, "15m": 5,
    "30m": 10, "1h": 15, "4h": 60, "1d": 180,
  };

  function visibleBars(interval) {
    const per = BARS_PER_SESSION[interval] || 26;
    const sessions = SESSIONS_VISIBLE[interval] || 5;
    return Math.max(40, Math.round(per * sessions));
  }

  // Mirrors _LOOKBACK in chart_app/server.py -- these two MUST stay in
  // lockstep. The old 30-day intraday ceiling is gone (2026-09-19): it was
  // never the provider, it was httpx's default 5.0s timeout inside PHClient.
  // 1h/4h now ask for enough history to actually seed EMA200 rather than the
  // most the fetch used to survive.
  const LOOKBACK = {
    "3m": "5d", "5m": "5d", "10m": "10d", "15m": "20d",
    "30m": "30d", "1h": "60d", "4h": "180d", "1d": "1y",
  };

  /* Price axes need different precision at $0.40 and at $81,000. A fixed 2dp
     renders bitcoin as "81046.70" (nine glyphs of mostly noise) and a $0.42
     token as "0.42" with every level collapsing onto the same label. */
  function priceFormatter(sample) {
    const v = Math.abs(sample || 0);
    const dp = v >= 1000 ? 0 : v >= 100 ? 1 : v >= 1 ? 2 : v >= 0.01 ? 4 : 6;
    return function (x) {
      if (x == null || isNaN(x)) return "";
      return v >= 1000
        ? Math.round(x).toLocaleString("en-US")
        : x.toFixed(dp);
    };
  }

  function compact(n) {
    if (n == null || isNaN(n)) return "-";
    const a = Math.abs(n);
    const sign = n < 0 ? "-" : "";
    if (a >= 1e9) return sign + (a / 1e9).toFixed(1) + "B";
    if (a >= 1e6) return sign + (a / 1e6).toFixed(1) + "M";
    if (a >= 1e3) return sign + (a / 1e3).toFixed(1) + "K";
    return sign + a.toFixed(0);
  }

  return { C, visibleBars, LOOKBACK, priceFormatter, compact, BARS_PER_SESSION };
})();
