# test_network.py

> 26 nodes · cohesion 0.09

## Key Concepts

- **test_network.py** (12 connections) — `tests/install/test_network.py`
- **MonkeyPatch** (5 connections)
- **test_detect_lan_ips_override_env()** (3 connections) — `tests/install/test_network.py`
- **test_main_emits_env_lines()** (3 connections) — `tests/install/test_network.py`
- **test_network_env_defaults()** (3 connections) — `tests/install/test_network.py`
- **test_network_env_port_from_env()** (3 connections) — `tests/install/test_network.py`
- **test_network_env_triple()** (3 connections) — `tests/install/test_network.py`
- **test_resolve_hostname_precedence()** (3 connections) — `tests/install/test_network.py`
- **test_derive_allowed_origins_covers_advertised_url()** (2 connections) — `tests/install/test_network.py`
- **test_derive_allowed_origins_dotted_hostname_no_local_suffix()** (2 connections) — `tests/install/test_network.py`
- **test_derive_allowed_origins_is_deduped()** (2 connections) — `tests/install/test_network.py`
- **test_derive_allowed_origins_public_url()** (2 connections) — `tests/install/test_network.py`
- **test_detect_lan_ips_override_arg_filters_junk()** (2 connections) — `tests/install/test_network.py`
- **CaptureFixture** (1 connections)
- **Tests for WS-C network-coherence derivation (``hal0.install.network``).  Covers** (1 connections) — `tests/install/test_network.py`
- **main() prints KEY=value lines the installer appends to api.env.** (1 connections) — `tests/install/test_network.py`
- **Explicit choice wins over env, env wins over gethostname().** (1 connections) — `tests/install/test_network.py`
- **HAL0_LAN_IPS (space or comma separated) short-circuits detection.** (1 connections) — `tests/install/test_network.py`
- **A garbage/IPv6 entry is dropped; valid IPv4 survive.** (1 connections) — `tests/install/test_network.py`
- **The advertised http://<lan-ip>:<port> URL is in the allowlist.** (1 connections) — `tests/install/test_network.py`
- **An already-qualified hostname is not given a spurious .local.** (1 connections) — `tests/install/test_network.py`
- **A reverse-proxy public_url contributes its bare scheme://host origin.** (1 connections) — `tests/install/test_network.py`
- **No duplicate origins even when hostname collides with a default.** (1 connections) — `tests/install/test_network.py`
- **network_env returns the three coherent keys with the bind choice.** (1 connections) — `tests/install/test_network.py`
- **All-unset call still yields a working bind host + origins.** (1 connections) — `tests/install/test_network.py`
- *... and 1 more nodes in this community*

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/install/test_network.py`

## Audit Trail

- EXTRACTED: 58 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*