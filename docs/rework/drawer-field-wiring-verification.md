# Slot + model drawer field-wiring verification

Scope: every user-editable field in the slot edit drawer (`#slots/:name`) and
the model drawer (`#models` → *Edit options*), audited for whether its value
reaches the wire with the right key, on the right route, and is actually
persisted.

Session record (PR/issue narrative, process lessons): `docs/archive/handoffs/slot-pin-and-drawer-audit-2026-07-30.md`.
Pin semantics: `docs/rework/hal0-specs/spec-slot-pin-lifecycle.md`.

**Verdict: 8 real defects, 2 of them silent data loss.** The audit was scoped as
test-writing. The specs passed; writing them is what surfaced the defects. Every
finding below was reproduced with captured request bodies or proven against the
real loader — none are inferred from reading code.

## Method

Playwright e2e against the mocked API (`ui/tests/e2e/fixtures/apiMock.ts`), slot
lists seeded through an `addInitScript` over `window.HAL0_DATA`, mutations left
to fall through to `page.route`, and exact request bodies asserted via
`postDataJSON()`. Mutating requests are never mock-substituted
(`src/api/mock.ts` is GET-only), so `page.route` is authoritative for every
POST/PUT asserted.

The API half was checked separately against the backend rather than assumed:

- every key the slot drawer emits passes `unknown_slot_config_keys` — `parallel`,
  `image_pin: null`, `npu.*`, `model.default`, `chat_template: null`, `pinned`
- `reconcile_slot_updates`: `null` deletes a key, `""` persists an empty string
- `merge_update` replaces model `defaults` **wholesale**, so any unsurfaced key
  the drawer fails to round-trip is destroyed on save. `ModelDefaults` has 9
  fields, the drawer surfaces 8, and the one it doesn't (`rope_freq_base`) is
  pinned by an existing spec. Bounded and covered.

## Field → wire matrix

### Slot drawer

