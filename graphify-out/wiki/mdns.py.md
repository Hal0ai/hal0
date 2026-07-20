# mdns.py

> 18 nodes · cohesion 0.18

## Key Concepts

- **mdns.py** (10 connections) — `src/hal0/services/mdns.py`
- **advertise()** (7 connections) — `src/hal0/services/mdns.py`
- **services_dir()** (7 connections) — `src/hal0/services/mdns.py`
- **advertised_ids()** (5 connections) — `src/hal0/services/mdns.py`
- **status()** (5 connections) — `src/hal0/services/mdns.py`
- **_addon_path()** (4 connections) — `src/hal0/services/mdns.py`
- **mdns_hostname()** (3 connections) — `src/hal0/services/mdns.py`
- **_service_group_xml()** (3 connections) — `src/hal0/services/mdns.py`
- **withdraw()** (3 connections) — `src/hal0/services/mdns.py`
- **Path** (2 connections)
- **mDNS / avahi discovery for companion services.  If something else drops ``/etc/a** (1 connections) — `src/hal0/services/mdns.py`
- **Write one addon file per (id, name, port); prune stale addon files.      Atomic** (1 connections) — `src/hal0/services/mdns.py`
- **Remove every hal0-addon-*.service file.** (1 connections) — `src/hal0/services/mdns.py`
- **The avahi services directory (env-overridable for tests).** (1 connections) — `src/hal0/services/mdns.py`
- **The ``<host>.local`` name this machine answers to via avahi.      avahi advertis** (1 connections) — `src/hal0/services/mdns.py`
- **Service ids currently advertised via our addon files.** (1 connections) — `src/hal0/services/mdns.py`
- **Discovery status for the dashboard.      ``available`` is a real signal (avahi-d** (1 connections) — `src/hal0/services/mdns.py`
- **Render one avahi service-group announcing an HTTP service.** (1 connections) — `src/hal0/services/mdns.py`

## Relationships

- [socket](socket.md) (1 shared connections)

## Source Files

- `src/hal0/services/mdns.py`

## Audit Trail

- EXTRACTED: 57 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*