#!/usr/bin/env bash
#
# ⚠️  This file is MIRRORED between two locations and must stay identical:
#       - Hal0ai/hal0:installer/bootstrap.sh   (canonical)
#       - Hal0ai/hal0-web:public/install.sh    (served at https://hal0.dev/install.sh)
#     When you edit one, sync the other in the same PR.
#
# hal0 one-line installer — fetch, verify, unpack, hand off to install.sh.
#
# Designed to be piped from curl:
#
#   curl -fsSL https://hal0.dev/install.sh | sudo bash
#   curl -fsSL https://hal0.dev/install.sh | sudo bash -s -- --no-tls --models-dir=/data/models
#
# Or downloaded and run directly:
#
#   curl -fsSLO https://hal0.dev/install.sh
#   sudo bash install.sh
#
# Env overrides:
#   HAL0_RELEASES_URL           full URL to a hal0.releases.v1 manifest
#                               (default: https://releases.hal0.dev/<channel>.json)
#   HAL0_CHANNEL                stable, preview, or nightly (default: stable)
#   HAL0_BOOTSTRAP_KEEP_TMP=1   don't delete the work directory on exit
#                               (debugging the unpacked tree)
#
# This script is the trust boundary for the one-line install. It first verifies
# the exact channel-manifest bytes with their sibling Sigstore bundle and a
# client-pinned release-workflow identity. Only then does it parse artifact
# URLs. The tarball's sha256 and publisher signature are both checked again as
# defense-in-depth before anything is executed. cosign is therefore a required
# bootstrap dependency — see ensure_cosign() below, which uses a system cosign
# when one exists and otherwise fetches a digest-pinned official build into the
# throwaway work directory. See docs/internal/release-manifest.md.
#
# Schema reference: docs/internal/release-manifest.md (hal0.releases.v1).

set -euo pipefail
IFS=$'\n\t'

HAL0_CHANNEL="${HAL0_CHANNEL:-stable}"
HAL0_RELEASES_URL="${HAL0_RELEASES_URL:-}"

# Keep these trust roots in lockstep with src/hal0/updater/updater.py. The
# requested channel selects admission before any manifest JSON is parsed.
_MANIFEST_IDENTITY_PREFIX='^https://github\.com/(Hal0ai|hal0ai)/hal0/\.github/workflows/release\.yml@'
_STABLE_MANIFEST_ADMISSION_IDENTITY="${_MANIFEST_IDENTITY_PREFIX}refs/tags/v\\d+\\.\\d+\\.\\d+$"
_PREVIEW_MANIFEST_ADMISSION_IDENTITY="${_MANIFEST_IDENTITY_PREFIX}refs/tags/v\\d+\\.\\d+\\.\\d+(-(alpha|beta|rc)\\.(0|[1-9]\\d*))?$"
_NIGHTLY_MANIFEST_IDENTITY="${_MANIFEST_IDENTITY_PREFIX}refs/heads/main$"
_MANIFEST_SIGNER_ISSUER='https://token.actions.githubusercontent.com'

