from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Callable

Point = tuple[float, float]
CoverageSegment = tuple[float, float]


@dataclass(slots=True)
class OverlapEstimate:
    matches: int
    inliers: int
    overlap_ratio_a_in_b: float
    overlap_ratio_b_in_a: float
    status: str
    projected_a_in_b: list[Point]
    projected_b_in_a: list[Point]
    overlay_frame: Any | None = None

    @property
    def overlap_percent(self) -> float:
        return ((self.overlap_ratio_a_in_b + self.overlap_ratio_b_in_a) / 2.0) * 100.0

    @property
    def success(self) -> bool:
        return self.overlay_frame is not None and self.inliers > 0


@dataclass(slots=True, frozen=True)
class CameraCoverageModel:
    name: str
    center_deg: float
    horizontal_fov_deg: float


def normalize_camera_indexes(
    camera_indexes: Sequence[int] | None,
    *,
    fallback: Sequence[int] = (0, 1),
) -> list[int]:
    selected = list(camera_indexes or fallback)
    normalized: list[int] = []
    seen: set[int] = set()
    for index in selected:
        if index in seen:
            continue
        seen.add(index)
        normalized.append(index)
    return normalized


def normalize_heading_degrees(angle_deg: float) -> float:
    normalized = ((angle_deg + 180.0) % 360.0) - 180.0
    if normalized == -180.0 and angle_deg > 0.0:
        return 180.0
    return normalized


def coverage_segments(center_deg: float, horizontal_fov_deg: float) -> list[CoverageSegment]:
    if horizontal_fov_deg <= 0.0:
        return []
    if horizontal_fov_deg >= 360.0:
        return [(-180.0, 180.0)]

    center = normalize_heading_degrees(center_deg)
    half_fov = horizontal_fov_deg * 0.5
    start = center - half_fov
    end = center + half_fov

    if start < -180.0:
        return [(-180.0, end), (start + 360.0, 180.0)]
    if end > 180.0:
        return [(-180.0, end - 360.0), (start, 180.0)]
    return [(start, end)]


def merge_coverage_segments(segments: Sequence[CoverageSegment]) -> list[CoverageSegment]:
    if not segments:
        return []

    merged: list[CoverageSegment] = []
    for start, end in sorted(segments, key=lambda segment: segment[0]):
        if not merged:
            merged.append((start, end))
            continue

        previous_start, previous_end = merged[-1]
        if start <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end))
            continue
        merged.append((start, end))
    return merged


def overlap_width_degrees(view_a: CameraCoverageModel, view_b: CameraCoverageModel) -> float:
    total = 0.0
    for start_a, end_a in coverage_segments(view_a.center_deg, view_a.horizontal_fov_deg):
        for start_b, end_b in coverage_segments(view_b.center_deg, view_b.horizontal_fov_deg):
            overlap_start = max(start_a, start_b)
            overlap_end = min(end_a, end_b)
            if overlap_end > overlap_start:
                total += overlap_end - overlap_start
    return total


def total_coverage_width_degrees(views: Sequence[CameraCoverageModel]) -> float:
    segments: list[CoverageSegment] = []
    for view in views:
        segments.extend(coverage_segments(view.center_deg, view.horizontal_fov_deg))
    return sum(end - start for start, end in merge_coverage_segments(segments))


def polygon_area(points: Sequence[Point]) -> float:
    if len(points) < 3:
        return 0.0
    doubled_area = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        doubled_area += (x1 * y2) - (x2 * y1)
    return abs(doubled_area) * 0.5


def _clip_polygon_against_edge(
    points: Sequence[Point],
    *,
    inside: Callable[[Point], bool],
    intersect: Callable[[Point, Point], Point],
) -> list[Point]:
    if not points:
        return []

    clipped: list[Point] = []
    previous = points[-1]
    previous_inside = inside(previous)

    for current in points:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                clipped.append(intersect(previous, current))
            clipped.append(current)
        elif previous_inside:
            clipped.append(intersect(previous, current))
        previous = current
        previous_inside = current_inside

    return clipped


def _intersect_vertical(start: Point, end: Point, x_limit: float) -> Point:
    x1, y1 = start
    x2, y2 = end
    if x1 == x2:
        return x_limit, y1
    ratio = (x_limit - x1) / (x2 - x1)
    return x_limit, y1 + (ratio * (y2 - y1))


