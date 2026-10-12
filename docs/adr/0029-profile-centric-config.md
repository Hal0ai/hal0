# ADR-0029: Profile-centric config: the model holds file facts, the profile holds every tune, the slot holds placement

## Status

Proposed (2026-10-12). It supersedes the v1.0 "hardware/model write
boundary", which had no ADR. That boundary came from the
`spec-flags-ownership` and `spec-hw-slot-ownership` design specs (now only in
git history) and is described in `docs/concepts/slots.mdx` ("The
hardware/model write boundary"). This ADR amends ADR-0028 decision 7 by adding
a second one-shot conversion that runs straight after the runner-system one.
It keeps the profile-carried runtime (`ProfileConfig.runner`).

## Context

Three objects carry tuning today, and none of them owns it cleanly.

- **The model row owns the launch tune.** `ModelDefaults.extra_args`
  (`src/hal0/registry/model.py:76`) is the `model_extra_args` argv segment
  (`src/hal0/providers/container.py:1213-1217`). `defaults.context_size`
  (`model.py:64`) is the authoritative window
  (`container.py:_resolve_context_size`, `_model_declared_ctx`).
  `enable_thinking` (`model.py:135`) is the request-time reasoning default
  (`api/routes/v1.py:444-459`). `vision` (`model.py:147`) gates `--mmproj`.
  Because these live on the model, one model can only be tuned one way. Two
  slots that want the same file at different context sizes or with different
  sampling had to share one row, and the old folds refused to run when that
  happened ("divergent share", `updater.py` `_OWNERSHIP_MIGRATIONS`,
  `config/migrations/slot_flags_fold.py`).
- **A profile is a copy-on-stamp template, not a tune.**
  `POST /api/models/{id}/seed-profile` (`api/routes/models.py:824-879`)
  copies `profile.flags` into `defaults.extra_args` and records
  `defaults.profile` as provenance. At launch, a profile's flags apply only
  through two side doors. One is the divergence overlay, used when
  `slot.profile` differs from the provenance (`container.py:1964-1988`). The
  other is the unstamped-model template, used when the model has neither a
  tune nor a provenance (`container.py:2012-2037`). The non-llama providers
  (kokoro, moonshine, qwen3tts, comfyui) do the opposite and read profile
  flags live (`providers/kokoro.py:146-152`, `moonshine.py:320-384`,
  `qwen3tts.py:174-180`, `comfyui.py:205,256`).
- **The slot still carries tuning keys, and most of them do nothing.**
  `[server].extra_args`, `parallel` and the slot `chat_template` are accepted
  and persisted, but the launch path ignores them:
  `del profile_flags, slot_parallel, extra_args` at `container.py:1191`, and
  `resolve_chat_template` reads only the model (`config/schema.py:1485-1505`).
  `[model].context_size` is a "ceiling" with a special case: on the derived
  path it *replaces* the 32768 dense cap (`container.py` `_dense_cap_for`).
  FLM, by contrast, reads the slot context as its real window
  (`providers/flm.py:743-764`).

A production install shows what this costs. The `agent` slot carries
`[server].extra_args = "-ctk q8_0 -ctv q8_0"`, and the rendered Quadlet
`Exec=` line has no `-ctk`, so the KV cache runs at f16. The same argv has
`--no-mmproj` (from the `thinking` profile) *and* `--mmproj <file>` (from
the model). llama.cpp's `common_params_handle_models` clears `mmproj` whenever
`no_mmproj` is set, so the slot has no vision even though the model drawer
shows it as on. The `embed` slot names no profile and launches without
`--embedding`. Three slots name profiles that do not exist and fall back to
`chat` without any visible sign.

Three CLIs exist to move tuning between these objects: `migrate-flags`,
`migrate-caps` and `migrate-hw` (`cli/slot_commands.py:1222-1567`). They run
only by hand. They refuse while units are live and when two slots share a
model with divergent tunes. `hal0 update` reports them through
`detect_pending_ownership_migrations` / `convergence_report`
(`updater.py:2385-2476`, `cli/update_commands.py:277-354`). As a result, any
box can still carry every pre-1.0 shape, and every read-side shim for those
shapes has to stay.

## Decision

### 1. Ownership

| Field | Owner | Notes |
|---|---|---|
| path, file set, size, sha256, quant, license, hf repo/filename | **Model** | unchanged |
| architecture, native context (`metadata.context_length`) | **Model** | file facts |
| mmproj path | **Model** | file fact; whether it loads is the profile's `vision` |
| chat template id (`auto` = GGUF-embedded) | **Model** | which template this file needs |
| `jinja` (tri-state) | **Model** | whether this file's template works with `--jinja`; hal0 injects `--jinja` when the runner supports it, unless this is `false` or the profile is in embedding/reranking mode |
| `mtp` (tri-state) | **Model** | **means "the file has MTP heads"**; `None` falls back to the `mtp` tag. It is no longer a switch: turning MTP off is the profile's `mtp = "off"` |
| tokenizer repo, capabilities/modalities, `tool_calling`, per-type default marker, provider, tags | **Model** | unchanged |
| family quirks | **Model family** | `config/data/family_defaults.toml`, keyed by architecture; ships empty today |
| `flags` (free text: KV cache types, batch/ubatch, sampling, reasoning flags, `--parallel`, everything else) | **Profile** | screened at write and again at launch (see 2) |
| `ctx_size` (int or unset) | **Profile** | unset means the model's native window, dense-capped at 32768, or 8192 if the native window is unknown |
| `enable_thinking` (tri-state) | **Profile** | request-time reasoning default for the slot that runs this profile |
| `vision` (`auto` / `off`) | **Profile** | `auto` loads the model's mmproj when it has one |
| `mtp` (`auto` / `off`) | **Profile** | `auto` drafts when the model has heads and the runner supports it |
| `runner` (runner key or unset) | **Profile** | kept: the profile-carried runtime. Applying the profile sets `slot.binary`, and the device too for a single-backend runner (`slots/config_write.py:121-164`) |
| `intent`, `cloned_from` | **Profile** | informational |
| id, name, type, port | **Slot** | |
| device, gpu_index, n_gpu_layers, threads, metrics, binary, image_pin, `[server].env` | **Slot** | placement |
| model, profile | **Slot** | which file, which tune |
| idle_timeout_s, pinned, autoload, priority, default, `[npu]`, `[image]`, TTS request defaults | **Slot** | serving and lifecycle; unchanged by this ADR |

A model runs differently by running under a different profile. Two slots that
want different tunes for one file name two profiles. Nothing is shared that
could conflict, so divergent-share refusals go away.

### 2. One launch composition

llama.cpp argv is built from four layers in this order. It is then
deduplicated last-wins by `slots/argv.py` (`resolve_argv`):

1. **base** (hal0-managed): `--host --port --model --alias --ctx-size`.
   `--ctx-size` comes from the profile's `ctx_size` rule.
2. **model**: family quirks, `--jinja`, `--chat-template-file`, and `--mmproj`
   (only when `vision` is `auto`).
3. **profile**: the MTP draft bundle (`config/schema.py:960`
   `build_mtp_flag_bundle`) when the profile's MTP is in effect, then `flags`,
   so a profile's own `--spec-draft-*` values win over the bundle defaults.
4. **slot**: `-ngl`, `--threads`, `--metrics`, and the MTP
   `--spec-draft-device` derived from the slot's device. Today that device is
   derived from the profile's inert `backend` hint (`container.py`
   `build_mtp_flag_bundle(getattr(profile, "backend", None))`). No seed sets
   that hint, so every MTP slot, Vulkan and CUDA included, is told to draft on
   `ROCm0` (`config/schema.py:957,970`).