# ── pinned cosign (see ensure_cosign) ──────────────────────────────────────
#
# cosign is what turns "some bytes off a CDN" into "bytes signed by the hal0
# release workflow", so it is a hard requirement. Distros that package it
# (Arch, Fedora, Alpine, openSUSE, nixpkgs) are used as-is. Debian/Ubuntu do
# NOT package cosign — and rather than degrade to an unverified install on
# the single most common hal0 host, this script fetches the official sigstore
# release binary itself and checks it against a digest pinned right here.
#
# Pinning the digest in this file introduces NO new trust root. The user
# already trusted these exact bytes the moment they piped this script to
# bash; a constant inside a script you have already decided to execute
# cannot be less trustworthy than the script executing it. The resulting
# chain is:
#
#   trusted script -> digest-pinned cosign -> OIDC-pinned release manifest
#                  -> digest-pinned + signature-verified release tarball
#
# There is deliberately NO opt-out environment variable. A flag such as
# HAL0_INSTALL_REQUIRE_COSIGN=0 becomes the copy-pasted default in forum
# answers and CI snippets within a week, and silently un-does the signature
# hardening this file exists to provide. Unsupported platform, failed
# download, or digest mismatch all fail closed with manual-install guidance.
#
# ── MAINTENANCE: bump these three constants with the tool, never by hand ──
# scripts/update-cosign-pin.sh owns this pin end to end:
#
#   scripts/update-cosign-pin.sh                   # is the pin current?
#   scripts/update-cosign-pin.sh --bump            # verify + rewrite below
#
# It refuses to write a digest it has not authenticated. The release's
# cosign_checksums.txt is keyless-signed by sigstore
# (cosign_checksums.txt.sigstore.json on the same release); the script
# verifies that bundle with cosign — bootstrapping the verifier from the pin
# currently in this file when the host has none — before reading a digest
# out of it, then re-downloads each release binary and sha256sums it against
# the signed manifest. Any failure exits non-zero with these constants
# untouched. .github/workflows/cosign-pin.yml runs the check weekly and
# opens a review PR on drift; nothing auto-merges.
# tests/installer/test_bootstrap_cosign_fetch.py pins the shape of these
# constants so a malformed or half-finished bump fails CI.
_COSIGN_VERSION='v3.1.2'
_COSIGN_BASE_URL='https://github.com/sigstore/cosign/releases/download'
# Digests below are the published sha256 of the official release assets for
# _COSIGN_VERSION, taken from that release's cosign_checksums.txt.
_COSIGN_SHA256_LINUX_AMD64='f7622ed3cf22e55e1ae6377c080979ff77a22da9981c11df222a2e444991e7cf'
_COSIGN_SHA256_LINUX_ARM64='90e7ae0b5dfd60f20816b52c012addf7fc055ebcc7bea4ce81c428ca8518c302'

# Resolved exactly once by ensure_cosign(); every cosign invocation goes
# through it so a fetched binary and a system one are indistinguishable to
# the verification code below.
_COSIGN_BIN=""

# ── tiny output helpers ────────────────────────────────────────────────────
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    _C_DIM=$'\033[2m'; _C_RED=$'\033[31m'; _C_YEL=$'\033[33m'
    _C_GRN=$'\033[32m'; _C_BLD=$'\033[1m'; _C_RST=$'\033[0m'
else
    _C_DIM=""; _C_RED=""; _C_YEL=""; _C_GRN=""; _C_BLD=""; _C_RST=""
fi
info() { printf '%s» %s%s\n'   "${_C_DIM}" "$*" "${_C_RST}"; }
ok()   { printf '%s✓ %s%s\n'   "${_C_GRN}" "$*" "${_C_RST}"; }
warn() { printf '%s! %s%s\n'   "${_C_YEL}" "$*" "${_C_RST}" >&2; }
err()  { printf '%s✗ %s%s\n'   "${_C_RED}" "$*" "${_C_RST}" >&2; }
die()  { err "$*"; exit 1; }

_BOOTSTRAP_WORK=""
cleanup_workdir() {
    if [[ -n "${_BOOTSTRAP_WORK}" ]]; then
        rm -rf -- "${_BOOTSTRAP_WORK}"
    fi
}

banner() {
    printf '\n%shal0%s — open-source home AI inference platform\n' "${_C_BLD}" "${_C_RST}"
    printf '%s%s%s\n\n' "${_C_DIM}" "https://hal0.dev" "${_C_RST}"
}

# ── preflight ──────────────────────────────────────────────────────────────
need() {
    command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1 — install it and re-run"
}

preflight() {
    [[ "$(uname -s)" == "Linux" ]] || die "hal0 only supports Linux right now (got $(uname -s))"
    need curl
    need tar
    need sha256sum
    need jq
    need python3
}

validate_channel() {
    case "${HAL0_CHANNEL}" in
        stable|preview|nightly) ;;
        *) die "HAL0_CHANNEL must be one of: stable, preview, nightly (got ${HAL0_CHANNEL})" ;;
    esac
}

