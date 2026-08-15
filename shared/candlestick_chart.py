"""Deterministic dark-theme renderer for normalized daily candle payloads."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import math
from os import PathLike

import matplotlib

# Select a non-interactive backend before importing pyplot for CLI/headless use.
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from shared.chart_data import CandlePayload, CandleRecord, ChartDataError


_BACKGROUND = "#111827"
_PANEL = "#182235"
_TEXT = "#E5E7EB"
_GRID = "#374151"
_UP = "#22C55E"
_DOWN = "#EF4444"


def _validate_payload(payload: CandlePayload) -> tuple[CandleRecord, ...]:
    if not isinstance(payload, CandlePayload):
        raise ChartDataError("payload must be a CandlePayload")
    if payload.interval != "1d":
        raise ChartDataError("payload interval must be '1d'")
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


def _title(payload: CandlePayload, observations: tuple[CandleRecord, ...]) -> str:
    metadata = [payload.ticker, payload.interval]
    if payload.lookback is not None:
        metadata.append(f"lookback {payload.lookback}")
    if payload.source:
        metadata.append(f"source {payload.source}")
    if payload.as_of is not None:
        metadata.append(f"as of {payload.as_of:%Y-%m-%d}")
    metadata.append(
        f"observations {observations[0].timestamp:%Y-%m-%d} "
        f"to {observations[-1].timestamp:%Y-%m-%d}"
    )
    return " · ".join(metadata)


def _style_axis(axis) -> None:
    axis.set_facecolor(_PANEL)
    axis.tick_params(colors=_TEXT, labelcolor=_TEXT)
    for spine in axis.spines.values():
        spine.set_color(_GRID)
    axis.grid(True, axis="y", color=_GRID, alpha=0.55, linewidth=0.7)
    axis.set_axisbelow(True)


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
                figsize=(11, 7),
                dpi=120,
                sharex=True,
                gridspec_kw={"height_ratios": (3, 1), "hspace": 0.05},
            )
        else:
            figure, axis = plt.subplots(figsize=(11, 5), dpi=120)
            volume_axis = None

        figure.patch.set_facecolor(_BACKGROUND)
        _style_axis(axis)
        axis.set_title(
            _title(payload, observations),
            color=_TEXT,
            fontsize=13,
            loc="left",
            pad=12,
            fontweight="bold",
        )
        axis.set_ylabel("Price", color=_TEXT)

        span = max(dates[-1] - dates[0], 1.0)
        candle_width = min(0.7, span / max(len(dates), 2))
        for date_number, candle in zip(dates, observations):
            color = _UP if candle.close >= candle.open else _DOWN
            axis.vlines(date_number, candle.low, candle.high, color=color, linewidth=1.4, zorder=3)
            body_bottom = min(candle.open, candle.close)
            body_height = max(abs(candle.close - candle.open), 1e-9)
            axis.add_patch(
                Rectangle(
                    (date_number - candle_width / 2, body_bottom),
                    candle_width,
                    body_height,
                    facecolor=color,
                    edgecolor=color,
                    linewidth=0.8,
                    zorder=4,
                )
            )

        axis.xaxis_date()
        axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
        axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(axis.xaxis.get_major_locator()))
        axis.margins(x=0.03)

        if volume_axis is not None:
            _style_axis(volume_axis)
            volume_axis.set_ylabel("Volume", color=_TEXT)
            for date_number, candle in zip(dates, observations):
                color = _UP if candle.close >= candle.open else _DOWN
                volume_axis.bar(
                    date_number,
                    candle.volume or 0.0,
                    width=candle_width,
                    color=color,
                    alpha=0.8,
                    align="center",
                )
            volume_axis.xaxis_date()
            volume_axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
            volume_axis.xaxis.set_major_formatter(
                mdates.ConciseDateFormatter(volume_axis.xaxis.get_major_locator())
            )
            volume_axis.margins(x=0.03)

        figure.savefig(path, format="png", facecolor=figure.get_facecolor(), bbox_inches="tight")
    except (IndexError, TypeError, ValueError, OverflowError) as exc:
        raise ChartDataError("could not render candle payload") from exc
    finally:
        if figure is not None:
            plt.close(figure)
    return path


__all__ = ["render_candlestick"]
