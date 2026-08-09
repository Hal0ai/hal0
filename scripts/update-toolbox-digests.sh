#!/usr/bin/env bash
#
# update-toolbox-digests.sh — refresh the published image digests pinned in
# the repo-root manifest.json under `toolbox_images`.
#
# Companion to `.github/workflows/toolbox.yml` (the CI build/push path for
# the toolbox images). Run it on `main` BEFORE cutting a release so the
# pinned `toolbox_images.<name>.digest` values track what is actually
# published on ghcr.io. `release.yml` refuses to publish a release manifest
# while any digest is null/missing. It also syncs the manifest's
# informational `version` field from pyproject.toml so it can't rot.
#
# For each entry under `toolbox_images`, the script:
#   1. parses the `.tag` field (usually `ghcr.io/hal0ai/<image>:<tag>`, but a
#      few entries — e.g. comfyui — pin a non-ghcr, digest-referenced image
#      like `docker.io/<owner>/<repo>@sha256:...`),
#   2. resolves the published content digest:
#        - a digest-pinned ref (`@sha256:...`) is authoritative BY
#          CONSTRUCTION — the digest is read straight out of the ref, no
#          registry round-trip;
#        - otherwise resolves from ghcr.io anonymously (registry v2 manifest
#          API), falling back to `docker buildx imagetools inspect` (works
#          against any registry, ghcr.io included) if the curl/token flow is
#          unavailable,
#   3. patches manifest.json in place via python3 so JSON formatting stays
#      stable.
#
# A ghcr.io image that fails resolution leaves its digest null and emits a
# warning — matching the runtime contract (null digest => pull-by-tag +
# warn); that image really is unpublished/unreachable. A NON-ghcr image that
# fails resolution (no authoritative path here beyond the two above) instead
# KEEPS whatever digest is already recorded and warns — a transient/offline
# lookup must never regress a previously-valid pin to null, which used to
# fail release.yml's null-digest gate on the very manifest this script
# prepares (#1676). The script never hard-fails on a single unresolved
# image; it exits non-zero only on a usage / environment error.
#
# Usage:
#   scripts/update-toolbox-digests.sh [path/to/manifest.json]
#
# Requirements: bash, python3, curl. Optional fallback: docker buildx.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MANIFEST="${1:-${REPO_ROOT}/manifest.json}"

if [[ ! -f "${MANIFEST}" ]]; then
    echo "error: manifest not found: ${MANIFEST}" >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 is required" >&2
    exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
    echo "error: curl is required" >&2
    exit 1
fi

ACCEPT_HEADER='application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json'

# Emit "<name>\t<tag>" for every toolbox image so we can loop over them.
list_images() {
    python3 - "${MANIFEST}" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1]))
for name, entry in (manifest.get("toolbox_images") or {}).items():
    tag = (entry or {}).get("tag") or ""
    print(f"{name}\t{tag}")
PY
}

# Resolve a ghcr.io content digest for "<registry>/<repo>:<reference>"
# anonymously. Prints the sha256 digest on stdout, or nothing on failure.
resolve_digest() {
    local image_ref="$1"
    local registry repo_ref repo reference token digest

    # A digest-pinned ref ("registry/repo@sha256:...") is authoritative by
    # construction — the digest IS the reference, no registry round-trip
    # needed (and none is possible for a non-ghcr registry anyway). This
    # also fixes the comfyui entry (a docker.io@sha256 pin): the split below
    # only understands a ":tag" suffix, so an "@sha256:..." ref used to mis-
    # parse into a bogus repo/reference pair, fail both the ghcr curl path
    # and the registry mismatch, and null out a perfectly valid pin (#1676).
    if [[ "${image_ref}" == *@sha256:* ]]; then
        printf 'sha256:%s\n' "${image_ref##*@sha256:}"
        return 0
    fi

    # Split "ghcr.io/owner/name:tag" into registry / repo / reference.
    registry="${image_ref%%/*}"
    repo_ref="${image_ref#*/}"
    if [[ "${repo_ref}" == *:* ]]; then
        repo="${repo_ref%:*}"
        reference="${repo_ref##*:}"
    else
        repo="${repo_ref}"
        reference="latest"
    fi

    if [[ "${registry}" != "ghcr.io" ]]; then
        echo "warn: ${image_ref}: only ghcr.io is supported by the curl path" >&2
    fi

    # ghcr.io hands out an anonymous pull token for public images.
    token="$(curl -fsSL \
        "https://ghcr.io/token?scope=repository:${repo}:pull&service=ghcr.io" \
        2>/dev/null | python3 -c \
        'import json,sys; print(json.load(sys.stdin).get("token",""))' \
        2>/dev/null || true)"

    if [[ -n "${token}" ]]; then
        # HEAD the manifest and read the Docker-Content-Digest response header.
        digest="$(curl -fsSI -X GET \
            -H "Authorization: Bearer ${token}" \
            -H "Accept: ${ACCEPT_HEADER}" \
            "https://${registry}/v2/${repo}/manifests/${reference}" \
            2>/dev/null \
            | tr -d '\r' \
            | awk -F': ' 'tolower($1)=="docker-content-digest"{print $2}' \
            | tail -n1 || true)"
        if [[ "${digest}" == sha256:* ]]; then
            printf '%s\n' "${digest}"
            return 0
        fi
    fi

    # Fallback: docker buildx imagetools inspect (handles auth/token quirks).
    if command -v docker >/dev/null 2>&1; then
        digest="$(docker buildx imagetools inspect "${image_ref}" 2>/dev/null \
            | awk -F': ' 'tolower($1)=="digest"{print $2}' \
            | head -n1 || true)"
        if [[ "${digest}" == sha256:* ]]; then
            printf '%s\n' "${digest}"
            return 0
        fi
    fi

    return 1
}

