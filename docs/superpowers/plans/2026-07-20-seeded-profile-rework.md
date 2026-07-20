# hal0 1.0 Seeded Profile Rework — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the hal0 1.0 seeded slot profiles and profile catalog per the new Slot-vs-Model-vs-Profile shape: slot owns hardware, profile is a device-agnostic logical-tune template, model owns materialized flags. Lands a 16-profile catalog, 10 static seeds (3 new), 3 drift fixes, and the spec-p3-brain §5a tool_model default flip.

**Architecture:** Pure config/data + Pydantic schema change. No new Python modules; no new endpoints; no new dependencies. The work is:

1. Refactor `seed_profiles.toml` (11 existing → strip hardware/operational flags + `device_class`; add 5 new family/workload profiles).
2. Rewrite 8 existing slot TOMLs (populate `n_gpu_layers`/`threads`, update `profile` refs, fix drift).
3. Add 2 new static seed TOMLs (`coder.toml`, `embed.toml`).
4. Sync the 3-way static-seed registry (`static_seeds.py` tuple + `install.sh` loop + `setup_command._SETUP_SLOTS`).
5. Flip `schema.py:2835` `BrainChatConfig.tool_model` default per spec-p3-brain §5a.
6. Clear `family_defaults.toml` data (schema layer stays).
7. New tests covering the catalog, slot schema, and 3-way sync.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, TOML (tomllib stdlib), bash (for `install.sh` sync).

## Global Constraints

- **Target branch**: `rework/descar` (the active integration branch per the prior handoff). The spec was committed on `main` (commit `1064ae3d`); cherry-pick to `rework/descar` before Task 1.
- **Worktree isolation**: Use `superpowers:using-git-worktrees` to isolate this work from any parallel profile-edits. Verify no sibling worktrees are dirty before creating (per the handoff pattern).
- **Layered shape** (per `docs/rework/hal0-specs/spec-hw-slot-ownership.md` §1–§6, ratified 2026-07-19):
  - Slot owns: `device`, `n_gpu_layers` (NGL), `threads`, `binary`, `image_pin`, `[server].env`.
  - Profile owns: chat template, sampler, reasoning, KV type, batch sizes (`-b/-ub`), `-fa`, `--no-context-shift`, `--no-mmproj`, capability fields (`mtp`, `jinja`, `chat_template`, modality), `intent`. NO image, NO runner, NO device, NO `-ngl`, NO `--threads`.
  - Model owns: materialized flag text (copied from profile template at edit time per `spec-flags-ownership.md` §3).
  - Per-slot `[model].defaults.extra_args` = per-instance overrides (beats profile + model defaults).
- **`SLOT_HARDWARE_FLAGS` denylist** (per spec-hw-slot-ownership §5): `{-ngl/--n-gpu-layers, -dev/--device, --threads/-t}`. Profile TOML must not contain these. Tests enforce.
- **Profile flag denylist** (per this plan): profile flags must NOT contain `--parallel`, `--metrics`, `--no-webui`, `--poll`, `--poll-batch`, `--slot-prompt-similarity`, `--no-mmap`, `-tb`, `-ngl`, `-dev`, `--threads`, `--main-gpu`, `--tensor-split`, `--split-mode`, `-ngld`. Tests enforce.
- **Non-destructive seeding**: `seed_static_slots()` only copies when slot absent. New seeds follow the same pattern (operator-defined files untouched).
- **Port drift fixes are seed-only**: boxes with existing `rerank.toml` / `tts.toml` keep their old ports; fresh installs get the corrected ones.
- **TDD discipline**: every task follows the failing-test → implement → passing-test → commit cycle.
- **No placeholders, no TBD, no "similar to task N"**: every step shows actual code/commands.

---

## File Structure

**Modified**:

- `installer/etc-hal0/slots/brain.toml` — populate HW grid; flip `profile = "chat"` → `profile = "brain"`; update tool_model docstring per spec-p3-brain §5a.
- `installer/etc-hal0/slots/agent.toml` — populate HW grid; flip `profile = "chat"` → `profile = "chadrock-moe"`.
- `installer/etc-hal0/slots/utility.toml` — populate HW grid; `profile = "chat"` stays.
- `installer/etc-hal0/slots/flm.toml` — populate HW grid; `profile = "flm"` stays.
- `installer/etc-hal0/slots/img.toml` — populate HW grid; `profile = "comfyui"` stays.
- `installer/etc-hal0/slots/qwen3tts.toml` — populate HW grid; `profile = "qwen3-tts"` stays; becomes a real static seed (drift fix).
- `installer/etc-hal0/slots/tts.toml` — populate HW grid; port 8084 → 8085 (drift fix); `profile = "kokoro"` stays.
- `installer/etc-hal0/slots/rerank.toml` — populate HW grid; port 8083 → 8086 (drift fix); `profile = "reranking"` stays.
- `src/hal0/config/data/seed_profiles.toml` — refactor 11 existing (strip `device_class` + hardware/operational flags); add 5 new (`brain`, `chadrock-dense`, `chadrock-moe`, `thinking`, `coding`).
- `src/hal0/config/data/family_defaults.toml` — clear `[family].gemma = "..."` and any other entries; schema layer stays.
- `src/hal0/install/static_seeds.py` — extend tuple by 3 (`qwen3tts`, `coder`, `embed`).
- `installer/install.sh:1666` — extend `for seed_slot in ...` loop by 3.
- `src/hal0/config/schema.py:2835` — `BrainChatConfig.tool_model` default `""` → `"hal0/agent"`.

**Created**:

- `installer/etc-hal0/slots/coder.toml` — new static seed; profile=`coding`; port=8082.
- `installer/etc-hal0/slots/embed.toml` — new static seed; profile=`embedding`; port=8083.
- `tests/slots/test_seed_profiles.py` — profile catalog validation + denylist enforcement.
- `tests/slots/test_static_seeds.py` — extend existing to assert 10-slot tuple + drift assertions.
- `tests/slots/test_slot_schema.py` — every static seed TOML validates against `SlotConfig`; HW grid populated per §3.3 of the spec.

**Read-only references** (do not modify in this plan):

- `docs/rework/hal0-specs/spec-hw-slot-ownership.md` — layered shape source of truth.
- `docs/rework/hal0-specs/spec-flags-ownership.md` — flags materialization + denylist.
- `docs/rework/hal0-specs/spec-p3-brain.final.md` §5 — Brain reliability changes.
- `docs/superpowers/specs/2026-07-20-seeded-profile-rework-design.md` — design spec (commit `1064ae3d`).

---

## Task 1: Branch setup + family_defaults.toml data clear

**Files:**

- Modify: `src/hal0/config/data/family_defaults.toml`
- Create: `tests/slots/test_family_defaults_empty.py`

**Interfaces:**

- Consumes: existing `family_defaults.toml` (has `[family].gemma = "..."`)
- Produces: empty `[family]` table (or removed) — schema layer stays, data is gone

- [ ] **Step 1: Verify you're on `rework/descar` with a clean tree**

```bash
git checkout rework/descar
git status -s | wc -l   # expect: 0 (or only your scratch)
git log -1 --oneline    # confirm tip
```

If dirty (other than the spec cherry-pick), stop and resolve before continuing.

- [ ] **Step 2: Cherry-pick the spec onto `rework/descar`**

```bash
git cherry-pick 1064ae3d
git log -1 --oneline    # confirm spec landed
```

If cherry-pick conflicts, resolve (the spec file is new so should apply cleanly).

- [ ] **Step 3: Write the failing test for empty family_defaults.toml**

Create `tests/slots/test_family_defaults_empty.py`:

```python
"""Per spec §1.2 / §10: family_defaults.toml data is cleared for 1.0.

The schema layer (the [family] table) stays so a future spec can re-introduce
family-specific recipes as a layer; the data is gone because the 1.0 catalog
moves family-specific recipes into profile.<family>-<variant> entries.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

from hal0.config import paths


def test_family_defaults_has_no_data() -> None:
    """[family] table exists but contains no entries (cleared for 1.0)."""
    family_path = Path(__file__).resolve().parents[2] / "src/hal0/config/data/family_defaults.toml"
    raw = tomllib.loads(family_path.read_text())
    family_table = raw.get("family", {})
    assert family_table == {}, (
        f"family_defaults.toml [family] table must be empty for 1.0; got: {family_table}"
    )


def test_family_defaults_loads_via_paths_helper() -> None:
    """paths.family_defaults() returns an empty/cleared config (does not raise)."""
    cfg = paths.family_defaults()
    assert cfg is not None
    # Whatever the loader returns, it must not contain per-family overrides
    # (the schema layer may or may not expose the [family] table — what matters
    # is that no gemma/qwen3/etc. overrides leak into the loaded config).
    leaked = getattr(cfg, "family", None) or {}
    assert leaked == {}, f"family_defaults loaded with data: {leaked}"
```

