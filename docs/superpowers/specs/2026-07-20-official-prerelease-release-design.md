# hal0 — Official prerelease release channel

> **Date:** 2026-07-20
> **Status:** Approved design
> **Scope:** Official alpha, beta, and release-candidate builds; signed GitHub
> releases; PyPI prereleases; installer/updater subscription; final-release
> promotion; explicit downgrade; and developer/editable-install identity.
> **Chosen approach:** One build/sign/publish pipeline driven by a deep,
> stdlib-only release-policy module.

---

## 1. Problem

hal0 currently has two release channels:

- `stable` for every non-nightly tag.
- `nightly` for scheduled snapshots.

The existing `channel_for_tag()` classifies alpha, beta, and RC tags as
`stable`. The release workflow consequently publishes them as normal/latest
GitHub releases and advances `stable.json`. Operators cannot opt into official
prereleases without also exposing stable subscribers to them.

The desired system must publish deliberate, numbered, immutable prereleases
without creating a second artifact pipeline or weakening the current signing
and updater safety model.

---

## 2. Decisions locked

1. Add one rolling **`preview`** channel.
2. Alpha, beta, and RC tags all advance `preview.json`.
3. Preview cuts are deliberate version tags, not scheduled builds.
4. Preview releases publish both signed GitHub assets and the `hal0ai` Python
   wheel to PyPI.
5. The UI remains bundled in the hal0 release artifact. `hal0-ui` stays private
   and is not published to npm.
6. A final release advances both `stable.json` and `preview.json`, moving preview
   subscribers onto GA automatically.
7. Official previews and their tags are immutable and retained permanently.
8. Preview releases support forward upgrades from the latest stable and prior
   preview. Rollback is governed explicitly by signed manifest policy.
9. Leaving preview never causes an implicit downgrade. Downgrade requires
   `--allow-downgrade` and confirmation.
10. Developer/editable installs share the preview base version while reporting
    checkout provenance separately. The updater never overwrites an editable
    checkout.
11. Nightly cadence, pruning, and behavior remain unchanged.

---

## 3. Ubiquitous language

| Term | Meaning |
| --- | --- |
| **Stable release** | A final `vX.Y.Z` build intended for production operators. |
| **Official preview** | A deliberately tagged, retained alpha, beta, or RC build with signed assets, release notes, updater metadata, and a PyPI prerelease. |
| **Nightly** | An automated snapshot identified by a timestamp and retained under the existing rolling policy. It is not an official preview. |
| **Preview channel** | The rolling opt-in channel containing the newest official preview, or the promoted final release when GA is newer. |
| **Release policy** | The complete set of publication decisions derived from an immutable tag. |
| **Release artifact** | The versioned hal0 runtime tarball, including `ui/dist`, plus its digest and signature assets. |
| **Channel manifest** | Release metadata served from `releases.hal0.dev/<channel>.json`. It is a movable, authenticated pointer to an immutable signed release artifact. |
| **Base version** | The comparable product version from `pyproject.toml`, such as `1.0.0-alpha.1`. |
| **Build identity** | Non-ordering provenance: install mode, checkout path, Git commit, branch, and dirty state. |
| **Promotion** | Publishing a final release and advancing both stable and preview channel manifests to that exact final artifact. |

---

## 4. Tag and version policy

### 4.1 Accepted tags

Official forms are:

```text
v1.0.0-alpha.0
v1.0.0-alpha.1
v1.0.0-beta.0
v1.0.0-rc.0
v1.0.0
v1.0.1-nightly.20260721060000
```

The policy rejects malformed or ambiguous variants. In particular, official
preview tags use a dot before the sequence number (`rc.1`, not `rc1`) so tags,
UI metadata, release titles, and operator documentation share one form.

### 4.2 Classification

| Tag | Kind | GitHub prerelease | GitHub latest | Manifest targets | PyPI |
| --- | --- | ---: | ---: | --- | ---: |
| `vX.Y.Z-alpha.N` | preview / alpha | yes | no | `preview` | yes |
| `vX.Y.Z-beta.N` | preview / beta | yes | no | `preview` | yes |
| `vX.Y.Z-rc.N` | preview / rc | yes | no | `preview` | yes |
| `vX.Y.Z-nightly.<stamp>` | nightly | yes | no | `nightly` | no |
| `vX.Y.Z` | stable | no | yes | `stable`, `preview` | yes |

GitHub does not define hal0's channel from tag text. The release-policy module
classifies the tag, and the workflow explicitly sets GitHub's `prerelease` and
`make_latest` behavior from that policy.

