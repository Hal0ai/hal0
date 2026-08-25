# Runner-image catalogue v2 — reliable discovery, operator defaults, IA moves

Date: 2026-08-24. Approved in-session by Alexander (operator). Scope grew out of
"the runner-images page has never worked as designed": publishing a new runner
image (e.g. `hal0-combined:0824`) and pressing Sync must surface it as
pullable and assignable as a default.

## Diagnosis (live-verified on ct105, rc7)

1. **Flagship runner missing from the catalogue.** `sync_runner_images`
   sources rows exclusively from `images.json`
   (`Hal0ai/hal0-runner-images`), which deliberately excludes
   `hal0-combined`; the stale `rocmfpx` row still advertises the retired,
   #1888-broken `hal0-rocmfpx:ade07ba`. A new `hal0-combined` push can
   never appear.
2. **Rows frozen to pinned tags.** Sync probes exactly the `tag` named in
   each `images.json` entry; new tags stay invisible until that file is
   hand-edited. The unpinned path's "newest" heuristic (`tags[-1]`,
   registry order) is unreliable.
3. **"Assign as default" never built.** The page is browse/pull only. The
   fleet default is the baked `DEFAULT_ROCMFPX_IMAGE` constant; the only
   override is per-slot `image_pin` in the slot drawer.

Adjacent live defect fixed during diagnosis (root-cause class belongs to
this wave): `/api/stacks` 500'd since Jul 12 because a root-run CLI wrote
`/var/lib/hal0/stacks/state.json` (mkstemp → 0600 root) and
`/etc/hal0/stacks.toml` (0600 root); the hal0-user API gets
`PermissionError`, which `read_stack_state`/`_read_toml` don't handle →
generic `system.internal`. Box hot-fixed (chown/chmod); code fixes below.

## Design

### 1. Per-family default override map (static by default)

- New optional `[slots.default_images]` table in `hal0.toml`, keyed by the
  existing runner-family/binary keys (`rocmfpx`, `cpu`, `vulkan`, `flm`,
  `kokoro`, `moonshine`, `qwen3tts`, `comfyui`).
- Image resolution order becomes: slot `image_pin` → profile `image` →
  `[slots.default_images]` entry → baked default. Unset key = exactly
  today's behaviour; release bumps remain the normal roll mechanism for
  every family (voice/ComfyUI/FLM stay release-static unless the operator
  sets a key).
- Written/cleared through the existing `PUT /api/settings` deep-merge; no
  new transport.
- Safety: the Vulkan-lane gate (`VULKAN_CAPABLE_IMAGE_REFS`) still applies
  at slot-load preflight — an override cannot silently re-arm #1888.
  Setting an override bounces nothing; slots surface image drift (#2035
  comparator) and existing restart paths apply it with today's consent
  posture.

### 2. Registry completeness + tag tracking (fresh uploads visible)

- `images.json` (hal0-runner-images repo, separate PR): add the missing
  `hal0-combined` entry; retire/replace the stale `rocmfpx: ade07ba` row.
- `runner_image_sync`: in addition to the (optional) pinned headline tag,
  fetch `tags/list` per entry and persist `available_tags` on the row,
  sorted newest-first: date-shaped numeric tags desc → semver desc →
  registry order as last resort. Pinned entries keep their pin as the
  headline but still carry the list. Push `:0826` tomorrow → Sync → it is
  in the row's tag list with no file edits anywhere.
- Row enrichment at list time: `is_default` (which family default resolves
  to this ref, and whether via override or release constant), `in_use_by`
  (slot names whose rendered unit references the ref), existing
  `downloaded` state.

### 3. UI + IA moves

- **Runner Images moves from a Models tab to a subpage of Slots**, with
  its own nav-rail sub-link (both nav registries: nav rail AND the
  command-palette Nav section — the palette list is a known stale
  duplicate).
- **Profiles moves beneath Models** (sub-link under Models where Runner
  Images used to sit).
- Runner Images page gains: a **Defaults strip** (family → effective ref,
  `release default` vs `override` badge, clear-override action); per-row
  **tag picker** with "newer tag available" chip; per-tag actions **Pull**
  and **Set as family default** (confirm dialog names the slots that will
  drift). Slot drawer `image_pin` untouched as the per-slot escape hatch.
  Settings → Hardware/Runtimes links here instead of duplicating.

### 4. Stacks permission hardening (root-cause class)

- `write_stack_state_atomic`: fchmod the tempfile to 0o664 before
  `os.replace` so a root-run CLI leaves the state readable/writable for
  the service group (dir is setgid `hal0`).
- Root-run CLI writes of `/etc/hal0/stacks.toml` land 0o640 `root:hal0`.
- `read_stack_state` and the stacks list path map `PermissionError` to a
  typed error (`stacks.state_unreadable`, actionable message naming the
  file) instead of the generic 500 — never a silent "no stack" lie.

## Delivery

Four PRs, independently mergeable, in order:

1. `hal0-runner-images`: `images.json` — add `hal0-combined`, retire stale
   `rocmfpx` row.
2. `hal0`: stacks permission hardening + typed error (small, standalone).
3. `hal0`: sync tag-tracking + row enrichment + default-override map +
   resolution change (backend).
4. `hal0`: UI — IA moves (Slots subpage, Profiles under Models, both nav
   registries) + Defaults strip + tag picker + set-default flow.

Testing: red-first throughout. Tag-sort unit tests; sync merge tests
(pinned + tracked); resolution-order tests incl. gate refusal; stacks
permission regression (root-written file readable, PermissionError typed);
UI vitest for defaults strip/tag picker/nav registries; γ-suite in CI.
