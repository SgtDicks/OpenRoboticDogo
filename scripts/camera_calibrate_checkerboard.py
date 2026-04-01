#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _require_cv2() -> Any:
    try:
        import cv2
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "OpenCV is required for calibration. Install it with "
            "`pip install -e '.[vision]'` or `pip install opencv-python`."
        ) from exc
    return cv2


ROLES = ("left", "forward", "right")
PAIR_SPECS = (("left", "forward"), ("right", "forward"))
DEFAULT_CAPTURE_ROOT = Path("config/calibration/captures")
DEFAULT_OUTPUT_PATH = Path("config/calibration/camera_alignment.json")


@dataclass(slots=True)
class PairCalibration:
    source_role: str
    target_role: str
    matrix: list[list[float]]
    inverse_matrix: list[list[float]]
    points_used: int
    inlier_points: int
    pairs_used: int
    mean_error_px: float
    max_error_px: float
    image_size: tuple[int, int]
    preview_path: str | None = None


@dataclass(slots=True)
class CapturePair:
    label: str
    source_path: Path
    target_path: Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Solve left->forward and right->forward checkerboard homographies from captured image triples."
    )
    parser.add_argument(
        "--capture-dir",
        type=Path,
        default=DEFAULT_CAPTURE_ROOT,
        help="Session directory with captured PNG triples, or the captures root to auto-pick the newest session.",
    )
    parser.add_argument("--pattern-cols", type=int, default=None, help="Checkerboard inner corner columns.")
    parser.add_argument("--pattern-rows", type=int, default=None, help="Checkerboard inner corner rows.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--preview-dir",
        type=Path,
        default=Path("config/calibration/previews"),
        help="Directory where blended preview images will be written.",
    )
    return parser


def _resolve_capture_dir(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.is_dir():
        png_files = list(resolved.glob("*.png"))
        if png_files:
            return resolved
        subdirs = sorted((candidate for candidate in resolved.iterdir() if candidate.is_dir()), key=lambda candidate: candidate.stat().st_mtime)
        if subdirs:
            return subdirs[-1]
    raise FileNotFoundError(f"Could not find a calibration capture session under {path}.")


def _load_manifest(session_dir: Path) -> dict[str, object]:
    manifest_path = session_dir / "session_manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _resolve_pattern_size(args: argparse.Namespace, manifest: dict[str, object]) -> tuple[int, int]:
    cols = args.pattern_cols or int(manifest.get("pattern_columns", 0) or 0)
    rows = args.pattern_rows or int(manifest.get("pattern_rows", 0) or 0)
    if cols <= 0 or rows <= 0:
        raise ValueError("Checkerboard size is required. Pass --pattern-cols and --pattern-rows or capture via the tools server.")
    return cols, rows


def _group_capture_triples(session_dir: Path) -> dict[str, dict[str, Path]]:
    groups: dict[str, dict[str, Path]] = {}
    for image_path in sorted(session_dir.glob("*.png")):
        stem = image_path.stem
        for role in ROLES:
            suffix = f"_{role}"
            if stem.endswith(suffix):
                label = stem[: -len(suffix)]
                groups.setdefault(label, {})[role] = image_path
                break
    return groups


def _find_checkerboard_corners(image_path: Path, pattern_size: tuple[int, int]) -> tuple[Any, tuple[int, int]]:
    cv2 = _require_cv2()
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Could not read image {image_path}.")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    corners = None
    found = False
    if hasattr(cv2, "findChessboardCornersSB"):
        found, corners = cv2.findChessboardCornersSB(
            gray,
            pattern_size,
            flags=cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE,
        )
    if not found:
        found, corners = cv2.findChessboardCorners(
            gray,
            pattern_size,
            flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE,
        )
        if found:
            criteria = (
                cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
                50,
                1e-4,
            )
            cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)

    if not found or corners is None:
        raise ValueError(f"Checkerboard was not detected in {image_path.name}.")
    return corners.astype("float32"), (image.shape[1], image.shape[0])


def _collect_pair_observations(
    triplets: dict[str, dict[str, Path]],
    *,
    source_role: str,
    target_role: str,
    pattern_size: tuple[int, int],
) -> tuple[list[CapturePair], Any, Any, tuple[int, int]]:
    import numpy as np

    pairs: list[CapturePair] = []
    all_source_points: list[Any] = []
    all_target_points: list[Any] = []
    image_size: tuple[int, int] | None = None

    for label, role_paths in sorted(triplets.items()):
        source_path = role_paths.get(source_role)
        target_path = role_paths.get(target_role)
        if source_path is None or target_path is None:
            continue

        try:
            source_corners, source_size = _find_checkerboard_corners(source_path, pattern_size)
            target_corners, target_size = _find_checkerboard_corners(target_path, pattern_size)
        except ValueError as exc:
            print(f"Skipping {label}: {exc}")
            continue

        if source_size != target_size:
            raise ValueError(
                f"Image sizes do not match for {label}: {source_path.name} is {source_size}, "
                f"but {target_path.name} is {target_size}."
            )

        image_size = source_size
        pairs.append(CapturePair(label=label, source_path=source_path, target_path=target_path))
        all_source_points.append(source_corners)
        all_target_points.append(target_corners)

    if not pairs or image_size is None:
        raise ValueError(f"No usable {source_role}->{target_role} checkerboard pairs were found in the session.")

    return pairs, np.concatenate(all_source_points, axis=0), np.concatenate(all_target_points, axis=0), image_size