| Field | Wire → route | Covered by |
|---|---|---|
| Model select | `{model_id}` → `POST /slots/{name}/swap` | `slot-drawer-field-wiring-v3` W1 |
| Model select, live container | same, behind a ConfirmDialog | W2 |
| Parallel | `{parallel}` int / `null` → `PUT /config` | W3 (4 cases) |
| NPU Chat / Embed | `{npu:{chat,asr,embed}}` + `POST /restart` | W4 |
| NPU chat-model (installed) | `{npu, model:{default}}` | W5 |
| NPU chat-model (not installed) | `POST /api/models/{tag}/pull` → apply on `completed` | W6 |
| chat_template — set/change | `{chat_template}` | `slot-drawer-profile-v3` C7k (pre-existing) |
| chat_template — **removal** | `{chat_template: null}` | W7 (was broken — §1) |
| NPU ASR | `{npu:{…asr}}` | `slot-edit-controls-v3` (pre-existing) |
| device / NGL / threads / binary / image_pin | top-level → `PUT /config` | `slot-edit-controls-v3` (pre-existing) |
| ctx_size | `{ctx_size}` → `PATCH /defaults` | `slot-edit-controls-v3` (pre-existing) |
| extra_args | `{server:{extra_args}}` | `slot-edit-controls-v3` (pre-existing) |
| header toggle | `{pinned}` | `slot-pin-toggle-v3` (#1368) |

### Model drawer

| Case | Wire → route | Covered by |
|---|---|---|
| Duplicate + device template | `{new_id, profile}` → `POST /api/models/{id}/duplicate` | `model-drawer-duplicate-v3` D1 |
| Duplicate, no template | `{new_id}` only, no null `profile` | D2 |
| Suggested id derivation / pinning | — | D3 |
| Invalid id | blocked, no POST | D4 |
| Server 409 | inline + toast, dialog stays open | D5 |
| Full save contract | `PUT /api/models/{id}` | `model-drawer-save-info-v3` (pre-existing) |

### Rejected writes (previously zero coverage)

A grep for `status: 4`/`status: 5` across every `slot-*` spec returned nothing.
`drawer-save-errors-v3` pins: a rejected write never closes the drawer; edits
survive; the envelope `message` is surfaced verbatim; a failed `PATCH /defaults`
short-circuits the `/config` PUT; a rejected NPU toggle reverts to server truth;
and managed-flag / unbalanced-quote input is blocked client-side with zero PUTs.

## Defects

### 1. `chat_template` removal wrote nothing — #1372, **fixed** (#1377)

Clearing a per-slot override issued no request at all. The template stayed on
disk and kept feeding `llama-server` while the drawer showed the model default.
Not counted dirty either, so the discard guard stayed silent. No other dashboard
path removes an override.

Both the save body and the dirty aggregate gated on `overrideOpen`, which the
Clear button sets to `false` before either predicate runs.

- **Before:** captured PUTs after clear + Save = `[]`
- **After:** `{chat_template: null}`
- **Wire value:** `null`, not `""` — `reconcile_slot_updates` deletes on None;
  `""` persists an empty-string override
- **Pinned by:** `slot-drawer-field-wiring-v3` W7 (+ an inverse case: clear then
  re-pick the same template ⇒ no write), and two route tests in
  `tests/api/test_slot_config_validation.py` — the first coverage of
  None-deletion for any slot key

### 2. The flags migrator dropped every slot's `extra_args` — #1396, **fixed** (#1397)

`slot_flags_fold` folds a slot's effective tune onto its bound model. It had no
operator entry point at all (no CLI, no installer hook, only test references)
while the launch-side readers were already deleted — so upgraded boxes silently
stopped applying their tune with no recovery path.

Building the CLI exposed a worse problem: the fold itself dropped the freeform
tune. `collect_inputs` feeds the planner `SlotConfig.model_dump(by_alias=True)`,
where the `_tuck_server_into_extra` model_serializer re-parks the table under
`extra["server"]`; `_slot_flag_tokens` read only a top-level `server` key.

```
top-level server key present?  False
extra: {'server': {'extra_args': '-b 2048'}}
tokens the planner sees: (['--parallel', '4', '--kv-unified'], None, None)
```

The same gap defeated the divergent-share guard — two slots differing *only* in
`extra_args` folded to an identical tune, so no conflict was detected and a
winner would have been picked silently.

- **Pinned by:** `tests/cli/test_slot_migrate_flags.py` (7) +
  `tests/config/test_slot_flags_fold.py` (+2, one per half of the gap)

### 3. Model Context size corrupted or deleted the stored value — #1378, fix in flight (#1386)

`parseInt` prefix-parses, and the field had no validation, no error slot and no
save gate.

| typed | actual wire |
|---|---|
| `32k` | `{"defaults":{"context_size":32}}` — a 1000× collapse |
| `abc` | `{"defaults":{}}` — key absent ⇒ **stored value deleted** |

The deletion case is the severe one: `PUT` replaces `defaults` wholesale, so an
absent key is a delete, not "unchanged" — `--ctx-size` disappears from the launch
line and every bound slot falls back to the llama-server default. Both outcomes
shipped with a green "Updated" toast.

### 4. vision↔mmproj guard was decorative — #1380, fix in flight (#1392)

Toggling `vision` on a model with no projector rendered a red error and saved
anyway; the row advertised `vision` with nothing for `--mmproj` to load.

```
saveDisabled= false   PUTS: [{"defaults":{},"capabilities":["chat","vision"]}]
```

Both save gates keyed on `flagsError` only. No server-side backstop either —
`screen_model_write` validates `defaults.extra_args` and nothing else.

### 5. Display name could not be cleared — #1381, fix in flight (#1392)

`if (trimmedName && trimmedName !== …)` collapsed "unchanged" and "deliberately
emptied" into one branch, so the key was omitted.

```
PUTS: [{"defaults":{}}]     # name absent; old name survives
```

`dirty` was true, so Save was live and the drawer closed with an
`Updated <old name>` toast — every signal claimed success. The sibling fields
(`mmproj`, `hf_repo`, `hf_filename`) all clear correctly; only `name` had the
extra gate.

### 6. NPU toggles clobber the configured FLM tag — #1388, **unfixed**

Every modality toggle attaches `model.default`, sourced from `slot.model_id` —
which `useSlots.ts` documents as stale for exactly this slot class, and for which
it already exposes the correct `modelDefault`. The drawer never reads it.

```
# slot configured qwen3:8b, live model_id a stale GGUF; clicked NPU · ASR
PUTS: [{"npu":{"chat":true,"asr":true,"embed":false},
        "model":{"default":"qwen2.5-7b-instruct-Q4"}}]
```

Silent config corruption on NPU boxes: an ASR toggle rewrites the chat tag, then
restarts. Compounded by the chat `<select>` having no out-of-vocab passthrough
option, so the operator cannot see the value that is about to be sent.

### 7. Save silently dead on an NPU slot with malformed persisted `extra_args` — #1389, **unfixed**

```
PUTS: []   PATCHES: []   drawerOpen= 1   hasQuoteErr= false
```

No request, no toast, no inline error, no state change — an enabled button that
does nothing, permanently. `extraArgsErr` is computed from the persisted seed
(so a slot already malformed on disk starts blocked), and `errs.extraArgs` is
never rendered; the visible error element lives inside the `device !== "npu"`
branch, unmounted on an NPU slot. Same shape for Parallel/Context after
switching Device to `npu`.

### 8. Dirty-baselines track a live-polled prop — #1390 / #1391, **unfixed**, root cause filed as #1398

Both drawers seed form state once, then compare it against a prop re-derived on
every poll, so the two drift with no operator input.

- **#1390 (reproduced):** `ctxBaseline` falls back to the live 5s `metrics.ctx`.
  Field displayed `8192`, untouched; Save wrote `PATCH {"ctx_size":8192}` —
  persisting a context window nobody chose.
- **#1391 (source-verified only):** a dropped `/api/slots` poll degrades to the
  `/api/status` shape, which has no `config_enrichment`; `reconcileEnrichment`
  carries forward four keys and none are config fields. Every batched field then
  reads dirty and Save rewrites them **plus fires a cold restart**. Not
  reproduced: `VITE_MOCK_HAL0` short-circuits `/api/slots`, so the degraded path
  is unreachable from the harness — wants a `reconcileEnrichment` unit test.

#1372 is the same class and its fix is the template: collapse duplicated
predicates into one derived value.

## Test inventory

All new suites written red-first.

| Suite | Cases | Red-first evidence |
|---|---|---|
| `slot-drawer-field-wiring-v3.spec.ts` | 14 | W7 captured `[]` PUTs against an expected 1 |
| `model-drawer-duplicate-v3.spec.ts` | 5 | new surface — no prior duplicate-dialog e2e |
| `drawer-save-errors-v3.spec.ts` | 7 | new surface — no `status: 4xx` anywhere in `slot-*` |
| `tests/cli/test_slot_migrate_flags.py` | 7 | file failed to **import** — no such command |
| `tests/config/test_slot_flags_fold.py` | +2 | pinned the re-parked `extra["server"]` shape |
| `tests/api/test_slot_config_validation.py` | +2 | first None-deletion coverage for any slot key |
| `ui/tests/e2e/port.test.ts` | 7 | file failed to import — no `port.ts` |

Suite state at close: e2e **458 passed / 18 skipped** (476 collected); `pytest
tests/config tests/cli tests/slots` **1919 passed / 9 skipped**; vitest **11
passed**; eslint, tsc and ruff clean.

## Harness defect found while verifying

`playwright.config.ts` pinned port 5173 with `reuseExistingServer: !CI`, so a run
in one git worktree attached to another's Vite server and tested a branch it had
never checked out — confidently wrong in both directions, not flaky:

```
npx playwright test ...                     →   7 failed, 27 passed
HAL0_E2E_PORT=5199 npx playwright test ...  →  34 passed    # identical commits
```

Those 7 failures were nearly filed as a regression against another agent's
branch. CI was never affected (`CI=1` disables reuse). Fixed in #1400 by deriving
a stable per-worktree port.

**Two verification rules this established, both learned by getting them wrong:**

- A *filtered* Playwright run cannot prove suite health. A vitest file placed
  under `tests/e2e/` was collected by Playwright's default `testMatch`; its
  import aborted collection for the entire suite (`Total: 0 tests in 0 files`)
  and broke γ-suite, while a filtered run stayed green.
- Re-fetch before rebasing a branch that has had a stacked PR merged into it. A
  stale local branch produced a rebase missing merged work; a force-push would
  have deleted it from the open PR.
