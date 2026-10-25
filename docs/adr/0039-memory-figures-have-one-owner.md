# ADR-0039: Memory figures have one owner and two budgets

## Status

Accepted (2026-10-25). An operator decision.

The change that carries this record adds the owner, the collector and the
`memory` block on `GET /api/stats/hardware`, then moves the top bar, the memory
ruler, the dashboard memory cards, Settings and the slot list onto it. The
Image Gen pane's own gauge, the image fit check and the generation preflight
come in later changes. Decision 5 says what that leaves in place.

It does not amend an earlier record. It applies the rule ADR-0029 states for
config, one owner per fact, to memory figures. ADR-0037 gave image models a
catalog; this record gives the memory they are measured against one source.

**Amended (2026-10-25, image readiness API).** `render_free_gb` no longer
subtracts the image engine. The fit check judges a catalog need that is measured
above an idle engine, so subtracting the engine's own memory as well counted a
warm engine's weights twice. Decision 4 has the formula and the reason.

## Context

At one moment on the reference box, five figures that all read as "memory"
disagreed.

| Figure | Value | Source |
|---|---|---|
| Top-bar CPU and memory cell | 16.1 / 116 GB | this box's RAM divided by the GPU pool cap |
| Memory ruler | 46.4 GB | the sum of per-slot estimates |
| Kernel GTT counter | 43.4 GB | device-wide, so a second guest on the same GPU reads the same number with no slots loaded |
| Image Gen "gtt" gauge | 4.4 / 116 GB | ComfyUI's own view of its device |
| Image Gen "system ram" gauge | 86.7 / 124 GB | ComfyUI's view of RAM, which is the host's |

- **Fixed ceilings.** Two of the gauges' ceilings were constants: 80 GB for the
  pool and 96 GB for RAM (`_GTT_CEIL_GB` and `_RAM_CEIL_GB` in
  `api/routes/comfyui.py`, and the same two numbers as fallbacks in
  `useComfyui.ts`). The pool's real cap on the reference box is 116 GiB.
- **A limit on no screen.** On a second guest on the same GPU with a 48 GiB
  container limit, the dashboard's headroom promised 114 GB, and Qwen-Image was
  OOM-killed with an `anon-rss` of 45.5 GB (47732248 KiB). The limit that
  killed it appeared nowhere. The probe's `cgroup_max_mb` read only the leaf
  cgroup's `memory.max`. Inside an LXC guest that file says `max`. For a
  service in a systemd slice it says `max` as well, because the limit sits on
  the slice above the service.
- **A stale snapshot.** The install-time hardware probe is a snapshot. It
  recorded 96 GB on a guest whose live limit is 105.5 GB.

The shared cause is that nothing owned the question of how much memory there
is and who is using it. Each screen combined whichever counters it had and
chose its own ceiling.

## Decision

One backend module computes every memory figure the dashboard shows. It reports
two budgets, the GPU pool and this box's RAM, with every ceiling read live. A
route serves the result, and the UI renders it.

### 1. One owner, one transport

`hal0.hardware.memory_budget` (`build_memory_budget`, `read_ram`,
`cgroup_limit_mb`) is the only code that measures, attributes or caps a memory
figure the dashboard shows. `build_memory_budget` is pure. The only I/O in the
module is the RAM read, which takes a filesystem root so tests build a fake
`/proc` and `/sys`. Decisions 5 and 6 name the two figures that are not yet
under it.

`hal0.api.memory_source.collect_memory_budget` gathers the live inputs: the GPU
sample, the slot rows and configs, ComfyUI's figure and the RAM reading. It
degrades instead of raising. A missing stats singleton or slot manager, a
failing probe or a dead ComfyUI each leave one input empty, and a budget still
comes back.

`GET /api/stats/hardware` serves the result as `memory`
(`MemoryBudget.as_dict()`), set in `stats_hardware`
(`api/routes/hardware.py`). It is the only transport:

- `memory` is always this process's own budget. It replaces whatever an
  upstream payload carried under that key.
- If the collector fails, the route logs `hardware.memory_budget_failed`,
  leaves the key out and answers 200. It never serves a stale budget or
  another box's.
- `per_slot` on the same response keeps its shape and still omits slots that
  are starting. The route builds the per-slot rows once and gives them to both.
- The GPU capability check (`capacity._host_has_capable_gpu`) reads the hardware
  cache and can fall back to a live probe. The route runs it once per request,
  off the event loop, and hands the answer to `build_per_slot` and to
  `collect_memory_budget(gpu_capable=)`; neither runs it again.

A screen may subtract a margin from a budget figure or take the smaller of two
(the Slots headroom does both today). It reads no counter, file or ceiling of
its own.

