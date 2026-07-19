# .container_spec

> 21 nodes · cohesion 0.10

## Key Concepts

- **.container_spec()** (11 connections) — `src/hal0/providers/flm.py`
- **ensure_host_flm_store_link()** (6 connections) — `src/hal0/providers/flm.py`
- **_host_flm_models_dir()** (5 connections) — `src/hal0/providers/flm.py`
- **_ensure_flm_models_dir()** (4 connections) — `src/hal0/providers/flm.py`
- **flm_pull_command()** (4 connections) — `src/hal0/providers/flm.py`
- **_flm_shadow_role_args()** (4 connections) — `src/hal0/providers/flm.py`
- **.build_env()** (4 connections) — `src/hal0/providers/flm.py`
- **.start_cmd()** (3 connections) — `src/hal0/providers/flm.py`
- **_npu_device_nodes()** (3 connections) — `src/hal0/providers/flm.py`
- **_resolve_render_gid()** (3 connections) — `src/hal0/providers/flm.py`
- **ContainerSpec** (1 connections)
- **Return ``(argv, host_models_dir)`` for a host ``flm pull <tag>`` run.      Uses** (1 connections) — `src/hal0/providers/flm.py`
- **Best-effort create the FLM store so the bind-mount source exists.      A missing** (1 connections) — `src/hal0/providers/flm.py`
- **Point flm's hardcoded host cache at the resolved store; return the store.      T** (1 connections) — `src/hal0/providers/flm.py`
- **Device nodes to pass through to the FLM container.      Fallback chain (document** (1 connections) — `src/hal0/providers/flm.py`
- **Build the ``--embed`` / ``--asr`` argv tail.      Shared by :meth:`FLMProvider.s** (1 connections) — `src/hal0/providers/flm.py`
- **Look up the ``render`` group's numeric gid on the host.      Slot containers nee** (1 connections) — `src/hal0/providers/flm.py`
- **Build HAL0_* env vars for an FLM slot.          Returned vars are stamped into t** (1 connections) — `src/hal0/providers/flm.py`
- **Return argv for the native ``flm serve`` invocation.          Used by tests and** (1 connections) — `src/hal0/providers/flm.py`
- **Build a ContainerSpec for FLM in the toolbox image.          The toolbox image i** (1 connections) — `src/hal0/providers/flm.py`
- **FLM model store for probe/pull bookkeeping and the container mount.      Delegat** (1 connections) — `src/hal0/providers/flm.py`

## Relationships

- [flm.py](flm.py.md) (9 shared connections)
- [FLMProvider](FLMProvider.md) (3 shared connections)
- [Model](Model.md) (2 shared connections)
- [paths.py](paths.py.md) (1 shared connections)
- [get_runner](get_runner.md) (1 shared connections)

## Source Files

- `src/hal0/providers/flm.py`

## Audit Trail

- EXTRACTED: 55 (95%)
- INFERRED: 3 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*