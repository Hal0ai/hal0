# slots_config_dir

> 21 nodes · cohesion 0.18

## Key Concepts

- **slots_config_dir()** (14 connections) — `src/hal0/config/paths.py`
- **retag_stale_slot_images()** (13 connections) — `src/hal0/updater/updater.py`
- **test_image_retag.py** (9 connections) — `tests/updater/test_image_retag.py`
- **_image_of()** (7 connections) — `tests/updater/test_image_retag.py`
- **_write_slot()** (7 connections) — `tests/updater/test_image_retag.py`
- **list_ports()** (5 connections) — `src/hal0/api/routes/ports.py`
- **test_cpu_slot_on_toolbox_pin_is_noop()** (5 connections) — `tests/updater/test_image_retag.py`
- **test_every_stale_ref_retags()** (5 connections) — `tests/updater/test_image_retag.py`
- **test_gpu_slot_on_old_toolbox_migrates_to_rocmfpx()** (5 connections) — `tests/updater/test_image_retag.py`
- **test_stale_pins_retag_and_custom_pins_survive()** (5 connections) — `tests/updater/test_image_retag.py`
- **test_retag_is_idempotent()** (4 connections) — `tests/updater/test_image_retag.py`
- **test_custom_profile_stale_image_retagged_flags_kept()** (3 connections) — `tests/updater/test_image_retag.py`
- **ports.py** (2 connections) — `src/hal0/api/routes/ports.py`
- **Request** (1 connections)
- **GET /api/ports — the global port-claim map (hal0.ports registry).  One place to** (1 connections) — `src/hal0/api/routes/ports.py`
- **Return the slot config directory (/etc/hal0/slots/).** (1 connections) — `src/hal0/config/paths.py`
- **Retag slot ``image`` pins that are stale FORMER DEFAULTS (upgrade migration).** (1 connections) — `src/hal0/updater/updater.py`
- **Upgrade migration: retag stale former-default runner-image pins.  Covers :func:`** (1 connections) — `tests/updater/test_image_retag.py`
- **A CPU-only slot already on the lean vulkan toolbox resolves back to     itself →** (1 connections) — `tests/updater/test_image_retag.py`
- **Every known former-default ref rolls to the current default. A GPU     (backend-** (1 connections) — `tests/updater/test_image_retag.py`
- **A GPU slot pinned to the old vulkan toolbox (now a stale former-default     ref)** (1 connections) — `tests/updater/test_image_retag.py`

## Relationships

- [paths.py](paths.py.md) (3 shared connections)
- [ConfigParseError](ConfigParseError.md) (3 shared connections)
- [profiles_toml](profiles_toml.md) (2 shared connections)
- [slots.py](slots.py.md) (1 shared connections)
- [__init__.py](__init__.py.md) (1 shared connections)
- [collect_port_claims](collect_port_claims.md) (1 shared connections)
- [RoutingHost](RoutingHost.md) (1 shared connections)
- [rerender_slot_units](rerender_slot_units.md) (1 shared connections)
- [test_mtp_defuse.py](test_mtp_defuse.py.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)
- [get_runner](get_runner.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/ports.py`
- `src/hal0/config/paths.py`
- `src/hal0/updater/updater.py`
- `tests/updater/test_image_retag.py`

## Audit Trail

- EXTRACTED: 58 (63%)
- INFERRED: 34 (37%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*