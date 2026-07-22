# Prerelease Preview Pipeline Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the official `vX.Y.Z-alpha.N`/beta/RC release action and `hal0 update --channel preview` safe, channel-isolated, immutable, and verifiable without publishing anything during implementation.

**Architecture:** Keep `src/hal0/release/policy.py` as the tag-policy source of truth. Add a shared accepted-channel/manifest acceptance layer used by schema, API, CLI, updater, and UI; make the updater validate requested channel against the fetched manifest before persisting or applying. Harden `.github/workflows/release.yml` to checkout the exact tag, consume policy outputs, reject collisions/overwrites, set explicit GitHub flags, and verify artifacts before the external channel pointer gate.

**Tech Stack:** Python 3.12, Pydantic, FastAPI, Typer, pytest, React/TypeScript, Bash, GitHub Actions, Cosign/Sigstore, PyPI Trusted Publishing.

## Global Constraints

- The canonical official prerelease channel is `preview`.
- Every official cut uses a fresh short-lived branch such as `release/v1.0.0-alpha.1`; never reuse `prerelease-channel`.
- Do not publish tags, GitHub Releases, PyPI distributions, or channel pointers during implementation or tests.
- Preview releases must be GitHub prereleases and must never be GitHub latest.
- Published tags, releases, assets, and PyPI versions are immutable; no `--clobber` or replacement path.
- Stable clients must never consume preview artifacts; preview may consume preview or a promoted stable artifact.
- Preserve existing Python, UI, Chromium/Gamma, updater, rollback, and release safety coverage.
- External `hal0-web` preview-pointer deployment is a required release gate, not an implicit mutation from this repository.
- Channel selection persists only after the requested channel's current manifest is reachable, signature/schema-valid, and channel-coherent; failures leave the prior channel unchanged.
- This repository implements the local manifest-bundle contract and verification seams; external hal0-web serving/verification remains a hard prerelease gate.
- Release tag resolution occurs in a prerequisite `resolve` job; build/publish jobs depend on it and checkout the resolved exact tag.

---

## File Map

- `src/hal0/updater/updater.py` — manifest channel semantics, accepted-channel validation, fetch/check/prepare channel binding.
- `src/hal0/config/schema.py` — persisted `telemetry.channel` validation.
- `src/hal0/api/routes/updater.py` — channel API validation and validated persistence.
- `src/hal0/cli/update_commands.py` — `UpdateChannel` enum and `--channel preview` help/flow.
- `ui/src/api/hooks/useUpdates.ts` — frontend channel union and mutation type.
- `ui/src/dash/settings/pages/diagnostics/UpdatesPage.jsx` — preview selector and display copy.
- `installer/bootstrap.sh` — canonical release endpoint and channel validation.
- `.github/workflows/release.yml` — exact-ref checkout, policy outputs, collision gates, manifest targets, explicit release flags, non-overwriting publication.
- `scripts/release-check.sh` — preview channel/tag preflight and collision policy.
- `tests/updater/test_updater.py` — manifest semantics, URL, channel binding, persistence/update behavior.
- `tests/api/test_updater_routes.py` — API preview acceptance and failure-preserves-channel behavior.
- `tests/cli/test_update_commands.py` — CLI preview option and API interaction.
- `tests/config/test_schema.py` — persisted preview channel validation.
- `tests/release/test_policy.py` and `tests/release/test_channel.py` — policy matrix and channel helpers.
- `tests/release/test_workflow_contract.py` — new static workflow safety contract tests.
- `tests/installer/test_bootstrap_contract.py` — new shell contract tests for canonical channel endpoint.
- `docs/internal/release-manifest.md` — stable-to-preview promotion and signed channel-pointer contract.
- `docs/guides/update-and-rollback.mdx` and `docs/getting-started/install.mdx` — preview install/update/rollback behavior and external hal0-web gate.

---

### Task 1: Add shared preview channel and manifest acceptance semantics

