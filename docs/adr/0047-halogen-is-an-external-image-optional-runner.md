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
   A mirror under a hal0 package name was rejected: the EULA allows it, but the
   trademark policy lists package names that contain the marks, where a
   reasonable person would take them to be the vendor's, as needing permission, a mirror adds a copy job and a second digest to keep in step, and
   `cuda` already set the precedent. `release_gate_problems` is untouched because
   the tier is optional. `HAL0_RUNNER_IMAGE_HALOGEN` and
   `[slots].default_images["halogen"]` work through the shared family-image
   resolver.

2. **An external optional row may be gated without a recipe. This amends
   ADR-0028 decision 4.** The runner-images repository gates a halogen row with
   its own `gates/halogen.toml`, taking image, tag and digest from `images.json`
   instead of a build recipe. A row's `lanes_gated` becomes `["rocm"]` only from
   a passing gate report for exactly the pinned digest. The probes cover the
   checkpoint `verify` mode, readiness at `/health`, a chat completion, a tool
   call, a structured output and a 32k prompt.

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
   need a writable mount and a privacy decision. A profile whose runner is
   `halogen` is screened family-aware: only `^HALOGEN_[A-Z0-9_]+=.+$` tokens pass,
   every denial names the hal0 field that owns the fact, the curated knobs are
   range-checked, and `HALOGEN_HOST_RESERVE_GIB` below 10 is refused because it
   leaves the 47.7 GiB lookup table no page cache. The llama-server screen is
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
   `slot.tune_unsupported_value`, the profile write answers
   `profiles.speculation_unsupported`, a stale stored value launches with a
   warning that names the slot, and the slot's `tune_effective` read gains
   `tune_unavailable` (an addition, which ADR-0045 allows): a map from each tune
   value the engine cannot run to the reason, `{}` on a llama-server slot and
   `{"speculation": "ngram: this engine has no n-gram drafter (auto or off)"}` on
   a halogen slot, so the drawer hides the option. `reasoning_effort_unavailable` reads null because the key is
   honoured.

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
   `TimeoutStopSec=150`, and the teardown bounds its `systemctl stop` by the
   plan's value plus 30 instead of the 20 s admin-call bound. The plan also adds
   exit statuses `1` and `2` to `RestartPreventExitStatus=64 132`: the entrypoint
   exits 1 for every runtime failure and 2 for a configuration it refuses (a
   `HALOGEN_CTX` above 262,144 with no YaRN factor), and the vendor's own guidance
   is that a restart policy after an unclean exit can hang the next start
   unkillably. The unit therefore never restarts itself after the engine's
   failure exits, and the slot parks in the error state (its unit in `failed`)
   with the reason in the journal. Recovery is an explicit `hal0 slot load`.

7. **Readiness and the sanity probe are the provider's.** `wait_ready` polls
   `/health` for `engine.responds` and `capability_probe == "ok"` within 600 s,
   a provider-local budget under the 900 s warming watchdog; the generic 180 s
   budget is never consulted for this family. The generic post-load sanity gate
   asks `/completion`, which the engine does not serve, so the provider runs its
   own sentinel instead: one `/v1/chat/completions` turn, thinking off,
   `max_tokens: 32`, `temperature: 0`, judged by the shared classifier, within
   60 s.

8. **The checkpoint is a registry model, staged by the operator.** A file whose
   first four bytes are `HGN1` and whose name is not a companion
   (`-ngram.hgn`, `-vision.hgn`, `-mtp.hgn`, `.overlay.hgn`) detects as kind
   `halogen`, backend `halogen`. `.hgn` joins the default
   `[models].file_extensions`. A model whose backends are `["halogen"]` on a
   non-halogen slot, and a non-halogen model on a halogen slot, are refused by
   name (`slot.model_runtime_mismatch`) at the slot write seam and again at load.
   There are no curated rows and no pull in v1; the operator downloads the
   weights and registers the file. **`HALOGEN_DOWNLOAD` is never set**: hal0 owns
   downloads, and the container stays free of outbound connections, which is what
   the EULA's "no outbound connections" default promises.