### 4.3 Python normalization

SemVer-style public versions remain canonical for tags, UI metadata, manifests,
and release notes. Python tooling normalizes prereleases to PEP 440:

```text
1.0.0-alpha.1 -> 1.0.0a1
1.0.0-beta.1  -> 1.0.0b1
1.0.0-rc.1    -> 1.0.0rc1
```

Release validation compares normalized versions. It does not require PyPI's
normalized spelling to match the Git tag byte-for-byte.

### 4.4 Version sources

`pyproject.toml` remains authoritative. The release preparation interface is:

```bash
python scripts/set-version.py 1.0.0-alpha.1
```

The command updates the literal version sources, refreshes `uv.lock`, and then
validates:

```text
pyproject.toml
uv.lock
ui/package.json
ui/package-lock.json
manifest.json
```

For `v1.0.0-alpha.1`, expected values are:

```text
pyproject.toml          1.0.0-alpha.1
uv.lock                 1.0.0a1
ui/package.json         1.0.0-alpha.1
ui/package-lock.json    1.0.0-alpha.1
manifest.json           1.0.0-alpha.1 / preview
```

Nightly retains its existing relaxed base-version match.

---

## 5. Release-policy module

Create `src/hal0/release/policy.py`. It must use only the Python standard
library so GitHub Actions can execute it before installing project dependencies.

Its external interface is intentionally small:

```python
policy = ReleasePolicy.from_tag("v1.0.0-alpha.1")
```

The immutable result includes:

```python
ReleasePolicy(
    tag="v1.0.0-alpha.1",
    version="1.0.0-alpha.1",
    python_version="1.0.0a1",
    kind="preview",
    prerelease_stage="alpha",
    manifest_targets=("preview",),
    github_prerelease=True,
    github_latest=False,
    publish_pypi=True,
    retain=True,
)
```

The module also provides a CLI that emits JSON or GitHub Actions outputs. The
workflow consumes this output rather than duplicating tag parsing in shell.
`src/hal0/release/channel.py`, release-note generation, release checks, and
runtime channel handling import the same policy logic.

Explicit workflow `channel` input may not contradict tag-derived policy.
Manual dispatch can request a tag, but cannot relabel an alpha as stable.

---

## 6. Release flow

### 6.1 Operator preflight

Before creating a tag:

```bash
./scripts/release-check.sh v1.0.0-alpha.1
```

The check verifies:

1. The tag is valid under release policy.
2. All version sources agree after normalization.
3. The target commit is current `origin/main`.
4. Required CI, sunset, UI, and Playwright checks succeeded for that commit.
5. The tag does not exist locally or remotely.
6. No GitHub Release or PyPI distribution already uses the version.
7. Release notes identify audience, known issues, migrations, and rollback
   policy.
8. Toolbox image pins and digests pass the existing release checks.

The maintainer then creates an annotated, signed tag:

```bash
git tag -s v1.0.0-alpha.1
git push origin v1.0.0-alpha.1
```

### 6.2 Build, sign, and publish

`.github/workflows/release.yml` remains the only artifact pipeline:

1. Resolve `ReleasePolicy` from the tag.
2. Repeat version, commit, checks, and tag-verification gates server-side.
3. Build `ui/dist`.
4. Stage the runtime tree and structured release notes.
5. Build the versioned tarball and Python wheel.
6. Calculate SHA-256 metadata.
7. Keyless-sign with the existing Cosign/OIDC identity.
8. Self-verify the Sigstore bundle and transition-window detached signature.
9. Create an immutable GitHub Release with explicit prerelease/latest flags.
10. Upload release assets.
11. Publish `hal0ai` to PyPI through trusted publishing when policy permits.
12. Generate and Cosign-sign each target channel manifest.
13. Download and verify the published GitHub and PyPI artifacts.
14. Advance the authenticated channel manifests last.

The versioned artifacts remain immutable. If any step before manifest
publication fails, clients continue seeing the previous channel target.

### 6.3 GitHub publication

Preview and nightly releases set:

```text
prerelease = true
make_latest = false
```

Final releases set:

```text
prerelease = false
make_latest = true
```

The workflow must verify the resulting GitHub Release fields after creation.
It may not rely on GitHub's automatic semantic-version selection.

### 6.4 PyPI publication

Official previews publish the `hal0ai` wheel. Because pip ignores prereleases by
default, users opt in explicitly:

```bash
python -m pip install --pre hal0ai
python -m pip install hal0ai==1.0.0a1
```

Nightly does not publish to PyPI. A failed release is replaced with a new version;
published versions are never deleted and reused.

