# hal0 1.0 — Seeded profile rework

> **Date**: 2026-07-20
> **Scope**: A (audit 8 seeded profiles + drift), B (brain.toml rework per
> spec-p3-brain §5), profile-catalog rework (16-profile catalog, slot-shape
> schema change, slot→profile mapping, drift fixes). Bundled per user call.
> **Source spec**: Layered shape follows
> [`spec-hw-slot-ownership.md`](../rework/hal0-specs/spec-hw-slot-ownership.md)
> (ratified 2026-07-19); flags-materialization follows
> [`spec-flags-ownership.md`](../rework/hal0-specs/spec-flags-ownership.md)
> §1–§6. This spec amends §10 of `spec-hw-slot-ownership.md` from 11 to 16
> profiles (adds family-specific + workload-specific profiles on top of the
> canonical workload set).
> **Branch target**: `rework/descar`. The currently-checked-out `main` has
> unrelated dirty work (`ui/src/dash/slot-modals.jsx`, graphify-out, shepherd
> scratch) — switch and verify clean before opening the PR.

---

## 1. Decisions locked (this session)

1. **Layered shape** = `SLOT | PROFILE | per-slot [model].defaults.extra_args`
   per `spec-hw-slot-ownership.md` §1 (Slot = hardware, Profile = device-agnostic
   logical-tune template, Model = materialized flag text).
2. **`family_defaults.toml` cleared** — schema layer stays in `src/hal0/config/data/`,
   but the `[family]` entries are emptied for this work (delete the
   `[family].gemma = "..."` row). Family-specific recipes now ship as
   `profile.<family>-<variant>` entries in `seed_profiles.toml` (combined with
   workload defaults per slot choice).
3. **`profile.brain`** = single combined profile (Brain steward workload +
   MiniCPM5-1B-Agentic-Tooluse quirks). Brain slot is 1:1 with its model, so a
   single profile is cleaner than overlay.
4. **`chadrock` splits** into `profile.chadrock-dense` (27B coder) and
   `profile.chadrock-moe` (35B Saber MoE). Generic `profile.moe` /
   `profile.dense` stay as model-agnostic fallbacks (stripped of chadrock-
   specific kv-cache/sampler; current flag values move to the chadrock-* profiles).
5. **New profile additions for 1.0**: `thinking` (reasoning ON), `coding`
   (code-gen tuned), `brain`, `chadrock-dense`, `chadrock-moe`.
6. **No vision slot or profile for 1.0** — vision stays dynamically scaffolded
   per `setup_command._SETUP_SLOTS["vision"]` / `_SCAFFOLD_CAPS` (port 8087);
   no static seed TOML, no catalog profile. Revisit in a later spec.
7. **New static seeds**: `coder` (port 8082) + `embed` (port 8083) +
   `qwen3tts` (port 8095 — drift fix). Total: **10 static seeds**.
8. **Slot-shape schema change** is bundled (one feature, atomic rollout).

---

## 2. Layered shape (cite, don't restate)

Follows `spec-hw-slot-ownership.md` §1–§6 verbatim:

| Layer | Owns | Surface |
| --- | --- | --- |
| **Slot** | Hardware: `device`, `n_gpu_layers` (NGL), `threads`, `binary` (runner image ref), `image_pin` (escape hatch). Plus `[server].env` (runtime env vars). | `installer/etc-hal0/slots/<name>.toml` |
| **Profile** | Device-agnostic **logical** tune template: chat template, sampler defaults, reasoning, KV type, batch sizes (`-b/-ub`), `-fa`, `--no-context-shift`, `--no-mmproj`, capability (`mtp`/`jinja`/`chat_template`/modality), `intent`. NO image, NO runner, NO device, NO `-ngl`, NO `--threads`. | `src/hal0/config/data/seed_profiles.toml` |
| **Model** | Materialized flag text (copied from a profile template at edit time per spec-flags-ownership §3), plus typed capability fields. Per-instance overrides live here. | registry row → bound at slot assignment |
| **Per-slot `[model].defaults.extra_args`** | Per-instance overrides (beats profile + model defaults). | `installer/etc-hal0/slots/<name>.toml` `[model].extra_args` |

