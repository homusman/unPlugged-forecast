"""Pure timeline utilities for HAEO-compatible load forecasts.

Forecast points represent mean power over consecutive fixed UTC intervals.
The 'previous' interpolation mode preserves that interpretation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math


def aligned_start(now: datetime, interval_minutes: int) -> datetime:
    """Return start of the UTC-aligned prediction interval containing now."""
    if now.tzinfo is None:
        raise ValueError("now must have timezone")
    if interval_minutes <= 0 or 60 % interval_minutes:
        raise ValueError("interval must divide one hour")
    utc = now.astimezone(timezone.utc)
    return utc.replace(
        minute=(utc.minute // interval_minutes) * interval_minutes,
        second=0, microsecond=0,
    )


def session_kw(start: datetime, end: datetime, sessions: list[dict]) -> float:
    """Expected charge power averaged across a full forecast interval."""
    seconds = (end - start).total_seconds()
    if seconds <= 0:
        raise ValueError("positive interval required")
    result = 0.0
    for session in sessions:
        overlap = max(
            0.0,
            (min(end, session["end"]) - max(start, session["start"])).total_seconds(),
        )
        result += float(session["power"]) * overlap / seconds
    return result


def construct_forecast(
    *, now: datetime, horizon_hours: int, interval_minutes: int,
    base_by_hour: list[float | None] | None, live_non_ev_kw: float,
    sessions: list[dict], localize, decay_minutes: float,
    actual_house_kw: float,
) -> list[dict]:
    """Build forecast on a stable UTC grid without an artificial first-point jump.

    A live reading and a future interval average are different quantities.
    Unlike the old implementation, the first point uses the SAME calculation
    as every other point. The sensor's state remains the live reading for
    monitoring, but it is not inserted into the prediction series.

    A measured EV session starting in the current interval is assumed to have
    occupied the full current interval at its observed power. This avoids
    predicting a partial EV charge for HAEO's *remaining* part of that interval.
    """
    anchor = aligned_start(now, interval_minutes)
    interval = timedelta(minutes=interval_minutes)
    count = math.ceil(horizon_hours * 60 / interval_minutes) + 1
    current_hour = localize(now).hour
    historical_now = base_by_hour[current_hour] if base_by_hour else None
    residual = (
        live_non_ev_kw - historical_now if historical_now is not None else 0.0
    )
    output = []
    for i in range(count):
        start = anchor + i * interval
        end = start + interval
        local = localize(start)
        value = base_by_hour[local.hour] if base_by_hour else None
        base = live_non_ev_kw if value is None else value
        if decay_minutes > 0 and historical_now is not None:
            # Do not decay backwards into the past portion of the first bucket.
            minutes = max(0.0, (start - anchor).total_seconds() / 60)
            base += residual * math.exp(-minutes / decay_minutes)
        ev_kw = session_kw(start, end, sessions)
        # The current bucket should be a prediction of ongoing demand rather
        # than an artificially low average of the elapsed part of the bucket.
        if i == 0 and sessions:
            ongoing_kw = sum(
                float(item["power"]) for item in sessions
                if item["start"] <= now < item["end"]
            )
            if ongoing_kw > ev_kw:
                ev_kw = ongoing_kw
        output.append({
            "time": start.isoformat(),
            "value": round(max(0.0, base) + ev_kw, 3),
        })
    return output
