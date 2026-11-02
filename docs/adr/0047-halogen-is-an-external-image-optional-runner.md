# ADR-0047: halogen is an external-image optional runner for Flash-Next

## Status

**ACCEPTED (2026-11-01).** An operator decision. Amends ADR-0028 decisions 1
and 4. Builds on ADR-0006, ADR-0027, ADR-0029, ADR-0039 and ADR-0045. ADR-0003
stands: FLM remains the NPU lane. Tracked in #3193.

## Context

Peonist's **halogen** is a closed-source inference engine for
Qwen3.8-Flash-Next on Strix Halo (gfx1151, ROCm). The vendor ships it as one
container image, `ghcr.io/peonist-ai/halogen-flash-server`, under the Peonist
EULA v0.1. The licence lets anyone run it for any purpose and redistribute it
unmodified, forbids redistributing a modified image as halogen, performs no
telemetry and opens no outbound connection unless `HALOGEN_DOWNLOAD` is set,
and leaves the model weights to their own licence.

An operator who wants this engine has two ways to get it today: run the
container beside hal0 by hand, where hal0's drift check, doctor and memory
budget cannot see it, or make it a runtime hal0 launches. Every seam the second
way needs already exists: fixed-family providers, the typed launch plan, the
runner tier system (ADR-0027), the pin table (ADR-0028), the profile-owned tune
(ADR-0029) and the sparse slot tune table (ADR-0045).

Four properties of the engine shape the decisions below:

- Everything it reads is an environment variable, read once at start. It has no
  command-line flags and no machine-readable flag schema.
- It is built for one chip and one model family. The entrypoint refuses any
  GPU whose `gfx_target_version` is not `110501`.
- It owns most of the box. Weights stay resident and the KV pool is reserved
  up front, so a 128 GB machine keeps roughly 12 GiB free at the vendor's own
  defaults. A hal0 box is not a dedicated host: hal0-api, the memory engine and
  the `utility` slot are normally resident beside it.
