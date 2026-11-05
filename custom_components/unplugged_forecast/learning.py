"""Learn charging sessions and start-time distributions from recorder history.

No estimator here controls a charger. Sessions come from charging-power
transitions, with SoC rises used to attribute sessions to an individual EV.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from math import isfinite


def parse_series(states):
    """Return (UTC timestamp, numeric value) pairs from HA recorder states."""
    result = []
    for state in states or []:
        try:
            value = float(state.state)
            if not isfinite(value):
                continue
            stamp = state.last_updated
            if stamp is not None:
                result.append((stamp, value))
        except (AttributeError, ValueError, TypeError):
            continue
    return sorted(result, key=lambda x: x[0])


def power_multiplier(unit):
    return {"w": 0.001, "kw": 1.0, "mw": 1000.0}.get((unit or "").lower())


def extract_sessions(power_series, soc_series, min_power=0.25, gap_minutes=20):
    """Detect charging windows and score per-EV SoC growth.

    Each power point is a state lasting until the next point. A brief off
    transition (< gap) does not split a charging session.
    """
    intervals = []
    for (at, val), (end, _) in zip(power_series, power_series[1:]):
        if val >= min_power and end > at:
            intervals.append((at, end))
    if not intervals:
        return []
    windows = []
    start, end = intervals[0]
    for a, b in intervals[1:]:
        if (a - end).total_seconds() <= gap_minutes * 60:
            end = b
        else:
            windows.append((start, end))
            start, end = a, b
    windows.append((start, end))

    sessions = []
    for start, end in windows:
        if (end - start).total_seconds() < 8 * 60:
            continue
        gains = {}
        for ev_id, rows in soc_series.items():
            before = [v for at, v in rows if start - timedelta(hours=1) <= at <= start]
            during = [v for at, v in rows if start <= at <= end + timedelta(minutes=20)]
            if not before or not during:
                continue
            gains[ev_id] = max(0.0, max(during) - before[-1])
        best = max(gains, key=gains.get) if gains else None
        assigned = best if best is not None and gains[best] >= 0.75 else None
        sessions.append({
            "start": start, "end": end, "ev_id": assigned,
            "soc_gains": {k: round(v, 2) for k, v in gains.items()},
        })
    return sessions


def recent_soc_delta(rows, since):
    """Return SoC rise since start, ignoring stale or missing observations."""
    values = [v for at, v in rows if at >= since]
    if len(values) < 2:
        return 0.0
    return max(0.0, max(values) - values[0])


def candidate_weights(now_local, ev, sessions, localize, horizon_hours):
    """Candidate starts weighted by matching weekday and learned local hours.

    Uses a configurable start-hour prior when no observations exist.
    Never creates a second full session: weights are normalized later.
    """
    relevant = [s for s in sessions if s["ev_id"] == ev["id"]]
    candidates = []
    today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    # Carry only candidates inside the requested horizon.
    for day_offset in range((horizon_hours + 47) // 24 + 1):
        day = today + timedelta(days=day_offset)
        observations = []
        for session in relevant:
            historic = localize(session["start"])
            if historic.weekday() == day.weekday():
                observations.append((historic.hour, historic.minute, 3.0))
            else:
                observations.append((historic.hour, historic.minute, 0.7))
        # Preserve a prior even with sparse historical charging sessions.
        observations.append((int(ev["start_hour"]), 0, 2.0 if relevant else 6.0))
        for hour, minute, weight in observations:
            candidate = day.replace(hour=hour, minute=minute)
            # Give a past due event a grace period before considering it missed.
            if candidate <= now_local - timedelta(minutes=45):
                continue
            if candidate > now_local + timedelta(hours=horizon_hours):
                continue
            candidates.append((candidate, weight))
    # Merge near-identical starts into hour buckets.
    grouped = defaultdict(float)
    for at, weight in candidates:
        key = at.replace(minute=0, second=0, microsecond=0)
        grouped[key] += weight
    return sorted(grouped.items())


def soc_probability(soc, low, high, exponent):
    if high <= low:
        return 0.0
    frac = min(1.0, max(0.0, (high - soc) / (high - low)))
    return frac ** exponent
