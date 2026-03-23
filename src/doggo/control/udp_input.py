from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from doggo.models import TeleopCommand

if TYPE_CHECKING:
    from doggo.control.supervisor import ControlSupervisor


class Esp32UdpInputProtocol(asyncio.DatagramProtocol):
    def __init__(self, supervisor: "ControlSupervisor") -> None:
        self.supervisor = supervisor
        self.loop = asyncio.get_running_loop()
        self.last_remote: tuple[str, int] | None = None

    def datagram_received(self, data: bytes, addr) -> None:
        self.last_remote = addr
        try:
            payload = json.loads(data.decode("utf-8"))
            payload.setdefault("source", "esp32")
            command = TeleopCommand.model_validate(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
            return
        self.loop.create_task(self.supervisor.apply_teleop(command))


async def start_udp_input(
    supervisor: "ControlSupervisor",
    host: str,
    port: int,
) -> tuple[asyncio.DatagramTransport, Esp32UdpInputProtocol]:
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: Esp32UdpInputProtocol(supervisor),
        local_addr=(host, port),
    )
    return transport, protocol
