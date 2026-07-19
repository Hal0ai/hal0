# test_hermes_env_seam.py

> 22 nodes

## Key Concepts

- **test_hermes_env_seam.py** (10 connections) — `tests/agents/test_hermes_env_seam.py`
- **MonkeyPatch** (9 connections)
- **Path** (6 connections)
- **test_secrets_env_root_writes_directly()** (4 connections) — `tests/agents/test_hermes_env_seam.py`
- **test_secrets_env_root_preserves_existing_lines()** (4 connections) — `tests/agents/test_hermes_env_seam.py`
- **test_driver_env_nonroot_routes_through_seam()** (4 connections) — `tests/agents/test_hermes_env_seam.py`
- **test_driver_env_root_writes_directly()** (4 connections) — `tests/agents/test_hermes_env_seam.py`
- **test_seed_toml_nonroot_routes_through_seam()** (4 connections) — `tests/agents/test_hermes_env_seam.py`
- **test_seed_toml_root_writes_directly()** (4 connections) — `tests/agents/test_hermes_env_seam.py`
- **test_secrets_env_nonroot_routes_through_seam()** (3 connections) — `tests/agents/test_hermes_env_seam.py`
- **test_secrets_env_nonroot_propagates_seam_failure()** (3 connections) — `tests/agents/test_hermes_env_seam.py`
- **test_voice_wire_surfaces_seam_failure_as_fail()** (3 connections) — `tests/agents/test_hermes_env_seam.py`
- **Tests for the D hardened-perms env seam (hal0-agentenv).  The provisioner writes** (1 connections) — `tests/agents/test_hermes_env_seam.py`
- **euid==0: merge into the vault directly, 0600, no sudo.** (1 connections) — `tests/agents/test_hermes_env_seam.py`
- **euid==0 merge keeps operator comments + unrelated keys, replaces matches.** (1 connections) — `tests/agents/test_hermes_env_seam.py`
- **euid!=0: pipe KEY=VALUE updates to `sudo -n hal0-agentenv merge-secrets`.** (1 connections) — `tests/agents/test_hermes_env_seam.py`
- **A non-zero seam exit must raise (so voice_wire surfaces it, not swallow).** (1 connections) — `tests/agents/test_hermes_env_seam.py`
- **voice_wire returns FAIL (not a swallowed OK) when the seam write fails.** (1 connections) — `tests/agents/test_hermes_env_seam.py`
- **euid!=0: the non-secret driver env is written via the seam too.** (1 connections) — `tests/agents/test_hermes_env_seam.py`
- **euid==0: write the driver env directly, no sudo.** (1 connections) — `tests/agents/test_hermes_env_seam.py`
- **euid!=0: the seed TOML write lands in root:root /etc/hal0/agents via the seam.** (1 connections) — `tests/agents/test_hermes_env_seam.py`
- **euid==0: write the seed TOML directly, no sudo.** (1 connections) — `tests/agents/test_hermes_env_seam.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/agents/test_hermes_env_seam.py`

## Audit Trail

- EXTRACTED: 68 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*