Profile flags may not set what another layer owns. Hardware flags
(`slots/argv.py` `SLOT_HARDWARE_FLAGS`), managed flags
(`MANAGED_ARGS_DENYLIST`, which covers `--ctx-size`), model-fact flags
(`--jinja`, `--chat-template*`, `--mmproj`, `--no-mmproj`, `--mmproj-auto`)
and `--spec-draft-device` are rejected when a profile is saved and refused
again at launch. `--reasoning-format none` stays rejected (#2545). The
non-llama providers already read profile flags live; they keep doing so.
The self-managed engines (kokoro, moonshine, qwen3-tts) are the one place a
profile names a file: their bundle has no model row (`slots/state.py`
`SELF_MANAGED_PROVIDERS`; a shipped Kokoro slot names no model), so its
`--model_path` stays in the engine's profile.

The resolved-argv preview (`GET /api/slots/{name}/resolved`) reports these
four layers as provenance labels: `base`, `model`, `profile`, `slot`.

### 3. What happens to every tuning field that exists today

| Today | Becomes |
|---|---|
| model `defaults.extra_args` | part of the slot's profile `flags` (converter) |
| model `defaults.context_size` | profile `ctx_size` |
| model `defaults.enable_thinking`, `defaults.vision` | profile `enable_thinking`, `vision` |
| model `defaults.profile` (provenance / preferred profile) | consumed by the converter, then deleted; a model no longer picks a profile. A swap keeps the slot's profile. Type-implied profiles (`slots/profile_adopt.py:175`) still apply at create |
| model `defaults.mtp` | `true` stays as the model fact "has MTP heads"; an explicit `false` is operator tuning and becomes the slot profile's `mtp = "off"` (the model's value is cleared, so the `mtp` tag decides the fact) |
| model `defaults.jinja`, `chat_template`, `tokenizer_repo` | model facts; lifted out of `defaults` (see 6) |
| model `defaults.rope_freq_base`, DB `n_gpu_layers`, `preferred_runner` | deleted (never emitted: `container.py:1210`; NGL is slot-owned; runner is `slot.binary`) |
| slot `[model].context_size` (and the old `[model].ctx_size` / top-level `ctx_size`) | profile `ctx_size` where it changed what launched (a ceiling below the model window, or one that replaced the dense cap); FLM's real window; otherwise dropped and reported |
| slot `[server].extra_args`, `parallel`, `chat_template`, `workers` | inert today: dropped and reported (see 7, "inert values") |
| slot `[model].n_gpu_layers`, `[model].rope_freq_base`, `[model].extra` | dropped (inert) |
| slot `backend`, `runtime`, `enabled` | normalized by the converter (`backend` becomes `device` when `device` is absent; the others are stripped), so `SlotConfig` can forbid unknown keys |
| slot bare `image` | `image_pin` on a comfyui slot, where it is honoured (`providers/comfyui.py:195`); dropped and reported on a llama slot, where `_resolve_image_ref` reads only `image_pin` (`container.py:302-358`) |
| stack rows' `mtp`, `vision`, `enable_thinking`, `server_extra_args` | inert today (stack apply never used them): dropped and reported. A row naming the profile its slot ran before conversion is re-pointed to the slot's converted profile, so re-applying the stack does not revert the slot |
| a stack's embedded `profiles` | converted like any custom profile; a model-fact flag blocks the stack |
| profile `mtp` (bool, informational), `device_class`, `backend`, `quant` | deleted; profile `mtp` returns as `auto`/`off` |
| `profiles.toml` `legacy_seeds_migrated`, `legacy_seeds_adopted` | deleted together with `config/data/legacy_seed_profiles.toml` |
| seed profiles' `--model_path` (kokoro, moonshine, qwen3-tts) | unchanged: the self-managed engines' bundles have no model row (decision 2) |

