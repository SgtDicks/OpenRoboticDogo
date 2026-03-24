from __future__ import annotations

import math
import time
from dataclasses import dataclass

from doggo.config import AppConfig, LEG_NAMES, LegName
from doggo.models import TeleopCommand


@dataclass(slots=True)
class GaitPlannerStatus:
    ready: bool
    reason: str


@dataclass(slots=True)
class GaitFrame:
    ready: bool
    reason: str
    commands: list[tuple[int, int]]
    speed: int
    acceleration: int


class CrawlGaitPlanner:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._active_since: float | None = None

    def status(self) -> GaitPlannerStatus:
        if not self.config.walking.enabled:
            return GaitPlannerStatus(
                ready=False,
                reason="Walking is disabled in config.",
            )
        if self.config.sit_pose is None:
            return GaitPlannerStatus(
                ready=False,
                reason="Walking needs a calibrated sit pose so lift directions can be derived safely.",
            )
        configured_order = self.config.walking.leg_order
        if sorted(configured_order) != sorted(LEG_NAMES):
            return GaitPlannerStatus(
                ready=False,
                reason="Walking leg_order must contain each leg exactly once.",
            )
        return GaitPlannerStatus(
            ready=True,
            reason="Basic crawl gait is ready for low-speed teleop tuning.",
        )

    def accept(self, command: TeleopCommand) -> GaitFrame:
        status = self.status()
        speed = self.config.walking.step_speed
        acceleration = self.config.walking.step_acceleration
        if not status.ready:
            self._active_since = None
            return GaitFrame(
                ready=False,
                reason=status.reason,
                commands=[],
                speed=speed,
                acceleration=acceleration,
            )

        intensity = max(
            abs(command.axes.forward),
            abs(command.axes.strafe),
            abs(command.axes.turn),
        )
        if intensity <= self.config.walking.command_deadzone:
            self._active_since = None
            return GaitFrame(
                ready=False,
                reason="Teleop inputs are inside the walking deadzone.",
                commands=[],
                speed=speed,
                acceleration=acceleration,
            )

        now = time.monotonic()
        if self._active_since is None:
            self._active_since = now

        phase = ((now - self._active_since) / self.config.walking.cycle_time_seconds) % 1.0
        commands: list[tuple[int, int]] = []
        leg_count = len(self.config.walking.leg_order)

        for index, leg_name in enumerate(self.config.walking.leg_order):
            leg_phase = (phase - (index / leg_count)) % 1.0
            commands.extend(
                self._commands_for_leg(
                    leg_name,
                    command,
                    leg_phase=leg_phase,
                    intensity=intensity,
                )
            )

        summary = (
            "Generated crawl gait frame "
            f"(forward={command.axes.forward:.2f}, strafe={command.axes.strafe:.2f}, turn={command.axes.turn:.2f})."
        )
        return GaitFrame(
            ready=True,
            reason=summary,
            commands=commands,
            speed=speed,
            acceleration=acceleration,
        )

    def _commands_for_leg(
        self,
        leg_name: LegName,
        command: TeleopCommand,
        *,
        leg_phase: float,
        intensity: float,
    ) -> list[tuple[int, int]]:
        leg_config = getattr(self.config.legs, leg_name)
        stand_pose = getattr(self.config.stand_pose, leg_name)
        sit_pose = getattr(self.config.sit_pose, leg_name)
        tuning = getattr(self.config.walking.legs, leg_name)

        sweep, lift = self._phase_components(leg_phase)
        hip_sweep = command.axes.strafe * tuning.strafe_sign
        knee_sweep = max(
            -1.0,
            min(1.0, command.axes.forward * tuning.forward_sign + command.axes.turn * tuning.turn_sign),
        )

        hip_target = self._clamp_target(
            leg_config.hip_x.id,
            stand_pose.hip_x + round(hip_sweep * self.config.walking.hip_strafe_ticks * sweep),
        )
        knee_target = self._clamp_target(
            leg_config.knee_y.id,
            stand_pose.knee_y
            + round(knee_sweep * self.config.walking.knee_stride_ticks * sweep)
            + round(self._lift_sign(stand_pose.knee_y, sit_pose.knee_y) * self.config.walking.knee_lift_ticks * lift * intensity),
        )
        foot_target = self._clamp_target(
            leg_config.foot.id,
            stand_pose.foot
            + round(self._lift_sign(stand_pose.foot, sit_pose.foot) * self.config.walking.foot_lift_ticks * lift * intensity),
        )

        return [
            (leg_config.hip_x.id, hip_target),
            (leg_config.knee_y.id, knee_target),
            (leg_config.foot.id, foot_target),
        ]

    def _phase_components(self, phase: float) -> tuple[float, float]:
        swing_ratio = self.config.walking.swing_ratio
        if phase < swing_ratio:
            local_phase = phase / swing_ratio
            return (-1.0 + (2.0 * local_phase), math.sin(math.pi * local_phase))

        local_phase = (phase - swing_ratio) / (1.0 - swing_ratio)
        return (1.0 - (2.0 * local_phase), 0.0)

    def _lift_sign(self, stand_ticks: int, sit_ticks: int) -> int:
        return 1 if sit_ticks >= stand_ticks else -1

    def _clamp_target(self, servo_id: int, target: int) -> int:
        joint = self.config.joint_config_for_servo(servo_id)
        if joint is None:
            return target
        return max(joint.min_ticks, min(joint.max_ticks, target))
