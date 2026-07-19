# load_hal0_config

> 82 nodes · cohesion 0.05

## Key Concepts

- **load_hal0_config()** (54 connections) — `src/hal0/config/loader.py`
- **Hal0Config** (49 connections) — `src/hal0/config/schema.py`
- **loader.py** (32 connections) — `src/hal0/config/loader.py`
- **write_toml_atomic()** (27 connections) — `src/hal0/config/loader.py`
- **save_hal0_config()** (25 connections) — `src/hal0/config/loader.py`
- **Path** (20 connections)
- **ConfigNotFound** (15 connections) — `src/hal0/config/loader.py`
- **load_upstreams_config()** (13 connections) — `src/hal0/config/loader.py`
- **_read_toml()** (13 connections) — `src/hal0/config/loader.py`
- **.test_loaders_respect_hal0_home()** (13 connections) — `tests/config/test_loader.py`
- **save_upstreams_config()** (12 connections) — `src/hal0/config/loader.py`
- **UpstreamsConfig** (11 connections) — `src/hal0/config/schema.py`
- **test_loader.py** (11 connections) — `tests/config/test_loader.py`
- **ProvidersConfig** (10 connections) — `src/hal0/config/schema.py`
- **Path** (10 connections)
- **load_providers_config()** (9 connections) — `src/hal0/config/loader.py`
- **load_agent_config()** (8 connections) — `src/hal0/config/loader.py`
- **TestHal0ConfigRoundTrip** (8 connections) — `tests/config/test_loader.py`
- **TestWriteTomlAtomic** (8 connections) — `tests/config/test_loader.py`
- **OSError** (7 connections)
- **save_providers_config()** (7 connections) — `src/hal0/config/loader.py`
- **config_validate()** (5 connections) — `src/hal0/cli/config_commands.py`
- **ConfigError** (5 connections) — `src/hal0/config/loader.py`
- **save_agent_config()** (5 connections) — `src/hal0/config/loader.py`
- **.test_load_with_explicit_path()** (5 connections) — `tests/config/test_loader.py`
- *... and 57 more nodes in this community*

## Relationships

- [ConfigParseError](ConfigParseError.md) (19 shared connections)
- [load_manifest](load_manifest.md) (13 shared connections)
- [settings.py](settings.py.md) (12 shared connections)
- [load_slot_config](load_slot_config.md) (11 shared connections)
- [schema.py](schema.py.md) (11 shared connections)
- [HardwareInfo](HardwareInfo.md) (8 shared connections)
- [memory.py](memory.py.md) (7 shared connections)
- [test_orchestrate.py](test_orchestrate.py.md) (7 shared connections)
- [StacksCatalog](StacksCatalog.md) (6 shared connections)
- [UpstreamRegistry](UpstreamRegistry.md) (6 shared connections)
- [BrainChatConfig](BrainChatConfig.md) (6 shared connections)
- [run_migrations](run_migrations.md) (4 shared connections)

## Source Files

- `src/hal0/cli/config_commands.py`
- `src/hal0/config/loader.py`
- `src/hal0/config/schema.py`
- `tests/config/test_loader.py`
- `tests/config/test_schema.py`
- `tests/install/test_orchestrate.py`

## Audit Trail

- EXTRACTED: 278 (54%)
- INFERRED: 233 (46%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*