9. **Memory has one owner (ADR-0039) and hal0 owns the GTT-leak warning.**
   `halogen_footprint_mb` in `hal0.slots.capacity` books the checkpoint file, the
   KV pool at 29,500 bytes per position (16,712 with `HALOGEN_KV8=1`) for the
   larger of the pool and the slot's context window, about
   115 MiB per KV slot, working memory of `8.8 x MAX_TOK/32768 + 3.8` GiB, the
   vision tower when it loads, and the prompt cache. The 47.7 GiB lookup table is
   an advisory of about 10 GiB of page cache and is never counted as resident.
   Eviction and the memory budget ask the provider for this figure instead of
   the file size. Before a load, hal0 refuses when the checkpoint or tokenizer is
   missing or the GPU is not gfx1151. It also checks for GTT that nothing
   accounts for: the measured GTT in use minus the live footprint of every
   resident hal0 GPU slot exceeds a named 8 GiB constant, no hal0 GPU slot is
   resident or starting, and the KFD process list is empty. That check **warns
   and never refuses.** The vendor's own heuristic would read a Vulkan slot's GTT
   as a leak, because Vulkan holds GTT through the render node and never appears
   under the KFD process list, so hal0 judges it from the slots it knows. Its
   counters are still host-wide while its slot list is per guest: where several
   LXC guests share one GPU, a sibling guest's Vulkan slot has exactly the leak's
   signature, and a refusal would tell the operator to reboot a healthy host.
   The warning (in the log, the slot message and a doctor row) therefore names
   both causes, another guest holding the memory or memory a previous GPU process
   did not give back (the vendor's issue #79), and says that a start on top of a
   real leak can hang at "reserving the KV pool". It runs before pre-load
   eviction, so the slots the eviction is about to unload are still resident and
   their memory is explained; a slot evicted by an earlier load may still read as
   a leak, which is one more reason the check only warns. The doctor row is `gtt_leak`, a warning on every run; per registered
   checkpoint the doctor also checks the file magic and the lookup table,
   tokenizer and vision sidecar beside it.

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
    revision the way the vendor's trademark policy asks of a distributor. "halogen" appears only to
    name the engine factually ("runs on halogen"), and no hal0 package or image
    carries the mark in its name. The EULA, the third-party notices and the
    telemetry-free behaviour travel inside the image, which hal0 does not alter.
    The weights are licensed separately by their authors and nothing here grants
    rights to them.

13. **Nothing changes for a box that never opts in.** `runner_for_backend` still
    derives `standard` for every GPU llm slot. No seed slot is added, no image is
    pulled until a slot adopts the runtime, and no model row exists until the
    operator registers one. `agent`, `utility`, `brain` and their profiles, and
    every other pin row, are untouched. The visible differences are the
    `halogen (Flash-Next)` entry under Optional runtimes in the pickers and on
    the Runner Images page, and the `halogen` seed profile in the profile list.

## Consequences

- hal0 depends on a closed-source engine it cannot fix. A bug in the engine is
  reported upstream; hal0 can only pin a different digest or stop offering the
  runner. Every image bump is a one-file `manifest.json` change plus a gate run,
  and re-checks the curated knob table, because upstream publishes no
  machine-readable flag schema.
- Operators who share the box lose headroom. At the seed layout the engine
  books about 72 GiB resident on `ht43` and about 80 GiB on `v2`; admission
  evicts only idle, unpinned slots, so `agent`, `utility` and `npu` (pinned by
  default, unless the operator un-pins them) are not evicted for it, and a load that still does not fit is
  refused with the figures.
- A leaked GTT reservation needs a host reboot, which hal0 cannot do, and from
  inside one guest hal0 cannot tell it from a sibling guest's memory. The
  warning and the doctor row make the condition visible before a start hangs and
  leave the decision to the operator; a failed slot never retries by itself.
- Stopping a halogen slot can take up to two minutes. Anything that bypasses
  hal0's stop (`kill -9`, `podman rm -f`) can cost the operator that reboot.
- The family literal is added in several places (`RuntimeFamily` in two
  modules, the provider-tag and provider-name tables, the capability catalogue,
  and the dashboard's family-to-slot-type and runtime-family tables), so a new
  family still costs a sweep. Tests pin each one.
- The runner-images repository gains an external gated row and an external mode
  in its gate script; `cuda` stays ungated.
- Disk: a checkpoint plus the 47.7 GiB lookup table is roughly 100 to 110 GiB on
  the model store, mounted read-only inside the container.

## Source evidence

Cited by line where the code is on `main`; by module and name where it is new
with this change.

- `src/hal0/runners/__init__.py`: the `RuntimeFamily` literal, the
  `RUNNER_IMAGES["halogen"]` entry (tier left at `Runner.tier`'s `optional`
  default) and `runner_for_backend`, which is unchanged. `manifest.json`: the
  `runners.halogen` pin. `src/hal0/runners/pins.py:288`: `release_gate_problems`,
  untouched. `src/hal0/providers/_image.py:63`: `resolve_family_image`.
  `tests/runners/test_registry.py:75`: the existing external-image precedent
  (`cuda`).
- `src/hal0/providers/halogen.py`: `halogen_env` and `_tune_env` (the
  environment layers and the tune mapping), `HalogenProvider.container_spec`
  (command `all`, `ipc=host`, `memlock=-1`, `apparmor`/`seccomp` unconfined, the
  120 s `stop_grace_s`, restart-prevent statuses), `READY_TIMEOUT_S` and
  `wait_ready` (600 s), `SENTINEL_TIMEOUT_S` and `verify_inference` (60 s).
- `src/hal0/runners/knobs.py`: `HALOGEN_KNOBS` (the curated settings),
  `HALOGEN_MANAGED_ENV`, `HALOGEN_TUNE_ENV` and `HALOGEN_DENIED_ENV` (who owns
  each name), `screen_halogen_flags`, the `HALOGEN_HOST_RESERVE_GIB` floor and
  `HALOGEN_TUNE_UNAVAILABLE`.
- `src/hal0/providers/base.py`: `RuntimeLaunchPlan.stop_timeout_s` and
  `restart_prevent_exit_status`, `Provider.stop_grace_s` and
  `Provider.footprint_mb`. `src/hal0/providers/container.py`: the Quadlet renderer
  (`--stop-timeout`, `TimeoutStopSec`, `RestartPreventExitStatus`),
  `_provider_for_family`, `ContainerProvider.unload_sync` and
  `SlotManager.terminate` (stop bounded by the plan's `stop_timeout_s + 30`; every
  other provider keeps the 20 s `_UNIT_STOP_TIMEOUT_S`), `load_sync` (the fit
  guard and the provider `preflight` hook, run before any unit is written) and
  `tune_effective_for_slot`.
- `src/hal0/profiles/__init__.py`: `_runtime_family` (from `profile.runner`),
  `_supported_slot_types`, `screen_profile_speculation` and the family-aware
  `screen_profile_flags`. `src/hal0/slots/tune.py`: `guard_tune_write`, which
  refuses `ngram`. `src/hal0/slot_view/__init__.py`: `resolve_tune_effective`,
  which carries `tune_unavailable` into the slot view.
- `src/hal0/config/data/seed_profiles.toml`: `[profile.halogen]`.
- `src/hal0/registry/detect.py`: `HGN_MAGIC`, `HALOGEN_COMPANION_SUFFIXES`,
  `HALOGEN_VISION_SIDECAR` and `_detect_halogen`. `src/hal0/config/schema.py`:
  `.hgn` in the default `ModelsConfig.file_extensions`.
- `src/hal0/slots/model_fit.py`: `model_runtime_mismatch` and
  `slot_model_runtime_mismatch`; `src/hal0/api/routes/slots.py` (the slot write
  seam) and `ContainerProvider.load_sync` raise `slot.model_runtime_mismatch`.
- `src/hal0/slots/capacity.py`: `halogen_footprint_mb`,
  `HALOGEN_TABLE_CACHE_ADVISORY_MB`, `provider_footprint_mb` and `build_per_slot`
  (live container figure against the estimate); `src/hal0/slots/preload_evict.py`
  reads the provider figure through `_estimate_incoming_footprint_mb`;
  `HalogenProvider.footprint_mb` implements the hook.
- `src/hal0/slots/gtt_guard.py`: `GTT_LEAK_THRESHOLD_MB`, `GFX1151` and
  `gtt_leak_message`, the one leak rule. `SlotManager._guard_gtt_leak` runs it at
  load, before pre-load eviction, for a provider that sets `gtt_leak_guard`.
  `HalogenProvider.preflight` refuses a non-gfx1151 GPU and a missing checkpoint
  or tokenizer. `src/hal0/cli/doctor_all.py`: `check_gtt_leak`, the `gtt_leak` row.
- `src/hal0/api/routes/v1.py:1503`, `:1789`: `/v1` serves `chat/completions`
  and `completions` (and embeddings, rerankings, audio and images), and no
  `messages` or `responses` route. `src/hal0/slots/reaper.py:94`: the slots
  pinned by default; `src/hal0/slots/preload_evict.py:377`: eviction skips a
  pinned slot.
- Vendor, tag `v0.18.22`: `LICENSE.md` sections 2, 3 and 5 (grant, restrictions,
  no telemetry), `TRADEMARKS.md` (factual references and unmodified
  redistribution with version and commit stated), `deploy/entrypoint.sh`
  (`gfx_target_version` 110501 check, `stop_engine`, `wd_gtt_after_exit`),
  `docs/host-setup.md` (the `host memory left` line, the weights lock, the
  share-a-third recipe) and `docs/FLAGS.md` (every `HALOGEN_*` default).
