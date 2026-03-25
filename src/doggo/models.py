from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class TeleopAxes(BaseModel):
    forward: float = Field(default=0.0, ge=-1.0, le=1.0)
    strafe: float = Field(default=0.0, ge=-1.0, le=1.0)
    turn: float = Field(default=0.0, ge=-1.0, le=1.0)


class TeleopButtons(BaseModel):
    stand: bool = False
    relax: bool = False
    stop: bool = False


class TeleopCommand(BaseModel):
    source: str = "web"
    mode: Literal["teleop", "stand", "relax", "stop"] = "teleop"
    axes: TeleopAxes = Field(default_factory=TeleopAxes)
    buttons: TeleopButtons = Field(default_factory=TeleopButtons)
    timestamp_ms: int | None = None


class ServoMoveRequest(BaseModel):
    position: int
    speed: int | None = None
    acceleration: int | None = None


class ServoAssignmentRequest(BaseModel):
    current_id: int
    new_id: int


class MotionFrame(BaseModel):
    timestamp_ms: int = Field(default=0, ge=0)
    positions: dict[int, int] = Field(default_factory=dict)


class MotionRecording(BaseModel):
    name: str = "last_capture"
    captured_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: int = Field(default=0, ge=0)
    sample_ms: int = Field(default=100, ge=20)
    stop_reason: Literal["duration", "manual", "idle", "unknown"] = "unknown"
    idle_stop_seconds: float | None = Field(default=None, ge=0.0)
    idle_threshold_ticks: int = Field(default=15, ge=1)
    servo_ids: list[int] = Field(default_factory=list)
    frames: list[MotionFrame] = Field(default_factory=list)

    @property
    def frame_count(self) -> int:
        return len(self.frames)


class MotionRecordRequest(BaseModel):
    name: str = "last_capture"
    duration_ms: int = Field(default=10_000, ge=500, le=300_000)
    sample_ms: int = Field(default=100, ge=20, le=1_000)
    idle_stop_seconds: float | None = Field(default=None, ge=0.0, le=600.0)
    idle_threshold_ticks: int = Field(default=15, ge=1, le=500)


class MotionPlaybackRequest(BaseModel):
    name: str | None = None
    speed: int | None = Field(default=None, ge=1)
    acceleration: int | None = Field(default=None, ge=0)


class MotionSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
