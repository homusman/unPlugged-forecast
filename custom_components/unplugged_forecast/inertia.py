"""Time-weighted persistence learning for non-EV forecast errors."""

from dataclasses import dataclass
from datetime import datetime
import math


def correction_mode(options):
    """Keep existing installs' correction policy until explicitly changed."""
    return options.get("live_correction_mode") or (
        "decay" if float(options.get("live_decay_minutes", 30)) > 0 else "none"
    )


@dataclass
class LoadInertia:
    """Integrate the previously observed residual; never backfill new evidence."""

    fast_kw: float = 0.0
    bias_kw: float = 0.0
    last_at: datetime | None = None
    last_residual: float | None = None
    last_fresh_at: datetime | None = None
    learning: bool = False

    def update(self, now, residual_kw, response_minutes=20, deadband_kw=0.1, maximum_kw=3):
        """Advance an exact two-stage filter for a piecewise-constant input.

        Fast time constant is one quarter of the configured slow time constant.
        Unknown/stale samples pause learning; gaps over five minutes reset it.
        """
        if response_minutes <= 0 or deadband_kw < 0 or maximum_kw <= 0:
            raise ValueError("Invalid inertia settings")
        if residual_kw is not None and not math.isfinite(residual_kw):
            residual_kw = None
        elapsed = (now - self.last_at).total_seconds() / 60 if self.last_at else 0.0
        fresh_gap = (now - self.last_fresh_at).total_seconds() / 60 if self.last_fresh_at else 0.0
        if elapsed < 0 or fresh_gap > 5:
            self.fast_kw = self.bias_kw = 0.0
            self.last_residual = None
        elif elapsed > 0 and self.last_residual is not None and residual_kw is not None:
            limit = maximum_kw + deadband_kw
            target = max(-limit, min(limit, self.last_residual))
            fast_tau = response_minutes / 4
            fast_decay = math.exp(-elapsed / fast_tau)
            slow_decay = math.exp(-elapsed / response_minutes)
            previous_fast = self.fast_kw
            self.fast_kw = target + (previous_fast - target) * fast_decay
            self.bias_kw = (
                target + (self.bias_kw - target) * slow_decay
                + (previous_fast - target) * fast_tau / (fast_tau - response_minutes)
                * (fast_decay - slow_decay)
            )
        self.last_at = now
        self.last_residual = residual_kw
        self.learning = residual_kw is not None
        if self.learning:
            self.last_fresh_at = now
        magnitude = max(0.0, min(maximum_kw, abs(self.bias_kw) - deadband_kw))
        return math.copysign(magnitude, self.bias_kw)
