# Official Prerelease Release Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify immutable alpha/beta/RC tags as official previews and publish one built-and-signed artifact to GitHub, PyPI, and authenticated channel manifests without affecting stable subscribers.

**Architecture:** A stdlib-only `ReleasePolicy` module is the single seam for tag classification and publication decisions. The existing `release.yml` remains the only build/sign/publish implementation; scripts and runtime channel helpers consume the same policy. Channel manifests are generated and Cosign-signed only after remote artifacts verify.

**Tech Stack:** Python 3.12+ stdlib, pytest, Hatchling/uv, Bash, GitHub Actions, GitHub CLI/API, Cosign/Sigstore, PyPI trusted publishing.

## Global Constraints

- Design source: `docs/superpowers/specs/2026-07-20-official-prerelease-release-design.md` (`c8bd3999`).
- Official tags are exactly `vX.Y.Z-alpha.N`, `vX.Y.Z-beta.N`, `vX.Y.Z-rc.N`, or `vX.Y.Z`; malformed variants fail closed.
- Nightly format and pruning remain `vX.Y.Z-nightly.YYYYMMDDHHMMSS` and are not published to PyPI.
- Preview/final GitHub and PyPI versions are immutable and never reused.
- `pyproject.toml` is authoritative; public SemVer spelling is normalized only at the Python packaging seam.
- `hal0-ui` stays private and is never published to npm.
- Channel manifests advance only after GitHub and PyPI artifacts verify.
- Every task follows failing test → minimal implementation → passing test → commit.
- Use an isolated worktree when executing; the current checkout may contain unrelated seeded-profile work.

---

## File Structure

**Create:**

- `src/hal0/release/policy.py` — stdlib-only tag parser and immutable publication policy.
- `tests/release/test_policy.py` — complete tag/policy matrix.
- `scripts/set-version.py` — synchronize release version literals and refresh `uv.lock`.
- `tests/scripts/test_set_version.py` — hermetic version synchronization tests.

**Modify:**

- `src/hal0/release/channel.py` — compatibility adapters over `ReleasePolicy`.
- `tests/release/test_channel.py` — preview and nightly channel tests.
- `scripts/release-check.sh` — invoke policy and validate source/remote collisions/checks.
- `scripts/gen_release_notes.py` — accept preview and require preview note sections.
- `src/hal0/release/notes.py` — expose preview stage metadata.
- `src/hal0/updater/updater.py` — additive manifest policy fields only; client behavior is Plan 2.
- `tests/updater/test_updater.py` — manifest parsing compatibility.
- `.github/workflows/release.yml` — consume policy, publish explicit GitHub fields, PyPI, and signed manifests.
- `.github/workflows/nightly.yml` — pass only a tag; policy derives nightly.
- `docs/internal/release-manifest.md` — preview and manifest-bundle contract.

---

### Task 1: Deep release-policy module

**Files:**

- Create: `src/hal0/release/policy.py`
- Create: `tests/release/test_policy.py`
- Modify: `src/hal0/release/channel.py`
- Modify: `tests/release/test_channel.py`

**Interfaces:**

- Produces: `ReleasePolicy.from_tag(tag: str) -> ReleasePolicy`
- Produces: `ReleasePolicy.to_github_outputs() -> dict[str, str]`
- Preserves: `channel_for_tag(tag: str) -> str`, `base_matches(pyproject_version: str, tag: str) -> bool`

- [ ] **Step 1: Write the failing policy matrix tests**

Create `tests/release/test_policy.py`:

