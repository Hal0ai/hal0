# Install / Setup / Update — Fix Plan (2026-07-10)

Follow-up to the v0.9.6 install/setup/update audit (session review of the
installer, `hal0 setup`, and the updater against the recently landed
features: FLM trio + 0.9.44 toolbox, in-tree hal0-bench, slot-image
override/retag, virtual seed profiles). This records the issues found and
the fix plan; work lands on `claude/install-setup-update-review-idud0q`.

## Findings → fixes, by tier

### Tier 1 — functional bug: toolbox manifest pins unreachable on prod installs
**Finding.** Nothing installs `/etc/hal0/manifest.json`, and
`_find_manifest_path()`'s only fallback resolves `manifest.json` relative to
the *source file* (`loader.py` → `parents[3]`), which on a production
(non-editable pip) install is the venv's `lib/python3.x/` — no manifest
there. Net effect: on every prod box `load_manifest()` returns `{}`, so the
comfyui digest pin (`manifest_image_ref("comfyui")`, `providers/comfyui.py:182`)
and `hal0 doctor toolbox-pull` silently fall back to tag pulls, and the
0.9.44 FLM digest never reaches installed boxes. The updater parses
`ReleaseManifest.toolbox_images` (`updater.py:205`) but never applies it —
a dead field.

**Fix.** Add `paths.usr_lib() / "manifest.json"` (i.e.
`/usr/lib/hal0/current/manifest.json`) as a resolution candidate in
`_find_manifest_path()`, after the `/etc/hal0` operator override and before
the source-tree dev fallback. The versioned tree ships `manifest.json`
(release tarball stages it; install.sh rsyncs the repo root), and the
`current` symlink is atomically swapped by both install and update — so
pins refresh with every code swap, with no updater pass and no `/etc`
write, matching the virtual-seeds philosophy. `/etc/hal0/manifest.json`
remains a deliberate operator override. Unit tests cover the new
resolution order under `HAL0_HOME`.

### Tier 2 — setup honesty + port typo
- **NPU step over-promises.** `setup_copy.py` ("embeddings, speech-to-text,
  and text-to-speech in parallel") and `setup_ui.py` ("Run embed + STT +
  TTS on the NPU?" / "Enable NPU trio?") promise three modalities, but
  `derive_device()` routes only STT to the NPU on opt-in (embed → GPU,
  TTS → CPU; trio passenger caps are deliberately dormant,
  `profile_derive.py:22-28`, and TTS is not an FLM trio modality at all).
  Fix: reword pane + prompt to what apply actually provisions (STT
  offload now; full chat/ASR/embed trio per-slot via the dashboard's NPU
  drawer). No behavior change.
- **`img` slot port typo.** `api/routes/installer.py` `_SLOT_META` maps
  `img` → 8186; the seed (`img.toml`), the port-range comment in
  `schema.py`, and the ComfyUI service all use 8188. Fix to 8188.

### Tier 3 — orphaned `npu.toml` seed
The install seed loop copies `flm tts rerank utility img` — never
`npu.toml` — yet the loop comment (`install.sh` "Pre-populate
/etc/hal0/slots/{npu,tts}.toml"), `installer/README.md`, and
`slots/qwen3tts.toml` all claim `npu` is seeded. `npu.toml` duplicates
`flm.toml` (same profile/device/port 8088; seeding both would port-clash).
Fix: delete `installer/etc-hal0/slots/npu.toml`, port its useful `[npu]`
trio how-to comment block into `flm.toml`, retarget
`tests/config/test_schema_npu.py::test_seed_npu_toml_validates` at
`flm.toml`, and correct the three stale references.

### Tier 4 — docs catch-up
- `docs/getting-started/setup.mdx`: still claims the installer passes
  `--no-slots` and seeds zero slots; the installer dropped `--no-slots`
  and now scaffolds model-less capability + NPU slot structure. Align
  with `install.mdx` (which is correct).
- `docs/guides/update-and-rollback.mdx`: documents two of the three
  self-healing passes — add `retag_stale_slot_images` (stale
  former-default slot/profile image refs → `DEFAULT_ROCMFPX_IMAGE`);
  refresh the stale cosign-hatch version references ("through v0.8.5b2",
  "v0.8.4b1") to describe the actual rule (all 0.x / pre-release).
- `docs/guides/manage-slots.mdx`: document the 0.9.5 slot-level image
  override (`slot.image` → `profile.image` → default; drawer Image row;
  updater retag of stale defaults).
- New `docs/guides/benchmarks.mdx`: the in-tree bench system —
  `hal0 bench`, `/api/benchmarks`, Benchmarks dashboard tab, the three
  systemd units (weekly timer, safe-by-default worker), suites +
  `window.toml` politeness policy, `/var/lib/hal0-bench` state root,
  `hal0-benchctl` sudoers seam.

### Tier 5 — installer test harness rot
`tests/harness/installer-test.sh` still tests Caddy/TLS/auth flows removed
in v0.3.0 (ADR-0012) (`tls-default`, `no-tls` rows), asserts a
`hal0-slot@.service` file under the prefix that install.sh no longer
writes, and carries stale comments (claims `installer/systemd/` was
removed — it exists and is read; cites dead uninstall.sh line numbers).
Fix: drop the Caddy/TLS rows, fix the dev-files/dev-units expectations to
match the current installer, correct the comments.

### Tier 6 — small hygiene
- `manifest.json`: bump `version` to the release line and `channel` to
  `stable` (was `0.5.0-alpha.1` / `dev`); fix the self-contradicting
  qwen3tts `_notes` ("digest is null until first CI push" next to a real
  digest).
- `scripts/update-toolbox-digests.sh`: header calls
  `.github/workflows/toolbox.yml` "the never-built workflow" — it exists
  and is the CI push path; fix the description.
- `installer/install.sh`: FLM pin comment block still narrates 0.9.43
  history above the 0.9.44 value — compress to current; drop the dead
  "pin the real checksum before v0.2 ships" placeholder narration.
- Bench systemd units hardcode `/usr/lib/hal0/venv/bin/hal0`; rewrite
  ExecStart on copy when `HAL0_PREFIX` is overridden (same pattern the
  other units use via `${PREFIX}`).

## Deliberately deferred (follow-up features, not this PR)
- **Wire PROFILE_BENCH to the live bench store** — the setup/profile hero
  numbers are a hardcoded June-2026 table (`schema.py:1082`); feeding them
  from `/var/lib/hal0-bench` roster results is a feature with UI impact.
- **Setup writing `[npu]` trio toggles / NPU chat slot** — the passenger
  caps are dormant by explicit design decision ("NPU box is chat-only");
  reviving them belongs to a product decision, not a cleanup PR.
- **Answers-file schema parity** (capability scaffold slots, accepted-but-
  unapplied keys) — needs schema design.
- **Updater applying release-manifest `toolbox_images`** — superseded by
  the Tier-1 approach (pins ride the code swap); the mirror field stays
  for external consumers of the releases endpoint.

## Validation
- `pytest tests/config tests/updater tests/api -k "manifest or npu or schema"`
  plus the full suite for touched modules; `ruff check` clean.
- Docs build not exercised here (Starlight); mdx edits kept structural.
