from __future__ import annotations

from pathlib import Path
from typing import Iterator, Literal

import yaml
from pydantic import BaseModel, Field

LEG_NAMES = ("front_left", "front_right", "rear_left", "rear_right")
JOINT_NAMES = ("hip_x", "knee_y", "foot")
LegName = Literal["front_left", "front_right", "rear_left", "rear_right"]
JointName = Literal["hip_x", "knee_y", "foot"]


class RobotMetadata(BaseModel):
    name: str = "Doggo"


class ServoBusConfig(BaseModel):
    enabled: bool = True
    device: str = "/dev/ttyUSB0"
    baud_rate: int = 1_000_000
    timeout_seconds: float = 0.05
    scan_start_id: int = 1
    scan_end_id: int = 12


class WebConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080


class Esp32Config(BaseModel):
    enabled: bool = True
    listen_host: str = "0.0.0.0"
    listen_port: int = 8765
    command_timeout_ms: int = 250


class MotionConfig(BaseModel):
    loop_hz: int = 40
    default_speed: int = 1200
    default_acceleration: int = 30
    stand_speed: int = 700
    stand_acceleration: int = 10
    sit_speed: int = 500
    sit_acceleration: int = 8


class VisionConfig(BaseModel):
    enabled: bool = False
    track_target: str = "person"
    camera_indexes: list[int] = Field(default_factory=lambda: [0])


class ServoJointConfig(BaseModel):
    id: int
    direction: int = 1
    neutral_ticks: int = 2048
    min_ticks: int = 0
    max_ticks: int = 4095


class LegConfig(BaseModel):
    hip_x: ServoJointConfig
    knee_y: ServoJointConfig
    foot: ServoJointConfig


class LegsConfig(BaseModel):
    front_left: LegConfig
    front_right: LegConfig
    rear_left: LegConfig
    rear_right: LegConfig


class PoseJointConfig(BaseModel):
    hip_x: int
    knee_y: int
    foot: int


class StandPoseConfig(BaseModel):
    front_left: PoseJointConfig
    front_right: PoseJointConfig
    rear_left: PoseJointConfig
    rear_right: PoseJointConfig


class StandSequenceStep(BaseModel):
    servo_id: int
    wait_after_ms: int = 0


