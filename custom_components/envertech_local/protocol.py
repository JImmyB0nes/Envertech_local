"""Envertech inverter wire protocol (stdlib only, no third-party dependency).

The integration used to depend on the ``envertech-local`` PyPI package for this.
That package pulls in ``netifaces``, a C extension whose last release (0.11.0,
May 2021) ships wheels only up to CPython 3.9 and whose ``setup.py`` still uses
``distutils`` - removed in Python 3.12. On Home Assistant OS 18.4 / Core 2026.11
(Python 3.14) the dependency therefore cannot be installed, so importing the
library raised ``ModuleNotFoundError`` and the config flow answered with
``500 Internal Server Error``.

The protocol helpers below are a faithful, dependency-free reimplementation of
what the integration actually needs from that library (command building,
checksum, module data parsing). Frame layout and scaling factors are unchanged,
so the values reported by the sensors are identical.

Original protocol library: https://pypi.org/project/envertech-local/
(Kaiserdragon2, MIT license).
"""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

# Control codes (decimal) used by the LOCALCON protocol.
CONTROL_DATA_REQUEST = 4215  # 0x1077
CONTROL_BREAK = 4161  # 0x1041
CONTROL_POWER_CONTROL = 4407  # 0x1137
CONTROL_DATA_RESPONSE = 4177  # 0x1051
CONTROL_ACK = 4102  # 0x1006

END_BYTE = 0x16
CHECKSUM_SALT = 85


def check_cs(byte_array: bytes | bytearray) -> int:
    """Return the frame checksum (simple additive checksum with salt)."""
    return (sum(byte_array) + CHECKSUM_SALT) & 0xFF


def build_inverter_command(
    current_id_hex: str,
    control_code_int: int,
    payload: bytes = b"",
    payload_padding: int = 0,
) -> bytes:
    """Build a protocol-compliant inverter command frame.

    ``current_id_hex`` is the 8-character hex serial of the inverter, e.g.
    ``"9A1B2C3D"``. Returns ``b""`` for malformed input so a bad serial can
    never crash a running stream.
    """
    if len(current_id_hex) != 8:
        _LOGGER.error(
            "Inverter ID hex string must be exactly 8 characters long, got %s",
            len(current_id_hex),
        )
        return b""
    try:
        control_code_bytes = control_code_int.to_bytes(2, "big")
    except OverflowError:
        _LOGGER.error("Control code %s is out of range", control_code_int)
        return b""

    data = bytearray()
    data.append(0x68)
    data.append(0x00)  # length placeholder (high byte)
    data.append(0x00)  # length placeholder (low byte)
    data.append(0x68)
    data += control_code_bytes
    try:
        data += bytes.fromhex(current_id_hex)
    except ValueError:
        _LOGGER.error("Inverter ID %r is not valid hex", current_id_hex)
        return b""
    data += payload
    data += bytes([0x00] * payload_padding)

    # Length covers everything after byte 3 plus checksum and end byte.
    total_length = len(data) + 2
    data[1] = (total_length >> 8) & 0xFF
    data[2] = total_length & 0xFF

    data.append(check_cs(data))
    data.append(END_BYTE)
    return bytes(data)


def build_inverter_request(current_id_hex: str) -> bytes:
    """Data request (control code 4215 / 0x1077)."""
    return build_inverter_command(
        current_id_hex=current_id_hex,
        control_code_int=CONTROL_DATA_REQUEST,
        payload_padding=20,
    )


def build_inverter_break_command(current_id_hex: str) -> bytes:
    """Disconnect/break command (control code 4161 / 0x1041)."""
    return build_inverter_command(
        current_id_hex=current_id_hex,
        control_code_int=CONTROL_BREAK,
        payload_padding=10,
    )


def build_inverter_powercontrol_command(current_id_hex: str, level: int) -> bytes:
    """Power control command (control code 4407 / 0x1137)."""
    if not 0 <= level <= 255:
        _LOGGER.error("Level %s out of valid byte range (0-255).", level)
        return b""
    return build_inverter_command(
        current_id_hex=current_id_hex,
        control_code_int=CONTROL_POWER_CONTROL,
        payload=bytes([level]),
    )


def _to_int16(byte1: int, byte2: int) -> int:
    return byte1 * 256 + byte2


def _to_int32(byte1: int, byte2: int, byte3: int, byte4: int) -> int:
    return (byte1 << 24) + (byte2 << 16) + (byte3 << 8) + byte4


def parse_module_data(raw: list[int], offset: dict[str, int]) -> dict[str, object] | None:
    """Parse one 32-byte module block; ``None`` if the block is truncated."""
    try:
        return {
            "mi_sn": "".join(
                f"{raw[offset['mi_sn'] + i]:02x}" for i in range(4)
            ),
            "input_voltage": _to_int16(
                raw[offset["input_voltage"]], raw[offset["input_voltage"] + 1]
            )
            * 64
            / 32768,
            "power": _to_int16(raw[offset["power"]], raw[offset["power"] + 1])
            * 512
            / 32768,
            "energy": _to_int32(
                raw[offset["energy"]],
                raw[offset["energy"] + 1],
                raw[offset["energy"] + 2],
                raw[offset["energy"] + 3],
            )
            * 4
            / 32768,
            "temperature": _to_int16(
                raw[offset["temperature"]], raw[offset["temperature"] + 1]
            )
            * 256
            / 32768
            - 40,
            "grid_voltage": _to_int16(
                raw[offset["grid_voltage"]], raw[offset["grid_voltage"] + 1]
            )
            * 512
            / 32768,
            "frequency": _to_int16(raw[offset["frequency"]], raw[offset["frequency"] + 1])
            * 128
            / 32768,
        }
    except IndexError:
        return None


def parse_inverter_response(
    raw: list[int],
) -> tuple[dict[str, object], int | None, int | None]:
    """Parse a raw response frame into ``(data, number_of_panels, control_code)``.

    Panel values are keyed ``"<index>_<key>"``. Totals are summed into
    ``total_power`` / ``total_energy``. Frames that carry no data (acknowledgements,
    unknown control codes) yield an empty dict.
    """
    if not raw or len(raw) < 22:
        return {}, None, None

    control_code = int.from_bytes(raw[4:6], "big")
    data: dict[str, object] = {}
    number_of_panels: int | None = None

    if control_code == CONTROL_DATA_RESPONSE:
        number_of_panels = (len(raw) - 22) // 32
        for i in range(number_of_panels):
            base = 20 + i * 32
            offset = {
                "mi_sn": base,
                "input_voltage": base + 6,
                "power": base + 8,
                "energy": base + 10,
                "temperature": base + 14,
                "grid_voltage": base + 16,
                "frequency": base + 18,
            }
            parsed = parse_module_data(raw, offset)
            if not parsed:
                continue
            for key, value in parsed.items():
                data[f"{i}_{key}"] = (
                    round(value, 2) if isinstance(value, (int, float)) else value
                )

        for key in ("power", "energy"):
            total = sum(data.get(f"{i}_{key}", 0) for i in range(number_of_panels))
            data[f"total_{key}"] = round(total, 2)

        data["firmware_version"] = f"{raw[10]}/{raw[12]}"

    elif control_code == CONTROL_ACK:
        # Command acknowledged, no payload.
        data = {}
        number_of_panels = None

    else:
        data = {}
        number_of_panels = None

    return data, number_of_panels, control_code