**Files:**
- Modify: `src/hal0/updater/updater.py:143-280,305-375,1450-1590`
- Modify: `src/hal0/config/schema.py:1985-2005`
- Test: `tests/updater/test_updater.py`
- Test: `tests/config/test_schema.py`

**Interfaces:**
- Produce `UpdateChannelName = Literal["stable", "preview", "nightly"]` or the repository-equivalent shared type.
- Produce a manifest acceptance helper with a stable signature, for example:
  `validate_manifest_for_channel(manifest: ReleaseManifest, requested_channel: str) -> ReleaseManifest`.
- Preserve `releases_url(channel)` as the canonical URL helper and make preview use `https://releases.hal0.dev/preview.json`.

- [ ] **Step 1: Write failing manifest/channel tests.**

Add tests to `tests/updater/test_updater.py` covering:

```python
def test_preview_manifest_accepts_preview_channel() -> None:
    manifest = _parse_manifest({**VALID_MANIFEST, "channel": "preview", "release_kind": "preview", "prerelease_stage": "alpha"})
    assert validate_manifest_for_channel(manifest, "preview") is manifest


def test_promoted_stable_manifest_is_accepted_by_preview_channel() -> None:
    manifest = _parse_manifest({**VALID_MANIFEST, "channel": "preview", "release_kind": "stable", "prerelease_stage": None})
    assert validate_manifest_for_channel(manifest, "preview") is manifest


def test_stable_channel_rejects_preview_manifest() -> None:
    manifest = _parse_manifest({**VALID_MANIFEST, "channel": "preview", "release_kind": "preview", "prerelease_stage": "alpha"})
    with pytest.raises(ValueError, match="requested channel.*stable"):
        validate_manifest_for_channel(manifest, "stable")


def test_requested_channel_must_match_manifest_channel() -> None:
    manifest = _parse_manifest({**VALID_MANIFEST, "channel": "nightly", "release_kind": "nightly"})
    with pytest.raises(ValueError, match="manifest channel.*nightly"):
        validate_manifest_for_channel(manifest, "preview")
```

Add `preview` to the URL table and add a schema test in `tests/config/test_schema.py` that `TelemetryConfig(channel="preview")` validates while an unknown channel still fails.

- [ ] **Step 2: Run the focused tests and confirm RED.**

Run:

```bash
HAL0_HOME="$(mktemp -d)" uv run pytest -q \
  tests/updater/test_updater.py \
  tests/config/test_schema.py
```

Expected: failures for preview channel validation, stable-to-preview promotion, and the new helper.

- [ ] **Step 3: Implement the minimum shared semantics.**

In `ReleaseManifest._validate_release_policy`, keep `preview` release kinds requiring `prerelease_stage`, but allow a stable release kind to target either `stable` or `preview`; keep nightly restricted to nightly. Add the requested-channel helper with this exact acceptance matrix:

```python
_ACCEPTED_RELEASE_KINDS = {
    "stable": {"stable"},
    "preview": {"preview", "stable"},
    "nightly": {"nightly"},
}
```

Reject an unknown requested channel, a manifest whose `channel` differs from the requested channel, or a release kind outside the matrix. Change `TelemetryConfig.channel` to accept exactly `stable | preview | nightly`, preserving the existing default.

- [ ] **Step 4: Run the focused tests and confirm GREEN.**

Run the same pytest command. Expected: all targeted updater/schema tests pass.

- [ ] **Step 5: Commit the independently testable model change.**

```bash
git add src/hal0/updater/updater.py src/hal0/config/schema.py \
  tests/updater/test_updater.py tests/config/test_schema.py
git commit -m "fix(updater): support preview channel manifest policy"
```

---

### Task 2: Make updater, API, CLI, and dashboard persist preview safely

**Files:**
- Modify: `src/hal0/updater/updater.py:330-375,1460-1590`
- Modify: `src/hal0/api/routes/updater.py:20-60,450-575,776-840`
- Modify: `src/hal0/cli/update_commands.py:50-70,236-305`
- Modify: `ui/src/api/hooks/useUpdates.ts:86-100`
- Modify: `ui/src/dash/settings/pages/diagnostics/UpdatesPage.jsx:1-180`
- Test: `tests/api/test_updater_routes.py`
- Test: `tests/cli/test_update_commands.py`