def _intersect_horizontal(start: Point, end: Point, y_limit: float) -> Point:
    x1, y1 = start
    x2, y2 = end
    if y1 == y2:
        return x1, y_limit
    ratio = (y_limit - y1) / (y2 - y1)
    return x1 + (ratio * (x2 - x1)), y_limit


def clip_polygon_to_frame(points: Sequence[Point], *, width: int, height: int) -> list[Point]:
    if width <= 0 or height <= 0:
        return []

    clipped = list(points)
    clipped = _clip_polygon_against_edge(
        clipped,
        inside=lambda point: point[0] >= 0.0,
        intersect=lambda start, end: _intersect_vertical(start, end, 0.0),
    )
    clipped = _clip_polygon_against_edge(
        clipped,
        inside=lambda point: point[0] <= float(width),
        intersect=lambda start, end: _intersect_vertical(start, end, float(width)),
    )
    clipped = _clip_polygon_against_edge(
        clipped,
        inside=lambda point: point[1] >= 0.0,
        intersect=lambda start, end: _intersect_horizontal(start, end, 0.0),
    )
    clipped = _clip_polygon_against_edge(
        clipped,
        inside=lambda point: point[1] <= float(height),
        intersect=lambda start, end: _intersect_horizontal(start, end, float(height)),
    )
    return clipped


def estimate_overlap_ratio(points: Sequence[Point], *, width: int, height: int) -> float:
    if width <= 0 or height <= 0:
        return 0.0
    clipped = clip_polygon_to_frame(points, width=width, height=height)
    ratio = polygon_area(clipped) / float(width * height)
    return max(0.0, min(1.0, ratio))


def _require_cv2() -> Any:
    try:
        import cv2
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "OpenCV is required for camera testing. Install it with "
            "`pip install -e '.[vision]'` or `pip install opencv-python`."
        ) from exc
    return cv2


def _draw_polygon(frame: Any, points: Sequence[Point], *, color: tuple[int, int, int]) -> None:
    if len(points) < 3:
        return

    cv2 = _require_cv2()
    import numpy as np

    polygon = np.array(points, dtype="float32").reshape((-1, 1, 2))
    cv2.polylines(frame, [polygon.astype("int32")], isClosed=True, color=color, thickness=2, lineType=cv2.LINE_AA)