- Its failure mode is physical. An unclean exit can leave tens of GiB of GTT
  held by the driver with no process behind it, after which every start hangs
  at "reserving the KV pool" until the host reboots (the vendor's issue #79).
  A restart policy turns that into a loop.

## Decision

1. **halogen is an external-image runner, pinned by digest. This amends
   ADR-0028 decision 1.** The registry key is `halogen`, in a new runtime
   family `halogen`, tier `optional`, device class `gpu`, backend `rocm` only,
   format `hgn`, title `halogen (Flash-Next)`. `manifest.json` `runners.halogen`
   pins the vendor's image by tag `0.18.22` and digest
   `sha256:273714ccb64dabb7fac0418ee1b6dd83eb57d2fd44cf286eb68a7c694a27c203`,
   with `lanes_gated` empty until the hardware gate has passed on this digest.
   hal0 now has two external images, `cuda` and `halogen`, and the
   `ghcr.io/hal0ai/hal0-runner-<key>` package-name rule does not apply to either.
   A mirror under a hal0 package name was rejected for three reasons. The EULA
   allows a mirror, but the vendor's trademark policy puts package names that
   contain the marks, where a reasonable person would take them to be the
   vendor's, among the uses that need written permission. A mirror also adds a
   copy job and a second digest to keep in step. And `cuda` already set the
   precedent. `release_gate_problems` is untouched because the tier is optional.
   `HAL0_RUNNER_IMAGE_HALOGEN` and `[slots].default_images["halogen"]` (the config
   validator accepts the `halogen` key) work through the shared family-image
   resolver.

2. **An external optional row may be gated without a recipe. This amends
   ADR-0028 decision 4.** The runner-images repository gates a halogen row with
   its own `gates/halogen.toml`, taking image, tag and digest from `images.json`
   instead of a build recipe. A row's `lanes_gated` becomes `["rocm"]` only from
   a passing gate report for exactly the pinned digest. The gate file runs the
   entrypoint's `verify` mode on the `ht43` checkpoint, then starts the engine
   with hal0's seed layout and waits for `/health` to report `capability_probe`
   `ok`. It checks a chat completion, a tool call, a structured output and that
   the entrypoint logs the `host memory left for everything else` line. An
   8,192-token `sweep` records prefill and decode speed and never gates. The
   catalogue row and the gate file are runner-images #48, and the row reads
   `lanes_gated: []` until the operator has run the gate on the Strix Halo box.
   hal0 does not refuse the image meanwhile: the one launch check that reads
   `lanes_gated` admits a Vulkan lane for the llama.cpp runners
   (`pins.py:228`), and halogen has no Vulkan lane.

3. **hal0 owns the launch facts; the profile owns the free knobs; the three
   layers never overlap.** The environment is built in this precedence, later
   layers winning:

   | Layer | Variables | Source |
   |---|---|---|
   | Profile free flags | any other `HALOGEN_NAME=value` | the profile's `flags` text (ADR-0029) |
   | Slot tune | `HALOGEN_CTX`, `HALOGEN_ENABLE_THINKING`, `HALOGEN_REASONING_EFFORT`, `HALOGEN_MAX_THINKING_TOKENS`, `HALOGEN_VISION_TOWER`, `HALOGEN_DRAFTER_DEFAULT`, `HALOGEN_PLD` | `effective_tune` (ADR-0045) |
   | hal0-managed | `HALOGEN_API_PORT` (slot port), `HALOGEN_BIND=127.0.0.1`, `HALOGEN_CHECKPOINT` (model file), `HALOGEN_MODEL_ID` (registry id) | always set, never overridable |

   Also kept out of the container, whatever a profile file says:
   `HALOGEN_DOWNLOAD`, `HALOGEN_API_KEY` and `HALOGEN_API_KEY_FILE`, every
   `HALOGEN_NPU_*` name, `HALOGEN_ENGINE` and `HALOGEN_PORT`, and the file
   locators the model row owns (`HALOGEN_TOKENIZER`, `HALOGEN_NGRAM_TABLE`,
   `HALOGEN_MTP_HEAD`; the entrypoint finds those files beside the
   checkpoint by name). `HALOGEN_CACHE_DIR`,
   `HALOGEN_CACHE_FILE` and every `HALOGEN_RECORD_*` name are held back: they
   need a writable mount and a privacy decision. The provider already binds a
   writable `/cache` (`/var/lib/hal0/halogen-cache`, or `HAL0_HALOGEN_CACHE`) for
   them, but no variable points at it yet. A profile whose runner is
   `halogen` is screened family-aware: only `^HALOGEN_[A-Z0-9_]+=.+$` tokens pass
   (`profiles.halogen_flag_malformed` otherwise), every denial names its owner
   (`slot port`, `model row`, `tune`, `hal0`, or `phase 2` for the held-back
   names; `profiles.halogen_flag_denied`), the curated knobs are range-checked
   (`profiles.halogen_flag_out_of_range`), and `HALOGEN_HOST_RESERVE_GIB` below
   10 is refused because it leaves the 47.7 GiB lookup table no page cache. A
   context window past the engine's native 262,144 positions is refused unless
   the profile's flags carry `HALOGEN_ROPE_YARN` (the entrypoint would exit 2):
   the profile write answers `profiles.halogen_flag_out_of_range` and the slot
   tune write `slot.tune_unsupported_value`. The llama-server screen is
   unchanged.

4. **Tune keys are mapped or refused, never ignored.**

   | hal0 tune key | halogen variable |
   |---|---|
   | `ctx_size` | `HALOGEN_CTX` (unset leaves the engine's 262,144) |
   | `enable_thinking` | `HALOGEN_ENABLE_THINKING`: true `1`, false `0`, unset leaves the template default |
   | `reasoning_effort` | `HALOGEN_REASONING_EFFORT` (same vocabulary; `default` unset) |
   | `reasoning_budget` | `HALOGEN_MAX_THINKING_TOKENS` (`-1` unset) |
   | `vision` | `HALOGEN_VISION_TOWER=1` when `auto` and `qwen38-flash-next-vision.hgn` sits beside the checkpoint; `off` unset |
   | `speculation` | `auto` unset (the engine's MTP head plus prompt lookup); `off` sets `HALOGEN_DRAFTER_DEFAULT=0` and `HALOGEN_PLD=0` |

   `speculation = "ngram"` is **refused** for this family: the engine has no
   n-gram drafter, and quietly running `auto` instead is the silent-ignore defect
   ADR-0045 exists to prevent. The slot tune write answers
   `slot.tune_unsupported_value` (400), the profile write answers
   `profiles.speculation_unsupported` (422), a stale stored value launches with a
   warning that names the slot, and the slot's `tune_effective` read gains
   `tune_unavailable` (an addition, which ADR-0045 allows): a map from each tune
   value the engine cannot run to the reason, `{}` on a llama-server slot and
   `{"speculation": "ngram: this engine has no n-gram drafter (auto or off)"}` on
   a halogen slot, so the drawer hides the option.
   `reasoning_effort_unavailable` reads null on a halogen slot because the key is
   honoured and the engine has no runner build floor to check.

5. **The shipped profile is the "share about a third" layout, with the weights
   locked.** The seed `[profile.halogen]` sets `runner = "halogen"`,
   `HALOGEN_KV_POOL_POSITIONS=262144 HALOGEN_KV_SLOTS=2 HALOGEN_MAX_TOK=16384
   HALOGEN_WEIGHTS_LOCK=1`, `ctx_size = 262144` and `vision = "auto"`. It is
   not the engine's Quickstart (pool 524,288, four slots), because a hal0 box
   always has co-tenants. The lock is the vendor's measured difference, on a
   shared host, between a silenced engine and 81 of 81 requests answered. A
   cloned profile may change any of it. `runner = "halogen"` is the structural
   family signal, so the seed header's remark that no seed sets `runner` is
   amended. The checkpoint recommended with it is `ht43`, 8.4 GiB smaller than
   `v2`.

6. **Stop is the engine's own sequence, and a failed halogen slot stays failed.**
   The entrypoint handles SIGTERM itself (SIGTERM, up to 30 s, SIGKILL, up to
   30 s more, then a report of the GTT it left). hal0 keeps one rule around
   that: it never force-removes or SIGKILLs the container, and never removes or
   regenerates its unit, while that sequence runs. The launch plan carries a
   typed `stop_timeout_s` of 120, rendered as podman `--stop-timeout=120` and
   `TimeoutStopSec=150`. The teardown bounds its `systemctl stop` by the plan's
   value plus 30 (150 s) instead of the 20 s admin-call bound, and
   `SlotManager.terminate` waits that bound plus the 10 s teardown margin
   (160 s), so a caller never gives up before the stop does. The plan also adds
   exit statuses `1` and `2` to `RestartPreventExitStatus=64 132`, so the unit
   reads `RestartPreventExitStatus=64 132 1 2`. The entrypoint exits 1 for every
   runtime failure and 2 for a configuration it refuses (a `HALOGEN_CTX` above
   262,144 with no YaRN factor), and the vendor's own guidance is that a restart
   policy after an unclean exit can hang the next start unkillably. The unit
   therefore does not restart itself after either exit, and the slot parks in
   the error state (its unit in `failed`) with the reason in the journal; a
   start the unit has parked in `failed` ends the readiness wait within seconds
   rather than running out its 600 s. Recovery is an explicit `hal0 slot load`. The
   unit keeps `Restart=always` for every other exit status, as for any slot, so
   the guarantee is about the engine's own failure exits and not about a
   container killed from outside.

7. **Readiness, liveness and the sanity probe are the provider's.**
   `wait_ready` polls `/health` for `engine.responds` and
   `capability_probe == "ok"` within 600 s, a provider-local budget under the
   900 s warming watchdog; the generic 180 s budget is never consulted for this
   family. A `capability_probe` of `failed` on a responding engine is only the
   front end missing the engine's capability line, not a broken engine, so once
   it has held for 60 s the wait ends and the sentinel judges the engine; any
   other value keeps the wait going until the 600 s run out. The generic
   post-load sanity gate asks `/completion`, which the engine does not serve, so
   the provider runs its own sentinel instead: one `/v1/chat/completions` turn,
   thinking off, `max_tokens: 32`, `temperature: 0`, judged by the shared
   classifier, within 60 s. Once the slot is up, the fail-watcher asks the
   provider's `liveness` instead of the readiness view: `GET /health` with a 35 s
   read timeout, where HTTP 200 is alive without judging `engine.responds`, and a
   read timeout or an HTTP 503 (the front end is up and the engine's PING
   failed) reads `stalled`, still counted healthy. Only a connection that breaks,
   or any other status, strikes the slot. The engine can go silent for minutes
   under host memory pressure, and taking it down then is what leaves the driver
   holding its memory. A stall is tolerated only up to a ceiling: 10 consecutive
   `stalled` probes spanning at least 900 s (the warming watchdog's figure), with
   no answered probe between, stamp the slot ERROR with a message naming the stall
   ceiling. The engine is not killed; any answered probe resets the count. The
   per-request readiness gate waits at most 5 s on that
   probe and reads a stall as "starting", so a request gets the retryable
   `slot.loading` 503 and does not sit out the stall.

8. **The checkpoint is a registry model, staged by the operator.** A file whose
   first four bytes are `HGN1` and whose name is not a companion
   (`-ngram.hgn`, `-vision.hgn`, `-mtp.hgn`, `.overlay.hgn`) detects as kind
   `halogen`, backend `halogen`, whatever it is named; a `.hgn` name without the
   magic degrades to a low-confidence guess. A companion is never a model: the
   scan skips it and `hal0 model add` refuses it (`models.halogen_companion`,
   409), naming the checkpoint it belongs to. `.hgn` joins the default
   `[models].file_extensions`. A model whose backends are `["halogen"]` on a
   non-halogen slot, and a non-halogen model on a halogen slot, are refused by
   name (`slot.model_runtime_mismatch`, 400) at the slot write seam and again at
   load. A slot whose `binary` names halogen under a profile that launches
   another runtime is refused with `slot.runner_family_mismatch` (409), because
   the profile, not `binary`, decides the provider.
   There are no curated rows and no pull in v1; the operator downloads the
   weights and registers the file. **`HALOGEN_DOWNLOAD` is never set**: hal0 owns
   downloads, and the container stays free of outbound connections, which is what
   the EULA's "no outbound connections" default promises.

9. **Memory has one owner (ADR-0039) and hal0 owns the GTT-leak warning.**
   `halogen_footprint_mb` in `hal0.slots.capacity` books the checkpoint file, the
   KV pool at 29,500 bytes per position (16,712 with `HALOGEN_KV8=1`) for the
   larger of the pool and the slot's context window, 115 MiB per KV slot, working
   memory of `8.8 x MAX_TOK/32768 + 3.8` GiB, the vision tower (0.84 GiB) when it
   loads, and the prompt cache (115 MiB per entry, 24 entries unless
   `HALOGEN_CACHE_ENTRIES` says otherwise). The 47.7 GiB lookup table is an
   advisory of about 10 GiB of page cache and is never counted as resident.
   Eviction and the memory budget ask the provider for this figure instead of
   the file size, and the slot drawer reads the same figure from
   `GET /api/slots/{name}/resolved` as `footprint` (`resident_mb`,
   `advisory_cache_mb`, `host_free_mb`, `verdict`).

   Before a load, `HalogenProvider.preflight` refuses what this host or this
   model directory cannot serve. `check_load` runs it before pre-load eviction,
   so a refused load costs the box none of its idle slots, and `load_sync` runs
   it again. A readable KFD topology with no gfx1151 GPU node in it (another GPU,
   or no GPU node at all) is `slot.host_unsupported` (409). A topology that
   cannot be read skips that rule, so a box whose sysfs is hidden is never
   refused on a guess. A
   checkpoint that is missing, unreadable or does not start with `HGN1`, or a
   `tokenizer/tokenizer.json` that is not beside it, is `slot.model_missing`.

   hal0 also checks for GTT that nothing accounts for. The amdgpu GTT counter
   exceeds a named 8 GiB constant, no process holds the compute device (the KFD
   process table is empty), and no other slot of this box could hold GPU memory:
   none is loading, up or unloading, or in `error` with a process that may still
   be alive, and NPU and CPU slots do not count. The slot under load is left out.
   Any such slot explains the memory, so no slot is ever sized. That check **warns
   and never refuses.** The vendor's own heuristic would read a Vulkan slot's GTT
   as a leak, because Vulkan holds GTT through the render node and never appears
   under the KFD process list, so hal0 judges it from the slots it knows. Its
   counters are still host-wide while its slot list is per guest: where several
   LXC guests share one GPU, a sibling guest's Vulkan slot has exactly the leak's
   signature, and a refusal would tell the operator to reboot a healthy host.
   The warning (the `slot.gtt_leak_suspected` log line, the slot message while the
   slot is starting or warming, and a doctor row) therefore names both causes,
   another guest holding the memory or memory a previous GPU process did not give
   back (the vendor's issue #79), and says that a start on top of a real leak can
   hang at "reserving the KV pool". A figure hal0 cannot read ends the check
   silently. It runs before pre-load eviction, so the slots the eviction is about
   to unload are still resident and their memory is explained; a restart or swap
   that stops this slot's own engine first can still see its memory lag, which is
   one more reason the check only warns. The doctor row is `gtt_leak` in
   `hal0 doctor all`: it warns when the signature holds and never fails, and it
   passes with a note when the slot list or the KFD table cannot be read.
   `hal0 doctor profiles` checks each registered checkpoint. It fails (exit 1)
   when the file is missing, is not `HGN1`, or has no `tokenizer/tokenizer.json`
   beside it; it warns, with the `hf download` line that fetches it, when the
   lookup table is not beside it; the vision sidecar is only reported. The
   diagnosis id is `HAL0-HALOGEN-WEIGHTS-INCOMPLETE`.

10. **Out of scope.** The NPU sidecar (`HALOGEN_NPU_MODELS`) stays unsupported:
    ADR-0003 keeps FLM as the NPU lane, the sidecar needs the fabric clock held
    from the host, and it has open hard-hang reports upstream. The engine's
    `/v1/messages` and `/v1/responses` wires are not proxied, because hal0's
    `/v1` serves neither; a passthrough is a separate product decision. Also
    deferred: GGUF-trunk mode and `convert`, the 1M-context and attention-budget
    configurations (`HALOGEN_ROPE_YARN`, `HALOGEN_INDEXER_BUDGET`, which change the
    model's numerics and stay free text in a cloned profile), the on-disk prompt
    cache and request records, and curated rows with a multi-file pull.

11. **The fork is a mirror.** `Hal0ai/hal0gen-flash` mirrors the vendor's
    repository. The vendor refuses pull requests and the EULA forbids modified
    redistribution, so no development happens there and hal0 never ships a
    patched image.

12. **Trademark and licence compliance.** hal0 runs the vendor's unmodified
    official image and does not copy, rebuild or patch it. The pin names
    tag `0.18.22` and digest
    `sha256:273714ccb64dabb7fac0418ee1b6dd83eb57d2fd44cf286eb68a7c694a27c203`;
    the image's own labels read version `0.18.22`, revision `7be7fa7c7d59` and
    licence `LicenseRef-Peonist-EULA`. The version and digest are stated in
    `manifest.json`, and the Runner Images page shows the digest and the build
    source read from the image's own labels, so a user can find the version and
    revision the way the vendor's trademark policy asks of a distributor.
    "halogen" appears only to name the engine factually ("runs on halogen"), and
    no hal0 package or image carries the mark in its name. The EULA, the third-party notices and the
    telemetry-free behaviour travel inside the image, which hal0 does not alter.
    The weights are licensed separately by their authors and nothing here grants
    rights to them.

13. **Nothing changes for a box that never opts in.** `runner_for_backend` still
    derives `standard` for every GPU llm slot. No seed slot is added, no image is
    pulled until a slot adopts the runtime, and no model row exists until the
    operator registers one. `agent`, `utility`, `brain` and their profiles, and
    every other pin row, are untouched. The visible differences are the
    `halogen (Flash-Next)` entry in the Runtime selects (sorted after the
    default runtimes) and under Optional runtimes on the Runner Images page, and
    the `halogen` seed profile in the profile list.

## Consequences

- hal0 depends on a closed-source engine it cannot fix. A bug in the engine is
  reported upstream; hal0 can only pin a different digest or stop offering the
  runner. Every image bump is a one-file `manifest.json` change plus a gate run,
  and re-checks the curated knob table, because upstream publishes no
  machine-readable flag schema.
- Operators who share the box lose headroom. At the seed layout the engine
  books about 72 GiB resident on `ht43` and about 80 GiB on `v2`; admission
  evicts only idle, unpinned slots, so `agent`, `utility` and `npu` (pinned by
  default, unless the operator un-pins them) are not evicted for it, and a load
  that still does not fit is refused with the figures.
- A leaked GTT reservation needs a host reboot, which hal0 cannot do, and from
  inside one guest hal0 cannot tell it from a sibling guest's memory. The
  warning and the doctor row make the condition visible before a start hangs and
  leave the decision to the operator; a slot the engine itself failed does not
  retry by itself.
- Stopping a halogen slot can take up to two minutes. Anything that bypasses
  hal0's stop (`kill -9`, `podman rm -f`) can cost the operator that reboot.
- The family literal is added in several places (`RuntimeFamily` in two
  modules, the model-meta family and provider tables, the capability catalogue,
  and the dashboard's runtime-family table), so a new family still costs a sweep.
- The runner-images repository gains an external row, ungated until the gate has
  run, and an external mode in its gate script (runner-images #48); `cuda` stays
  ungated.
- Disk: a checkpoint plus the 47.7 GiB lookup table is roughly 102 GiB for `ht43`
  and 111 GiB for `v2` on the model store, mounted read-only inside the
  container.

## Source evidence

Every line number below was read on `main` at e6f3c0e6a (the runner, the model
and registry work, and the drawers all merged). Vendor files are at tag
`v0.18.22`.

- `src/hal0/runners/__init__.py`: the `RuntimeFamily` literal (`:33`), the
  `RUNNER_IMAGES["halogen"]` entry (`:235`, tier left at `Runner.tier`'s
  `optional` default, `:119`) and `runner_for_backend` (`:300`), which is
  unchanged. `manifest.json:57`: the `runners.halogen` pin, tag `0.18.22` and
  `lanes_gated: []`. `src/hal0/runners/pins.py:288`: `release_gate_problems`,
  untouched; `:222`: `env_override` (`HAL0_RUNNER_IMAGE_HALOGEN`); `:228`:
  `vulkan_capable_refs`, the one launch check that reads `lanes_gated`.
  `src/hal0/providers/_image.py:63`: `resolve_family_image`.
  `src/hal0/config/schema.py:1845`, `:1865`: `[slots].default_images` and the
  validator that accepts the `halogen` key. `tests/runners/test_registry.py:74`:
  the existing external-image precedent (`cuda`).
- `src/hal0/providers/halogen.py`: `_profile_env` (`:133`), `_tune_env` (`:150`)
  and `halogen_env` (`:173`), the environment layers and the tune mapping;
  `container_spec` (`:374`: command `all` at `:416`, the `/cache` mount at
  `:428`, `apparmor`/`seccomp` unconfined at `:433`, `ipc=host` and
  `memlock=-1` at `:438-439`, `stop_timeout_s` at `:449`, the restart-prevent
  statuses at `:452`); `stop_grace_s = 120` (`:286`); `READY_TIMEOUT_S` (`:291`),
  `CAPABILITY_GRACE_S` (`:297`) and `wait_ready` (`:667`); `liveness` (`:637`,
  its 35 s read timeout at `:301`) and `readiness_budget_s` (`:306`);
  `SENTINEL_TIMEOUT_S` (`:310`) and `verify_inference` (`:719`); `preflight`
  (`:475`); `footprint_mb` (`:551`); `prepare_load` (`:352`, the stale-`ngram`
  warning); `gtt_leak_guard` (`:282`).
- `src/hal0/runners/knobs.py`: `HALOGEN_NATIVE_CTX` (`:38`),
  `HALOGEN_TUNE_UNAVAILABLE` (`:43`), `HALOGEN_KNOBS` (`:76`, the curated
  settings), the `HALOGEN_HOST_RESERVE_GIB` floor (`:74`), `HALOGEN_MANAGED_ENV`
  (`:182`), `HALOGEN_TUNE_ENV` (`:186`) and `HALOGEN_DENIED_ENV` (`:201`, who
  owns each name), `parse_env_tokens` (`:249`), `native_ctx_problem` (`:302`),
  `_flag_owner` (`:323`) and `screen_halogen_flags` (`:381`).
- `src/hal0/providers/base.py`: `RuntimeLaunchPlan.stop_timeout_s` (`:193`) and
  `restart_prevent_exit_status` (`:198`), `Provider.stop_grace_s` (`:236`) and
  `Provider.footprint_mb` (`:294`). `src/hal0/providers/container.py`: the
  Quadlet renderer (`--stop-timeout` at `:942`, `Restart=always` at `:977`,
  `TimeoutStopSec` at `:988`, `RestartPreventExitStatus` at `:1020`),
  `unit_stop_bound_s` (`:1348`), `terminate_budget_s` (`:1373`) and
  `_UNIT_STOP_TIMEOUT_S` (`:601`, the 20 s bound every other provider keeps),
  `ContainerProvider.unload_sync` (`:2732`, the bounded stop at `:2759`),
  `check_load` (`:2470`, the fit guard and the provider `preflight` hook) and
  `load_sync` (`:2513`),
  `runner_family_mismatch` (`:1274`), the liveness dispatch (`:2295`),
  `tune_effective_for_slot` (`:3457`). `src/hal0/slot_lifecycle_budget.py:140`:
  `TEARDOWN_MARGIN_S`. `src/hal0/slots/manager.py:2964`:
  `SlotManager.terminate`, which waits for the widened bound; `:4212`:
  `_wait_ready_watching_unit`, which ends a readiness wait on a unit systemd has
  parked in `failed`. `src/hal0/slots/watchdog.py:507`: `readiness_check`, the
  per-request gate that bounds the liveness probe (`:549`); `:95`: the 900 s stall ceiling; `:85`: the 900 s
  warming watchdog. `installer/wrappers/hal0-systemctl:415`: the root-side
  `--stop-timeout` check.
- `src/hal0/profiles/__init__.py`: `_runtime_family` (`:162`, from
  `profile.runner`), `_supported_slot_types` (`:190`),
  `screen_profile_speculation` (`:554`), `screen_profile_context` (`:573`) and
  the family-aware `screen_profile_flags` (`:595`). `src/hal0/slots/tune.py:228`:
  `guard_tune_write`, which refuses `ngram` and a context past the native window.
  `src/hal0/slot_view/__init__.py:448`: `resolve_tune_effective`, which carries
  `tune_unavailable` into the slot view.
- `src/hal0/config/data/seed_profiles.toml:144`: `[profile.halogen]`.
- `src/hal0/registry/detect.py`: `HGN_MAGIC` (`:82`),
  `HALOGEN_COMPANION_SUFFIXES` (`:87`), `HALOGEN_VISION_SIDECAR` (`:98`),
  `HALOGEN_TOKENIZER_JSON` (`:109`) and `_detect_halogen` (`:466`).
  `src/hal0/registry/discover.py:455`, `:692`: the scan skips companions and
  classifies by magic. `src/hal0/services/models_service.py:2058`:
  `models.halogen_companion`. `src/hal0/config/schema.py:2719`: `.hgn` in the
  default `ModelsConfig.file_extensions`.
- `src/hal0/slots/model_fit.py`: `model_runtime_mismatch` (`:71`) and
  `slot_model_runtime_mismatch` (`:136`); `src/hal0/api/routes/slots.py` (the slot
  write seam) and `ContainerProvider.check_load` raise
  `slot.model_runtime_mismatch` (`src/hal0/slots/state.py:312`, 400).
  `src/hal0/slots/state.py:299`, `:326`: `slot.runner_family_mismatch` (409) and
  `slot.host_unsupported` (409).
- `src/hal0/slots/capacity.py`: `halogen_footprint_mb` (`:193`),
  `HALOGEN_CACHE_ENTRIES_DEFAULT` (`:185`), `HALOGEN_TABLE_CACHE_ADVISORY_MB`
  (`:190`), `provider_footprint_mb` (`:228`) and `build_per_slot` (`:717`, live
  container figure against the estimate, `:845`);
  `src/hal0/slots/preload_evict.py:247` reads the provider figure through
  `_estimate_incoming_footprint_mb`; `HalogenProvider.footprint_mb` implements
  the hook. `gtt_feasibility_verdict` (`:321`) and `halogen_footprint_readout`
  (`:364`) build the `footprint` of `GET /api/slots/{name}/resolved`
  (`src/hal0/api/routes/slots.py:1293`, `_halogen_footprint` at `:1319`).
- `src/hal0/slots/gtt_guard.py`: `GTT_LEAK_THRESHOLD_MB` (`:41`), `GFX1151`
  (`:44`), `gtt_leak_message` (`:64`, the one leak rule) and
  `slot_may_hold_gpu_memory` (`:105`). `SlotManager._guard_gtt_leak`
  (`src/hal0/slots/manager.py:2885`) runs it at load, after `check_load`
  (`:1984`) and before pre-load eviction (`:1993`, `:2005`), for a provider that
  sets `gtt_leak_guard`; the slot message is set at `:2593`.
  `src/hal0/cli/doctor_all.py:259`: `check_gtt_leak`, the `gtt_leak` row.
  `src/hal0/cli/doctor_commands.py:1904`: `check_halogen_weights`, and `:2089`
  `HAL0-HALOGEN-WEIGHTS-INCOMPLETE`.
- `src/hal0/api/routes/hardware.py:1006-1007`: `backends[].knobs` and
  `knob_denied` of `GET /api/system-info`.
- `src/hal0/registry/runner_image_sync.py:66-67`: the OCI `source` and
  `revision` labels the Runner Images page shows.
- `src/hal0/api/routes/v1.py:1503`, `:1789`: `/v1` serves `chat/completions`
  and `completions` (and embeddings, rerankings, audio and images), and no
  `messages` or `responses` route. `src/hal0/slots/reaper.py:94`: the slots
  pinned by default; `src/hal0/slots/preload_evict.py:393`: eviction skips a
  pinned slot.
- Vendor, tag `v0.18.22`: `LICENSE.md` sections 2, 3, 4 and 5 (grant,
  restrictions, benchmarking, no telemetry), `TRADEMARKS.md` (factual references
  and unmodified redistribution with version and commit stated),
  `deploy/entrypoint.sh` (`gfx_target_version` 110501 check at `:282`,
  `stop_engine` at `:852`, `wd_gtt_after_exit` at `:837`, exit 2 for a context
  past 262,144 without `HALOGEN_ROPE_YARN` at `:74-76`), `docs/host-setup.md`
  (the `host memory left` line, the weights lock, the share-a-third recipe at
  "If you must share it") and `docs/FLAGS.md` (every `HALOGEN_*` default). The
  image `ghcr.io/peonist-ai/halogen-flash-server@sha256:273714cc...` carries the
  labels version `0.18.22`, revision `7be7fa7c7d59` and licence
  `LicenseRef-Peonist-EULA`.
- `hal0-runner-images`: `images.json` (the `halogen` row) and
  `gates/halogen.toml`, added in #48.
