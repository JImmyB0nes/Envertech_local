"""Guard against names that are used but never imported.

A constant that is referenced but not imported is a ``NameError`` at runtime.
When it happens in the config flow, Home Assistant turns it into
``500 Internal Server Error`` and the integration cannot be set up at all.
That is exactly what happened with ``DISCOVERY_TIMEOUT`` in 1.3.7.

The check is deliberately limited to constant-style names, because those are
the ones that must come from ``const.py`` or another module: local variables,
parameters and function names are excluded by construction.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[1] / "custom_components" / "envertech_inverter"
_MODULES = sorted(_PKG.glob("*.py"))


def _constant_names(tree: ast.Module) -> set[str]:
    """Return UPPER_CASE names that are imported or defined in this module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {alias.asname or alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            names |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            names |= {
                target.id
                for target in targets
                if isinstance(target, ast.Name)
            }
    return {name for name in names if name.isupper()}


def _used_constant_names(tree: ast.Module) -> set[str]:
    """Return UPPER_CASE names referenced anywhere in the module."""
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id.isupper()
    }


@pytest.mark.parametrize("module", _MODULES, ids=lambda p: p.name)
def test_every_constant_is_imported_or_defined(module: Path) -> None:
    tree = ast.parse(module.read_text(), filename=str(module))
    missing = _used_constant_names(tree) - _constant_names(tree)
    assert not missing, (
        f"{module.name} uses {sorted(missing)} without importing or defining them"
    )


def test_config_flow_resolves_discovery_timeout() -> None:
    """The regression that made the config flow raise.

    ``async_step_user`` reads ``DISCOVERY_TIMEOUT`` before it touches the
    network, so a missing import surfaces as a 500 on the very first step.
    """
    config_flow = _PKG / "config_flow.py"
    tree = ast.parse(config_flow.read_text(), filename=str(config_flow))
    assert "DISCOVERY_TIMEOUT" in _used_constant_names(tree), (
        "the config flow is expected to use DISCOVERY_TIMEOUT"
    )
    assert "DISCOVERY_TIMEOUT" in _constant_names(tree), (
        "DISCOVERY_TIMEOUT must be imported from const.py"
    )
