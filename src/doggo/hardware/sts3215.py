from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Sequence

import serial

BROADCAST_ID = 0xFE

INST_PING = 0x01
INST_READ = 0x02
INST_WRITE = 0x03
INST_REG_WRITE = 0x04
INST_ACTION = 0x05
INST_SYNC_READ = 0x82
INST_SYNC_WRITE = 0x83

STS_MODEL_L = 3
STS_ID = 5
STS_TORQUE_ENABLE = 40
STS_ACC = 41
STS_GOAL_POSITION_L = 42
STS_LOCK = 55
STS_PRESENT_POSITION_L = 56
STS_PRESENT_SPEED_L = 58
STS_PRESENT_VOLTAGE = 62
STS_PRESENT_TEMPERATURE = 63

ERRBIT_VOLTAGE = 1
ERRBIT_ANGLE = 2
ERRBIT_OVERHEAT = 4
ERRBIT_OVERCURRENT = 8
ERRBIT_OVERLOAD = 32


class ServoBusError(RuntimeError):
    """Base error for servo bus operations."""


class ServoTimeoutError(ServoBusError):
    """Raised when the servo does not answer in time."""


class ServoPacketError(ServoBusError):
    """Raised when the servo returns an invalid packet."""


class ServoStatusError(ServoBusError):
    """Raised when the servo returns an error bit."""


@dataclass(slots=True)
class ServoScanResult:
    servo_id: int
    model_number: int
    position: int | None = None
    voltage: int | None = None
    temperature: int | None = None

    def to_dict(self) -> dict[str, int | None]:
        return {
            "servo_id": self.servo_id,
            "model_number": self.model_number,
            "position": self.position,
            "voltage": self.voltage,
            "temperature": self.temperature,
        }


def compute_checksum(packet_without_checksum: Sequence[int]) -> int:
    return (~sum(packet_without_checksum[2:]) & 0xFF)


def build_packet(servo_id: int, instruction: int, parameters: Sequence[int] = ()) -> bytes:
    length = len(parameters) + 2
    packet = [0xFF, 0xFF, servo_id, length, instruction, *parameters]
    packet.append(compute_checksum(packet))
    return bytes(packet)


def parse_status_packet(packet: bytes) -> tuple[int, int, bytes]:
    if len(packet) < 6:
        raise ServoPacketError("Packet too short.")
    if packet[0] != 0xFF or packet[1] != 0xFF:
        raise ServoPacketError("Missing packet header.")
    expected_length = packet[3] + 4
    if len(packet) != expected_length:
        raise ServoPacketError(
            f"Packet length mismatch. Expected {expected_length}, got {len(packet)}."
        )
    expected_checksum = compute_checksum(list(packet[:-1]))
    if packet[-1] != expected_checksum:
        raise ServoPacketError("Checksum mismatch.")
    servo_id = packet[2]
    error = packet[4]
    parameters = packet[5:-1]
    return servo_id, error, parameters


def decode_error_bits(error: int) -> list[str]:
    messages: list[str] = []
    if error & ERRBIT_VOLTAGE:
        messages.append("input voltage error")
    if error & ERRBIT_ANGLE:
        messages.append("angle error")
    if error & ERRBIT_OVERHEAT:
        messages.append("overheat error")
    if error & ERRBIT_OVERCURRENT:
        messages.append("overcurrent error")
    if error & ERRBIT_OVERLOAD:
        messages.append("overload error")
    return messages


def to_signed_15(value: int) -> int:
    if value & (1 << 15):
        return -(value & ~(1 << 15))
    return value


def split_u16(value: int) -> tuple[int, int]:
    return value & 0xFF, (value >> 8) & 0xFF


