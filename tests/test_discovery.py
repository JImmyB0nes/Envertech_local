"""Unit tests for the dependency-free LAN discovery (no Home Assistant needed).

The LOCALCON replies are decoded exactly like the old ``netifaces``-based helper
did, but the sockets here are plain stdlib, so the whole path can be exercised
against a loopback peer.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

import pytest

from tests._loader import load

discovery = load("discovery")


def test_decode_localcon_response_maps_ip_and_serial() -> None:
    data = bytes([192, 168, 123, 200, 0, 0, 0x9A, 0x1B, 0x2C, 0x3D])
    assert discovery.decode_localcon_response(data) == {
        "ip": "192.168.123.200",
        "serial_number": "9A1B2C3D",
        "mac": None,
        "source": "ethernet",
    }


def test_decode_localcon_response_rejects_short_frames() -> None:
    assert discovery.decode_localcon_response(b"\x01\x02\x03") is None


def test_decode_wifi_response_splits_ip_mac_serial() -> None:
    device = discovery.decode_wifi_response(b"192.168.1.9, aa:bb:cc:dd:ee:ff, 1234ABCD")
    assert device == {
        "ip": "192.168.1.9",
        "mac": "aa:bb:cc:dd:ee:ff",
        "serial_number": "1234ABCD",
        "source": "wifi",
    }
    assert discovery.decode_wifi_response(b"just,one") is None


def test_discover_on_port_collects_distinct_replies() -> None:
    async def main() -> list[dict[str, object]]:
        # Peer that answers every datagram with a valid LOCALCON reply.
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(("127.0.0.1", 0))
        peer.setblocking(False)
        reply = bytes([192, 168, 123, 200, 0, 0, 0x9A, 0x1B, 0x2C, 0x3D])
        port = peer.getsockname()[1]

        loop = asyncio.get_running_loop()
        found = await asyncio.gather(
            discovery._discover_on_port(
                discovery.UDP_DISCOVERY_MSG, port, discovery.decode_localcon_response,
                0.4, loop, "127.0.0.1",
            ),
            _responder(peer, loop, reply),
        )
        peer.close()
        return found[0]

    devices = asyncio.run(asyncio.wait_for(main(), 5))
    assert devices == [
        {
            "ip": "192.168.123.200",
            "serial_number": "9A1B2C3D",
            "mac": None,
            "source": "ethernet",
        }
    ]


async def _responder(peer, loop, reply: bytes):
    """Answer the first datagram, then hand the rest to the timeout path."""
    with contextlib.suppress(Exception):
        data, addr = await asyncio.wait_for(loop.sock_recvfrom(peer, 1024), 0.3)
        if data:
            await loop.sock_sendto(peer, reply, addr)
    await asyncio.sleep(0.05)


def test_discover_devices_deduplicates_across_ports(monkeypatch) -> None:
    """The same inverter answering on both ports must appear only once."""
    serial = {"AABBCCDD"}
    calls: list[int] = []

    async def fake(msg, port, decoder, timeout, loop, dest_address, source_ip="0.0.0.0"):
        calls.append(port)
        if port == discovery.DEST_PORTS["localcon"]:
            return [
                {
                    "ip": "10.0.0.1",
                    "serial_number": "AABBCCDD",
                    "mac": None,
                    "source": "ethernet",
                }
            ]
        # Wi-Fi port reports the very same inverter.
        return [
            {
                "ip": "10.0.0.1",
                "mac": "aa:bb:cc:dd:ee:ff",
                "serial_number": "AABBCCDD",
                "source": "wifi",
            }
        ]

    monkeypatch.setattr(discovery, "_discover_on_port", fake)
    devices = asyncio.run(discovery.discover_devices(timeout=0.1))

    assert sorted(calls) == sorted(discovery.DEST_PORTS.values())
    assert len(devices) == 1
    assert devices[0]["serial_number"] in serial


def test_discover_devices_returns_empty_list_without_replies() -> None:
    """Nothing found must not raise; manual entry is the fallback."""
    devices = asyncio.run(
        asyncio.wait_for(discovery.discover_devices(timeout=0.1), 5)
    )
    assert devices == []


def test_discover_devices_survives_a_failing_port(monkeypatch) -> None:
    """A crashing port must not break the flow - manual entry stays available."""

    async def boom(msg, port, decoder, timeout, loop, dest_address, source_ip="0.0.0.0"):
        raise OSError("Host unreachable")

    monkeypatch.setattr(discovery, "_discover_on_port", boom)
    assert asyncio.run(discovery.discover_devices(timeout=0.1)) == []


def test_bad_replies_are_ignored_not_fatal() -> None:
    async def main() -> list[dict[str, object]]:
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(("127.0.0.1", 0))
        peer.setblocking(False)
        port = peer.getsockname()[1]
        loop = asyncio.get_running_loop()

        async def responder():
            with contextlib.suppress(Exception):
                data, addr = await asyncio.wait_for(loop.sock_recvfrom(peer, 1024), 0.3)
                if data:
                    await loop.sock_sendto(peer, b"garbage", addr)
            await asyncio.sleep(0.05)

        task = asyncio.create_task(
            discovery._discover_on_port(
                discovery.UDP_DISCOVERY_MSG, port,
                discovery.decode_localcon_response, 0.4, loop, "127.0.0.1",
            )
        )
        resp = asyncio.create_task(responder())
        found = await task
        await asyncio.gather(resp)
        peer.close()
        return found

    assert asyncio.run(asyncio.wait_for(main(), 5)) == []


def test_discover_devices_broadcasts_from_every_source_ip(monkeypatch) -> None:
    """One broadcast per local address and port, like the original library."""
    calls: list[tuple[str, int]] = []

    async def fake(msg, port, decoder, timeout, loop, dest_address, source_ip="0.0.0.0"):
        calls.append((source_ip, port))
        return [{"ip": source_ip, "serial_number": f"SN-{source_ip}", "mac": None}]

    monkeypatch.setattr(discovery, "_discover_on_port", fake)
    devices = asyncio.run(
        discovery.discover_devices(
            timeout=0.1,
            source_ips=["192.168.1.10", "10.0.0.5", "127.0.0.1", "192.168.1.10"],
        )
    )
    ports = sorted(discovery.DEST_PORTS.values())
    assert sorted(calls) == sorted(
        (ip, port) for ip in ("192.168.1.10", "10.0.0.5") for port in ports
    )
    assert {d["serial_number"] for d in devices} == {"SN-192.168.1.10", "SN-10.0.0.5"}


def test_discover_devices_without_sources_uses_default_route(monkeypatch) -> None:
    calls: list[str] = []

    async def fake(msg, port, decoder, timeout, loop, dest_address, source_ip="0.0.0.0"):
        calls.append(source_ip)
        return []

    monkeypatch.setattr(discovery, "_discover_on_port", fake)
    asyncio.run(discovery.discover_devices(timeout=0.1, source_ips=["127.0.0.1"]))
    assert calls == ["0.0.0.0", "0.0.0.0"]


def test_discover_on_port_binds_to_source_ip() -> None:
    """A real loopback round trip with an explicit source address."""

    async def main() -> list[dict[str, object]]:
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(("127.0.0.1", 0))
        peer.setblocking(False)
        reply = bytes([10, 0, 0, 9, 0, 0, 0x11, 0x22, 0x33, 0x44])
        loop = asyncio.get_running_loop()
        found = await asyncio.gather(
            discovery._discover_on_port(
                discovery.UDP_DISCOVERY_MSG, peer.getsockname()[1],
                discovery.decode_localcon_response, 0.4, loop, "127.0.0.1",
                source_ip="127.0.0.1",
            ),
            _responder(peer, loop, reply),
        )
        peer.close()
        return found[0]

    devices = asyncio.run(asyncio.wait_for(main(), 5))
    assert [d["serial_number"] for d in devices] == ["11223344"]