### 4. Profiles: seeded and custom

Seeds stay virtual and read-only (code-shipped, `config/data/seed_profiles.toml`).
Operator and converter profiles live in `profiles.toml`. The seed set must
cover every slot hal0 ships. The shipped `agent` and `coder` slot seeds name
`dense` and `coding` (`installer/etc-hal0/slots/agent.toml:44`,
`coder.toml:36`), which exist only as demoted seeds that a fresh install adopts
on first reference (`profiles/__init__.py` `ProfileCatalog._materialize_legacy`).
That adoption path is deleted with this ADR. This ADR promotes seeds with
canonical names: `agent` (long context, `ctx_size = 65536`; the bundled
Hermes agent's floor is 64,000), `utility` (`ctx_size = 65536`) and `coding`,
and points the shipped slots at them. Slots are built around the seeds hal0
ships, never the other way round.

Seeds keep their canonical names. When an install already has a custom
profile with a seed's name (a `coding` adopted on 1.2–1.3), the conversion
compares the two tunes: an identical custom is removed and its slots simply
run the seed; a different one is renamed to `<name>-custom` (or `-custom-2`,
`-custom-3`… when that name is already taken, so no stored profile is ever
overwritten) and every slot and stack row naming it is re-pointed, reported. A demoted seed a fresh install
would have adopted on first reference is written as a custom profile in the
new shape (no `--jinja`, `--no-mmproj` becomes `vision = "off"`), unless the
install's adoption ledger says it was deleted.

The installer's VRAM clamp (`install/static_seeds.py:213-288`,
`install/orchestrate.py:194-215,593-607`) stops writing the slot's
`[model].context_size`. When the clamped window differs from the seed's, it
writes a custom profile, `<seed>-ctx<N>`, and points the slot at it, using the
same naming rule as the converter.

