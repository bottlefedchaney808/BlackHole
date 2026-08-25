import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import os
from datetime import datetime

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle

# Column display order + formatting precision
# Charm was previously omitted here even though MC.py's AmericanLSMPricer already
# computed and returned it (main.py's non-comparison FINAL RESULTS printout showed
# it, but the run-all comparison table/CSV/PDF silently dropped it). MCHestonLSM.py
# didn't compute charm at all until now, so every model in the comparison can
# actually populate this column.
# rho_euro (closed-form European rho) and rho_ee_premium (American rho minus
# that European rho -- the early-exercise premium on this Greek specifically)
# ride alongside the American `rho` so both conventions are visible at once;
# see _bs_rho's docstring in american_binomial.py for why they're both here.
# Neither has a clean single-word capitalize()-able name, hence _GREEK_LABELS.
_GREEK_COLS = [
    "delta",
    "gamma",
    "vega",
    "rho",
    "rho_euro",
    "rho_ee_premium",
    "theta",
    "vanna",
    "vomma",
    "color",
    "speed",
    "charm",
]
_GREEK_LABELS = {
    "rho_euro": "RhoEuro",
    "rho_ee_premium": "RhoEEPrem",
}


def _label(g):
    return _GREEK_LABELS.get(g, g.capitalize())


_DISPLAY_COLS = ["Model", "Price", "Sigma"] + [_label(g) for g in _GREEK_COLS]

_PRECISION = {
    "Price": 2,
    "Sigma": 4,
    "Delta": 4,
    "Gamma": 4,
    "Vega": 4,
    "Rho": 4,
    "RhoEuro": 4,
    "RhoEEPrem": 4,
    "Theta": 4,
    "Vanna": 4,
    # Vomma, Color, Speed and Charm run small/variable in magnitude (Color in
    # particular was previously landing around 1e-5 and rounding to a misleading
    # "0.0000" at 4 dp). 6 dp keeps them readable across the range these take.
    "Vomma": 6,
    "Color": 6,
    "Speed": 6,
    "Charm": 6,
    # Added for the full-chain report (save_full_chain_csv/save_full_chain_pdf) --
    # 'IV' is each model's own per-strike solved/observed implied vol, 'Strike'
    # is the chain strike itself (2dp is plenty for real listed strikes).
    "IV": 4,
    "Strike": 2,
}

# Model/column pairs whose second-order Greeks are known to be noise-dominated
# rather than a real model disagreement -- MC's Vomma/Color come from an LSM
# regression finite difference that remains seed-unstable (sign-flipping,
# 5-50x off magnitude) even at production path counts after the F9 bump-width
# fix (widening the bump narrowed but did not eliminate the noise -- the LSM
# continuation-value regression re-fits independently at each bumped sigma,
# so common random numbers don't cancel it). Flagged in the report itself so
# a reader doesn't mistake regression noise for a genuine cross-model
# disagreement worth investigating.
_UNRELIABLE_CELLS = {("MC", "Vomma"), ("MC", "Color")}

# Brand palette
_NAVY = "#1b2a4a"
_NAVY_LIGHT = "#2c4270"
_ACCENT = "#3d7ea6"
_GOOD = "#1e8f5f"
_WARN = "#c98a1b"
_BAD = "#c0392b"
_GRID = "#dfe3eb"
_ROW_ALT = "#f4f6fa"
_MARKET_ROW = "#fff3d6"


def _standardize_entry(name, data):
    """Convert model data into a flat dict for tabular output."""
    entry = {
        "Model": name,
        "Price": data.get("price") if data is not None else None,
    }
    greeks = data.get("greeks") or {}
    for g in _GREEK_COLS:
        entry[_label(g)] = greeks.get(g)
    entry["Sigma"] = data.get("sigma", None)
    calib = data.get("calib") or {}
    entry["Calib_kappa"] = calib.get("kappa")
    entry["Calib_theta"] = calib.get("theta")
    entry["Calib_xi"] = calib.get("xi")
    entry["Calib_rho"] = calib.get("rho")
    entry["Calib_v0"] = calib.get("v0")
    entry["Calib_rmse"] = calib.get("rmse")
    return entry


def _build_rows(models: dict, market: dict):
    rows = []
    mrow = {"Model": "Market", "Price": market.get("price")}
    for g in _GREEK_COLS:
        mrow[_label(g)] = market.get(g)
    mrow["Sigma"] = market.get("sigma")
    rows.append(mrow)
    for name, data in models.items():
        rows.append(_standardize_entry(name, data))
    return rows


