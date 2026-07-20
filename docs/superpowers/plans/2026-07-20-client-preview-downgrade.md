# Client Preview Channel and Explicit Downgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `preview` channel end-to-end in installer, bootstrap, config, API, CLI, updater, and dashboard; expose explicit downgrade with rollback policy and persisted installed manifests.

**Architecture:** Add `preview` to every channel validator. Authenticate manifest and artifact signatures before accepting policy. Persist verified manifests locally to support rollback policy and downgrade decisions. Reuse existing updater transaction but require explicit flag and confirmation for downgrade.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, Bash, Hal0 web UI, Cosign client.

## Global Constraints

- Design source: `docs/superpowers/specs/2026-07-20-official-prerelease-release-design.md` (`c8bd3999`).
- Stable clients must never be offered preview versions; preview clients must consume only verified manifests.
- Rollback policy and persisted manifests are part of the manifest signature; no client computes them locally.
- Downgrade is opt-in: `--allow-downgrade` flag plus interactive confirmation unless `--yes` is supplied.
- Editable installs hard-refuse all mutating operations; behavior unchanged in this plan (covered by Plan 3).

---

## File Structure

**Modify:**

- `installer/bootstrap.sh` — switch to `releases.hal0.dev/<channel>.json`, set channel defaults.
- `installer/install.sh` — accept `HAL0_CHANNEL=preview` and persist.
- `src/hal0/config/schema.py` — preview channel and persisted installed manifest fields.
- `src/hal0/cli/update_commands.py` — `--allow-downgrade`, preview channel option.
- `src/hal0/api/routes/updater.py` — preview channel PUT/GET, downgrade flow, persisted installed manifest state.
- `src/hal0/updater/updater.py` — manifest bundle verification, persisted installed manifest, rollback policy enforcement.
- `ui/src/dash/settings/pages/diagnostics/UpdatesPage.jsx` — surface preview status, installed manifest, downgrade button.
- `tests/updater/test_updater.py`, `tests/api/test_updater_routes.py`, `tests/cli/test_update_commands.py` — channel and downgrade tests.
- `docs/hal0-install-migration-guide.html` — preview install/update/downgrade commands.

---

### Task 1: Channel validation across the surface

**Files:**

- Modify: `src/hal0/config/schema.py`
- Modify: `src/hal0/cli/update_commands.py:54`
- Modify: `src/hal0/api/routes/updater.py:58`
- Modify: `installer/bootstrap.sh`, `installer/install.sh`
- Modify: `tests/cli/test_update_commands.py`, `tests/api/test_updater_routes.py`

**Interfaces:**

- Produces `Channel = Literal["stable", "preview", "nightly"]` exported from `src/hal0/release/policy.py` and imported by all modules.
- Produces persistent config field `telemetry.channel` accepting the new value.

- [ ] **Step 1: Failing test: preview channel is accepted at the API and CLI**

```python
def test_set_channel_preview(api_client, seeded_config):
    response = api_client.put("/api/updates/channel", json={"channel": "preview"})
    assert response.status_code == 200
    assert response.json()["channel"] == "preview"
```

Extend to CLI: `hal0 update --channel preview --check` returns a preview manifest pointer.

- [ ] **Step 2: Run tests and confirm RED**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/api/test_updater_routes.py tests/cli/test_update_commands.py -q
```

Expected: `channel.unknown` for preview; CLI errors with `invalid value`.

- [ ] **Step 3: Centralize the channel enum**

Add a `Channel` literal in `src/hal0/release/policy.py` (reused by `schema.py`, `update_commands.py`, `routes/updater.py`) and helper `is_channel(value)` returning `value in {"stable", "preview", "nightly"}`. Replace every `_VALID_CHANNELS` and `UpdateChannel` literal in repo with this shared definition. Migrate existing test fixtures by adding `preview` to boundary cases.

- [ ] **Step 4: Update bootstrap and installer defaults**

`installer/bootstrap.sh` must consume `HAL0_CHANNEL` (default `stable`) for both signing fetch and manifest fetch:

```bash
CHANNEL="${HAL0_CHANNEL:-stable}"
MANIFEST="${HAL0_RELEASES_URL:-https://releases.hal0.dev/${CHANNEL}.json}"
```

`installer/install.sh` writes `telemetry.channel` into `hal0.toml` so the running API can persist it; preview must require cosign-verified manifest before persisting.

- [ ] **Step 5: Verify**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/api/test_updater_routes.py tests/cli/test_update_commands.py -q
bash -n installer/bootstrap.sh installer/install.sh
```

