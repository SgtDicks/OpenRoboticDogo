from __future__ import annotations

from dataclasses import dataclass

from doggo.models import TeleopCommand


@dataclass(slots=True)
class GaitPlannerStatus:
    ready: bool
    reason: str


class PlaceholderGaitPlanner:
    """Walking is intentionally deferred until calibration and geometry exist."""

    def status(self) -> GaitPlannerStatus:
        return GaitPlannerStatus(
            ready=False,
            reason="Walking is scaffolded only. We need calibrated joints and measured leg geometry first.",
        )

    def accept(self, command: TeleopCommand) -> GaitPlannerStatus:
        _ = command
        return self.status()