**Interfaces:**
- API `PUT /api/updates/channel` accepts `{"channel": "preview"}` and returns it only after validated persistence.
- CLI `hal0 update --channel preview` sends the same API payload.
- `Updater.check()` and `Updater.prepare()` call `validate_manifest_for_channel` before returning/applying data.

- [ ] **Step 1: Write failing API/CLI/updater tests.**

Add to `tests/api/test_updater_routes.py`:

```python
def test_channel_put_accepts_preview(isolated_client: TestClient, tmp_hal0_home: str) -> None:
    response = isolated_client.put("/api/updates/channel", json={"channel": "preview"})
    assert response.status_code == 200
    assert response.json() == {"channel": "preview"}


def test_channel_put_keeps_previous_channel_when_validation_fails(isolated_client: TestClient) -> None:
    # Use the route's manifest-fetch seam to force validation failure.
    isolated_client.put("/api/updates/channel", json={"channel": "stable"})
    response = isolated_client.put("/api/updates/channel", json={"channel": "preview"})
    assert response.status_code in {400, 502}
    assert isolated_client.get("/api/updates/channel").json() == {"channel": "stable"}
```

Add CLI coverage asserting `UpdateChannel.preview.value == "preview"` and `update(channel=UpdateChannel.preview)` posts `{"channel": "preview"}`. Add updater tests that `check()` and `prepare()` reject a wrong-channel manifest before download/apply state changes.

- [ ] **Step 2: Run the focused tests and confirm RED.**

```bash
HAL0_HOME="$(mktemp -d)" uv run pytest -q \
  tests/api/test_updater_routes.py \
  tests/cli/test_update_commands.py \
  tests/updater/test_updater.py -k 'channel or preview'
```

Expected: preview is rejected or not present, and wrong-channel manifests are not rejected at the requested boundary.

- [ ] **Step 3: Implement channel validation and delayed persistence.**

Use the shared accepted-channel set in `src/hal0/api/routes/updater.py` instead of a second literal set. In the channel PUT route:

1. reject unknown channel;
2. construct/validate the merged config;
3. if switching to a release channel, fetch and parse the channel manifest through the same updater validation seam;
4. only then atomically persist `telemetry.channel`;
5. leave the prior file unchanged when validation fails.

Update `Updater.check()` to parse the manifest and call `validate_manifest_for_channel` before constructing `ReleaseInfo`. Update `prepare()` to perform the same check before any download, cache write, or state transition. Keep explicit `HAL0_RELEASES_URL` test overrides intact.

Add `preview` to `UpdateChannel`, its help text, frontend `UpdateChannelName`, and the dashboard selector. Use an explicit option branch:

```jsx
onChange={e => {
  const next = e.target.value;
  if (next === 'stable' || next === 'preview' || next === 'nightly') {
    setChannel(next);
  }
}}
```

- [ ] **Step 4: Run the focused tests and confirm GREEN.**

```bash
HAL0_HOME="$(mktemp -d)" uv run pytest -q \
  tests/api/test_updater_routes.py \
  tests/cli/test_update_commands.py \
  tests/updater/test_updater.py -k 'channel or preview'
cd ui && npm run typecheck && npx eslint src/api/hooks/useUpdates.ts src/dash/settings/pages/diagnostics/UpdatesPage.jsx
```

- [ ] **Step 5: Commit the updater/client change.**

```bash
git add src/hal0/updater/updater.py src/hal0/api/routes/updater.py \
  src/hal0/cli/update_commands.py src/hal0/config/schema.py \
  ui/src/api/hooks/useUpdates.ts \
  ui/src/dash/settings/pages/diagnostics/UpdatesPage.jsx \
  tests/api/test_updater_routes.py tests/cli/test_update_commands.py
 git commit -m "feat(update): make preview channel selectable and safe"
```

---

