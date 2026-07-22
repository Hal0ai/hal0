# Release manifest

The release manifest is the JSON schema used to describe a hal0 release on the
`releases.hal0.dev` endpoint and consumed by the bootstrap installer and
self-updater (``installer/bootstrap.sh`` and
``src/hal0/updater/updater.py``).

## Schema identity

```json
"_schema": "hal0.releases.v1"
```

This field is mandatory and must be the string literal shown above. Missing,
unknown, null, or non-string schema identities are rejected. Future schema
iterations (v2, v3…) will use a distinct identifier.

## Fields required by the strict bootstrap policy

| Field              | Type     | Description                                              |
|--------------------|----------|----------------------------------------------------------|
| `_schema`          | literal  | Exactly `"hal0.releases.v1"`.                           |
| `version`          | `str`    | Nonempty release version, e.g. `"0.1.1"`.               |
| `channel`          | `str`    | Canonical pointer: `stable`, `preview`, or `nightly`.    |
| `release_kind`     | `str`    | Canonical artifact kind: stable, preview, or nightly.    |
| `url`              | `str`    | Nonempty tarball download URL (https or file).           |
| `bundle_url`       | `str`    | Nonempty, mandatory Sigstore artifact-bundle URL.        |
| `digest_sha256`    | `str`    | 64 hex chars, optionally prefixed with `sha256:`.        |
| `signer_identity`  | `str`    | Nonempty GitHub Actions OIDC subject regex for cosign.   |
| `signer_issuer`    | `str`    | Nonempty OIDC issuer used for artifact verification.     |

## Optional fields

| Field                | Type                 | Default                            | Description                                              |
|----------------------|----------------------|------------------------------------|----------------------------------------------------------|
| `prerelease_stage`   | `str` or `null`      | `null`                             | Preview stage: `"alpha"`, `"beta"`, or `"rc"`.           |
| `rollback_policy`    | `str`                | `"safe"`                           | Rollback policy: `"safe"`, `"backup-required"`, `"blocked"`. |
| `upgrade_from`       | `str`                | `""`                               | Version constraint for supported upgrade paths, e.g. `">=0.9.8"`. |
| `operator_migrations`| `list[str]`          | `[]`                               | Operator-visible migration steps for this release.        |
| `min_data_version`   | `int`                | `1`                                | Minimum config schema version.                            |
| `revoked`            | `bool`               | `false`                            | True if the release is yanked/withdrawn.                  |
| `revoked_reason`     | `str`                | `""`                               | Reason shown when `revoked` is true.                      |
| `released_at`        | `str` or `null`      | `null`                             | ISO-8601 release timestamp.                               |
| `notes_url`          | `str` or `null`      | `null`                             | URL to release notes.                                     |
| `manifest_url`       | `str` or `null`      | `null`                             | Self-reference URL.                                       |
| `toolbox_images`     | `dict`               | `{}`                               | Mirror of manifest.json's toolbox_images block.           |

For updater compatibility, the Python model still defaults omitted `channel`
and `release_kind` to `stable`, and `signer_issuer` to the GitHub Actions OIDC
issuer. The bootstrap intentionally does not apply those defaults: all three
must be explicit nonempty strings in an authenticated pointer.

## Cross-field validation

The pydantic model enforces these rules at parse time:

1. **Preview coherence** — When `release_kind` is `"preview"`:
   - `prerelease_stage` must be one of `"alpha"`, `"beta"`, or `"rc"`.
   - `channel` must be `"preview"`.

2. **Stable / nightly coherence**:
   - Stable artifacts have `release_kind: "stable"`, no `prerelease_stage`,
     and may target the `stable` or `preview` pointer. This permits promotion
     of an already-built stable artifact to preview before advancing stable.
   - Nightly artifacts have `release_kind: "nightly"`, no `prerelease_stage`,
     and target only the `nightly` pointer.

3. **Operator-migration safety** — When `operator_migrations` is non-empty:
   - `rollback_policy` must be `"backup-required"` or `"blocked"`.

## Channel pointers and artifact kinds