# ── cosign acquisition ─────────────────────────────────────────────────────
# Map `uname -m` onto the sigstore release asset name. Fails closed: an
# architecture we hold no pinned digest for is an unsupported platform, not a
# reason to skip verification.
cosign_asset_for_machine() {
    case "$1" in
        x86_64|amd64)  printf 'cosign-linux-amd64\n' ;;
        aarch64|arm64) printf 'cosign-linux-arm64\n' ;;
        *) return 1 ;;
    esac
}

cosign_pinned_sha256() {
    case "$1" in
        cosign-linux-amd64) printf '%s\n' "${_COSIGN_SHA256_LINUX_AMD64}" ;;
        cosign-linux-arm64) printf '%s\n' "${_COSIGN_SHA256_LINUX_ARM64}" ;;
        *) return 1 ;;
    esac
}

cosign_manual_install_hint() {
    printf '%s' "\
   install cosign manually and re-run:
     Arch:    sudo pacman -S cosign
     Fedora:  sudo dnf install cosign
     Alpine:  sudo apk add cosign
     other:   https://docs.sigstore.dev/cosign/system_config/installation/"
}

# Resolve _COSIGN_BIN. Prefers a distro-packaged cosign (it rides the
# distro's own update track and needs no pin from us); otherwise downloads
# the pinned official build into the trap-guarded work dir, verifies its
# sha256 against the constant above, and uses it from there. Nothing is
# installed anywhere persistent.
ensure_cosign() {
    local work="$1"

    if command -v cosign >/dev/null 2>&1; then
        _COSIGN_BIN="cosign"
        info "using system cosign ($(command -v cosign))"
        return 0
    fi

    local machine asset expected out actual
    machine="$(uname -m)"
    if ! asset="$(cosign_asset_for_machine "${machine}")"; then
        die "cosign is required to verify the release, and hal0 pins no cosign
   build for this architecture (uname -m: ${machine}).
$(cosign_manual_install_hint)"
    fi
    expected="$(cosign_pinned_sha256 "${asset}")" \
        || die "internal error: no pinned cosign sha256 for ${asset}"

    out="${work}/cosign"
    info "cosign not found — fetching pinned ${_COSIGN_VERSION} (${asset})"
    info "  ${_C_DIM}${_COSIGN_BASE_URL}/${_COSIGN_VERSION}/${asset}${_C_RST}"
    if ! curl -fsSL --retry 3 --retry-delay 2 -o "${out}" \
            --url "${_COSIGN_BASE_URL}/${_COSIGN_VERSION}/${asset}"; then
        rm -f -- "${out}"
        die "could not download pinned cosign ${_COSIGN_VERSION} (${asset}).
$(cosign_manual_install_hint)"
    fi

    actual="$(sha256sum "${out}" | awk '{print $1}')"
    if [[ "${actual}" != "${expected}" ]]; then
        rm -f -- "${out}"
        die "pinned cosign sha256 mismatch — expected ${expected}, got ${actual}
   refusing to run an unverified cosign binary.
$(cosign_manual_install_hint)"
    fi

    chmod +x "${out}"
    # A hardened host may mount the temp filesystem noexec, which would
    # otherwise surface as an inscrutable verification failure later.
    if ! "${out}" version >/dev/null 2>&1; then
        rm -f -- "${out}"
        die "fetched cosign could not be executed from ${work}
   (is that filesystem mounted noexec? retry with TMPDIR=/var/tmp)
$(cosign_manual_install_hint)"
    fi
    _COSIGN_BIN="${out}"
    ok "pinned cosign ${_COSIGN_VERSION} sha256 OK (${actual:0:12}…)"
}

