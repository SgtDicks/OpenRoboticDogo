from __future__ import annotations

import asyncio
from pathlib import Path

from doggo.config import AppConfig, load_config
from doggo.control.supervisor import ControlSupervisor
from doggo.control.udp_input import Esp32UdpInputProtocol, start_udp_input
from doggo.hardware.sts3215 import Sts3215Bus


class DoggoRuntime:
    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)
        self.runtime_state_path = self.config_path.with_suffix(".state.json")
        self.config: AppConfig = load_config(self.config_path)
        self.servo_bus: Sts3215Bus | None = None
        self.supervisor: ControlSupervisor | None = None
        self.udp_transport: asyncio.DatagramTransport | None = None
        self.udp_protocol: Esp32UdpInputProtocol | None = None

    async def start(self) -> None:
        servo_bus: Sts3215Bus | None = None
        startup_warning: str | None = None
        if self.config.servo_bus.enabled:
            servo_bus = Sts3215Bus(
                device=self.config.servo_bus.device,
                baud_rate=self.config.servo_bus.baud_rate,
                timeout_seconds=self.config.servo_bus.timeout_seconds,
            )
            try:
                await asyncio.to_thread(servo_bus.open)
            except Exception as exc:  # pragma: no cover - depends on local hardware availability
                startup_warning = f"Servo bus unavailable at startup: {exc}"
                servo_bus = None

        self.servo_bus = servo_bus
        self.supervisor = ControlSupervisor(
            self.config,
            servo_bus,
            runtime_state_path=self.runtime_state_path,
        )
        if startup_warning:
            self.supervisor.last_message = startup_warning
        await self.supervisor.start()

        if self.config.esp32.enabled:
            self.udp_transport, self.udp_protocol = await start_udp_input(
                self.supervisor,
                self.config.esp32.listen_host,
                self.config.esp32.listen_port,
            )

    async def stop(self) -> None:
        if self.udp_transport:
            self.udp_transport.close()
            self.udp_transport = None
            self.udp_protocol = None

        if self.supervisor:
            await self.supervisor.stop()

        if self.servo_bus:
            await asyncio.to_thread(self.servo_bus.close)
            self.servo_bus = None