The dashboard polls this route every 2.5 s, so the budget must never wait on
ComfyUI. `comfyui.engine_used_mb` caches its answer for 5 s, failures and
cancelled reads included, and uses a 0.25 s connect and 0.5 s read timeout.
`collect_memory_budget` also wraps it in a 0.75 s `asyncio.wait_for`, and skips
it unless the budget will use the answer: a shared GPU pool with an img slot to
attribute it to (decision 3 says why a discrete card does not). Past the bound,
the img slot's own attribution stands in.

### 2. Two budgets, never summed

The GPU pool and RAM are different resources. The budget treats GTT as not
charged to the guest's RAM limit, so unloading GPU slots frees little RAM. The
two are reported side by side and never added.

**The GPU pool.** `kind` is `shared` when the sample is unified memory, where
the model-loading pool is GTT shared with the host and every other guest, and
`dedicated` otherwise, where it is VRAM. Total and used come from the sample's
`gtt_*` fields when shared and its `vram_*` fields when dedicated. The sample
reads the kernel's `mem_info_*` counters (`hardware/gpu_view.py`). The
max-pooled `total_mb`, which can pick the wrong counter, is never used.

`gpu` and `render_free_gb` are null when the box has no GPU lane, no sample was
read, or the sample lacks the total or the used counter for its pool. A missing
counter is never read as zero, because that would publish a headroom nothing
measured. The dashboard shows RAM as its pool in that case.

**This box's RAM.** `read_ram` returns the limit, used and free, read live on
every call:

- `limit` is the smaller of `MemTotal` from `/proc/meminfo` and the tightest
  finite cgroup limit on the process's chain. Inside an LXC guest, lxcfs
  already reports the guest limit as `MemTotal` and every visible `memory.max`
  reads `max`, so `MemTotal` carries it.
- `cgroup_limit_mb` covers the rest. It reads the process's own cgroup from
  `/proc/self/cgroup` (the v2 `0::` line and the v1 `memory` controller line)
  and walks up to the root, reading `memory.max` (v2) and
  `memory/memory.limit_in_bytes` (v1) at every level. A limit set on a systemd
  slice is found from the service below it.
- `max`, empty, unreadable, unparseable and negative values count as no limit.
  So do values of 2^60 bytes or more, which is how v1 spells no limit.
- `used` is `MemTotal - MemAvailable`, so the reclaimable page cache that
  reading safetensors fills is not counted. `free` is `limit - used`, with a
  floor of 0.
- `source` is `container` when `/run/systemd/container` exists or the chain
  carried a finite limit, and `system` otherwise. A slice limit on a bare host
  therefore reads `container`.

The probe's `_read_cgroup_memory_max_mb` now delegates to `cgroup_limit_mb`, so
`cgroup_max_mb` on the hardware info follows the same walk.

### 3. Attribution: other guests

The pool's usage is the kernel counter. The budget splits it by who holds it.

- **This box.** `this_box` lists every other GPU slot (a device starting with
  `gpu`) that has a row, ordered by slot name, at the footprint
  `slots/capacity.py` `build_per_slot` estimates for it.
- **The image engine.** On a shared pool, `image_engine_gb` is ComfyUI's own
  figure, `vram_total - vram_free` on the first device of its `/system_stats`,
  when present, even at 0. Otherwise, and always on a dedicated card, it is the
  img slot's attributed footprint. The img slot is the first one
  `gpu_exclusive_group` puts in the `img` group, and it is not listed in
  `this_box`, so the engine is never counted twice. The budget names that slot
  (`image_engine_slot`, `null` with no img slot) and says when the figure is the
  slot's estimate rather than ComfyUI's reading (`image_engine_estimated`), so a
  screen neither guesses the slot nor decides for itself what to call an
  estimate.
  - ComfyUI's figure is not used on a dedicated card. `torch.cuda.mem_get_info`
    is device-wide there: the figure already holds every other slot's weights
    and any other process's memory, so taking it as the engine's share would
    count them twice: `attribution_excess_gb` would read the size of the slots,
    and `other_guests_gb` would collapse to 0, so `render_free_gb` would read too
    high by whatever other guests hold.
  - It is not used when ComfyUI runs on the CPU either: its `vram_*` fields
    then describe system RAM, and `engine_used_mb` returns `None`.
- **Other guests.** `other_guests_gb` is the counter minus this box:
  `max(0, used - resident slots - image engine)`. It is whatever the pool
  holds that no slot here accounts for, such as a second guest on the same GPU.
