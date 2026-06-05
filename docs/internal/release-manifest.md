# hal0 release manifest schema (`hal0.releases.v1`)

The release manifest is a single JSON file served per channel at:

```
https://releases.hal0.dev/{stable|nightly}.json
```

A path-based fallback URL (`https://hal0.dev/releases/{channel}.json`)
serves the same file. Hosting moved off the original Cloudflare Pages
target to **Vercel** in the v0.1.0-alpha cycle (see
`hal0_install_bootstrap` auto-memory); both URLs still resolve and the
schema below is unchanged. The
[hal0ai/hal0-web](https://github.com/Hal0ai/hal0-web) repo carries the
files under `public/releases/`.

`hal0 update --check` (CLI) and `GET /api/updates/check` (API) both
fetch this file, validate it against the schema below, and compare
`version` against `hal0.__version__`. `hal0 update` (no `--check`)
additionally downloads the tarball + cosign signature + Fulcio
certificate, verifies them, extracts to `/usr/lib/hal0-<version>/`,
and atomically swaps the `/usr/lib/hal0/current` symlink.

The publisher (the `release.yml` GitHub Actions workflow) produces this
file post-build; the runtime consumer is `hal0.updater.Updater.apply()`.

### Smoke check

```sh
# Fetch + pretty-print the live stable manifest
curl -s https://releases.hal0.dev/stable.json | jq .

# Verify cache + CORS headers
curl -sI https://releases.hal0.dev/stable.json | grep -iE 'cache-control|access-control'

# Path-based fallback
curl -s https://hal0.dev/releases/stable.json | jq -r '.version, .channel, .digest_sha256'
```

> **Current state at v0.3.0-alpha.1 (2026-05-27):** the live manifest at
> `releases.hal0.dev/stable.json` remains a placeholder
> (`_placeholder: true`, version `0.0.0`, all-zeros `digest_sha256`).
> It parses cleanly through `ReleaseManifest.model_validate` so
> `hal0 update --check` succeeds and reports "no update available", but
> `apply()` will fail at the cosign-verify step — intentional, since no
> real signed artifact has been published yet. The `release.yml`
> workflow is in flight; production wiring will follow.

## Schema

```jsonc
{
  "_schema": "hal0.releases.v1",
  "version": "0.3.0-alpha.2",
  "channel": "stable",
  "url":      "https://github.com/Hal0ai/hal0/releases/download/v0.3.0-alpha.2/hal0-0.3.0-alpha.2.tar.gz",
  "sig_url":  "https://github.com/Hal0ai/hal0/releases/download/v0.3.0-alpha.2/hal0-0.3.0-alpha.2.tar.gz.sig",
  "cert_url": "https://github.com/Hal0ai/hal0/releases/download/v0.3.0-alpha.2/hal0-0.3.0-alpha.2.tar.gz.crt",
  "digest_sha256": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
  "signer_identity": "^(?i)https://github\\.com/hal0ai/hal0/\\.github/workflows/release\\.yml@refs/tags/v0\\.3\\.0-alpha\\.2$",
  "signer_issuer": "https://token.actions.githubusercontent.com",
  "min_data_version": 1,
  "released_at": "2026-05-27T12:00:00Z",
  "notes_url":    "https://github.com/Hal0ai/hal0/releases/tag/v0.3.0-alpha.2",
  "manifest_url": "https://releases.hal0.dev/stable.json",

  // OPTIONAL: runtime artefact pins.
  //
  // v0.2+ shifted the runtime away from per-modality toolbox container
  // images and onto a single Lemonade install (Lemonade embeddable
  // tarball + FastFlowLM .deb on NPU hosts). The runtime artefact
  // pins below mirror the installer's pinned versions so an update
  // can refuse to apply if the host's Lemonade install drifts away
  // from the release's expected baseline.
  //
  // Both blocks are OPTIONAL — pre-v0.2 manifests and dev manifests
  // may omit them.
  "lemonade": {
    "version": "v10.6.0",
    "tarball_url": "https://github.com/lemonade-sdk/lemonade/releases/download/v10.6.0/lemonade-embeddable-10.6.0-ubuntu-x64.tar.gz",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "flm": {
    "version": "0.9.42",
    "deb_url": "https://github.com/FastFlowLM/FastFlowLM/releases/download/v0.9.42/fastflowlm_0.9.42_ubuntu24.04_amd64.deb",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },

  // OPTIONAL: historical / out-of-tree consumers.
  //
  // The `toolbox_images` block is retained as a historical reference
  // and for any out-of-tree consumers still reading v0.1.x manifests.
  // The hal0 runtime itself NO LONGER pulls these images in v0.2+ —
  // the runtime is Lemonade. The in-tree `manifest.json` continues to
  // carry the image pins (still under `toolbox_images`) but they are
  // not on the v0.2/v0.3 install path.
  "toolbox_images": {
    "vulkan":    { "tag": "ghcr.io/hal0ai/hal0-toolbox-vulkan:v1",    "digest": "sha256:..." },
    "rocm":      { "tag": "ghcr.io/hal0ai/hal0-toolbox-rocm:v1",      "digest": "sha256:..." },
    "flm":       { "tag": "ghcr.io/hal0ai/hal0-toolbox-flm:v1",       "digest": "sha256:..." },
    "moonshine": { "tag": "ghcr.io/hal0ai/hal0-toolbox-moonshine:v1", "digest": "sha256:..." },
    "kokoro":    { "tag": "ghcr.io/hal0ai/hal0-toolbox-kokoro:v1",    "digest": "sha256:..." },
    "comfyui":   { "tag": "ghcr.io/hal0ai/hal0-toolbox-comfyui:v1",   "digest": "sha256:..." }
  }
}
```

### Field reference

| Field | Type | Required | Notes |
|---|---|---|---|
| `_schema` | string | yes | Always `"hal0.releases.v1"`. Bump when the shape changes. |
| `version` | string | yes | Dotted release version (no leading `v`). |
| `channel` | string | no | `"stable"` (default), `"nightly"`, `"dev"`. |
| `url` | string | yes | `http(s)` or `file://` URL of the release tarball. |
| `sig_url` | string | yes | URL of the detached cosign signature (`*.sig`). |
| `cert_url` | string | yes | URL of the Fulcio-issued certificate (`*.crt`). cosign 3.x requires `--certificate` alongside `--signature` for keyless verify; the cert's SAN is what `--certificate-identity-regexp` checks. See `hal0_cosign_cert_plumbing` auto-memory. |
| `digest_sha256` | string | yes | Hex sha256 of the tarball bytes (64 chars). `sha256:` prefix tolerated. |
| `signer_identity` | string | yes | Regex passed to `cosign verify-blob --certificate-identity-regexp`. Must anchor (`^...$`) to the exact GH Actions workflow + ref. Use `(?i)` to absorb GH's case preservation — the `Hal0ai` org slug is canonical capital-H but lowercase `hal0ai` references redirect; `(?i)` matches either. |
| `signer_issuer` | string | no | OIDC issuer; defaults to GitHub Actions (`token.actions.githubusercontent.com`). |
| `min_data_version` | int | no | Smallest acceptable `meta.schema_version` on disk. Updater runs forward migrations up to `max(min_data_version, latest_version())`. Default `1`. |
| `released_at` | string | no | ISO-8601 release timestamp; surfaced in the dashboard. |
| `notes_url` | string | no | Link to release notes. |
| `manifest_url` | string | no | Self-reference (so a cached copy knows where it came from). |
| `lemonade` | object | no | Runtime pin for the Lemonade embeddable tarball. Mirrors the installer's `LEMONADE_VERSION` / `LEMONADE_TARBALL` / `LEMONADE_SHA256` pins. v0.2+ only. |
| `flm` | object | no | Runtime pin for the FastFlowLM `.deb`. Mirrors the installer's `FLM_DEB_VERSION` / `FLM_DEB_URL` / `FLM_DEB_SHA256` pins. v0.2+ only. NPU hosts only — non-NPU hosts ignore this. |
| `toolbox_images` | object | no | **Historical.** v0.1.x runtime pulled these images; v0.2+ does not. Retained for out-of-tree consumers. Mirrors the in-tree `manifest.json` shape: `{name: {tag, digest}}`. |

Pydantic enforces shape at fetch time
(`hal0.updater.updater._parse_manifest`). Malformed manifests reject
with `system.update_manifest_invalid` and the update flow stops before
the first byte is downloaded.

## Cosign verification

The updater shells out to `cosign verify-blob`:

```
cosign verify-blob \
  --signature <sig_url-payload> \
  --certificate <cert_url-payload> \
  --certificate-identity-regexp <signer_identity> \
  --certificate-oidc-issuer    <signer_issuer> \
  <tarball-path>
```

If `cosign` is not on `PATH` the updater raises a typed error
(`system.update_cosign_missing`) with install hints. It does **not**
fall back to unsigned acceptance.

### Pre-release escape hatch: `HAL0_UPDATE_SKIP_COSIGN`

For prototype + LXC smoke workflows, setting
`HAL0_UPDATE_SKIP_COSIGN=1` bypasses the verify step with a loud WARN
log.

The env var is **gated to dev (`0.x`) and pre-release builds** (any
version containing a `-` such as `1.0.0-rc1`, `1.0.0-dev`). On stable
v1+ tags it is silently ignored — the updater logs
`updater.cosign_skip_ignored_on_stable` and proceeds with mandatory
verification. There is no operator override on stable: if you need to
bypass cosign on a stable build, you have to either downgrade the
binary to a pre-release build or fix your cosign install.

## Filesystem effects of `apply()`

```
# Before
/usr/lib/hal0/
  current -> /usr/lib/hal0-0.3.0-alpha.1/
  hal0-0.3.0-alpha.1/

/var/lib/hal0/
  cache/                 (may be absent)
  hal0.previous          (may be absent — first install)

# After `hal0 update` to 0.3.0-alpha.2
/usr/lib/hal0/
  current -> /usr/lib/hal0-0.3.0-alpha.2/   (atomic swap)
  hal0-0.3.0-alpha.1/                       (retained for rollback)
  hal0-0.3.0-alpha.2/                       (newly extracted)

/var/lib/hal0/
  cache/0.3.0-alpha.2/
    hal0-0.3.0-alpha.2.tar.gz
    hal0-0.3.0-alpha.2.tar.gz.sig
    hal0-0.3.0-alpha.2.tar.gz.crt
  hal0.previous          (content: "/usr/lib/hal0-0.3.0-alpha.1")
```

`hal0 update --rollback` reads `/var/lib/hal0/hal0.previous`, swaps the
symlink back, and re-points the previous record at what was just
current — so a second rollback bounces between the two installs.

Lemonade + FLM pin reconciliation is a separate step (not part of the
hal0 tarball swap): if `lemonade.version` or `flm.version` in the
new manifest differs from what's installed, the updater logs the gap
and surfaces a follow-up `hal0 doctor lemonade` / `hal0 doctor flm`
suggestion. Forced reconciliation is left to the user; the updater
does not silently re-pull the embeddable tarball or the `.deb`.

## What the release pipeline must guarantee

When the `release.yml` workflow runs, it must:

1. Build `hal0-<version>.tar.gz` as a tar archive with a top-level
   `hal0-<version>/` directory (matches the synthetic-prototype shape
   used by the tests and the LXC smoke).
2. Compute `digest_sha256` of the tarball bytes (no compression-format
   ambiguity — sha the on-disk file).
3. Sign with cosign keyless against the workflow's OIDC identity. The
   `signer_identity` in the manifest must anchor exactly to that
   identity (`^https://github.com/<org>/<repo>/.github/workflows/release.yml@refs/tags/v<ver>$`).
4. Publish `hal0-<version>.tar.gz` + `.sig` + `.crt` as release assets
   (all three are required by the cosign 3.x verify flow — dropping
   the cert breaks `apply()`).
5. POST/commit the new `stable.json` or `nightly.json` to
   [hal0ai/hal0-web](https://github.com/Hal0ai/hal0-web) under
   `public/releases/{channel}.json` — Vercel auto-deploys the change
   to `releases.hal0.dev` (and the path-based fallback
   `hal0.dev/releases/`). Two acceptable mechanisms:
   - **PR flow** (preferred for `stable`): `release.yml` opens a PR
     against `hal0ai/hal0-web` updating only `public/releases/stable.json`,
     gated on a human approve before merge.
   - **Direct push** (acceptable for `nightly`): a deploy key with
     write scope limited to `public/releases/nightly.json` lets the
     workflow commit straight to `main`, no PR.
   Either way, the file replaces the prior manifest atomically; the
   updater fetches whatever's there with a `max-age=60` cache window
   set by `public/_headers`.
6. Populate `lemonade.{version,tarball_url,sha256}` from the
   installer's `LEMONADE_*` constants so a `hal0 update` knows which
   embeddable tarball the new release expects on disk. Same for
   `flm.{version,deb_url,sha256}` on NPU-supporting releases. These
   are advisory pins — see "Filesystem effects" above for why the
   updater does not auto-reconcile them.

## Why no longer toolbox-image-centric

v0.1.x shipped a per-modality toolbox container model:
`hal0-toolbox-{vulkan,rocm,flm,moonshine,kokoro,comfyui}:v1` images
pulled from ghcr.io, supervised under `hal0-slot@.service` instances.

v0.2 replaced that runtime with **Lemonade** (ADR-0008, supersedes
the invalidated ADR-0006 total-replacement plan). The runtime
artefact is now:

- The Lemonade embeddable tarball (`lemonade-embeddable-<ver>-ubuntu-x64.tar.gz`)
  extracted to `/opt/lemonade/`, supervised under
  `hal0-lemonade.service`.
- The FastFlowLM `.deb` (`fastflowlm_<ver>_ubuntu24.04_amd64.deb`)
  on AMD XDNA2 NPU hosts.
- `lemond` (the Lemonade daemon) talks to backends (llama.cpp Vulkan,
  ROCm where supported, FLM on NPU, whisper, kokoro) using upstream
  Lemonade's own packaging — hal0 no longer maintains per-modality
  container images on the install path.

The in-tree `manifest.json` continues to carry the v0.1.x
`toolbox_images` pin block. That file is retained for two reasons:

1. **Out-of-tree consumers.** Anyone scripting against the older
   manifest shape continues to read sensible pins.
2. **Historical reference.** When debugging a v0.1.x install the pins
   are still the source of truth for which image was current at the
   v0.1.x cycle's last release.

It is **not** the runtime artefact pin for v0.2+ installs. The
installer's Lemonade + FLM constants (in `installer/install.sh`) are
the live truth; the release manifest mirrors them via the new
`lemonade` + `flm` blocks above.

## Toolbox image digests

The `toolbox_images` block in the repo-root `manifest.json` pins each
toolbox image (`vulkan`, `rocm`, `flm`, `moonshine`, `kokoro`,
`comfyui`) to its published `ghcr.io/hal0ai/...` content digest. These
digests are **not** patched by any CI workflow (the historical
`.github/workflows/toolbox.yml` was never built). Refresh them with the
runnable script:

```sh
# Run on main BEFORE cutting a release. Queries ghcr.io anonymously for
# each image's published digest and patches manifest.json in place.
scripts/update-toolbox-digests.sh

git diff manifest.json   # review
git add manifest.json && git commit -m "chore: refresh toolbox image digests"
```

Behaviour:

- The script resolves each digest from the ghcr.io registry v2 manifest
  API (anonymous pull token + `Docker-Content-Digest` header), falling
  back to `docker buildx imagetools inspect` when the curl/token flow is
  unavailable.
- An unpublished or unreachable image leaves its `digest` as `null` and
  emits a warning. **A null digest is a soft fallback:** the runtime
  pulls that image by `:tag` and warns (see `load_manifest` /
  `manifest_image_ref`). It does not crash anything.
- `release.yml` (and `scripts/release-check.sh`) **refuse to publish a
  release manifest while any `toolbox_images.<n>.digest` is null** — so
  run this script and commit the result before tagging, or the release
  job fails fast with the list of missing images.

## Yanking a release

If a published release turns out to be bad (broken cosign cert, regression,
data-loss bug), **yank it** so `hal0 update` stops recommending it instead of
deleting the GitHub release (which loses history and breaks anyone mid-download).

A release is yanked by setting `revoked: true` on its channel manifest:

```jsonc
{
  "version": "0.4.2",
  "revoked": true,
  "revoked_reason": "cosign cert mismatch — re-cut as 0.4.3",
  // …all other fields unchanged…
}
```

Effect (implemented in `hal0.updater`):

- `Updater.check()` and `GET /api/updates/check` report `update_available: false`
  for a revoked latest — the version is still surfaced (`revoked` + `revoked_reason`)
  so the dashboard can explain why no update is offered, but the operator is never
  nudged toward it. `updater.latest_revoked` is logged.
- `GET /api/updates/state` carries `hal0.revoked` + `hal0.revoked_reason`.
- Older manifests without the field parse as `revoked: false` (backward compatible).

SOP:

1. Re-upload the channel manifest asset with `revoked: true` + a `revoked_reason`
   (`gh release upload <TAG> <channel>.json --clobber`).
2. Annotate the GitHub release: `gh release edit <TAG> --notes "YANKED: <reason>"`.
3. Cut and publish the fixed release; once a newer non-revoked version is the latest,
   it supersedes the revoked one automatically.

## Related

- `PLAN.md` §9 — Update mechanism spec
- `PLAN.md` §17 risk #2 — cosign edge cases
- `src/hal0/updater/updater.py` — implementation
- `src/hal0/api/routes/updater.py` — route layer
- `installer/install.sh` — `LEMONADE_VERSION` / `LEMONADE_URL` /
  `LEMONADE_SHA256` + `FLM_DEB_VERSION` / `FLM_DEB_URL` /
  `FLM_DEB_SHA256` constants (the live install-time pins this
  manifest mirrors)
- `manifest.json` — in-tree historical toolbox image pin file
- ADR-0006 — original total-replacement Lemonade plan
  (**invalidated** — see `hal0_lemonade_adr_0006_invalidated`)
- ADR-0008 — accepted Lemonade adoption (the decision that landed)
- `v0.3-state.md` — canonical v0.3 stream + status snapshot
