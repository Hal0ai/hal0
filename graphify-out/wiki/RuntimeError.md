# RuntimeError

> 7 nodes

## Key Concepts

- **RuntimeError** (6 connections)
- **seam.py** (6 connections) — `src/hal0/system/seam.py`
- **Hal0SeamMissing** (3 connections) — `src/hal0/system/seam.py`
- **is_hal0_service_user()** (3 connections) — `src/hal0/system/seam.py`
- **SystemCtlSeam — the one narrow privileged seam hal0-api needs post-flip.  P3-per** (1 connections) — `src/hal0/system/seam.py`
- **Raised when the hal0-systemctl seam is required but not installed.      Distinct** (1 connections) — `src/hal0/system/seam.py`
- **True only when THIS process's euid is literally the ``hal0`` service     account** (1 connections) — `src/hal0/system/seam.py`

## Relationships

- [._seam_argv](_seam_argv.md) (2 shared connections)
- [Hal0MemoryClient](Hal0MemoryClient.md) (1 shared connections)
- [_client](_client.md) (1 shared connections)
- [MemoryProvider](MemoryProvider.md) (1 shared connections)
- [manager.py](manager.py.md) (1 shared connections)
- [_shared.py](_shared.py.md) (1 shared connections)
- [SystemCtlSeam](SystemCtlSeam.md) (1 shared connections)
- [updater.py](updater.py.md) (1 shared connections)

## Source Files

- `src/hal0/system/seam.py`

## Audit Trail

- EXTRACTED: 20 (95%)
- INFERRED: 1 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*