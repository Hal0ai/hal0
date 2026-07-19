# _probe_power

> 24 nodes · cohesion 0.12

## Key Concepts

- **_probe_power()** (9 connections) — `src/hal0/api/routes/power.py`
- **.tick()** (7 connections) — `src/hal0/metrics/sampler.py`
- **power.py** (6 connections) — `src/hal0/api/routes/power.py`
- **sampler.py** (5 connections) — `src/hal0/metrics/sampler.py`
- **_find_hwmon()** (4 connections) — `src/hal0/api/routes/power.py`
- **_parse_pp_dpm_sclk()** (4 connections) — `src/hal0/api/routes/power.py`
- **Path** (4 connections)
- **_read_float()** (4 connections) — `src/hal0/api/routes/power.py`
- **_scrape_llama()** (4 connections) — `src/hal0/metrics/sampler.py`
- **.__init__()** (4 connections) — `src/hal0/metrics/sampler.py`
- **get_power_stats()** (3 connections) — `src/hal0/api/routes/power.py`
- **_probe_power_snapshot()** (3 connections) — `src/hal0/metrics/sampler.py`
- **_mb_to_bytes()** (2 connections) — `src/hal0/metrics/sampler.py`
- **Any** (2 connections)
- **GET /api/stats/power — lightweight hwmon power/thermal snapshot.  Resolves hwmon** (1 connections) — `src/hal0/api/routes/power.py`
- **Return a lightweight hwmon power/thermal snapshot.      All fields are independe** (1 connections) — `src/hal0/api/routes/power.py`
- **Return the first hwmon directory whose ``name`` file matches *name*.      Return** (1 connections) — `src/hal0/api/routes/power.py`
- **Read a single numeric sysfs file, returning None on any error.** (1 connections) — `src/hal0/api/routes/power.py`
- **Scan /sys/class/drm/card*/device/pp_dpm_sclk for the active (*) clock.      Retu** (1 connections) — `src/hal0/api/routes/power.py`
- **Read hwmon + drm sysfs and return the power snapshot dict.      All fields are i** (1 connections) — `src/hal0/api/routes/power.py`
- **SlotManager** (1 connections)
- **T2 per-slot sampler -- background asyncio task, one tick per interval.  Reuses t** (1 connections) — `src/hal0/metrics/sampler.py`
- **Run exactly one sample cycle. Exposed directly for tests.** (1 connections) — `src/hal0/metrics/sampler.py`
- **Reuse the existing per-slot llama-server scrape (best-effort, degrades to {}).** (1 connections) — `src/hal0/metrics/sampler.py`

## Relationships

- [SlotSampler](SlotSampler.md) (4 shared connections)
- [comfyui.py](comfyui.py.md) (1 shared connections)
- [MetricsWriter](MetricsWriter.md) (1 shared connections)
- [build_per_slot](build_per_slot.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/power.py`
- `src/hal0/metrics/sampler.py`

## Audit Trail

- EXTRACTED: 65 (92%)
- INFERRED: 6 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*