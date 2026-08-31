"""Single source of truth for the repo's dark chart palette.

Every Python chart-producing module (candlestick renderer, Vol_Suite dealer
positioning, the surface explorer) imports its colors from here instead of
hand-copying hex constants. `dashboard/templates/base.html` and
`chart_app/static/index.html` are CSS/JS and can't import this module, so
their literal values are kept in sync by hand -- see the comments there.
"""

from __future__ import annotations

from matplotlib.colors import LinearSegmentedColormap

BG = "#081326"
BG_GRADIENT_END = "#0f2545"
PANEL = "#101d34"
PANEL_VOLUME = "#171a35"
TEXT = "#F4F7FF"
MUTED_TEXT = "#A9B7D0"
GRID = "#64748B"

PANEL_SHADOW = ("#050914", 0.34)
CANDLE_SHADOW = ("#050914", 0.22)

UP = "#38BDF8"
DOWN = "#F87171"

ACCENT_BLUE = "#38BDF8"
ACCENT_GREEN = "#34D399"
ACCENT_RED = "#F87171"
ACCENT_GOLD = "#FBBF24"
ACCENT_TEAL = "#2DD4BF"
ACCENT_CYAN = "#22D3EE"
ACCENT_ORANGE = "#FB923C"

BACKGROUND_GRADIENT = (BG, BG_GRADIENT_END)
GRADIENT = LinearSegmentedColormap.from_list("navy_blue", BACKGROUND_GRADIENT)

HEATMAP_CMAP = LinearSegmentedColormap.from_list(
    "heatmap_pro",
    ["#7f1d1d", ACCENT_RED, "#7a2e2e", BG, "#134a63", ACCENT_BLUE, "#a5e6fb"],
    N=256,
)

GAMMA_BAR_CMAP = LinearSegmentedColormap.from_list(
    "gamma_bar",
    [ACCENT_RED, "#7a2e2e", PANEL, "#134a63", ACCENT_BLUE],
    N=256,
)

# Dashboard dark-theme palette, matching dashboard/templates/base.html's CSS
# custom properties. Used by surface_explorer_tool for its 3D surface PNGs.
DASHBOARD_BG = "#070a14"
DASHBOARD_PANEL = "#0c1122"
DASHBOARD_TEXT = "#f8fafc"
DASHBOARD_MUTED = "#94a3b8"
DASHBOARD_ACCENT_CMAP = "cividis"