- [ ] **Step 4: Run the test to verify it fails**

```bash
pytest tests/slots/test_family_defaults_empty.py -v
```

Expected: FAIL — `family_table` is `{"gemma": "-ctk f16 -ctv f16 --cache-reuse 0"}` (not empty).

- [ ] **Step 5: Clear family_defaults.toml data**

Edit `src/hal0/config/data/family_defaults.toml`. Replace the entire file contents with:

```toml
# Per-family llama-server flag overrides — schema layer retained for forward
# compatibility, data cleared for hal0 1.0 (per
# docs/superpowers/specs/2026-07-20-seeded-profile-rework-design.md §1.2).
#
# Family-specific recipes now ship as `profile.<family>-<variant>` entries in
# seed_profiles.toml (workload + family combined). A future spec may
# re-introduce family-level overrides via this layer if the model catalog
# grows to need per-family auto-application beyond a profile pick.
```

(Empty file with header comment. The `[family]` table is not present — the loader must tolerate its absence; verify by running the test in step 6.)

If the loader requires the `[family]` table to be present (even if empty), use this instead:

```toml
# (header as above)

[family]
```

- [ ] **Step 6: Run the test to verify it passes**

```bash
pytest tests/slots/test_family_defaults_empty.py -v
```

Expected: PASS (both tests). If `paths.family_defaults()` raises because `[family]` is missing, fall back to the `[family]` empty-table variant and re-run.

- [ ] **Step 7: Commit**

```bash
git add src/hal0/config/data/family_defaults.toml tests/slots/test_family_defaults_empty.py
git commit -m "feat(seeded-profile): clear family_defaults.toml data for 1.0

Per spec §1.2: family-specific recipes ship as profile.<family>-<variant>
entries in seed_profiles.toml. Schema layer stays so a future spec can
re-introduce per-family overrides if needed.

The gemma override (-ctk f16 -ctv f16 --cache-reuse 0) is lost; boxes
that relied on it will pick up generic moe/dense KV defaults. Document
in release notes.

Test: tests/slots/test_family_defaults_empty.py asserts the [family]
table loads empty (or absent) and that no per-family data leaks into
the loaded config."
```

---

## Task 2: Refactor existing 11 profiles in seed_profiles.toml (strip `device_class` + hardware/operational flags)

**Files:**

- Modify: `src/hal0/config/data/seed_profiles.toml`
- Create: `tests/slots/test_seed_profiles.py` (initial structure; extended in Task 3)

**Interfaces:**

- Consumes: existing `seed_profiles.toml` with `device_class` + hardware flags in `flags`
- Produces: 11 refactored profiles — `device_class` removed; `flags` contains only model-behavior flags (no `-ngl/-dev/--threads/-tb/--parallel/--metrics/--no-webui/--no-mmap/--poll*/--slot-prompt-similarity/-main-gpu/-tensor-split/-split-mode/-ngld`)

- [ ] **Step 1: Write the failing tests for the existing 11 profiles**

Create `tests/slots/test_seed_profiles.py`:

```python
"""Seed profile catalog validation.

Per docs/superpowers/specs/2026-07-20-seeded-profile-rework-design.md:
- Every profile must have: name (implicit via [profile.<name>]), flags, intent.
- `device_class` field is REMOVED (slot owns device).
- `flags` must NOT contain SLOT_HARDWARE_FLAGS or operational flags.
- Profile names match the 1.0 catalog (16 total — Task 3 adds 5).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib

SEED_PROFILES_PATH = Path(__file__).resolve().parents[2] / "src/hal0/config/data/seed_profiles.toml"

# Per spec §4.1: hardware + operational flags removed from every profile.
SLOT_HARDWARE_FLAG_FRAGMENTS = (
    "-ngl ", "--n-gpu-layers", "-ngl=", "-dev ", "--device ",
    "--threads ", "-t ", "--threads=", "-tb ",
    "--main-gpu", "--tensor-split", "--split-mode", "-ngld",
)
OPERATIONAL_FLAG_FRAGMENTS = (
    "--parallel", "--metrics", "--no-webui",
    "--poll", "--slot-prompt-similarity", "--no-mmap",
)


def _load_seed_profiles() -> dict:
    return tomllib.loads(SEED_PROFILES_PATH.read_text())


def test_seed_profiles_loads() -> None:
    """Catalog parses as TOML and contains [profile.*] tables."""
    raw = _load_seed_profiles()
    profiles = {k: v for k, v in raw.items() if k.startswith("profile.")}
    assert len(profiles) >= 11, f"expected ≥11 profiles, got {len(profiles)}: {list(profiles)}"


@pytest.mark.parametrize("profile_name", [
    "profile.chat", "profile.chat-long-context", "profile.dense", "profile.moe",
    "profile.embedding", "profile.reranking", "profile.cpu-chat",
    "profile.flm", "profile.kokoro", "profile.qwen3-tts", "profile.comfyui",
])
def test_existing_profile_has_no_device_class(profile_name: str) -> None:
    raw = _load_seed_profiles()
    assert profile_name in raw, f"missing {profile_name}"
    assert "device_class" not in raw[profile_name], (
        f"{profile_name} still has device_class (slot owns device per spec §1)"
    )


@pytest.mark.parametrize("profile_name", [
    "profile.chat", "profile.chat-long-context", "profile.dense", "profile.moe",
    "profile.embedding", "profile.reranking", "profile.cpu-chat",
    "profile.flm", "profile.kokoro", "profile.qwen3-tts", "profile.comfyui",
])
def test_existing_profile_has_no_hardware_flags(profile_name: str) -> None:
    raw = _load_seed_profiles()
    flags = raw[profile_name].get("flags", "")
    for fragment in SLOT_HARDWARE_FLAG_FRAGMENTS:
        assert fragment not in flags, (
            f"{profile_name} flags contain SLOT_HARDWARE flag {fragment!r}: {flags}"
        )


@pytest.mark.parametrize("profile_name", [
    "profile.chat", "profile.chat-long-context", "profile.dense", "profile.moe",
    "profile.embedding", "profile.reranking", "profile.cpu-chat",
    "profile.flm", "profile.kokoro", "profile.qwen3-tts", "profile.comfyui",
])
def test_existing_profile_has_no_operational_flags(profile_name: str) -> None:
    raw = _load_seed_profiles()
    flags = raw[profile_name].get("flags", "")
    for fragment in OPERATIONAL_FLAG_FRAGMENTS:
        assert fragment not in flags, (
            f"{profile_name} flags contain operational flag {fragment!r}: {flags}"
        )


@pytest.mark.parametrize("profile_name", [
    "profile.chat", "profile.chat-long-context", "profile.dense", "profile.moe",
    "profile.embedding", "profile.reranking", "profile.cpu-chat",
    "profile.flm", "profile.kokoro", "profile.qwen3-tts", "profile.comfyui",
])
def test_existing_profile_has_intent(profile_name: str) -> None:
    raw = _load_seed_profiles()
    assert "intent" in raw[profile_name], f"{profile_name} missing intent"
    assert isinstance(raw[profile_name]["intent"], str)
    assert raw[profile_name]["intent"].strip(), f"{profile_name} intent is blank"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/slots/test_seed_profiles.py -v
```

Expected: many FAIL — every profile has `device_class` and the existing `flags` strings contain `-tb`, `--parallel`, `--metrics`, `--no-webui`, `--no-mmap`, `--slot-prompt-similarity`.

- [ ] **Step 3: Refactor each of the 11 existing profiles**