Expected: tests pass, bash scripts parse cleanly.

- [ ] **Step 6: Commit**

```bash
git add src/hal0/release/policy.py src/hal0/config/schema.py \
  src/hal0/cli/update_commands.py src/hal0/api/routes/updater.py \
  installer/bootstrap.sh installer/install.sh \
  tests/api/test_updater_routes.py tests/cli/test_update_commands.py
git commit -m "feat(update): add preview channel to install, API, and CLI"
```

---

### Task 2: Verified manifest bootstrap

**Files:**

- Modify: `src/hal0/updater/updater.py:269-302`
- Modify: `tests/updater/test_updater.py`

**Interfaces:**

- Produces `fetch_release_manifest(channel) -> ReleaseManifest | None` that verifies `<channel>.json.bundle` whenever the bundle is available and the API permits.
- Persists the verified manifest to `paths.var_lib()/releases/installed.json` after apply success.

- [ ] **Step 1: Failing test: bootstrap fetches the bundle and rejects tampered manifests**

```python
def test_fetch_verifies_bundle(tmp_path, monkeypatch, signed_manifest):
    set_releases_url(tmp_path)
    write_signed_bundle(tmp_path, "preview", tampered=True)
    monkeypatch.setattr("hal0.updater.updater._verify_cosign", lambda *_: False)
    assert Updater(channel="preview").check() is None
```

Assert preview/deviation payload contains only documented fields; stable clients continue to skip bundle verification.

- [ ] **Step 2: Run tests and confirm RED**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/updater/test_updater.py -k bundle -q
```

Expected: bundle fetch/verify surface absent.

- [ ] **Step 3: Implement bundle verification**

Read `<channel>.json.bundle` next to the manifest URL, attempt `cosign verify-blob --bundle <bundle> --certificate-identity-regexp ... --certificate-oidc-issuer ... <manifest>`. Use existing `_verify_cosign()` for both manifest and tarball; only skip when env says so or when the bundle is absent and the channel is `stable`.

- [ ] **Step 4: Persist verified manifests**

After `Updater.apply()` succeeds and after a successful `hal0 update --check` for any channel, write the verified manifest JSON to `paths.var_lib() / "releases" / "installed.json"`. The format mirrors the release manifest with one extra field `installed_at`. Expose `load_installed_manifest()` for rollback policy reads.

- [ ] **Step 5: Verify**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/updater/test_updater.py -q
uv run ruff check src/hal0/updater/updater.py tests/updater/
```

Expected: tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/hal0/updater/updater.py tests/updater/test_updater.py
git commit -m "feat(updater): verify manifest bundle and persist installed manifest"
```

---

### Task 3: Explicit downgrade with rollback policy

**Files:**

- Modify: `src/hal0/cli/update_commands.py`
- Modify: `src/hal0/api/routes/updater.py`
- Modify: `src/hal0/updater/updater.py`
- Modify: `tests/cli/test_update_commands.py`, `tests/api/test_updater_routes.py`, `tests/updater/test_updater.py`

**Interfaces:**

- Adds CLI flag `--allow-downgrade` to `hal0 update`.
- Adds API endpoint `POST /api/updates/downgrade` with body `{"channel": "stable", "allow_downgrade": true, "yes": false}`.
- Adds `UpdateRollbackRefused` error variant surfaced with HTTP 409 and CLI exit 2.

- [ ] **Step 1: Failing tests**

```python
def test_downgrade_refuses_blocked_rollback(api_client, persisted_preview, monkeypatch):
    persisted_preview["rollback_policy"] = "blocked"
    response = api_client.post("/api/updates/downgrade", json={"channel": "stable", "allow_downgrade": True})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "system.update_rollback_blocked"