def _fmt(col, val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "N/A"
    if col in _PRECISION and isinstance(val, (int, float)):
        return f"{val:,.{_PRECISION[col]}f}"
    return str(val)


def _pct_diff(val, ref):
    try:
        if val is None or ref is None or ref == 0:
            return None
        return abs(val - ref) / abs(ref)
    except Exception:
        return None


def _cell_color(col, val, market_row):
    """Traffic-light color vs the Market row for Price/greek columns."""
    if col not in _PRECISION or val is None:
        return None
    ref = market_row.get(col)
    if ref is None:
        return None
    d = _pct_diff(val, ref)
    if d is None:
        return None
    if d < 0.015:
        return _GOOD
    if d < 0.05:
        return _WARN
    return _BAD


def save_comparison_csv(
    models: dict,
    market: dict,
    meta: dict = None,
    out_dir: str = ".",
    prefix: str = "comparison",
) -> str:
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = os.path.join(out_dir, f"{prefix}_{now}.csv")
    rows = _build_rows(models, market)
    meta = meta or {}
    for row in rows:
        row.update(
            {
                "Ticker": meta.get("ticker"),
                "Strike": meta.get("K"),
                "Spot": meta.get("S"),
                "T_years": meta.get("T"),
                "Rate": meta.get("r"),
                "DivYield": meta.get("q"),
                "OptionType": meta.get("option_type"),
                "Expiry": meta.get("expiry"),
                "GeneratedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    df = pd.DataFrame(rows)
    df.to_csv(fname, index=False)
    return fname


_SMILE_PALETTE = [_ACCENT, _GOOD, _WARN, _BAD, "#7a5195", "#665191"]


def _draw_smile_page(fig, smile: dict, meta: dict = None):
    """Second report page: each model's OWN implied-vol curve plotted against
    the real market smile, so genuine model-to-model divergence is visible
    directly instead of collapsed into a single per-strike sigma number. No
    curve here is derived from another model's output -- see main.py's
    smile_data construction: every model (CRR, Leisen-Reimer, Newton-Raphson,
    SABR, Vanna-Volga, MC, Heston) now solves its OWN implied vol at every
    real chain strike (against that strike's own observed market price), not
    a single flat sigma -- a real smile, not a placeholder line.
    """
    ax = fig.add_subplot(111)

    mkt_k = smile.get("market_strikes")
    mkt_iv = smile.get("market_ivs")
    mkt_src = smile.get("market_sources") or []
    mkt_right = smile.get("market_rights") or []
    if mkt_k is not None and len(mkt_k) > 0:
        mkt_k = np.asarray(mkt_k, dtype=float)
        mkt_iv = np.asarray(mkt_iv, dtype=float)
        is_vendor = (
            np.array([s == "vendor" for s in mkt_src])
            if len(mkt_src) == len(mkt_k)
            else np.ones(len(mkt_k), dtype=bool)
        )
        # Color by put/call side, not just vendor-vs-solved marker shape --
        # this makes the OTM-put/OTM-call crossover (right at/near the
        # forward, where the smile convention switches sides) visually
        # obvious, so a jump or kink exactly at that point is identifiable
        # as a put/call quoting-convention artifact rather than a mystery.
        is_put = (
            np.array([rt == "P" for rt in mkt_right])
            if len(mkt_right) == len(mkt_k)
            else (mkt_k <= ((meta or {}).get("S") or np.median(mkt_k)))
        )
        put_color, call_color = _NAVY, _WARN
        for side_mask, color, side_label in (
            (is_put, put_color, "Put"),
            (~is_put, call_color, "Call"),
        ):
            v_mask = side_mask & is_vendor
            s_mask = side_mask & ~is_vendor
            if v_mask.any():
                ax.scatter(
                    mkt_k[v_mask],
                    mkt_iv[v_mask],
                    color=color,
                    marker="o",
                    s=50,
                    zorder=5,
                    label=f"Market IV (vendor, {side_label})",
                )
            if s_mask.any():
                ax.scatter(
                    mkt_k[s_mask],
                    mkt_iv[s_mask],
                    edgecolors=color,
                    facecolors="none",
                    marker="^",
                    s=55,
                    zorder=5,
                    linewidths=1.4,
                    label=f"Market IV (self-solved, {side_label})",
                )

    curves = smile.get("curves") or {}
    _CURVE_STYLES = ["-", "--", "-.", ":"]
    for i, (name, (ks, ivs)) in enumerate(curves.items()):
        ks = np.asarray(ks, dtype=float)
        ivs = np.asarray(ivs, dtype=float)
        order = np.argsort(ks)
        # Thinner + a distinct linestyle per curve + some transparency --
        # a wide-swinging curve (e.g. SABR sweeping several vol points near
        # a wing) used to render at the same bold 2.0 solid width as every
        # other curve, so its sheer vertical excursion visually buried
        # flatter/closer-together curves sitting right on top of it.
        ax.plot(
            ks[order],
            ivs[order],
            label=name,
            color=_SMILE_PALETTE[i % len(_SMILE_PALETTE)],
            linewidth=1.6,
            linestyle=_CURVE_STYLES[i % len(_CURVE_STYLES)],
            alpha=0.9,
            zorder=4,
        )

    flats = smile.get("flats") or {}
    flat_colors = ["#9aa5b8", "#c2a8d9", "#e0b98a", "#8ac4c2"]
    for i, (name, sigma) in enumerate(flats.items()):
        if sigma is None:
            continue
        ax.axhline(
            sigma,
            linestyle="--",
            linewidth=1.3,
            alpha=0.85,
            color=flat_colors[i % len(flat_colors)],
            label=f"{name} (flat, no skew)",
            zorder=3,
        )

    if meta and meta.get("K") is not None:
        ax.axvline(meta["K"], color="#888", linestyle=":", linewidth=1.0, zorder=1)

    # Frame the view around the strike that actually matters, instead of
    # letting matplotlib auto-scale to whatever the fetched chain happens to
    # span. The chain is deliberately unfiltered/uncapped (see
    # smile_utils.py) so calibration sees the whole real market -- but that
    # means far-wing strikes (thin, often minimum-tick-priced contracts whose
    # BS-inverted "IV" is an artifact of the inversion being poorly
    # conditioned there, not a real vol read) can span 10-20x the ATM strike
    # and 5-10x the ATM vol. Plotting that full range makes the one region
    # anyone is actually pricing -- near spot/the priced K -- collapse into
    # an unreadable sliver. This does not drop or alter any data (the full
    # chain is still what SABR/Heston calibrate against); it only chooses
    # what the default view shows, and says explicitly how much was cropped.
    S_ref = (meta or {}).get("S") or (meta or {}).get("K")
    n_hidden_x = 0
    if S_ref:
        k_target = (meta or {}).get("K") or S_ref
        lo_x = min(0.5 * S_ref, 0.8 * k_target)
        hi_x = max(1.6 * S_ref, 1.25 * k_target)
        if mkt_k is not None and len(mkt_k) > 0:
            n_hidden_x = int(np.sum((mkt_k < lo_x) | (mkt_k > hi_x)))
        ax.set_xlim(lo_x, hi_x)

        # y-range: percentile-based, computed only from points that actually
        # fall inside the displayed x-window (market IVs, curves, and flats),
        # so a handful of extreme-wing prints (or a SABR/Hagan-formula
        # divergence at very low strikes -- a known artifact of the Hagan
        # expansion as K -> 0 under beta < 1) don't blow out the scale for
        # everything else.
        y_vals = []
        if mkt_k is not None and len(mkt_k) > 0:
            in_window = (mkt_k >= lo_x) & (mkt_k <= hi_x) & np.isfinite(mkt_iv)
            y_vals.extend(mkt_iv[in_window].tolist())
        for ks, ivs in curves.values():
            ks_arr = np.asarray(ks, dtype=float)
            ivs_arr = np.asarray(ivs, dtype=float)
            mask = (ks_arr >= lo_x) & (ks_arr <= hi_x) & np.isfinite(ivs_arr)
            y_vals.extend(ivs_arr[mask].tolist())
        for sigma in flats.values():
            if sigma is not None:
                y_vals.append(sigma)
        if y_vals:
            y_lo, y_hi = np.percentile(y_vals, [2, 98])
            pad = max((y_hi - y_lo) * 0.15, 0.02)
            ax.set_ylim(max(0.0, y_lo - pad), y_hi + pad)

    if meta and meta.get("K") is not None:
        ax.text(
            meta["K"],
            ax.get_ylim()[1],
            " priced K",
            fontsize=8,
            color="#666",
            va="top",
            ha="left",
        )

    if n_hidden_x:
        fig.text(
            0.98,
            0.008,
            f"{n_hidden_x} market strike(s) outside plotted range (extreme far-wing, not dropped from calibration)",
            fontsize=7.5,
            color="#999",
            style="italic",
            ha="right",
        )

    ax.set_xlabel("Strike", fontsize=10, color="#444")
    ax.set_ylabel("Implied Vol", fontsize=10, color="#444")
    ax.set_title(
        "Smile Comparison -- Market vs. Each Model (independently solved)",
        fontsize=12,
        color=_NAVY,
        fontweight="bold",
        loc="left",
    )
    # Persistent reference to the ACTUAL calibration strike range -- not just
    # what's visible in the (deliberately zoomed-in) plot window above. SABR
    # and Heston both calibrate against this full range (same
    # fetch_market_smile call, same moneyness/spread filters -- see
    # smile_utils.py), so this answers "what strikes did the calibration
    # actually use" directly on the report, without scrolling back through
    # console debug output to find it. A separate annotation (not a second
    # ax.set_title -- that would silently overwrite the title above instead
    # of appending anything, since both would share loc='left') positioned
    # just under the main title.
    if mkt_k is not None and len(mkt_k) > 0:
        ax.text(
            0.0,
            1.02,
            f"Calibration strike range: {mkt_k.min():.1f} - {mkt_k.max():.1f} ({len(mkt_k)} strikes total -- "
            f"same range for SABR & Heston)",
            transform=ax.transAxes,
            fontsize=8.5,
            color="#666",
            ha="left",
            va="bottom",
        )
    ax.grid(color=_GRID, linewidth=0.8, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=8, loc="best", ncol=2, framealpha=0.9)
    fig.text(
        0.02,
        0.008,
        "Generated automatically — verify inputs before use in trading decisions.",
        fontsize=7.5,
        color="#999",
        style="italic",
    )


def save_comparison_pdf(
    models: dict,
    market: dict,
    meta: dict = None,
    out_dir: str = ".",
    prefix: str = "comparison",
    smile: dict = None,
) -> str:
    meta = meta or {}
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = os.path.join(out_dir, f"{prefix}_{now}.pdf")

    rows = _build_rows(models, market)
    df = pd.DataFrame(rows)
    for c in _DISPLAY_COLS:
        if c not in df.columns:
            df[c] = None
    df = df[_DISPLAY_COLS]
    market_row = (
        df[df["Model"] == "Market"].iloc[0].to_dict()
        if (df["Model"] == "Market").any()
        else {}
    )

    n_models = len(df)
    n_cols = len(_DISPLAY_COLS)
    # Widen the table in proportion to column count so per-column pixel width
    # doesn't shrink every time a column is added -- 13in/12 non-Model columns
    # was the known-good baseline before RhoEuro/RhoEEPrem; keeping that same
    # per-column width for whatever n_cols is now (rather than a fixed 13in)
    # is what stops long new headers from overlapping their neighbors.
    fig_width = max(13.0, 13.0 * (n_cols - 1) / 12.0)
    fig = plt.figure(
        figsize=(fig_width, 4.6 + 0.42 * n_models + 3.2), facecolor="white"
    )
    gs = fig.add_gridspec(
        3, 1, height_ratios=[1.1, 0.42 * n_models + 1.4, 2.6], hspace=0.35
    )

    # ---- Header ----
    # Three distinct rows, each with its own y-coordinate, so long ticker/strike/expiry
    # strings can never collide with the title (previously the title and the trade-info
    # subtitle shared the same row, and a long subtitle would run into the title text).
    ax_head = fig.add_subplot(gs[0])
    ax_head.axis("off")
    ax_head.add_patch(
        Rectangle(
            (0, 0),
            1,
            1,
            transform=ax_head.transAxes,
            facecolor=_NAVY,
            edgecolor="none",
            zorder=0,
        )
    )

    ax_head.text(
        0.02,
        0.80,
        "Model Comparison Report",
        transform=ax_head.transAxes,
        fontsize=19,
        fontweight="bold",
        color="white",
        va="center",
    )

    ticker = meta.get("ticker", "N/A")
    K = meta.get("K")
    S = meta.get("S")
    T = meta.get("T")
    r = meta.get("r")
    q = meta.get("q")
    opt = str(meta.get("option_type", "")).capitalize()
    expiry = meta.get("expiry") or "N/A"
    subtitle = (
        f"{ticker}   |   {opt}   |   Strike ${K:,.2f}" if K is not None else f"{ticker}"
    )
    if S is not None:
        subtitle += f"   |   Spot ${S:,.2f}"
    if T is not None:
        subtitle += f"   |   T={T:.3f}y"
    subtitle += f"   |   Expiry {expiry}"
    ax_head.text(
        0.02,
        0.48,
        subtitle,
        transform=ax_head.transAxes,
        fontsize=11,
        color="white",
        va="center",
        ha="left",
        fontweight="bold",
    )

    ax_head.text(
        0.02,
        0.15,
        "Monte Carlo American Pricer & Greeks",
        transform=ax_head.transAxes,
        fontsize=10,
        color="#c8d3e8",
        va="center",
    )
    params_line = f"r={r:.4f}" if r is not None else "r=N/A"
    params_line += f"   q={q:.4f}" if q is not None else "   q=N/A"
    params_line += f"   Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    ax_head.text(
        0.98,
        0.15,
        params_line,
        transform=ax_head.transAxes,
        fontsize=9,
        color="#c8d3e8",
        va="center",
        ha="right",
    )

    # ---- Table ----
    ax_tbl = fig.add_subplot(gs[1])
    ax_tbl.axis("off")
    cell_text = [
        [
            _fmt(c, row[c]) + (" †" if (row["Model"], c) in _UNRELIABLE_CELLS else "")
            for c in _DISPLAY_COLS
        ]
        for row in df.to_dict("records")
    ]
    # Give the Model column extra width -- long names like "Newton-Raphson" were
    # getting squeezed/overlapping the Price column at equal column widths.
    # The remaining columns are weighted by header label length (not split
    # equally) -- an equal split was tuned for 3-6 char headers (Rho, Vega,
    # Vanna) and let longer ones (RhoEuro, RhoEEPrem) overlap their neighbors.
    model_frac = 0.115
    rest_labels = _DISPLAY_COLS[1:]
    label_weights = [max(len(c), 4) for c in rest_labels]
    total_weight = sum(label_weights)
    col_widths = [model_frac] + [
        (1 - model_frac) * (w / total_weight) for w in label_weights
    ]
    tbl = ax_tbl.table(
        cellText=cell_text,
        colLabels=_DISPLAY_COLS,
        cellLoc="center",
        loc="center",
        colWidths=col_widths,
    )
    tbl.auto_set_font_size(False)
    fontsize = 9.5 if n_models < 10 else max(6.5, 11 - 0.2 * n_models)
    tbl.set_fontsize(fontsize)
    tbl.scale(1.0, 1.65)

    for (row_i, col_i), cell in tbl.get_celld().items():
        cell.set_edgecolor(_GRID)
        col_name = _DISPLAY_COLS[col_i]
        if row_i == 0:
            cell.set_text_props(weight="bold", color="white", fontsize=fontsize + 0.5)
            cell.set_facecolor(_NAVY_LIGHT)
            continue
        model_name = cell_text[row_i - 1][0]
        is_market = model_name == "Market"
        base_face = (
            _MARKET_ROW if is_market else (_ROW_ALT if row_i % 2 == 0 else "#ffffff")
        )
        cell.set_facecolor(base_face)
        if col_i == 0:
            cell.set_text_props(weight="bold", color=_NAVY)
        elif not is_market:
            raw_val = df.iloc[row_i - 1][col_name]
            color = _cell_color(col_name, raw_val, market_row)
            if color:
                cell.set_text_props(color=color, weight="bold")

    # legend for the traffic-light coloring
    ax_tbl.text(
        0.0,
        -0.03,
        "vs Market:",
        transform=ax_tbl.transAxes,
        fontsize=8.5,
        color="#555",
        fontweight="bold",
    )
    ax_tbl.text(
        0.09,
        -0.03,
        "● within 1.5%",
        transform=ax_tbl.transAxes,
        fontsize=8.5,
        color=_GOOD,
    )
    ax_tbl.text(
        0.23,
        -0.03,
        "● within 5%",
        transform=ax_tbl.transAxes,
        fontsize=8.5,
        color=_WARN,
    )
    ax_tbl.text(
        0.35, -0.03, "● beyond 5%", transform=ax_tbl.transAxes, fontsize=8.5, color=_BAD
    )
    _unreliable_models = {mc for mc, _ in _UNRELIABLE_CELLS}
    if any(model_name in _unreliable_models for model_name in df["Model"]):
        ax_tbl.text(
            0.55,
            -0.03,
            "† noise-dominated (LSM regression), not decision-quality",
            transform=ax_tbl.transAxes,
            fontsize=8,
            color=_WARN,
            style="italic",
        )

    # ---- Price comparison chart ----
    ax_chart = fig.add_subplot(gs[2])
    plot_df = df[df["Price"].notna()].copy()
    names = plot_df["Model"].tolist()
    prices = plot_df["Price"].astype(float).tolist()
    colors = [_NAVY if n == "Market" else _ACCENT for n in names]
    bars = ax_chart.bar(
        names, prices, color=colors, edgecolor="white", width=0.55, zorder=3
    )
    # Extra headroom above the tallest bar/line so value labels and the market-line
    # annotation never collide with each other regardless of how close a model's price
    # lands to the market price.
    _price_pool = prices + (
        [market_row["Price"]] if market_row.get("Price") is not None else []
    )
    y_top = max(_price_pool) * 1.18 if _price_pool and max(_price_pool) > 0 else 1.0
    ax_chart.set_ylim(0, y_top)
    for b, p in zip(bars, prices):
        ax_chart.text(
            b.get_x() + b.get_width() / 2,
            p + y_top * 0.012,
            f"${p:,.2f}",
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="bold",
            color=_NAVY,
        )
    if market_row.get("Price") is not None:
        ax_chart.axhline(
            market_row["Price"], color=_BAD, linestyle="--", linewidth=1.2, zorder=2
        )
        # Fixed axes-fraction position (top-left) instead of a legend/inline label --
        # a legend or line-anchored label can land right on top of a bar's own value
        # label whenever that model's price happens to be close to the market price.
        ax_chart.text(
            0.02,
            0.97,
            f"- - -  Market: ${market_row['Price']:,.2f}",
            transform=ax_chart.transAxes,
            fontsize=8.5,
            color=_BAD,
            va="top",
            ha="left",
            fontweight="bold",
        )
    ax_chart.set_ylabel("Price ($)", fontsize=9.5, color="#444")
    ax_chart.set_title(
        "Price by Model", fontsize=11, color=_NAVY, fontweight="bold", loc="left"
    )
    ax_chart.grid(axis="y", color=_GRID, linewidth=0.8, zorder=0)
    ax_chart.spines[["top", "right", "left"]].set_visible(False)
    ax_chart.tick_params(axis="x", labelrotation=15, labelsize=9)
    ax_chart.tick_params(axis="y", labelsize=9)

    fig.text(
        0.02,
        0.008,
        "Generated automatically — verify inputs before use in trading decisions.",
        fontsize=7.5,
        color="#999",
        style="italic",
    )

    has_smile = bool(
        smile
        and (
            smile.get("curves")
            or (
                smile.get("market_strikes") is not None
                and len(smile.get("market_strikes")) > 0
            )
        )
    )
    if has_smile:
        with PdfPages(fname) as pdf:
            pdf.savefig(fig, bbox_inches="tight", facecolor="white", dpi=150)
            plt.close(fig)
            fig2 = plt.figure(figsize=(13, 7.5), facecolor="white")
            _draw_smile_page(fig2, smile, meta=meta)
            pdf.savefig(fig2, bbox_inches="tight", facecolor="white", dpi=150)
            plt.close(fig2)
    else:
        fig.savefig(fname, bbox_inches="tight", facecolor="white", dpi=150)
        plt.close(fig)
    return fname


# ---------------------------------------------------------------------------
# Full-chain evaluation report (NOTES_chain_evaluation.md "full chain" spec).
#
# Genuinely different shape than the single-K comparison above: a table per
# STRIKE (every model, every real chain strike), not one row per model at a
# single K. Reuses _draw_smile_page directly for the smile page (no
# reimplementation -- chain_evaluation.run_full_chain's 'smile' key is shaped
# exactly like build_smile_comparison's return value).
# ---------------------------------------------------------------------------

# 1st-order-only subset shown in the PDF summary table -- the PDF's summary
# page is a moneyness-windowed table (see save_full_chain_pdf's docstring for
# why full 2nd/3rd-order Greeks at every strike, for every model, would blow
# out both column count and page count); the CSV export carries every Greek
# in _GREEK_COLS at every strike a model solved, so nothing is lost, only
# de-emphasized on the printed summary.
_CHAIN_SUMMARY_GREEK_COLS = ["delta", "gamma", "vega", "theta"]


def _isclose_strike(a, b, tol=1e-6):
    try:
        return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))
    except Exception:
        return False


