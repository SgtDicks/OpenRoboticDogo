#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from doggo.vision.camera_overlap import normalize_camera_indexes, run_camera_overlap_viewer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open two cameras, verify they stream, and visualize their overlapping field of view."
    )
    parser.add_argument(
        "--config",
        default="config/doggo.local.yaml",
        help="Path to the Doggo config file. Used when --camera is not supplied.",
    )
    parser.add_argument(
        "--camera",
        action="append",
        dest="camera_indexes",
        type=int,
        default=None,
        help="Camera index to test. Pass this flag twice for two cameras.",
    )
    parser.add_argument("--width", type=int, default=None, help="Requested capture width for both cameras.")
    parser.add_argument("--height", type=int, default=None, help="Requested capture height for both cameras.")
    parser.add_argument(
        "--refresh-every",
        type=int,
        default=10,
        help="Recompute the overlap estimate every N frames.",
    )
    parser.add_argument(
        "--max-features",
        type=int,
        default=1_500,
        help="Maximum ORB features used to estimate overlap.",
    )
    parser.add_argument(
        "--min-matches",
        type=int,
        default=25,
        help="Minimum good matches required before the overlap estimate is treated as stable.",
    )
    return parser


def _camera_indexes_from_config(config_path: Path) -> list[int]:
    try:
        from doggo.config import load_config
    except ModuleNotFoundError as exc:
        print(
            f"Could not import the Doggo config loader ({exc}); defaulting to camera indexes [0, 1].",
            file=sys.stderr,
        )
        return [0, 1]

    try:
        config = load_config(config_path)
    except FileNotFoundError:
        print(
            f"Config {config_path} was not found; defaulting to camera indexes [0, 1].",
            file=sys.stderr,
        )
        return [0, 1]
    return normalize_camera_indexes(config.vision.camera_indexes)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    camera_indexes = (
        normalize_camera_indexes(args.camera_indexes)
        if args.camera_indexes
        else _camera_indexes_from_config(Path(args.config))
    )

    if len(camera_indexes) < 2:
        print(
            "Need at least two camera indexes. Pass `--camera 0 --camera 1` or set "
            "`vision.camera_indexes` in the Doggo config.",
            file=sys.stderr,
        )
        return 1

    try:
        return run_camera_overlap_viewer(
            camera_indexes,
            width=args.width,
            height=args.height,
            refresh_every=args.refresh_every,
            max_features=args.max_features,
            min_matches=args.min_matches,
        )
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
