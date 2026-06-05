#!/usr/bin/env bash
#
# update-toolbox-digests.sh — refresh the published image digests pinned in
# the repo-root manifest.json under `toolbox_images`.
#
# This is the real, runnable replacement for the never-built
# `.github/workflows/toolbox.yml`. Run it on `main` BEFORE cutting a release
# so the pinned `toolbox_images.<name>.digest` values track what is actually
# published on ghcr.io. `release.yml` refuses to publish a release manifest
# while any digest is null/missing.
#
# For each entry under `toolbox_images`, the script:
#   1. parses `ghcr.io/hal0ai/<image>:<tag>` out of the `.tag` field,
#   2. resolves the published content digest from ghcr.io anonymously
#      (registry v2 manifest API; falls back to `docker buildx imagetools
#      inspect` if the curl/token flow is unavailable),
#   3. patches manifest.json in place via python3 so JSON formatting stays
#      stable.
#
# A missing/unpublished image leaves its digest as null and emits a warning —
# matching the runtime contract (null digest => pull-by-tag + warn). The
# script never hard-fails on a single unpublished image; it exits non-zero
# only on a usage / environment error.
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

echo "Refreshing toolbox image digests in ${MANIFEST}"

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
        echo "warn: ${name}: ${tag} is unpublished or unreachable — leaving digest null (runtime pulls by tag)" >&2
        patch_digest "${name}" ""
        warned=$((warned + 1))
    fi
done < <(list_images)

echo "Done: ${updated} digest(s) updated, ${warned} left null."
echo "Review the diff (git diff ${MANIFEST}) and commit before cutting a release."
