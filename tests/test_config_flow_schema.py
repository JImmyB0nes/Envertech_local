"""Every config/options flow form must be serialisable for the frontend.

Home Assistant 2026.11 serialises ``data_schema`` before sending a form to the
browser and raises ``ValueError: unable to serialize schema`` for any plain
Python function used as a validator. The user sees
*"500 Internal Server Error - Der Konfigurationsfluss konnte nicht geladen
werden"* and cannot set the integration up (issues #4 and #5: the custom
``_validate_port`` was the actual cause).

The flow module is loaded with lightweight stubs for the few Home Assistant
names it imports, so the test runs without Home Assistant installed. The schema
is serialised with ``voluptuous_serialize``, which rejects plain functions the
same way.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest

vol = pytest.importorskip("voluptuous")
voluptuous_serialize = pytest.importorskip("voluptuous_serialize")


# --------------------------------------------------------------------------- #
# Minimal Home Assistant stubs
# --------------------------------------------------------------------------- #
class _AbortFlow(Exception):
    pass


class _FlowBase:
    def async_show_form(self, **kwargs: Any) -> dict:
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs: Any) -> dict:
        return {"type": "create_entry", **kwargs}


class _ConfigFlow(_FlowBase):
    def __init_subclass__(cls, domain: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

    async def async_set_unique_id(self, unique_id: str) -> None:
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self) -> None:
        return None


class _OptionsFlow(_FlowBase):
    pass


def _install_ha_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register the stubs for the duration of one test only."""
    ha = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigFlow = _ConfigFlow
    config_entries.OptionsFlow = _OptionsFlow
    config_entries.ConfigEntry = object
    const = types.ModuleType("homeassistant.const")
    const.CONF_IP_ADDRESS = "ip_address"
    const.CONF_PORT = "port"
    const.CONF_UNIQUE_ID = "unique_id"
    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict
    components = types.ModuleType("homeassistant.components")
    network = types.ModuleType("homeassistant.components.network")

    async def async_get_adapters(hass):
        return [
            {"enabled": True, "ipv4": [{"address": "192.168.1.2", "network_prefix": 24}]},
            {"enabled": False, "ipv4": [{"address": "10.9.9.9", "network_prefix": 8}]},
        ]

    network.async_get_adapters = async_get_adapters
    components.network = network
    core = types.ModuleType("homeassistant.core")
    core.callback = lambda func: func
    ha.config_entries = config_entries
    for name, mod in {
        "homeassistant": ha,
        "homeassistant.config_entries": config_entries,
        "homeassistant.const": const,
        "homeassistant.data_entry_flow": data_entry_flow,
        "homeassistant.core": core,
        "homeassistant.components": components,
        "homeassistant.components.network": network,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)


def _load_config_flow(monkeypatch: pytest.MonkeyPatch, devices: list[dict]):
    _install_ha_stubs(monkeypatch)
    import importlib.util
    from pathlib import Path

    pkg_dir = (
        Path(__file__).resolve().parents[1] / "custom_components" / "envertech_local"
    )
    pkg_name = "envertech_local_flowtest"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(pkg_dir)]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, pkg_name, pkg)
    for sub in ("config_flow", "const", "discovery", "protocol"):
        monkeypatch.delitem(sys.modules, f"{pkg_name}.{sub}", raising=False)

    module_name = f"{pkg_name}.config_flow"
    spec = importlib.util.spec_from_file_location(
        module_name, pkg_dir / "config_flow.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)

    calls = {"n": 0}

    async def fake_discover(timeout: float, source_ips=None) -> list[dict]:
        calls["n"] += 1
        calls["source_ips"] = source_ips
        return list(devices)

    monkeypatch.setattr(module, "discover_devices", fake_discover)
    return module, calls


def _new_flow(module) -> Any:
    flow = module.InverterMonitorConfigFlow()
    flow.hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_entries=lambda domain: [])
    )
    return flow


def _assert_serialisable(result: dict) -> None:
    assert result["type"] == "form"
    # Raises ValueError on plain Python callables - exactly what HA trips over.
    voluptuous_serialize.convert(result["data_schema"])


DEVICE = {"ip": "192.168.1.50", "serial_number": "9A1B2C3D"}


def test_user_step_form_is_serialisable(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = _load_config_flow(monkeypatch, [DEVICE])
    result = asyncio.run(_new_flow(module).async_step_user())
    _assert_serialisable(result)


def test_user_step_form_is_serialisable_without_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, _ = _load_config_flow(monkeypatch, [])
    result = asyncio.run(_new_flow(module).async_step_user())
    _assert_serialisable(result)


def test_manual_step_form_is_serialisable(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = _load_config_flow(monkeypatch, [])
    result = asyncio.run(_new_flow(module).async_step_manual())
    _assert_serialisable(result)


def test_options_form_is_serialisable(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = _load_config_flow(monkeypatch, [])
    flow = module.InverterMonitorOptionsFlow()
    flow.config_entry = types.SimpleNamespace(options={})
    _assert_serialisable(asyncio.run(flow.async_step_init()))


def test_port_validator_coerces_and_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = _load_config_flow(monkeypatch, [])
    assert module.PORT_VALIDATOR("14889") == 14889
    for bad in (0, 65536, "abc"):
        with pytest.raises(vol.Invalid):
            module.PORT_VALIDATOR(bad)


def test_submit_uses_cached_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    module, calls = _load_config_flow(monkeypatch, [DEVICE])
    flow = _new_flow(module)

    async def run() -> dict:
        await flow.async_step_user()
        return await flow.async_step_user(
            {"ip_address": DEVICE["ip"], "port": 14889}
        )

    result = asyncio.run(run())
    assert calls["n"] == 1
    # Only enabled adapters from HA's network settings are used.
    assert calls["source_ips"] == ["192.168.1.2"]
    assert result["type"] == "create_entry"
    assert result["data"]["unique_id"] == DEVICE["serial_number"]


def test_manual_step_rejects_invalid_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = _load_config_flow(monkeypatch, [])
    result = asyncio.run(
        _new_flow(module).async_step_manual(
            {"ip_address": "not-an-ip", "unique_id": "ABC", "port": 14889}
        )
    )
    assert result["type"] == "form"
    assert result["errors"] == {"ip_address": "invalid_ip"}
    _assert_serialisable(result)


def test_manual_step_creates_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = _load_config_flow(monkeypatch, [])
    result = asyncio.run(
        _new_flow(module).async_step_manual(
            {"ip_address": " 192.168.1.7 ", "unique_id": " ABC ", "port": 14889}
        )
    )
    assert result["type"] == "create_entry"
    assert result["data"] == {
        "ip_address": "192.168.1.7",
        "unique_id": "ABC",
        "port": 14889,
    }
