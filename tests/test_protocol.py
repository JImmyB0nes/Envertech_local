"""Unit tests for the dependency-free protocol helpers.

The helpers used to come from the ``envertech-local`` package. These tests
assert the byte-exact and value-exact equivalence of the reimplementation so a
future change cannot silently alter what the sensors report.
"""

from __future__ import annotations

from tests._loader import load

protocol = load("protocol")

SN = "12345678"


def _frame(panels: int, control_code: int = 4177) -> list[int]:
    """Build a synthetic data-response frame with ``panels`` module blocks."""
    raw = [
        0x68,
        0x00,
        0x16,
        0x68,
        (control_code >> 8) & 0xFF,
        control_code & 0xFF,
        0x9A,
        0x1B,
        0x2C,
        0x3D,
        12,
        34,
        56,
        78,
        0,
        0,
        0,
        0,
        0,
        0,
    ]
    for i in range(panels):
        block = [0] * 32
        block[0:4] = [0xAA, 0xBB, 0xCC, 0xDD]  # module serial
        block[6:8] = [(i * 10 + 5) >> 8 & 0xFF, (i * 10 + 5) & 0xFF]
        block[8:10] = [(i * 100 + 11) >> 8 & 0xFF, (i * 100 + 11) & 0xFF]
        block[10:14] = [0, 0, (i * 1000 + 12) >> 8 & 0xFF, (i * 1000 + 12) & 0xFF]
        block[14:16] = [0, 60]
        block[16:18] = [(i * 7 + 200) >> 8 & 0xFF, (i * 7 + 200) & 0xFF]
        block[18:20] = [0, 50]
        raw += block
    raw += [0, 0]  # checksum + end byte
    return raw


def test_request_frame_matches_the_protocol_spec() -> None:
    frame = protocol.build_inverter_request(SN)
    assert frame[0] == 0x68 and frame[3] == 0x68
    assert frame[4:6] == b"\x10\x77"  # 4215 = data request
    assert frame[6:10].hex() == SN.lower()
    assert frame[-1] == 0x16
    # The length field counts the whole frame, checksum and end byte included.
    assert int.from_bytes(frame[1:3], "big") == len(frame)


def test_break_frame_and_length_field() -> None:
    frame = protocol.build_inverter_break_command(SN)
    assert frame[4:6] == b"\x10\x41"  # 4161 = break
    assert frame[-1] == 0x16
    assert int.from_bytes(frame[1:3], "big") == len(frame)


def test_checksum_is_additive_with_salt() -> None:
    assert protocol.check_cs(bytearray([0, 0, 0])) == 85
    assert protocol.check_cs(bytearray([1, 2, 3])) == (6 + 85) & 0xFF


def test_invalid_input_yields_empty_frame_instead_of_raising() -> None:
    assert protocol.build_inverter_request("1234567") == b""
    assert protocol.build_inverter_request("ZZZZZZZZ") == b""
    assert protocol.build_inverter_command(SN, 70000) == b""
    assert protocol.build_inverter_powercontrol_command(SN, 300) == b""


def test_power_control_frame_carries_the_level() -> None:
    frame = protocol.build_inverter_powercontrol_command(SN, 42)
    assert frame[4:6] == b"\x11\x37"  # 4407
    assert 42 in frame


def test_parse_reads_all_panel_values_and_totals() -> None:
    data, panels, control_code = protocol.parse_inverter_response(_frame(2))

    assert panels == 2
    assert control_code == 4177
    assert data["0_mi_sn"] == "aabbccdd"
    assert data["0_input_voltage"] == round(5 * 64 / 32768, 2)
    assert data["0_power"] == round(11 * 512 / 32768, 2)
    assert data["1_energy"] == round(1012 * 4 / 32768, 2)
    assert data["1_temperature"] == round(60 * 256 / 32768 - 40, 2)
    assert data["1_grid_voltage"] == round(207 * 512 / 32768, 2)
    assert data["1_frequency"] == round(50 * 128 / 32768, 2)
    assert data["total_power"] == round(data["0_power"] + data["1_power"], 2)
    assert data["total_energy"] == round(data["0_energy"] + data["1_energy"], 2)
    assert data["firmware_version"] == "12/56"


def test_parse_handles_ack_short_and_unknown_frames() -> None:
    assert protocol.parse_inverter_response([0x68, 0]) == ({}, None, None)
    assert protocol.parse_inverter_response([]) == ({}, None, None)

    ack = _frame(1, control_code=4102)
    assert protocol.parse_inverter_response(ack) == ({}, None, 4102)

    unknown = _frame(1, control_code=999)
    assert protocol.parse_inverter_response(unknown) == ({}, None, 999)


def test_header_only_frame_has_no_panels_and_no_crash() -> None:
    """A frame shorter than one module block must not raise or invent panels."""
    raw = _frame(1)[: 20 + 32]  # header + one block, checksum/end cut off
    data, panels, _ = protocol.parse_inverter_response(raw)
    assert panels == 0
    assert data["total_power"] == 0
    assert data["total_energy"] == 0
