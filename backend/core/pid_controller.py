"""PID controller with autotuning and anti-windup."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def _now_s() -> float:
    return datetime.now(UTC).timestamp()


@dataclass(slots=True)
class PIDConfig:
    kp: float = 1.0
    ki: float = 0.1
    kd: float = 0.05
    output_min: float = 0.0
    output_max: float = 1.0
    integral_min: float = -5.0
    integral_max: float = 5.0
    sample_time: float = 1.0


@dataclass(slots=True)
class PIDState:
    integral: float = 0.0
    last_error: float | None = None
    last_time: float | None = None
    output: float = 0.0


class PIDController:
    def __init__(self, config: PIDConfig | None = None) -> None:
        self.config = config or PIDConfig()
        self.state = PIDState()

    def set_gains(
        self,
        *,
        kp: float | None = None,
        ki: float | None = None,
        kd: float | None = None,
    ) -> None:
        if kp is not None:
            self.config.kp = kp
        if ki is not None:
            self.config.ki = ki
        if kd is not None:
            self.config.kd = kd

    def reset(self) -> None:
        self.state = PIDState()

    def compute(
        self,
        setpoint: float,
        measurement: float,
        *,
        timestamp: float | None = None,
    ) -> float:
        now = timestamp if timestamp is not None else _now_s()
        error = setpoint - measurement

        if self.state.last_time is None:
            self.state.last_time = now
            self.state.last_error = error
            self.state.integral = 0.0
            output = self._clamp(
                self.config.output_min, self.config.output_max, self.config.kp * error
            )
            self.state.output = output
            return output

        dt = now - self.state.last_time
        if dt < self.config.sample_time and dt > 0:
            return self.state.output

        integral = self.state.integral + error * dt
        integral = self._clamp(self.config.integral_min, self.config.integral_max, integral)
        derivative = 0.0
        if self.state.last_error is not None and dt > 0:
            derivative = (error - self.state.last_error) / dt

        output = self.config.kp * error + self.config.ki * integral + self.config.kd * derivative
        output_clamped = self._clamp(self.config.output_min, self.config.output_max, output)

        # Anti-windup: if clamped, reduce integral
        if output != output_clamped:
            integral = self.state.integral  # discard newest contribution to avoid windup

        self.state.integral = integral
        self.state.last_error = error
        self.state.last_time = now
        self.state.output = output_clamped
        return output_clamped

    def autotune(
        self,
        setpoint: float,
        process_variable: float,
        *,
        oscillation_amplitude: float | None = None,
    ) -> PIDConfig:
        error = abs(setpoint - process_variable)
        if error <= 0.1:
            logger.debug("Autotune skipped; error below threshold")
            return self.config

        amp = (
            oscillation_amplitude
            if oscillation_amplitude and oscillation_amplitude > 0
            else max(error, 0.5)
        )
        kp = max(0.5, min(10.0, 1.2 * (error / amp)))
        ki = max(0.01, min(2.0, kp / 4))
        kd = max(0.0, min(1.0, kp / 16))

        self.config.kp = kp
        self.config.ki = ki
        self.config.kd = kd

        logger.info("PID autotune updated gains", extra={"kp": kp, "ki": ki, "kd": kd})
        return self.config

    @staticmethod
    def _clamp(low: float, high: float, value: float) -> float:
        if value < low:
            return low
        if value > high:
            return high
        return value


__all__ = ["PIDConfig", "PIDController", "PIDState"]
