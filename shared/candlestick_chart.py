"""Deterministic dark-theme renderer for normalized daily candle payloads."""

from __future__ import annotations

import math
import re
from datetime import datetime
from itertools import pairwise
from os import PathLike
from pathlib import Path
from statistics import median

import matplotlib

# Select a non-interactive backend before importing pyplot for CLI/headless use.
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import patheffects
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter

from shared.chart_data import CandlePayload, CandleRecord, ChartDataError


_BACKGROUND_GRADIENT = ("#081326", "#21113d")
_BACKGROUND = _BACKGROUND_GRADIENT[0]
_PANEL = "#101d34"
_PANEL_VOLUME = "#171a35"
_TEXT = "#F4F7FF"
_GRID = "#64748B"
_UP = "#38BDF8"
_DOWN = "#C084FC"
_MUTED_TEXT = "#A9B7D0"

_PANEL_SHADOW = ("#050914", 0.34)
_CANDLE_SHADOW = ("#050914", 0.22)
_GRADIENT = LinearSegmentedColormap.from_list("navy_purple", _BACKGROUND_GRADIENT)


def _validate_payload(payload: CandlePayload) -> tuple[CandleRecord, ...]:
    if not isinstance(payload, CandlePayload):
        raise ChartDataError("payload must be a CandlePayload")
    if payload.interval not in ("3m", "5m", "10m", "15m", "30m", "1h", "4h", "1d"):
        raise ChartDataError("payload interval is unsupported")
    try:
        observations = tuple(payload.observations)
    except TypeError as exc:
        raise ChartDataError("payload observations must be iterable") from exc
    if not observations:
        raise ChartDataError("payload observations must not be empty")
    if not isinstance(payload.ticker, str) or not payload.ticker.strip():
        raise ChartDataError("payload ticker must be non-empty")
    if payload.as_of is not None and not isinstance(payload.as_of, datetime):
        raise ChartDataError("payload as_of must be a datetime")

    validated: list[CandleRecord] = []
    for index, candle in enumerate(observations):
        if not isinstance(candle, CandleRecord):
            raise ChartDataError(f"payload contains invalid candle at row {index}")
        if not isinstance(candle.timestamp, datetime):
            raise ChartDataError(f"payload contains invalid timestamp at row {index}")
        raw_values = (candle.open, candle.high, candle.low, candle.close)
        try:
            values = tuple(
                float(value)
                if not isinstance(value, bool) and math.isfinite(float(value))
                else None
                for value in raw_values
            )
        except (TypeError, ValueError, OverflowError):
            values = (None, None, None, None)
        if any(value is None for value in values):
            raise ChartDataError(f"payload contains invalid OHLC at row {index}")
        open_value, high_value, low_value, close_value = values
        if not (
            low_value <= open_value <= high_value
            and low_value <= close_value <= high_value
        ):
            raise ChartDataError(f"payload contains invalid OHLC at row {index}")

        volume = candle.volume
        if volume is not None:
            try:
                volume = (
                    float(volume)
                    if not isinstance(volume, bool) and math.isfinite(float(volume))
                    else None
                )
            except (TypeError, ValueError, OverflowError):
                volume = None
            if volume is None:
                raise ChartDataError(f"payload contains invalid volume at row {index}")
        validated.append(
            CandleRecord(
                timestamp=candle.timestamp,
                open=open_value,
                high=high_value,
                low=low_value,
                close=close_value,
                volume=volume,
            )
        )
    return tuple(validated)


def _display_lookback(lookback: object) -> str:
    """Disambiguate public month lookbacks from minute intervals in titles."""
    text = str(lookback)
    match = re.fullmatch(r"(\d+)\s*m", text, flags=re.IGNORECASE)
    return f"{match.group(1)}mo" if match else text


def _format_volume(value: float, _position: int | None = None) -> str:
    """Format volume ticks with compact K/M/B units and no scientific offset."""
    magnitude = abs(float(value))
    if magnitude >= 1_000_000_000:
        scaled, suffix = value / 1_000_000_000, "B"
    elif magnitude >= 1_000_000:
        scaled, suffix = value / 1_000_000, "M"
    elif magnitude >= 1_000:
        scaled, suffix = value / 1_000, "K"
    else:
        return f"{value:,.0f}"
    return f"{scaled:.1f}{suffix}".replace(".0", "")


def _title(payload: CandlePayload, observations: tuple[CandleRecord, ...]) -> str:
    metadata = [payload.ticker, payload.interval]
    if payload.lookback is not None:
        metadata.append(_display_lookback(payload.lookback))
    if payload.source:
        metadata.append(payload.source)
    metadata.append(f"{len(observations)} bars")
    metadata.append(
        f"{observations[0].timestamp:%Y-%m-%d}—{observations[-1].timestamp:%Y-%m-%d}"
    )
    if payload.as_of is not None:
        metadata.append(f"as of {payload.as_of:%Y-%m-%d}")
    return " · ".join(metadata)


def _add_background_gradient(figure) -> None:
    """Paint a restrained navy-to-purple wash behind the chart panels."""
    background = figure.add_axes((0, 0, 1, 1), zorder=-10)
    background.imshow(
        np.linspace(0, 1, 256, dtype=float)[:, None],
        aspect="auto",
        cmap=_GRADIENT,
        origin="lower",
        extent=(0, 1, 0, 1),
    )
    background.set_axis_off()


