# KokoroProvider

> 33 nodes

## Key Concepts

- **KokoroProvider** (26 connections) — `src/hal0/providers/kokoro.py`
- **test_kokoro_container_spec.py** (12 connections) — `tests/providers/test_kokoro_container_spec.py`
- **_slot_cfg()** (9 connections) — `tests/providers/test_kokoro_container_spec.py`
- **.container_spec()** (7 connections) — `src/hal0/providers/kokoro.py`
- **test_renderer_no_device_args_publish_volume_command()** (6 connections) — `tests/providers/test_kokoro_container_spec.py`
- **Any** (5 connections)
- **_render_from_spec()** (5 connections) — `tests/providers/test_kokoro_container_spec.py`
- **test_slot_port_override_wins()** (5 connections) — `tests/providers/test_kokoro_container_spec.py`
- **.infer()** (4 connections) — `src/hal0/providers/kokoro.py`
- **test_spec_ro_mount_is_read_only()** (4 connections) — `tests/providers/test_kokoro_container_spec.py`
- **.build_env()** (3 connections) — `src/hal0/providers/kokoro.py`
- **.health()** (3 connections) — `src/hal0/providers/kokoro.py`
- **test_spec_has_no_gpu_devices_or_groups()** (3 connections) — `tests/providers/test_kokoro_container_spec.py`
- **test_spec_command_carries_port_host_and_model_path()** (3 connections) — `tests/providers/test_kokoro_container_spec.py`
- **test_spec_mounts_model_store_and_publishes_loopback()** (3 connections) — `tests/providers/test_kokoro_container_spec.py`
- **test_spec_security_opts_for_lxc()** (3 connections) — `tests/providers/test_kokoro_container_spec.py`
- **.start_cmd()** (2 connections) — `src/hal0/providers/kokoro.py`
- **_exec()** (2 connections) — `tests/providers/test_kokoro_container_spec.py`
- **_pin_model_store()** (2 connections) — `tests/providers/test_kokoro_container_spec.py`
- **ContainerSpec** (1 connections)
- **Provider for the Kokoro ONNX TTS backend.      CPU-only: no GPU devices, no grou** (1 connections) — `src/hal0/providers/kokoro.py`
- **Informational env block (container is self-contained).** (1 connections) — `src/hal0/providers/kokoro.py`
- **Not applicable — systemd starts the container.** (1 connections) — `src/hal0/providers/kokoro.py`
- **Build a ContainerSpec for the Kokoro TTS slot.          The toolbox image ENTRYP** (1 connections) — `src/hal0/providers/kokoro.py`
- **Probe GET /health on the kokoro-server port.          NOTE: dead code in the con** (1 connections) — `src/hal0/providers/kokoro.py`
- *... and 8 more nodes in this community*

## Relationships

- [_spec_provider_for](_spec_provider_for.md) (5 shared connections)
- [Mount](Mount.md) (4 shared connections)
- [Provider](Provider.md) (3 shared connections)
- [get_runner](get_runner.md) (3 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)
- [Qwen3TTSProvider](Qwen3TTSProvider.md) (1 shared connections)
- [model_store_root](model_store_root.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)

## Source Files

- `src/hal0/providers/kokoro.py`
- `tests/providers/test_kokoro_container_spec.py`

## Audit Trail

- EXTRACTED: 93 (77%)
- INFERRED: 28 (23%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*