def _add_hud(frame: Any, title: str, lines: Sequence[str]) -> Any:
    cv2 = _require_cv2()

    annotated = frame.copy()
    hud_height = 32 + (len(lines) * 22)
    overlay = annotated.copy()
    cv2.rectangle(overlay, (0, 0), (annotated.shape[1], hud_height), (10, 10, 10), thickness=-1)
    cv2.addWeighted(overlay, 0.65, annotated, 0.35, 0.0, dst=annotated)

    cv2.putText(
        annotated,
        title,
        (14, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

    for line_number, line in enumerate(lines, start=1):
        cv2.putText(
            annotated,
            line,
            (14, 24 + (line_number * 22)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (215, 240, 215),
            1,
            cv2.LINE_AA,
        )
    return annotated


def _open_capture(index: int, *, width: int | None, height: int | None) -> Any:
    cv2 = _require_cv2()

    capture = cv2.VideoCapture(index)
    if width is not None:
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height is not None:
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return capture


def _build_overlay_frame(frame_a: Any, frame_b: Any, homography: Any) -> Any:
    cv2 = _require_cv2()
    import numpy as np

    height_b, width_b = frame_b.shape[:2]
    warped_a = cv2.warpPerspective(frame_a, homography, (width_b, height_b))
    mask = cv2.warpPerspective(
        np.full(frame_a.shape[:2], 255, dtype="uint8"),
        homography,
        (width_b, height_b),
    )
    blended = cv2.addWeighted(warped_a, 0.5, frame_b, 0.5, 0.0)
    overlay = frame_b.copy()
    overlay[mask > 0] = blended[mask > 0]
    return overlay


def estimate_camera_overlap(
    frame_a: Any,
    frame_b: Any,
    *,
    max_features: int = 1_500,
    ratio_test: float = 0.75,
    min_matches: int = 25,
) -> OverlapEstimate:
    cv2 = _require_cv2()
    import numpy as np

    orb = cv2.ORB_create(nfeatures=max_features)
    gray_a = cv2.cvtColor(frame_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(frame_b, cv2.COLOR_BGR2GRAY)
    keypoints_a, descriptors_a = orb.detectAndCompute(gray_a, None)
    keypoints_b, descriptors_b = orb.detectAndCompute(gray_b, None)

    if descriptors_a is None or descriptors_b is None:
        return OverlapEstimate(
            matches=0,
            inliers=0,
            overlap_ratio_a_in_b=0.0,
            overlap_ratio_b_in_a=0.0,
            status="Not enough texture or light to extract matching features from both cameras.",
            projected_a_in_b=[],
            projected_b_in_a=[],
        )

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    raw_matches = matcher.knnMatch(descriptors_a, descriptors_b, k=2)
    good_matches = []
    for pair in raw_matches:
        if len(pair) < 2:
            continue
        first, second = pair
        if first.distance < ratio_test * second.distance:
            good_matches.append(first)

    required_matches = max(min_matches, 4)
    if len(good_matches) < required_matches:
        return OverlapEstimate(
            matches=len(good_matches),
            inliers=0,
            overlap_ratio_a_in_b=0.0,
            overlap_ratio_b_in_a=0.0,
            status=f"Need at least {required_matches} good matches to estimate overlap (have {len(good_matches)}).",
            projected_a_in_b=[],
            projected_b_in_a=[],
        )

    source_points = np.float32([keypoints_a[match.queryIdx].pt for match in good_matches]).reshape(-1, 1, 2)
    destination_points = np.float32([keypoints_b[match.trainIdx].pt for match in good_matches]).reshape(-1, 1, 2)
    homography, mask = cv2.findHomography(source_points, destination_points, cv2.RANSAC, 4.0)

    if homography is None or mask is None:
        return OverlapEstimate(
            matches=len(good_matches),
            inliers=0,
            overlap_ratio_a_in_b=0.0,
            overlap_ratio_b_in_a=0.0,
            status="Feature matches were found, but homography fitting failed.",
            projected_a_in_b=[],
            projected_b_in_a=[],
        )

    inliers = int(mask.ravel().sum())
    required_inliers = max(10, min_matches // 2)
    if inliers < required_inliers:
        return OverlapEstimate(
            matches=len(good_matches),
            inliers=inliers,
            overlap_ratio_a_in_b=0.0,
            overlap_ratio_b_in_a=0.0,
            status=f"Homography looks unstable: only {inliers} inlier matches out of {len(good_matches)}.",
            projected_a_in_b=[],
            projected_b_in_a=[],
        )

    height_a, width_a = frame_a.shape[:2]
    height_b, width_b = frame_b.shape[:2]
    corners_a = np.float32(
        [[0.0, 0.0], [float(width_a), 0.0], [float(width_a), float(height_a)], [0.0, float(height_a)]]
    ).reshape(-1, 1, 2)
    corners_b = np.float32(
        [[0.0, 0.0], [float(width_b), 0.0], [float(width_b), float(height_b)], [0.0, float(height_b)]]
    ).reshape(-1, 1, 2)

    projected_a_in_b = cv2.perspectiveTransform(corners_a, homography).reshape(-1, 2)
    try:
        inverse_homography = np.linalg.inv(homography)
    except np.linalg.LinAlgError:
        return OverlapEstimate(
            matches=len(good_matches),
            inliers=inliers,
            overlap_ratio_a_in_b=0.0,
            overlap_ratio_b_in_a=0.0,
            status="Homography was singular, so the overlap estimate was discarded.",
            projected_a_in_b=[],
            projected_b_in_a=[],
        )
    projected_b_in_a = cv2.perspectiveTransform(corners_b, inverse_homography).reshape(-1, 2)

    projected_a_points = [(float(x), float(y)) for x, y in projected_a_in_b]
    projected_b_points = [(float(x), float(y)) for x, y in projected_b_in_a]
    overlap_ratio_a_in_b = estimate_overlap_ratio(projected_a_points, width=width_b, height=height_b)
    overlap_ratio_b_in_a = estimate_overlap_ratio(projected_b_points, width=width_a, height=height_a)
    overlay_frame = _build_overlay_frame(frame_a, frame_b, homography)

    return OverlapEstimate(
        matches=len(good_matches),
        inliers=inliers,
        overlap_ratio_a_in_b=overlap_ratio_a_in_b,
        overlap_ratio_b_in_a=overlap_ratio_b_in_a,
        status=(
            f"{inliers}/{len(good_matches)} inliers, overlap "
            f"~{((overlap_ratio_a_in_b + overlap_ratio_b_in_a) * 50.0):.1f}%."
        ),
        projected_a_in_b=projected_a_points,
        projected_b_in_a=projected_b_points,
        overlay_frame=overlay_frame,
    )


def run_camera_overlap_viewer(
    camera_indexes: Sequence[int],
    *,
    width: int | None = None,
    height: int | None = None,
    refresh_every: int = 10,
    max_features: int = 1_500,
    min_matches: int = 25,
) -> int:
    cv2 = _require_cv2()

    selected = normalize_camera_indexes(camera_indexes)
    if len(selected) < 2:
        raise ValueError("Need at least two camera indexes to compare overlap.")
    selected = selected[:2]

    captures: list[tuple[int, Any]] = []
    try:
        for camera_index in selected:
            capture = _open_capture(camera_index, width=width, height=height)
            if not capture.isOpened():
                raise RuntimeError(f"Could not open camera index {camera_index}.")
            captures.append((camera_index, capture))

        print("Press q or Esc to quit. Press r to force a fresh overlap estimate.")
        frame_count = 0
        current_estimate: OverlapEstimate | None = None

        window_left = f"Doggo Camera {selected[0]}"
        window_right = f"Doggo Camera {selected[1]}"
        window_overlay = "Doggo Camera Overlap"
        cv2.namedWindow(window_left, cv2.WINDOW_NORMAL)
        cv2.namedWindow(window_right, cv2.WINDOW_NORMAL)
        cv2.namedWindow(window_overlay, cv2.WINDOW_NORMAL)

        while True:
            frames: dict[int, Any] = {}
            for camera_index, capture in captures:
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(f"Camera {camera_index} stopped returning frames.")
                frames[camera_index] = frame

            left_index, right_index = selected
            left_frame = frames[left_index]
            right_frame = frames[right_index]

            refresh_due = (frame_count % max(refresh_every, 1)) == 0
            if refresh_due or current_estimate is None:
                current_estimate = estimate_camera_overlap(
                    left_frame,
                    right_frame,
                    max_features=max_features,
                    min_matches=min_matches,
                )

            left_view = left_frame.copy()
            right_view = right_frame.copy()
            overlay_view = (
                current_estimate.overlay_frame.copy()
                if current_estimate is not None and current_estimate.overlay_frame is not None
                else right_frame.copy()
            )

            if current_estimate is not None:
                _draw_polygon(left_view, current_estimate.projected_b_in_a, color=(0, 180, 255))
                _draw_polygon(right_view, current_estimate.projected_a_in_b, color=(0, 220, 0))
                _draw_polygon(overlay_view, current_estimate.projected_a_in_b, color=(0, 220, 0))

            left_lines = [
                f"Resolution: {left_frame.shape[1]}x{left_frame.shape[0]}",
                f"Estimated overlap: {current_estimate.overlap_percent:.1f}%"
                if current_estimate is not None
                else "Estimated overlap: waiting...",
                f"Orange outline = camera {right_index} footprint",
            ]
            right_lines = [
                f"Resolution: {right_frame.shape[1]}x{right_frame.shape[0]}",
                f"Matches/Inliers: {current_estimate.matches}/{current_estimate.inliers}"
                if current_estimate is not None
                else "Matches/Inliers: waiting...",
                f"Green outline = camera {left_index} footprint",
            ]
            overlay_lines = [
                f"Blend: camera {left_index} warped onto camera {right_index}",
                current_estimate.status if current_estimate is not None else "Waiting for frames...",
                "Keys: q / Esc quit, r refresh",
            ]

            cv2.imshow(window_left, _add_hud(left_view, f"Camera {left_index}", left_lines))
            cv2.imshow(window_right, _add_hud(right_view, f"Camera {right_index}", right_lines))
            cv2.imshow(window_overlay, _add_hud(overlay_view, "Estimated Overlap", overlay_lines))

            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break
            if key == ord("r"):
                current_estimate = None

            frame_count += 1

        return 0
    finally:
        for _, capture in captures:
            capture.release()
        cv2.destroyAllWindows()
