# unPlugged Forecast

**v0.2.0 experimental** — Home Assistant / HACS integration for **EV-cleaned historical household load + SoC-informed EV session prediction**, with a forecast suitable for [HAEO](https://github.com/hass-energy/haeo).

This is a **forecaster**, not an EV charger controller. It does not command chargers or batteries.

## Install

In HACS → Custom repositories, add `https://github.com/homusman/unPlugged-forecast` as **Integration**. Download, restart Home Assistant and add **unPlugged Forecast** from **Settings → Devices & services**.

The setup asks for:

- **Whole-house power**: live total demand, including all EV charging (W or kW).
- **EV charging power**: live *total* EV charging demand (W or kW).
- **Historical days**: N-day mean of hourly demand after subtracting EV power (default 7, range 1–30).
- **Forecast horizon**: default 48 hours.
- **Forecast resolution**: default 30 minutes (15/30/60 available).
- **Live correction decay**: default 30 minutes (0 disables). Deviations of the current non-EV load from the historical baseline are added to the near-term projection with an exponential decay, instead of persisting for 48 hours.

Then open the integration's **Configure** (Options) and choose **Add an EV** for each car. Each EV includes:

| EV setting | Purpose |
| --- | --- |
| Name | Distinguishes cars |
| Typical min SoC | Near/below this SoC, charging is highly likely |
| Typical max SoC | At/above this SoC, forecast no charging |
| Usable battery capacity (kWh) | Convert SoC deficit to energy |
| Actual EV SoC sensor | Read current vehicle SoC |
| Assumed charger power (kW) | Determine duration of a predicted future session |
| Assumed next charging hour | **Timing assumption**, defaults to 18:00 local |
| Probability curve exponent | Defaults to 2: more likely at low SoC |
| Optional away status | Binary sensor ON or device tracker `not_home`; modestly raises charge likelihood for away EVs |

**After adding an EV**, the integration reloads, retrieves history again and produces updated forecasts.

## Sensors

- **Load forecast** — e.g. `sensor.unplugged_forecast_load_forecast`; kW. The `forecast` attribute contains timestamped `{"time": "...", "value": 1.23}` points accepted by HAEO. The state is the live whole-house load.
- **Live non-EV baseload** — current whole-house power minus measured EV charger power.
- **Live EV charging load** — current combined EV charging power.
- **Forecast status** — history coverage and errors.

The forecast sensor also includes `ev_predictions` (per-EV current SoC, probability, AC energy to target, expected AC energy and assumed session start), as well as `matched_history_hours`.
## v0.2 implemented (initial heuristics)

- **Session learning:** recorder raw EV charging-power transitions are grouped into sessions (up to at least 14 days of history). Candidate start slots use observed hour and weekday, weighted toward matching weekdays; the configured start hour remains a prior.
- **EV identification:** compare per-EV SoC increases during observed sessions. An ongoing charge is attributed to an EV with SoC increase >=0.5%; otherwise explicitly label the lowest-relative-SoC fallback **unconfirmed**.
- **Missed sessions:** past candidate starts are excluded after a 45-minute grace period and the expected-event probability is redistributed across future candidates (including later evenings). It **does not** multiply the full energy demand by every candidate.
- **Unexpected charging:** measured EV power always overrides the SoC probability for an active session, including when SoC is above the configured typical maximum. When energy to normal target is exhausted, use a provisional one-hour estimate rather than pretend charging stops instantly.
- **Away state:** optional binary sensor (`on`) or device tracker (`not_home`); raises probability by 25% if away. Without an ETA this does not predict the precise return time.
- **Diagnostics:** the forecast sensor now includes each EV's session candidates, identification method, SoC gains, away state and historical session count.
- **Short-term live household feedback:** unexpected non-EV power is blended into the future baseline as `live_residual * exp(-minutes_ahead / decay_minutes)`; EV charging is modelled separately from measured charging power and SoC.
- **Regression tests:** `python -m unittest discover -s tests -v` tests the pure learning helpers.

### Still planned

- Confident charger-to-vehicle identification (per-EV charging power / plugged-in sensors); active SoC rise is observational and may be delayed.
- Proper expected **daily EV driving energy / future SoC** estimation over multi-day horizons. v0.2 distributes the current-SoC-derived expected **next** charge over possible starts, not a complete recurrent driving/charging simulation.
- Better learned arrival-time and day-of-week probability models, calibrated from non-events as well as sessions; the current weights are a heuristic, **not statistically calibrated probabilities**.
- Live charger target SoC, per-EV charge efficiency and confirmed charging stop prediction.
- Sophisticated weekday/weekend baseload and short-term residual corrections.
- Better household load detection & forecast. Based on historic data, start to observe big ticket loads and their expected energy useage (if trends are observable), e.g. heat pump is well recogniseable, or dryer, etc. many loads wont be recogniseable though.

## Baseline and SoC forecast algorithm

```text
Historical baseline hour-of-day =
    mean over the last N days of max(0, whole-house power - EV charger power)

P(charge | SoC) =
    clamp((typical_max_soc - soc) / (typical_max_soc - typical_min_soc), 0, 1) ^ gamma

EV full AC demand in kWh =
    usable_capacity_kWh * max(0, max_soc - current_soc) / 100 / 0.90

Expected EV power profile =
    P(charge | SoC) × charger_power_kW,
    during one finite charging session beginning at assumed start hour.

Total forecast = cleaned baseline + sum of all predicted EV sessions
```

If charging power is already detected (>=0.25 kW), v0.2 replaces the future event probability with a **confirmed active session**, starting immediately and with duration estimated from remaining SoC and measured charge power.

**Important timing assumption:** A live SoC sensor cannot tell us *when the user will decide to plug in*. The initial heuristic uses a configured start hour as a **prior**. Once sufficient recorder history exists, candidate start times are also learned from charging sessions; future candidate weights sum to at most one expected event per EV. Explicit plug-in/ETA telemetry remains a future enhancement.

**Multiple EVs:** One combined EV charger-power sensor still cannot definitively identify a charging EV. v0.2 uses rising SoC in recent history where possible. If no EV shows a convincing rise yet, it falls back to the lowest relative SoC and explicitly marks identification as unconfirmed. Per-EV charger/plug signals remain preferable.

**History limitation:** Baseline history requires matching *hourly recorder statistics* for **both** the household power and EV charging power sensors. Missing EV statistics are **not** interpreted as zero, so the forecaster will instead temporarily fall back to current non-EV baseload for missing historical hours. You can check coverage in the Forecast status sensor. Set up both statistics-producing power sensors and retain enough recorder history.

The forecast is republished every 5 minutes, its historical baseline/session model is refreshed hourly, and live household load, EV charger power, EV SoC or away state changes trigger a forecast update after ~20 seconds (debounced). This can trigger HAEO to reoptimize.

## How live load is used

The **current** state of the Load forecast sensor is the measured whole-house power, including actual EV charging. The time series in its `forecast` attribute uses the EV-free historical load baseline plus predicted EV sessions.

**Live non-EV correction:** at each update, the integration computes
`residual = (current_house_power - current_ev_power) - historical_nonEV_baseline_now`.
This residual is added to the **future non-EV** load forecast with exponential decay (configurable 0–240 minutes, default 30). Thus a 2 kW unexpected oven or appliance load modifies the first few forecast periods without being assumed to run all night. Set the decay to **0** to use the pure historical average for future non-EV loads.

**Live EV charging:** whenever measured EV charging is detected, active power and current SoC replace the speculative EV session with a confirmed near-term forecast. When the session ends, the charging profile disappears. The *measured* charger power and *predicted* charger power are **never added together** for the same current session.

**Limitations:** The first forecast point is measured consumption; later points are future predictions. Forecast points are spaced by the configured interval; HAEO interpolates its own optimization periods. Short bursts and stale sensors can make this prediction inaccurate. The historical baseline excludes EV charging only where both power sensors have matching Recorder statistics.

## Feed it to HAEO

Configure HAEO's **Load** element with the **unPlugged Forecast → Load forecast** sensor, replacing the static 1 kW load or HAFO feed. Do **not** additionally include the raw whole-house load sensor or a separate EV charge-power load, because EV charging is already included in this forecast.

HAEO's load forecasts use time/value pairs in kW:
[HAEO forecasts and sensors](https://github.com/hass-energy/haeo/blob/main/docs/user-guide/forecasts-and-sensors.md).

## Commissioning checks

1. Confirm whole-house load is positive and EV charger power is positive when charging.
2. Verify `Live non-EV baseload` is not negative and compare the two source power sensors.
3. Check at least 24 matched hours of historical household **and** EV statistics; preferably N × 24.
4. Open the forecast sensor attributes: check `forecast`, `ev_predictions`, session timing and total expected AC energy.
5. Confirm HAEO replans after a known charging-power or EV SoC change.
6. Avoid using speculative EV forecasts alone to guarantee energy for an important trip. This is an expected-value model, not a hard reserve.

## Planned v0.3+

- Separate plug-in/charging-power sensors for every EV; distinguish unplanned charging from predicted future sessions.
- Calibrated charging-event and non-event probabilities with proper driving-energy replenishment over long horizons.
- Weekday/weekend cleaned baseload profiles; short-term residual correction.
- Probability/scenario selection, confidence bands, calibrated probability models and explicit home-battery reserve policy.
- Tests for missing data, daylight-saving transitions and HAEO interpolation.

**No control commands** are issued by unPlugged Forecast.