def _full_chain_rows(chain_result: dict, focus_k, greek_cols):
    """Flatten chain_evaluation.run_full_chain's per-strike/per-model dict
    into one row per (strike, model) pair -- Market plus every model that
    actually solved at that strike (a model's failed strikes are simply
    absent from its sub-dict, per the "no fake numbers on failure" house
    convention -- this omits the row entirely rather than zero-filling it).

    MC and Heston's 'greeks' sub-dict only carries 1st-order Greeks at every
    strike (2nd/3rd order is prohibitively expensive per-strike for these
    two -- see NOTES_chain_evaluation.md's scope decision); at the single
    strike matching `focus_k`, this merges in chain_result['focus_k_greeks']
    so that one row (and only that one) shows the full Greek set for MC/Heston,
    identical to what the single-K "Run all models & compare" report shows.
    """
    _strikes_raw = chain_result.get("strikes")
    strikes = list(_strikes_raw) if _strikes_raw is not None else []
    market = chain_result.get("market") or {}
    per_model = chain_result.get("per_model") or {}
    focus_greeks = chain_result.get("focus_k_greeks") or {}

    all_strikes = sorted(set(strikes) | set(market.keys()))
    rows = []
    for k in all_strikes:
        mkt = market.get(k)
        if mkt is not None:
            row = {
                "Strike": k,
                "Model": "Market",
                "Right": mkt.get("right"),
                "Price": mkt.get("price"),
                "IV": mkt.get("iv"),
            }
            for g in greek_cols:
                row[_label(g)] = None
            rows.append(row)
        for model_name, model_data in per_model.items():
            entry = (model_data or {}).get(k)
            if entry is None:
                continue
            greeks = dict(entry.get("greeks") or {})
            if (
                focus_k is not None
                and model_name in focus_greeks
                and _isclose_strike(k, focus_k)
            ):
                greeks.update(focus_greeks[model_name] or {})
            # 'Right' matters here in a way it doesn't on the single-K report:
            # a chain spans strikes on BOTH sides of the forward, and every
            # model here prices whichever side is OTM for that strike (the
            # same convention build_smile_comparison/fetch_market_smile use
            # throughout this project -- put side for K<=forward, call side
            # for K>=forward). A reader scanning down the Strike column will
            # see Delta change sign partway through even though `meta`'s
            # single global OptionType never changes -- that's this, not a
            # bug, and Right disambiguates it row-by-row.
            row = {
                "Strike": k,
                "Model": model_name,
                "Right": entry.get("right"),
                "Price": entry.get("price"),
                "IV": entry.get("iv"),
            }
            for g in greek_cols:
                row[_label(g)] = greeks.get(g)
            rows.append(row)
    return rows


