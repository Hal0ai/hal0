# MonkeyPatch

> 38 nodes

## Key Concepts

- **MonkeyPatch** (27 connections)
- **TestDeriveAllowedOrigins** (12 connections) — `tests/config/test_network.py`
- **test_network.py** (9 connections) — `tests/config/test_network.py`
- **TestHostname** (8 connections) — `tests/config/test_network.py`
- **TestBindHost** (6 connections) — `tests/config/test_network.py`
- **TestDetectLanIps** (6 connections) — `tests/config/test_network.py`
- **_hermetic_network_env()** (3 connections) — `tests/config/test_network.py`
- **.test_explicit_value_wins()** (2 connections) — `tests/config/test_network.py`
- **.test_strips_whitespace()** (2 connections) — `tests/config/test_network.py`
- **.test_empty_string_falls_back_to_default()** (2 connections) — `tests/config/test_network.py`
- **.test_whitespace_only_falls_back_to_default()** (2 connections) — `tests/config/test_network.py`
- **.test_env_override_wins()** (2 connections) — `tests/config/test_network.py`
- **.test_env_local_suffix_stripped()** (2 connections) — `tests/config/test_network.py`
- **.test_env_trailing_dots_stripped()** (2 connections) — `tests/config/test_network.py`
- **.test_gethostname_local_suffix_stripped()** (2 connections) — `tests/config/test_network.py`
- **.test_dots_only_falls_back_to_hal0()** (2 connections) — `tests/config/test_network.py`
- **.test_env_whitespace_only_falls_back_to_gethostname()** (2 connections) — `tests/config/test_network.py`
- **.test_psutil_finds_non_loopback_ipv4()** (2 connections) — `tests/config/test_network.py`
- **.test_dedupes_and_sorts()** (2 connections) — `tests/config/test_network.py`
- **.test_falls_back_to_udp_trick_when_psutil_empty()** (2 connections) — `tests/config/test_network.py`
- **.test_udp_fallback_filters_loopback_result()** (2 connections) — `tests/config/test_network.py`
- **.test_returns_empty_when_all_sources_fail()** (2 connections) — `tests/config/test_network.py`
- **.test_default_loopback_bind_excludes_lan()** (2 connections) — `tests/config/test_network.py`
- **.test_hal0_port_env_used_when_no_arg()** (2 connections) — `tests/config/test_network.py`
- **.test_explicit_port_overrides_env()** (2 connections) — `tests/config/test_network.py`
- *... and 13 more nodes in this community*

## Relationships

- [types.py](types.py.md) (1 shared connections)
- [socket](socket.md) (1 shared connections)

## Source Files

- `tests/config/test_network.py`

## Audit Trail

- EXTRACTED: 128 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*