### 6.5 Final promotion

Publishing `v1.0.0` creates a new final artifact and:

- Publishes a normal/latest GitHub Release.
- Publishes `hal0ai==1.0.0` to PyPI.
- Writes the same final artifact metadata to `stable.json` and `preview.json`.

Preview subscribers therefore move from RC to final without changing channels.
The next `v1.1.0-alpha.0` advances preview again while stable stays on `v1.0.0`.

---

## 7. Channel manifests and endpoint

Bootstrap and updater use one channel endpoint family:

```text
https://releases.hal0.dev/stable.json
https://releases.hal0.dev/preview.json
https://releases.hal0.dev/nightly.json
```

`installer/bootstrap.sh` must stop using GitHub's
`/releases/latest/download/<channel>.json`, because GitHub deliberately does not
make official prereleases latest.

The existing `hal0.releases.v1` manifest is extended additively. Current clients
preserve unknown fields (`extra = "allow"`), so these additions do not break
stable or nightly consumers:

```json
{
  "_schema": "hal0.releases.v1",
  "version": "1.0.0-alpha.1",
  "channel": "preview",
  "release_kind": "preview",
  "prerelease_stage": "alpha",
  "rollback_policy": "safe",
  "upgrade_from": ">=0.9.8",
  "operator_migrations": []
}
```

Each channel manifest is Cosign-signed by the same release workflow identity and
served with `<channel>.json.bundle`. New clients verify the manifest bundle before
using channel, rollback, migration, URL, or digest policy. Artifact verification
remains mandatory as a separate check; trusting the manifest does not replace
verification of the downloaded tarball.

The channel endpoint serves both files:

```text
preview.json
preview.json.bundle
```

Preview installation hard-requires Cosign verification of both manifest and
artifact; it has no SHA-only fallback. Stable bootstrap compatibility remains
unchanged in this scope. Editable installs do not consume release artifacts and
therefore do not cross this bootstrap verification path.

Legacy stable/nightly clients continue consuming the additive v1 JSON during the
transition. The new `preview` channel requires a client version that supports
manifest-bundle verification, so no legacy client silently opts into policy it
cannot authenticate.

Allowed rollback policies are:

- `safe`: explicit downgrade is supported.
- `backup-required`: installation must create and record a backup first.
- `blocked`: downgrade is refused because state may not be backward-readable.

The hal0-web channel resolver must recognize preview GitHub prereleases and
serve the newest successfully verified `preview.json`. This is a coordinated
cross-repository change and must deploy before the first preview tag.

---

## 8. Installer and updater behavior

### 8.1 Install preview

```bash
curl -fsSL https://hal0.dev/install.sh |
  sudo env HAL0_CHANNEL=preview bash
```

`preview` is added to every channel enum and validator in bootstrap, config,
API, CLI, updater, and dashboard surfaces.

### 8.2 Subscribe and update

```bash
hal0 update --channel preview --check
hal0 update --channel preview
```

The selected channel is persisted only after its manifest is fetched and
validated. Stable clients never inspect `preview.json` and therefore cannot be
offered a prerelease.

### 8.3 Leave preview

Changing channels alone never downgrades:

```bash
hal0 update --channel stable
```

If stable is older than the installed preview, the command reports the target
without applying it. An explicit downgrade is:

```bash
hal0 update --channel stable --allow-downgrade
```

The updater:

1. Fetches and verifies stable metadata.
2. Reads the locally persisted, previously verified preview manifest from
   `/var/lib/hal0/releases/installed.json`.
3. Refuses a blocked rollback.
4. Verifies required backup state for `backup-required`.
5. Displays current/target versions, service impact, and backup status.
6. Requires confirmation unless `--yes` is present.
7. Uses the existing atomic tree and symlink swap.
8. Runs health verification.
9. Persists the target manifest and `stable` channel only after success.

Automatic destructive migrations remain forbidden. `operator_migrations`
lists deliberate commands, but installation never invokes them. A release with
a non-reversible operator migration must declare rollback `blocked`. A reversible
migration may declare `backup-required` only when it records an identifiable
backup that the updater can verify before downgrade.

---

## 9. Developer and editable installs

### 9.1 Supported workflows

The existing installer remains the entry point:

```bash
git clone https://github.com/Hal0ai/hal0.git
cd hal0
git checkout v1.0.0-alpha.1
bash installer/install.sh --dev
```

Active development can track a branch instead:

```bash
git switch rework
bash installer/install.sh --dev
```

`--dev` keeps its isolated `.hal0ai/` prefix and `pip install -e .` behavior.
An editable install uses the base version from `pyproject.toml`.

