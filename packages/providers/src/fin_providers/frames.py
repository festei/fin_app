"""Vendor price frames -> domain bars."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from fin_domain.market_data import PriceBar


def _num(x: Any) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def bars_from_frame(frame: pd.DataFrame) -> list[PriceBar]:
    """Provider frame (first column = timestamp, then Open/High/Low/Close/Volume) -> bars.

    Timestamps are converted to UTC (naive ones are taken as UTC). Rows without a close are
    dropped, other missing values become ``None``.
    """
    if frame.empty:
        return []
    ts_col = frame.columns[0]
    bars: list[PriceBar] = []
    for rec in frame.to_dict("records"):
        close = _num(rec.get("Close"))
        if close is None:
            continue
        ts = pd.Timestamp(rec[ts_col])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        volume = _num(rec.get("Volume"))
        bars.append(
            PriceBar(
                ts=ts.to_pydatetime(),
                open=_num(rec.get("Open")),
                high=_num(rec.get("High")),
                low=_num(rec.get("Low")),
                close=close,
                volume=int(volume) if volume is not None else None,
            )
        )
    return bars
