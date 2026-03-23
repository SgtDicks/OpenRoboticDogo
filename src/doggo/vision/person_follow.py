from __future__ import annotations

from dataclasses import dataclass

from doggo.config import VisionConfig


@dataclass(slots=True)
class VisionStatus:
    enabled: bool
    target: str
    ready: bool
    reason: str


class PersonFollowPipeline:
    def __init__(self, config: VisionConfig) -> None:
        self.config = config

    def status(self) -> VisionStatus:
        if not self.config.enabled:
            return VisionStatus(
                enabled=False,
                target=self.config.track_target,
                ready=False,
                reason="Vision is disabled in config.",
            )
        return VisionStatus(
            enabled=True,
            target=self.config.track_target,
            ready=False,
            reason="Pipeline scaffolded. Add OpenCV detector/tracker after locomotion bring-up.",
        )