### 9.2 Version versus build identity

Comparable release version remains clean:

```text
version = 1.0.0-alpha.1
```

Checkout provenance is reported separately:

```json
{
  "version": "1.0.0-alpha.1",
  "install_mode": "editable",
  "git_commit": "e21d03d8",
  "git_branch": "rework",
  "git_dirty": true,
  "editable_path": "/home/mint/hal0"
}
```

Build identity uses these install modes:

- `release`: a signed release/FHS installation.
- `editable`: a PEP 610 editable checkout.
- `git-fhs`: a non-editable FHS installation sourced from Git.
- `source`: a raw import with no installed distribution metadata.

Expose build identity through:

- `hal0 system-info` and its JSON form; `hal0 --version` remains a stable,
  single-line base-version interface.
- `/api/health`.
- `/api/status`.
- `/api/updates/state`.
- Doctor/support bundles.
- Dashboard About and Updates views.

Git SHA, branch, and dirty state are never appended to the package version.
Local version segments such as `+git.<sha>` must not affect updater ordering.

### 9.3 Editable updater safety

Editable installs may perform read-only checks:

```bash
hal0 update --channel preview --check
```

The response identifies the checkout as behind, equal to, or ahead of the
official preview base version.

Editable installs continue to hard-refuse apply, commit, rollback, and
`--allow-downgrade`. The updater may not replace developer files. Its error
provides the safe workflow:

```bash
git fetch --tags
git checkout v1.0.0-beta.0
pip install -e .
npm --prefix ui run build
```

PEP 610 `direct_url.json` remains the authoritative editable-install detector.
Detection should query the canonical distribution name `hal0ai` first and keep
the transitional `hal0` fallback.

---

## 10. Release notes contract

Every official preview includes:

1. Stage and stability warning.
2. Intended testing audience.
3. Changes since the prior official preview.
4. Known issues.
5. Supported upgrade sources.
6. Operator-run migrations.
7. Rollback policy and stable rollback target.
8. Artifact digest and Cosign verification instructions.

Release-note generation accepts `preview` and renders stage-specific headings.
Missing required preview sections fail release preflight.

---

## 11. Security and repository controls

1. Add a GitHub tag ruleset for `v*`; only release maintainers or approved
   automation may create release tags.
2. Require annotated, signed tags for stable and official preview releases.
3. Verify GitHub's tag signature status and target commit in the release job.
4. Require successful CI and Playwright checks for the tagged commit.
5. Use a protected GitHub `release` environment for PyPI trusted publishing.
6. Keep workflow permissions minimal: `contents: write`, `id-token: write`, and
   required read permissions only.
7. Preserve existing Cosign keyless identity, Sigstore bundle, detached
   transition assets, SHA-256 checks, and self-verification.
8. Never overwrite or reuse a Git tag, GitHub Release version, or PyPI version.
9. Advance an authenticated channel manifest only after published artifacts
   verify.

---

## 12. Testing strategy

### 12.1 Release-policy unit tests

Cover the full matrix:

```text
alpha/beta/rc -> preview, prerelease, not latest, PyPI
nightly       -> nightly, prerelease, not latest, no PyPI
final         -> stable + preview, latest, PyPI
invalid tag   -> hard failure
```

Also cover sequence parsing, base-version matching, Python normalization,
manual-input contradiction, and malformed tags.

### 12.2 Manifest and updater tests

Test:

- Stable never recommends a prerelease.
- Preview advances alpha -> beta -> RC -> final.
- Final manifest data is identical across stable and preview targets.
- Revoked releases are never recommended.
- `safe`, `backup-required`, and `blocked` rollback paths.
- Channel changes do not imply downgrade.
- `--allow-downgrade` confirmation and atomic failure recovery.
- Bootstrap and updater resolve the same channel endpoint.
- Bootstrap and updater verify `<channel>.json.bundle` before using manifest
  policy.
- Channel persistence occurs only after successful validation/apply.

### 12.3 Editable-install tests

Test:

- `--dev` reports the `pyproject.toml` preview base version.
- Build identity reports editable path, commit, branch, and dirty state.
- Git provenance never changes version ordering.
- Editable preview checks are read-only.
- Apply, commit, rollback, and downgrade hard-refuse in editable mode.
- Refusal output contains the safe Git/editable update sequence.
- Wheel/FHS installs retain normal atomic updater behavior.

### 12.4 Workflow and publication tests

Test or dry-run:

