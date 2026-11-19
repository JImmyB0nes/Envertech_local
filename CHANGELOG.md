# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.8] - 2026-11-19

### Fixed

- **Config flow raised `NameError`, Home Assistant answered `500 Internal Server
  Error`.** `async_step_user()` passed `DISCOVERY_TIMEOUT` to
  `discover_devices()` without importing it from `const.py`, so the first step of
  the flow aborted before the form was ever shown. Reported as *"Der
  Konfigurationsfluss konnte nicht geladen werden: 500 Internal Server Error"*
  and the reason the 500 persisted after 1.3.7.
  Fixes [#5](https://github.com/JImmyB0nes/Envertech_local/issues/5).
- Added `tests/test_module_names.py`, an AST check that fails when any module of
  the package uses an `UPPER_CASE` constant it neither imports nor defines. The
  same class of bug can no longer ship unnoticed.

## [1.3.7] - 2026-11-19

### Fixed

- **Setup failed with `500 Internal Server Error` on current Home Assistant OS
  releases.** The integration depended on the `envertech-local` library, which
  pulls in [`netifaces`](https://pypi.org/project/netifaces/) - a C extension last
  released in 2021, with wheels only up to CPython 3.9 and a `distutils` based
  build. It cannot be installed on Home Assistant 2026.11 (Python 3.14), so
  importing the config flow raised `ModuleNotFoundError`.
  Fixes [#4](https://github.com/JImmyB0nes/Envertech_local/issues/4).
- `sensor.py` used `async_get_device_by_identifier()`, which exists only from
  Home Assistant 2026.8 on, although the manifest advertises 2024.6.0. Replaced
  with `async_get_device()`, which works across the whole supported range.
- The options update listener is now unregistered on unload. Reloading an entry
  no longer stacks listeners and unloading no longer logs a `KeyError`.

### Added

- `protocol.py`: command builders, checksum and frame parser reimplemented with
  the standard library. Frames and scaling factors are byte-identical to the old
  library and covered by new tests.
- `discovery.py`: UDP discovery on ports 48889/48899 with plain `asyncio` and
  `socket`, without enumerating network interfaces.

### Changed

- `manifest.json`: `requirements` is empty, the integration has no third-party
  dependency anymore.
- `coordinator.py`: removed the 30 s polling tick. The stream pushes updates as
  soon as a packet arrives, so the periodic refresh only added wakeups.

## [1.3.6] - 2026-11-14

### Changed

- Repository moved to `JImmyB0nes/Envertech_local`. Issue tracker, documentation
  links and `codeowners` updated accordingly, copyright holder in `LICENSE` set
  to `JImmyB0nes`.

## [1.3.5] - 2026-11-08

### Added

- HACS validation and hassfest workflows restored, both required for the
  integration to be accepted into the HACS default catalog.

## [1.3.4] - 2026-11-08

### Added

- Poll interval configurable per config entry (5 / 10 / 30 s) via the options
  flow; the stream is restarted with the new value on change.
- IPv4 address and TCP port are validated in the config flow.
- Options flow registered through `async_get_options_flow`, so the poll interval
  is editable from the integration page.
- Device lookup passes `config_entry_id`, and `last_reset` no longer raises
  `ValueError` on `TOTAL_INCREASING` sensors.

## [1.2.0] and earlier

The history before 1.3.4 belongs to the upstream project and is documented in
[Kaiserdragon2/Envertech_local](https://github.com/Kaiserdragon2/Envertech_local).
Notable points carried over:

- 1.1.2 - auto-reconnect with backoff, entities are created even when the
  inverter is unreachable at startup
  ([#8](https://github.com/Kaiserdragon2/Envertech_local/pull/8)).
- 1.2.0 - panel cleanup, rollback rebase, reconnect tracking, native
  `last_reset`, and stable entity/device unique ids so renaming the display name
  does not break existing setups.