class Sts3215Bus:
    def __init__(self, device: str, baud_rate: int = 1_000_000, timeout_seconds: float = 0.05):
        self.device = device
        self.baud_rate = baud_rate
        self.timeout_seconds = timeout_seconds
        self._serial: serial.Serial | None = None

    def open(self) -> None:
        if self._serial and self._serial.is_open:
            return
        self._serial = serial.Serial(
            port=self.device,
            baudrate=self.baud_rate,
            bytesize=serial.EIGHTBITS,
            timeout=0.002,
        )
        self._serial.reset_input_buffer()

    def close(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    def __enter__(self) -> "Sts3215Bus":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    def _require_port(self) -> serial.Serial:
        if not self._serial or not self._serial.is_open:
            raise ServoBusError("Servo bus is not open.")
        return self._serial

    def _write_and_read(self, packet: bytes, expected_id: int | None) -> tuple[int, int, bytes]:
        port = self._require_port()
        port.reset_input_buffer()
        port.write(packet)
        if expected_id is None:
            return BROADCAST_ID, 0, b""
        return self._read_status_packet(expected_id)

    def _read_status_packet(self, expected_id: int) -> tuple[int, int, bytes]:
        port = self._require_port()
        deadline = time.monotonic() + self.timeout_seconds
        buffer = bytearray()

        while time.monotonic() < deadline:
            chunk = port.read(64)
            if chunk:
                buffer.extend(chunk)

            while len(buffer) >= 2 and (buffer[0] != 0xFF or buffer[1] != 0xFF):
                del buffer[0]

            if len(buffer) >= 4:
                packet_length = buffer[3] + 4
                if len(buffer) >= packet_length:
                    packet = bytes(buffer[:packet_length])
                    servo_id, error, parameters = parse_status_packet(packet)
                    if servo_id != expected_id:
                        del buffer[:packet_length]
                        continue
                    if error:
                        details = ", ".join(decode_error_bits(error)) or f"error bits={error}"
                        raise ServoStatusError(f"Servo {servo_id} returned {details}.")
                    return servo_id, error, parameters

        raise ServoTimeoutError(f"No response from servo {expected_id} on {self.device}.")

    def ping(self, servo_id: int) -> ServoScanResult:
        self._write_and_read(build_packet(servo_id, INST_PING), expected_id=servo_id)
        model_number = self.read_u16(servo_id, STS_MODEL_L)
        position = self.read_present_position(servo_id)
        voltage = self.read_u8(servo_id, STS_PRESENT_VOLTAGE)
        temperature = self.read_u8(servo_id, STS_PRESENT_TEMPERATURE)
        return ServoScanResult(
            servo_id=servo_id,
            model_number=model_number,
            position=position,
            voltage=voltage,
            temperature=temperature,
        )

    def scan(self, start_id: int = 1, end_id: int = 12) -> list[ServoScanResult]:
        found: list[ServoScanResult] = []
        for servo_id in range(start_id, end_id + 1):
            try:
                found.append(self.ping(servo_id))
            except ServoBusError:
                continue
        return found

    def read_bytes(self, servo_id: int, address: int, length: int) -> bytes:
        parameters = (address, length)
        _, _, payload = self._write_and_read(
            build_packet(servo_id, INST_READ, parameters),
            expected_id=servo_id,
        )
        if len(payload) != length:
            raise ServoPacketError(
                f"Servo {servo_id} returned {len(payload)} bytes for address {address}, expected {length}."
            )
        return payload

    def read_u8(self, servo_id: int, address: int) -> int:
        return self.read_bytes(servo_id, address, 1)[0]

    def read_u16(self, servo_id: int, address: int) -> int:
        low, high = self.read_bytes(servo_id, address, 2)
        return low | (high << 8)

    def write_bytes(
        self,
        servo_id: int,
        address: int,
        data: Sequence[int],
        *,
        expect_status: bool = True,
    ) -> None:
        parameters = (address, *data)
        expected_id = None if not expect_status or servo_id == BROADCAST_ID else servo_id
        self._write_and_read(build_packet(servo_id, INST_WRITE, parameters), expected_id)

    def write_u8(self, servo_id: int, address: int, value: int) -> None:
        self.write_bytes(servo_id, address, (value & 0xFF,))

    def unlock_eeprom(self, servo_id: int) -> None:
        self.write_u8(servo_id, STS_LOCK, 0)

    def lock_eeprom(self, servo_id: int) -> None:
        self.write_u8(servo_id, STS_LOCK, 1)

    def set_servo_id(self, current_id: int, new_id: int) -> None:
        self.unlock_eeprom(current_id)
        self.write_u8(current_id, STS_ID, new_id)
        self.lock_eeprom(new_id)

    def set_torque_enabled(self, servo_id: int, enabled: bool) -> None:
        self.write_u8(servo_id, STS_TORQUE_ENABLE, 1 if enabled else 0)

    def read_present_position(self, servo_id: int) -> int:
        return to_signed_15(self.read_u16(servo_id, STS_PRESENT_POSITION_L))

    def read_present_speed(self, servo_id: int) -> int:
        return to_signed_15(self.read_u16(servo_id, STS_PRESENT_SPEED_L))

    def move(self, servo_id: int, position: int, speed: int, acceleration: int) -> None:
        low, high = split_u16(position)
        speed_low, speed_high = split_u16(speed)
        payload = (acceleration & 0xFF, low, high, 0, 0, speed_low, speed_high)
        self.write_bytes(servo_id, STS_ACC, payload)

    def sync_move(
        self,
        commands: Iterable[tuple[int, int]],
        *,
        speed: int,
        acceleration: int,
    ) -> None:
        speed_low, speed_high = split_u16(speed)
        parameters: list[int] = [STS_ACC, 7]
        for servo_id, position in commands:
            low, high = split_u16(position)
            parameters.extend(
                [
                    servo_id,
                    acceleration & 0xFF,
                    low,
                    high,
                    0,
                    0,
                    speed_low,
                    speed_high,
                ]
            )
        packet = build_packet(BROADCAST_ID, INST_SYNC_WRITE, parameters)
        self._write_and_read(packet, expected_id=None)
