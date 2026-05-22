# hal0 release manifest schema (`hal0.releases.v1`)

The release manifest is a single JSON file served per channel at:

```
https://releases.hal0.dev/{stable|nightly}.json
```

A path-based fallback URL (`https://hal0.dev/releases/{channel}.json`)
serves the same file — both resolve to `public/releases/*.json` in the
[hal0ai/hal0-web](https://github.com/hal0ai/hal0-web) repo via a
Cloudflare Pages `_redirects` rewrite. See that repo's `README.md`
("Release manifest hosting") for the CF Pages + DNS setup.

`hal0 update --check` (CLI) and `GET /api/updates/check` (API) both fetch
this file, validate it against the schema below, and compare
`version` against `hal0.__version__`. `hal0 update` (no `--check`)
additionally downloads the tarball + cosign signature, verifies both,
extracts to `/usr/lib/hal0-<version>/`, and atomically swaps the
`/usr/lib/hal0/current` symlink.

The publisher (the `release.yml` GitHub Actions workflow) produces this
file post-build; the runtime consumer is `hal0.updater.Updater.apply()`.

### Smoke check

```sh
# Fetch + pretty-print the live stable manifest
curl -s https://releases.hal0.dev/stable.json | jq .

# Verify cache + CORS headers are wired up by Cloudflare Pages
curl -sI https://releases.hal0.dev/stable.json | grep -iE 'cache-control|access-control'

# Path-based fallback
curl -s https://hal0.dev/releases/stable.json | jq -r '.version, .channel, .digest_sha256'
```

Until `release.yml` ships, the live manifest is a placeholder
(`_placeholder: true`, version `0.0.0`, all-zeros `digest_sha256`). It
parses cleanly through `ReleaseManifest.model_validate` so
`hal0 update --check` succeeds and reports "no update available", but
any attempt to `apply()` it will fail at the cosign-verify step —
intentional, since there is no real signed artifact behind it yet.

## Schema

```json
{
  "_schema": "hal0.releases.v1",
  "version": "0.1.1",
  "channel": "stable",
  "url": "https://github.com/hal0ai/hal0/releases/download/v0.1.1/hal0-0.1.1.tar.gz",
  "sig_url": "https://github.com/hal0ai/hal0/releases/download/v0.1.1/hal0-0.1.1.tar.gz.sig",
  "cert_url": "https://github.com/hal0ai/hal0/releases/download/v0.1.1/hal0-0.1.1.tar.gz.crt",
  "digest_sha256": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
  "signer_identity": "^(?i)https://github\\.com/hal0ai/hal0/\\.github/workflows/release\\.yml@refs/tags/v0\\.1\\.1$",
  "signer_issuer": "https://token.actions.githubusercontent.com",
  "min_data_version": 1,
  "released_at": "2026-05-15T12:00:00Z",
  "notes_url": "https://github.com/hal0ai/hal0/releases/tag/v0.1.1",
  "manifest_url": "https://releases.hal0.dev/stable.json",
  "toolbox_images": {
    "vulkan":    { "tag": "ghcr.io/hal0ai/hal0-toolbox-vulkan:v0.1.1",    "digest": "sha256:..." },
    "rocm":      { "tag": "ghcr.io/hal0ai/hal0-toolbox-rocm:v0.1.1",      "digest": "sha256:..." },
    "flm":       { "tag": "ghcr.io/hal0ai/hal0-toolbox-flm:v0.1.1",       "digest": "sha256:..." },
    "moonshine": { "tag": "ghcr.io/hal0ai/hal0-toolbox-moonshine:v0.1.1", "digest": "sha256:..." },
    "kokoro":    { "tag": "ghcr.io/hal0ai/hal0-toolbox-kokoro:v0.1.1",    "digest": "sha256:..." },
    "comfyui":   { "tag": "ghcr.io/hal0ai/hal0-toolbox-comfyui:v0.1.1",   "digest": "sha256:..." }
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
| `cert_url` | string | yes | URL of the Fulcio-issued certificate (`*.crt`). cosign 3.x requires `--certificate` alongside `--signature` for keyless verify; the cert's SAN is what `--certificate-identity-regexp` checks. |
| `digest_sha256` | string | yes | Hex sha256 of the tarball bytes (64 chars). `sha256:` prefix tolerated. |
| `signer_identity` | string | yes | Regex passed to `cosign verify-blob --certificate-identity-regexp`. Must anchor (`^...$`) to the exact GH Actions workflow + ref. Use `(?i)` to absorb GH's case preservation — the `Hal0ai` org slug is canonical capital-H but lowercase `hal0ai` references redirect; `(?i)` matches either. |
| `signer_issuer` | string | no | OIDC issuer; defaults to GitHub Actions (`token.actions.githubusercontent.com`). |
| `min_data_version` | int | no | Smallest acceptable `meta.schema_version` on disk. Updater runs forward migrations up to `max(min_data_version, latest_version())`. Default `1`. |
| `released_at` | string | no | ISO-8601 release timestamp; surfaced in the dashboard. |
| `notes_url` | string | no | Link to release notes. |
| `manifest_url` | string | no | Self-reference (so a cached copy knows where it came from). |
| `toolbox_images` | object | no | Mirrors the in-tree `manifest.json` shape: `{name: {tag, digest}}`. The release pins compatible container images. |

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

For the Phase-2 prototype + LXC smoke (PLAN §17 risk #2 — "prototype in
phase 2 against a throwaway release, not phase 5"), setting
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
  current -> /usr/lib/hal0-0.1.0/
  hal0-0.1.0/

/var/lib/hal0/
  cache/                 (may be absent)
  hal0.previous          (may be absent — first install)

# After hal0 update to 0.1.1
/usr/lib/hal0/
  current -> /usr/lib/hal0-0.1.1/   (atomic swap)
  hal0-0.1.0/                       (retained for rollback)
  hal0-0.1.1/                       (newly extracted)

/var/lib/hal0/
  cache/0.1.1/
    hal0-0.1.1.tar.gz
    hal0-0.1.1.tar.gz.sig
  hal0.previous          (content: "/usr/lib/hal0-0.1.0")
```

`hal0 update --rollback` reads `/var/lib/hal0/hal0.previous`, swaps the
symlink back, and re-points the previous record at what was just
current — so a second rollback bounces between the two installs.

## What the release pipeline must guarantee

When `hal0.dev` exists and the `release.yml` workflow runs, it must:

1. Build `hal0-<version>.tar.gz` as a tar archive with a top-level
   `hal0-<version>/` directory (matches the synthetic-prototype shape
   used by the tests and the LXC smoke).
2. Compute `digest_sha256` of the tarball bytes (no compression-format
   ambiguity — sha the on-disk file).
3. Sign with cosign keyless against the workflow's OIDC identity. The
   `signer_identity` in the manifest must anchor exactly to that
   identity (`^https://github.com/<org>/<repo>/.github/workflows/release.yml@refs/tags/v<ver>$`).
4. Publish `hal0-<version>.tar.gz` + `.sig` as release assets.
5. POST/commit the new `stable.json` or `nightly.json` to
   [hal0ai/hal0-web](https://github.com/hal0ai/hal0-web) under
   `public/releases/{channel}.json` — Cloudflare Pages auto-deploys
   the change to `releases.hal0.dev` (and the path-based fallback
   `hal0.dev/releases/`). Two acceptable mechanisms:
   - **PR flow** (preferred for `stable`): `release.yml` opens a PR
     against `hal0ai/hal0-web` updating only `public/releases/stable.json`,
     gated on a human approve before merge.
   - **Direct push** (acceptable for `nightly`): a deploy key with
     write scope limited to `public/releases/nightly.json` lets the
     workflow commit straight to `master`, no PR.
   Either way, the file replaces the prior manifest atomically; the
   updater fetches whatever's there with a `max-age=60` cache window
   set by `public/_headers`.
6. Patch `toolbox_images.<name>.digest` with the sha256 content digests
   from the toolbox build (`toolbox.yml` already does this for
   `manifest.json`; mirror that block into the release manifest so an
   update pulls the exact image set known-good for that release).

## Currently pinned toolbox images (`manifest.json`)

The in-tree `manifest.json` is the source of truth for what
`toolbox_images.<name>` the release pipeline must mirror into the
release manifest. As of `2026-05-20` the pinned set is:

| Name        | Tag                                       | Notes |
|---|---|---|
| `vulkan`    | `ghcr.io/hal0ai/hal0-toolbox-vulkan:v1`   | llama.cpp Vulkan backend; default for Strix Halo iGPU. |
| `rocm`      | `ghcr.io/hal0ai/hal0-toolbox-rocm:v1`     | llama.cpp ROCm backend; multi-arch (gfx1030..gfx1151). |
| `flm`       | `ghcr.io/hal0ai/hal0-toolbox-flm:v1`      | FastFlowLM on AMD XDNA2 NPU. Image is self-contained (FLM ships under `/opt/fastflowlm/` inside the image — no host bind-mount required since commit c998106). Requires host kernel ≥ 6.11 + `amdxdna` driver. |
| `moonshine` | `ghcr.io/hal0ai/hal0-toolbox-moonshine:v1`| Moonshine STT (CPU ONNX only — upstream wheel has no Vulkan/ROCm EP). **Rebuilt 2026-05-20** to fix the `MoonshineOnnxModel(models_dir=..., model_name=...)` constructor requirement; prior tag would `TypeError` on local-weights load. |
| `kokoro`    | `ghcr.io/hal0ai/hal0-toolbox-kokoro:v1`   | Kokoro-82M TTS (CPU ONNX); OpenAI-compat `/v1/audio/speech`. |
| `comfyui`   | `ghcr.io/hal0ai/hal0-toolbox-comfyui:v1`  | ComfyUI image-gen on ROCm; SDXL + SD 1.5 + Flux. Translates OpenAI `/v1/images/generations`. |

All six entries carry pinned `sha256` digests today (no remaining
`null`/placeholder values); the `release.yml` mirror step can publish
straight from `manifest.json` without a patch pass.

`available_backends()` only advertises `npu` when the FLM image is
locally present (`docker image inspect` returns 0). Hosts without
ghcr credentials therefore see GPU/CPU only rather than spiralling
into a `docker pull → unauthorized → systemd restart` loop on every
NPU selection. See
[models-slots-impl-plan.md → FLM provider live](./models-slots-impl-plan.md#flm-provider-live-npu-srchal0providersflmpy).

## Related

- `PLAN.md` §9 — Update mechanism spec
- `PLAN.md` §17 risk #2 — cosign edge cases
- `src/hal0/updater/updater.py` — implementation
- `src/hal0/api/routes/updater.py` — route layer (Team C)
- `manifest.json` — toolbox image pin file (Team A)
