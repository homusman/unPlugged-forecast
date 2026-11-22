# unPlugged Forecast v0.2.12 — inertia-based load adaptation

Adds an optional Inertia live-load correction mode. Sustained measured non-EV
load deviations gradually adjust the next few forecast hours; brief appliance
spikes have much less influence than immediate live correction.

Select Configure → Forecast settings → Live-load correction mode → Inertia.
Start with 20-minute learning time constant, 3-hour forecast window, 0.1 kW
deadband and 3 kW maximum correction. Legacy decay may remain 0 in this mode.
Existing installations keep their old mode until explicitly changed.

The correction holds for the first two-thirds of its forecast window and tapers
to zero over the final third. EV charging remains separate. Diagnostics show the
measured residual, smoothed bias and applied offset. Stale data does not train the
estimate. Restart/reload resets learning; allow time for it to build again.

Regression coverage includes microwave pulses versus sustained loads, event-rate
independence, stale readings and gaps, negative/bounded corrections, forecast
energy accounting, EV subtraction and Home Assistant settings serialization.

Update through HACS and restart Home Assistant. HAEO's acceptance rules and
actuator dispatch behavior are unchanged.
