"""Observed price levels for display only; never used to rank or trade."""
import math


def first_resistance(highs, close, atr, day_index, symbol_index):
    """Nearest overhead high cluster known by the signal day's close.

    Prefer the signal day and preceding 20 sessions, then expand to 60 or
    120 sessions only if the nearer window has no high above the close.
    Highs within half an ATR of the nearest level form one observation zone.
    """
    close = float(close)
    if not math.isfinite(close) or close <= 0:
        return None
    width = float(atr) / 2 if math.isfinite(float(atr)) and float(atr) > 0 else close * .01
    for lookback in (20, 60, 120):
        values = [float(value) for value in highs[max(0, day_index - lookback):day_index + 1, symbol_index]
                  if math.isfinite(float(value)) and float(value) > close]
        if not values:
            continue
        nearest = min(values)
        cluster = [value for value in values if value <= nearest + width + 1e-9]
        return dict(low=round(nearest, 5), high=round(max(cluster), 5), lookback=lookback)
    return None
