import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

from doggo.config import StandSequenceStep, load_config
from doggo.control.supervisor import ControlSupervisor
from doggo.models import MotionRecording, TeleopAxes, TeleopCommand


def test_order_by_sequence_prioritizes_requested_servos() -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    supervisor = ControlSupervisor(config, servo_bus=None)

    commands = [(4, 10), (1, 20), (2, 30), (3, 40)]
    ordered = supervisor._order_by_sequence(commands, [2, 3, 1])

    assert ordered == [(2, 30), (3, 40), (1, 20), (4, 10)]


def test_max_guard_moves_servo_2_before_1_when_near_max() -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    supervisor = ControlSupervisor(config, servo_bus=None)

    async def fake_read_position(servo_id: int) -> int | None:
        if servo_id == 1:
            return 2100
        if servo_id == 2:
            return 2055
        return None

    supervisor._read_position_if_available = fake_read_position  # type: ignore[method-assign]
    commands = [(1, 2200), (2, 2000), (3, 2048)]

    reordered = asyncio.run(supervisor._reorder_for_max_guard(commands))

    assert reordered == [(2, 2000), (1, 2200), (3, 2048)]


def test_stand_filters_sequence_to_requested_leg() -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    config.stand_sequence = [
        StandSequenceStep(servo_id=2, wait_after_ms=600),
        StandSequenceStep(servo_id=3, wait_after_ms=600),
        StandSequenceStep(servo_id=1, wait_after_ms=0),
        StandSequenceStep(servo_id=5, wait_after_ms=200),
    ]
    supervisor = ControlSupervisor(config, servo_bus=None)
    captured: dict[str, object] = {}

    async def fake_run_ordered_pose(
        commands: list[tuple[int, int]],
        *,
        speed: int,
        acceleration: int,
        wait_map_ms: dict[int, int] | None = None,
        servo_sequence: list[int] | None = None,
    ) -> None:
        captured["commands"] = commands
        captured["speed"] = speed
        captured["acceleration"] = acceleration
        captured["wait_map_ms"] = wait_map_ms
        captured["servo_sequence"] = servo_sequence

    supervisor.servo_bus = object()  # type: ignore[assignment]
    supervisor._run_ordered_pose = fake_run_ordered_pose  # type: ignore[method-assign]

    asyncio.run(supervisor.stand(leg_names=["front_left"]))

    assert captured["commands"] == [(1, 2048), (2, 2048), (3, 2048)]
    assert captured["wait_map_ms"] == {2: 600, 3: 600, 1: 0}
    assert captured["servo_sequence"] == [2, 3, 1]


def test_sit_moves_rear_legs_first_then_front_legs() -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    config.stand_sequence = [
        StandSequenceStep(servo_id=2, wait_after_ms=600),
        StandSequenceStep(servo_id=3, wait_after_ms=600),
        StandSequenceStep(servo_id=1, wait_after_ms=0),
        StandSequenceStep(servo_id=4, wait_after_ms=200),
        StandSequenceStep(servo_id=6, wait_after_ms=200),
        StandSequenceStep(servo_id=5, wait_after_ms=0),
    ]
    supervisor = ControlSupervisor(config, servo_bus=object())  # type: ignore[arg-type]
    ordered_calls: list[dict[str, object]] = []
    sync_calls: list[dict[str, object]] = []

    async def fake_run_ordered_pose(
        commands: list[tuple[int, int]],
        *,
        speed: int,
        acceleration: int,
        wait_map_ms: dict[int, int] | None = None,
        servo_sequence: list[int] | None = None,
    ) -> None:
        ordered_calls.append(
            {
                "commands": commands,
                "speed": speed,
                "acceleration": acceleration,
                "wait_map_ms": wait_map_ms,
                "servo_sequence": servo_sequence,
            }
        )

    async def fake_run_sync_pose(
        commands: list[tuple[int, int]],
        *,
        speed: int,
        acceleration: int,
    ) -> None:
        sync_calls.append(
            {
                "commands": commands,
                "speed": speed,
                "acceleration": acceleration,
            }
        )

    supervisor._run_ordered_pose = fake_run_ordered_pose  # type: ignore[method-assign]
    supervisor._run_sync_pose = fake_run_sync_pose  # type: ignore[method-assign]

    sleep_mock = AsyncMock()
    with patch("doggo.control.supervisor.asyncio.sleep", sleep_mock):
        asyncio.run(supervisor.sit())

    assert sync_calls == [
        {
            "commands": [(7, 2048), (8, 2048), (9, 2048), (10, 2048), (11, 2048), (12, 2048)],
            "speed": 500,
            "acceleration": 8,
        }
    ]
    assert ordered_calls == [
        {
            "commands": [(1, 2048), (2, 2048), (3, 2048), (4, 2048), (5, 2048), (6, 2048)],
            "speed": 500,
            "acceleration": 8,
            "wait_map_ms": {2: 600, 3: 600, 1: 0, 4: 200, 6: 200, 5: 0},
            "servo_sequence": [2, 3, 1, 4, 6, 5],
        },
    ]
    sleep_mock.assert_awaited_once_with(0.5)


