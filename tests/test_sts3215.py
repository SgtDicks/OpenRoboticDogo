from unittest.mock import patch

import serial

from doggo.hardware.sts3215 import (
    BROADCAST_ID,
    INST_PING,
    INST_SYNC_WRITE,
    ServoBusError,
    Sts3215Bus,
    build_packet,
    compute_checksum,
    parse_status_packet,
)


def test_build_ping_packet() -> None:
    packet = build_packet(1, INST_PING)
    assert list(packet) == [0xFF, 0xFF, 0x01, 0x02, 0x01, 0xFB]


def test_parse_status_packet() -> None:
    raw = [0xFF, 0xFF, 0x01, 0x04, 0x00, 0x2A, 0x00]
    raw.append(compute_checksum(raw))
    servo_id, error, payload = parse_status_packet(bytes(raw))
    assert servo_id == 1
    assert error == 0
    assert payload == bytes([0x2A, 0x00])


def test_broadcast_sync_packet_checksum() -> None:
    packet = build_packet(BROADCAST_ID, INST_SYNC_WRITE, [41, 7, 1, 30, 0, 8, 0, 0, 176, 4])
    assert packet[0] == 0xFF
    assert packet[1] == 0xFF
    assert packet[2] == BROADCAST_ID
    assert packet[-1] == compute_checksum(list(packet[:-1]))


def test_open_wraps_serial_errors_as_servo_bus_error() -> None:
    bus = Sts3215Bus("COM4")

    with patch("doggo.hardware.sts3215.serial.Serial", side_effect=serial.SerialException("busy")):
        try:
            bus.open()
        except ServoBusError as exc:
            assert "Could not open servo bus on COM4" in str(exc)
        else:
            raise AssertionError("Expected ServoBusError when Serial raises SerialException")