def _render_preview(source_path: Path, target_path: Path, homography: Any, output_path: Path) -> None:
    cv2 = _require_cv2()
    source = cv2.imread(str(source_path))
    target = cv2.imread(str(target_path))
    if source is None or target is None:
        return

    height, width = target.shape[:2]
    warped_source = cv2.warpPerspective(source, homography, (width, height))
    blended = cv2.addWeighted(warped_source, 0.5, target, 0.5, 0.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), blended)


def _solve_pair(
    triplets: dict[str, dict[str, Path]],
    *,
    source_role: str,
    target_role: str,
    pattern_size: tuple[int, int],
    preview_dir: Path,
) -> PairCalibration:
    import numpy as np

    cv2 = _require_cv2()
    capture_pairs, source_points, target_points, image_size = _collect_pair_observations(
        triplets,
        source_role=source_role,
        target_role=target_role,
        pattern_size=pattern_size,
    )

    homography, mask = cv2.findHomography(source_points, target_points, cv2.RANSAC, 3.0)
    if homography is None:
        raise ValueError(f"Could not solve a homography for {source_role}->{target_role}.")

    projected = cv2.perspectiveTransform(source_points, homography)
    errors = np.linalg.norm(projected - target_points, axis=2).reshape(-1)
    if mask is not None:
        inlier_mask = mask.ravel().astype(bool)
        inlier_errors = errors[inlier_mask]
        inlier_points = int(inlier_mask.sum())
    else:
        inlier_errors = errors
        inlier_points = int(errors.shape[0])

    inverse_homography = np.linalg.inv(homography)
    preview_path = preview_dir / f"{source_role}_to_{target_role}_preview.png"
    _render_preview(capture_pairs[0].source_path, capture_pairs[0].target_path, homography, preview_path)

    return PairCalibration(
        source_role=source_role,
        target_role=target_role,
        matrix=homography.tolist(),
        inverse_matrix=inverse_homography.tolist(),
        points_used=int(source_points.shape[0]),
        inlier_points=inlier_points,
        pairs_used=len(capture_pairs),
        mean_error_px=float(inlier_errors.mean()) if inlier_errors.size else 0.0,
        max_error_px=float(inlier_errors.max()) if inlier_errors.size else 0.0,
        image_size=image_size,
        preview_path=str(preview_path),
    )


def _write_output(
    output_path: Path,
    *,
    session_dir: Path,
    pattern_size: tuple[int, int],
    manifest: dict[str, object],
    pairs: dict[str, PairCalibration],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "capture_session": str(session_dir),
        "pattern_size": {
            "columns": pattern_size[0],
            "rows": pattern_size[1],
        },
        "square_size_mm": manifest.get("square_size_mm"),
        "pairs": {
            key: {
                "source_role": value.source_role,
                "target_role": value.target_role,
                "matrix": value.matrix,
                "inverse_matrix": value.inverse_matrix,
                "points_used": value.points_used,
                "inlier_points": value.inlier_points,
                "pairs_used": value.pairs_used,
                "mean_error_px": value.mean_error_px,
                "max_error_px": value.max_error_px,
                "image_size": {
                    "width": value.image_size[0],
                    "height": value.image_size[1],
                },
                "preview_path": value.preview_path,
            }
            for key, value in pairs.items()
        },
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session_dir = _resolve_capture_dir(args.capture_dir)
    manifest = _load_manifest(session_dir)
    pattern_size = _resolve_pattern_size(args, manifest)
    preview_dir = args.preview_dir / session_dir.name

    print(f"Using capture session: {session_dir}")
    print(f"Checkerboard size: {pattern_size[0]} x {pattern_size[1]} inner corners")

    triplets = _group_capture_triples(session_dir)
    if not triplets:
        raise SystemExit(f"No PNG captures were found in {session_dir}.")

    solved_pairs: dict[str, PairCalibration] = {}
    for source_role, target_role in PAIR_SPECS:
        print(f"Solving {source_role} -> {target_role}...")
        calibration = _solve_pair(
            triplets,
            source_role=source_role,
            target_role=target_role,
            pattern_size=pattern_size,
            preview_dir=preview_dir,
        )
        solved_pairs[f"{source_role}_to_{target_role}"] = calibration
        print(
            f"  pairs={calibration.pairs_used}, inliers={calibration.inlier_points}/{calibration.points_used}, "
            f"mean error={calibration.mean_error_px:.2f}px, max error={calibration.max_error_px:.2f}px"
        )

    _write_output(
        args.output,
        session_dir=session_dir,
        pattern_size=pattern_size,
        manifest=manifest,
        pairs=solved_pairs,
    )
    print(f"Saved calibration to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
