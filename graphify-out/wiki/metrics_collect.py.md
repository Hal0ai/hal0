# metrics_collect.py

> 17 nodes · cohesion 0.15

## Key Concepts

- **metrics_collect.py** (8 connections) — `src/hal0/slots/metrics_collect.py`
- **Any** (5 connections)
- **local_tps()** (4 connections) — `src/hal0/slots/metrics_collect.py`
- **local_ttft()** (4 connections) — `src/hal0/slots/metrics_collect.py`
- **tps_from_events()** (4 connections) — `src/hal0/slots/metrics_collect.py`
- **collect_local()** (3 connections) — `src/hal0/slots/metrics_collect.py`
- **llama_metrics()** (3 connections) — `src/hal0/slots/metrics_collect.py`
- **container_mem_bytes()** (2 connections) — `src/hal0/slots/metrics_collect.py`
- **systemd_props()** (2 connections) — `src/hal0/slots/metrics_collect.py`
- **Per-slot live-metrics collection adapters (extracted from routes/slots.py).  The** (1 connections) — `src/hal0/slots/metrics_collect.py`
- **Return ``systemctl show -p <prop>...`` parsed into a dict.      Empty / missing** (1 connections) — `src/hal0/slots/metrics_collect.py`
- **Scrape llama.cpp's /metrics + /slots endpoints on loopback.      /metrics is par** (1 connections) — `src/hal0/slots/metrics_collect.py`
- **Cgroup-wide memory.current for a named docker container.      Walks: ``docker in** (1 connections) — `src/hal0/slots/metrics_collect.py`
- **Build per-slot live metrics from cgroup + systemd activation time.      MEM: doc** (1 connections) — `src/hal0/slots/metrics_collect.py`
- **Compute current tokens/sec from a rolling (ts, tokens) deque.      Rate is ``tok** (1 connections) — `src/hal0/slots/metrics_collect.py`
- **Per-slot/upstream tok/s measured on this process's streaming path.      Reads th** (1 connections) — `src/hal0/slots/metrics_collect.py`
- **Per-slot TTFT view — latest sample + windowed mean.      Reads the per-name ttft** (1 connections) — `src/hal0/slots/metrics_collect.py`

## Relationships

- [samples_from_events](samples_from_events.md) (1 shared connections)

## Source Files

- `src/hal0/slots/metrics_collect.py`

## Audit Trail

- EXTRACTED: 42 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*