```

Cover `backup-required` (requires `/var/lib/hal0/backups/hal0-<ts>`), `safe` (downgrade after confirmation), and editable-install refusal.

- [ ] **Step 2: Run tests and confirm RED**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/api/test_updater_routes.py tests/cli/test_update_commands.py -k downgrade -q
```

Expected: missing route, missing CLI flag, missing rollback policy enforcement.

- [ ] **Step 3: Implement `Updater.downgrade_to(target_channel)`**

`Updater.downgrade_to()` performs:

1. Read installed manifest. Refuse if absent or `rollback_policy == "blocked"`.
2. For `backup_required`, refuse unless a backup under `/var/lib/hal0/backups/hal0-*` exists with `mtime` after the installed manifest's `installed_at`.
3. Fetch and verify target manifest. Compare versions. Refuse if not older.
4. Confirm in CLI/UI unless `yes`.
5. Run the existing atomic apply flow but with target manifest. On success, persist target manifest.

- [ ] **Step 4: Wire CLI/API**

CLI accepts `--allow-downgrade`. API endpoint returns 200 on success, 409 for blocked, 412 for missing backup, 422 for monotonic violation. The updater's error envelope reports the same JSON shape already used in the routes.

- [ ] **Step 5: Verify**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/api/test_updater_routes.py tests/cli/test_update_commands.py \
  tests/updater/test_updater.py -k 'downgrade or rollback' -q
uv run ruff check src/hal0/
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/hal0/cli/update_commands.py src/hal0/api/routes/updater.py \
  src/hal0/updater/updater.py tests/cli/test_update_commands.py \
  tests/api/test_updater_routes.py tests/updater/test_updater.py
git commit -m "feat(updater): explicit downgrade honoring rollback policy"
```

---

### Task 4: UI surfaces and operator documentation

**Files:**

- Modify: `ui/src/dash/settings/pages/diagnostics/UpdatesPage.jsx`
- Modify: `docs/hal0-install-migration-guide.html`

- [ ] **Step 1: Update the dashboard Updates page**

Use `useUpdates` to render channel and installed manifest:

```jsx
<span className="pill">{channel}</span>
{installed.installed_at && <span>installed {installed.installed_at}</span>}
```

Add a "Switch channel" combobox with the channel list. Add a "Downgrade" button when preview is installed and downgrade is enabled. Display rollback policy inline and disable the button when blocked.

- [ ] **Step 2: Update the migration guide**

Add an `Install preview` section to `docs/hal0-install-migration-guide.html` after the current `Migrations` block:

```text
## Install preview (v1.0.0-alpha.1)

curl -fsSL https://hal0.dev/install.sh |
  sudo env HAL0_CHANNEL=preview bash

hal0 update --channel preview --check
hal0 update --channel stable --allow-downgrade
```

Use the same style (cards/notes) as the existing `Migrations` section.

- [ ] **Step 3: Verify**

```bash
cd ui && npm exec eslint -- src/dash/settings/pages/diagnostics/UpdatesPage.jsx
cd ui && npm run typecheck
git diff --check -- docs/hal0-install-migration-guide.html ui/src/dash/settings/pages/diagnostics/UpdatesPage.jsx
```

- [ ] **Step 4: Commit**

```bash
git add ui/src/dash/settings/pages/diagnostics/UpdatesPage.jsx \
  docs/hal0-install-migration-guide.html
git commit -m "feat(ui/docs): surface preview channel and explicit downgrade"
```
