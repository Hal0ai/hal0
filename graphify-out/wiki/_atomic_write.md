# _atomic_write

> 14 nodes · cohesion 0.16

## Key Concepts

- **_atomic_write()** (9 connections) — `src/hal0/agents/hermes_provision.py`
- **_merge_config_yaml_layers()** (7 connections) — `src/hal0/agents/hermes_provision.py`
- **_render_honcho_json()** (7 connections) — `src/hal0/agents/hermes_provision.py`
- **_atomic_write_if_changed()** (6 connections) — `src/hal0/agents/hermes_provision.py`
- **content_hash()** (6 connections) — `src/hal0/agents/hermes_provision.py`
- **_deep_merge()** (6 connections) — `src/hal0/agents/hermes_provision.py`
- **_disable_honcho_hermes_host()** (5 connections) — `src/hal0/agents/hermes_provision.py`
- **Stable content hash steps use to detect "inputs unchanged".      Steps that prod** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Deep-merge the irreducible list keys + operator overrides onto config.yaml.** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Recursive dict merge — overlay wins; nested dicts merge.** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Deep-merge hal0's Honcho wiring into ``$HERMES_HOME/honcho.json``.      Hermes's** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Flip ``hosts.hermes.enabled`` to False in an existing ``honcho.json``.      Call** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Tmp-write + rename for atomicity. Returns the sha256 of content.** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Atomic write that skips a byte-identical file. Returns ``(sha256, wrote)``.** (1 connections) — `src/hal0/agents/hermes_provision.py`

## Relationships

- [hermes_provision.py](hermes_provision.py.md) (7 shared connections)
- [Path](Path.md) (5 shared connections)
- [Any](Any.md) (5 shared connections)
- [_phase_config_write](_phase_config_write.md) (5 shared connections)
- [write_gateway_secrets_dropin](write_gateway_secrets_dropin.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes_provision.py`

## Audit Trail

- EXTRACTED: 53 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*