def save_full_chain_csv(
    chain_result: dict,
    meta: dict = None,
    out_dir: str = ".",
    prefix: str = "full_chain",
) -> str:
    """One row per (strike, model) pair -- price/IV/every Greek in
    _GREEK_COLS (blank/NaN where a model didn't compute or solve that Greek
    at that strike, e.g. MC/Heston's 2nd/3rd-order away from the focus K).
    Same out_dir/prefix/timestamped-filename convention as
    save_comparison_csv."""
    meta = meta or {}
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = os.path.join(out_dir, f"{prefix}_{now}.csv")
    focus_k = meta.get("K")
    rows = _full_chain_rows(chain_result, focus_k, _GREEK_COLS)
    for row in rows:
        row.update(
            {
                "Ticker": meta.get("ticker"),
                "FocusK": focus_k,
                "Spot": meta.get("S"),
                "T_years": meta.get("T"),
                "Rate": meta.get("r"),
                "DivYield": meta.get("q"),
                "OptionType": meta.get("option_type"),
                "Expiry": meta.get("expiry"),
                "GeneratedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    cols = (
        ["Strike", "Model", "Right", "Price", "IV"]
        + [_label(g) for g in _GREEK_COLS]
        + [
            "Ticker",
            "FocusK",
            "Spot",
            "T_years",
            "Rate",
            "DivYield",
            "OptionType",
            "Expiry",
            "GeneratedAt",
        ]
    )
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols]
    df.to_csv(fname, index=False)
    return fname


