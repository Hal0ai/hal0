# _probe_power

> 13 nodes

## Key Concepts

- **_probe_power()** (9 connections) — `src/hal0/api/routes/power.py`
- **power.py** (6 connections) — `src/hal0/api/routes/power.py`
- **_find_hwmon()** (4 connections) — `src/hal0/api/routes/power.py`
- **Path** (4 connections)
- **_read_float()** (4 connections) — `src/hal0/api/routes/power.py`
- **_parse_pp_dpm_sclk()** (4 connections) — `src/hal0/api/routes/power.py`
- **get_power_stats()** (3 connections) — `src/hal0/api/routes/power.py`
- **GET /api/stats/power — lightweight hwmon power/thermal snapshot.  Resolves hwmon** (1 connections) — `src/hal0/api/routes/power.py`
- **Return the first hwmon directory whose ``name`` file matches *name*.      Return** (1 connections) — `src/hal0/api/routes/power.py`
- **Read a single numeric sysfs file, returning None on any error.** (1 connections) — `src/hal0/api/routes/power.py`
- **Scan /sys/class/drm/card*/device/pp_dpm_sclk for the active (*) clock.      Retu** (1 connections) — `src/hal0/api/routes/power.py`
- **Read hwmon + drm sysfs and return the power snapshot dict.      All fields are i** (1 connections) — `src/hal0/api/routes/power.py`
- **Return a lightweight hwmon power/thermal snapshot.      All fields are independe** (1 connections) — `src/hal0/api/routes/power.py`

## Relationships

- [Any](Any.md) (1 shared connections)
- [.tick](tick.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/power.py`

## Audit Trail

- EXTRACTED: 36 (90%)
- INFERRED: 4 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*