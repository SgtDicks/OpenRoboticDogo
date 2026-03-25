from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doggo.config import AppConfig, LEG_NAMES, LegName
from doggo.control.gait import CrawlGaitPlanner
from doggo.hardware.sts3215 import ServoBusError, ServoScanResult, Sts3215Bus
from doggo.models import MotionFrame, MotionRecording, TeleopCommand


@dataclass(slots=True)
class CommandStamp:
    command: TeleopCommand
    received_at: float


class ControlSupervisor:
    _PLAYBACK_SPEED_MIN = 1
    _PLAYBACK_SPEED_MAX = 4095
    _DEFAULT_IDLE_THRESHOLD_TICKS = 15

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
        self.gait_planner = CrawlGaitPlanner(config)
        self._lock = asyncio.Lock()
        self._motion_lock = asyncio.Lock()
        self._running = False
        self.runtime_state_path = Path(runtime_state_path) if runtime_state_path else Path(".doggo_runtime_state.json")
        self.recording_path = self.runtime_state_path.with_suffix(".recording.json")
        self.recordings_dir = self.runtime_state_path.parent / "recordings"
        self._recording_stop_event: asyncio.Event | None = None
        self._recording_active = False
        self._last_command_pose = self._load_last_command_pose()
        self.last_recording = self._load_last_recording()

    def _monotonic(self) -> float:
        return time.monotonic()

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

    def _load_last_recording(self) -> MotionRecording | None:
        try:
            payload = json.loads(self.recording_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return MotionRecording.model_validate(payload)
        except Exception:
            return None

    def _write_last_recording(self) -> None:
        if self.last_recording is None:
            return
        try:
            self.recording_path.parent.mkdir(parents=True, exist_ok=True)
            self.recording_path.write_text(
                self.last_recording.model_dump_json(indent=2),
                encoding="utf-8",
            )
        except OSError:
            return

    def _sanitize_recording_name(self, name: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
        cleaned = cleaned.strip("._-")
        return cleaned or "clip"

    def _saved_recording_path(self, name: str) -> Path:
        sanitized = self._sanitize_recording_name(name)
        return self.recordings_dir / f"{sanitized}.json"

    def _load_recording_from_path(self, path: Path) -> MotionRecording | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return MotionRecording.model_validate(payload)
        except Exception:
            return None

    def list_saved_recordings(self) -> list[dict[str, Any]]:
        if not self.recordings_dir.exists():
            return []

        saved: list[dict[str, Any]] = []
        for path in sorted(self.recordings_dir.glob("*.json")):
            recording = self._load_recording_from_path(path)
            if recording is None:
                continue
            saved.append(
                {
                    "name": recording.name,
                    "captured_at": recording.captured_at,
                    "frame_count": recording.frame_count,
                    "duration_ms": recording.duration_ms,
                    "sample_ms": recording.sample_ms,
                    "stop_reason": recording.stop_reason,
                    "file": path.name,
                }
            )
        return saved

    def _load_saved_recording(self, name: str) -> MotionRecording:
        path = self._saved_recording_path(name)
        recording = self._load_recording_from_path(path)
        if recording is None:
            raise ServoBusError(f"No saved recording named {name!r} was found.")
        return recording

    def save_last_recording_as(self, name: str) -> MotionRecording:
        if self.last_recording is None:
            raise ServoBusError("No motion recording is available to save.")

        saved_recording = self.last_recording.model_copy(update={"name": name})
        path = self._saved_recording_path(name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(saved_recording.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            raise ServoBusError(f"Could not save recording {name!r}: {exc}") from exc
        return saved_recording

    def recording_snapshot(self) -> dict[str, Any]:
        if self.last_recording is None:
            return {
                "available": False,
                "active": self._recording_active,
                "name": None,
                "captured_at": None,
                "frame_count": 0,
                "duration_ms": 0,
                "sample_ms": 0,
                "stop_reason": None,
                "idle_stop_seconds": None,
                "idle_threshold_ticks": self._DEFAULT_IDLE_THRESHOLD_TICKS,
                "servo_ids": [],
                "saved_recordings": self.list_saved_recordings(),
            }
        return {
            "available": True,
            "active": self._recording_active,
            "name": self.last_recording.name,
            "captured_at": self.last_recording.captured_at,
            "frame_count": self.last_recording.frame_count,
            "duration_ms": self.last_recording.duration_ms,
            "sample_ms": self.last_recording.sample_ms,
            "stop_reason": self.last_recording.stop_reason,
            "idle_stop_seconds": self.last_recording.idle_stop_seconds,
            "idle_threshold_ticks": self.last_recording.idle_threshold_ticks,
            "servo_ids": list(self.last_recording.servo_ids),
            "saved_recordings": self.list_saved_recordings(),
        }

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

    def _normalize_recording_frames(self, frames: list[MotionFrame]) -> list[MotionFrame]:
        if not frames:
            return []
        origin_ms = frames[0].timestamp_ms
        normalized: list[MotionFrame] = []
        for frame in frames:
            normalized.append(
                MotionFrame(
                    timestamp_ms=max(0, frame.timestamp_ms - origin_ms),
                    positions=dict(frame.positions),
                )
            )
        return normalized

    def _playback_speed_for_frame(
        self,
        previous_positions: dict[int, int],
        next_positions: dict[int, int],
        delta_ms: int,
    ) -> int:
        if delta_ms <= 0:
            return self.config.motion.default_speed

        max_delta = 0
        for servo_id, next_position in next_positions.items():
            previous_position = previous_positions.get(servo_id)
            if previous_position is None:
                continue
            max_delta = max(max_delta, abs(next_position - previous_position))

        if max_delta <= 0:
            return self.config.motion.default_speed

        ticks_per_second = round((max_delta * 1000) / delta_ms)
        return max(self._PLAYBACK_SPEED_MIN, min(self._PLAYBACK_SPEED_MAX, ticks_per_second))

    def _has_major_change(
        self,
        previous_positions: dict[int, int],
        next_positions: dict[int, int],
        threshold_ticks: int,
    ) -> bool:
        for servo_id, next_position in next_positions.items():
            previous_position = previous_positions.get(servo_id)
            if previous_position is None:
                continue
            if abs(next_position - previous_position) >= threshold_ticks:
                return True
        return False

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

    async def _capture_current_positions(self) -> dict[int, int]:
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
        return positions

    async def _run_main_stand_sequence(
        self,
        *,
        speed: int,
        acceleration: int,
    ) -> None:
        if self._last_command_pose == "sit":
            prep_commands = self.config.sit_to_stand_front_prep_commands()
            if prep_commands:
                await self._run_sync_pose(
                    prep_commands,
                    speed=speed,
                    acceleration=acceleration,
                )
                if self.config.sit_to_stand_front_prep_pause_ms > 0:
                    await asyncio.sleep(self.config.sit_to_stand_front_prep_pause_ms / 1000.0)

        midpoint_commands = self.config.sit_to_stand_mid_test_2_commands()
        if not midpoint_commands:
            await self._run_sync_pose(
                self.config.stand_commands(),
                speed=speed,
                acceleration=acceleration,
            )
            return

        rear_midpoint_commands = self.config.sit_to_stand_mid_test_2_commands(["rear_left", "rear_right"])
        if rear_midpoint_commands:
            await self._run_sync_pose(
                rear_midpoint_commands,
                speed=speed,
                acceleration=acceleration,
            )
            await asyncio.sleep(1.0)

        await self._run_sync_pose(
            self.config.stand_commands(),
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
            "recording": self.recording_snapshot(),
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
                moving = self._teleop_axes_are_active(command)
                blocked_pose = self._teleop_pose_blocker()
                if moving:
                    if blocked_pose:
                        self.state = "teleop_blocked"
                        self.last_message = f"Walking teleop is blocked while Doggo is in {blocked_pose}. Move to stand first."
                    else:
                        gait_frame = self.gait_planner.accept(command)
                        if gait_frame.ready and self.servo_bus:
                            async with self._motion_lock:
                                await self._run_sync_pose(
                                    gait_frame.commands,
                                    speed=gait_frame.speed,
                                    acceleration=gait_frame.acceleration,
                                )
                            self.state = "walking"
                            self.last_message = gait_frame.reason
                            self._record_pose_command("walking")
                        else:
                            self.state = "teleop_requested"
                            self.last_message = gait_frame.reason
                elif self._last_command_pose == "walking" and self.servo_bus:
                    async with self._motion_lock:
                        await self._run_sync_pose(
                            self.config.stand_commands(),
                            speed=self.config.motion.stand_speed,
                            acceleration=self.config.motion.stand_acceleration,
                        )
                    self.state = "standing"
                    self.last_message = "Teleop inputs are neutral; Doggo settled back into stand."
                    self._record_pose_command("stand")
                elif self.state in {"teleop_requested", "teleop_blocked"}:
                    self.state = "idle"
                    self.last_message = "Teleop inputs are neutral."

        return self.status_snapshot()

    def _teleop_axes_are_active(self, command: TeleopCommand) -> bool:
        return any(
            abs(axis_value) > self.config.walking.command_deadzone
            for axis_value in (
                command.axes.forward,
                command.axes.strafe,
                command.axes.turn,
            )
        )

    def _teleop_pose_blocker(self) -> str | None:
        if self._last_command_pose in {"sit", "storage", "relaxed"}:
            return self._last_command_pose
        return None

    async def scan_servos(self, start_id: int | None = None, end_id: int | None = None) -> list[ServoScanResult]:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")
        start_id = start_id or self.config.servo_bus.scan_start_id
        end_id = end_id or self.config.servo_bus.scan_end_id
        async with self._motion_lock:
            results = await asyncio.to_thread(self.servo_bus.scan, start_id, end_id)
        self.last_scan = results
        self.state = "scanned"
        self.last_message = f"Found {len(results)} servo(s) on the bus."
        return results

    async def read_position(self, servo_id: int) -> int:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")
        async with self._motion_lock:
            position = await asyncio.to_thread(self.servo_bus.read_present_position, servo_id)
        self.last_message = f"Read position for servo {servo_id}: {position}"
        return position

    async def read_all_positions(self) -> dict[int, int]:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")
        async with self._motion_lock:
            positions = await self._capture_current_positions()
        self.last_message = f"Read positions for {len(positions)} reachable configured servos."
        return positions

    async def record_motion(
        self,
        *,
        name: str = "last_capture",
        duration_ms: int = 10_000,
        sample_ms: int = 100,
        idle_stop_seconds: float | None = None,
        idle_threshold_ticks: int = _DEFAULT_IDLE_THRESHOLD_TICKS,
    ) -> MotionRecording:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")
        if self._recording_active:
            raise ServoBusError("A motion recording is already in progress.")

        frames: list[MotionFrame] = []
        capture_started = self._monotonic()
        self._recording_stop_event = asyncio.Event()
        self._recording_active = True
        self.state = "recording"
        idle_note = ""
        if idle_stop_seconds and idle_stop_seconds > 0:
            idle_note = f" Auto-stop after {idle_stop_seconds:.1f}s without >= {idle_threshold_ticks} tick change."
        self.last_message = (
            f"Recording motion for {duration_ms}ms at {sample_ms}ms intervals.{idle_note}"
        )

        stop_reason = "unknown"
        try:
            async with self._motion_lock:
                last_positions = await self._capture_current_positions()
                if not last_positions:
                    raise ServoBusError("Could not read any servos to start recording.")

                last_major_change_time = self._monotonic()
                previous_positions = dict(last_positions)

                while True:
                    loop_started = self._monotonic()
                    observed = await self._capture_current_positions()
                    if observed:
                        if self._has_major_change(previous_positions, observed, idle_threshold_ticks):
                            last_major_change_time = self._monotonic()
                        previous_positions = dict(observed)
                        last_positions.update(observed)

                    elapsed_ms = int(round((self._monotonic() - capture_started) * 1000))
                    frames.append(
                        MotionFrame(
                            timestamp_ms=elapsed_ms,
                            positions=dict(sorted(last_positions.items())),
                        )
                    )

                    if elapsed_ms >= duration_ms:
                        stop_reason = "duration"
                        break
                    if self._recording_stop_event and self._recording_stop_event.is_set():
                        stop_reason = "manual"
                        break
                    if idle_stop_seconds and idle_stop_seconds > 0:
                        idle_elapsed = self._monotonic() - last_major_change_time
                        if idle_elapsed >= idle_stop_seconds:
                            stop_reason = "idle"
                            break

                    sleep_seconds = max(0.0, (sample_ms / 1000.0) - (self._monotonic() - loop_started))
                    if sleep_seconds > 0:
                        await asyncio.sleep(sleep_seconds)
        finally:
            self._recording_active = False
            self._recording_stop_event = None

        normalized_frames = self._normalize_recording_frames(frames)
        actual_duration_ms = normalized_frames[-1].timestamp_ms if normalized_frames else 0
        self.last_recording = MotionRecording(
            name=name,
            duration_ms=actual_duration_ms,
            sample_ms=sample_ms,
            stop_reason=stop_reason if stop_reason in {"duration", "manual", "idle"} else "unknown",
            idle_stop_seconds=idle_stop_seconds,
            idle_threshold_ticks=idle_threshold_ticks,
            servo_ids=self.config.servo_ids(),
            frames=normalized_frames,
        )
        self._write_last_recording()
        self.state = "recorded"
        self.last_message = (
            f"Recorded {self.last_recording.frame_count} frame(s) "
            f"over {self.last_recording.duration_ms}ms. Stop reason: {self.last_recording.stop_reason}."
        )
        self._record_pose_command("recorded")
        return self.last_recording

    async def stop_recording(self) -> dict[str, Any]:
        if not self._recording_active or self._recording_stop_event is None:
            self.last_message = "No motion recording is currently running."
            return self.recording_snapshot()

        self._recording_stop_event.set()
        self.last_message = "Stop requested for the active motion recording."
        return self.recording_snapshot()

    def save_recording(self, name: str) -> MotionRecording:
        saved = self.save_last_recording_as(name)
        self.last_message = f"Saved recording as {saved.name}."
        return saved

    async def playback_recording(
        self,
        *,
        name: str | None = None,
        speed: int | None = None,
        acceleration: int | None = None,
    ) -> MotionRecording:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")
        recording = self._load_saved_recording(name) if name else self.last_recording
        if recording is None:
            raise ServoBusError("No motion recording is available yet.")
        if not recording.frames:
            raise ServoBusError("The saved motion recording has no frames.")

        move_speed = speed or self.config.motion.default_speed
        move_acc = acceleration or self.config.motion.default_acceleration
        self.state = "playback"
        self.last_message = f"Playing back recording {recording.name}."

        async with self._motion_lock:
            current_positions = await self._capture_current_positions()
            previous_positions = current_positions if current_positions else dict(recording.frames[0].positions)
            for index, frame in enumerate(recording.frames):
                commands = sorted(frame.positions.items())
                frame_speed = (
                    speed
                    if speed is not None
                    else self._playback_speed_for_frame(
                        previous_positions,
                        frame.positions,
                        frame.timestamp_ms if index == 0 else frame.timestamp_ms - recording.frames[index - 1].timestamp_ms,
                    )
                )
                await self._run_sync_pose(
                    commands,
                    speed=frame_speed if speed is None else move_speed,
                    acceleration=move_acc,
                )
                previous_positions = dict(frame.positions)
                if index + 1 >= len(recording.frames):
                    continue
                next_frame = recording.frames[index + 1]
                delay_ms = max(0, next_frame.timestamp_ms - frame.timestamp_ms)
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0)

        self.state = "playback_complete"
        self.last_message = (
            f"Played back {recording.frame_count} frame(s) "
            f"from {recording.name}."
        )
        self._record_pose_command("playback")
        return recording

    async def assign_servo_id(self, current_id: int, new_id: int) -> None:
        if not self.servo_bus:
            raise ServoBusError("Servo bus is not configured.")
        async with self._motion_lock:
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
        async with self._motion_lock:
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
        async with self._motion_lock:
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

        async with self._motion_lock:
            if leg_names is None:
                await self._run_main_stand_sequence(
                    speed=move_speed,
                    acceleration=move_acc,
                )
            else:
                commands = self.config.stand_commands(leg_names)
                target_servo_ids = {servo_id for servo_id, _ in commands}
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
        move_speed = speed or self.config.motion.stand_speed
        move_acc = acceleration or self.config.motion.stand_acceleration
        async with self._motion_lock:
            await self._run_main_stand_sequence(
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
        async with self._motion_lock:
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

        async with self._motion_lock:
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
        async with self._motion_lock:
            for servo_id in self.config.servo_ids():
                try:
                    await asyncio.to_thread(self.servo_bus.set_torque_enabled, servo_id, False)
                    relaxed_ids.append(servo_id)
                except ServoBusError:
                    continue
        self.state = "relaxed"
        self._record_pose_command("relaxed")
        self.last_message = f"Torque disabled on reachable servos: {relaxed_ids}."