### 5. Slot API and UI follow the table

The slot write paths (`POST /api/slots`, `PUT /api/slots/{name}/config`, stack
apply, `SlotManager.create/update_config`) reject `context_size`/`ctx_size`,
`extra_args`, `parallel`, `chat_template`, `mtp`, `enable_thinking` and
`vision` with `slot.tuning_key_denied`, whose message is "this belongs on a
profile". That code replaces `slot.model_owned_key_denied`. After conversion
`SlotConfig`, `ModelConfig` and `ServerConfig` forbid unknown keys. A slot
file the converter could not convert fails to load with `slot.unconverted`,
which names the converter report.

### 6. API, MCP and UI changes (1.x minor, with a CHANGELOG Breaking note)

- **Removed:** `POST /api/models/{id}/seed-profile`; `PATCH /api/slots/{name}/defaults`
  and the MCP `slot_set_defaults` tool (a model swap goes through
  `PUT /config` / `slot_edit` / `model_swap`); the `ownership_migrations` /
  `convergence` payloads on the update job and status; the slot fields
  `ctx_max`, `chat_template`, `parallel`, `llamacpp_args` and `rope_freq_base`
  in `slot_view`.
- **Changed:** the `Model` object loses `defaults`. `chat_template`, `jinja`,
  `mtp` and `tokenizer_repo` become top-level fields (`PUT /api/models/{id}`,
  `POST /api/models`, `/validate`, scan commit, MCP `model_edit` /
  `model_validate`). A `defaults` key in a write is a 422.
  `POST /api/models/feasibility` takes the context to test from the request
  (a profile name or a number) rather than from `defaults.context_size`.
- **Changed:** profile bodies (`POST/PUT /api/profiles`, import/export,
  `profile_create` / `profile_update` / `profile_generate`) gain `ctx_size`,
  `enable_thinking`, `vision` and `mtp` (`auto`/`off`), and lose
  `device_class`, `backend` and `quant`. The export envelope moves to profile
  schema 2; schema-1 envelopes are refused with a "re-export from hal0 1.5"
  message. The shipped `community/addons/*.json` are regenerated in the same
  change. `GET /api/profiles` adds `used_by` (slot names) to every entry.
- **Changed:** slot responses gain `ctx_effective` and `ctx_source`
  (`profile` / `native` / `fallback`), produced by the one resolver that the
  launch path, `slots/capacity.py`, `agents/anchor_window.py`,
  `agents/hermes_provision.py` and `cli/doctor_all.py` also call. A stack's
  `StackSlotEntry` loses `vision`, `mtp`, `enable_thinking` and
  `server_extra_args`, and the stack schema moves to 3.
- **Thinking default:** `/v1` resolves `enable_thinking` from the profile of
  the slot that serves the request, after slot selection, not from the model
  id (`api/routes/v1.py:444-459`).