def _draw_chain_header(
    fig, ax_head, meta: dict, page_label: str = None, note: str = None
):
    """Same branded navy header as save_comparison_pdf's ax_head block
    (title/subtitle/params layout, _NAVY background) -- factored out here
    since the full-chain summary table is paginated (one header per page),
    which save_comparison_pdf's single-page layout never needed."""
    ax_head.axis("off")
    ax_head.add_patch(
        Rectangle(
            (0, 0),
            1,
            1,
            transform=ax_head.transAxes,
            facecolor=_NAVY,
            edgecolor="none",
            zorder=0,
        )
    )

    title = "Full Chain Evaluation Report"
    if page_label:
        title += f"  ({page_label})"
    ax_head.text(
        0.02,
        0.85,
        title,
        transform=ax_head.transAxes,
        fontsize=17,
        fontweight="bold",
        color="white",
        va="center",
    )

    ticker = meta.get("ticker", "N/A")
    K = meta.get("K")
    S = meta.get("S")
    T = meta.get("T")
    r = meta.get("r")
    q = meta.get("q")
    opt = str(meta.get("option_type", "")).capitalize()
    expiry = meta.get("expiry") or "N/A"
    subtitle = (
        f"{ticker}   |   {opt}   |   Focus Strike ${K:,.2f}"
        if K is not None
        else f"{ticker}"
    )
    if S is not None:
        subtitle += f"   |   Spot ${S:,.2f}"
    if T is not None:
        subtitle += f"   |   T={T:.3f}y"
    subtitle += f"   |   Expiry {expiry}"
    ax_head.text(
        0.02,
        0.60,
        subtitle,
        transform=ax_head.transAxes,
        fontsize=10.5,
        color="white",
        va="center",
        ha="left",
        fontweight="bold",
    )

    # note (page 1's is often long -- the strike-window/CSV-export caveat) and
    # params_line each get their OWN row now, not a shared one -- they used to
    # both sit at y=0.15 (note left-aligned, params right-aligned) and a long
    # note would visually run into the right-aligned params text. Same class
    # of collision save_comparison_pdf's header already had fixed once before
    # (see PROJECT_ROADMAP.md's "three distinct rows" note) -- same fix here.
    ax_head.text(
        0.02,
        0.32,
        note or "Every model, every chain strike -- price + IV + Greeks",
        transform=ax_head.transAxes,
        fontsize=9.0,
        color="#c8d3e8",
        va="center",
    )
    params_line = f"r={r:.4f}" if r is not None else "r=N/A"
    params_line += f"   q={q:.4f}" if q is not None else "   q=N/A"
    params_line += f"   Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    ax_head.text(
        0.98,
        0.08,
        params_line,
        transform=ax_head.transAxes,
        fontsize=8.5,
        color="#c8d3e8",
        va="center",
        ha="right",
    )


