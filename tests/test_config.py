from pathlib import Path

from doggo.config import load_config


def test_example_config_loads() -> None:
    config = load_config(Path("config/doggo.example.yaml"))
    assert config.robot.name == "Doggo"
    assert config.legs.front_left.hip_x.id == 1
    assert config.legs.rear_right.foot.id == 12
    assert config.web.port == 8080
    assert config.motion.stand_speed == 700
    assert config.motion.stand_acceleration == 10
    assert config.motion.sit_speed == 500
    assert config.motion.sit_acceleration == 8
    assert len(config.stand_commands()) == 12
    assert config.storage_pose is not None
    assert config.storage_pose.front_right.hip_x == 2048
    assert config.sit_pose is not None
    assert config.sit_pose.front_right.hip_x == 2048
    assert config.stand_to_sit_mid_pose is not None
    assert config.stand_to_sit_mid_pose.front_right.hip_x == 2048
    assert config.sit_to_stand_mid_pose is not None
    assert config.sit_to_stand_mid_pose.front_right.hip_x == 2048
    assert config.sit_to_stand_mid_test_2_pose is not None
    assert config.sit_to_stand_mid_test_2_pose.front_right.hip_x == 2048
    assert config.max_pose is not None
    assert config.max_pose.front_right.hip_x == 2048
    assert config.sit_to_stand_mid_pause_ms == 500
    assert config.stand_position_for_servo(1) == 2048
    assert config.stand_position_for_servo(99) is None
    assert config.joint_config_for_servo(8).id == 8
    assert config.joint_config_for_servo(99) is None


def test_pose_commands_can_be_filtered_to_a_single_leg() -> None:
    config = load_config(Path("config/doggo.example.yaml"))

    assert config.stand_commands(["front_left"]) == [(1, 2048), (2, 2048), (3, 2048)]
    assert config.storage_commands(["rear_right"]) == [(10, 2048), (11, 2048), (12, 2048)]
    assert config.sit_commands(["rear_left"]) == [(7, 2048), (8, 2048), (9, 2048)]
    assert config.stand_to_sit_mid_commands(["front_right"]) == [(4, 2048), (5, 2048), (6, 2048)]
    assert config.sit_to_stand_mid_commands(["front_right"]) == [(4, 2048), (5, 2048), (6, 2048)]
    assert config.sit_to_stand_mid_test_2_commands(["front_right"]) == [(4, 2048), (5, 2048), (6, 2048)]