manifest_admission_identity() {
    case "$1" in
        stable) printf '%s\n' "${_STABLE_MANIFEST_ADMISSION_IDENTITY}" ;;
        preview) printf '%s\n' "${_PREVIEW_MANIFEST_ADMISSION_IDENTITY}" ;;
        nightly) printf '%s\n' "${_NIGHTLY_MANIFEST_IDENTITY}" ;;
        *) return 1 ;;
    esac
}

exact_manifest_identity() {
    local release_kind="$1" version="$2" escaped_version
    case "${release_kind}" in
        stable)
            [[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
            ;;
        preview)
            [[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+-(alpha|beta|rc)\.(0|[1-9][0-9]*)$ ]] \
                || return 1
            ;;
        nightly)
            [[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+-nightly\.([0-9]{8}|[0-9]{14})$ ]] \
                || return 1
            printf '%s\n' "${_NIGHTLY_MANIFEST_IDENTITY}"
            return 0
            ;;
        *) return 1 ;;
    esac
    escaped_version="${version//./\\.}"
    printf '%srefs/tags/v%s$\n' "${_MANIFEST_IDENTITY_PREFIX}" "${escaped_version}"
}

resolve_release_manifest_url() {
    if [[ -z "${HAL0_RELEASES_URL}" ]]; then
        HAL0_RELEASES_URL="https://releases.hal0.dev/${HAL0_CHANNEL}.json"
    fi
}

release_manifest_bundle_url() {
    python3 - "$1" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit

scheme, netloc, path, query, fragment = urlsplit(sys.argv[1])
print(urlunsplit((scheme, netloc, f"{path}.bundle", query, fragment)))
PY
}

# ── manifest fetch + authenticate + parse ─────────────────────────────────
fetch_manifest() {
    local out="$1"
    info "fetching release manifest"
    info "  ${_C_DIM}${HAL0_RELEASES_URL}${_C_RST}"
    if ! curl -fsSL --retry 3 --retry-delay 2 -o "${out}" --url "${HAL0_RELEASES_URL}"; then
        die "could not download release manifest from ${HAL0_RELEASES_URL}"
    fi
}

verify_release_manifest() {
    local manifest="$1" bundle="$2" identity="$3"
    [[ -n "${_COSIGN_BIN}" ]] \
        || die "cosign is required to verify the release manifest but is not available.
$(cosign_manual_install_hint)"

    info "verifying release manifest with pinned workflow identity"
    if ! "${_COSIGN_BIN}" verify-blob \
            --bundle "${bundle}" \
            --certificate-identity-regexp "${identity}" \
            --certificate-oidc-issuer "${_MANIFEST_SIGNER_ISSUER}" \
            "${manifest}" >/dev/null 2>&1; then
        die "release manifest signature verification FAILED — refusing to trust artifact URLs"
    fi
    ok "release manifest signature OK"
}