- **Dashboard:** the model drawer keeps only facts: chat template, jinja,
  MTP heads, mmproj, and the native window shown read-only. Its flag editor,
  context input, the Thinking/MTP/Vision override ledger, "seed from
  profile", the divergence diff and re-stamp all go. The profile drawer gains
  ctx, thinking, vision and MTP. The slot drawer loses the context ceiling.
  It shows the effective context and its source, has "Edit profile" and
  "Duplicate and edit" next to the profile picker, and labels the resolved
  command by layer. The stacks editor loses the per-row MTP control. The
  Updates page loses its convergence panel.
- **Bundled agents:** the `hal0-tune` skill's "apply a winning config"
  section (`installer/agent-skills/hal0-tune/SKILL.md:130-139`) is rewritten.
  It now creates or edits a profile and points the slot at it. The
  codebase-map reference and the MCP tool descriptions are updated in the same
  change.

### 7. The conversion

`hal0.config.migrations.profile_centric` moves an install onto this model
exactly once. It follows ADR-0028's contract:

- **Where it runs.** In `run_post_activation_migrations`, straight after
  `migrate_runner_system` and fatal like it, so a converted install's runner
  keys are already current. It also runs in `hal0 serve` before the API module
  is imported (`cli/main.py:245`), never on import. It takes the same locks
  (`hal0_config_file_lock`, `slot_write_lock`) and also honours its own restore
  marker. `hal0 profile migrate` previews it, `--apply` runs it, and
  `--restore <archive>` reverses it.
- **Inputs.** Raw TOML for every slot file, `profiles.toml` and `stacks.toml`,
  the model rows (with the tuning columns read back from the holding table
  once migration 012 has moved them), and the target seed catalog. Nothing is
  validated through the new pydantic models first. When an id-keyed and a name-keyed file carry the same slot, only the
  one the loader uses (the id-keyed file, `SlotManager._config_file_for`) is
  converted; the other is reported as shadowed and left alone.
- **Every slot is evaluated, and both launches are compared.** A frozen copy
  of today's resolution (`profile_centric_frozen`) computes what each slot
  launches now: the argv after last-wins with the segment that set each flag,
  the context window, the thinking default and whether the projector actually
  loads. The tune is the part set by the profile and model-tune segments. Each
  flag in it is routed to its new home:
  - `--jinja` is dropped (hal0 adds it);
  - `--no-mmproj` / `--no-mmproj-auto` become `vision = "off"`;
  - `--mmproj-auto` is dropped;
  - `--threads` moves to the slot when the slot sets none;
  - `--spec-draft-device` is dropped (the slot's device decides it);
  - `--reasoning-format none` is dropped;
  - a hardware flag the profile path stripped at launch is dropped;
  - `--chat-template*`, `--mmproj <path>` and `-dev` have no profile home and
    block the slot.

  The slot keeps its profile when that profile, in the new shape, holds the
  same tune; otherwise the converter generates one with
  `cloned_from = <the profile it ran>`. An explicit model `mtp = false`
  becomes the profile's `mtp = "off"`. The new rules are then applied to the
  converted slot, model and profile (`profile_centric.new_launch`, which the
  launch-path change implements and is pinned to), and the two launches are
  compared flag by flag, plus the context window, the thinking default and
  whether the projector loads. Every difference is either one of the launch
  changes below or blocks the slot.
- **Naming and dedupe.** The name is `slug(<profile that launched>-<model id>)`.
  For a model-less slot that has a ceiling, it is
  `slug(<profile>-ctx<N>)`. When the slug is longer than 32 characters, or
  is already taken by a profile with a different tune, the name becomes the
  first 26 characters plus `-` plus 5 hex characters of the tune's hash.
  Identical tunes produce one profile, deduplicated against every profile in
  the new catalog (seeds, converted customs and profiles generated in the same
  run). The same input always produces the same name.
- **Model-less slots.** A ceiling equal to the profile's window (its
  `ctx_size`, else the 32768 cap) is dropped, because it means the same
  thing. Any other ceiling becomes `<profile>-ctx<N>`. An unparsable ceiling
  is inert today and is reported.