- Version-source synchronization.
- Required-check lookup for the tagged commit.
- Tag verification and immutable-version collision checks.
- GitHub prerelease/latest fields after publication.
- PyPI publication enabled for preview and stable, disabled for nightly.
- Channel manifests are Cosign-signed and advance only after remote
  verification.
- Existing nightly scheduling and pruning remain unchanged.

---

## 13. Expected files and repositories

### hal0

Likely additions and edits:

```text
src/hal0/release/policy.py
src/hal0/release/channel.py
src/hal0/release/notes.py
src/hal0/updater/updater.py
src/hal0/api/routes/updater.py
src/hal0/api/routes/health.py
src/hal0/cli/update_commands.py
src/hal0/cli/main.py
installer/bootstrap.sh
installer/install.sh
scripts/release-check.sh
scripts/set-version.py
scripts/gen_release_notes.py
.github/workflows/release.yml
.github/workflows/nightly.yml
pyproject.toml
uv.lock
ui/package.json
ui/package-lock.json
manifest.json
docs/internal/release-manifest.md
```

Tests follow existing release, updater, installer, API, CLI, and UI test
locations.

### hal0-web

The channel middleware must add `preview.json` resolution and must select
GitHub prereleases by verified channel manifest rather than GitHub's `latest`
release pointer.

The hal0-web deployment is a prerequisite for the first official preview tag.

---

## 14. Rollout plan

1. Add and test release policy without changing publication behavior.
2. Add preview channel parsing, manifest fields, and client validators.
3. Add build identity and editable read-only preview checks.
4. Add explicit downgrade policy and tests.
5. Update bootstrap to use `releases.hal0.dev` for all channels.
6. Deploy hal0-web preview resolution.
7. Update release workflow and PyPI trusted-publisher environment.
8. Dry-run policy and artifacts without advancing manifests.
9. Cut `v1.0.0-alpha.1` as the first official preview.
10. Verify GitHub fields, Cosign, PyPI, preview install, update, and editable
    behavior from clean hosts.

---

## 15. Risks and mitigations

- **Preview leaks to stable:** separate manifests; stable policy cannot target
  preview versions; enforce with matrix tests.
- **GitHub marks preview latest:** set explicit API fields and verify them after
  publication.
- **Channel pointer advances after partial publication:** verify artifacts first;
  sign and advance the manifest last.
- **Manifest policy is tampered with or replayed:** verify its Cosign bundle and
  enforce channel/version monotonicity before using policy.
- **Raw SemVer and PEP 440 mismatch:** use one policy parser and normalized
  comparisons.
- **Editable checkout mistaken for an official artifact:** report separate build
  identity and a visible `install_mode`.
- **Updater overwrites developer files:** preserve the PEP 610 hard refusal for
  every mutating updater operation.
- **Unsafe downgrade corrupts state:** require signed rollback policy, an explicit
  flag, confirmation, backup gate, and health rollback.
- **hal0 and hal0-web deploy out of order:** deploy channel resolution before the
  first preview tag.
- **Release policy is duplicated in YAML and runtime:** consume one stdlib-only
  policy module everywhere.
- **Nightly regresses:** preserve existing nightly policy and run parity tests
  before enabling preview.

---

## 16. Acceptance criteria

The design is implemented when all of the following are demonstrated:

1. `v1.0.0-alpha.1` creates an immutable signed GitHub prerelease with
   `make_latest=false`.
2. The same cut publishes `hal0ai==1.0.0a1` to PyPI and does not publish npm.
3. `preview.json` and `preview.json.bundle` advance only after GitHub and PyPI
   artifacts verify; clients hard-require manifest and artifact signatures for
   preview installation.
4. Stable subscribers are not offered the alpha.
5. Preview subscribers receive alpha -> beta -> RC -> final in order.
6. `v1.0.0` advances both stable and preview to the same final artifact.
7. Preview installation and subscription commands work from clean hosts.
8. Explicit safe downgrade works; blocked downgrade refuses before mutation.
9. A tagged editable install reports the preview base plus editable provenance.
10. Editable installs can check preview but cannot apply or downgrade through the
    updater.
11. Release notes expose audience, known issues, migrations, and rollback policy.
12. Nightly scheduling, signing, manifest publication, and pruning remain
    unchanged.

---

## 17. Out of scope

- Publishing `hal0-ui` as an npm package.
- Replacing GitHub Releases or PyPI with another registry.
- Automatically creating official preview tags from `main` or a schedule.
- Automatically running destructive operator migrations.
- Treating nightly builds as official previews.
- Adding Git-derived local segments to the comparable package version.
- Guaranteeing rollback when a release manifest declares it blocked.