def _style_axis(axis, *, panel_color: str = _PANEL) -> None:
    axis.set_facecolor(panel_color)
    axis.patch.set_alpha(0.96)
    axis.patch.set_path_effects(
        [
            patheffects.SimplePatchShadow(
                offset=(2, -2),
                shadow_rgbFace=_PANEL_SHADOW[0],
                alpha=_PANEL_SHADOW[1],
                rho=0.9,
            ),
            patheffects.Normal(),
        ]
    )
    axis.tick_params(colors=_MUTED_TEXT, labelcolor=_MUTED_TEXT, labelsize=8, length=3)
    for spine in axis.spines.values():
        spine.set_color(_GRID)
        spine.set_alpha(0.58)
        spine.set_linewidth(0.8)
    axis.grid(True, axis="y", color=_GRID, alpha=0.22, linewidth=0.65)
    axis.set_axisbelow(True)


def _candle_width(dates: list[float]) -> float:
    """Choose a compact width in date units without filling short-window gaps."""
    if len(dates) < 2:
        return 0.55
    spacing = median(
        right - left for left, right in pairwise(dates) if right > left
    )
    if not math.isfinite(spacing) or spacing <= 0:
        return 0.55
    return min(0.7, max(0.35, spacing * 0.72))


def _configure_date_axis(axis, dates: list[float], *, intraday: bool = False) -> None:
    """Use adaptive date/time ticks for daily and intraday windows."""
    span = dates[-1] - dates[0] if len(dates) > 1 else 1.0
    if intraday:
        locator = mdates.AutoDateLocator(minticks=5, maxticks=9)
    elif span <= 100:
        interval = max(1, math.ceil((span + 1) / 6))
        locator = mdates.DayLocator(interval=interval)
    else:
        locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    axis.xaxis_date()
    axis.xaxis.set_major_locator(locator)
    if intraday or span > 100:
        axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    else:
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b"))
    axis.tick_params(axis="x", pad=4)


def render_candlestick(payload: CandlePayload, output_path: str | PathLike[str]) -> Path:
    """Render ``payload`` to a deterministic PNG and return its path.

    The renderer only reads the immutable normalized payload. Empty, malformed,
    or directory output targets raise :class:`ChartDataError` explicitly.
    """
    observations = _validate_payload(payload)
    if isinstance(output_path, (str, PathLike)):
        path = Path(output_path)
    else:
        raise ChartDataError("output_path must be a path")
    if path.exists() and path.is_dir():
        raise ChartDataError("output_path must name a file")
    path.parent.mkdir(parents=True, exist_ok=True)

    dates = []
    has_volume = any(observation.volume is not None for observation in observations)
    figure = None
    try:
        dates = [mdates.date2num(observation.timestamp) for observation in observations]
        if has_volume:
            figure, (axis, volume_axis) = plt.subplots(
                2,
                1,
                figsize=(11, 7.2),
                dpi=120,
                sharex=True,
                gridspec_kw={"height_ratios": (4.2, 1.35), "hspace": 0.035},
            )
        else:
            figure, axis = plt.subplots(figsize=(11, 5), dpi=120)
            volume_axis = None

        figure.patch.set_facecolor(_BACKGROUND)
        _add_background_gradient(figure)
        _style_axis(axis)
        axis.set_title(
            _title(payload, observations),
            color=_TEXT,
            fontsize=12,
            loc="left",
            pad=9,
            fontweight="bold",
        )
        axis.set_ylabel("Price", color=_TEXT, fontsize=9, labelpad=8)

        candle_width = _candle_width(dates)
        if payload.interval != "1d" and len(dates) > 1:
            spacing = median(right - left for left, right in pairwise(dates) if right > left)
            candle_width = max(0.0002, spacing * 0.72)
        for date_number, candle in zip(dates, observations):
            color = _UP if candle.close >= candle.open else _DOWN
            axis.vlines(date_number, candle.low, candle.high, color=color, linewidth=1.15, zorder=3)
            body_bottom = min(candle.open, candle.close)
            body_height = max(abs(candle.close - candle.open), 1e-9)
            axis.add_patch(
                Rectangle(
                    (date_number - candle_width / 2, body_bottom),
                    candle_width,
                    body_height,
                    facecolor=color,
                    edgecolor=color,
                    linewidth=0.55,
                    zorder=4,
                    path_effects=[
                        patheffects.SimplePatchShadow(
                            offset=(1.2, -1.2),
                            shadow_rgbFace=_CANDLE_SHADOW[0],
                            alpha=_CANDLE_SHADOW[1],
                            rho=0.85,
                        ),
                        patheffects.Normal(),
                    ],
                )
            )

        _configure_date_axis(axis, dates, intraday=payload.interval != "1d")
        axis.margins(x=0.025, y=0.06)

        if volume_axis is not None:
            _style_axis(volume_axis, panel_color=_PANEL_VOLUME)
            volume_axis.set_ylabel("Volume", color=_TEXT, fontsize=9, labelpad=8)
            for date_number, candle in zip(dates, observations):
                color = _UP if candle.close >= candle.open else _DOWN
                volume_axis.bar(
                    date_number,
                    candle.volume or 0.0,
                    width=candle_width,
                    color=color,
                    edgecolor=color,
                    linewidth=0.25,
                    alpha=0.48,
                    align="center",
                    zorder=3,
                )
            _configure_date_axis(volume_axis, dates, intraday=payload.interval != "1d")
            volume_axis.yaxis.set_major_formatter(FuncFormatter(_format_volume))
            volume_axis.margins(x=0.025, y=0.08)

        figure.subplots_adjust(left=0.075, right=0.985, top=0.89, bottom=0.12)

        figure.savefig(path, format="png", facecolor=figure.get_facecolor(), bbox_inches="tight")
    except (IndexError, TypeError, ValueError, OverflowError) as exc:
        raise ChartDataError("could not render candle payload") from exc
    finally:
        if figure is not None:
            plt.close(figure)
    return path


__all__ = ["render_candlestick"]
