"""Persistent EV charging intentions; pure calculations in local calendar time."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, time
import math

MODES = ["Off", "Auto", "Tonight", "Tomorrow night", *[f"In {day} nights" for day in range(2, 8)], "Skip"]
UTC = timezone.utc


def driving_energy(rows, capacity):
    """Estimate daily discharge energy from at least three days of SoC history."""
    rows = sorted({at: value for at, value in rows if 0 <= value <= 100}.items())
    if len(rows) < 8:
        return None
    days = (rows[-1][0] - rows[0][0]).total_seconds() / 86400
    if days < 3:
        return None
    loss = sum(max(0.0, before - after) for (_, before), (_, after) in zip(rows, rows[1:]))
    return min(float(capacity), loss * capacity / 100 / days)


def window(day, settings, ev, tz):
    """Resolve an evening date and local times to a UTC charging window."""
    start_time = settings.get("start_time", f"{int(ev['start_hour']):02}:00:00")
    ready_time = settings.get("ready_time", "07:00:00")
    start = datetime.combine(day, datetime.strptime(start_time, "%H:%M:%S").time(), tzinfo=tz)
    end = datetime.combine(day, datetime.strptime(ready_time, "%H:%M:%S").time(), tzinfo=tz)
    if end <= start:
        end += timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def update_plan(settings, ev, now, soc, target, active, measured_power, rows, localize):
    """Update a JSON-compatible intention in place and return its forecast diagnostics."""
    mode = settings.get("mode", "Off")
    local = localize(now)
    learned = driving_energy(rows, float(ev["capacity_kwh"]))
    daily = learned if learned is not None else float(settings.get("daily_kwh", 8))
    result = {"mode": mode, "daily_driving_kwh": round(daily, 3),
              "driving_source": "SoC history" if learned is not None else "configured fallback",
              "start": None, "ready_by": None, "energy_kwh": 0.0}
    if mode in ("Off", "Skip"):
        return {**result, "status": "Off" if mode == "Off" else "Skipped"}
    if soc is None:
        return {**result, "status": "Awaiting SoC"}
    capacity = float(ev["capacity_kwh"])
    record = settings.get("plan")
    if record and (active or record.get("observed") or now >= datetime.fromisoformat(record["start"])) and soc >= target - 0.1:
        record["status"] = "Completed"
    if record and record.get("status") == "Completed" and mode == "Auto" and not active:
        settings["last_completed"] = record["start"]
        settings.pop("plan", None)
        record = None
    if record is None:
        if mode == "Auto":
            reserve = min(float(ev["min_soc"]), max(0.0, target - 1))
            usable = max(0, (soc - reserve) * capacity / 100)
            if target <= 0 or (daily <= 0 and usable > 0):
                return {**result, "status": "No charging needed"}
            days = max(0, min(30, math.floor(usable / daily) - 1)) if daily > 0 else 0
            if soc >= target - 0.1 and days == 0:
                days = 1
        else:
            days = MODES.index(mode) - 2
        start, end = window(local.date() + timedelta(days=days), settings, ev, local.tzinfo)
        record = {"start": start.isoformat(), "ready_by": end.isoformat(), "status": "Scheduled"}
        settings["plan"] = record
    start, end = datetime.fromisoformat(record["start"]), datetime.fromisoformat(record["ready_by"])
    if mode == "Auto" and not active and not record.get("observed") and start > now:
        reserve = min(float(ev["min_soc"]), max(0.0, target - 1))
        if (soc - reserve) * capacity / 100 < daily and localize(start).date() > local.date():
            start, end = window(local.date(), settings, ev, local.tzinfo)
            record.update(start=start.isoformat(), ready_by=end.isoformat(), status="Scheduled",
                          reason="Brought forward because current SoC risks the driving reserve")
            record.pop("notified", None)
    if active and not record.get("observed"):
        record["original_start"] = record["start"]
        start = now
        ready = datetime.combine(local.date(), time.fromisoformat(settings.get("ready_time", "07:00:00")), tzinfo=local.tzinfo)
        if ready <= local:
            ready += timedelta(days=1)
        end = ready.astimezone(UTC)
        record.update(start=start.isoformat(), ready_by=end.isoformat(), observed=True)
    if active:
        record["observed"] = True
    if record.get("status") == "Completed" and active and soc < target - 0.1:
        record["status"] = "Charging"
    if record.get("status") != "Completed":
        if active:
            record["status"] = "Charging"
        elif now >= end:
            record["status"] = "Incomplete" if record.get("observed") else "Missed"
        else:
            record["status"] = "Scheduled"
    projected_soc = max(0.0, soc - daily * max(0, (start - now).total_seconds()) / 86400 / capacity * 100)
    power = measured_power if active else float(ev["charge_power_kw"])
    energy = max(0, target - (soc if active else projected_soc)) / 100 * capacity / 0.9
    if record["status"] == "Completed":
        energy = 0.0
    available = max(0, (end - max(start, now)).total_seconds()) / 3600
    result.update(status=record["status"], start=record["start"], ready_by=record["ready_by"],
                  energy_kwh=round(energy, 3), projected_soc=round(projected_soc, 2), target_soc=target,
                  power_kw=power, can_finish=power * available + 0.01 >= energy,
                  expected_end=(max(start, now) + timedelta(hours=energy / power)).isoformat() if power > 0 else None,
                  unserved_energy_kwh=round(max(0, energy - power * available), 3))
    return result
