# network.py

> 20 nodes · cohesion 0.15

## Key Concepts

- **network.py** (11 connections) — `src/hal0/install/network.py`
- **network_env()** (8 connections) — `src/hal0/install/network.py`
- **derive_allowed_origins()** (5 connections) — `src/hal0/install/network.py`
- **detect_lan_ips()** (5 connections) — `src/hal0/install/network.py`
- **_dedupe()** (4 connections) — `src/hal0/install/network.py`
- **_coerce_port()** (3 connections) — `src/hal0/install/network.py`
- **_is_lan_ipv4()** (3 connections) — `src/hal0/install/network.py`
- **main()** (3 connections) — `src/hal0/install/network.py`
- **_origin_of()** (3 connections) — `src/hal0/install/network.py`
- **resolve_hostname()** (3 connections) — `src/hal0/install/network.py`
- **Network-coherence derivation for the installer / answer-file (WS-C).  One ``HAL0** (1 connections) — `src/hal0/install/network.py`
- **Build the WS-origin allowlist for the given bind/hostname choice.      The list** (1 connections) — `src/hal0/install/network.py`
- **Resolve the three coherent network env vars from a bind choice.      Returns ``H** (1 connections) — `src/hal0/install/network.py`
- **Emit ``KEY=value`` env lines for the installer to append to api.env.      Invoke** (1 connections) — `src/hal0/install/network.py`
- **Parse a port from an int/str, falling back to the default.** (1 connections) — `src/hal0/install/network.py`
- **True for a plausible non-loopback IPv4 dotted-quad.** (1 connections) — `src/hal0/install/network.py`
- **Return the ``scheme://host[:port]`` origin of a URL, or None.      Tolerant of a** (1 connections) — `src/hal0/install/network.py`
- **Order-preserving de-duplication.** (1 connections) — `src/hal0/install/network.py`
- **Return the hostname to advertise (mDNS + origin allowlist).      Precedence: exp** (1 connections) — `src/hal0/install/network.py`
- **Best-effort list of the host's routable LAN IPv4 addresses.      ``override`` (o** (1 connections) — `src/hal0/install/network.py`

## Relationships

- [socket](socket.md) (1 shared connections)
- [load_answers](load_answers.md) (1 shared connections)

## Source Files

- `src/hal0/install/network.py`

## Audit Trail

- EXTRACTED: 57 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*