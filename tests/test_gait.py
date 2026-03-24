from unittest.mock import patch

from doggo.config import load_config
from doggo.control.gait import CrawlGaitPlanner
from doggo.models import TeleopAxes, TeleopCommand


def test_crawl_gait_status_is_ready_with_example_config() -> None:
    config = load_config("config/doggo.example.yaml")
    planner = CrawlGaitPlanner(config)

    status = planner.status()

    assert status.ready is True
    assert "crawl gait" in status.reason


def test_crawl_gait_generates_joint_space_commands_within_joint_limits() -> None:
    config = load_config("config/doggo.example.yaml")
    planner = CrawlGaitPlanner(config)
    command = TeleopCommand(axes=TeleopAxes(forward=0.6, strafe=0.2, turn=-0.1))

    with patch("doggo.control.gait.time.monotonic", return_value=10.0):
        frame = planner.accept(command)

    assert frame.ready is True
    assert len(frame.commands) == 12
    assert frame.speed == config.walking.step_speed
    assert frame.acceleration == config.walking.step_acceleration

    command_map = dict(frame.commands)
    rear_left = config.legs.rear_left
    assert command_map[rear_left.knee_y.id] != config.stand_pose.rear_left.knee_y

    for servo_id, target in frame.commands:
        joint = config.joint_config_for_servo(servo_id)
        assert joint is not None
        assert joint.min_ticks <= target <= joint.max_ticks