**`SLOT_HARDWARE_FLAGS` denylist** (per spec-hw-slot-ownership §5):
`{-ngl/--n-gpu-layers, -dev/--device, --threads/-t}` — model + profile save
hard-rejects these.

---

## 3. Slot schema changes

### 3.1 Fields to POPULATE in seeded slot TOMLs (per `spec-hw-slot-ownership.md` §2)

The schema (`src/hal0/config/schema.py:300` `SlotConfig`) already has these
fields declared. Seed TOMLs need to populate them:

- **`n_gpu_layers`** (int, default `-1` = all layers / 0 = CPU only). Per slot.
- **`threads`** (int, default `0` = unset → launcher omits `--threads`). Per slot.
- **`binary`** (str, image ref key into `hal0.runners.RUNNER_IMAGES`). Per slot.
  Default `""` = derive HW-gated default from `device`.
- **`image_pin`** (str | None, default `None`). Escape hatch for debug/A-B.
- **`[server].env`** (dict, runtime env vars like `HSA_OVERRIDE_GFX_VERSION`).
  Already declared; populating is optional.

### 3.2 Fields to STRIP from seeded slot TOMLs

- **`[server].extra_args`** — INERT at launch per spec-flags-ownership §4; migrator
  folds effective tune into the bound model's `defaults.extra_args`. Keep the
  field for round-trip but stop *writing* operational flags here (move to model
  defaults via the migrator).
- **`parallel`** — HAL0-SUNSET v1.0.0; folded into model `defaults.extra_args`.
  Already removed from seed TOMLs (no static seed carries it today).
- **`chat_template`** — HAL0-SUNSET v1.0.0; model-intrinsic. Already not in seeds.
- **`workers`** — HAL0-SUNSET v1.0.0; inert. Already not in seeds.
- **`[model].n_gpu_layers`** — HAL0-SUNSET v1.0.0; folded into slot `n_gpu_layers`.
  Already not in seeds.

### 3.3 Field semantics to encode in each seeded slot TOML

For 1.0, every seeded slot populates `n_gpu_layers` and `threads` with
sane-default values matching the slot's `device`:

| device | n_gpu_layers | threads | rationale |
| --- | --- | --- | --- |
| `gpu-vulkan` | `-1` (all) | `0` (unset) | Vulkan runtime handles GPU layers; CPU count left to runtime |
| `gpu-rocm` | `-1` (all) | `0` (unset) | ROCm runtime handles GPU layers |
| `gpu-rocmfp4` | `-1` (all) | `0` (unset) | ROCmFP4 same as rocm |
| `cpu` | `0` (CPU only) | `8` (safe default for hal0 boxes) | CPU only; explicit thread count |
| `npu` | n/a (FLM doesn't take `-ngl`) | `0` | FLM is its own runtime; thread count N/A |

`binary` is left as `""` (derive from `device`) for all 1.0 seeds — operators
can pin a specific image via `image_pin` if they need to A-B or rollback.

---

## 4. Profile catalog (16 profiles for 1.0)

### 4.1 Catalog structure

`src/hal0/config/data/seed_profiles.toml` becomes the **workload + family
recipe library** — every entry is a complete, device-agnostic logical tune.
The slot picks ONE profile. Profiles carry:

```toml
[profile.<name>]
flags = "<chat template + sampler + reasoning + KV + batch>"
mtp = true|false          # informational only — not read at launch (spec-hw-slot-ownership §10)
jinja = true|false        # if true, profile uses --jinja flag (already in flags)
intent = "<one-line purpose>"
quant = "<ROCmFP4|BF16|W4ABF16|>"   # quant hint for registry metadata only
```

**Removed from every profile**: `device_class` (slot owns device). Removed
flag fragments from every profile: `--parallel`, `--metrics`, `--no-webui`,
`--poll`, `--poll-batch`, `--slot-prompt-similarity`, `--no-mmap`, `-tb`,
`-ngl`, `-dev`, `--threads`, `--main-gpu`, `--tensor-split`, `--split-mode`,
`-ngld` — these are slot/operational/managed-arg surface, not model tune.

### 4.2 The 16 profiles

**Kept (11) from `spec-hw-slot-ownership.md` §10** — current seeds refactored
to strip hardware/operational flags + `device_class`:

| Profile | runtime family | intent | flag recipe source |
| --- | --- | --- | --- |
| `chat` | llama-server | generic chat (fallback) | minimal: `-fa on --jinja -b 2048 -ub 512` |
| `chat-long-context` | llama-server | long-context chat | current flags minus hardware bits |
| `dense` | llama-server | generic dense workload | stripped of chadrock kv-cache/sampler |
| `moe` | llama-server | generic MoE workload | stripped of chadrock kv-cache/sampler |
| `embedding` | llama-server | pooled embeddings | `--embedding -fa on -b 8192 -ub 8192` |
| `reranking` | llama-server | reranking | `--reranking -fa on -b 8192 -ub 8192` |
| `cpu-chat` | llama-server | CPU-safe chat | current `--threads-batch 8 -b 256 -ub 256` (the `--threads-batch` is hardware-adjacent but llama-server specific; keep) |
| `flm` | FLM | NPU chat/embed/STT | current empty `flags = ""` |
| `kokoro` | Kokoro | CPU TTS | current `--model_path ...` |
| `qwen3-tts` | Qwen3-TTS | GPU TTS | current `--model_path ... --default_voice Ryan --default_language Auto` |
| `comfyui` | ComfyUI | image generation | current `--disable-mmap --bf16-vae --cache-none` (ComfyUI's own flags, not llama-server) |

**New (5)** — workload + family combined:

| Profile | type | flag recipe source | rationale |
| --- | --- | --- | --- |
| `brain` | workload + family | MiniCPM5-1B-Agentic-Tooluse model card + spec-p3-brain §5a (Brain steward's tool-call limitations) | Brain steward is 1:1 with MiniCPM5-1B. Small-batch (1B model), reasoning off, no native toolcalls on FPX — tool turns route via `tool_model`. Combined profile is cleaner than overlay. |
| `chadrock-dense` | family (27B dense coder) | `jcbtc/chadrock3.6-27b-pi-agent-rocmfp4-mtp` model card (chadrock launch card) | Current `profile.dense` flags were already distilled from this card — they move here verbatim. 27B dense + ROCmFP4 + MTP-capable + mmproj (vision). |
| `chadrock-moe` | family (35B MoE Saber) | `jcbtc/chadrock-35b-ace-saber-rocmfp4-mtp` model card (Saber launch card) | Current `profile.moe` flags were already distilled from this card — they move here verbatim. 35B MoE A3B + ROCmFPX MoEQuality + MTP. |
| `thinking` | workload (reasoning ON) | distilled from `qwen3-5-9b-deepseek-v4-flash-mtp-q6-k` + `qwen3-6-35b-a3b-halostrix-dyn-mtp-v7` + `chadrock3-6-35b-uncensored-mtp-strix-lean` model cards (the three reasoning-capable families in our registry) | Workload-level: any reasoning-capable model. Reasoning budget defaults (`--reasoning-format deepseek` etc.), higher top-k for thinking mode, min-p allowed. Family-specific bits (MTP draft heads etc.) fold into per-family defaults at model materialize time. |
| `coding` | workload (code-gen) | distilled from `qwen3-coder-next` + `qwopus3-6-27b-coder-mtp-q6-k` + `Qwen3-Coder-30B-A3B-Instruct-GGUF` model cards | Workload-level: code-gen tuned. Higher temp for code-gen creativity, no reasoning (coders are non-reasoning), specific sampler (`--temp 0.7 --top-p 0.95 --top-k 40`). Used by the new `coder` slot (port 8082). |

### 4.3 What goes in each profile's `flags` (final shape)

Per profile, the `flags` string is the union of:

- Chat template: `--jinja` (always for chat families; absent for non-chat like
  embedding/reranking/tts/comfyui)
- Context: `-c <window>` (model-specific; default dense-capped per
  `ModelConfig.context_size` floor)
- Reasoning: `--reasoning on|off` + `--reasoning-format <fmt>` +
  `--reasoning-budget <n>` (off for chat/coding/brain/cpu-chat; on for thinking;
  family-specific format for chadrock-deepseek / qwen3-deepseek)
- KV cache: `-ctk q8_0|q4_0|f16 -ctv q8_0|q4_0|f16` (default per profile; model
  family defaults can override per `family_defaults`-equivalent layered at model
  materialize time — but `family_defaults.toml` data is cleared, so the override
  goes into the family-specific profile itself, e.g. `chadrock-moe` pins
  `-ctk f16 -ctv f16`)
- Flash attention: `-fa on|off` (default on for GPU profiles; off for some
  edge cases)
- Sampler: `--temp <n> --top-p <n> --top-k <n>` (model-tuned defaults; coding
  uses higher temp, thinking uses lower temp)
- Batch: `-b <n> -ub <n>` (workload default; slot can override per-instance)
- Multimodal: `--no-mmproj` or `--mmproj` (only for vision profiles; not in 1.0)
- Capability toggles: `--embedding` (embedding), `--reranking` (reranking)
- TTS/ComfyUI engine-specific: `--model_path`, `--default_voice`,
  `--default_language`, `--bf16-vae`, etc.

---

## 5. Slot → profile mapping (10 static seeds + dynamic scaffolds)

### 5.1 Static seeds (10 files in `installer/etc-hal0/slots/`)

| Slot | Port | device | runtime | profile | enabled | `[model].default` | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `brain` | 8089 | `gpu-vulkan` | container | `brain` | `true` | `MiniCPM5-1B-Agentic-Tooluse` | Per spec-p3-brain §5a — Brain is platform steward, ships working |
| `agent` | 8081 | `gpu-vulkan` | container | `chadrock-moe` | `false` | (none) | ADR-0023 anchor; model-less at seed per §5b/c (readiness gate warms it) |
| `utility` | 8090 | `gpu-vulkan` | container | `chat` | `false` | (none) | Operator-customizable generic chat; current port 8090 (free in 8081-8099 pool) |
| `flm` | 8088 | `npu` | container | `flm` | `false` | (none) | NPU; chat default on, asr/embed via `[npu]` table |
| `img` | 8188 | `gpu-rocm` | container | `comfyui` | `false` | (none) | ComfyUI sidecar; `[image]` table with `idle_restore_minutes=60` |
| `qwen3tts` | 8095 | `gpu-rocm` | container | `qwen3-tts` | `false` | (none) | **NEW static seed** (drift fix — see §5.4) |
| `tts` | 8085 | `cpu` | container | `kokoro` | `false` | (none) | **port drift fix** (was 8084 → 8085 per `_SETUP_SLOTS["tts"]`) |
| `rerank` | 8086 | `gpu-vulkan` | container | `reranking` | `false` | (none) | **port drift fix** (was 8083 → 8086 per `_SETUP_SLOTS["rerank"]`) |
| `coder` | 8082 | `gpu-vulkan` | container | `coding` | `false` | (none) | **NEW static seed** (per `_SETUP_SLOTS["coder"]`) |
| `embed` | 8083 | `gpu-vulkan` | container | `embedding` | `false` | (none) | **NEW static seed** (per `_SETUP_SLOTS["embed"]`) |

### 5.2 Dynamic scaffolds (no static seed TOML for 1.0)

Per `setup_command._SETUP_SLOTS` / `_SCAFFOLD_CAPS`:

| Slot | Port | Profile | Notes |
| --- | --- | --- | --- |
| `vision` | 8087 | (none — scaffolded) | Operator picks a vision model + manually sets `profile = "chat"` or similar; no profile.vision in 1.0 |
| `stt` | 8084 | (none — scaffolded) | Whisper is whisper.cpp (not llama-server); profile schema doesn't apply; operator binds Whisper model directly |

### 5.3 Spec-p3-brain §5 must-lands (in `installer/etc-hal0/slots/brain.toml` + `agent.toml`)

Per `docs/rework/hal0-specs/spec-p3-brain.final.md` §5:

- **`schema.py:2835` `BrainChatConfig.tool_model` default** flips `""` → `"hal0/agent"` (5a). *(spec-p3-brain §5a cites line 3003 — the field has moved since the spec was written; verified against `rework/descar` tip.)*
- **`brain.toml` docstring correction** — currently recommends
  `tool_model = "hal0/code"`; spec §5a mandates `hal0/agent`. Update comment.
- **`agent.toml` posture** — stays `enabled = false` + model-less at seed (5b/5c);
  readiness gate warms when operator binds a model. Per §5b reconcile-on-provision
  is a design call — spec says "consider seeding enabled (or document
  reconcile-on-provision)" — we choose **reconcile-on-provision** because model-
  less + surprise-download-free is the cleanest WS-E #1107 pattern.
- **`brain/readiness.py`** (per §5c) — separate spec/PR, not bundled here.

### 5.4 Drift fixes (this spec)

| File | Drift | Fix |
| --- | --- | --- |
| `installer/etc-hal0/slots/qwen3tts.toml` | File exists on disk but missing from `STATIC_SEED_SLOTS` tuple and `install.sh:1666` loop — never gets copied on fresh install | Add `qwen3tts` to both registries; file becomes a real static seed |
| `installer/etc-hal0/slots/rerank.toml` | Port `8083` conflicts with `_SETUP_SLOTS["embed"] = 8083` | Change to `port = 8086` per `_SETUP_SLOTS["rerank"]` |
| `installer/etc-hal0/slots/tts.toml` | Port `8084` conflicts with `_SETUP_SLOTS["stt"] = 8084` | Change to `port = 8085` per `_SETUP_SLOTS["tts"]` |

### 5.5 Static-seed registry sync (3-way)

| Source | Path | Current | After |
| --- | --- | --- | --- |
| Python tuple | `src/hal0/install/static_seeds.py:34-42` `STATIC_SEED_SLOTS` | `(flm, tts, rerank, utility, img, agent, brain)` | `(flm, tts, rerank, utility, img, agent, brain, qwen3tts, coder, embed)` |
| Bash loop | `installer/install.sh:1666` `for seed_slot in ...` | `flm tts rerank utility img agent brain` | `flm tts rerank utility img agent brain qwen3tts coder embed` |
| Mirror (already correct) | `src/hal0/cli/setup_command.py:36` `_SETUP_SLOTS` | includes `coder`, `embed`, `agent`, `brain` entries | unchanged (already aligned) |

---

## 6. Files add / touch summary

**Add**:

- `installer/etc-hal0/slots/coder.toml` (new static seed; profile=`coding`, port=8082)
- `installer/etc-hal0/slots/embed.toml` (new static seed; profile=`embedding`, port=8083)

**Rewrite** (slot TOMLs gain `n_gpu_layers`, `threads`; strip deprecated
fields; populate new profile refs):

- `installer/etc-hal0/slots/brain.toml` (update docstring for `tool_model` per §5.3; keep `profile = "brain"`)
- `installer/etc-hal0/slots/agent.toml` (flip `profile = "chat"` → `profile = "chadrock-moe"`; keep model-less + disabled)
- `installer/etc-hal0/slots/utility.toml` (keep `profile = "chat"`)
- `installer/etc-hal0/slots/flm.toml` (no profile change)
- `installer/etc-hal0/slots/img.toml` (no profile change)
- `installer/etc-hal0/slots/qwen3tts.toml` (no profile change; port stays 8095)
- `installer/etc-hal0/slots/tts.toml` (port 8084 → 8085)
- `installer/etc-hal0/slots/rerank.toml` (port 8083 → 8086)

**Rewrite** (`seed_profiles.toml` becomes the 16-profile catalog):

- `src/hal0/config/data/seed_profiles.toml` — refactor existing 11 (strip
  `device_class`, strip hardware/operational flags); add 5 new
  (`brain`, `chadrock-dense`, `chadrock-moe`, `thinking`, `coding`).

**Edit**:

- `src/hal0/install/static_seeds.py` — extend tuple by 3 (`qwen3tts`, `coder`, `embed`).
- `installer/install.sh:1666` — extend bash loop by 3.
- `src/hal0/config/data/family_defaults.toml` — clear `[family].gemma = "..."`
  (and any other entries); leave `[family]` table empty / remove the table.

**Edit** (spec-p3-brain §5 must-land):

- `src/hal0/config/schema.py:2835` — `tool_model` default `""` → `"hal0/agent"`.

**Tests** (new):

- `tests/slots/test_seed_profiles.py` — every seed profile loads; flag string
  is parseable (no `SLOT_HARDWARE_FLAGS` violation); no `device_class` field;
  no `--parallel/--metrics/--no-webui/--no-mmap/-tb/-ngl/-dev/--threads`
  fragments in flags.
- `tests/slots/test_static_seeds.py` — extend to assert the 10-slot tuple;
  drift assertions (every seed slot TOML exists; every `STATIC_SEED_SLOTS`
  entry has a corresponding file; port matches `_SETUP_SLOTS`).
- `tests/slots/test_slot_schema.py` — every static seed TOML validates against
  `SlotConfig`; `n_gpu_layers` and `threads` are populated per §3.3.

---

## 7. Migration / rollout

1. **Snapshot-first** — `hal0 slot snapshot` before any seed update (idempotent
   one-shot migration per `spec-hw-slot-ownership.md` §6).
2. **No existing-operator-data loss** — `seed_static_slots()` in `static_seeds.py`
   is non-destructive (only seeds when slot absent). New seeds (`qwen3tts`,
   `coder`, `embed`) get the same treatment — operator who already has these
   defined manually is untouched.
3. **Port drift fixes** (`rerank.toml` 8083→8086, `tts.toml` 8084→8085) are
   *seed-only* changes. Per the brain.toml docstring pattern
   ("existing installs keep whatever their file says — seeds never overwrite"),
   boxes that already have `rerank.toml` or `tts.toml` with their old ports are
   untouched. Only fresh installs get the corrected ports.
4. **`schema.py:3003` tool_model default flip** — non-breaking for boxes that
   already have `[brain_chat] tool_model = "<something>"` in their config (their
   value wins); breaking for boxes that relied on the empty default — those boxes
   will start routing TOOL turns to `hal0/agent` automatically. Document in
   release notes.
5. **`family_defaults.toml` clear** — clearing the data doesn't break loading
   (the schema still validates empty); boxes that *had* the gemma override get
   generic moe/dense kv-cache defaults. Document in release notes.

---

## 8. Risks

1. **Slot TOML schema migration in-flight** — `SlotConfig.parallel`,
   `SlotConfig.chat_template`, `SlotConfig.workers`, `SlotConfig.model.n_gpu_layers`,
   `SlotConfig.server.extra_args` all carry HAL0-SUNSET comments. This spec
   populates the *new* fields (`n_gpu_layers`, `threads`, `binary`, `image_pin`)
   and stops writing the sunset fields. The actual sunset-ratchet drop is a
   later PR — coordinate.
2. **Model materialization timing** — spec-flags-ownership §1 says flags
   attach to models via materialization (profile→model text on edit). The 1.0
   profile catalog defines the *templates*; the materialization step is part of
   the model-drawer UI flow. If a box loads a slot from a static seed WITHOUT
   going through the model drawer (e.g. `hal0 setup` first-run), the materialization
   may not have happened yet — verify the static-seed path also materializes
   the profile's flags into the bound model.
3. **chadrock model card availability** — `profile.chadrock-dense` /
   `profile.chadrock-moe` flag recipes depend on `jcbtc/chadrock3.6-27b-pi-agent-rocmfp4-mtp`
   and `jcbtc/chadrock-35b-ace-saber-rocmfp4-mtp` model cards. If either card
   disappears from HF, the profile's source-of-truth disappears. Mitigation:
   cache the card content under `docs/rework/model-cards/` (per HF-MIRROR pattern).
4. **Brain model MiniCPM5-1B card** — Brain profile recipe depends on the
   MiniCPM5-1B-Agentic-Tooluse model card. Same mitigation as #3.
5. **Operator surprise on `tool_model` default flip** — see §7.4. Boxes that
   ran 0.9.x with `tool_model = ""` and a working brain setup may now route
   TOOL turns to `hal0/agent` (which is disabled + model-less by default per
   §5b/c) → readiness gate degrades to read-only instead of 500. This is the
   intended behavior per spec-p3-brain §5c but operators may notice. Document
   in release notes + dashboard toast on first load post-upgrade.
6. **Port drift fixes are seed-only** — see §7.3. Operators who installed
   pre-1.0 keep their old ports; fresh installs get the corrected ones.
   No automatic migration of existing port conflicts.

---

## 9. Verification

Before claiming done:

1. **`pytest tests/slots/ tests/capabilities/ tests/installer/`** green.
2. **`grep -rn "device_class" src/hal0/config/data/seed_profiles.toml`** returns
   no matches (every profile's `device_class` removed).
3. **`grep -nE '\-ngl|\-\-threads|\-tb|\-dev|\-\-parallel|\-\-metrics|\-\-no\-webui|\-\-no\-mmap|\-\-slot\-prompt\-similarity|\-\-poll' src/hal0/config/data/seed_profiles.toml`**
   returns no matches (no hardware/operational flags in profiles).
4. **`grep -n 'profile = ' installer/etc-hal0/slots/*.toml`** matches the
   §5.1 table (every seeded slot has its target profile).
5. **`python -c "from hal0.install.static_seeds import STATIC_SEED_SLOTS;
   assert len(STATIC_SEED_SLOTS) == 10; assert set(STATIC_SEED_SLOTS) ==
   {'flm','tts','rerank','utility','img','agent','brain','qwen3tts','coder','embed'}"`**.
6. **`grep -n 'for seed_slot' installer/install.sh`** matches
   `flm tts rerank utility img agent brain qwen3tts coder embed`.
7. **`hal0 slot load --dry-run brain`** — slot loads, profile resolves,
   `n_gpu_layers=-1 threads=0 device=gpu-vulkan` populate correctly.
8. **`hal0 doctor models`** — no warnings on slot↔profile mismatch.
9. **`grep -rn 'tool_model' src/hal0/config/schema.py`** shows default
   `"hal0/agent"` at line ~2835 (the `BrainChatConfig.tool_model` field).
10. **`grep -rn 'gemma' src/hal0/config/data/family_defaults.toml`** returns no
    matches (entry cleared).

---

## 10. Out of scope (explicit)

- **`brain/readiness.py`** (spec-p3-brain §5c) — separate spec/PR.
- **P2-toolloop** (spec-p3-brain §8 risk #1) — separate PR.
- **spec-flags-ownership §7 supersession** — already done by spec-hw-slot-ownership.
- **Materialization timing in installer path** — risk #2 above; track but don't fix here.
- **STT profile (whisper)** — different binary family; defer to a later spec.
- **Vision profile / static seed** — explicitly excluded for 1.0 (decision §1.6).
- **`family_defaults.toml` schema removal** — schema layer stays (per
  decision §1.2); only the data is cleared.
- **`seed_stacks.toml`** — built-in stacks reference `profile = "moe"` /
  `profile = "chat"` (e.g. `stack.saber` uses `qwen3-6-35b-a3b-nsc-ace-saber-mtp-f16-to-rocmfp4-strix-lean` with `profile = "moe"` — a qwen3-architecture model, NOT chadrock). Generic profiles still exist after this spec, so no breakage. A separate pass could move qwen3-architecture stacks to a `profile.qwen3-moe` once such a profile exists (not in 1.0). Out of scope for this spec.

---

## 11. Companion specs to cite (not redo)

- [`spec-hw-slot-ownership.md`](../rework/hal0-specs/spec-hw-slot-ownership.md) — primary
  layered-shape spec (ratified 2026-07-19).
- [`spec-flags-ownership.md`](../rework/hal0-specs/spec-flags-ownership.md) §1–§6 — flags
  materialization + SLOT_HARDWARE_FLAGS denylist.
- [`spec-p3-brain.final.md`](../rework/hal0-specs/spec-p3-brain.final.md) §5 — Brain
  reliability changes (5a/5b/5c/5d).
- [`spec-17-installer.md`](../rework/hal0-specs/spec-17-installer.md) — installer overhaul,
  one profile authority (already externalized `SEED_PROFILES` per P3-schema).
- [`spec-p3-schema.final.md`](../rework/hal0-specs/spec-p3-schema.final.md) — schema
  externalization lane; `seed_profiles.toml` already at `src/hal0/config/data/`.
- Prior session handoff: `/tmp/handoff-hal0-seeded-profiles-2026-07-20.md` — context
  for the 1.0 profile pass (HF publishing artifacts, etc.).