def save_full_chain_pdf(
    chain_result: dict,
    meta: dict = None,
    out_dir: str = ".",
    prefix: str = "full_chain",
    window: int = 5,
    rows_per_page: int = 26,
) -> str:
    """Full-chain PDF: a paginated summary table windowed around the focus K,
    followed by the smile-comparison page (via the existing _draw_smile_page,
    reused unmodified).

    Table-scoping decision: a full table of every real chain strike (often
    100-150+) times every model (up to 9 with Market) would be many hundreds
    of rows -- unreadable as a single page and not very useful printed in
    full (that's what the CSV export is for). Instead this shows a
    moneyness window of `window` strikes on each side of the focus K
    (2*window+1 strikes total) with 1st-order Greeks only (Delta/Gamma/Vega/
    Theta -- the ones every model computes at every strike; 2nd/3rd-order
    for MC/Heston only exist at the single focus K anyway, per the scope
    decision in NOTES_chain_evaluation.md, and are in the CSV/single-K
    report), paginated at `rows_per_page` rows/page so it stays legible
    regardless of how many models solved at a given strike.
    """
    meta = meta or {}
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = os.path.join(out_dir, f"{prefix}_{now}.pdf")

    focus_k = meta.get("K")
    _strikes_raw = chain_result.get("strikes")
    strikes = sorted(_strikes_raw) if _strikes_raw is not None else []
    if focus_k is not None and strikes:
        idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - focus_k))
        lo = max(0, idx - window)
        hi = min(len(strikes), idx + window + 1)
        window_strikes = set(strikes[lo:hi])
    else:
        window_strikes = set(strikes)

    rows = _full_chain_rows(chain_result, focus_k, _CHAIN_SUMMARY_GREEK_COLS)
    rows = [
        row
        for row in rows
        if any(_isclose_strike(row["Strike"], ws) for ws in window_strikes)
    ]
    rows.sort(
        key=lambda row: (
            row["Strike"],
            0 if row["Model"] == "Market" else 1,
            row["Model"],
        )
    )

    # 'Right' is shown here (unlike save_comparison_pdf's single-K table,
    # which has one fixed call/put for the whole report) because a chain
    # window can straddle the forward -- Delta legitimately flips sign
    # between an OTM put row and an OTM call row a few strikes apart, and
    # without this column that looks like a bug at a glance.
    display_cols = ["Strike", "Model", "Right", "Price", "IV"] + [
        _label(g) for g in _CHAIN_SUMMARY_GREEK_COLS
    ]

    # Market row per strike, for traffic-light coloring of each model's row
    # against ITS OWN strike's market reference (not a single global market
    # row like save_comparison_pdf, since here every strike has its own).
    market_by_strike = {row["Strike"]: row for row in rows if row["Model"] == "Market"}

    n_rows = len(rows)
    n_pages = max(1, -(-n_rows // rows_per_page)) if n_rows else 1
    note = (
        f"Showing {len(window_strikes)} of {len(strikes)} chain strikes "
        f"(+/-{window} around focus K={focus_k:,.2f}); full per-strike/per-model "
        f"data incl. 2nd/3rd-order Greeks is in the CSV export."
        if focus_k is not None
        else "Full-chain summary (windowed) -- see CSV export for complete data."
    )

    with PdfPages(fname) as pdf:
        for page in range(n_pages):
            chunk = (
                rows[page * rows_per_page : (page + 1) * rows_per_page]
                if n_rows
                else []
            )
            n_chunk = max(len(chunk), 1)
            fig = plt.figure(
                figsize=(11.5, 2.6 + 0.42 * n_chunk + 1.0), facecolor="white"
            )
            gs = fig.add_gridspec(
                2, 1, height_ratios=[1.0, 0.42 * n_chunk + 1.0], hspace=0.25
            )

            ax_head = fig.add_subplot(gs[0])
            page_label = f"page {page + 1}/{n_pages}" if n_pages > 1 else None
            _draw_chain_header(
                fig,
                ax_head,
                meta,
                page_label=page_label,
                note=note if page == 0 else None,
            )

            ax_tbl = fig.add_subplot(gs[1])
            ax_tbl.axis("off")
            cell_text = [
                [_fmt(c, row.get(c)) for c in display_cols] for row in chunk
            ] or [["" for _ in display_cols]]
            tbl = ax_tbl.table(
                cellText=cell_text,
                colLabels=display_cols,
                cellLoc="center",
                loc="center",
            )
            tbl.auto_set_font_size(False)
            fontsize = 9.0 if n_chunk < 20 else 7.5
            tbl.set_fontsize(fontsize)
            tbl.scale(1.0, 1.6)

            for (row_i, col_i), cell in tbl.get_celld().items():
                cell.set_edgecolor(_GRID)
                col_name = display_cols[col_i]
                if row_i == 0:
                    cell.set_text_props(
                        weight="bold", color="white", fontsize=fontsize + 0.5
                    )
                    cell.set_facecolor(_NAVY_LIGHT)
                    continue
                if row_i - 1 >= len(chunk):
                    continue
                data_row = chunk[row_i - 1]
                is_market = data_row["Model"] == "Market"
                base_face = (
                    _MARKET_ROW
                    if is_market
                    else (_ROW_ALT if row_i % 2 == 0 else "#ffffff")
                )
                cell.set_facecolor(base_face)
                if col_i in (0, 1):
                    cell.set_text_props(weight="bold", color=_NAVY)
                elif not is_market:
                    ref_row = market_by_strike.get(data_row["Strike"])
                    if ref_row:
                        color = _cell_color(col_name, data_row.get(col_name), ref_row)
                        if color:
                            cell.set_text_props(color=color, weight="bold")

            fig.text(
                0.02,
                0.008,
                "Generated automatically — verify inputs before use in trading decisions.",
                fontsize=7.5,
                color="#999",
                style="italic",
            )
            pdf.savefig(fig, bbox_inches="tight", facecolor="white", dpi=150)
            plt.close(fig)

        smile = chain_result.get("smile")
        has_smile = bool(
            smile
            and (
                smile.get("curves")
                or (
                    smile.get("market_strikes") is not None
                    and len(smile.get("market_strikes")) > 0
                )
            )
        )
        if has_smile:
            fig2 = plt.figure(figsize=(13, 7.5), facecolor="white")
            _draw_smile_page(fig2, smile, meta=meta)
            pdf.savefig(fig2, bbox_inches="tight", facecolor="white", dpi=150)
            plt.close(fig2)

    return fname
