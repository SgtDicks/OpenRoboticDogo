from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doggo.config import AppConfig, LEG_NAMES, LegName
from doggo.control.gait import PlaceholderGaitPlanner
from doggo.hardware.sts3215 import ServoBusError, ServoScanResult, Sts3215Bus
from doggo.models import TeleopCommand


@dataclass(slots=True)
class CommandStamp:
    command: TeleopCommand
    received_at: float


class ControlSupervisor:
    def __init__(
        self,
        config: AppConfig,
        servo_bus: Sts3215Bus | None = None,
        *,
        runtime_state_path: str | Path | None = None,
    ) -> None:
        self.config = config
        self.servo_bus = servo_bus
        self.state = "idle"
        self.last_message = "Supervisor initialized."
        self.last_scan: list[ServoScanResult] = []
        self.last_teleop: dict[str, CommandStamp] = {}
        self.gait_planner = PlaceholderGaitPlanner()
        self._lock = asyncio.Lock()
        self._running = False
        self.runtime_state_path = Path(runtime_state_path) if runtime_state_path else Path(".doggo_runtime_state.json")
        self._last_command_pose = self._load_last_command_pose()

    def _load_last_command_pose(self) -> str | None:
        try:
            payload = json.loads(self.runtime_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        pose_name = payload.get("last_command_pose")
        return pose_name if isinstance(pose_name, str) else None

    def _write_runtime_state(self) -> None:
        payload = {"last_command_pose": self._last_command_pose}
        try:
            self.runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
            self.runtime_state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            return

    def _record_pose_command(self, pose_name: str) -> None:
        self._last_command_pose = pose_name
        self._write_runtime_state()

    async def _is_near_pose(
        self,
        commands: list[tuple[int, int]],
        *,
        tolerance_ticks: int = 150,
    ) -> bool:
        if not self.servo_bus or not commands:
            return False

        for servo_id, target in commands:
            current = await self._read_position_if_available(servo_id)
            if current is None or abs(current - target) > tolerance_ticks:
                return False

        return True

    async def _read_position_if_available(self, servo_id: int) -> int | None:
        if not self.servo_bus:
            return None
        try:
            return await asyncio.to_thread(self.servo_bus.read_present_position, servo_id)
        except ServoBusError:
            return None

    def _command_map(self, commands: list[tuple[int, int]]) -> dict[int, int]:
        return {servo_id: position for servo_id, position in commands}

    def _move_index(self, commands: list[tuple[int, int]], servo_id: int) -> int | None:
        for index, (configured_servo_id, _) in enumerate(commands):
            if configured_servo_id == servo_id:
                return index
        return None

    def _order_by_sequence(
        self,
        commands: list[tuple[int, int]],
        servo_sequence: list[int] | None,
    ) -> list[tuple[int, int]]:
        if not servo_sequence:
            return commands

        command_map = self._command_map(commands)
        ordered: list[tuple[int, int]] = []
        seen: set[int] = set()

        for servo_id in servo_sequence:
            if servo_id in command_map and servo_id not in seen:
                ordered.append((servo_id, command_map[servo_id]))
                seen.add(servo_id)

        for servo_id, target in commands:
            if servo_id not in seen:
                ordered.append((servo_id, target))

        return ordered

    async def _reorder_for_max_guard(self, commands: list[tuple[int, int]]) -> list[tuple[int, int]]:
        guarded_servo_ids = (1, 2)
        guard_window = self.config.max_pose_guard_ticks
        command_map = self._command_map(commands)

        near_max = False
        for servo_id in guarded_servo_ids:
            max_position = self.config.max_position_for_servo(servo_id)
            if max_position is None:
                continue

            current_position = await self._read_position_if_available(servo_id)
            target_position = command_map.get(servo_id)

            if current_position is not None and abs(current_position - max_position) <= guard_window:
                near_max = True
            if target_position is not None and abs(target_position - max_position) <= guard_window:
                near_max = True

        if not near_max:
            return commands

        servo2_index = self._move_index(commands, 2)
        servo1_index = self._move_index(commands, 1)
        if servo2_index is None or servo1_index is None or servo2_index < servo1_index:
            return commands

        reordered = list(commands)
        servo2_command = reordered.pop(servo2_index)
        servo1_index = self._move_index(reordered, 1)
        insert_at = servo1_index if servo1_index is not None else len(reordered)
        reordered.insert(insert_at, servo2_command)
        return reordered

    def _sequence_and_waits_for_servo_ids(
        self,
        target_servo_ids: set[int],
        *,
        reverse: bool = False,
    ) -> tuple[list[int], dict[int, int]]:
        steps = list(reversed(self.config.stand_sequence)) if reverse else list(self.config.stand_sequence)
        wait_map = {
            step.servo_id: step.wait_after_ms
            for step in steps
            if step.servo_id in target_servo_ids
        }
        servo_sequence = [step.servo_id for step in steps if step.servo_id in target_servo_ids]
        return servo_sequence, wait_map

    async def _run_ordered_pose(
        self,
        commands: list[tuple[int, int]],
        *,
        speed: int,
        acceleration: int,
        wait_map_ms: dict[int, int] | None = None,
        servo_sequence: list[int] | None = None,
    ) -> None:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")
        ordered_commands = self._order_by_sequence(commands, servo_sequence)
        ordered_commands = await self._reorder_for_max_guard(ordered_commands)
        wait_map_ms = wait_map_ms or {}

        for servo_id, target in ordered_commands:
            try:
                await asyncio.to_thread(self.servo_bus.set_torque_enabled, servo_id, True)
                await asyncio.to_thread(
                    self.servo_bus.move,
                    servo_id,
                    target,
                    speed,
                    acceleration,
                )
                wait_after_ms = wait_map_ms.get(servo_id, 0)
                if wait_after_ms > 0:
                    await asyncio.sleep(wait_after_ms / 1000.0)
            except ServoBusError:
                continue

    async def _run_sync_pose(
        self,
        commands: list[tuple[int, int]],
        *,
        speed: int,
        acceleration: int,
    ) -> None:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")

        for servo_id, _ in commands:
            try:
                await asyncio.to_thread(self.servo_bus.set_torque_enabled, servo_id, True)
            except ServoBusError:
                continue

        await asyncio.to_thread(
            self.servo_bus.sync_move,
            commands,
            speed=speed,
            acceleration=acceleration,
        )

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def status_snapshot(self) -> dict[str, Any]:
        active_sources = [
            source
            for source, stamp in self.last_teleop.items()
            if (time.monotonic() - stamp.received_at) * 1000 <= self.config.esp32.command_timeout_ms
        ]
        gait_status = self.gait_planner.status()
        return {
            "robot": self.config.robot.name,
            "state": self.state,
            "last_message": self.last_message,
            "servo_bus": {
                "enabled": self.config.servo_bus.enabled,
                "device": self.config.servo_bus.device,
                "connected": bool(self.servo_bus and self.servo_bus.is_open),
            },
            "vision": {
                "enabled": self.config.vision.enabled,
                "target": self.config.vision.track_target,
                "camera_indexes": self.config.vision.camera_indexes,
            },
            "gait": {"ready": gait_status.ready, "reason": gait_status.reason},
            "active_sources": active_sources,
            "last_scan": [result.to_dict() for result in self.last_scan],
        }

    async def apply_teleop(self, command: TeleopCommand) -> dict[str, Any]:
        async with self._lock:
            self.last_teleop[command.source] = CommandStamp(
                command=command,
                received_at=time.monotonic(),
            )

            if command.mode == "stop" or command.buttons.stop:
                await self.relax()
            elif command.mode == "stand" or command.buttons.stand:
                await self.stand()
            elif command.mode == "relax" or command.buttons.relax:
                await self.relax()
            else:
                moving = any(
                    abs(axis_value) > 0.05
                    for axis_value in (
                        command.axes.forward,
                        command.axes.strafe,
                        command.axes.turn,
                    )
                )
                if moving:
                    self.state = "teleop_requested"
                    gait_status = self.gait_planner.accept(command)
                    self.last_message = gait_status.reason
                elif self.state == "teleop_requested":
                    self.state = "idle"
                    self.last_message = "Teleop inputs are neutral."

        return self.status_snapshot()

    async def scan_servos(self, start_id: int | None = None, end_id: int | None = None) -> list[ServoScanResult]:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")
        start_id = start_id or self.config.servo_bus.scan_start_id
        end_id = end_id or self.config.servo_bus.scan_end_id
        results = await asyncio.to_thread(self.servo_bus.scan, start_id, end_id)
        self.last_scan = results
        self.state = "scanned"
        self.last_message = f"Found {len(results)} servo(s) on the bus."
        return results

    async def read_position(self, servo_id: int) -> int:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")
        position = await asyncio.to_thread(self.servo_bus.read_present_position, servo_id)
        self.last_message = f"Read position for servo {servo_id}: {position}"
        return position

    async def read_all_positions(self) -> dict[int, int]:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")
        positions: dict[int, int] = {}
        for servo_id in self.config.servo_ids():
            try:
                positions[servo_id] = await asyncio.to_thread(
                    self.servo_bus.read_present_position,
                    servo_id,
                )
            except ServoBusError:
                continue
        self.last_message = f"Read positions for {len(positions)} reachable configured servos."
        return positions

    async def assign_servo_id(self, current_id: int, new_id: int) -> None:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")
        await asyncio.to_thread(self.servo_bus.set_servo_id, current_id, new_id)
        self.state = "configured"
        self.last_message = f"Assigned servo ID {current_id} -> {new_id}."
        self._record_pose_command("manual")

    async def move_servo(
        self,
        servo_id: int,
        position: int,
        speed: int | None = None,
        acceleration: int | None = None,
    ) -> None:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")
        move_speed = speed or self.config.motion.default_speed
        move_acc = acceleration or self.config.motion.default_acceleration
        await asyncio.to_thread(self.servo_bus.set_torque_enabled, servo_id, True)
        await asyncio.to_thread(self.servo_bus.move, servo_id, position, move_speed, move_acc)
        self.state = "calibrating"
        self.last_message = f"Moved servo {servo_id} to {position} ticks."
        self._record_pose_command("manual")

    async def step_test_servo(
        self,
        servo_id: int,
        *,
        delta: int = 60,
        steps: int = 10,
        hold_ms: int = 250,
        speed: int | None = None,
        acceleration: int | None = None,
    ) -> dict[str, Any]:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")
        if steps < 2:
            raise ValueError("Step test needs at least 2 steps.")
        if delta < 1:
            raise ValueError("Step test delta must be positive.")

        move_speed = speed or self.config.motion.default_speed
        move_acc = acceleration or self.config.motion.default_acceleration
        current = await asyncio.to_thread(self.servo_bus.read_present_position, servo_id)
        joint = self.config.joint_config_for_servo(servo_id)

        if joint is not None and not (joint.min_ticks <= current <= joint.max_ticks):
            raise ServoBusError(
                f"Servo {servo_id} current position {current} is outside configured range "
                f"{joint.min_ticks}..{joint.max_ticks}. Update calibration before step testing."
            )

        low_target = current - delta
        high_target = current + delta
        if joint is not None:
            low_target = max(joint.min_ticks, low_target)
            high_target = min(joint.max_ticks, high_target)

        positions = [
            round(low_target + ((high_target - low_target) * index / (steps - 1)))
            for index in range(steps)
        ]
        sequence = positions + positions[-2::-1]

        self.state = "calibrating"
        for target in sequence:
            await asyncio.to_thread(self.servo_bus.move, servo_id, target, move_speed, move_acc)
            await asyncio.sleep(hold_ms / 1000.0)

        await asyncio.to_thread(self.servo_bus.move, servo_id, current, move_speed, move_acc)
        await asyncio.sleep(hold_ms / 1000.0)

        summary = {
            "servo_id": servo_id,
            "start": current,
            "low_target": low_target,
            "high_target": high_target,
            "steps": steps,
            "hold_ms": hold_ms,
            "positions": positions,
        }
        self.last_message = f"Completed step test for servo {servo_id}: {json.dumps(summary)}"
        self._record_pose_command("manual")
        return summary

    async def stand(
        self,
        *,
        leg_names: list[LegName] | None = None,
        speed: int | None = None,
        acceleration: int | None = None,
    ) -> None:
        if not self.servo_bus:
            self.state = "standing"
            self.last_message = "Stand requested, but servo bus is not configured."
            return
        move_speed = speed or self.config.motion.stand_speed
        move_acc = acceleration or self.config.motion.stand_acceleration
        commands = self.config.stand_commands(leg_names)

        # Leaving sit is the one transition where the whole body should lift together.
        use_sync_stand = leg_names is None and (
            self._last_command_pose == "sit" or await self._is_near_pose(self.config.sit_commands())
        )

        if use_sync_stand:
            midpoint_commands = self.config.sit_to_stand_mid_commands()
            if midpoint_commands:
                await self._run_sync_pose(
                    midpoint_commands,
                    speed=move_speed,
                    acceleration=move_acc,
                )
                await asyncio.sleep(self.config.sit_to_stand_mid_pause_ms / 1000.0)
            await self._run_sync_pose(
                commands,
                speed=move_speed,
                acceleration=move_acc,
            )
        else:
            target_servo_ids = {
                servo_id for servo_id, _ in commands
            }
            servo_sequence, wait_map = self._sequence_and_waits_for_servo_ids(target_servo_ids)
            await self._run_ordered_pose(
                commands,
                speed=move_speed,
                acceleration=move_acc,
                wait_map_ms=wait_map,
                servo_sequence=servo_sequence,
            )
        self.state = "standing"
        if leg_names is None:
            self._record_pose_command("stand")
        if leg_names:
            legs_text = ", ".join(leg_names)
            self.last_message = f"Moved {legs_text} into the configured stand pose."
        else:
            self.last_message = "Moved Doggo into the configured stand pose."

    async def stand_test_2(
        self,
        *,
        speed: int | None = None,
        acceleration: int | None = None,
    ) -> None:
        if not self.servo_bus:
            self.state = "standing"
            self.last_message = "Stand test 2 requested, but servo bus is not configured."
            return
        midpoint_commands = self.config.sit_to_stand_mid_test_2_commands()
        if not midpoint_commands:
            raise ServoBusError("Stand test 2 midpoint pose is not configured.")

        move_speed = speed or self.config.motion.stand_speed
        move_acc = acceleration or self.config.motion.stand_acceleration
        rear_midpoint_commands = self.config.sit_to_stand_mid_test_2_commands(["rear_left", "rear_right"])
        await self._run_sync_pose(
            rear_midpoint_commands,
            speed=move_speed,
            acceleration=move_acc,
        )
        await asyncio.sleep(1.0)
        await self._run_sync_pose(
            self.config.stand_commands(),
            speed=move_speed,
            acceleration=move_acc,
        )
        self.state = "standing"
        self._record_pose_command("stand")
        self.last_message = "Moved Doggo into the configured stand pose via stand test 2."

    async def storage(
        self,
        *,
        leg_names: list[LegName] | None = None,
        speed: int | None = None,
        acceleration: int | None = None,
    ) -> None:
        if not self.servo_bus:
            self.state = "storage"
            self.last_message = "Storage requested, but servo bus is not configured."
            return
        move_speed = speed or self.config.motion.default_speed
        move_acc = acceleration or self.config.motion.default_acceleration
        if self.config.storage_pose is None:
            raise ServoBusError("Storage pose is not configured.")
        target_servo_ids = {
            servo_id for servo_id, _ in self.config.storage_commands(leg_names)
        }

        # Reverse the stand sequence when returning from stand to a folded pose.
        servo_sequence, wait_map = self._sequence_and_waits_for_servo_ids(
            target_servo_ids,
            reverse=True,
        )
        await self._run_ordered_pose(
            self.config.storage_commands(leg_names),
            speed=move_speed,
            acceleration=move_acc,
            wait_map_ms=wait_map,
            servo_sequence=servo_sequence,
        )
        self.state = "storage"
        if leg_names is None:
            self._record_pose_command("storage")
        if leg_names:
            legs_text = ", ".join(leg_names)
            self.last_message = f"Moved {legs_text} into the configured storage pose."
        else:
            self.last_message = "Moved Doggo into the configured storage pose."

    async def sit(
        self,
        *,
        leg_names: list[LegName] | None = None,
        speed: int | None = None,
        acceleration: int | None = None,
    ) -> None:
        if not self.servo_bus:
            self.state = "sitting"
            self.last_message = "Sit requested, but servo bus is not configured."
            return

        if self.config.sit_pose is None:
            raise ServoBusError("Sit pose is not configured.")

        move_speed = speed or self.config.motion.sit_speed
        move_acc = acceleration or self.config.motion.sit_acceleration
        use_midpoint_sit = leg_names is None and self._last_command_pose == "stand"

        if use_midpoint_sit:
            midpoint_commands = self.config.stand_to_sit_mid_commands()
            if midpoint_commands:
                await self._run_sync_pose(
                    midpoint_commands,
                    speed=move_speed,
                    acceleration=move_acc,
                )
                await asyncio.sleep(self.config.sit_to_stand_mid_pause_ms / 1000.0)

        requested_legs = tuple(leg_names) if leg_names else LEG_NAMES
        rear_legs = [leg_name for leg_name in requested_legs if leg_name.startswith("rear_")]
        front_legs = [leg_name for leg_name in requested_legs if leg_name.startswith("front_")]

        if rear_legs:
            rear_commands = self.config.sit_commands(rear_legs)
            if len(rear_legs) > 1:
                await self._run_sync_pose(
                    rear_commands,
                    speed=move_speed,
                    acceleration=move_acc,
                )
            else:
                rear_servo_ids = {servo_id for servo_id, _ in rear_commands}
                rear_sequence, rear_wait_map = self._sequence_and_waits_for_servo_ids(rear_servo_ids)
                await self._run_ordered_pose(
                    rear_commands,
                    speed=move_speed,
                    acceleration=move_acc,
                    wait_map_ms=rear_wait_map,
                    servo_sequence=rear_sequence,
                )

        if rear_legs and front_legs:
            await asyncio.sleep(0.5)

        if front_legs:
            front_commands = self.config.sit_commands(front_legs)
            front_servo_ids = {servo_id for servo_id, _ in front_commands}
            front_sequence, front_wait_map = self._sequence_and_waits_for_servo_ids(front_servo_ids)
            await self._run_ordered_pose(
                front_commands,
                speed=move_speed,
                acceleration=move_acc,
                wait_map_ms=front_wait_map,
                servo_sequence=front_sequence,
            )

        self.state = "sitting"
        if leg_names is None:
            self._record_pose_command("sit")
        if leg_names:
            legs_text = ", ".join(leg_names)
            self.last_message = f"Moved {legs_text} into the configured sit pose."
        else:
            self.last_message = "Moved Doggo into the configured sit pose."

    async def sss(
        self,
        *,
        leg_names: list[LegName] | None = None,
        speed: int | None = None,
        acceleration: int | None = None,
    ) -> None:
        await self.stand(leg_names=leg_names, speed=speed, acceleration=acceleration)
        await self.storage(leg_names=leg_names, speed=speed, acceleration=acceleration)
        await self.stand(leg_names=leg_names, speed=speed, acceleration=acceleration)
        if leg_names:
            legs_text = ", ".join(leg_names)
            self.last_message = f"Completed stand -> storage -> stand for {legs_text}."
        else:
            self.last_message = "Completed stand -> storage -> stand for Doggo."

    async def relax(self) -> None:
        if not self.servo_bus:
            self.state = "relaxed"
            self.last_message = "Relax requested, but servo bus is not configured."
            return
        relaxed_ids: list[int] = []
        for servo_id in self.config.servo_ids():
            try:
                await asyncio.to_thread(self.servo_bus.set_torque_enabled, servo_id, False)
                relaxed_ids.append(servo_id)
            except ServoBusError:
                continue
        self.state = "relaxed"
        self._record_pose_command("relaxed")
        self.last_message = f"Torque disabled on reachable servos: {relaxed_ids}."
