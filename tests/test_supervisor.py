import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

from doggo.config import StandSequenceStep, load_config
from doggo.control.supervisor import ControlSupervisor
from doggo.models import TeleopAxes, TeleopCommand


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


def test_stand_from_sit_uses_sync_move_for_full_body_transition(tmp_path: Path) -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    midpoint_value = 1900
    for leg_name in ("front_left", "front_right", "rear_left", "rear_right"):
        midpoint_pose = getattr(config.sit_to_stand_mid_pose, leg_name)  # type: ignore[arg-type]
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

    async def fail_run_ordered_pose(*args, **kwargs) -> None:
        raise AssertionError("sit -> stand should use sync_move, not ordered sequencing")

    supervisor._run_ordered_pose = fail_run_ordered_pose  # type: ignore[method-assign]
    sleep_mock = AsyncMock()
    with patch("doggo.control.supervisor.asyncio.sleep", sleep_mock):
        asyncio.run(supervisor.stand())

    assert fake_bus.torque_calls == ([(servo_id, True) for servo_id in range(1, 13)] * 2)
    assert fake_bus.sync_calls == [
        {
            "commands": [(servo_id, midpoint_value) for servo_id in range(1, 13)],
            "speed": 700,
            "acceleration": 10,
        },
        {
            "commands": [(servo_id, 2048) for servo_id in range(1, 13)],
            "speed": 700,
            "acceleration": 10,
        }
    ]
    sleep_mock.assert_awaited_once_with(0.5)


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
