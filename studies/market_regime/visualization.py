"""Dependency-free SVG visualization for regime inspection."""

from __future__ import annotations

from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

COLORS = {
    "BULL_LOW_VOL": "#b7e4c7",
    "BULL_HIGH_VOL": "#ffd6a5",
    "NEUTRAL_TRANSITION": "#fff3b0",
    "BEAR_LOW_VOL": "#ffcad4",
    "BEAR_HIGH_VOL": "#ef476f",
    "UNAVAILABLE": "#d9d9d9",
}


def _color_for(state: str) -> str:
    if state in COLORS:
        return COLORS[state]
    if state.startswith("RISK_") and "_OF_" in state:
        try:
            rank = int(state.split("_", 2)[1])
            total = int(state.rsplit("_", 1)[1])
            palette = ["#b7e4c7", "#fff3b0", "#ffd6a5", "#ef476f"]
            index = round((rank - 1) / max(total - 1, 1) * (len(palette) - 1))
            return palette[index]
        except (ValueError, IndexError):
            pass
    return COLORS["UNAVAILABLE"]


def _scale(values: pd.Series, low: float, high: float):
    finite = values[np.isfinite(values)]
    minimum = float(finite.min()) if len(finite) else 0.0
    maximum = float(finite.max()) if len(finite) else 1.0
    if maximum == minimum:
        maximum = minimum + 1.0
    return lambda value: high - (float(value) - minimum) / (maximum - minimum) * (high - low)


def _path(frame: pd.DataFrame, column: str, x_for, y_for, color: str, width: float = 1.5) -> str:
    points = []
    for index, value in enumerate(frame[column]):
        if pd.notna(value) and np.isfinite(float(value)):
            points.append(f"{x_for(index):.2f},{y_for(value):.2f}")
    if not points:
        return ""
    return f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="{width}"/>'


def render_regime_svg(frame: pd.DataFrame, output_path: Path, title: str) -> Path:
    """Render SPY/SMA and VIX/RV panels with regime shading."""
    data = frame.sort_values("date").reset_index(drop=True)
    if data.empty:
        raise ValueError("cannot visualize an empty regime frame")
    width, height = 1500, 760
    left, right = 80, 30
    price_top, price_bottom = 70, 470
    vol_top, vol_bottom = 535, 690
    plot_width = width - left - right
    denominator = max(len(data) - 1, 1)
    x_for = lambda index: left + index / denominator * plot_width
    y_price = _scale(data["spy_close"], price_top, price_bottom)
    vol_values = pd.concat([data["vix"], data["rv_20"] * 100.0], ignore_index=True)
    y_vol = _scale(vol_values, vol_top, vol_bottom)

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{left}" y="32" font-family="sans-serif" font-size="21" font-weight="600">{escape(title)}</text>',
    ]

    states = data["regime"].fillna("UNAVAILABLE").astype(str).tolist()
    run_start = 0
    for index in range(1, len(states) + 1):
        if index == len(states) or states[index] != states[run_start]:
            x1 = x_for(run_start)
            x2 = x_for(index) if index < len(states) else width - right
            color = _color_for(states[run_start])
            elements.append(
                f'<rect x="{x1:.2f}" y="{price_top}" width="{max(x2-x1, 0.5):.2f}" '
                f'height="{vol_bottom-price_top}" fill="{color}" opacity="0.40"/>'
            )
            run_start = index

    elements.extend([
        f'<line x1="{left}" y1="{price_bottom}" x2="{width-right}" y2="{price_bottom}" stroke="#777"/>',
        f'<line x1="{left}" y1="{vol_bottom}" x2="{width-right}" y2="{vol_bottom}" stroke="#777"/>',
        _path(data, "spy_close", x_for, y_price, "#111827", 2.0),
        _path(data, "sma_50", x_for, y_price, "#2563eb", 1.2),
        _path(data, "sma_200", x_for, y_price, "#7c3aed", 1.2),
        _path(data.assign(rv_20_pct=data["rv_20"] * 100.0), "vix", x_for, y_vol, "#dc2626", 1.5),
        _path(data.assign(rv_20_pct=data["rv_20"] * 100.0), "rv_20_pct", x_for, y_vol, "#0f766e", 1.5),
        f'<text x="15" y="{price_top+18}" font-family="sans-serif" font-size="13">SPY</text>',
        f'<text x="15" y="{vol_top+18}" font-family="sans-serif" font-size="13">Vol %</text>',
    ])

    start_date = str(pd.Timestamp(data["date"].iloc[0]).date())
    end_date = str(pd.Timestamp(data["date"].iloc[-1]).date())
    elements.extend([
        f'<text x="{left}" y="{height-28}" font-family="sans-serif" font-size="12">{start_date}</text>',
        f'<text x="{width-right-72}" y="{height-28}" font-family="sans-serif" font-size="12">{end_date}</text>',
        f'<text x="{left+10}" y="{price_top+22}" font-family="sans-serif" font-size="12" fill="#111827">SPY close</text>',
        f'<text x="{left+90}" y="{price_top+22}" font-family="sans-serif" font-size="12" fill="#2563eb">SMA50</text>',
        f'<text x="{left+150}" y="{price_top+22}" font-family="sans-serif" font-size="12" fill="#7c3aed">SMA200</text>',
        f'<text x="{left+10}" y="{vol_top+22}" font-family="sans-serif" font-size="12" fill="#dc2626">VIX</text>',
        f'<text x="{left+50}" y="{vol_top+22}" font-family="sans-serif" font-size="12" fill="#0f766e">RV20 annualized</text>',
    ])
    legend_x = left
    legend_states = list(dict.fromkeys(states))
    for state in legend_states:
        color = _color_for(state)
        elements.append(f'<rect x="{legend_x}" y="{height-20}" width="13" height="10" fill="{color}"/>')
        elements.append(
            f'<text x="{legend_x+17}" y="{height-11}" font-family="sans-serif" font-size="10">{escape(state)}</text>'
        )
        legend_x += 210
    elements.append("</svg>")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(value for value in elements if value) + "\n", encoding="utf-8")
    return output_path
