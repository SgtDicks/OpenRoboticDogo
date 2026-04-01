#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import re
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8767
DEFAULT_CALIBRATION_PATH = REPO_ROOT / "config" / "calibration" / "camera_alignment.json"
DEFAULT_CAPTURE_ROOT = REPO_ROOT / "config" / "calibration" / "captures"
DATA_URL_RE = re.compile(r"^data:image/(?P<fmt>[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_name(value: str, *, fallback: str) -> str:
    cleaned = SAFE_NAME_RE.sub("-", value.strip()).strip("-.")
    return cleaned or fallback


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, object], *, status: int = 200) -> None:
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _require_cv2():
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("OpenCV is required for checkerboard validation.") from exc
    return cv2


def _require_numpy():
    try:
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("NumPy is required for checkerboard validation.") from exc
    return np


def _decode_data_url_image(data_url: str) -> bytes:
    match = DATA_URL_RE.match(data_url)
    if not match:
        raise ValueError("Image payload is not a PNG/JPEG data URL.")
    try:
        return base64.b64decode(match.group("data"))
    except binascii.Error as exc:
        raise ValueError("Image payload is not valid base64 data.") from exc


def _check_checkerboard_image(image_bytes: bytes, pattern_size: tuple[int, int]) -> dict[str, object]:
    cv2 = _require_cv2()
    np = _require_numpy()

    array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not decode image payload.")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    flags = cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE
    found = False
    corners = None
    if hasattr(cv2, "findChessboardCornersSB"):
        found, corners = cv2.findChessboardCornersSB(gray, pattern_size, flags=flags)
    if not found:
        found, corners = cv2.findChessboardCorners(
            gray,
            pattern_size,
            flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE,
        )

    height, width = image.shape[:2]
    return {
        "detected": bool(found and corners is not None),
        "resolution": {"width": int(width), "height": int(height)},
    }