```python
from __future__ import annotations

import pytest

from hal0.release.policy import ReleasePolicy, ReleaseTagError


@pytest.mark.parametrize(
    ("tag", "kind", "stage", "targets", "prerelease", "latest", "pypi", "python_version"),
    [
        ("v1.0.0-alpha.0", "preview", "alpha", ("preview",), True, False, True, "1.0.0a0"),
        ("v1.0.0-beta.2", "preview", "beta", ("preview",), True, False, True, "1.0.0b2"),
        ("v1.0.0-rc.1", "preview", "rc", ("preview",), True, False, True, "1.0.0rc1"),
        ("v1.0.0", "stable", None, ("stable", "preview"), False, True, True, "1.0.0"),
        (
            "v1.0.1-nightly.20260721060000",
            "nightly",
            None,
            ("nightly",),
            True,
            False,
            False,
            None,
        ),
    ],
)
def test_policy_matrix(
    tag: str,
    kind: str,
    stage: str | None,
    targets: tuple[str, ...],
    prerelease: bool,
    latest: bool,
    pypi: bool,
    python_version: str | None,
) -> None:
    policy = ReleasePolicy.from_tag(tag)
    assert policy.kind == kind
    assert policy.prerelease_stage == stage
    assert policy.manifest_targets == targets
    assert policy.github_prerelease is prerelease
    assert policy.github_latest is latest
    assert policy.publish_pypi is pypi
    assert policy.python_version == python_version


@pytest.mark.parametrize(
    "tag",
    [
        "1.0.0-alpha.1",
        "v1.0.0-alpha1",
        "v1.0.0-rc1",
        "v1.0.0-preview.1",
        "v1.0",
        "v1.0.0-alpha.-1",
        "v1.0.0-nightly.20260721",
    ],
)
def test_invalid_tags_fail_closed(tag: str) -> None:
    with pytest.raises(ReleaseTagError):
        ReleasePolicy.from_tag(tag)


def test_github_outputs_are_strings() -> None:
    outputs = ReleasePolicy.from_tag("v1.0.0-alpha.1").to_github_outputs()
    assert outputs == {
        "tag": "v1.0.0-alpha.1",
        "version": "1.0.0-alpha.1",
        "python_version": "1.0.0a1",
        "base_version": "1.0.0",
        "kind": "preview",
        "prerelease_stage": "alpha",
        "manifest_targets": "preview",
        "github_prerelease": "true",
        "github_latest": "false",
        "publish_pypi": "true",
        "retain": "true",
    }
```

- [ ] **Step 2: Run the policy tests and confirm RED**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/release/test_policy.py -q
```

Expected: collection fails because `hal0.release.policy` does not exist.

- [ ] **Step 3: Implement the stdlib-only policy**

Create `src/hal0/release/policy.py` with frozen dataclasses, anchored regular expressions, and a `__main__` CLI. The implementation must use these public types and fields:

```python
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from typing import Literal

ReleaseKind = Literal["stable", "preview", "nightly"]
PreviewStage = Literal["alpha", "beta", "rc"]

_PREVIEW = re.compile(
    r"^v(?P<base>\d+\.\d+\.\d+)-(?P<stage>alpha|beta|rc)\.(?P<seq>0|[1-9]\d*)$"
)
_FINAL = re.compile(r"^v(?P<base>\d+\.\d+\.\d+)$")
_NIGHTLY = re.compile(
    r"^v(?P<base>\d+\.\d+\.\d+)-nightly\.(?P<stamp>\d{14})$"
)


class ReleaseTagError(ValueError):
    pass


@dataclass(frozen=True)
class ReleasePolicy:
    tag: str
    base_version: str
    version: str
    python_version: str | None
    kind: ReleaseKind
    prerelease_stage: PreviewStage | None
    manifest_targets: tuple[str, ...]
    github_prerelease: bool
    github_latest: bool
    publish_pypi: bool
    retain: bool

    @classmethod
    def from_tag(cls, tag: str) -> "ReleasePolicy":
        if match := _PREVIEW.fullmatch(tag):
            stage = match.group("stage")
            seq = match.group("seq")
            marker = {"alpha": "a", "beta": "b", "rc": "rc"}[stage]
            return cls(
                tag=tag,
                base_version=match.group("base"),
                version=tag[1:],
                python_version=f"{match.group('base')}{marker}{seq}",
                kind="preview",
                prerelease_stage=stage,  # type: ignore[arg-type]
                manifest_targets=("preview",),
                github_prerelease=True,
                github_latest=False,
                publish_pypi=True,
                retain=True,
            )
        if match := _FINAL.fullmatch(tag):
            version = match.group("base")
            return cls(
                tag=tag,
                base_version=version,
                version=version,
                python_version=version,
                kind="stable",
                prerelease_stage=None,
                manifest_targets=("stable", "preview"),
                github_prerelease=False,
                github_latest=True,
                publish_pypi=True,
                retain=True,
            )
        if match := _NIGHTLY.fullmatch(tag):
            return cls(
                tag=tag,
                base_version=match.group("base"),
                version=tag[1:],
                python_version=None,
                kind="nightly",
                prerelease_stage=None,
                manifest_targets=("nightly",),
                github_prerelease=True,
                github_latest=False,
                publish_pypi=False,
                retain=False,
            )
        raise ReleaseTagError(f"unsupported release tag: {tag!r}")

    def to_github_outputs(self) -> dict[str, str]:
        return {
            "tag": self.tag,
            "base_version": self.base_version,
            "version": self.version,
            "python_version": self.python_version or "",
            "kind": self.kind,
            "prerelease_stage": self.prerelease_stage or "",
            "manifest_targets": ",".join(self.manifest_targets),
            "github_prerelease": str(self.github_prerelease).lower(),
            "github_latest": str(self.github_latest).lower(),
            "publish_pypi": str(self.publish_pypi).lower(),
            "retain": str(self.retain).lower(),
        }
