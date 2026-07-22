# Prerelease Preview Pipeline Hardening

## Status

Approved direction: use the existing canonical `preview` channel for official alpha,
beta, and release-candidate builds. This design covers the local hal0 updater,
installer contract, release workflow, and verification tests. It does not silently
deploy or mutate the external hal0-web channel service.

## Problem

The repository has tag policy for `vX.Y.Z-alpha.N`, `vX.Y.Z-beta.N`, and
`vX.Y.Z-rc.N`, but the release action and updater do not consistently implement
that policy. Current gaps include:

- preview tags can be published as GitHub latest/stable releases;
- `hal0 update --channel preview` is rejected by CLI/API/config validation;
- manual and reusable release invocations can build a ref other than the supplied
tag;
- release reruns can overwrite existing release assets;
- the updater does not bind the requested channel to the fetched manifest before
persisting or applying it;
- final-release promotion to both `stable` and `preview` is not represented by the
manifest validator and workflow;
- bootstrap and updater endpoint defaults are inconsistent.

The first official preview must be immutable, signed, channel-isolated, and
verifiable before the rolling `preview` pointer advances.

## Goals

1. Make `preview` a first-class channel across CLI, API, schema, updater, UI, and
installer validation.
2. Make release workflow behavior derive from the shared release policy rather than
branching on ad hoc tag strings.
3. Build the exact requested tag for push, dispatch, and reusable workflow events.
4. Refuse tag/release/version/asset reuse and never use `--clobber` for official
artifacts.
5. Publish preview releases as GitHub prereleases, never latest, and publish the
corresponding PyPI prerelease only after artifact verification.
6. Validate requested-channel/manifest-channel coherence and persist a channel only
after successful manifest validation.
7. Define final-release promotion so the same stable artifact can be addressed by
both `stable` and `preview` channel manifests.
8. Add deterministic tests for policy, workflow contracts, updater behavior, and
CLI/API/config validation.

## Non-goals and boundaries

- Do not publish a tag, GitHub Release, PyPI distribution, or channel pointer during
implementation or test runs.
- Do not reuse the long-lived `prerelease-channel` branch. Each cut uses a fresh
short-lived preparation branch such as `release/v1.0.0-alpha.1`.
- Do not change the `nightly` channel semantics except where shared validation or
workflow policy requires an explicit matrix case.
- Do not implement or deploy the external hal0-web service in this repository. Its
preview-pointer and signed-manifest serving contract is a release gate and must be
validated separately before the first publication.
- Do not weaken existing Gamma, Python, UI, or release safety coverage.

## Canonical channel and manifest model

`preview` is the rolling client channel for alpha, beta, and RC releases. The
manifest `channel` field identifies the channel pointer being served; the
`release_kind` field identifies the artifact's release class. These are distinct:

| Requested channel | Accepted release kind | Meaning |
|---|---|---|
| `stable` | `stable` | Stable users never consume previews. |
| `preview` | `preview` or `stable` | Preview users receive the newest official preview, or the promoted final artifact after GA. |
| `nightly` | `nightly` | Nightly remains separate from official previews. |

A preview manifest must still carry a non-null `prerelease_stage` when its
`release_kind` is `preview`. A promoted stable artifact has `release_kind =
"stable"`, `prerelease_stage = null`, and may be serialized once for `stable` and
once for `preview` with the target channel field changed. Artifact identity,
digest, signature, and version must remain identical between those two manifests.

The updater's canonical endpoint is:

`https://releases.hal0.dev/<channel>.json`

The bootstrap installer must use the same endpoint contract rather than GitHub's
mutable `/releases/latest` endpoint. Both clients must verify the channel manifest
signature/bundle according to the existing release-manifest trust design before
trusting artifact URLs or digests.

## Release workflow design

### Input and ref resolution

- For a tag push, derive the tag from `GITHUB_REF`.
- For `workflow_dispatch` and `workflow_call`, require the tag input and checkout
that exact tag/ref explicitly.
- Resolve `ReleasePolicy` once and export `tag`, `version`, `channel`, release kind,
prerelease stage, GitHub flags, manifest targets, and PyPI publication policy as
workflow outputs.
- Refuse a tag whose checked-out commit does not equal the tag target.
- Require a fresh release-preparation branch policy before a tag is created; the
workflow validates the immutable tag, while the branch naming rule is enforced by
the cut checklist/preflight.

