from __future__ import annotations

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