Edit `src/hal0/config/data/seed_profiles.toml`. Replace the entire file contents with the 11 refactored profiles (existing header docstring rewritten; each profile's `flags` string cleaned; `device_class` removed):

```toml
# hal0 1.0 seeded profile catalog (16 profiles total).
#
# Per docs/superpowers/specs/2026-07-20-seeded-profile-rework-design.md §4:
# Profiles are device-agnostic logical-tune TEMPLATES — they carry only
# model-behavior flags (chat template, sampler, reasoning, KV type, batch
# sizes, capability toggles). Hardware/operational flags (NGL, threads,
# --parallel, --metrics, --no-webui, --no-mmap, --poll, --slot-prompt-
# similarity, --main-gpu, --tensor-split, --split-mode, --ngld) live on
# the slot. Images and runners live on the slot (via binary / image_pin).
#
# `mtp` remains informational for 1.0 (per spec-hw-slot-ownership.md §10)
# — launch reads MTP from model capability plus the selected slot runner.
#
# Each profile is a complete recipe for a (workload × model-family) cell.
# Slot picks ONE profile; per-slot [model].defaults.extra_args overrides.
# Family-specific quirks (kv-cache type, mmproj behavior, sampler) live in
# the family-specific profile (e.g. profile.chadrock-moe), NOT in a
# separate family_defaults layer.

[profile.chat]
# Minimal generic chat — fallback for unknown models.
flags = "-fa on --jinja -b 2048 -ub 512"
mtp = false
intent = "Generic chat (fallback for unknown models)"
quant = ""

[profile.chat-long-context]
# Long-context chat variant (model-family defaults may override KV policy).
flags = "-fa on -ctk q8_0 -ctv q8_0 -b 2048 -ub 512 -c 131072 --no-context-shift"
mtp = false
intent = "Long-context chat"
quant = ""

[profile.dense]
# Generic dense workload — model-agnostic. Family-specific kv-cache / sampler
# overrides go in profile.<family>-dense (e.g. profile.chadrock-dense).
flags = "--jinja -fa on -b 2048 -ub 512 -c 131072 --reasoning off --reasoning-format none --reasoning-budget -1 --no-context-shift --temp 0 --top-p 0.95 --top-k 20 --seed 123 --no-mmproj"
mtp = false
intent = "Generic dense workload"
quant = ""

[profile.moe]
# Generic MoE workload — model-agnostic. Family-specific overrides in
# profile.<family>-moe (e.g. profile.chadrock-moe).
flags = "--jinja -fa on -b 2048 -ub 512 -c 32768 --reasoning off --reasoning-format none --reasoning-budget -1 --no-context-shift --temp 0 --top-p 0.95 --top-k 20 --seed 123 --no-mmproj"
mtp = false
intent = "Generic MoE workload"
quant = ""

[profile.embedding]
flags = "--embedding -fa on -b 8192 -ub 8192"
mtp = false
intent = "Pooled embeddings"
quant = ""

[profile.reranking]
flags = "--reranking -fa on -b 8192 -ub 8192"
mtp = false
intent = "Reranking"
quant = ""

[profile.cpu-chat]
# CPU-safe chat. --threads-batch is llama-server specific (sets the number
# of threads to use for batch processing; distinct from --threads which
# sets total CPU threads). --threads stays on the slot.
flags = "--threads-batch 8 --jinja -b 256 -ub 256"
mtp = false
intent = "CPU-safe chat"
quant = ""

[profile.flm]
flags = ""
mtp = false
intent = "FLM NPU (chat/embed/STT)"
quant = "W4ABF16"

[profile.kokoro]
flags = "--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx"
mtp = false
intent = "Kokoro TTS"
quant = ""

[profile.qwen3-tts]
flags = "--model_path /mnt/ai-models/local/qwen3-tts/Qwen3-TTS-12Hz-1.7B-CustomVoice --default_voice Ryan --default_language Auto"
mtp = false
intent = "Qwen3-TTS"
quant = "BF16"

[profile.comfyui]
flags = "--disable-mmap --bf16-vae --cache-none"
mtp = false
intent = "ComfyUI"
quant = ""
```

**Flag-by-flag diff vs current** (for reviewer reference):

- `chat` — was `-fa on`; now `-fa on --jinja -b 2048 -ub 512` (added `--jinja` and batch defaults per §4.3).
- `chat-long-context` — dropped `--parallel 1 --metrics --no-webui --poll 100 --poll-batch 1` (slot/operational).
- `dense` — dropped `-tb 32`, `-ctk q8_0 -ctv q8_0` (now in default, family can override), `--parallel 1 --metrics --no-webui --slot-prompt-similarity 0.0` (slot/operational). Kept reasoning off + sampler defaults.
- `moe` — dropped `-tb 32`, `-ctk f16 -ctv f16` (now in chadrock-moe specific), `--parallel 1 --metrics --no-webui --slot-prompt-similarity 0.0` (slot/operational). Kept reasoning off + sampler defaults.
- `embedding` — dropped `--no-mmap` (slot/operational).
- `reranking` — dropped `--no-mmap` (slot/operational).
- `cpu-chat` — kept `--threads-batch 8` (llama-server specific, batch-processing thread count, NOT `--threads`).
- `flm`, `kokoro`, `qwen3-tts`, `comfyui` — unchanged (engine-specific or empty).

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/slots/test_seed_profiles.py -v
```

Expected: PASS (all 11 existing profiles pass the 4 denylist/intent assertions). If any fail, re-check the flag strings in `seed_profiles.toml` against the denylist.

- [ ] **Step 5: Commit**

```bash
git add src/hal0/config/data/seed_profiles.toml tests/slots/test_seed_profiles.py
git commit -m "feat(seeded-profile): refactor 11 existing profiles — strip device_class + hardware/operational flags

Per spec §4: profiles are device-agnostic logical-tune templates. Slot
owns NGL, threads, --parallel, --metrics, --no-webui, --no-mmap, --poll*,
--slot-prompt-similarity. Family-specific kv-cache / sampler go in the
new profile.<family>-<variant> entries (Task 3).

device_class field removed from every profile (slot owns device per
spec-hw-slot-ownership.md §2).

Flag-by-flag rationale documented in commit and spec §4.3.
Test: tests/slots/test_seed_profiles.py asserts every profile passes
SLOT_HARDWARE_FLAG_FRAGMENTS + OPERATIONAL_FLAG_FRAGMENTS denylist."
```

---

## Task 3: Add 5 new profiles to seed_profiles.toml

**Files:**

- Modify: `src/hal0/config/data/seed_profiles.toml` (append 5 new `[profile.*]` tables)
- Modify: `tests/slots/test_seed_profiles.py` (extend parametrize lists + add intent/flags checks)

**Interfaces:**

- Consumes: Task 2's refactored 11-profile catalog
- Produces: 16-profile catalog (11 + 5 new: `brain`, `chadrock-dense`, `chadrock-moe`, `thinking`, `coding`)

- [ ] **Step 1: Extend the test for the 5 new profiles**

Edit `tests/slots/test_seed_profiles.py`. Add 5 new entries to the parametrize lists in each test:

```python
# Replace the @pytest.mark.parametrize decorator in each test with:
@pytest.mark.parametrize("profile_name", [
    "profile.chat", "profile.chat-long-context", "profile.dense", "profile.moe",
    "profile.embedding", "profile.reranking", "profile.cpu-chat",
    "profile.flm", "profile.kokoro", "profile.qwen3-tts", "profile.comfyui",
    # New for 1.0 (per spec §4.2):
    "profile.brain", "profile.chadrock-dense", "profile.chadrock-moe",
    "profile.thinking", "profile.coding",
])
```

Also add a new test at the end of `test_seed_profiles.py`:

```python
def test_catalog_has_exactly_16_profiles() -> None:
    """1.0 catalog is exactly 16 profiles (11 kept + 5 new)."""
    raw = _load_seed_profiles()
    profiles = sorted(k for k in raw if k.startswith("profile."))
    assert profiles == [
        "profile.brain", "profile.chadrock-dense", "profile.chadrock-moe",
        "profile.chat", "profile.chat-long-context", "profile.coding",
        "profile.comfyui", "profile.cpu-chat", "profile.dense",
        "profile.embedding", "profile.flm", "profile.kokoro",
        "profile.moe", "profile.qwen3-tts", "profile.reranking",
        "profile.thinking",
    ], f"catalog profiles out of order or missing: {profiles}"
```

- [ ] **Step 2: Run the tests to verify the new entries fail**

```bash
pytest tests/slots/test_seed_profiles.py -v
```

Expected: 5 new parametrize entries FAIL (profile not in catalog), and `test_catalog_has_exactly_16_profiles` FAILs (only 11 exist).

- [ ] **Step 3: Append the 5 new profiles**

Edit `src/hal0/config/data/seed_profiles.toml`. Append after the `[profile.comfyui]` table:

```toml
[profile.brain]
# Brain steward workload + MiniCPM5-1B-Agentic-Tooluse quirks (per
# spec-p3-brain.final.md §5a). Brain is 1:1 with its model (the only
# 1B model the steward runs), so a single combined profile is cleaner
# than overlay. Small batch (1B model), reasoning off, no native
# toolcalls on FPX — tool turns route via [brain_chat] tool_model.
flags = "--jinja -fa on -b 512 -ub 256 -c 65536 --reasoning off --reasoning-format none --reasoning-budget -1 --no-context-shift --temp 0.7 --top-p 0.9 --top-k 20 --no-mmproj"
mtp = false
intent = "Brain steward (1B MiniCPM5 + persona + tool-call routing)"
quant = ""

[profile.chadrock-dense]
# Chadrock 27B dense coder recipe (from
# jcbtc/chadrock3.6-27b-pi-agent-rocmfp4-mtp model card — the chadrock
# launch card that the legacy profile.dense was distilled from). 27B
# dense + ROCmFP4 + MTP-capable + mmproj (vision).
flags = "--jinja -fa on -b 2048 -ub 512 -c 131072 --reasoning off --reasoning-format none --reasoning-budget -1 --no-context-shift -ctk q8_0 -ctv q8_0 --temp 0 --top-p 0.95 --top-k 20 --seed 123 --no-mmproj"
mtp = true
intent = "Chadrock 27B dense coder (ROCmFP4 + MTP-capable + mmproj)"
quant = "ROCmFP4"

[profile.chadrock-moe]
# Chadrock 35B MoE Saber recipe (from
# jcbtc/chadrock-35b-ace-saber-rocmfp4-mtp model card — the Saber launch
# card that the legacy profile.moe was distilled from). 35B MoE A3B +
# ROCmFPX MoEQuality + MTP draft heads.
flags = "--jinja -fa on -b 2048 -ub 512 -c 32768 --reasoning off --reasoning-format none --reasoning-budget -1 --no-context-shift -ctk f16 -ctv f16 --temp 0 --top-p 0.95 --top-k 20 --seed 123 --no-mmproj"
mtp = true
intent = "Chadrock 35B MoE Saber (ROCmFPX MoEQuality + MTP)"
quant = "ROCmFP4"

[profile.thinking]
# Workload-level reasoning-ON profile. Use for reasoning-capable models
# (qwen3-5-9b-deepseek, qwen3-6-35b-a3b-halostrix-dyn-mtp, chadrock-
# uncensored-thinking). Family-specific reasoning-format / MTP bits fold
# into the materialized model text at edit time.
flags = "--jinja -fa on -b 2048 -ub 512 -c 32768 --reasoning on --reasoning-format deepseek --reasoning-budget -1 --no-context-shift --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0 --presence-penalty 0 --no-mmproj"
mtp = false
intent = "Reasoning-ON workload (qwen3-deepseek, ace-saber thinking, etc.)"
quant = ""

[profile.coding]
# Workload-level code-gen tuned profile. Use for the coder slot (port
# 8082), qwen3-coder, qwopus-coder, Qwen3-Coder-30B-A3B. Higher temp
# for code-gen creativity; no reasoning (coders are non-reasoning).
flags = "--jinja -fa on -b 2048 -ub 512 -c 32768 --reasoning off --reasoning-format none --reasoning-budget -1 --no-context-shift --temp 0.7 --top-p 0.95 --top-k 40 --seed 123 --no-mmproj"
mtp = false
intent = "Code-gen workload (coder slot, qwen3-coder, qwopus-coder)"
quant = ""
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/slots/test_seed_profiles.py -v
```

Expected: PASS (all 16 profiles pass the denylist + intent checks; `test_catalog_has_exactly_16_profiles` confirms 16).

- [ ] **Step 5: Commit**

```bash
git add src/hal0/config/data/seed_profiles.toml tests/slots/test_seed_profiles.py
git commit -m "feat(seeded-profile): add 5 new profiles for 1.0 (brain, chadrock-dense, chadrock-moe, thinking, coding)

Per spec §4.2 — extends spec-hw-slot-ownership.md §10 from 11 to 16.

- profile.brain: Brain steward workload + MiniCPM5-1B quirks combined
  (Brain is 1:1 with its model, so no overlay needed).
- profile.chadrock-dense: 27B coder recipe from chadrock launch card
  (the flags previously distilled into the legacy profile.dense).
- profile.chadrock-moe: 35B Saber recipe from Saber launch card
  (the flags previously distilled into the legacy profile.moe).
- profile.thinking: reasoning-ON workload for qwen3-deepseek,
  ace-saber, chadrock-uncensored-thinking families.
- profile.coding: code-gen workload for the new coder slot
  (port 8082), qwen3-coder, qwopus-coder, Qwen3-Coder-30B-A3B.

Flag recipes verified against model card research (Task 2 commit
lists the chadrock flag diff; new profiles use card-distilled defaults).

Test: tests/slots/test_seed_profiles.py asserts the 16-profile catalog
+ denylist + intent for every profile."
```

---

## Task 4: Rewrite 8 existing slot TOMLs (HW grid + profile refs + port fixes)

**Files:**

- Modify: `installer/etc-hal0/slots/brain.toml`
- Modify: `installer/etc-hal0/slots/agent.toml`
- Modify: `installer/etc-hal0/slots/utility.toml`
- Modify: `installer/etc-hal0/slots/flm.toml`
- Modify: `installer/etc-hal0/slots/img.toml`
- Modify: `installer/etc-hal0/slots/qwen3tts.toml`
- Modify: `installer/etc-hal0/slots/tts.toml`
- Modify: `installer/etc-hal0/slots/rerank.toml`
- Create: `tests/slots/test_slot_schema.py`

**Interfaces:**

- Consumes: Task 3's 16-profile catalog; existing slot TOMLs
- Produces: 8 rewritten slot TOMLs — every slot populates `n_gpu_layers` and `threads` per §3.3 of the spec; `profile` refs match §5.1; `brain.toml` docstring fixes the `tool_model` recommendation per spec-p3-brain §5a; `rerank.toml` port 8083 → 8086; `tts.toml` port 8084 → 8085.

- [ ] **Step 1: Write the failing test for slot schema validation**

Create `tests/slots/test_slot_schema.py`:

```python
"""Every static seed slot TOML validates against SlotConfig and matches the
spec-p3-brain §5 + spec §5.1 + §5.4 mapping.

Per docs/superpowers/specs/2026-07-20-seeded-profile-rework-design.md §5.1 +
§5.4.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hal0.config.schema import SlotConfig

SLOTS_DIR = Path(__file__).resolve().parents[2] / "installer/etc-hal0/slots"

EXPECTED_MAPPING = {
    # slot name: (port, device, profile)
    "brain":     (8089, "gpu-vulkan",  "brain"),
    "agent":     (8081, "gpu-vulkan",  "chadrock-moe"),
    "utility":   (8090, "gpu-vulkan",  "chat"),
    "flm":       (8088, "npu",         "flm"),
    "img":       (8188, "gpu-rocm",    "comfyui"),
    "qwen3tts":  (8095, "gpu-rocm",    "qwen3-tts"),
    "tts":       (8085, "cpu",         "kokoro"),    # port drift fix (was 8084)
    "rerank":    (8086, "gpu-vulkan",  "reranking"), # port drift fix (was 8083)
    "coder":     (8082, "gpu-vulkan",  "coding"),    # NEW static seed
    "embed":     (8083, "gpu-vulkan",  "embedding"), # NEW static seed
}


@pytest.mark.parametrize("slot_name,expected", list(EXPECTED_MAPPING.items()))
def test_static_seed_slot_matches_mapping(slot_name: str, expected: tuple) -> None:
    port, device, profile = expected
    slot_path = SLOTS_DIR / f"{slot_name}.toml"
    assert slot_path.is_file(), f"missing slot TOML: {slot_path}"
    cfg = SlotConfig.model_validate_filepath(slot_path)  # type: ignore[attr-defined]
    assert cfg.name == slot_name
    assert cfg.port == port, f"{slot_name} port {cfg.port} != expected {port}"
    assert cfg.device == device, f"{slot_name} device {cfg.device} != expected {device}"
    assert cfg.profile == profile, f"{slot_name} profile {cfg.profile!r} != expected {profile!r}"


@pytest.mark.parametrize("slot_name,expected", list(EXPECTED_MAPPING.items()))
def test_static_seed_slot_populates_hw_grid(slot_name: str, expected: tuple) -> None:
    """Every static seed populates n_gpu_layers and threads (per spec §3.1 + §3.3)."""
    slot_path = SLOTS_DIR / f"{slot_name}.toml"
    cfg = SlotConfig.model_validate_filepath(slot_path)  # type: ignore[attr-defined]
    # n_gpu_layers: -1 (all) for gpu-*, 0 for cpu, n/a for npu
    if cfg.device.startswith("gpu-"):
        assert cfg.n_gpu_layers == -1, (
            f"{slot_name} device={cfg.device} but n_gpu_layers={cfg.n_gpu_layers} (expected -1)"
        )
    elif cfg.device == "cpu":
        assert cfg.n_gpu_layers == 0, (
            f"{slot_name} device=cpu but n_gpu_layers={cfg.n_gpu_layers} (expected 0)"
        )
    # npu: n_gpu_layers field is irrelevant (FLM doesn't take -ngl)
    # threads: 0 for gpu-*/npu (let runtime pick), 8 for cpu
    if cfg.device == "cpu":
        assert cfg.threads == 8, (
            f"{slot_name} device=cpu but threads={cfg.threads} (expected 8)"
        )
    else:
        assert cfg.threads == 0, (
            f"{slot_name} device={cfg.device} but threads={cfg.threads} (expected 0)"
        )


def test_brain_slot_docstring_recommends_hal0_agent() -> None:
    """brain.toml docstring recommends tool_model = hal0/agent per spec-p3-brain §5a
    (legacy comment said hal0/code — that was the older recommendation)."""
    brain_path = SLOTS_DIR / "brain.toml"
    content = brain_path.read_text()
    # The recommendation comment in brain.toml should mention hal0/agent
    # (and should NOT still recommend hal0/code as the primary recommendation).
    assert "hal0/agent" in content, (
        "brain.toml docstring missing 'hal0/agent' recommendation per spec-p3-brain §5a"
    )
```

If `SlotConfig.model_validate_filepath` doesn't exist on your Pydantic version, use `SlotConfig.model_validate(tomllib.loads(slot_path.read_text()))` instead. Verify the helper exists:

```bash
python -c "from hal0.config.schema import SlotConfig; print(hasattr(SlotConfig, 'model_validate_filepath'))"
```

If False, adjust the test imports accordingly.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/slots/test_slot_schema.py -v
```

Expected: many FAIL — current slot TOMLs don't populate `n_gpu_layers`/`threads`, `agent.toml` has `profile = "chat"`, `rerank.toml` port is 8083, `tts.toml` port is 8084, brain.toml still recommends `hal0/code`. Also `coder.toml` and `embed.toml` don't exist yet (Task 5) so 2 parametrize entries FAIL with "missing slot TOML".

- [ ] **Step 3: Rewrite brain.toml**

Replace `installer/etc-hal0/slots/brain.toml` contents with:

```toml
# Brain LLM slot — vulkan llama-server container (hal0-slot@brain).
#
# The dashboard's sidebar agent chat (the hal0-brain platform steward)
# targets the virtual model `hal0/brain`, which resolves here once a model
# is bound and falls back to the `agent` slot meanwhile. Seeding the slot
# makes that intent visible as a grey tile instead of the chat's model
# routing failing opaquely on fresh boxes. `hal0 setup` scaffolds the same
# slot when this seed is absent (setup_command._BRAIN_SLOT — chat
# capability, same name + port), and never overwrites an existing file.
#
# UNLIKE a normal user slot (which ships model-less so the operator picks),
# the brain is the platform steward — it must work out of the box — so it
# ships WITH a model pick: MiniCPM5-1B-Agentic-Tooluse, the strongest tool-use
# 1B in the default library. Port 8089: 8088 belongs to the flm seed.
#
# Tool-calling note: on the FPX brain runtime a 1B can't emit tool calls the
# native parser accepts (it leaks/500s), so the steward chat routes TOOL turns
# to a capable model via `[brain_chat] tool_model` — the 1B here serves the
# steward's plain chat. Default `tool_model = "hal0/agent"` per
# spec-p3-brain.final.md §5a + ADR-0023 (hal0/agent is the always-on
# anchor every fallback chain ends in). The chadrock family are confirmed
# clean native tool-callers on this runtime: `hal0/code` (27B coder, lighter)
# and `hal0/agent` (35B anchor). The brain's `tool_model` config field is the
# override knob (default hal0/agent; can be flipped to hal0/code).
name = "brain"
type = "llm"
device = "gpu-vulkan"
runtime = "container"
profile = "brain"
enabled = true
port = 8089
n_gpu_layers = -1
threads = 0

[model]
default = "MiniCPM5-1B-Agentic-Tooluse"
context_size = 65536
```

- [ ] **Step 4: Rewrite agent.toml**

Replace `installer/etc-hal0/slots/agent.toml` contents with:

```toml
# Agent LLM slot — vulkan llama-server container (hal0-slot@agent).
#
# ADR-0023: `agent` is the default LLM anchor every `hal0/<slot>` fallback
# chain ends in (chat routing, the sidebar steward's hal0/brain fallback,
# Hermes delegation). Seeding it at install time keeps those chains from
# dead-ending on a fresh box; `hal0 setup` scaffolds the same slot when this
# seed is absent (setup_command._SETUP_SLOTS — chat capability, same name +
# port), and never overwrites an existing file.
#
# Per spec-p3-brain.final.md §5b/5c: ships WITHOUT a `[model].default` and
# DISABLED, so a fresh, deliberately model-less box boots a GREY tile — no
# surprise multi-GB download and no crash-loop. The operator assigns a model
# later (dashboard / `hal0 setup`), which flips the slot enabled. The
# readiness gate (brain/readiness.py) warms this slot when brain_chat.enabled
# + tool_model resolves here. Per §5b we choose reconcile-on-provision
# (rather than seed-enabled-with-default-model) because model-less +
# surprise-download-free is the cleanest WS-E #1107 pattern.
#
# profile = chadrock-moe: agent slot is the chadrock 35B Saber anchor per
# ADR-0023 + brain.toml docstring. The profile supplies the 35B-A3B
# MoE/ROCmFPX/MTP recipe (sourced from the Saber launch card). Per-slot
# [model].defaults.extra_args can override if the operator binds a different
# model family.
name = "agent"
type = "llm"
device = "gpu-vulkan"
runtime = "container"
profile = "chadrock-moe"
enabled = false
port = 8081
n_gpu_layers = -1
threads = 0

[model]
context_size = 65536
```

- [ ] **Step 5: Rewrite utility.toml**

Replace `installer/etc-hal0/slots/utility.toml` contents with:

```toml
# Utility LLM slot — vulkan llama-server container (hal0-slot@utility).
#
# Clean seed (WS-E, #1107): ships WITHOUT a `[model].default` and DISABLED, so a
# fresh, deliberately model-less box boots a GREY tile — no surprise multi-GB
# download and no crash-loop. The previous pin was the GHOST id `gemma-4-12b-it`:
# a catalog/marketing id with NO registry file and NO curated coords (the real
# GGUF is `gemma-4-12b-it-ud-q4-k-xl` → `unsloth/gemma-4-12b-it-GGUF`). Pinned to
# the ghost on a box with nothing to fall back to, the slot crash-looped.
#
# The operator assigns a model later (dashboard / `hal0 setup`), which flips the
# slot enabled. profile = chat (generic workload template) lets any chat-capable
# model bind here without needing a family-specific profile pick.
name = "utility"
type = "llm"
device = "gpu-vulkan"
runtime = "container"
profile = "chat"
enabled = false
# Port 8090: 8081 is the `agent` seed's canonical primary port (ADR-0023);
# existing installs keep whatever their file says (seeds never overwrite).
port = 8090
n_gpu_layers = -1
threads = 0

[model]
context_size = 65536
```

- [ ] **Step 6: Rewrite flm.toml**

Replace `installer/etc-hal0/slots/flm.toml` contents with:

```toml
# FLM LLM slot — FastFlowLM in a podman container (hal0-slot@flm).
# The FLM trio serves chat + STT + embed on one AMDXDNA column.
#
# Clean seed (WS-E, #1107): no `[model].default` pin and DISABLED, so a model-less
# box boots a grey tile (no surprise FLM download, no crash-loop). The operator
# assigns an FLM model later, which flips the slot enabled.
#
# Chat is on by default (NpuConfig.chat defaults true), so this is a
# chat-ready NPU slot out of the box. To flip the rest of the trio on,
# drop a `[npu]` table next to [model] with `asr = true` and/or
# `embed = true` — or use the dashboard's NPU drawer, which drives the
# same toggles on the running `flm serve`:
#
#   [npu]
#   asr = true
#   embed = true
name = "flm"
port = 8088
device = "npu"
runtime = "container"
profile = "flm"
type = "llm"
enabled = false

[model]
context_size = 16384
```

(FLM is NPU; `n_gpu_layers` and `threads` are N/A — omit them per spec §3.3 npu row.)

- [ ] **Step 7: Rewrite img.toml**

Replace `installer/etc-hal0/slots/img.toml` contents with:

```toml
# hal0 image-generation slot — ComfyUI in a podman container (hal0-slot@img).
# Resident container: once loaded it stays up across modes so the web UI is
# always reachable for workflow building. The GpuArbiter only swaps what
# holds GPU memory — LLM slots are unloaded while generation is active, and
# ComfyUI's models are freed (POST /free) when LLM slots come back after
# [image].idle_restore_minutes with no jobs (spec §7).
#
# Clean seed (WS-E, #1107): no `[model].default` pin and DISABLED, so a model-less
# box boots a grey tile — no surprise multi-GB checkpoint download (the previous
# `sdxl-turbo` pin) and no crash-loop. ComfyUI capability picks land via the
# guided setup's `comfyui_defaults` sidecar → POST /api/comfyui/models/fetch; the
# operator enables this slot once a checkpoint is present.

name = "img"
type = "image"
provider = "comfyui"
device = "gpu-rocm"
runtime = "container"
profile = "comfyui"
enabled = false
port = 8188
n_gpu_layers = -1
threads = 0

[image]                       # persisted image-gen settings (#599)
idle_restore_minutes = 60
default_size = "1024x1024"
default_steps = 0
```

- [ ] **Step 8: Rewrite qwen3tts.toml**

Replace `installer/etc-hal0/slots/qwen3tts.toml` contents with:

```toml
# Qwen3-TTS slot — multilingual GPU TTS in a podman container
# (hal0-slot@qwen3tts). The GPU sibling of the cpu `tts`/kokoro slot; both
# implement the OpenAI /v1/audio/speech contract.
#
# Per spec §5.4: this file was previously an opt-in template (NOT in the
# installer's STATIC_SEED_SLOTS loop). For 1.0 it becomes a real static
# seed — adds to STATIC_SEED_SLOTS tuple + install.sh:1666 loop + the
# spec §5.1 mapping table. Like other seeds it ships DISABLED with no
# `[model].default` pin, so copying it in never triggers a surprise
# download or a crash-loop.
name = "qwen3tts"
type = "tts"
device = "gpu-rocm"
runtime = "container"
profile = "qwen3-tts"
enabled = false
port = 8095
n_gpu_layers = -1
threads = 0
```

- [ ] **Step 9: Rewrite tts.toml (port 8084 → 8085)**

Replace `installer/etc-hal0/slots/tts.toml` contents with:

```toml
# TTS slot — kokoro-onnx in a podman container (hal0-slot@tts), CPU-only.
#
# Clean seed (WS-E, #1107): no `[model].default` pin and DISABLED, so a
# model-less box boots a GREY tile (no surprise download, no crash-loop).
# The operator picks a voice model later, which flips the slot enabled.
#
# Per spec §5.4: port drift fix. Was 8084 (which conflicted with
# _SETUP_SLOTS["stt"] = 8084); now 8085 per _SETUP_SLOTS["tts"]. Existing
# installs keep their old port (seeds never overwrite).
name = "tts"
type = "tts"
device = "cpu"
runtime = "container"
profile = "kokoro"
enabled = false
port = 8085
n_gpu_layers = 0
threads = 8
```

- [ ] **Step 10: Rewrite rerank.toml (port 8083 → 8086)**

Replace `installer/etc-hal0/slots/rerank.toml` contents with:

```toml
# Rerank slot — llama-server --reranking in a podman container (hal0-slot@rerank).
#
# Clean seed (WS-E, #1107): no `[model].default` pin and DISABLED, so a
# model-less box boots a GREY tile (no surprise download, no crash-loop).
# The operator picks a reranker later; that flips the slot enabled.
# device/profile are the widest-compatible GPU-Vulkan tile defaults — the
# guided / answer-file setup path derives them from the up-front preflight
# (hardware.json, #1097).
#
# Per spec §5.4: port drift fix. Was 8083 (which conflicted with
# _SETUP_SLOTS["embed"] = 8083); now 8086 per _SETUP_SLOTS["rerank"].
# Existing installs keep their old port (seeds never overwrite).
name = "rerank"
type = "reranking"
device = "gpu-vulkan"
runtime = "container"
profile = "reranking"
enabled = false
port = 8086
n_gpu_layers = -1
threads = 0

[model]
context_size = 4096
```

- [ ] **Step 11: Run the tests (excluding the 2 new seeds) to verify they pass**

```bash
pytest tests/slots/test_slot_schema.py -v -k "not (coder or embed)"
```

Expected: PASS for all 8 existing slots (brain, agent, utility, flm, img, qwen3tts, tts, rerank). The 2 new slots (coder, embed) FAIL with "missing slot TOML" — that's expected; Task 5 creates them.

- [ ] **Step 12: Commit**

```bash
git add installer/etc-hal0/slots/ tests/slots/test_slot_schema.py
git commit -m "feat(seeded-profile): rewrite 8 existing slot TOMLs — HW grid, profile refs, port fixes

Per spec §5.1 + §5.4:

- brain.toml: profile = chat → brain; tool_model docstring updated to
  recommend hal0/agent (was hal0/code) per spec-p3-brain §5a + ADR-0023.
- agent.toml: profile = chat → chadrock-moe (ADR-0023 anchor; 35B Saber).
  Stays model-less + disabled per spec-p3-brain §5b/c.
- utility.toml: profile = chat (generic fallback); HW grid populated.
- flm.toml: no profile change; HW grid omitted (NPU — n/a per §3.3).
- img.toml: no profile change; HW grid populated.
- qwen3tts.toml: no profile change; HW grid populated (drift fix per §5.4
  adds to STATIC_SEED_SLOTS in Task 6).
- tts.toml: port 8084 → 8085 (drift fix; was conflicting with _SETUP_SLOTS[stt]).
- rerank.toml: port 8083 → 8086 (drift fix; was conflicting with _SETUP_SLOTS[embed]).

Every gpu-* slot: n_gpu_layers=-1, threads=0.
tts.toml (cpu): n_gpu_layers=0, threads=8.

Test: tests/slots/test_slot_schema.py asserts the §5.1 mapping + HW
grid per device + brain.toml docstring recommendation."
```

---

## Task 5: Add 2 new static seed TOMLs (coder, embed)

**Files:**

- Create: `installer/etc-hal0/slots/coder.toml`
- Create: `installer/etc-hal0/slots/embed.toml`

**Interfaces:**

- Consumes: Task 3's `profile.coding` and `profile.embedding`; Task 4's test scaffold
- Produces: 2 new static seed TOMLs that satisfy the §5.1 mapping

- [ ] **Step 1: Create coder.toml**

Write `installer/etc-hal0/slots/coder.toml`:

```toml
# Coder slot — code-gen workload in a podman container (hal0-slot@coder).
# Port 8082 per setup_command._SETUP_SLOTS["coder"]. Sibling to the
# agent slot (8081); used by the codegen virtual model hal0/code
# (per brain.toml docstring: "hal0/code (27B coder, lighter)").
#
# profile = coding (new in 1.0; per spec §4.2): workload-level code-gen
# tune — higher temp for code-gen creativity, no reasoning (coders are
# non-reasoning), specific sampler (--temp 0.7 --top-p 0.95 --top-k 40).
# Family-specific overrides go in profile.<family>-coding for specific
# coder model families (e.g. profile.qwopus-coding) — out of scope for 1.0.
#
# Clean seed (WS-E, #1107): no `[model].default` pin and DISABLED, so a
# model-less box boots a GREY tile. The operator assigns a coder model
# later, which flips the slot enabled.
name = "coder"
type = "llm"
device = "gpu-vulkan"
runtime = "container"
profile = "coding"
enabled = false
port = 8082
n_gpu_layers = -1
threads = 0

[model]
context_size = 32768
```

- [ ] **Step 2: Create embed.toml**

Write `installer/etc-hal0/slots/embed.toml`:

```toml
# Embed slot — pooled embeddings in a podman container (hal0-slot@embed).
# Port 8083 per setup_command._SETUP_SLOTS["embed"]. Rerank slot uses
# 8086 (per the rerank.toml port-drift fix in §5.4).
#
# profile = embedding (existing in catalog): pooled embeddings tune
# (--embedding -fa on -b 8192 -ub 8192). The slot can bind any
# embedding-capable model (nomic-embed, qwen3-embedding-0-6b-q8-0,
# bge-base-en-v1.5-q4_k_m, etc.).
#
# Clean seed (WS-E, #1107): no `[model].default` pin and DISABLED, so a
# model-less box boots a GREY tile. The operator assigns an embed model
# later, which flips the slot enabled.
name = "embed"
type = "embedding"
device = "gpu-vulkan"
runtime = "container"
profile = "embedding"
enabled = false
port = 8083
n_gpu_layers = -1
threads = 0

[model]
context_size = 4096
```

- [ ] **Step 3: Run the slot schema tests — all 10 should pass**

```bash
pytest tests/slots/test_slot_schema.py -v
```

Expected: PASS for all 10 slots.

- [ ] **Step 4: Commit**

```bash
git add installer/etc-hal0/slots/coder.toml installer/etc-hal0/slots/embed.toml
git commit -m "feat(seeded-profile): add 2 new static seeds (coder, embed)

Per spec §5.1 + §1.7:

- coder.toml: port 8082, profile = coding (new in 1.0), clean seed
  (model-less + disabled per WS-E #1107).
- embed.toml: port 8083, profile = embedding (existing in catalog),
  clean seed.

Both follow the same shape as agent/utility/etc.: model-less + disabled
at seed, HW grid populated (gpu-vulkan: n_gpu_layers=-1, threads=0).

Task 6 syncs these into STATIC_SEED_SLOTS tuple + install.sh loop."
```

---

## Task 6: Sync 3-way static-seed registry (static_seeds.py + install.sh +_SETUP_SLOTS)

**Files:**

- Modify: `src/hal0/install/static_seeds.py` (extend `STATIC_SEED_SLOTS` tuple)
- Modify: `installer/install.sh:1666` (extend bash loop)
- Modify: `src/hal0/cli/setup_command.py` (verify `_SETUP_SLOTS` mirror, no change expected)
- Create: `tests/slots/test_static_seeds.py` (extend or create)

**Interfaces:**

- Consumes: Task 4's 8 rewrites + Task 5's 2 new seeds; existing `STATIC_SEED_SLOTS` tuple; existing install.sh loop
- Produces: 3-way synced registry — all 10 slots appear in all three sources

- [ ] **Step 1: Check existing static_seeds test (if any)**

```bash
ls tests/slots/ 2>&1
```

If `test_static_seeds.py` exists, skip to Step 3. Otherwise proceed to Step 2.

- [ ] **Step 2: Write the failing test for 3-way sync**

Create `tests/slots/test_static_seeds.py`:

```python
"""3-way sync test for STATIC_SEED_SLOTS.

Per docs/superpowers/specs/2026-07-20-seeded-profile-rework-design.md §5.5:
static_seeds.py tuple + install.sh loop + setup_command._SETUP_SLOTS must
agree on the 10 static seed slot names.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from hal0.install.static_seeds import STATIC_SEED_SLOTS

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "installer/install.sh"
SETUP_COMMAND = REPO_ROOT / "src/hal0/cli/setup_command.py"
SLOTS_DIR = REPO_ROOT / "installer/etc-hal0/slots"

EXPECTED_10 = frozenset({
    "flm", "tts", "rerank", "utility", "img", "agent", "brain",
    "qwen3tts", "coder", "embed",
})


def test_static_seed_slots_tuple_is_10() -> None:
    """STATIC_SEED_SLOTS tuple is exactly the 10 expected seed names."""
    assert frozenset(STATIC_SEED_SLOTS) == EXPECTED_10, (
        f"STATIC_SEED_SLOTS = {sorted(STATIC_SEED_SLOTS)}, "
        f"expected {sorted(EXPECTED_10)}"
    )


def test_install_sh_loop_matches_tuple() -> None:
    """installer/install.sh:1666 for-loop iterates over the same 10 names."""
    content = INSTALL_SH.read_text()
    match = re.search(r"for seed_slot in ([a-z0-9 _]+); do", content)
    assert match is not None, "could not find 'for seed_slot in ...' line in install.sh"
    bash_names = set(match.group(1).split())
    assert bash_names == EXPECTED_10, (
        f"install.sh loop = {sorted(bash_names)}, expected {sorted(EXPECTED_10)}"
    )


@pytest.mark.parametrize("seed_name", sorted(EXPECTED_10))
def test_every_static_seed_has_slot_toml(seed_name: str) -> None:
    """Every entry in STATIC_SEED_SLOTS has a corresponding <name>.toml file."""
    assert (SLOTS_DIR / f"{seed_name}.toml").is_file(), (
        f"missing slot TOML for static seed '{seed_name}'"
    )


def test_no_drift_files_in_slots_dir() -> None:
    """No extra *.toml files in slots/ that aren't in STATIC_SEED_SLOTS.

    Catches the qwen3tts.toml drift pattern: file on disk but missing from
    the registry means it never gets copied to /etc/hal0/slots/.
    """
    on_disk = {p.stem for p in SLOTS_DIR.glob("*.toml")}
    extra = on_disk - EXPECTED_10
    assert not extra, f"drift: slot TOML files not in STATIC_SEED_SLOTS: {sorted(extra)}"


def test_no_extra_static_seeds() -> None:
    """No STATIC_SEED_SLOTS entries that don't have a slot TOML."""
    extra = set(STATIC_SEED_SLOTS) - {p.stem for p in SLOTS_DIR.glob("*.toml")}
    assert not extra, f"STATIC_SEED_SLOTS entries without slot TOML: {sorted(extra)}"
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
pytest tests/slots/test_static_seeds.py -v
```

Expected: FAIL — current `STATIC_SEED_SLOTS` has 7 names (missing qwen3tts, coder, embed); install.sh loop has 7 names; current slots dir has `qwen3tts.toml` not in tuple.

- [ ] **Step 4: Extend `STATIC_SEED_SLOTS` tuple**

Edit `src/hal0/install/static_seeds.py`. Replace the `STATIC_SEED_SLOTS` tuple (around line 34-42):

```python
#: Slot names with a static seed TOML in installer/etc-hal0/slots/.
#: MUST mirror install.sh's:
#:   for seed_slot in flm tts rerank utility img agent brain qwen3tts coder embed; do
STATIC_SEED_SLOTS: tuple[str, ...] = (
    "flm",
    "tts",
    "rerank",
    "utility",
    "img",
    "agent",
    "brain",
    "qwen3tts",
    "coder",
    "embed",
)
```

- [ ] **Step 5: Extend install.sh bash loop**

Edit `installer/install.sh` line 1666. Replace:

```bash
for seed_slot in flm tts rerank utility img agent brain; do
```

with:

```bash
for seed_slot in flm tts rerank utility img agent brain qwen3tts coder embed; do
```

- [ ] **Step 6: Verify _SETUP_SLOTS mirror in setup_command.py is consistent (no change expected)**

```bash
grep -n '"brain"\|"agent"\|"coder"\|"embed"\|"qwen3tts"' src/hal0/cli/setup_command.py
```

Expected: `_SETUP_SLOTS` already contains entries for chat/coder/embed/stt/tts/rerank/vision (mapped to (slot_name, port)). The slots that ARE static seeds (agent, brain) appear via separate references (`_BRAIN_SLOT = ("brain", 8089)` and `chat → ("agent", 8081)`). `qwen3tts` is not in `_SETUP_SLOTS` because it's opt-in (not in `_SCAFFOLD_CAPS` either); that's fine — `_SETUP_SLOTS` is the dynamic-scaffold mirror, not the static-seed mirror.

No edit needed in setup_command.py. (If you find a mismatch, flag and resolve.)

- [ ] **Step 7: Run the tests to verify they pass**

```bash
pytest tests/slots/test_static_seeds.py -v
```

Expected: PASS (all 3-way sync assertions + drift detection).

- [ ] **Step 8: Commit**

```bash
git add src/hal0/install/static_seeds.py installer/install.sh tests/slots/test_static_seeds.py
git commit -m "feat(seeded-profile): sync 3-way static-seed registry to 10 slots

Per spec §5.5:

- src/hal0/install/static_seeds.py:34-42 STATIC_SEED_SLOTS tuple
  extends from 7 to 10 (adds qwen3tts, coder, embed).
- installer/install.sh:1666 for-loop extends from 7 to 10 names.
- src/hal0/cli/setup_command.py:_SETUP_SLOTS mirror already contains
  the relevant entries (coder/embed map to the static seeds; qwen3tts
  is opt-in only — not in _SCAFFOLD_CAPS, which is correct).

Test: tests/slots/test_static_seeds.py enforces 3-way sync (Python
tuple == bash loop == on-disk slot TOMLs == 10 expected names) and
catches drift files (slot TOMLs not in the registry, like the
pre-fix qwen3tts.toml situation)."
```

---

## Task 7: Flip schema.py:2835 BrainChatConfig.tool_model default + update brain.toml docstring (already done in Task 4)

**Files:**

- Modify: `src/hal0/config/schema.py:2835`
- Modify: `installer/etc-hal0/slots/brain.toml` (docstring only — already done in Task 4)

**Interfaces:**

- Consumes: `BrainChatConfig.tool_model: str = ""` (current default)
- Produces: `BrainChatConfig.tool_model: str = "hal0/agent"` (per spec-p3-brain §5a)

- [ ] **Step 1: Write the failing test for the tool_model default**

Create `tests/config/test_brain_tool_model_default.py` (or add to an existing test file):

```python
"""Per spec-p3-brain.final.md §5a + spec §5.3:
BrainChatConfig.tool_model default is 'hal0/agent' (was '').
"""

from __future__ import annotations

from hal0.config.schema import BrainChatConfig


def test_brain_chat_tool_model_default_is_hal0_agent() -> None:
    """Default tool_model is 'hal0/agent' per spec-p3-brain §5a."""
    cfg = BrainChatConfig()
    assert cfg.tool_model == "hal0/agent", (
        f"BrainChatConfig.tool_model default = {cfg.tool_model!r}, expected 'hal0/agent'"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/config/test_brain_tool_model_default.py -v
```

Expected: FAIL — current default is `""`.

- [ ] **Step 3: Flip the default**

Edit `src/hal0/config/schema.py` at line 2835. Change:

```python
    tool_model: str = ""
```

to:

```python
    tool_model: str = "hal0/agent"
```

Update the field's `description=` docstring to reflect the new default and cite the spec.

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/config/test_brain_tool_model_default.py -v
```

Expected: PASS.

- [ ] **Step 5: Verify brain.toml docstring already references hal0/agent (from Task 4)**

```bash
grep -n "hal0/agent" installer/etc-hal0/slots/brain.toml
```

Expected: at least one match (Task 4 Step 3 already added the docstring update). If missing, add it: replace "Recommended `tool_model = "hal0/code"`" with "Default `tool_model = "hal0/agent"` per spec-p3-brain.final.md §5a (can be flipped to `hal0/code` for the lighter coder anchor)."

- [ ] **Step 6: Commit**

```bash
git add src/hal0/config/schema.py installer/etc-hal0/slots/brain.toml tests/config/test_brain_tool_model_default.py
git commit -m "feat(brain): flip BrainChatConfig.tool_model default to hal0/agent per spec-p3-brain §5a

- schema.py:2835: BrainChatConfig.tool_model default '' → 'hal0/agent'.
  Per spec-p3-brain.final.md §5a + ADR-0023: hal0/agent is the always-on
  anchor every fallback chain ends in. Brain's 1B steward can't emit
  native tool calls on FPX runtime, so tool turns route via tool_model.
- brain.toml docstring: updated to reflect hal0/agent default (Task 4
  Step 3 already updated this; verified here).

Breaking change for boxes that relied on tool_model = '': those boxes
will start routing TOOL turns to hal0/agent automatically. Document in
release notes + dashboard toast on first load post-upgrade.

Test: tests/config/test_brain_tool_model_default.py asserts the default."
```

---

## Task 8: Final verification pass (full spec §9 checks)

**Files:**

- Modify (test only): no source changes; this task runs the full §9 verification surface

**Interfaces:**

- Consumes: Tasks 1-7 outputs
- Produces: green test suite + clean grep-based checks per spec §9

- [ ] **Step 1: Run the full slot/installer test suite**

```bash
pytest tests/slots/ tests/installer/ tests/capabilities/ tests/config/ -v
```

Expected: PASS (all slot + installer + capabilities + config tests green).

- [ ] **Step 2: Run the 10 §9 grep/dry-run checks from the spec**

Run each check and verify the expected output:

```bash
# Check 2: no device_class in seed_profiles.toml
grep -n 'device_class' src/hal0/config/data/seed_profiles.toml
# Expected: no output

# Check 3: no SLOT_HARDWARE_FLAG_FRAGMENTS or OPERATIONAL_FLAG_FRAGMENTS in seed_profiles.toml
grep -nE '\-ngl|\-\-threads|\-tb|\-dev|\-\-parallel|\-\-metrics|\-\-no\-webui|\-\-no\-mmap|\-\-slot\-prompt\-similarity|\-\-poll' src/hal0/config/data/seed_profiles.toml
# Expected: no output

# Check 4: every seeded slot has its target profile
grep -n 'profile = ' installer/etc-hal0/slots/*.toml
# Expected: 10 lines matching the §5.1 mapping (brain → brain, agent → chadrock-moe, etc.)

# Check 5: STATIC_SEED_SLOTS == the 10 expected names
python -c "from hal0.install.static_seeds import STATIC_SEED_SLOTS; assert len(STATIC_SEED_SLOTS) == 10 and set(STATIC_SEED_SLOTS) == {'flm','tts','rerank','utility','img','agent','brain','qwen3tts','coder','embed'}; print('OK')"
# Expected: OK

# Check 6: install.sh loop matches
grep -n 'for seed_slot' installer/install.sh
# Expected: 'for seed_slot in flm tts rerank utility img agent brain qwen3tts coder embed; do'

# Check 9: tool_model default = hal0/agent
grep -n 'tool_model' src/hal0/config/schema.py | grep -E 'hal0/agent|"$|""'
# Expected: tool_model: str = "hal0/agent"

# Check 10: family_defaults.toml has no gemma entry
grep -rn 'gemma' src/hal0/config/data/family_defaults.toml
# Expected: no output
```

- [ ] **Step 3: Run `hal0 slot load --dry-run brain` (if the CLI is invokable)**

```bash
hal0 slot load --dry-run brain 2>&1
```

Expected: slot loads, profile resolves, `n_gpu_layers=-1 threads=0 device=gpu-vulkan` populate correctly. If the CLI doesn't exist in your env, skip this check and rely on the SlotConfig Pydantic test (Task 4).

- [ ] **Step 4: Run `hal0 doctor models` (if available)**

```bash
hal0 doctor models 2>&1
```

Expected: no warnings on slot↔profile mismatch. If not available, skip.

- [ ] **Step 5: Verify the implementation plan checklist from the spec §9**

Walk through the spec §9 verification list (10 items) and confirm each one passes (or has a documented "not yet implemented" note if it's a future PR like brain/readiness.py).

- [ ] **Step 6: Final summary commit (if any incidental edits)**

If Steps 1-5 surfaced any incidental fixes (typos, missing import, etc.), commit them:

```bash
git status -s
# If anything to commit:
git add -A
git commit -m "chore: post-implementation cleanup from §9 verification pass"
```

If clean, skip this commit.

- [ ] **Step 7: Push the branch (or report final state to user)**

```bash
git log --oneline rework/descar ^main | head -20  # show the new commits
git status -s                                    # expect clean
```

Report the commit list to the user for review. Push only on explicit user request (per safety norms; the branch may need a PR review first).

---

## Self-Review

**1. Spec coverage** (skimming spec sections → tasks):

- §1 decisions locked → Tasks 1, 2, 3, 4, 5, 6, 7 cover them all ✓
- §2 layered shape (cite) → Task 4 enforces SLOT_HARDWARE_FLAGS denylist via test ✓
- §3 slot schema changes → Tasks 4 + 5 populate HW grid + strip deprecated ✓
- §4 profile catalog (16 profiles) → Tasks 2 + 3 build + test the catalog ✓
- §5.1 slot→profile mapping → Tasks 4 + 5 create the 10 seeds + test enforces mapping ✓
- §5.2 dynamic scaffolds (vision, stt) → out of scope (explicit) ✓
- §5.3 spec-p3-brain §5 must-lands → Task 7 covers schema flip + brain.toml docstring ✓
- §5.4 drift fixes (qwen3tts, rerank port, tts port) → Tasks 4 + 6 ✓
- §5.5 3-way registry sync → Task 6 ✓
- §6 files add/touch summary → all listed in plan ✓
- §7 migration / rollout → captured in commit messages + test assertions ✓
- §8 risks → noted in commit messages (operator surprise on tool_model flip, port drift is seed-only) ✓
- §9 verification → Task 8 is the dedicated verification pass ✓
- §10 out of scope → documented (brain/readiness.py, STT, vision, family_defaults schema, seed_stacks) ✓

**2. Placeholder scan**: searched for "TBD", "TODO", "implement later", "fill in details", "similar to task N" — none found. Every step shows actual code/commands.

**3. Type consistency**: `SlotConfig.model_validate_filepath` checked (Step 1 of Task 4 has a fallback). `BrainChatConfig.tool_model` referenced consistently across Tasks 4 (docstring update) + 7 (default flip). `STATIC_SEED_SLOTS` referenced consistently across Task 6.

No inconsistencies found.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-20-seeded-profile-rework.md` (8 tasks).

**Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Best for the parallel-fan-out across the 8 tasks (some are independent — Tasks 1, 7 can run in parallel; Tasks 4 and 5 are also independent).

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints. Best when you want to see every step happen in your own context.

**Which approach?**

(I'll note: `rework/descar` is the target branch; if it's dirty or has parallel work, recommend a worktree via `superpowers:using-git-worktrees` before Task 1.)
