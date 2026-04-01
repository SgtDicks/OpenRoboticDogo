import pytest

from doggo.vision.camera_overlap import (
    CameraCoverageModel,
    clip_polygon_to_frame,
    coverage_segments,
    estimate_overlap_ratio,
    merge_coverage_segments,
    normalize_camera_indexes,
    normalize_heading_degrees,
    overlap_width_degrees,
    polygon_area,
    total_coverage_width_degrees,
)


def test_normalize_camera_indexes_uses_fallback_when_empty() -> None:
    assert normalize_camera_indexes(None) == [0, 1]
    assert normalize_camera_indexes([]) == [0, 1]


def test_normalize_camera_indexes_deduplicates_in_order() -> None:
    assert normalize_camera_indexes([1, 0, 1, 2, 0, 2]) == [1, 0, 2]


def test_clip_polygon_to_frame_bounds_points_and_preserves_area() -> None:
    clipped = clip_polygon_to_frame(
        [(-10.0, 0.0), (10.0, 0.0), (10.0, 20.0), (-10.0, 20.0)],
        width=20,
        height=20,
    )

    assert all(0.0 <= x <= 20.0 and 0.0 <= y <= 20.0 for x, y in clipped)
    assert polygon_area(clipped) == pytest.approx(200.0)


def test_estimate_overlap_ratio_reports_fraction_of_frame() -> None:
    assert estimate_overlap_ratio([(0.0, 0.0), (10.0, 0.0), (10.0, 20.0), (0.0, 20.0)], width=20, height=20) == pytest.approx(0.5)
    assert estimate_overlap_ratio([], width=20, height=20) == 0.0


def test_normalize_heading_wraps_into_expected_range() -> None:
    assert normalize_heading_degrees(190.0) == pytest.approx(-170.0)
    assert normalize_heading_degrees(-225.0) == pytest.approx(135.0)


def test_coverage_segments_split_when_crossing_angle_wrap() -> None:
    assert coverage_segments(170.0, 40.0) == [(-180.0, -170.0), (150.0, 180.0)]


def test_merge_coverage_segments_combines_touching_ranges() -> None:
    merged = merge_coverage_segments([(-80.0, 10.0), (-45.0, 45.0), (-10.0, 80.0)])
    assert merged == [(-80.0, 80.0)]


def test_three_camera_overlap_and_union_match_expected_layout() -> None:
    left = CameraCoverageModel(name="left", center_deg=-35.0, horizontal_fov_deg=90.0)
    forward = CameraCoverageModel(name="forward", center_deg=0.0, horizontal_fov_deg=90.0)
    right = CameraCoverageModel(name="right", center_deg=35.0, horizontal_fov_deg=90.0)

    assert overlap_width_degrees(left, forward) == pytest.approx(55.0)
    assert overlap_width_degrees(forward, right) == pytest.approx(55.0)
    assert overlap_width_degrees(left, right) == pytest.approx(20.0)
    assert total_coverage_width_degrees([left, forward, right]) == pytest.approx(160.0)