class CameraToolsHandler(BaseHTTPRequestHandler):
    server_version = "DoggoCameraTools/0.1"

    @property
    def repo_root(self) -> Path:
        return self.server.repo_root  # type: ignore[attr-defined]

    @property
    def calibration_path(self) -> Path:
        return self.server.calibration_path  # type: ignore[attr-defined]

    @property
    def capture_root(self) -> Path:
        return self.server.capture_root  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        super().log_message(fmt, *args)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/scripts/camera_overlap_browser.html")
            self.end_headers()
            return

        if path == "/api/calibration/current":
            if not self.calibration_path.exists():
                _json_response(
                    self,
                    {
                        "ok": False,
                        "error": "Calibration file not found.",
                        "path": str(self.calibration_path.relative_to(self.repo_root)),
                    },
                    status=HTTPStatus.NOT_FOUND,
                )
                return

            body = self.calibration_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self._serve_static(path)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/api/calibration/current":
            if not self.calibration_path.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "Calibration file not found")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(self.calibration_path.stat().st_size))
            self.end_headers()
            return

        self._serve_static(path, send_body=False)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path not in {"/api/calibration/capture", "/api/calibration/checkerboard"}:
            _json_response(self, {"ok": False, "error": "Unknown API endpoint."}, status=HTTPStatus.NOT_FOUND)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        raw_body = self.rfile.read(content_length)

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            _json_response(self, {"ok": False, "error": f"Invalid JSON payload: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return

        try:
            if path == "/api/calibration/capture":
                response = self._save_capture(payload)
                status = HTTPStatus.CREATED
            else:
                response = self._check_checkerboard(payload)
                status = HTTPStatus.OK
        except ValueError as exc:
            _json_response(self, {"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:  # pragma: no cover - defensive
            _json_response(self, {"ok": False, "error": f"Unexpected server error: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        _json_response(self, response, status=status)

    def _save_capture(self, payload: dict[str, object]) -> dict[str, object]:
        session_name = _sanitize_name(str(payload.get("session", "")), fallback=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
        capture_label = _sanitize_name(str(payload.get("capture_label", "")), fallback="capture")
        images = payload.get("images")
        if not isinstance(images, dict) or not images:
            raise ValueError("Payload must include an `images` object with left/forward/right PNG data URLs.")

        pattern_columns = int(payload.get("pattern_columns", 0) or 0)
        pattern_rows = int(payload.get("pattern_rows", 0) or 0)
        square_size_mm = payload.get("square_size_mm")
        device_labels = payload.get("device_labels") if isinstance(payload.get("device_labels"), dict) else {}

        session_dir = self.capture_root / session_name
        session_dir.mkdir(parents=True, exist_ok=True)

        saved_files: dict[str, str] = {}
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        for role, data_url in images.items():
            if role not in {"left", "forward", "right"}:
                continue
            if not isinstance(data_url, str):
                raise ValueError(f"Image payload for {role} must be a base64 data URL.")
            match = DATA_URL_RE.match(data_url)
            if not match:
                raise ValueError(f"Image payload for {role} is not a PNG/JPEG data URL.")

            image_bytes = base64.b64decode(match.group("data"))
            file_name = f"{timestamp}_{capture_label}_{role}.png"
            destination = session_dir / file_name
            destination.write_bytes(image_bytes)
            saved_files[role] = str(destination.relative_to(self.repo_root))

        if {"left", "forward", "right"} - set(saved_files):
            raise ValueError("Capture payload must contain left, forward, and right images.")

        manifest_path = session_dir / "session_manifest.json"
        manifest: dict[str, object]
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = {
                "session": session_name,
                "created_at": datetime.now(UTC).isoformat(),
                "captures": [],
            }

        manifest["pattern_columns"] = pattern_columns
        manifest["pattern_rows"] = pattern_rows
        if square_size_mm not in ("", None):
            manifest["square_size_mm"] = square_size_mm

        captures = manifest.setdefault("captures", [])
        if not isinstance(captures, list):
            raise ValueError("Invalid manifest format: `captures` must be a list.")
        captures.append(
            {
                "label": capture_label,
                "timestamp": timestamp,
                "files": saved_files,
                "device_labels": device_labels,
            }
        )

        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "session": session_name,
            "capture_label": capture_label,
            "saved_files": saved_files,
            "manifest": str(manifest_path.relative_to(self.repo_root)),
        }

    def _check_checkerboard(self, payload: dict[str, object]) -> dict[str, object]:
        images = payload.get("images")
        if not isinstance(images, dict) or not images:
            raise ValueError("Payload must include an `images` object with left/forward/right PNG data URLs.")

        pattern_columns = int(payload.get("pattern_columns", 0) or 0)
        pattern_rows = int(payload.get("pattern_rows", 0) or 0)
        if pattern_columns <= 0 or pattern_rows <= 0:
            raise ValueError("Checkerboard pattern_columns and pattern_rows are required.")

        pattern_size = (pattern_columns, pattern_rows)
        results: dict[str, object] = {}
        detected_roles: set[str] = set()

        for role in ("left", "forward", "right"):
            data_url = images.get(role)
            if not isinstance(data_url, str):
                raise ValueError(f"Image payload for {role} must be a base64 data URL.")
            image_bytes = _decode_data_url_image(data_url)
            result = _check_checkerboard_image(image_bytes, pattern_size)
            results[role] = result
            if result["detected"]:
                detected_roles.add(role)

        return {
            "ok": True,
            "pattern_size": {
                "columns": pattern_columns,
                "rows": pattern_rows,
            },
            "results": results,
            "pairwise_overlap": {
                "left_forward": {"detected": {"left", "forward"} <= detected_roles},
                "right_forward": {"detected": {"right", "forward"} <= detected_roles},
                "all_three": {"detected": len(detected_roles) == 3},
            },
        }

    def _serve_static(self, request_path: str, *, send_body: bool = True) -> None:
        target = self.repo_root / request_path.lstrip("/")
        try:
            resolved = target.resolve(strict=True)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        try:
            resolved.relative_to(self.repo_root)
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN, "Path escapes repo root")
            return

        if resolved.is_dir():
            candidate = resolved / "index.html"
            if not candidate.exists():
                self.send_error(HTTPStatus.FORBIDDEN, "Directory listing disabled")
                return
            resolved = candidate

        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        body = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve Doggo camera tools pages and capture API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--calibration-path", type=Path, default=DEFAULT_CALIBRATION_PATH)
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), CameraToolsHandler)
    server.repo_root = args.repo_root.resolve()  # type: ignore[attr-defined]
    server.calibration_path = args.calibration_path.resolve()  # type: ignore[attr-defined]
    server.capture_root = args.capture_root.resolve()  # type: ignore[attr-defined]
    server.capture_root.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]

    print(f"Doggo camera tools server listening on http://{args.host}:{args.port}")
    print("Viewer:      /scripts/camera_overlap_browser.html")
    print("Capture:     /scripts/camera_calibration_capture.html")
    print("Calibration: /api/calibration/current")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down camera tools server...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