### Collision and publication gates

Before any upload:

1. Confirm the tag exists and is not moved or reused.
2. Confirm the GitHub Release for the version/tag does not already exist.
3. Confirm required assets do not already exist.
4. Confirm the PyPI version is not already published when policy enables PyPI.
5. Build the runtime tarball and UI from the checked-out tag.
6. Generate the channel manifest(s), sign them, and self-verify signatures and
artifact digests.

Creation uses explicit policy-derived GitHub fields:

- alpha/beta/RC: `prerelease=true`, `latest=false`;
- nightly: `prerelease=true`, `latest=false`;
- final: `prerelease=false`, `latest=true`.

Uploads must fail if an asset already exists. No `--clobber` or equivalent overwrite
path is permitted. A rerun after any publication failure must use a new immutable
version/tag, not replace the prior release.

For preview publication, the workflow verifies the GitHub assets and PyPI result
before the external `preview.json` pointer is advanced. Pointer advancement is the
last publication step. If the pointer update or external verification fails, the
immutable release remains published and the next operator action is repair/new
pointer publication, never asset replacement.

For a final release, generate equivalent stable and preview target manifests for the
same artifact identity and advance both pointers only after all artifact/signature
and PyPI checks pass.

## Updater and API design

- Define one shared channel type containing `stable`, `preview`, and `nightly`.
- Accept `preview` in CLI `update --channel`, API update/check routes, persisted
configuration, and dashboard update controls.
- Fetch and parse the manifest before persisting a requested channel change.
- Reject a manifest whose `channel` is not the requested channel.
- Apply the acceptance matrix above: stable rejects preview artifacts; preview
accepts preview or promoted stable; nightly accepts nightly only.
- Keep channel state unchanged when fetch, signature, schema, or channel-coherence
validation fails.
- Ensure `check`, `prepare`, `commit`, rollback, and editable-install read-only
paths use the same channel validation.
- Preserve rollback behavior: an applied preview update records the previous tree
and manifest, and rollback does not silently switch channels or downgrade outside
the requested policy.

## Error handling and observability

Errors must identify the rejected tag/channel/version and the failed gate without
printing credentials or signer secrets. Publication failures must distinguish:

- pre-publication validation failure;
- immutable GitHub artifact publication failure;
- PyPI publication/verification failure;
- external channel-pointer advancement failure.

The workflow must leave enough logs and artifact metadata to prove which gate
completed. It must never report a channel as advanced before remote verification.

## Verification plan

### Unit and integration tests

- Release policy matrix for alpha, beta, RC, nightly, and final tags.
- Workflow contract tests for exact-tag checkout, prerelease/latest flags, no
`--clobber`, collision checks, manifest targets, and PyPI gating.
- Manifest tests for preview coherence, stable-to-preview promotion, and signature
metadata.
- Updater tests for preview URL selection, requested-channel mismatch, acceptance
matrix, failed-persistence behavior, update, and rollback.
- CLI/API/config tests for accepting preview and preserving the old channel after
validation failure.
- Installer tests for stable/preview canonical endpoint selection and channel
isolation.
- Existing full Python, UI, Chromium/Gamma, and release test suites.

### Release rehearsal (non-publishing)

Use a disposable local/tag fixture and workflow-contract checks to exercise the
policy and generated metadata without pushing a tag, uploading an asset, publishing
to PyPI, or advancing an external pointer. The rehearsal must verify the exact
release branch/tag naming and artifact identity checks.

## Release cut sequence after merge

1. Wait for the final incoming session and merge PR #1330 only after fresh required
checks are green.
2. Create a new short-lived branch `release/v1.0.0-alpha.N` from the merged commit.
3. Run release preflight, full tests, UI/Gamma coverage, manifest/signature rehearsal,
and external hal0-web preview contract verification.
4. Create and push the immutable signed tag `v1.0.0-alpha.N` only after all gates pass.
5. Let the guarded action build, sign, publish, and verify GitHub/PyPI artifacts.
6. Advance `preview.json` last, then run live preview install/update/rollback
validation.
7. Stop on any unresolved gate; cut a new version rather than replacing a failed
published prerelease.