### Task 3: Align bootstrap and release-manifest documentation contracts

**Files:**
- Modify: `installer/bootstrap.sh:1-100`
- Modify: `docs/internal/release-manifest.md`
- Modify: `docs/guides/update-and-rollback.mdx`
- Modify: `docs/getting-started/install.mdx`
- Create: `tests/installer/test_bootstrap_contract.py`

**Interfaces:**
- Default bootstrap endpoint: `https://releases.hal0.dev/${HAL0_CHANNEL}.json`.
- `HAL0_CHANNEL` accepts `stable`, `preview`, or `nightly`; invalid values fail before download.
- The installer uses the same manifest signature/bundle trust contract as the updater.

- [ ] **Step 1: Write failing shell contract tests.**

Create `tests/installer/test_bootstrap_contract.py` that reads the script text and asserts:

```python
def test_bootstrap_uses_canonical_channel_endpoint() -> None:
    script = Path("installer/bootstrap.sh").read_text()
    assert "https://releases.hal0.dev/${HAL0_CHANNEL}.json" in script
    assert "/releases/latest/download" not in script


def test_bootstrap_validates_preview_channel() -> None:
    script = Path("installer/bootstrap.sh").read_text()
    assert 'stable|preview|nightly' in script or 'preview' in script
```

- [ ] **Step 2: Run tests and confirm RED.**

```bash
uv run pytest -q tests/installer/test_bootstrap_contract.py
```

Expected: the endpoint assertion fails against the current GitHub `/latest` default.

- [ ] **Step 3: Implement the canonical endpoint and validation.**

Set the script default to the canonical per-channel endpoint, preserve explicit
`HAL0_RELEASES_URL` overrides, validate the channel before constructing the URL, and
keep the current digest/signature verification path. Add the local manifest-bundle
verification seam used by the updater/bootstrap while leaving external bundle
serving as a hal0-web release gate. Update the manifest docs to state that `channel`
is the pointer target and `release_kind` is the artifact kind, including the stable
artifact promoted to preview case and the external hal0-web pointer gate. Update
install/update/rollback docs with `HAL0_CHANNEL=preview` and explicitly state that
stable clients never consume preview.

- [ ] **Step 4: Run tests and shell syntax validation.**

```bash
uv run pytest -q tests/installer/test_bootstrap_contract.py
bash -n installer/bootstrap.sh
```

- [ ] **Step 5: Commit the installer/docs contract.**

```bash
git add installer/bootstrap.sh tests/installer/test_bootstrap_contract.py \
  docs/internal/release-manifest.md docs/guides/update-and-rollback.mdx \
  docs/getting-started/install.mdx
git commit -m "fix(update): align bootstrap with preview channel manifests"
```

---

### Task 4: Harden the release workflow for exact refs and immutable publication

**Files:**
- Modify: `.github/workflows/release.yml:35-70,80-120,450-510,532-600`
- Modify: `scripts/release-check.sh:1-80,250-335`
- Create: `tests/release/test_workflow_contract.py`

**Interfaces:**
- Workflow outputs must consume `ReleasePolicy.to_github_outputs()` for `kind`,
`prerelease_stage`, `manifest_targets`, `github_prerelease`, `github_latest`, and
`publish_pypi`.
- `workflow_dispatch` and `workflow_call` checkout the exact supplied tag.
- Official upload fails when release/tag/asset/PyPI version already exists.

- [ ] **Step 1: Write failing workflow contract tests.**

Create `tests/release/test_workflow_contract.py` using `Path.read_text()` and
assertions on the workflow text. Cover:

```python
def test_release_checks_out_dispatch_tag() -> None:
    text = Path(".github/workflows/release.yml").read_text()
    assert "ref: ${{ steps.ver.outputs.tag }}" in text


def test_preview_release_is_not_latest() -> None:
    text = Path(".github/workflows/release.yml").read_text()
    assert "github_prerelease" in text
    assert "github_latest" in text
    assert "--clobber" not in text


def test_release_policy_controls_manifest_targets() -> None:
    text = Path(".github/workflows/release.yml").read_text()
    assert "manifest_targets" in text
    assert "publish_pypi" in text


def test_release_signs_and_uploads_every_manifest_bundle() -> None:
    text = Path(".github/workflows/release.yml").read_text()
    assert 'cosign sign-blob --yes --bundle "${MANIFEST}.bundle" "${MANIFEST}"' in text
    assert '"${MANIFEST}.bundle"' in text
```