Clients select one of the canonical pointers at
`https://releases.hal0.dev/<channel>.json`, where `<channel>` is `stable`,
`preview`, or `nightly`. The `channel` field identifies the pointer being
advanced; `release_kind` classifies the immutable artifact it references.
They are intentionally not synonyms: `preview.json` may point to a stable
artifact during promotion, but `stable.json` accepts only stable artifacts.
Consequently, stable clients never consume preview artifacts.

Each pointer has an exact sibling Sigstore bundle, for example
`preview.json.bundle`. The bootstrap and updater download the manifest bytes
and sibling bundle, then run `cosign verify-blob` with their client-pinned
`.github/workflows/release.yml` identity and
`https://token.actions.githubusercontent.com` issuer before trusting artifact
URLs. The bootstrap performs this check before parsing the JSON; no manifest
field can influence manifest verification. After authentication, one jq
1.6-compatible fail-closed pass requires the exact schema, nonempty bootstrap
strings, normalized digest, requested-channel equality, the stable/preview/
nightly kind matrix, and preview-only alpha/beta/rc stage rules. The updater
may decode for schema/channel rejection first but does not return or act on the
manifest until authentication succeeds. The manifest's own `signer_identity`
and `signer_issuer` fields become trusted only after that check. A missing
`jq`, bundle, or `cosign`, or failed validation/verification aborts closed.
The artifact `bundle_url` is mandatory; bootstrap has no detached `.sig`/`.crt`
fallback. Tarball digest and bundle verification then remain defense-in-depth.

The release workflow produces each pointer and bundle together as release
assets. Serving or advancing those external `releases.hal0.dev` pointers is a
separate **hal0-web release gate**: hal0-web must publish the exact manifest
bytes and matching sibling `.bundle`, and must not expose a new pointer when
either asset is absent. This repository does not make that external serving
step optional.

## Backward compatibility

Older manifests (before the preview/rollback policy fields were added) omit
the new fields and parse with safe defaults:

| Field                | Default value        |
|----------------------|----------------------|
| `release_kind`       | `"stable"`           |
| `prerelease_stage`   | `null`               |
| `rollback_policy`    | `"safe"`             |
| `upgrade_from`       | `""`                 |
| `operator_migrations`| `[]`                 |

The model uses `extra = "allow"` so any unknown fields round-trip without
error, preserving forward compatibility.

## Yanking a release

Set `revoked: true` and supply a `revoked_reason`. The updater's `check()`
method will not recommend a revoked release as an available update, but the
version and reason are still surfaced so the dashboard can explain why no
update is offered.

## Example: stable manifest

```json
{
  "_schema": "hal0.releases.v1",
  "version": "1.0.0",
  "channel": "stable",
  "release_kind": "stable",
  "rollback_policy": "safe",
  "url": "https://releases.hal0.dev/v1.0.0/hal0.tar.gz",
  "bundle_url": "https://releases.hal0.dev/v1.0.0/hal0.tar.gz.bundle",
  "digest_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "signer_identity": "^https://github\\.example/haloai/hal0/.*"
}
```

## Example: stable artifact promoted to preview

The pointer target remains `preview`, while the immutable artifact keeps its
stable classification:

```json
{
  "_schema": "hal0.releases.v1",
  "version": "1.0.0",
  "channel": "preview",
  "release_kind": "stable",
  "prerelease_stage": null,
  "rollback_policy": "safe",
  "url": "https://releases.hal0.dev/v1.0.0/hal0.tar.gz",
  "bundle_url": "https://releases.hal0.dev/v1.0.0/hal0.tar.gz.bundle",
  "digest_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "signer_identity": "^https://github\\.example/haloai/hal0/.*"
}
```

## Example: preview manifest

```json
{
  "_schema": "hal0.releases.v1",
  "version": "1.0.0-alpha.1",
  "channel": "preview",
  "release_kind": "preview",
  "prerelease_stage": "alpha",
  "rollback_policy": "safe",
  "upgrade_from": ">=0.9.8",
  "operator_migrations": [],
  "url": "https://releases.hal0.dev/v1.0.0-alpha.1/hal0.tar.gz",
  "bundle_url": "https://releases.hal0.dev/v1.0.0-alpha.1/hal0.tar.gz.bundle",
  "digest_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "signer_identity": "^https://github\\.example/haloai/hal0/.*"
}
```