- **Excess.** The opposite remainder, `attribution_excess_gb`, is
  `max(0, resident slots + image engine - used)`. It is reported and never
  drawn. Per-slot figures are estimates and can exceed the counter: 46.4 GB of
  slot estimates against a 43.4 GB counter on the reference box. The total
  always comes from the counter.

### 4. Derived figures

- `free_gb` is `max(0, total - used)`.
- `render_free_gb` is what a render can use once image mode has unloaded this
  box's GPU slots: `max(0, total - other_guests - starting slots -
  RENDER_MARGIN_GB)`.
  - It leaves out resident GPU slots because every GPU LLM slot unloads for a
    render (`docs/concepts/slots.mdx`, "The GPU arbiter").
  - It does not subtract the image engine. The catalog's `gpu_peak_gb` is the
    rise above an idle engine, and the render runs inside the engine's own
    allocation: it reuses a resident model, or frees its cached one before
    loading another. So the engine's memory, a model still resident from the
    last render included, is room for the render. Subtracting it as well counted
    a warm engine's weights in the need and again in the room, and refused
    renders that succeed. The engine still counts as attributed, so
    `other_guests_gb` never absorbs it, and its own context, which the
    measurement leaves out, is well under the margin.
  - It subtracts slots in the `starting` state, at their estimated footprint,
    because a slot still loading when image mode starts finishes onto the pool
    (#3056). Those slots are listed in `this_box`, but they are not counted as
    resident.
  - `RENDER_MARGIN_GB` is 2.0 GB, for the driver and allocator slop. It is
    subtracted once, from the pool.
- Units: "GB" is MiB / 1024 everywhere. Each GB field is rounded to one decimal
  once, from unrounded MiB values, so rounding error does not accumulate through
  the subtractions.

Two behaviours of the split are worth stating exactly.

- **A starting slot only lowers the figure.** While a slot is starting, the
  bytes it has already loaded are in the counter but not in the resident sum,
  so they count as "other guests". The same bytes are subtracted from
  free-for-a-render a second time, through the slot's estimated footprint. This
  can make `render_free_gb` lower than the truth and `other_guests_gb` higher
  than the truth. It cannot make either error the other way, and it ends when
  the slot is resident.
- **Estimates above the counter can hide a guest.** `other_guests_gb` clamps at
  0 when resident slots plus the image engine are estimated above the counter,
  which is when `attribution_excess_gb` is above 0. The estimates may be high,
  and a real second guest's memory can be absorbed into them. Then
  `other_guests_gb` reads too low and `render_free_gb` reads too high. The
  counter is a measurement and the split is not, so a reported excess means the
  split may be hiding a guest.

### 5. No fixed ceilings

Every ceiling in the budget is read live: the pool's cap from the kernel
counter, the RAM limit from `MemTotal` and the cgroup chain. The only constant
is the render margin. For the GPU pool, a missing reading leaves the field
absent (`gpu` is null), and no constant stands in for it. RAM has no absent
state: `read_ram` always returns a `RamBudget`, and a `/proc/meminfo` that
cannot be read gives 0.0 GB for the limit, used and free. No constant stands in
there either.

The 80 and 96 GB constants are not part of the budget, and nothing in it reads
them. They still back the Image Gen pane's own gauge, the `memory` key on
`GET /api/comfyui/status`, which this change leaves in place. The pane's
rework deletes the gauge and its constants together. Until then the pane can
still disagree with the rest of the dashboard.

### 6. Out of scope

- **The LLM feasibility verdict.** `POST /api/models/feasibility` and
  `gtt_feasibility_verdict` keep their own logic and their own fits, tight and
  exceeds vocabulary. Issue #3063 tracks merging them with the budget's
  fits, tight and won't fit.
- **Coordinating guests.** One guest's arbiter cannot unload another guest's
  slots. The budget shows that memory as "other guests" and subtracts it from
  free-for-a-render. It does not try to free it.

## Options considered

- **Serve the budget to the top bar and the Image Gen pane only.** Rejected.
  It would leave the Slots page's headroom, the figure that promised 114 GB on
  the capped guest, on the old arithmetic, and this record would be untrue on
  the day it merged. The operator chose the wider scope: every screen's pool,
  RAM-limit and attribution figures move to the owner.
- **Also merge the LLM feasibility verdict now.** Rejected for this change. The
  verdict has its own consumers (the model drawer, `RichSelect` and profiles)
  and its own vocabulary, and merging it is a separate change.
- **Keep 80 and 96 as fallbacks for a failed read.** Rejected. A constant that
  is wrong on a 116 GiB pool and on a 48 GiB container is a number nothing
  measured, shown as if it were real.
- **Take the RAM limit from the install-time probe.** Rejected. It is a
  snapshot, and it has already been wrong by 9.5 GB.
- **Read the leaf cgroup only, or `MemTotal` only.** Rejected. The leaf misses
  a limit on a slice, and `MemTotal` misses a limit on a cgroup in a box that
  is not an LXC guest. The chain walk and `MemTotal` together cover both.
- **Scale per-slot figures down to fit the counter, or let the ruler overflow.**
  Rejected. The counter is a measurement and the per-slot figures are
  estimates. Scaling would invent per-slot numbers, and overflow would draw
  memory the box does not have.
- **Redesign the memory bar, the top-bar cells and the ruler to carry the new
  figures.** Rejected. The operator ruled that their styling and design stay as
  they are, and that only their data and label text change. No markup structure,
  CSS or visual element is added. This is why the top bar's GPU cell does not
  gain a pool figure, as the design first proposed: the ruler names and carries
  the pool.
- **Add the pool and RAM into one figure.** Rejected. They are different
  resources with different limits, and a sum answers neither question.

## Consequences

- On the capped guest, the dashboard reads the 48 GB RAM limit, labelled as a
  container limit, and shows the counter's 43.4 GB as other guests. The RAM
  figure in the top bar is divided by the RAM limit, not by the GPU pool cap.
- The memory ruler's total is the kernel counter, it names its pool, and its
  segments are this box's slots, the image engine and other guests. The slot
  figures are estimates and the UI says so.
- The memory bar, the top-bar cells, the ruler and the dashboard memory cards
  keep their markup and styling, and change only their numbers and label text.
  The top bar's GPU cell shows no pool figure.
- `GET /api/stats/hardware` gains a `memory` key and keeps its other keys. A
  client that ignores `memory` is unaffected.
- The image fit check and the `/v1` preflight that follow call
  `collect_memory_budget`, so a verdict and the ruler cannot disagree about
  what is free.
- `cgroup_max_mb` on the hardware info now sees a limit on a systemd slice. The
  installer's memory budget for model sizing (`install/orchestrate.py`) clamps
  against it, so an install on such a box sizes against the slice limit. Inside
  an LXC guest the leaf still reads `max`, so nothing changes there.
- A dead ComfyUI costs one refused connection per 5 s. A wedged one can hold a
  poll for up to 0.75 s.
- Known limits, stated so the UI copy can match them:
  - `used` RAM is system-wide, because `MemAvailable` is not scoped to a
    cgroup. Under a slice limit below `MemTotal`, `used` can exceed `limit`, and
    `free` floors at 0.
  - On a shared pool the image engine figure is ComfyUI's own view of its
    device, and hal0 counts it as the engine's share. It has not been confirmed
    on shared hardware that the figure rises with a render and ignores other
    processes. If it took in another process's memory, `render_free_gb` would
    read too high by that amount, because decision 4 counts the engine's
    figure as room.
  - On a discrete card the image engine figure is always the img slot's
    estimate, and `image_engine_estimated` says so. The card's `used_gb` and
    `free_gb` still come from the kernel counter; only the split of the usage
    between the engine and other guests rests on the estimate.
  - A slot row without a `vram_mb` reads as 0.0 GB in `this_box`.
  - While `attribution_excess_gb` is above 0, `other_guests_gb` and
    `render_free_gb` depend on the per-slot estimates, as decision 4 says.
  - An unreadable `/proc/meminfo` shows as a RAM limit of 0.0 GB, not as a
    missing field.
- The Image Gen pane's gauge and its 80 and 96 GB constants stay until the
  pane's rework, as decision 5 says.

## References

- ADR-0029 (profile-centric config; one owner per fact), ADR-0037 (image models
  are a ComfyUI catalog).
- `src/hal0/hardware/memory_budget.py` (`build_memory_budget`, `read_ram`,
  `cgroup_limit_mb`, `RENDER_MARGIN_GB`), `src/hal0/api/memory_source.py`
  (`collect_memory_budget`), `src/hal0/api/routes/hardware.py`
  (`stats_hardware`), `src/hal0/api/routes/comfyui.py` (`engine_used_mb`,
  `_parse_memory`), `src/hal0/hardware/gpu_view.py` (`GPUMemorySample`),
  `src/hal0/hardware/probe.py` (`_read_cgroup_memory_max_mb`),
  `src/hal0/slots/capacity.py` (`build_per_slot`, `include_starting`),
  `src/hal0/slots/arbiter.py` (`gpu_exclusive_group`).
- `docs/concepts/slots.mdx` ("The GPU arbiter"), #3056 (a slot still loading
  when image mode starts).