def test_full_body_stand_uses_test_2_midpoint_before_stand(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    midpoint_value = 1777
    for leg_name in ("front_left", "front_right", "rear_left", "rear_right"):
        midpoint_pose = getattr(config.sit_to_stand_mid_test_2_pose, leg_name)  # type: ignore[arg-type]
        midpoint_pose.hip_x = midpoint_value
        midpoint_pose.knee_y = midpoint_value
        midpoint_pose.foot = midpoint_value

    class FakeBus:
        def __init__(self) -> None:
            self.torque_calls: list[tuple[int, bool]] = []
            self.sync_calls: list[dict[str, object]] = []

        def read_present_position(self, servo_id: int) -> int:
            return 0

        def set_torque_enabled(self, servo_id: int, enabled: bool) -> None:
            self.torque_calls.append((servo_id, enabled))

        def sync_move(
            self,
            commands: list[tuple[int, int]],
            *,
            speed: int,
            acceleration: int,
        ) -> None:
            self.sync_calls.append(
                {
                    "commands": list(commands),
                    "speed": speed,
                    "acceleration": acceleration,
                }
            )

    runtime_state_path = tmp_path / "doggo.state.json"
    fake_bus = FakeBus()
    supervisor = ControlSupervisor(
        config,
        servo_bus=fake_bus,  # type: ignore[arg-type]
        runtime_state_path=runtime_state_path,
    )
    supervisor._record_pose_command("stand")

    async def fail_run_ordered_pose(*args, **kwargs) -> None:
        raise AssertionError("full-body stand should use the main sync stand sequence, not ordered sequencing")

    supervisor._run_ordered_pose = fail_run_ordered_pose  # type: ignore[method-assign]
    sleep_mock = AsyncMock()
    with patch("doggo.control.supervisor.asyncio.sleep", sleep_mock):
        asyncio.run(supervisor.stand())

    assert fake_bus.torque_calls == (
        [(servo_id, True) for servo_id in range(7, 13)]
        + [(servo_id, True) for servo_id in range(1, 13)]
    )
    assert fake_bus.sync_calls == [
        {
            "commands": [(servo_id, midpoint_value) for servo_id in range(7, 13)],
            "speed": 700,
            "acceleration": 10,
        },
        {
            "commands": [(servo_id, 2048) for servo_id in range(1, 13)],
            "speed": 700,
            "acceleration": 10,
        }
    ]
    sleep_mock.assert_awaited_once_with(1.0)


def test_stand_from_sit_moves_front_knees_forward_before_main_sequence(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    config.sit_to_stand_front_prep_pause_ms = 500
    midpoint_value = 1777
    for leg_name in ("front_left", "front_right", "rear_left", "rear_right"):
        midpoint_pose = getattr(config.sit_to_stand_mid_test_2_pose, leg_name)  # type: ignore[arg-type]
        midpoint_pose.hip_x = midpoint_value
        midpoint_pose.knee_y = midpoint_value
        midpoint_pose.foot = midpoint_value

    class FakeBus:
        def __init__(self) -> None:
            self.torque_calls: list[tuple[int, bool]] = []
            self.sync_calls: list[dict[str, object]] = []

        def set_torque_enabled(self, servo_id: int, enabled: bool) -> None:
            self.torque_calls.append((servo_id, enabled))

        def sync_move(
            self,
            commands: list[tuple[int, int]],
            *,
            speed: int,
            acceleration: int,
        ) -> None:
            self.sync_calls.append(
                {
                    "commands": list(commands),
                    "speed": speed,
                    "acceleration": acceleration,
                }
            )

    runtime_state_path = tmp_path / "doggo.state.json"
    stateful_supervisor = ControlSupervisor(
        config,
        servo_bus=None,
        runtime_state_path=runtime_state_path,
    )
    stateful_supervisor._record_pose_command("sit")

    fake_bus = FakeBus()
    supervisor = ControlSupervisor(
        config,
        servo_bus=fake_bus,  # type: ignore[arg-type]
        runtime_state_path=runtime_state_path,
    )

    sleep_mock = AsyncMock()
    with patch("doggo.control.supervisor.asyncio.sleep", sleep_mock):
        asyncio.run(supervisor.stand())

    assert fake_bus.sync_calls == [
        {
            "commands": [
                (1, 2048),
                (2, 2148),
                (3, 2048),
                (4, 2048),
                (5, 2148),
                (6, 2048),
                (7, 2048),
                (8, 2048),
                (9, 2048),
                (10, 2048),
                (11, 2048),
                (12, 2048),
            ],
            "speed": 700,
            "acceleration": 10,
        },
        {
            "commands": [(servo_id, midpoint_value) for servo_id in range(7, 13)],
            "speed": 700,
            "acceleration": 10,
        },
        {
            "commands": [(servo_id, 2048) for servo_id in range(1, 13)],
            "speed": 700,
            "acceleration": 10,
        },
    ]
    assert sleep_mock.await_args_list == [call(0.5), call(1.0)]


def test_sit_from_stand_uses_midpoint_before_sit_transition(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    config.stand_sequence = [
        StandSequenceStep(servo_id=2, wait_after_ms=600),
        StandSequenceStep(servo_id=3, wait_after_ms=600),
        StandSequenceStep(servo_id=1, wait_after_ms=0),
        StandSequenceStep(servo_id=4, wait_after_ms=200),
        StandSequenceStep(servo_id=6, wait_after_ms=200),
        StandSequenceStep(servo_id=5, wait_after_ms=0),
    ]
    midpoint_value = 1900
    for leg_name in ("front_left", "front_right", "rear_left", "rear_right"):
        midpoint_pose = getattr(config.stand_to_sit_mid_pose, leg_name)  # type: ignore[arg-type]
        midpoint_pose.hip_x = midpoint_value
        midpoint_pose.knee_y = midpoint_value
        midpoint_pose.foot = midpoint_value

    runtime_state_path = tmp_path / "doggo.state.json"
    stateful_supervisor = ControlSupervisor(
        config,
        servo_bus=None,
        runtime_state_path=runtime_state_path,
    )
    stateful_supervisor._record_pose_command("stand")

    supervisor = ControlSupervisor(
        config,
        servo_bus=object(),  # type: ignore[arg-type]
        runtime_state_path=runtime_state_path,
    )
    ordered_calls: list[dict[str, object]] = []
    sync_calls: list[dict[str, object]] = []

    async def fake_run_ordered_pose(
        commands: list[tuple[int, int]],
        *,
        speed: int,
        acceleration: int,
        wait_map_ms: dict[int, int] | None = None,
        servo_sequence: list[int] | None = None,
    ) -> None:
        ordered_calls.append(
            {
                "commands": commands,
                "speed": speed,
                "acceleration": acceleration,
                "wait_map_ms": wait_map_ms,
                "servo_sequence": servo_sequence,
            }
        )

    async def fake_run_sync_pose(
        commands: list[tuple[int, int]],
        *,
        speed: int,
        acceleration: int,
    ) -> None:
        sync_calls.append(
            {
                "commands": commands,
                "speed": speed,
                "acceleration": acceleration,
            }
        )

    supervisor._run_ordered_pose = fake_run_ordered_pose  # type: ignore[method-assign]
    supervisor._run_sync_pose = fake_run_sync_pose  # type: ignore[method-assign]

    sleep_mock = AsyncMock()
    with patch("doggo.control.supervisor.asyncio.sleep", sleep_mock):
        asyncio.run(supervisor.sit())

    assert sync_calls == [
        {
            "commands": [(servo_id, midpoint_value) for servo_id in range(1, 13)],
            "speed": 500,
            "acceleration": 8,
        },
        {
            "commands": [(7, 2048), (8, 2048), (9, 2048), (10, 2048), (11, 2048), (12, 2048)],
            "speed": 500,
            "acceleration": 8,
        },
    ]
    assert ordered_calls == [
        {
            "commands": [(1, 2048), (2, 2048), (3, 2048), (4, 2048), (5, 2048), (6, 2048)],
            "speed": 500,
            "acceleration": 8,
            "wait_map_ms": {2: 600, 3: 600, 1: 0, 4: 200, 6: 200, 5: 0},
            "servo_sequence": [2, 3, 1, 4, 6, 5],
        },
    ]
    assert sleep_mock.await_args_list == [call(0.5), call(0.5)]


def test_stand_test_2_uses_test_2_midpoint_before_stand(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    midpoint_value = 1777
    for leg_name in ("front_left", "front_right", "rear_left", "rear_right"):
        midpoint_pose = getattr(config.sit_to_stand_mid_test_2_pose, leg_name)  # type: ignore[arg-type]
        midpoint_pose.hip_x = midpoint_value
        midpoint_pose.knee_y = midpoint_value
        midpoint_pose.foot = midpoint_value

    class FakeBus:
        def __init__(self) -> None:
            self.torque_calls: list[tuple[int, bool]] = []
            self.sync_calls: list[dict[str, object]] = []

        def set_torque_enabled(self, servo_id: int, enabled: bool) -> None:
            self.torque_calls.append((servo_id, enabled))

        def sync_move(
            self,
            commands: list[tuple[int, int]],
            *,
            speed: int,
            acceleration: int,
        ) -> None:
            self.sync_calls.append(
                {
                    "commands": list(commands),
                    "speed": speed,
                    "acceleration": acceleration,
                }
            )

    runtime_state_path = tmp_path / "doggo.state.json"
    fake_bus = FakeBus()
    supervisor = ControlSupervisor(
        config,
        servo_bus=fake_bus,  # type: ignore[arg-type]
        runtime_state_path=runtime_state_path,
    )

    sleep_mock = AsyncMock()
    with patch("doggo.control.supervisor.asyncio.sleep", sleep_mock):
        asyncio.run(supervisor.stand_test_2())

    assert fake_bus.torque_calls == (
        [(servo_id, True) for servo_id in range(7, 13)]
        + [(servo_id, True) for servo_id in range(1, 13)]
    )
    assert fake_bus.sync_calls == [
        {
            "commands": [(servo_id, midpoint_value) for servo_id in range(7, 13)],
            "speed": 700,
            "acceleration": 10,
        },
        {
            "commands": [(servo_id, 2048) for servo_id in range(1, 13)],
            "speed": 700,
            "acceleration": 10,
        },
    ]
    sleep_mock.assert_awaited_once_with(1.0)


def test_stand_test_2_matches_main_stand_sequence(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    runtime_state_path = tmp_path / "doggo.state.json"
    supervisor = ControlSupervisor(
        config,
        servo_bus=object(),  # type: ignore[arg-type]
        runtime_state_path=runtime_state_path,
    )
    captured_calls: list[tuple[int, int]] = []

    async def fake_main_stand_sequence(
        *,
        speed: int,
        acceleration: int,
    ) -> None:
        captured_calls.append((speed, acceleration))

    supervisor._run_main_stand_sequence = fake_main_stand_sequence  # type: ignore[method-assign]

    asyncio.run(supervisor.stand_test_2())

    assert captured_calls == [(config.motion.stand_speed, config.motion.stand_acceleration)]
    assert supervisor._last_command_pose == "stand"


def test_apply_teleop_runs_walk_frame_when_robot_is_standing(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    runtime_state_path = tmp_path / "doggo.state.json"
    stateful_supervisor = ControlSupervisor(
        config,
        servo_bus=None,
        runtime_state_path=runtime_state_path,
    )
    stateful_supervisor._record_pose_command("stand")

    class FakeBus:
        is_open = True

    supervisor = ControlSupervisor(
        config,
        servo_bus=FakeBus(),  # type: ignore[arg-type]
        runtime_state_path=runtime_state_path,
    )
    sync_calls: list[dict[str, object]] = []

    async def fake_run_sync_pose(
        commands: list[tuple[int, int]],
        *,
        speed: int,
        acceleration: int,
    ) -> None:
        sync_calls.append(
            {
                "commands": commands,
                "speed": speed,
                "acceleration": acceleration,
            }
        )

    supervisor._run_sync_pose = fake_run_sync_pose  # type: ignore[method-assign]
    command = TeleopCommand(axes=TeleopAxes(forward=0.6))

    with patch("doggo.control.gait.time.monotonic", return_value=10.0):
        status = asyncio.run(supervisor.apply_teleop(command))

    assert supervisor.state == "walking"
    assert supervisor._last_command_pose == "walking"
    assert sync_calls[0]["speed"] == config.walking.step_speed
    assert sync_calls[0]["acceleration"] == config.walking.step_acceleration
    assert any(
        position != config.stand_position_for_servo(servo_id)
        for servo_id, position in sync_calls[0]["commands"]  # type: ignore[index]
    )
    assert status["state"] == "walking"


def test_apply_teleop_returns_to_stand_when_axes_go_neutral(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    runtime_state_path = tmp_path / "doggo.state.json"
    stateful_supervisor = ControlSupervisor(
        config,
        servo_bus=None,
        runtime_state_path=runtime_state_path,
    )
    stateful_supervisor._record_pose_command("walking")

    class FakeBus:
        is_open = True

    supervisor = ControlSupervisor(
        config,
        servo_bus=FakeBus(),  # type: ignore[arg-type]
        runtime_state_path=runtime_state_path,
    )
    sync_calls: list[dict[str, object]] = []

    async def fake_run_sync_pose(
        commands: list[tuple[int, int]],
        *,
        speed: int,
        acceleration: int,
    ) -> None:
        sync_calls.append(
            {
                "commands": commands,
                "speed": speed,
                "acceleration": acceleration,
            }
        )

    supervisor._run_sync_pose = fake_run_sync_pose  # type: ignore[method-assign]

    status = asyncio.run(supervisor.apply_teleop(TeleopCommand()))

    assert sync_calls == [
        {
            "commands": config.stand_commands(),
            "speed": config.motion.stand_speed,
            "acceleration": config.motion.stand_acceleration,
        }
    ]
    assert supervisor._last_command_pose == "stand"
    assert status["state"] == "standing"


def test_apply_teleop_blocks_walking_when_robot_is_sitting(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    runtime_state_path = tmp_path / "doggo.state.json"
    stateful_supervisor = ControlSupervisor(
        config,
        servo_bus=None,
        runtime_state_path=runtime_state_path,
    )
    stateful_supervisor._record_pose_command("sit")

    class FakeBus:
        is_open = True

    supervisor = ControlSupervisor(
        config,
        servo_bus=FakeBus(),  # type: ignore[arg-type]
        runtime_state_path=runtime_state_path,
    )

    async def fail_run_sync_pose(*args, **kwargs) -> None:
        raise AssertionError("Walking should stay blocked while the robot is sitting")

    supervisor._run_sync_pose = fail_run_sync_pose  # type: ignore[method-assign]
    status = asyncio.run(supervisor.apply_teleop(TeleopCommand(axes=TeleopAxes(forward=0.4))))

    assert supervisor.state == "teleop_blocked"
    assert "Move to stand first" in supervisor.last_message
    assert status["state"] == "teleop_blocked"


def test_record_motion_captures_frames_and_persists_recording(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))

    class FakeBus:
        def __init__(self) -> None:
            self._tick = 0

        def read_present_position(self, servo_id: int) -> int:
            self._tick += 1
            return (servo_id * 100) + self._tick

    supervisor = ControlSupervisor(
        config,
        servo_bus=FakeBus(),  # type: ignore[arg-type]
        runtime_state_path=tmp_path / "doggo.state.json",
    )

    monotonic_values = [0.0, 0.0, 0.02, 0.10, 0.12, 0.20, 0.22]

    def fake_monotonic() -> float:
        return monotonic_values.pop(0) if monotonic_values else 0.22

    sleep_mock = AsyncMock()
    with patch.object(supervisor, "_monotonic", side_effect=fake_monotonic):
        with patch("doggo.control.supervisor.asyncio.sleep", sleep_mock):
            recording = asyncio.run(
                supervisor.record_motion(
                    name="test_clip",
                    duration_ms=200,
                    sample_ms=100,
                )
            )

    assert recording.name == "test_clip"
    assert recording.frame_count == 2
    assert recording.duration_ms == 120
    assert recording.frames[0].timestamp_ms == 0
    assert recording.frames[1].timestamp_ms == 120
    assert recording.frames[0].positions[1] != recording.frames[1].positions[1]
    assert supervisor.recording_snapshot()["available"] is True
    assert supervisor.recording_path.exists()
    assert sleep_mock.await_count in {0, 1}


def test_playback_recording_replays_frames_with_original_timing(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))

    class FakeBus:
        def __init__(self) -> None:
            self.torque_calls: list[tuple[int, bool]] = []
            self.sync_calls: list[dict[str, object]] = []
            self.positions = {servo_id: 2048 for servo_id in range(1, 13)}
            self.positions[1] = 1200
            self.positions[2] = 2200

        def read_present_position(self, servo_id: int) -> int:
            return self.positions[servo_id]

        def set_torque_enabled(self, servo_id: int, enabled: bool) -> None:
            self.torque_calls.append((servo_id, enabled))

        def sync_move(
            self,
            commands: list[tuple[int, int]],
            *,
            speed: int,
            acceleration: int,
        ) -> None:
            self.sync_calls.append(
                {
                    "commands": list(commands),
                    "speed": speed,
                    "acceleration": acceleration,
                }
            )

    supervisor = ControlSupervisor(
        config,
        servo_bus=FakeBus(),  # type: ignore[arg-type]
        runtime_state_path=tmp_path / "doggo.state.json",
    )
    supervisor.last_recording = MotionRecording.model_validate(
        {
            "name": "test_clip",
            "duration_ms": 150,
            "sample_ms": 100,
            "servo_ids": [1, 2],
            "frames": [
                {"timestamp_ms": 0, "positions": {1: 1200, 2: 2200}},
                {"timestamp_ms": 150, "positions": {1: 1300, 2: 2300}},
            ],
        }
    )

    sleep_mock = AsyncMock()
    with patch("doggo.control.supervisor.asyncio.sleep", sleep_mock):
        recording = asyncio.run(supervisor.playback_recording(speed=900, acceleration=6))

    assert recording.name == "test_clip"
    assert supervisor.state == "playback_complete"
    assert supervisor.last_message == "Played back 2 frame(s) from test_clip."
    assert supervisor.servo_bus.sync_calls == [  # type: ignore[union-attr]
        {
            "commands": [(1, 1200), (2, 2200)],
            "speed": 900,
            "acceleration": 6,
        },
        {
            "commands": [(1, 1300), (2, 2300)],
            "speed": 900,
            "acceleration": 6,
        },
    ]
    sleep_mock.assert_awaited_once_with(0.15)


def test_playback_recording_uses_recorded_timing_to_derive_speed(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))

    class FakeBus:
        def __init__(self) -> None:
            self.torque_calls: list[tuple[int, bool]] = []
            self.sync_calls: list[dict[str, object]] = []
            self.positions = {servo_id: 2048 for servo_id in range(1, 13)}
            self.positions[1] = 1000
            self.positions[2] = 2000

        def read_present_position(self, servo_id: int) -> int:
            return self.positions[servo_id]

        def set_torque_enabled(self, servo_id: int, enabled: bool) -> None:
            self.torque_calls.append((servo_id, enabled))

        def sync_move(
            self,
            commands: list[tuple[int, int]],
            *,
            speed: int,
            acceleration: int,
        ) -> None:
            self.sync_calls.append(
                {
                    "commands": list(commands),
                    "speed": speed,
                    "acceleration": acceleration,
                }
            )

    supervisor = ControlSupervisor(
        config,
        servo_bus=FakeBus(),  # type: ignore[arg-type]
        runtime_state_path=tmp_path / "doggo.state.json",
    )
    supervisor.last_recording = MotionRecording.model_validate(
        {
            "name": "timed_clip",
            "duration_ms": 200,
            "sample_ms": 100,
            "servo_ids": [1, 2],
            "frames": [
                {"timestamp_ms": 0, "positions": {1: 1200, 2: 2100}},
                {"timestamp_ms": 200, "positions": {1: 1400, 2: 2400}},
            ],
        }
    )

    sleep_mock = AsyncMock()
    with patch("doggo.control.supervisor.asyncio.sleep", sleep_mock):
        asyncio.run(supervisor.playback_recording())

    assert supervisor.servo_bus.sync_calls == [  # type: ignore[union-attr]
        {
            "commands": [(1, 1200), (2, 2100)],
            "speed": config.motion.default_speed,
            "acceleration": config.motion.default_acceleration,
        },
        {
            "commands": [(1, 1400), (2, 2400)],
            "speed": 1500,
            "acceleration": config.motion.default_acceleration,
        },
    ]
    sleep_mock.assert_awaited_once_with(0.2)


def test_record_motion_auto_stops_after_idle_timeout(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))

    class FakeBus:
        def read_present_position(self, servo_id: int) -> int:
            return 2000 + servo_id

    supervisor = ControlSupervisor(
        config,
        servo_bus=FakeBus(),  # type: ignore[arg-type]
        runtime_state_path=tmp_path / "doggo.state.json",
    )

    monotonic_values = [0.0, 0.0, 0.05, 0.2, 0.25, 0.25, 0.75, 0.8, 0.8]

    def fake_monotonic() -> float:
        return monotonic_values.pop(0) if monotonic_values else 0.8

    sleep_mock = AsyncMock()
    with patch.object(supervisor, "_monotonic", side_effect=fake_monotonic):
        with patch("doggo.control.supervisor.asyncio.sleep", sleep_mock):
            recording = asyncio.run(
                supervisor.record_motion(
                    name="idle_clip",
                    duration_ms=10_000,
                    sample_ms=200,
                    idle_stop_seconds=0.5,
                    idle_threshold_ticks=15,
                )
            )

    assert recording.name == "idle_clip"
    assert recording.stop_reason == "idle"
    assert recording.duration_ms == 600
    assert supervisor.last_message.endswith("Stop reason: idle.")


def test_save_recording_persists_named_clip_and_lists_it(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    supervisor = ControlSupervisor(
        config,
        servo_bus=None,
        runtime_state_path=tmp_path / "doggo.state.json",
    )
    supervisor.last_recording = MotionRecording.model_validate(
        {
            "name": "last_capture",
            "duration_ms": 250,
            "sample_ms": 100,
            "frames": [{"timestamp_ms": 0, "positions": {1: 1000}}],
            "servo_ids": [1],
            "stop_reason": "manual",
        }
    )

    saved = supervisor.save_recording("wave_test")

    assert saved.name == "wave_test"
    assert (tmp_path / "recordings" / "wave_test.json").exists()
    assert supervisor.list_saved_recordings()[0]["name"] == "wave_test"


def test_save_current_recording_reads_live_positions_and_persists_named_clip(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))

    class FakeBus:
        def read_present_position(self, servo_id: int) -> int:
            return 2000 + servo_id

    supervisor = ControlSupervisor(
        config,
        servo_bus=FakeBus(),  # type: ignore[arg-type]
        runtime_state_path=tmp_path / "doggo.state.json",
    )

    saved = asyncio.run(supervisor.save_current_recording("stand_snapshot"))

    assert saved.name == "stand_snapshot"
    assert saved.duration_ms == 0
    assert saved.frame_count == 1
    assert saved.frames[0].timestamp_ms == 0
    assert saved.frames[0].positions[1] == 2001
    assert saved.frames[0].positions[12] == 2012
    assert saved.servo_ids == list(range(1, 13))
    assert supervisor.recording_snapshot()["available"] is True
    assert (tmp_path / "recordings" / "stand_snapshot.json").exists()
    assert supervisor.recording_path.exists()
    assert supervisor.last_message == "Saved current positions as stand_snapshot from 12 reachable servo(s)."


def test_playback_recording_can_load_saved_clip_by_name(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))

    class FakeBus:
        def __init__(self) -> None:
            self.torque_calls: list[tuple[int, bool]] = []
            self.sync_calls: list[dict[str, object]] = []
            self.positions = {servo_id: 2048 for servo_id in range(1, 13)}

        def read_present_position(self, servo_id: int) -> int:
            return self.positions[servo_id]

        def set_torque_enabled(self, servo_id: int, enabled: bool) -> None:
            self.torque_calls.append((servo_id, enabled))

        def sync_move(
            self,
            commands: list[tuple[int, int]],
            *,
            speed: int,
            acceleration: int,
        ) -> None:
            self.sync_calls.append(
                {
                    "commands": list(commands),
                    "speed": speed,
                    "acceleration": acceleration,
                }
            )

    supervisor = ControlSupervisor(
        config,
        servo_bus=FakeBus(),  # type: ignore[arg-type]
        runtime_state_path=tmp_path / "doggo.state.json",
    )
    saved_path = tmp_path / "recordings" / "saved_wave.json"
    saved_path.parent.mkdir(parents=True, exist_ok=True)
    saved_path.write_text(
        MotionRecording.model_validate(
            {
                "name": "saved_wave",
                "duration_ms": 100,
                "sample_ms": 100,
                "servo_ids": [1],
                "stop_reason": "duration",
                "frames": [
                    {"timestamp_ms": 0, "positions": {1: 1500}},
                    {"timestamp_ms": 100, "positions": {1: 1700}},
                ],
            }
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    sleep_mock = AsyncMock()
    with patch("doggo.control.supervisor.asyncio.sleep", sleep_mock):
        recording = asyncio.run(supervisor.playback_recording(name="saved_wave"))

    assert recording.name == "saved_wave"
    assert supervisor.servo_bus.sync_calls[-1]["commands"] == [(1, 1700)]  # type: ignore[union-attr]