# Read the currently-recorded toolbox_images.<name>.digest, or "" if unset.
current_digest() {
    local name="$1"
    python3 - "${MANIFEST}" "${name}" <<'PY'
import json
import sys

path, name = sys.argv[1], sys.argv[2]
with open(path) as fh:
    manifest = json.load(fh)
entry = (manifest.get("toolbox_images") or {}).get(name) or {}
print(entry.get("digest") or "")
PY
}

# Patch a single toolbox_images.<name>.digest in place via python3.
patch_digest() {
    local name="$1" digest="$2"
    python3 - "${MANIFEST}" "${name}" "${digest}" <<'PY'
import json
import sys

path, name, digest = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as fh:
    manifest = json.load(fh)
entry = manifest.setdefault("toolbox_images", {}).setdefault(name, {})
entry["digest"] = digest if digest else None
with open(path, "w") as fh:
    json.dump(manifest, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
PY
}

# Sync the informational version field from pyproject.toml — the manifest
# had drifted several minors behind the release line before this existed.
sync_version() {
    python3 - "${MANIFEST}" "${REPO_ROOT}/pyproject.toml" <<'PY'
import json
import sys
import tomllib

manifest_path, pyproject_path = sys.argv[1], sys.argv[2]
with open(pyproject_path, "rb") as fh:
    version = tomllib.load(fh)["project"]["version"]
with open(manifest_path) as fh:
    manifest = json.load(fh)
if manifest.get("version") != version:
    manifest["version"] = version
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"  version: -> {version}")
PY
}

echo "Refreshing toolbox image digests in ${MANIFEST}"
sync_version

updated=0
warned=0
while IFS=$'\t' read -r name tag; do
    [[ -z "${name}" ]] && continue
    if [[ -z "${tag}" ]]; then
        echo "warn: ${name}: no tag in manifest — leaving digest null" >&2
        patch_digest "${name}" ""
        warned=$((warned + 1))
        continue
    fi

    echo "  ${name}: resolving ${tag}"
    if digest="$(resolve_digest "${tag}")" && [[ -n "${digest}" ]]; then
        patch_digest "${name}" "${digest}"
        echo "    -> ${digest}"
        updated=$((updated + 1))
    else
        # Never regress a valid pin to null. A ghcr.io image that fails
        # resolution really is unpublished/unreachable — null is correct,
        # the runtime falls back to pull-by-tag. A non-ghcr registry (no
        # authoritative resolution path here beyond the digest-pinned-ref
        # shortcut and the docker buildx fallback above) that fails is far
        # more likely a transient/offline lookup than an actual unpublish —
        # keep whatever digest is already recorded and warn instead (#1676).
        registry="${tag%%/*}"
        existing="$(current_digest "${name}")"
        if [[ "${registry}" != "ghcr.io" && -n "${existing}" ]]; then
            echo "warn: ${name}: ${tag} could not be resolved on ${registry} — keeping existing digest ${existing} (never regress a valid pin to null)" >&2
            patch_digest "${name}" "${existing}"
        else
            echo "warn: ${name}: ${tag} is unpublished or unreachable — leaving digest null (runtime pulls by tag)" >&2
            patch_digest "${name}" ""
        fi
        warned=$((warned + 1))
    fi
done < <(list_images)

echo "Done: ${updated} digest(s) updated, ${warned} left null."
echo "Review the diff (git diff ${MANIFEST}) and commit before cutting a release."
