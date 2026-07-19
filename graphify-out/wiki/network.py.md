# network.py

> 11 nodes

## Key Concepts

- **network.py** (7 connections) — `src/hal0/config/network.py`
- **derive_allowed_origins()** (6 connections) — `src/hal0/config/network.py`
- **hostname()** (4 connections) — `src/hal0/config/network.py`
- **bind_host()** (3 connections) — `src/hal0/config/network.py`
- **detect_lan_ips()** (3 connections) — `src/hal0/config/network.py`
- **_api_port()** (2 connections) — `src/hal0/config/network.py`
- **Network-shape resolution — the single source both the systemd unit and ``hal0 se** (1 connections) — `src/hal0/config/network.py`
- **The canonical bind host — read by BOTH the unit and ``hal0 serve``.      Default** (1 connections) — `src/hal0/config/network.py`
- **The canonical operator-facing hostname (bare, no ``.local`` suffix).      ``HAL0** (1 connections) — `src/hal0/config/network.py`
- **Best-effort enumeration of this host's non-loopback IPv4 addresses.      Tries `** (1 connections) — `src/hal0/config/network.py`
- **Derive the WS/CORS origin allowlist from the bind host + hostname.      Always i** (1 connections) — `src/hal0/config/network.py`

## Relationships

- [socket](socket.md) (1 shared connections)
- [system_info_command.py](system_info_command.py.md) (1 shared connections)

## Source Files

- `src/hal0/config/network.py`

## Audit Trail

- EXTRACTED: 29 (97%)
- INFERRED: 1 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*