# Release manifest

The release manifest is the JSON schema used to describe a hal0 release on the
`releases.hal0.dev` endpoint and consumed by the self-updater
(``src/hal0/updater/updater.py``).

## Schema identity

```json
"_schema": "hal0.releases.v1"
```

All manifests carry this field so consumers can reject an unexpected schema
shape. Future schema iterations (v2, v3…) will use a distinct identifier.

## Required fields

| Field              | Type     | Description                                              |
|--------------------|----------|----------------------------------------------------------|
| `version`          | `str`    | Release version, e.g. `"0.1.1"`.                        |
| `url`              | `str`    | Tarball download URL (https or file).                    |
| `bundle_url`       | `str`    | Sigstore bundle URL (cosign keyless OIDC).               |
| `digest_sha256`    | `str`    | Hex sha256 of the tarball bytes (64 hex chars).          |
| `signer_identity`  | `str`    | GitHub Actions OIDC subject regex for cosign verify.     |

## Optional fields

| Field                | Type                 | Default                            | Description                                              |
|----------------------|----------------------|------------------------------------|----------------------------------------------------------|
| `channel`            | `str`                | `"stable"`                         | Release channel.                                         |
| `release_kind`       | `str`                | `"stable"`                         | Kind: `"stable"`, `"nightly"`, or `"preview"`.           |
| `prerelease_stage`   | `str` or `null`      | `null`                             | Preview stage: `"alpha"`, `"beta"`, or `"rc"`.           |
| `rollback_policy`    | `str`                | `"safe"`                           | Rollback policy: `"safe"`, `"backup-required"`, `"blocked"`. |
| `upgrade_from`       | `str`                | `""`                               | Version constraint for supported upgrade paths, e.g. `">=0.9.8"`. |
| `operator_migrations`| `list[str]`          | `[]`                               | Operator-visible migration steps for this release.        |
| `signer_issuer`      | `str`                | `"https://token.actions.githubusercontent.com"` | OIDC issuer URL.                         |
| `min_data_version`   | `int`                | `1`                                | Minimum config schema version.                            |
| `revoked`            | `bool`               | `false`                            | True if the release is yanked/withdrawn.                  |
| `revoked_reason`     | `str`                | `""`                               | Reason shown when `revoked` is true.                      |
| `released_at`        | `str` or `null`      | `null`                             | ISO-8601 release timestamp.                               |
| `notes_url`          | `str` or `null`      | `null`                             | URL to release notes.                                     |
| `manifest_url`       | `str` or `null`      | `null`                             | Self-reference URL.                                       |
| `toolbox_images`     | `dict`               | `{}`                               | Mirror of manifest.json's toolbox_images block.           |

## Cross-field validation

The pydantic model enforces these rules at parse time:

1. **Preview coherence** — When `release_kind` is `"preview"`:
   - `prerelease_stage` must be one of `"alpha"`, `"beta"`, or `"rc"`.
   - `channel` must be `"preview"`.

2. **Stable / nightly coherence** — When `release_kind` is `"stable"` or `"nightly"`:
   - `prerelease_stage` must be `null`.
   - `channel` must match `release_kind`.

3. **Operator-migration safety** — When `operator_migrations` is non-empty:
   - `rollback_policy` must be `"backup-required"` or `"blocked"`.

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