class AppConfig(BaseModel):
    robot: RobotMetadata = Field(default_factory=RobotMetadata)
    servo_bus: ServoBusConfig = Field(default_factory=ServoBusConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    esp32: Esp32Config = Field(default_factory=Esp32Config)
    motion: MotionConfig = Field(default_factory=MotionConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    legs: LegsConfig
    stand_pose: StandPoseConfig
    storage_pose: StandPoseConfig | None = None
    sit_pose: StandPoseConfig | None = None
    stand_to_sit_mid_pose: StandPoseConfig | None = None
    sit_to_stand_mid_pose: StandPoseConfig | None = None
    sit_to_stand_mid_test_2_pose: StandPoseConfig | None = None
    max_pose: StandPoseConfig | None = None
    stand_sequence: list[StandSequenceStep] = Field(default_factory=list)
    max_pose_guard_ticks: int = 150
    sit_to_stand_mid_pause_ms: int = 500

    def iter_joint_configs(self) -> Iterator[tuple[LegName, JointName, ServoJointConfig]]:
        for leg_name in LEG_NAMES:
            leg = getattr(self.legs, leg_name)
            for joint_name in JOINT_NAMES:
                yield leg_name, joint_name, getattr(leg, joint_name)

    def servo_ids(self) -> list[int]:
        return [joint.id for _, _, joint in self.iter_joint_configs()]

    def _selected_leg_names(self, leg_names: list[LegName] | tuple[LegName, ...] | None = None) -> tuple[LegName, ...]:
        if not leg_names:
            return LEG_NAMES
        return tuple(leg_names)

    def joint_config_for_servo(self, servo_id: int) -> ServoJointConfig | None:
        for _, _, joint in self.iter_joint_configs():
            if joint.id == servo_id:
                return joint
        return None

    def stand_commands(self, leg_names: list[LegName] | tuple[LegName, ...] | None = None) -> list[tuple[int, int]]:
        commands: list[tuple[int, int]] = []
        for leg_name in self._selected_leg_names(leg_names):
            leg = getattr(self.legs, leg_name)
            pose = getattr(self.stand_pose, leg_name)
            commands.extend(
                [
                    (leg.hip_x.id, pose.hip_x),
                    (leg.knee_y.id, pose.knee_y),
                    (leg.foot.id, pose.foot),
                ]
            )
        return commands

    def stand_position_for_servo(self, servo_id: int) -> int | None:
        for configured_servo_id, position in self.stand_commands():
            if configured_servo_id == servo_id:
                return position
        return None

    def storage_commands(self, leg_names: list[LegName] | tuple[LegName, ...] | None = None) -> list[tuple[int, int]]:
        if self.storage_pose is None:
            return []
        commands: list[tuple[int, int]] = []
        for leg_name in self._selected_leg_names(leg_names):
            leg = getattr(self.legs, leg_name)
            pose = getattr(self.storage_pose, leg_name)
            commands.extend(
                [
                    (leg.hip_x.id, pose.hip_x),
                    (leg.knee_y.id, pose.knee_y),
                    (leg.foot.id, pose.foot),
                ]
            )
        return commands

    def storage_position_for_servo(self, servo_id: int) -> int | None:
        for configured_servo_id, position in self.storage_commands():
            if configured_servo_id == servo_id:
                return position
        return None

    def sit_commands(self, leg_names: list[LegName] | tuple[LegName, ...] | None = None) -> list[tuple[int, int]]:
        if self.sit_pose is None:
            return []
        commands: list[tuple[int, int]] = []
        for leg_name in self._selected_leg_names(leg_names):
            leg = getattr(self.legs, leg_name)
            pose = getattr(self.sit_pose, leg_name)
            commands.extend(
                [
                    (leg.hip_x.id, pose.hip_x),
                    (leg.knee_y.id, pose.knee_y),
                    (leg.foot.id, pose.foot),
                ]
            )
        return commands

    def sit_position_for_servo(self, servo_id: int) -> int | None:
        for configured_servo_id, position in self.sit_commands():
            if configured_servo_id == servo_id:
                return position
        return None

    def stand_to_sit_mid_commands(
        self,
        leg_names: list[LegName] | tuple[LegName, ...] | None = None,
    ) -> list[tuple[int, int]]:
        if self.stand_to_sit_mid_pose is None:
            return []
        commands: list[tuple[int, int]] = []
        for leg_name in self._selected_leg_names(leg_names):
            leg = getattr(self.legs, leg_name)
            pose = getattr(self.stand_to_sit_mid_pose, leg_name)
            commands.extend(
                [
                    (leg.hip_x.id, pose.hip_x),
                    (leg.knee_y.id, pose.knee_y),
                    (leg.foot.id, pose.foot),
                ]
            )
        return commands

    def sit_to_stand_mid_commands(
        self,
        leg_names: list[LegName] | tuple[LegName, ...] | None = None,
    ) -> list[tuple[int, int]]:
        if self.sit_to_stand_mid_pose is None:
            return []
        commands: list[tuple[int, int]] = []
        for leg_name in self._selected_leg_names(leg_names):
            leg = getattr(self.legs, leg_name)
            pose = getattr(self.sit_to_stand_mid_pose, leg_name)
            commands.extend(
                [
                    (leg.hip_x.id, pose.hip_x),
                    (leg.knee_y.id, pose.knee_y),
                    (leg.foot.id, pose.foot),
                ]
            )
        return commands

    def sit_to_stand_mid_test_2_commands(
        self,
        leg_names: list[LegName] | tuple[LegName, ...] | None = None,
    ) -> list[tuple[int, int]]:
        if self.sit_to_stand_mid_test_2_pose is None:
            return []
        commands: list[tuple[int, int]] = []
        for leg_name in self._selected_leg_names(leg_names):
            leg = getattr(self.legs, leg_name)
            pose = getattr(self.sit_to_stand_mid_test_2_pose, leg_name)
            commands.extend(
                [
                    (leg.hip_x.id, pose.hip_x),
                    (leg.knee_y.id, pose.knee_y),
                    (leg.foot.id, pose.foot),
                ]
            )
        return commands

    def max_commands(self, leg_names: list[LegName] | tuple[LegName, ...] | None = None) -> list[tuple[int, int]]:
        if self.max_pose is None:
            return []
        commands: list[tuple[int, int]] = []
        for leg_name in self._selected_leg_names(leg_names):
            leg = getattr(self.legs, leg_name)
            pose = getattr(self.max_pose, leg_name)
            commands.extend(
                [
                    (leg.hip_x.id, pose.hip_x),
                    (leg.knee_y.id, pose.knee_y),
                    (leg.foot.id, pose.foot),
                ]
            )
        return commands

    def max_position_for_servo(self, servo_id: int) -> int | None:
        for configured_servo_id, position in self.max_commands():
            if configured_servo_id == servo_id:
                return position
        return None


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)