Add policy matrix tests that assert:

```python
assert ReleasePolicy.from_tag("v1.0.0-alpha.1").github_prerelease is True
assert ReleasePolicy.from_tag("v1.0.0-alpha.1").github_latest is False
assert ReleasePolicy.from_tag("v1.0.0").manifest_targets == ("stable", "preview")
```

- [ ] **Step 2: Run tests and confirm RED.**

```bash
uv run pytest -q tests/release/test_workflow_contract.py tests/release/test_policy.py
```

Expected: the current workflow fails the no-`--clobber` and policy-output assertions.

- [ ] **Step 3: Implement exact ref checkout and policy outputs.**

Add a dedicated `resolve` job before the build job. It resolves the push/dispatch/call tag, runs `ReleasePolicy.to_github_outputs()`, and exposes the outputs. Make the build and PyPI jobs depend on `resolve`; both use `ref: ${{ needs.resolve.outputs.tag }}` in `actions/checkout`. Assert that `git rev-parse HEAD` equals `git rev-list -n1 "${TAG}"`.

Export policy outputs from a single `PYTHONPATH=src python3 -m hal0.release.policy
"${TAG}" --format github` step. Use those outputs for version checks, manifest
creation, release flags, target manifests, and the PyPI job condition. Do not infer
preview/stable behavior from a string comparison such as `channel == nightly`.

Generate one manifest per `manifest_targets` target. For a stable final, the stable
and preview manifests must differ only in target `channel` and self-reference URL;
artifact version, digest, signer, and release kind remain identical. Run
`ReleaseManifest` validation on every generated manifest. For each exact generated
manifest file, run `cosign sign-blob --bundle "${MANIFEST}.bundle" "${MANIFEST}"`,
self-verify the bundle using the client-pinned release-workflow identity and issuer,
and upload both `${channel}.json` and `${channel}.json.bundle`. The updater's
manifest verification intentionally derives the latter sibling URL, so missing or
unsigned bundles must make the workflow contract test fail.

- [ ] **Step 4: Implement collision-safe publication.**

Before creating a release, fail if `gh release view "$TAG"` succeeds. Before upload,
fail if any target asset already exists. Replace the current `gh release upload "$TAG" "$TARBALL" "$BUNDLE" "$SIG" "$CRT" "$MANIFEST" --clobber` command with the same explicit asset list and no overwrite option. Verify the created release's
`isPrerelease` and `isLatest` values against policy after creation.

Add explicit preflight for the PyPI normalized version when `publish_pypi=true`; the
PyPI job must not run for nightly and must checkout the exact tag. Refuse a dispatch
or reusable invocation when the supplied tag is absent or its commit does not equal
the checked-out ref.

Keep external channel-pointer advancement outside the immutable release creation
step and document it as the final, separately verified gate. Do not add a live
pointer mutation to tests or local runs.

- [ ] **Step 5: Extend `scripts/release-check.sh`.**

Accept `--channel preview`, require preview tags to match `^vX.Y.Z-(alpha|beta|rc).N$`,
check that the tag's base version is consistent with `pyproject.toml`, and reject a
release/tag/version that already exists. Keep dry-run behavior read-only. Update the
usage text to `stable|preview|nightly`.

- [ ] **Step 6: Run contract and shell checks.**

```bash
uv run pytest -q tests/release/test_workflow_contract.py tests/release/test_policy.py tests/release/test_channel.py
bash -n scripts/release-check.sh
bash scripts/release-check.sh --help
```

- [ ] **Step 7: Commit the workflow hardening.**