```

The CLI accepts `TAG`, `--format json|github`, prints JSON with `asdict(policy)`, or writes `key=value` lines for GitHub output.

- [ ] **Step 4: Make `channel.py` delegate to policy**

Replace tag classification in `channel_for_tag()` with:

```python
from hal0.release.policy import ReleasePolicy


def channel_for_tag(tag: str) -> str:
    return ReleasePolicy.from_tag(tag).kind
```

Keep `base_matches()` for nightly and implement it as
`ReleasePolicy.from_tag(tag).base_version == normalized_release_base(pyproject_version)`.
Extend `tests/release/test_channel.py` to assert alpha/beta/RC return `preview`.

- [ ] **Step 5: Run focused tests and static checks**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest \
  tests/release/test_policy.py tests/release/test_channel.py -q
uv run ruff check src/hal0/release/policy.py src/hal0/release/channel.py \
  tests/release/test_policy.py tests/release/test_channel.py
uv run mypy src/hal0/release/policy.py src/hal0/release/channel.py
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/hal0/release/policy.py src/hal0/release/channel.py \
  tests/release/test_policy.py tests/release/test_channel.py
git commit -m "feat(release): derive stable preview and nightly policy from tags"
```

---

### Task 2: Atomic version synchronization and release preflight

**Files:**

- Create: `scripts/set-version.py`
- Create: `tests/scripts/test_set_version.py`
- Modify: `scripts/release-check.sh`

**Interfaces:**

- Consumes: `ReleasePolicy.from_tag()` from Task 1.
- Produces: `set_version(root: Path, version: str) -> None`.
- Produces CLI: `python scripts/set-version.py VERSION`.

- [ ] **Step 1: Write failing hermetic synchronization tests**

Use `importlib.util.spec_from_file_location()` in the test to load the hyphenated
`scripts/set-version.py` file as module name `set_version_script`. Create temporary copies of `pyproject.toml`, `uv.lock`, `ui/package.json`, `ui/package-lock.json`, and `manifest.json`. Assert `set_version()` writes SemVer to all public files, PEP 440 to the `hal0ai` lock package, and the policy-derived channel to `manifest.json`. Include one test that injects a missing/duplicate version field and asserts the script raises without replacing any file.

Use this core assertion:

```python
set_version(tmp_path, "1.0.0-alpha.2")
assert tomllib.loads((tmp_path / "pyproject.toml").read_text())["project"]["version"] == "1.0.0-alpha.2"
assert json.loads((tmp_path / "ui/package.json").read_text())["version"] == "1.0.0-alpha.2"
assert json.loads((tmp_path / "manifest.json").read_text()) == {
    "version": "1.0.0-alpha.2",
    "channel": "preview",
}
```

- [ ] **Step 2: Run tests and confirm RED**

```bash
uv run pytest --confcutdir=tests/scripts tests/scripts/test_set_version.py -q
```

Expected: import failure for `scripts.set_version`.

- [ ] **Step 3: Implement atomic synchronization**

Implement `scripts/set-version.py` with stdlib `json`, `tomllib`, `tempfile`, and `os.replace`. Parse the requested version through `ReleasePolicy.from_tag(f"v{version}")`; reject nightly because nightly does not rewrite source versions. Update exact top-level/package-root JSON version fields and the one editable `hal0ai` package block in `uv.lock`. Write every candidate to temporary files, validate all candidates, then replace originals.

After successful replacement, the CLI runs:

```python
subprocess.run(["uv", "lock"], cwd=root, check=True)
```

and re-validates the resulting lock version.

- [ ] **Step 4: Extend `release-check.sh`**

The check must:

```bash
POLICY_JSON="$(PYTHONPATH=src python3 -m hal0.release.policy "${TAG}" --format json)"
POLICY_VERSION="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' <<<"${POLICY_JSON}")"
```

