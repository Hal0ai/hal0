# ADR-0028: A fresh runner system: one key per runner, one pin table, one conversion

## Status

Accepted (2026-10-11). Supersedes the runner parts of ADR-0025 (its default
model and agent-lane decisions stand, its fork-freezing and migration plan do
not). Amends ADR-0006 decision 3 and ADR-0027 decisions 1, 2 and 4.

## Context

By October 2026 the runner images had grown four overlapping naming schemes
and three sources of truth:

- runner keys (`rocmfpx`, with `vulkanfpx` as a permanent alias, `strix`,
  `promptforge`), catalogue ids (`rocmfpx-combined`, `combined-upstream`),
  package names (`hal0-combined`, `hal0-toolbox-*`, `hal0-strix-vulkan`,
  `hal0-promptforge`, `hal0-comfyui`) and constants (`DEFAULT_ROCMFPX_IMAGE`,
  `VULKAN_FIXED_IMAGE`, `FALLBACK_*`);
- image refs pinned in three places: `src/hal0/config/schema.py` literals,
  `manifest.json` `toolbox_images` (plus `_legacy_toolboxes`), and a
  hand-kept `VULKAN_CAPABLE_IMAGE_REFS` set, with
  `exports/runner-image-pins.json` and the installer's preflight
  (sed-parsing `schema.py`) derived by hand from the first;
- a retag sweep and stale-ref sets that rewrote old pins on update, an alias
  fold applied at every config read, an arch denylist, and a specialty system
  for one model family.

Every fork behind the non-default runners had gone quiet (ADR-0025's context)
and upstream llama.cpp carried the model support that mattered. The operator
decided to build the runner system again from scratch rather than migrate the
old one piece by piece: delete the old code paths, aliases, shims and
fixtures, and convert existing installs with exactly one migration.

## Decision

1. **One key per runner, everywhere.** The hal0 runner key is the registry
   key, the `manifest.json` pin row, the catalogue id and the package name
   `ghcr.io/hal0ai/hal0-runner-<key>`. Runners: `standard` (title
   **Standard**, the AMD llama.cpp runner, ROCm and Vulkan), `cpu`, `flm`,
   `kokoro`, `moonshine`, `comfyui` (tier `default`); `qwen3tts` and `cuda`
   (tier `optional`). `cuda` pins ggml-org's `server-cuda-b<N>` image instead
   of a hal0 build. `rocmfpx`, the `vulkanfpx` alias, `strix` and
   `promptforge` are removed, with no alias. The `frozen` tier is removed;
   nothing uses it.
2. **One pin table.** `manifest.json` `runners` maps each key to `image`
   (bare repo), `tag` (immutable), `digest` and `lanes_gated` (backends the
   hardware gate passed for that digest). It is the only place an image is
   named. `hal0.runners.pins` reads it; `Runner.image`, the Vulkan-capable
   set, `exports/runner-image-pins.json`, the bench harness lanes,
   `hal0 doctor toolbox-pull` and `installer/lib/preflight.sh` (reading the
   JSON with python3) all derive from it. A routine runner bump is a one-file
   PR to that table. `HAL0_RUNNER_IMAGE_<KEY>` overrides one runner's ref.
3. **Vulkan admission is data next to the digest.** A runner's image may serve
   the `gpu-vulkan` llama.cpp lane only when the runner runs on Vulkan and the
   pin's `lanes_gated` names `vulkan`. The rule of #1888 stays (evidence per
   digest, never an ordering over tags); the hand-kept set is gone.
4. **Release gate.** `scripts/release-check.sh` runs
   `scripts/check_runner_pins.py`, which fails while any default-tier runner
   has a null tag or digest, while `standard` and `cpu` name different
   llama.cpp releases, or while a published `cuda` pin is not a
   `server-cuda-b<M>` tag with `M` at or below `standard`'s release. A `cuda`
   pin more than 50 releases behind warns. Until the runner-images repo
   publishes the new packages, every row is a placeholder (`tag: null`) and the
   gate refuses to cut a release.
5. **Fork-only quants are unsupported.** ROCmFPX-family (ROCmFP3/4/6/8,
   ROCmFPX), IU4 and ActiveFPX GGUFs need a fork no runner ships. They are
   refused at model add, pull, scan and launch with one message
   (`models.unsupported_quant`, `slot.unsupported_quant`) instead of being
   routed to a special runner. The specialty system, the arch fit-check and
   the ROCmFP4 model filter in the slot drawer are deleted with them.
6. **The catalogue is the runner list.** The runner-images repo's
   `images.json` uses schema `hal0.runner-images.v2` (`id` = runner key,
   `title`, `summary`, `image`, `tag`, `digest`, `backends`, `lanes_gated`,
   `family`, `tier`); v1 is no longer parsed. hal0 keeps one catalogue row per
   entry whose id is a runner it ships, maps rows to runners by key, and prunes
   every other row on a successful sync, downloaded or not (images already in
   podman's store are a separate fact). It syncs in the background at
   `hal0-api` boot, during `hal0 update`, and on demand.
7. **One conversion.** `hal0.config.migrations.runner_system` moves an install
   onto the new system: slot runtimes and profile runtimes to the new keys,
   `[slots].default_images` renamed or dropped, image pins to retired hal0
   images removed (an operator's own image is kept and reported), `HAL0_TOOLBOX_IMAGE_*` in `api.env` renamed, model
   `preferred_runner` leftovers renamed, and slots whose model is a fork-only
   quant keep their settings but lose the model, each reported. It runs on raw
   TOML before config validation (first in the post-update migrations and
   first thing at `hal0-api` startup), backs up every file it changes first,
   restores them itself on any failure, and is idempotent.
   `hal0 migrate runners` previews it; `--apply` runs it; `--restore` puts a
   backup back.

## Options considered

- **Migrate piece by piece, keeping aliases (ADR-0025's plan).** Rejected by
  the operator: it keeps every old path alive for a release cycle and leaves a
  frozen fork runner nobody bumps.
- **Keep a frozen `rocmfpx` runner for one release** so FPX slots keep running.
  Rejected: the conversion unassigns those models with a report instead; a
  plain GGUF of the same model loads on `standard`.
- **Name the default runner `upstream`.** Rejected in favour of `standard`,
  which matches the title operators already see.
- **A separate lock file for the pins.** Rejected: `manifest.json` already
  ships with every release and is read by the installer.

## Consequences

- `schema.py` loses every runner-image constant; `hal0.runners` loses the
  alias, stale-ref, arch and FPX tables; the updater loses the retag sweep;
  the slot routes lose the arch fit-check; `/api/system-info` loses
  `backends[].is_default` (use `tier`).
- The default curated chat model is `qwen3.8-27b` (ADR-0025 decision 4) and
  the install-time agent offer is that model (ADR-0027 decision 6); the
  ROCmFP4 curated rows, the FPX brain variants and the `hal0-quantize` agent
  skill are removed.
- Bench lanes use the runner images' `/opt/hal0/bin/llama-bench`.
- A box running an old release keeps pulling its old packages until it
  updates; the runner-images repo must keep those packages until no
  supported release names them (its retention allowlist owns that).

## References

- ADR-0006 (every shipped image is a registry runner)
- ADR-0025 (upstream llama.cpp default runner; runner parts superseded here)
- ADR-0027 (a runner's tier is the one source of "default")
- `manifest.json`, `src/hal0/runners/pins.py`, `src/hal0/runners/__init__.py`
- `src/hal0/config/migrations/runner_system.py`
- `docs/guides/manage-runner-images.mdx`
