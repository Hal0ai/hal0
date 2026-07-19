# TestLoopbackFenceCommand

> 9 nodes · cohesion 0.33

## Key Concepts

- **TestLoopbackFenceCommand** (9 connections) — `tests/providers/test_container.py`
- **_loopback_fence_command()** (8 connections) — `src/hal0/providers/container.py`
- **.test_embedded_shell_string()** (2 connections) — `tests/providers/test_container.py`
- **.test_inline_equals_form()** (2 connections) — `tests/providers/test_container.py`
- **.test_no_bind_flag_untouched()** (2 connections) — `tests/providers/test_container.py`
- **.test_split_token_host()** (2 connections) — `tests/providers/test_container.py`
- **.test_split_token_listen()** (2 connections) — `tests/providers/test_container.py`
- **Flip any ``0.0.0.0`` bind in *command* to loopback (host-net fence).      THE si** (1 connections) — `src/hal0/providers/container.py`
- **Unit coverage for the fence helper across every bind-flag shape.** (1 connections) — `tests/providers/test_container.py`

## Relationships

- [Mount](Mount.md) (2 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (1 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)

## Source Files

- `src/hal0/providers/container.py`
- `tests/providers/test_container.py`

## Audit Trail

- EXTRACTED: 17 (59%)
- INFERRED: 12 (41%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*