# releases_url

> 6 nodes · cohesion 0.33

## Key Concepts

- **releases_url()** (8 connections) — `src/hal0/updater/updater.py`
- **test_releases_url_appends_channel_for_http_override()** (4 connections) — `tests/updater/test_updater.py`
- **test_releases_url_defaults_per_channel()** (4 connections) — `tests/updater/test_updater.py`
- **Return the release-manifest URL for ``channel``.      Resolution:       - ``HAL0** (1 connections) — `src/hal0/updater/updater.py`
- **Without the override env var the URL is per-channel under releases.hal0.dev.** (1 connections) — `tests/updater/test_updater.py`
- **An http(s) override is rewritten with ?channel= for non-stable channels.** (1 connections) — `tests/updater/test_updater.py`

## Relationships

- [test_updater.py](test_updater.py.md) (5 shared connections)
- [ConfigParseError](ConfigParseError.md) (3 shared connections)
- [updater.py](updater.py.md) (1 shared connections)

## Source Files

- `src/hal0/updater/updater.py`
- `tests/updater/test_updater.py`

## Audit Trail

- EXTRACTED: 13 (68%)
- INFERRED: 6 (32%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*