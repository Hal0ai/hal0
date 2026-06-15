# NPU as the chat-only `utility` slot

**Date:** 2026-06-15
**Status:** Approved (design) — pending spec review
**Author:** Claude (with Alexander)
**Related:** PR #822 (FLM tag/image fix, merged), memory `hal0-flm-trio-npu`, ADR-0008 §5 (NPU exclusivity), ADR-0009 (NPU packing)

## Problem / context

The NPU FLM "trio" (one `flm serve` process serving chat + embed + ASR via
`--embed 1 --asr 1`) is the only way the NPU is modelled today. It is bespoke,
caused a multi-fault outage (see PR #822), and blocks the model we actually want:

- **gemma4-it:e2b** runs fine **chat-only** (~23 tok/s) but **fails under the
  trio flags** — `DRM_IOCTL_AMDXDNA_CREATE_HWCTX IOCTL failed (err=-22)`: the
  e2b model + co-resident embed + ASR exceed the single-tenant NPU's 8-column
  hardware-context budget. The trio forces the slower gemma3:4b (~12 tok/s).
- The NPU-served **embed/ASR are unused**: Hindsight embeds on CPU and only
  needs an LLM for inference. So the trio buys complexity with no current payoff.

We also want the NPU to carry the **`utility` role** (background/utility LLM
inference — Hindsight memory extract/reflect, etc.), replacing the iGPU utility
slot (ROCm `qwopus3.5-9b-coder-mtp`, ~7 GB). That offloads utility work to the
NPU and frees the iGPU for the `chat`/`agent` slots.

## Decision

Make the NPU a **single chat-only slot** running **gemma4-it:e2b**, bound to the
**`utility` role via an alias (the `role` field) — not a rename**. Disable the
iGPU utility slot and the unused NPU embed/ASR shadow slots. Leave the trio code
**dormant** for a future "stacks" feature (model/slot/config snapshots that can
be switched) to generalise. Apply to the live box **and** the repo seeds /
installer / updater so fresh installs and upgrades match.

### Why alias, not rename

`normalize/resolver.py` routes virtual names by a role chain:

```python
DEFAULT_CHAINS = {
    "hal0/npu":     ("npu", "utility", "chat"),
    "hal0/utility": ("utility", "npu", "chat"),
}
# _slot_matches_role: role=="npu" matches device=="npu" OR role tag "npu";
# otherwise matches (slot.role or slot.name)
```

Setting `role = "utility"` on the NPU slot makes `hal0/utility` bind to it
explicitly, while `hal0/npu` still resolves to it because it matches by
**device** (`device=="npu"`). The slot keeps its **name `npu`**, so all the
name-keyed code (`routes/npu.py`, `dispatcher/npu_swap_status.py`,
`routes/models.py` upstream `"npu"`, NPU telemetry in `routes/hardware.py`,
the dashboard NPU card) is untouched. A rename would ripple through all of it
for no benefit.

## Design

### Slot end-state

| Slot | Before | After |
|------|--------|-------|
| `npu` | device=npu, FLM, `gemma3-4b-FLM`, trio (`[npu] asr=true embed=true`), `role=npu` | device=npu, FLM, **`gemma4-it-e2b-FLM`**, **chat-only** (no `[npu]` table), **`role="utility"`**, port 8088 |
| `utility` (iGPU) | device=gpu-rocm, `qwopus3.5-9b-coder-mtp`, role=utility, port 8081 | **`enabled=false`** on this NPU-present box (frees ~7 GB iGPU; reversible). NPU-absent boxes keep it. |
| `embed`, `stt` (NPU shadows) | trio shadows (offline) | **`enabled=false`** (unused — Hindsight embeds on CPU) |

Routing after the change:
- `hal0/utility` → NPU slot (explicit `role` match; iGPU utility disabled so no contention)
- `hal0/npu` → NPU slot (device match — unchanged)
- `gemma4-it-e2b-FLM` resolves to FLM tag `gemma4-it:e2b` via `flm_id_to_tag()` (PR #822). Load via the `-FLM` id form, never the bare colon tag (the `/load` gate rejects colon tags).

### Consumer repoint — Hindsight

Hindsight is the main `utility` consumer. The `hermes.env` profile is **stale**
(`HINDSIGHT_API_LLM_MODEL=qwen3-it-4b-FLM`, `BASE_URL=…:13305` — the retired
lemond port). Implementation step:
1. Identify the **live** operator memory-engine LLM config (not the stale
   `hermes` profile).
2. Point it at the virtual **`hal0/utility`** (role-tracking) rather than a
   hardcoded model id, so it follows the role wherever it lives.
3. Restart/verify the memory engine performs an inference round-trip against the
   NPU gemma4 slot.

### Installer / firstrun seeding

`install/profile_derive.py` currently provisions the NPU as a trio:

```python
NPU_TRIO_CAPS = frozenset({"agent", "stt-npu", "embed-npu"})
```

Change fresh-install provisioning to be **hardware-conditional**:
- **NPU present:** the NPU slot is chat-only and carries the `utility` role; the
  iGPU `utility` slot is **not** provisioned (or provisioned disabled); `stt-npu`
  / `embed-npu` passengers are **not** auto-provisioned (embed/STT, if selected,
  derive to GPU/CPU).
- **NPU absent:** unchanged — `utility` stays on the iGPU as today.

This lives in `profile_derive.py` (hardware-conditional logic), not the static
seed files. The static `utility.toml` seed is retained for the NPU-absent path;
`derive_device` / the firstrun bundle decides whether to enable it.

### Updater

`routes/updater.py` `/state` reports `flm: {current: "v0.9.42", source:
"manual-deb"}` and a `_parse_flm_version` helper with a `v0.9.42` literal.
Update so it reflects the **`0.9.43` toolbox image** (the source of truth post
PR #822 — `providers/flm.py` `_DEFAULT_FLM_IMAGE`, `capabilities/catalog.py`
`_FLM_TOOLBOX_IMAGE`) rather than a stale literal, and degrades gracefully.

### Seeds (repo)

Mirror the intended defaults into the checked-in seeds so fresh installs match,
keeping the hardware-conditional behaviour above:
- `installer/etc-hal0/slots/npu.toml` → role=utility, gemma4-it-e2b-FLM default, no `[npu]` table.
- `installer/etc-hal0/slots/utility.toml` → retained for NPU-absent boxes; `profile_derive` disables it when the NPU takes the utility role.
- NPU `embed`/`stt` shadow seeding → gated off by `profile_derive` (not auto-provisioned on NPU-present boxes).
- Keep `SEED_PROFILES`/seed-parity in lockstep (the `TestSeedFileParity` guard).

## Out of scope (deferred to "stacks")

- Removing the trio code (`is_npu_trio_shadow`, `dispatcher/npu_trio.py`,
  `NPU_SEEDED_SLOTS` shadows, the `[npu]` toggle schema). Left **dormant**.
- The "stacks" feature itself (switchable model/slot/config snapshots) — the
  trio becomes one stack later. Not designed here.
- Re-homing embed/ASR onto GPU/CPU as first-class slots (only needed if a
  consumer asks for them; today none does).

## Testing

- **Unit:** installer `derive_device` provisions NPU-present box as chat-only
  utility (no `stt-npu`/`embed-npu`); updater `/state` returns `0.9.43`.
- **Config/seed:** seed-parity tests pass; npu seed has `role=utility`, no `[npu]`.
- **Live verification (CT105):** `hal0/utility` resolves to the NPU gemma4 slot;
  gemma4-it:e2b serves chat (no HWCTX error); iGPU freed (~7 GB); Hindsight
  performs an inference round-trip via `hal0/utility`.

## Rollout

1. Live CT105: reconfigure `npu.toml` (role/model/drop trio), disable iGPU
   `utility` + `embed`/`stt` shadows, repoint Hindsight, verify.
2. Repo PR: seeds + `profile_derive.py` + `updater.py` + tests.
3. Deploy PR via `scripts/deploy.sh`; re-verify.

## Risks

- **Hindsight wiring unknown until inspected live** — the one step requiring
  live verification before flip. Mitigated by step-1 ordering (verify first).
- **gemma4-it:e2b capability vs qwopus-9b** for utility tasks (smaller model).
  User has chosen gemma4 for speed/intelligence; fall back to gemma3:4b or the
  iGPU slot is a one-line re-enable if quality regresses.
- **Other `hal0/utility` consumers** beyond Hindsight follow the role
  automatically; enumerate during implementation to confirm none expect the
  iGPU qwopus specifically.