Then compare normalized source versions, require the tag target to equal `origin/main`, query GitHub checks for that SHA, and reject existing local/remote tags, GitHub Releases, and PyPI versions. Nightly keeps existing base-match and collision behavior.

- [ ] **Step 5: Verify**

```bash
uv run pytest --confcutdir=tests/scripts tests/scripts/test_set_version.py -q
python scripts/set-version.py --check 1.0.0-alpha.0
bash scripts/release-check.sh --tag v1.0.0-alpha.0 --dry-run
```

Expected: tests pass; current source validation passes; dry-run prints policy and performs no tag/release mutation.

- [ ] **Step 6: Commit**

```bash
git add scripts/set-version.py tests/scripts/test_set_version.py scripts/release-check.sh
git commit -m "feat(release): synchronize and preflight official versions"
```

---

### Task 3: Additive preview manifest and release-note contract

**Files:**

- Modify: `src/hal0/updater/updater.py:143-216`
- Modify: `tests/updater/test_updater.py`
- Modify: `src/hal0/release/notes.py`
- Modify: `scripts/gen_release_notes.py`
- Modify: `docs/internal/release-manifest.md`

**Interfaces:**

- Extends `ReleaseManifest` with `release_kind`, `prerelease_stage`, `rollback_policy`, `upgrade_from`, and `operator_migrations`.
- Preserves `_schema = "hal0.releases.v1"` and `extra = "allow"`.

- [ ] **Step 1: Write failing manifest parsing tests**

Add tests that parse an alpha manifest, reject an invalid channel/stage/policy combination, and parse an old stable v1 manifest with safe defaults:

```python
manifest = ReleaseManifest.model_validate({
    "_schema": "hal0.releases.v1",
    "version": "1.0.0-alpha.1",
    "channel": "preview",
    "release_kind": "preview",
    "prerelease_stage": "alpha",
    "rollback_policy": "safe",
    "upgrade_from": ">=0.9.8",
    "operator_migrations": [],
    "url": "https://example.test/hal0.tar.gz",
    "bundle_url": "https://example.test/hal0.tar.gz.bundle",
    "digest_sha256": "0" * 64,
    "signer_identity": "release-workflow",
})
assert manifest.prerelease_stage == "alpha"
```

- [ ] **Step 2: Run tests and confirm RED**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/updater/test_updater.py -k manifest -q
```

Expected: missing attributes or missing validation failure.

- [ ] **Step 3: Add typed fields and model validation**

Add `Literal` fields with backward-compatible defaults. Add one `model_validator(mode="after")` enforcing:

- preview requires alpha/beta/rc;
- stable/nightly require no preview stage;
- non-empty operator migrations imply rollback is `backup-required` or `blocked`;
- `channel` and `release_kind` agree.

- [ ] **Step 4: Extend release-note generation**

Accept `preview` in `scripts/gen_release_notes.py`. Preview output must include headings `Audience`, `Known issues`, `Supported upgrades`, `Operator migrations`, and `Rollback`. Add parser/render tests in the existing release-note test module; a missing heading exits non-zero.

- [ ] **Step 5: Verify and document**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/updater/test_updater.py -k manifest -q
HAL0_HOME=$(mktemp -d) uv run pytest tests/release/ -q
uv run ruff check src/hal0/updater/updater.py src/hal0/release/notes.py
```

Update `docs/internal/release-manifest.md` with all new fields and compatibility defaults.

- [ ] **Step 6: Commit**

```bash
git add src/hal0/updater/updater.py tests/updater/test_updater.py \
  src/hal0/release/notes.py scripts/gen_release_notes.py \
  docs/internal/release-manifest.md
git commit -m "feat(release): add preview and rollback manifest policy"
```

---

### Task 4: Publish explicit GitHub previews, PyPI wheels, and signed manifests

**Files:**

- Modify: `.github/workflows/release.yml`
- Modify: `.github/workflows/nightly.yml`
- Create or modify: workflow fixtures/tests under `tests/release/`

**Interfaces:**

- Consumes all GitHub output keys from Task 1.
- Produces versioned tarball/signatures, Python wheel, `<channel>.json`, and `<channel>.json.bundle`.

- [ ] **Step 1: Add a failing workflow-policy contract test**

Create `tests/release/test_workflow_contract.py` that loads `release.yml` and asserts it invokes `hal0.release.policy`, does not contain an independent alpha/beta/rc regex, contains conditions for PyPI and GitHub prerelease/latest, signs channel manifests, and iterates `manifest_targets`.