- **FLM.** `providers/flm.py` `build_env` uses the slot's context, else 8192.
  After conversion the window is always explicit: every FLM slot's profile
  carries `ctx_size` equal to what it runs today, and the launch path reads
  only that. The FLM trio shadows (`flm-stt`, `flm-embed`) run no process of
  their own; their window is their anchor's, so they keep the window they
  had through the anchor's conversion and are not converted themselves. The
  `flm` seed (16384) is what a fresh install runs.
- **Unbound models.** A model that has tuning but is bound to no slot gets a
  generated profile that nothing is assigned to, so operator data is never
  silently lost. Rows whose only setting is `mtp = true` need nothing.
- **Launch changes are reported, never hidden.** The converter preserves what
  launches, except in these four cases, where today's state is broken or
  cannot be expressed:
  - A typed slot with no profile, or naming one that does not exist, gets its
    type's seed (`embedding`, `reranking`). `embed` gains `--embedding`.
  - `--jinja` is no longer added in embedding or reranking mode.
  - An MTP slot gets its own device's draft device, where today the profile's
    `backend` hint (`ROCm0` when unset) decided it, including an explicit
    `--spec-draft-device` in a model tune.
  - A model that forced MTP on (`mtp = true`) on a runner without MTP support
    stops drafting.

  A non-llama slot whose profile resolves to nothing (a profile-less Kokoro
  slot asks for the non-seed `tts` and fails to launch today) gets its
  engine's seed. Each such slot is listed as "changes at next restart".
- **Inert values are never applied.** The conversion preserves what launches
  (operator decision, 2026-10-12). Slot `extra_args`, `parallel` and
  `chat_template`, a ceiling that never took effect, stack-row tuning and
  non-llama model `extra_args` are dropped. Each value is recorded verbatim in
  the report and the backup, so an operator who wants one can put it on the
  slot's profile. There is no option to fold them in.
- **Running units.** The converter does not refuse to run while units are
  live, and that is safe because for every slot not on the launch-change list
  the result is effective-argv-equivalent. After writing, it re-renders units
  as `migrate_runner_system` does (`rerender_slot_units`) and compares each
  slot's newly rendered argv with the plan. The check uses a strict render
  (`providers.container.strict_slot_argv`), not the fail-open preview.
  - A rendered argv that differs blocks only that slot. Each slot file is
    separate, so its original goes back, a stack row re-pointed to follow it
    is put back, and any profile generated for it alone stays in
    `profiles.toml`, unreferenced. The difference is reported as BLOCKED,
    and the rest of the install converts. The model tuning that slot still
    launches from is kept.
  - Any error while rendering (a registry or catalog error, a file that does
    not read) is not a mismatch. It rolls back every write, as does a failure
    writing a shared file (`profiles.toml`, `stacks.toml`) or the registry.
    The next start or update tries again.
  The plan renders a model with no registry row (never pulled, or deleted)
  as the launch path does: `--model <model id>`
  (`_best_effort_model_info`), so a stale slot converts like any other. The
  previously rendered `Exec=` line is also compared. A difference there is
  pre-existing drift and is reported only. Running containers keep running;
  launch-change slots show the existing config-drift badge until they are
  restarted.
- **Fail loudly.** These cases leave that slot's file untouched and name it in
  the report; every other slot still converts:
  - the slot's TOML does not parse;
  - flag text does not `shlex`-split;
  - an untrusted segment carries a managed flag (today's launch already
    refuses this);
  - its tune, or the profile it keeps, carries a flag with no profile home
    (`--chat-template*`, `--mmproj <path>`, `-dev`);
  - the converted launch differs from today's outside the four launch changes;

  A stored profile that cannot be converted (a flag with no profile home, or
  mixed shapes) is reported as blocked even when no slot uses it, and so is a
  stack whose embedded profile cannot be. `hal0 profile migrate` exits 2 while
  anything is blocked, and `--json` lists every blocker (`<slot file>`,
  `profile:<name>`, `stack:<name>`). `hal0 update` prints each blocker with
  its reason and the fix, and exits 2 (applied, not converged).

  A blocker never takes the rest of the box down:
  - The demoted-seed markers (`legacy_seeds_migrated` / `legacy_seeds_adopted`)
    move out of `profiles.toml` into `/var/lib/hal0/profile-centric-ledger.json`
    on the first run, whatever is blocked, so `profiles.toml` is always in the
    strict new shape. Only the frozen resolver reads the ledger, so a deleted
    demoted profile is never re-adopted.
  - The profile loader quarantines a single invalid entry, such as a
    mixed-shape profile left as it is: it is logged, kept verbatim on disk,
    and resolves to `profiles.invalid` for the slots that name it. The rest of
    the catalog loads.
