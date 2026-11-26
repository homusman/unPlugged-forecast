# unPlugged Forecast v0.2.13 — live EV charge targets

Adds an optional charge-limit sensor or number entity to each EV. Configure → Edit EV → Charge limit entity (%) accepts the Tesla integration's live 0–100% charge target. It overrides the configured maximum for remaining charging energy and upcoming-session probability, including temporary higher charge targets.

Active sessions use measured EVSE power rather than the configured assumed rate. A rate change recalculates the remaining duration while preserving energy needed to reach the live target. Charge-rate and target changes now bypass the household forecast's 100 W notification threshold; source events retain the existing 20-second debounce. Future sessions still use configured assumed power until charging is detected.

The live limit uses the existing input grace period during unavailable states, then falls back to the configured maximum. A valid target already reached no longer creates the one-hour provisional charging forecast. Diagnostics expose target value/source, selected entity, charging power/source and estimated remaining hours. Charging energy retains the existing 90% AC efficiency assumption.

Update in HACS and restart Home Assistant Core, then edit the EV to select its charge-limit entity. No entity is selected automatically; existing configurations retain their target behavior. The integration reads the selected entity only and does not change the Tesla's charge limit or charge rate.

Tests cover live targets, invalid states, bounded grace/recovery, rate changes, target reached, event subscription, optional field clearing and existing forecasting behavior. For rollback, reinstall v0.2.12 through HACS and restart Core.
