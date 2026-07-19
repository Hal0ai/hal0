# MonkeyPatch

> 38 nodes · cohesion 0.09

## Key Concepts

- **MonkeyPatch** (27 connections)
- **TestDeriveAllowedOrigins** (12 connections) — `tests/config/test_network.py`
- **test_network.py** (9 connections) — `tests/config/test_network.py`
- **TestHostname** (8 connections) — `tests/config/test_network.py`
- **TestBindHost** (6 connections) — `tests/config/test_network.py`
- **TestDetectLanIps** (6 connections) — `tests/config/test_network.py`
- **_hermetic_network_env()** (3 connections) — `tests/config/test_network.py`
- **.test_empty_string_falls_back_to_default()** (2 connections) — `tests/config/test_network.py`
- **.test_explicit_value_wins()** (2 connections) — `tests/config/test_network.py`
- **.test_strips_whitespace()** (2 connections) — `tests/config/test_network.py`
- **.test_whitespace_only_falls_back_to_default()** (2 connections) — `tests/config/test_network.py`
- **.test_always_includes_loopback_and_hostname()** (2 connections) — `tests/config/test_network.py`
- **.test_concrete_lan_bind_host_adds_lan_and_itself()** (2 connections) — `tests/config/test_network.py`
- **.test_default_loopback_bind_excludes_lan()** (2 connections) — `tests/config/test_network.py`
- **.test_explicit_port_overrides_env()** (2 connections) — `tests/config/test_network.py`
- **.test_hal0_port_env_used_when_no_arg()** (2 connections) — `tests/config/test_network.py`
- **.test_invalid_port_env_falls_back_to_default()** (2 connections) — `tests/config/test_network.py`
- **.test_ipv6_loopback_bind_host_excludes_lan()** (2 connections) — `tests/config/test_network.py`
- **.test_ipv6_wildcard_bind_adds_lan_but_not_bind_host_itself()** (2 connections) — `tests/config/test_network.py`
- **.test_localhost_bind_host_excludes_lan()** (2 connections) — `tests/config/test_network.py`
- **.test_result_is_sorted_tuple()** (2 connections) — `tests/config/test_network.py`
- **.test_wildcard_bind_adds_lan_but_not_bind_host_itself()** (2 connections) — `tests/config/test_network.py`
- **.test_dedupes_and_sorts()** (2 connections) — `tests/config/test_network.py`
- **.test_falls_back_to_udp_trick_when_psutil_empty()** (2 connections) — `tests/config/test_network.py`
- **.test_psutil_finds_non_loopback_ipv4()** (2 connections) — `tests/config/test_network.py`
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