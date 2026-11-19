"""LAN discovery for Envertech inverters/gateways (stdlib only).

The ``discover_devices_async`` helper of ``envertech-local`` enumerates network
interfaces through ``netifaces``. That dependency cannot be installed on current
Home Assistant OS releases (see :mod:`.protocol`), so discovery is implemented
here with plain ``socket`` + ``asyncio`` instead.

Discovery works by sending the protocol's UDP broadcast on the two LOCALCON
discovery ports and collecting the replies:

* ``48889`` - Ethernet/LOCALCON gateways, binary reply
* ``48899`` - Wi-Fi modules, comma separated text reply

Broadcasting from an unbound socket lets the kernel replicate the datagram to
every interface that has a broadcast-capable IPv4 address, which covers the
common single-subnet Home Assistant installation. Broadcasts do not cross
routers, so devices on other VLANs/subnets still need manual entry.
"""

from __future__ import annotations

import asyncio
import logging
import socket

_LOGGER = logging.getLogger(__name__)

UDP_DISCOVERY_MSG = b"LOCALCON-1508-READ"
UDP_DISCOVERY_MSG_WIFI = b"www.usr.cn"

DEST_PORTS = {
    "localcon": 48889,
    "wifi": 48899,
}

RECV_BUFFER = 1024
DEFAULT_TIMEOUT = 5.0

# "<broadcast>" lets the kernel replicate the datagram to every
# broadcast-capable interface. Overridable so the reply handling can be tested
# against a loopback peer.
BROADCAST_ADDRESS = "<broadcast>"


def decode_localcon_response(data: bytes) -> dict[str, object] | None:
    """Decode a binary LOCALCON reply into ``{ip, serial_number, mac, source}``."""
    if len(data) < 10:
        return None
    return {
        "ip": ".".join(str(b) for b in data[:4]),
        "serial_number": "".join(f"{b:02X}" for b in data[6:10]),
        "mac": None,  # The LOCALCON reply carries no MAC.
        "source": "ethernet",
    }


def decode_wifi_response(data: bytes) -> dict[str, object] | None:
    """Decode a Wi-Fi reply ``ip,mac,serial`` into the same shape."""
    text = data.decode("utf-8", errors="ignore")
    parts = [part.strip() for part in text.split(",")]
    if len(parts) < 3:
        return None
    return {
        "ip": parts[0],
        "mac": parts[1],
        "serial_number": parts[2],
        "source": "wifi",
    }


async def _discover_on_port(
    msg: bytes,
    dest_port: int,
    decoder,
    timeout: float,
    loop: asyncio.AbstractEventLoop,
    dest_address: str = BROADCAST_ADDRESS,
) -> list[dict[str, object]]:
    """Broadcast on one port and collect the distinct replies."""
    found: list[dict[str, object]] = []
    seen: set[str] = set()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", 0))
        sock.sendto(msg, (dest_address, dest_port))
        _LOGGER.debug("Sent %r to %s:%s", msg, dest_address, dest_port)

        end_time = loop.time() + timeout
        while True:
            remaining = end_time - loop.time()
            if remaining <= 0:
                break
            try:
                data, addr = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, RECV_BUFFER), timeout=remaining
                )
            except TimeoutError:
                break
            except OSError as err:  # socket closed / ICMP unreachable
                _LOGGER.debug("Discovery socket error on port %s: %s", dest_port, err)
                break

            _LOGGER.debug("Discovery reply from %s: %r", addr, data)
            try:
                device = decoder(data)
            except Exception:  # noqa: BLE001 - keep other replies usable
                _LOGGER.debug("Could not decode reply on port %s", dest_port)
                continue
            if not device:
                continue

            serial = device.get("serial_number")
            if serial and serial not in seen:
                seen.add(str(serial))
                found.append(device)
    finally:
        sock.close()

    return found


async def discover_devices(
    timeout: float = DEFAULT_TIMEOUT, dest_address: str = BROADCAST_ADDRESS
) -> list[dict[str, object]]:
    """Return the inverters/gateways found on the local network.

    Never raises for a single failing port, so the config flow can always show
    the manual-entry fallback.
    """
    loop = asyncio.get_running_loop()
    tasks = [
        asyncio.create_task(
            _discover_on_port(msg, port, decoder, timeout, loop, dest_address)
        )
        for msg, port, decoder in (
            (UDP_DISCOVERY_MSG, DEST_PORTS["localcon"], decode_localcon_response),
            (UDP_DISCOVERY_MSG_WIFI, DEST_PORTS["wifi"], decode_wifi_response),
        )
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    discovered: list[dict[str, object]] = []
    seen: set[str] = set()
    for result in results:
        if isinstance(result, BaseException):
            _LOGGER.debug("Discovery task failed: %s", result)
            continue
        for device in result:
            serial = str(device.get("serial_number", ""))
            if serial and serial not in seen:
                seen.add(serial)
                discovered.append(device)
    return discovered
