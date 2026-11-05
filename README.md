# unPlugged Forecast

**v0.1.0 experimental** — Home Assistant / HACS integration for **EV-cleaned historical household load + SoC-informed EV session prediction**, with a forecast suitable for [HAEO](https://github.com/hass-energy/haeo).

This is a **forecaster**, not an EV charger controller. It does not command chargers or batteries.

## Install

In HACS → Custom repositories, add `https://github.com/homusman/unPlugged-forecast` as **Integration**. Download, restart Home Assistant and add **unPlugged Forecast** from **Settings → Devices & services**.

The setup asks for:

- **Whole-house power**: live total demand, including all EV charging (W or kW).
- **EV charging power**: live *total* EV charging demand (W or kW).
- **Historical days**: N-day mean of hourly demand after subtracting EV power (default 7, range 1–30).
- **Forecast horizon**: default 48 hours.
- **Forecast resolution**: default 30 minutes (15/30/60 available).

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

**After adding an EV**, the integration reloads, retrieves history again and produces updated forecasts.

## Sensors

- **Load forecast** — e.g. `sensor.unplugged_forecast_load_forecast`; kW. The `forecast` attribute contains timestamped `{"time": "...", "value": 1.23}` points accepted by HAEO. The state is the live whole-house load.
- **Live non-EV baseload** — current whole-house power minus measured EV charger power.
- **Live EV charging load** — current combined EV charging power.
- **Forecast status** — history coverage and errors.

The forecast sensor also includes `ev_predictions` (per-EV current SoC, probability, AC energy to target, expected AC energy and assumed session start), as well as `matched_history_hours`.
## TODO
- use historical SoC data to automatically learn charging behaviour
- detect which EV is plugged in by observing which SoC increases once charge session is detected, use this to accurately update energy needed for current session in forecast.
- 

## V0.1 forecast algorithm

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

If charging power is already detected (>0.2 kW), v0.1 replaces the future event probability with a **confirmed active session**, starting immediately and with duration estimated from remaining SoC and measured charge power.

**Important timing assumption:** A live SoC sensor cannot tell us *when the user will decide to plug in*. The initial heuristic uses a configured start hour for **one next session per EV**, not recurring daily phantom charges. Session-start learning, plugged-in status and vehicle location are future enhancements.

**Multiple EVs:** Because v0.1 accepts one combined EV charger-power sensor, it cannot reliably identify which EV is actively charging. In this prototype, it associates an active session with the EV having the lowest SoC. This is a heuristic. Future versions should support per-EV charger/plug state to resolve this correctly.

**History limitation:** Baseline history requires matching *hourly recorder statistics* for **both** the household power and EV charging power sensors. Missing EV statistics are **not** interpreted as zero, so the forecaster will instead temporarily fall back to current non-EV baseload for missing historical hours. You can check coverage in the Forecast status sensor. Set up both statistics-producing power sensors and retain enough recorder history.

The output updates every 15 minutes, and roughly 20 seconds after live household load, EV charger power or EV SoC changes (debounced). This can trigger HAEO to reoptimize.

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

## Planned v0.2+

- Separate plug-in/charging-power sensors for every EV; distinguish unplanned charging from predicted future sessions.
- Historical charging-event detection and start-time probability distributions.
- Weekday/weekend cleaned baseload profiles; short-term residual correction.
- Probability/scenario selection, confidence bands, calibrated probability models and explicit home-battery reserve policy.
- Tests for missing data, daylight-saving transitions and HAEO interpolation.

**No control commands** are issued by unPlugged Forecast.