- **Database.** SQL migration `012_model_tuning_holding.sql` copies `profile`,
  `extra_args`, `n_gpu_layers`, `context_size`, `rope_freq_base`,
  `preferred_runner`, and the `_enable_thinking` / `_vision` keys of `extra`
  into `legacy_model_tuning`. It leaves the `model` columns in place.
  - Once 012 has run, the converter reads only the holding table, and drops
    it once nothing is blocked. Because of this, the order of "DB opened" and
    "converter ran" cannot lose data.
  - Nothing new reads or writes the old columns. They stay so that a rollback
    to an older release finds the schema it expects, even while that
    release's `hal0-api` is still running during the update.
  - A later release drops them with its own migration, once the upgrade floor
    is past this release (HAL0-SUNSET v1.7.0 in `db/repository.py`).
- **Backup.** Before the first write, the converter writes
  `/var/lib/hal0/backups/profiles-<ts>.tar.gz` (mode 0600). It holds every file
  the converter changes, plus a JSON dump of the holding table. There is no
  backup without a write and no write without a backup.
- **Shape is read from content, which makes it idempotent.** Nothing records
  what has been converted; each object says it.
  - A **profile** carrying a key only the new shape has (`ctx_size`,
    `enable_thinking`, `vision`, a string `mtp`) is already converted. It is
    kept exactly as it is and never handed to the frozen resolver, where
    `mtp = "off"` or `ctx_size` would mean something else.
  - A profile carrying a key only the old shape has (a boolean `mtp`,
    `device_class`, `backend`, `quant`, `image`), or flags the new shape
    refuses, is converted.
  - A profile with only the shared keys (`flags`, `runner`, `intent`,
    `cloned_from`) and flags already in the new form reads the same in both
    shapes.
  - A profile mixing old and new keys blocks, and is left as it is.
  - A **slot** is already converted when its file carries no old-shape key,
    its model row carries no tuning, and it names no stored old-shape profile
    (nor a neutral one a new seed now shadows). No profile at all, a seed
    name, a new-shape profile, or a name nothing defines all count as new
    shape. The last one fails at launch with `slot.profile_missing` for the
    operator to fix, rather than being re-pointed on every boot. Anything
    else is evaluated, whatever an earlier run did. That includes an
    old-shape slot file added after a conversion.
  - Old-shape slot keys next to an already-converted profile cannot be
    evaluated, so that slot blocks.
  - A converted slot whose model row still carries tuning keeps the slot as
    it is. The tuning no longer reaches the slot, so it is kept as a profile
    of its own and cleared from the model, as for a model no slot uses.
  - **Known limit.** A hand-written old slot file with no old-shape key that
    names a seed, over a model with no tuning, reads as converted and runs the
    new seed. Every slot file an older hal0 wrote carries one: its writer
    stamped `workers`, and the shipped llama slots carried a
    `[model].context_size`.
    `runtime` is still a valid slot key, so it marks nothing; an `[image]`
    settings table is not the old image pin and stays.
  - `/var/lib/hal0/profile-centric.json` is only an audit record of the last
    run (written 0640). It is never consulted.
- **Restore** (for a downgrade below the release that ships this).
  - `hal0 update --rollback` does it in one command. When the target release
    predates the conversion (its tree has no `profile_centric_apply.py`),
    `Updater.rollback` restores every conversion backup the audit record
    lists, newest first, before the swap. It runs inside `hal0-api`, which
    is the service user the conversion ran as, under the config locks.
  - The restore writes back the files the converter owns, puts the held
    tuning back into the `model` columns, drops the holding table, and
    removes migration 012's row.
  - When a backup is missing or unreadable, the rollback refuses and prints
    the manual steps: stop `hal0-api`, run
    `hal0 profile migrate --restore <backup>` for each backup newest first,
    start `hal0-api` again (the restore marker keeps it from converting), then
    roll back again.
  - Afterwards a version-keyed marker makes the boot-time conversion stand
    down, as `runner_system.restore_backup` does.

