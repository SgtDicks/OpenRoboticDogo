from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import uvicorn

from doggo.config import AppConfig, LEG_NAMES, load_config
from doggo.control.supervisor import ControlSupervisor
from doggo.hardware.sts3215 import ServoScanResult, Sts3215Bus
from doggo.web.api import create_app


def _runtime_state_path(config_path: Path) -> Path:
    return config_path.with_suffix(".state.json")


def _load_runtime_bits(config_path: Path) -> tuple[AppConfig, Sts3215Bus | None, ControlSupervisor]:
    config = load_config(config_path)
    servo_bus = None
    if config.servo_bus.enabled:
        servo_bus = Sts3215Bus(
            device=config.servo_bus.device,
            baud_rate=config.servo_bus.baud_rate,
            timeout_seconds=config.servo_bus.timeout_seconds,
        )
        servo_bus.open()
    supervisor = ControlSupervisor(
        config,
        servo_bus,
        runtime_state_path=_runtime_state_path(config_path),
    )
    return config, servo_bus, supervisor


def _scan_to_text(results: list[ServoScanResult]) -> str:
    return json.dumps([result.to_dict() for result in results], indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Doggo control tools")
    parser.add_argument(
        "--config",
        default="config/doggo.local.yaml",
        help="Path to the Doggo YAML config file.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan", help="Scan the servo bus.")
    stand_parser = subparsers.add_parser("stand", help="Move into the configured stand pose.")
    stand_parser.add_argument("--leg", action="append", choices=LEG_NAMES, dest="legs", default=None)
    stand_parser.add_argument("--speed", type=int, default=None)
    stand_parser.add_argument("--acceleration", type=int, default=None)
    storage_parser = subparsers.add_parser("storage", help="Move into the configured storage pose.")
    storage_parser.add_argument("--leg", action="append", choices=LEG_NAMES, dest="legs", default=None)
    storage_parser.add_argument("--speed", type=int, default=None)
    storage_parser.add_argument("--acceleration", type=int, default=None)
    sit_parser = subparsers.add_parser("sit", help="Move into the configured sit pose.")
    sit_parser.add_argument("--leg", action="append", choices=LEG_NAMES, dest="legs", default=None)
    sit_parser.add_argument("--speed", type=int, default=None)
    sit_parser.add_argument("--acceleration", type=int, default=None)
    stand_test_2_parser = subparsers.add_parser("stand-test-2", help="Run sit -> stand test 2 midpoint -> stand.")
    stand_test_2_parser.add_argument("--speed", type=int, default=None)
    stand_test_2_parser.add_argument("--acceleration", type=int, default=None)
    sss_parser = subparsers.add_parser("sss", help="Run stand -> storage -> stand.")
    sss_parser.add_argument("--leg", action="append", choices=LEG_NAMES, dest="legs", default=None)
    sss_parser.add_argument("--speed", type=int, default=None)
    sss_parser.add_argument("--acceleration", type=int, default=None)
    record_parser = subparsers.add_parser("record", help="Record a whole-body motion clip.")
    record_parser.add_argument("--name", default="last_capture")
    record_parser.add_argument("--duration-ms", type=int, default=10_000)
    record_parser.add_argument("--sample-ms", type=int, default=100)
    record_parser.add_argument("--idle-stop-seconds", type=float, default=None)
    record_parser.add_argument("--idle-threshold-ticks", type=int, default=15)
    subparsers.add_parser("stop-recording", help="Stop the active motion recording.")
    save_recording_parser = subparsers.add_parser("save-recording", help="Save the last motion recording under a name.")
    save_recording_parser.add_argument("--name", required=True)
    playback_parser = subparsers.add_parser("playback", help="Play back the latest recorded motion clip.")
    playback_parser.add_argument("--name", default=None)
    playback_parser.add_argument("--speed", type=int, default=None)
    playback_parser.add_argument("--acceleration", type=int, default=None)
    subparsers.add_parser("relax", help="Disable torque for configured servos.")
    subparsers.add_parser("read-all", help="Read all configured servo positions.")
    serve_parser = subparsers.add_parser("serve", help="Run the web API.")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=None)

    read_pos = subparsers.add_parser("read-pos", help="Read a servo position.")
    read_pos.add_argument("--id", type=int, required=True)

    move = subparsers.add_parser("move", help="Move a single servo.")
    move.add_argument("--id", type=int, required=True)
    move.add_argument("--position", type=int, required=True)
    move.add_argument("--speed", type=int, default=None)
    move.add_argument("--acceleration", type=int, default=None)

    step_test = subparsers.add_parser("step-test", help="Run a small reversible step test on one servo.")
    step_test.add_argument("--id", type=int, required=True)
    step_test.add_argument("--delta", type=int, default=60)
    step_test.add_argument("--steps", type=int, default=10)
    step_test.add_argument("--hold-ms", type=int, default=250)
    step_test.add_argument("--speed", type=int, default=None)
    step_test.add_argument("--acceleration", type=int, default=None)

    assign_id = subparsers.add_parser("assign-id", help="Change a servo ID.")
    assign_id.add_argument("--current-id", type=int, required=True)
    assign_id.add_argument("--new-id", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)

    if args.command == "serve":
        config = load_config(config_path)
        host = args.host or config.web.host
        port = args.port or config.web.port
        uvicorn.run(create_app(config_path), host=host, port=port, log_level="info")
        return 0

    config, servo_bus, supervisor = _load_runtime_bits(config_path)
    del config

    try:
        if args.command == "scan":
            results = asyncio.run(supervisor.scan_servos())
            print(_scan_to_text(results))
        elif args.command == "read-all":
            positions = asyncio.run(supervisor.read_all_positions())
            print(json.dumps(positions, indent=2))
        elif args.command == "read-pos":
            position = asyncio.run(supervisor.read_position(args.id))
            print(position)
        elif args.command == "move":
            asyncio.run(
                supervisor.move_servo(
                    args.id,
                    args.position,
                    speed=args.speed,
                    acceleration=args.acceleration,
                )
            )
            print(supervisor.last_message)
        elif args.command == "step-test":
            summary = asyncio.run(
                supervisor.step_test_servo(
                    args.id,
                    delta=args.delta,
                    steps=args.steps,
                    hold_ms=args.hold_ms,
                    speed=args.speed,
                    acceleration=args.acceleration,
                )
            )
            print(json.dumps(summary, indent=2))
        elif args.command == "assign-id":
            asyncio.run(supervisor.assign_servo_id(args.current_id, args.new_id))
            print(supervisor.last_message)
        elif args.command == "stand":
            asyncio.run(
                supervisor.stand(
                    leg_names=args.legs,
                    speed=args.speed,
                    acceleration=args.acceleration,
                )
            )
            print(supervisor.last_message)
        elif args.command == "storage":
            asyncio.run(
                supervisor.storage(
                    leg_names=args.legs,
                    speed=args.speed,
                    acceleration=args.acceleration,
                )
            )
            print(supervisor.last_message)
        elif args.command == "sit":
            asyncio.run(
                supervisor.sit(
                    leg_names=args.legs,
                    speed=args.speed,
                    acceleration=args.acceleration,
                )
            )
            print(supervisor.last_message)
        elif args.command == "stand-test-2":
            asyncio.run(
                supervisor.stand_test_2(
                    speed=args.speed,
                    acceleration=args.acceleration,
                )
            )
            print(supervisor.last_message)
        elif args.command == "sss":
            asyncio.run(
                supervisor.sss(
                    leg_names=args.legs,
                    speed=args.speed,
                    acceleration=args.acceleration,
                )
            )
            print(supervisor.last_message)
        elif args.command == "record":
            recording = asyncio.run(
                supervisor.record_motion(
                    name=args.name,
                    duration_ms=args.duration_ms,
                    sample_ms=args.sample_ms,
                    idle_stop_seconds=args.idle_stop_seconds,
                    idle_threshold_ticks=args.idle_threshold_ticks,
                )
            )
            print(recording.model_dump_json(indent=2))
        elif args.command == "stop-recording":
            snapshot = asyncio.run(supervisor.stop_recording())
            print(json.dumps(snapshot, indent=2))
        elif args.command == "save-recording":
            recording = supervisor.save_recording(args.name)
            print(recording.model_dump_json(indent=2))
        elif args.command == "playback":
            recording = asyncio.run(
                supervisor.playback_recording(
                    name=args.name,
                    speed=args.speed,
                    acceleration=args.acceleration,
                )
            )
            print(recording.model_dump_json(indent=2))
        elif args.command == "relax":
            asyncio.run(supervisor.relax())
            print(supervisor.last_message)
        else:
            raise ValueError(f"Unknown command: {args.command}")
    finally:
        if servo_bus:
            servo_bus.close()

    return 0
