# Task 4 report — immutable prerelease publication

## Status

Complete. The release workflow now resolves one tag-derived policy before any build, checks out that exact tag in release and PyPI jobs, publishes policy-targeted manifests with sibling Sigstore bundles, and refuses mutable GitHub/PyPI publication. The local preflight accepts and validates the preview channel. No installer, UI, updater, Graphify, or Shepherd source was intentionally changed.

## Commits

- `5dd93fdd` — `fix(release): publish preview artifacts immutably`
- Follow-up documentation commit — this report only.

## Changed files

- `.github/workflows/release.yml`
  - Added the prerequisite `resolve` job and exposed `ReleasePolicy.to_github_outputs()` values.
  - Made release and PyPI jobs depend on the resolved exact tag and verify tag/HEAD equality.
  - Generated and schema-validated every `manifest_targets` manifest.
  - Signed and self-verified each exact JSON file with its sibling `.bundle` using the client-pinned release-workflow identity and issuer.
  - Added release, asset, and normalized PyPI collision preflights; removed overwrite publication.
  - Drove GitHub prerelease/latest flags and PyPI eligibility from policy outputs and verified release flags after creation.
  - Kept channel-pointer advancement external as the final separately verified gate.
- `scripts/release-check.sh`
  - Added `stable|preview|nightly` usage/help and channel validation.
  - Enforced preview tag grammar, channel-policy agreement, base/source version agreement, and normalized PyPI collision checks.
  - Kept dry-run preflight operations read-only.
- `tests/release/test_workflow_contract.py`
  - Added static contracts for resolve outputs, exact-ref checkout, immutable upload, policy flags, manifest validation/signing/verification/upload, and separate pointer advancement.

## TDD evidence

### RED

1. Initial literal command:
   - `uv run pytest -q tests/release/test_workflow_contract.py tests/release/test_policy.py`
   - Collection blocked with exit 4 because the host `/etc/hal0/hal0.toml` was present but unreadable. This was an environment issue, not the intended contract RED.
2. Isolated command:
   - `HAL0_HOME=/tmp/hal0-task4-test uv run pytest -q tests/release/test_workflow_contract.py tests/release/test_policy.py`
   - **6 failed, 13 passed**. All six new workflow-contract tests failed against the pre-change workflow.

### GREEN

- `HAL0_HOME=/tmp/hal0-task4-test uv run pytest -q tests/release/test_workflow_contract.py tests/release/test_policy.py tests/release/test_channel.py`
  - **34 passed**, 1 unrelated Starlette deprecation warning.
- `bash -n scripts/release-check.sh`
  - Passed.
- `bash scripts/release-check.sh --help`
  - Passed and printed `stable|preview|nightly` usage without running release gates.
- Extracted workflow manifest generator, run locally with dummy digest and stable policy targets.
  - Both manifests passed `ReleaseManifest.model_validate`; comparison confirmed stable/preview outputs differ only by `channel` and `manifest_url`.
- Parsed `.github/workflows/release.yml` with PyYAML.
  - Passed; jobs were `resolve`, `release`, and `pypi-publish`.
- `git diff --check`
  - Passed.

## Self-review

### Findings

- **No blocker/high findings.** Exact tag checkout and tag-to-HEAD verification are present in both publishing jobs.
- **No blocker/high findings.** Every generated channel JSON is validated, signed as exact bytes, self-verified with the updater-pinned trust root, collision-checked, and uploaded with its sibling bundle.
- **No blocker/high findings.** `--clobber` is absent; existing releases, assets, and policy-normalized PyPI versions fail closed.
- **No blocker/high findings.** Preview prerelease/latest values and PyPI eligibility are policy outputs rather than inferred from an input channel.

### Concerns / residual risks

- No live GitHub Release, Sigstore OIDC signing, or PyPI publication was executed locally; safety/security wiring is covered by static contracts plus local manifest validation.
- An external channel-pointer advancement implementation remains outside this workflow by explicit requirement; this workflow only records that separately verified final gate.
- If GitHub release creation succeeds but a later verification or upload fails, the intentionally immutable retry policy requires an operator to inspect and clean up the incomplete release rather than overwrite it.
- Pre-existing/generated `.pi/shepherd/index.json` and `graphify-out/*` changes remain unstaged and were not included in the Task 4 implementation commit.