validate_manifest_for_channel() {
    local manifest="$1" requested_channel="$2" normalized="$3"

    # This is deliberately one fail-closed jq policy pass over the exact bytes
    # authenticated above. It emits a normalized manifest only when every
    # bootstrap-required field and channel/kind/stage relationship is valid.
    if ! jq -e -s \
            --arg requested "${requested_channel}" \
            --arg trusted_issuer "${_MANIFEST_SIGNER_ISSUER}" '
        def nonempty_string: type == "string" and length > 0;
        select(length == 1)
        | .[0]
        | select(
            type == "object"
            and ._schema == "hal0.releases.v1"
            and (.version | nonempty_string)
            and (.url | nonempty_string)
            and (.bundle_url | nonempty_string)
            and (.signer_identity | nonempty_string)
            and .signer_issuer == $trusted_issuer
            and (try (.digest_sha256 | test("^(sha256:)?[0-9A-Fa-f]{64}$")) catch false)
            and (.channel == "stable" or .channel == "preview" or .channel == "nightly")
            and (.release_kind == "stable" or .release_kind == "preview" or .release_kind == "nightly")
            and .channel == $requested
            and (
                ($requested == "stable" and .release_kind == "stable")
                or ($requested == "preview" and (.release_kind == "preview" or .release_kind == "stable"))
                or ($requested == "nightly" and .release_kind == "nightly")
            )
            and (
                (.release_kind == "preview" and (
                    .prerelease_stage == "alpha"
                    or .prerelease_stage == "beta"
                    or .prerelease_stage == "rc"
                ))
                or ((.release_kind == "stable" or .release_kind == "nightly") and .prerelease_stage == null)
            )
            and (
                (.release_kind == "stable"
                    and (try (.version | test("^[0-9]+\\.[0-9]+\\.[0-9]+$")) catch false))
                or (.release_kind == "preview" and (
                    (.prerelease_stage == "alpha" and
                        (try (.version | test("^[0-9]+\\.[0-9]+\\.[0-9]+-alpha\\.(0|[1-9][0-9]*)$")) catch false))
                    or (.prerelease_stage == "beta" and
                        (try (.version | test("^[0-9]+\\.[0-9]+\\.[0-9]+-beta\\.(0|[1-9][0-9]*)$")) catch false))
                    or (.prerelease_stage == "rc" and
                        (try (.version | test("^[0-9]+\\.[0-9]+\\.[0-9]+-rc\\.(0|[1-9][0-9]*)$")) catch false))
                ))
                or (.release_kind == "nightly" and
                    (try (.version | test("^[0-9]+\\.[0-9]+\\.[0-9]+-nightly\\.([0-9]{8}|[0-9]{14})$")) catch false))
            )
        )
        | .digest_sha256 |= (ascii_downcase | sub("^sha256:"; ""))
    ' "${manifest}" >"${normalized}"; then
        rm -f -- "${normalized}"
        die "authenticated release manifest failed strict policy validation"
    fi
}

parse_manifest_field() {
    local file="$1" field="$2"
    jq -er --arg field "${field}" '.[$field] | select(type == "string")' "${file}" \
        || die "validated release manifest field extraction failed: ${field}"
}

# ── tarball fetch + sha256 verify ─────────────────────────────────────────
fetch_and_hash_check() {
    local url="$1" expected_digest="$2" out="$3"
    info "downloading tarball"
    info "  ${_C_DIM}${url}${_C_RST}"
    curl -fsSL --retry 3 --retry-delay 2 -o "${out}" --url "${url}" \
        || die "could not download tarball"

    info "verifying sha256"
    local actual
    actual="$(sha256sum "${out}" | awk '{print $1}')"
    if [[ "${actual}" != "${expected_digest}" ]]; then
        die "sha256 mismatch — expected ${expected_digest}, got ${actual}"
    fi
    ok "sha256 OK (${actual:0:12}…)"
}

# ── tarball cosign verify (defense-in-depth) ───────────────────────────────
fetch_sidecar() {
    local label="$1" url="$2" out="$3"
    info "downloading ${label}"
    info "  ${_C_DIM}${url}${_C_RST}"
    curl -fsSL --retry 3 --retry-delay 2 -o "${out}" --url "${url}" \
        || die "could not download ${label}"
}

cosign_verify() {
    local tarball="$1" bundle="$2" identity="$3"

    [[ -n "${_COSIGN_BIN}" ]] \
        || die "cosign disappeared after release manifest verification — refusing to install"

    info "verifying signature with cosign keyless OIDC"
    info "  identity-regex: ${_C_DIM}${identity}${_C_RST}"
    info "  issuer:         ${_C_DIM}${_MANIFEST_SIGNER_ISSUER}${_C_RST}"

    # The authenticated manifest must provide a Sigstore bundle. Detached
    # signature/certificate sidecars are not an accepted bootstrap scheme.
    local -a verify_args=(--bundle "${bundle}")
    if ! "${_COSIGN_BIN}" verify-blob \
            "${verify_args[@]}" \
            --certificate-identity-regexp "${identity}" \
            --certificate-oidc-issuer "${_MANIFEST_SIGNER_ISSUER}" \
            "${tarball}" >/dev/null 2>&1; then
        die "cosign signature verification FAILED — refusing to install"
    fi
    ok "cosign verify OK"
}