- [ ] **Step 2: Run the contract test and confirm RED**

```bash
uv run pytest tests/release/test_workflow_contract.py -q
```

Expected: assertions fail against current stable/nightly-only workflow.

- [ ] **Step 3: Replace tag/channel shell derivation with policy outputs**

Use:

```bash
PYTHONPATH=src python3 -m hal0.release.policy "${TAG}" --format github >> "${GITHUB_OUTPUT}"
```

Remove explicit channel override precedence. A workflow-call input may be retained only as an assertion that equals the policy result.

- [ ] **Step 4: Build wheel and publish conditionally**

Build with:

```bash
python -m build --wheel
```

Use `pypa/gh-action-pypi-publish` only when `publish_pypi == 'true'`, under the protected `release` environment. Verify the normalized version exists through PyPI's JSON endpoint before moving channel manifests.

- [ ] **Step 5: Create GitHub Release with explicit API fields**

Create/update the release using GitHub's Releases API body containing exact booleans:

```json
{
  "tag_name": "${TAG}",
  "name": "${TAG}",
  "draft": false,
  "prerelease": true,
  "make_latest": "false"
}
```

Stable uses `prerelease=false` and `make_latest=true`. Query the release after creation and assert both fields match policy before uploading manifests.

- [ ] **Step 6: Generate and sign each target manifest**

For every comma-separated `manifest_targets` entry, generate `<channel>.json`, then:

```bash
cosign sign-blob --yes \
  --bundle "${CHANNEL}.json.bundle" \
  "${CHANNEL}.json"
```

Upload JSON and bundle. Download them through GitHub asset URLs and verify with the existing workflow identity. Only then is the release considered published.

- [ ] **Step 7: Preserve nightly behavior**

Change `nightly.yml` to pass the generated tag and let policy derive `nightly`. Keep CI greenness gate, change gate, 14-digit stamp, and retention of seven releases/tags unchanged.

- [ ] **Step 8: Verify**

```bash
uv run pytest tests/release/ -q
uv run ruff check src/hal0/release/ tests/release/
python -c 'import yaml; yaml.safe_load(open(".github/workflows/release.yml"))'
python -c 'import yaml; yaml.safe_load(open(".github/workflows/nightly.yml"))'
```

Run `actionlint` if installed. Use workflow dispatch against a non-publishing dry-run input and confirm the job summary shows preview policy without creating a release.

- [ ] **Step 9: Commit**

```bash
git add .github/workflows/release.yml .github/workflows/nightly.yml \
  tests/release/test_workflow_contract.py
git commit -m "feat(release): publish signed official previews"
```

---

### Task 5: Publication integration gate and operator documentation

**Files:**

- Modify: `docs/internal/release-manifest.md`
- Modify: `CHANGELOG.md`
- Modify: release runbook referenced by `scripts/release-check.sh`
- Test: `tests/release/`

- [ ] **Step 1: Add end-to-end policy fixtures**

Create fixtures for alpha → beta → RC → final and assert final returns both manifest targets while nightly remains isolated. Assert a simulated failure before manifest upload leaves the previous preview fixture selected.

- [ ] **Step 2: Protect release tags and publishing environments**

In GitHub repository settings, add a tag ruleset matching `refs/tags/v*` that
restricts creation/deletion/update to the release-maintainer team. Configure a
protected `release` environment and bind the PyPI trusted publisher to
`Hal0ai/hal0`, workflow `release.yml`, environment `release`. Record the settings
and trusted-publisher identity in the runbook.

- [ ] **Step 3: Document exact operator sequence**

Document:

```bash
python scripts/set-version.py 1.0.0-alpha.1
./scripts/release-check.sh --tag v1.0.0-alpha.1
git commit -am "chore(release): prepare v1.0.0-alpha.1"
git tag -s v1.0.0-alpha.1
git push origin main v1.0.0-alpha.1
```

Include GitHub/PyPI verification and the rule that a failed version is never reused.

- [ ] **Step 4: Run final subsystem verification**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/release/ tests/scripts/test_set_version.py \
  tests/updater/test_updater.py -k 'policy or manifest or release' -q
uv run ruff check src/hal0/release/ scripts/set-version.py tests/release/
python scripts/check_sunset.py
```

Expected: all pass and sunset guard reports `1.0.0-alpha.*` before GA.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md docs/internal/release-manifest.md tests/release/
git commit -m "docs(release): add official preview cut runbook"
```