### 8. What is deleted

- The CLIs `migrate-flags`, `migrate-caps` and `migrate-hw`
  (`cli/slot_commands.py:1222-1567`, keeping `active_hal0_units` at `:911`).
- The fold modules `config/migrations/slot_flags_fold.py`, `model_owned_caps.py`
  and `hw_slot_ownership.py`.
- `detect_pending_ownership_migrations`, `_OWNERSHIP_MIGRATIONS`,
  `convergence_report` and the "pre-v1.0 slot/model shape" panel
  (`cli/update_commands.py:277-354`, `api/routes/updater.py:396`, the
  `install.sh:2998-3036` heredoc).
- `clear_stale_mtp_overrides` and `sanitize_model_extra_args`, whose subjects
  no longer exist.
- The copy-on-stamp machinery: the seed-profile route,
  `apply_preferred_profile` / `preferred_profile_for`,
  `defuse_stale_mtp_on_swap`, the `slot_profile` and `slot_profile_template`
  segments, and `_resolve_profile_or_base`'s silent fallback (a missing
  profile now refuses to launch with `slot.profile_missing`).
- `ModelDefaults`; slot `ModelConfig.context_size`/`n_gpu_layers`/
  `rope_freq_base`; `ServerConfig.extra_args`; `SlotConfig.parallel`/
  `chat_template`/`workers`; `_effective_parallel`; the Quadlet `PodmanArgs`
  hatch for plan `extra_args` (`container.py:971-990`) once no provider passes
  it; the `ctx_size` alias folds in `slot_config/__init__.py`;
  `config/data/legacy_seed_profiles.toml` and `LEGACY_SEED_PROFILES`.
- Their tests, UI surfaces and docs (`docs/concepts/slots.mdx` "The
  hardware/model write boundary" is rewritten as "What the model, the profile
  and the slot own").

## Options considered

- **Keep the model as the tune owner and finish the folds (the v1.0
  boundary).** Rejected by the operator. One file can only be tuned one way,
  and divergent shares need a policy no choice satisfies.
- **Per-slot overrides on top of a profile** ("profile + slot extra_args").
  Rejected. It recreates two owners for one flag, and the slot's free-text
  `extra_args` is exactly the field that went inert without anyone noticing.
- **Convert intent instead of effect** (apply every inert slot value, or offer
  it as an option). Rejected by the operator. It would change live slots at
  their next restart; for example, a production `agent` slot would move from
  an f16 to a q8_0 KV cache. The report lists every such value instead.
- **Name generated profiles after slots.** Rejected. A profile shared by two
  slots would carry one slot's name, and slot names are operator labels the
  framework must not design around.

## Consequences

- One object answers "how does this run": the profile. Two slots can run the
  same file differently with no shared state between them.
- The launch path loses its two side doors (overlay and template) and one
  silent fallback, so every slot's argv can be explained layer by layer.
- Every pre-1.0 slot and model shape converts automatically, which unblocks
  deleting the shims listed in legacy-inventory row 4.
- 1.x clients that write `defaults`, `PATCH /defaults` or `seed-profile`
  break. The dashboard and the bundled agents ship in the same change.
- `hal0 update rollback` across this release needs `hal0 profile migrate
  --restore`, like the runner conversion.

## References

- ADR-0006, ADR-0023, ADR-0025, ADR-0027, ADR-0028
- `docs/concepts/slots.mdx`, `docs/concepts/capabilities-and-profiles.mdx`
- `src/hal0/providers/container.py` (`_llama_argv_segments`,
  `_resolve_llama_scalars`, `_resolve_context_size`)
- `src/hal0/slots/argv.py`, `src/hal0/config/migrations/runner_system.py`