# ── main ──────────────────────────────────────────────────────────────────
main() {
    validate_channel
    local admission_identity
    admission_identity="$(manifest_admission_identity "${HAL0_CHANNEL}")" \
        || die "could not derive manifest admission identity"
    banner
    preflight
    resolve_release_manifest_url

    local work
    work="$(mktemp -d -t hal0-install-XXXXXX)"
    if [[ "${HAL0_BOOTSTRAP_KEEP_TMP:-0}" != "1" ]]; then
        _BOOTSTRAP_WORK="${work}"
        trap cleanup_workdir EXIT
    else
        warn "HAL0_BOOTSTRAP_KEEP_TMP=1 — leaving work dir ${work}"
    fi

    # Resolve cosign before any release bytes are fetched: if we cannot get a
    # verifier we must not go on to download things we cannot verify.
    ensure_cosign "${work}"

    local manifest="${work}/manifest.json"
    local manifest_bundle="${work}/manifest.json.bundle"
    fetch_manifest "${manifest}"
    fetch_sidecar \
        "release manifest signature bundle" \
        "$(release_manifest_bundle_url "${HAL0_RELEASES_URL}")" \
        "${manifest_bundle}"
    verify_release_manifest "${manifest}" "${manifest_bundle}" "${admission_identity}"

    local validated_manifest="${work}/manifest.validated.json"
    validate_manifest_for_channel "${manifest}" "${HAL0_CHANNEL}" "${validated_manifest}"

    local version release_kind url bundle_url digest manifest_identity expected_identity
    version="$(parse_manifest_field "${validated_manifest}" version)"
    release_kind="$(parse_manifest_field "${validated_manifest}" release_kind)"
    url="$(parse_manifest_field "${validated_manifest}" url)"
    bundle_url="$(parse_manifest_field "${validated_manifest}" bundle_url)"
    digest="$(parse_manifest_field "${validated_manifest}" digest_sha256)"
    manifest_identity="$(parse_manifest_field "${validated_manifest}" signer_identity)"
    expected_identity="$(exact_manifest_identity "${release_kind}" "${version}")" \
        || die "validated release manifest has unsupported release identity policy"
    if [[ "${manifest_identity}" != "${expected_identity}" ]]; then
        die "authenticated release manifest signer_identity does not match exact release identity"
    fi
    if [[ "${admission_identity}" != "${expected_identity}" ]]; then
        verify_release_manifest "${manifest}" "${manifest_bundle}" "${expected_identity}"
    fi

    info "release: ${_C_BLD}hal0 v${version}${_C_RST} (${HAL0_CHANNEL})"

    # Manifest strings never become shell syntax or path components.
    local tarball="${work}/artifact.tar.gz"
    fetch_and_hash_check "${url}" "${digest}" "${tarball}"

    local bundle="${tarball}.bundle"
    fetch_sidecar "signature bundle" "${bundle_url}" "${bundle}"
    cosign_verify "${tarball}" "${bundle}" "${expected_identity}"

    info "extracting tarball"
    local unpacked="${work}/unpacked"
    mkdir "${unpacked}"
    tar -xzf "${tarball}" --strip-components=1 -C "${unpacked}"
    [[ -x "${unpacked}/installer/install.sh" ]] \
        || die "extracted tree is missing installer/install.sh — corrupt tarball?"

    ok "ready — handing off to installer"
    printf '\n'

    # Signal install.sh that this tree was sha256 + cosign-verified above,
    # so its release-verification gate lets us through without an explicit
    # HAL0_INSTALL_SKIP_VERIFY opt-out.
    export HAL0_BOOTSTRAP_VERIFIED=1

    # Pass through stdin so install.sh's interactive prompts work when
    # the user invoked us as `sudo bash install.sh`. When invoked via
    # curl|bash, stdin is closed and install.sh falls back to defaults.
    exec bash "${unpacked}/installer/install.sh" "$@"
}

main "$@"