```bash
git add .github/workflows/release.yml scripts/release-check.sh \
  tests/release/test_workflow_contract.py tests/release/test_policy.py
 git commit -m "fix(release): publish preview artifacts immutably"
```

---

### Task 5: Add non-publishing release rehearsal and full verification

**Files:**
- Modify: `tests/release/test_workflow_contract.py`
- Modify: `tests/updater/test_updater.py`
- Modify: `docs/superpowers/specs/2026-07-22-prerelease-preview-pipeline-hardening-design.md` only if verification reveals a contract correction.

- [ ] **Step 1: Add non-publishing policy rehearsal tests.**

Test generated policy metadata for:

```python
@pytest.mark.parametrize("tag, kind, targets, prerelease, latest", [
    ("v1.0.0-alpha.1", "preview", ("preview",), True, False),
    ("v1.0.0-beta.2", "preview", ("preview",), True, False),
    ("v1.0.0-rc.1", "preview", ("preview",), True, False),
    ("v1.0.0", "stable", ("stable", "preview"), False, True),
])
def test_policy_matrix(tag, kind, targets, prerelease, latest):
    policy = ReleasePolicy.from_tag(tag)
    assert policy.kind == kind
    assert policy.manifest_targets == targets
    assert policy.github_prerelease is prerelease
    assert policy.github_latest is latest
```

Use local temporary manifests and `HAL0_RELEASES_URL=file:///tmp/preview-manifest.json` to exercise
`Updater(channel="preview").check()` and `prepare()` without network, tag push,
GitHub, PyPI, or external pointer access.

- [ ] **Step 2: Run the complete focused release/update suite.**

```bash
HAL0_HOME="$(mktemp -d)" uv run pytest -q \
  tests/release \
  tests/updater \
  tests/api/test_updater_routes.py \
  tests/cli/test_update_commands.py \
  tests/config/test_schema.py \
  tests/installer/test_bootstrap_contract.py
```

Expected: zero failures and no test-created publication/network side effects.

- [ ] **Step 3: Run repository validation.**

```bash
git diff --check
uv run ruff check src/hal0/release src/hal0/updater/updater.py src/hal0/api/routes/updater.py src/hal0/cli/update_commands.py tests/release tests/updater tests/api/test_updater_routes.py tests/cli/test_update_commands.py tests/config/test_schema.py tests/installer/test_bootstrap_contract.py
uv run pytest -q
cd ui && npm run typecheck && npm run build && npm run test:unit
cd ui && CI=true npx playwright test --project=chromium
```

- [ ] **Step 4: Update the graph and inspect the final diff.**

```bash
cd /home/mint/hal0/.worktrees/prerelease-preview-pipeline
graphify update .
git status --short
git diff --check
git log --oneline --decorate -12
```

Generated Graphify/Shepherd files remain excluded from commits unless explicitly
required by the repository release policy.

- [ ] **Step 5: Commit verification-only documentation corrections, if any.**

```bash
git add docs/superpowers/specs/2026-07-22-prerelease-preview-pipeline-hardening-design.md
git commit -m "docs: record preview pipeline verification corrections"
```

Do not mark the release pipeline ready for publication unless all focused and full
checks are green and the external hal0-web preview-pointer contract is separately
verified.

---

## Final handoff gates

Before any release tag or publication:

1. PR #1330 has landed and the final incoming session is included.
2. A fresh `release/vX.Y.Z-alpha.N` branch exists from the intended merged commit.
3. `scripts/release-check.sh --channel preview --tag vX.Y.Z-alpha.N` passes in read-only mode.
4. Full Python, UI, Chromium/Gamma, updater, and release contract suites pass.
5. External hal0-web serves a signed, channel-correct `preview.json` contract.
6. The release action's workflow run is reviewed for exact tag checkout, explicit
GitHub prerelease/not-latest flags, immutable assets, PyPI verification, and no
premature pointer advancement.
7. Live preview install, update, rollback, and stable-channel isolation checks pass.
8. Any failed publication is replaced by a new immutable version; no existing
published prerelease is overwritten.
