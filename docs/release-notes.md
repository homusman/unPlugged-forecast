# unPlugged Forecast v0.2.14 — persistent charge planning and device controls

Adds per-EV controls and sensors directly to the existing unPlugged Forecast device. Planning is off by default.

Choose Auto, Tonight, Tomorrow night, In 2–7 nights, or Skip with the Charge schedule selector. Configure Charging starts and Ready by (default 07:00), and set Daily driving fallback (default 8 kWh/day). The resolved Next planned charge timestamp remains fixed across midnight and restarts. Replan charge explicitly starts a new intention.

Auto estimates consumption from non-charging SoC decreases in up to 14 days of history, with at least eight readings spanning three days. It uses the fallback until enough history exists. It plans before the minimum SoC reserve is expected to be exhausted, retains that date, and can bring it forward for a near-term reserve risk.

Committed sessions replace probabilistic charging predictions, include estimated driving before charging, and use the live charge target if configured. Active charging uses measured EVSE power and current SoC. Unplanned charging supersedes the future session rather than double counting it.

New sensors show the date, planned AC energy, status and estimated daily driving energy. Diagnostic attributes include the deadline, projected SoC, target, expected completion and energy shortfall. Windows that cannot fit the charge are flagged. Missed and incomplete plans stay visible for explicit replanning.

Charge reminders are opt-in Home Assistant persistent notifications two hours before the session and after a missed/incomplete window. They are not mobile push notifications. Intentions and notification deduplication survive restart in private HA storage.

Update through HACS and restart Core. Open the unPlugged device, set the window and fallback, then select Auto or a night. Existing forecasting is preserved until you select planning. The integration never commands a charger or vehicle.

Rollback: reinstall v0.2.13 and restart Core, or select Off to restore probabilistic charging forecasts.
