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
#   HAL0_RELEASES_URL          full URL to a hal0.releases.v1 manifest
#                              (default: GitHub Releases /latest/download/stable.json)
#   HAL0_CHANNEL               channel name when using the default URL (default: stable)
#   HAL0_UPDATE_SKIP_COSIGN=1  skip cosign verify (only honored when cosign
#                              isn't installed; emits a loud warning)
#   HAL0_BOOTSTRAP_KEEP_TMP=1  don't delete the work directory on exit
#                              (debugging the unpacked tree)
#
# This script is the trust boundary for the one-line install — it never
# executes anything from the manifest or the tarball until cosign (or
# the documented skip) has verified the signature against the workflow
# OIDC identity in the manifest.
#
# Schema reference: docs/internal/release-manifest.md (hal0.releases.v1).

set -euo pipefail
IFS=$'\n\t'

HAL0_CHANNEL="${HAL0_CHANNEL:-stable}"
HAL0_RELEASES_URL="${HAL0_RELEASES_URL:-https://github.com/Hal0ai/hal0/releases/latest/download/${HAL0_CHANNEL}.json}"

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
    need python3
}

# ── manifest fetch + parse ────────────────────────────────────────────────
fetch_manifest() {
    local out="$1"
    info "fetching release manifest"
    info "  ${_C_DIM}${HAL0_RELEASES_URL}${_C_RST}"
    if ! curl -fsSL --retry 3 --retry-delay 2 -o "${out}" "${HAL0_RELEASES_URL}"; then
        die "could not download release manifest from ${HAL0_RELEASES_URL}"
    fi
}

parse_manifest_field() {
    local file="$1" field="$2"
    python3 -c "
import json, sys
try:
    v = json.load(open('${file}')).get('${field}')
    if v is None:
        sys.exit('manifest missing required field: ${field}')
    print(v)
except json.JSONDecodeError as e:
    sys.exit(f'manifest is not valid JSON: {e}')
"
}

# ── tarball fetch + sha256 verify ─────────────────────────────────────────
fetch_and_hash_check() {
    local url="$1" expected_digest="$2" out="$3"
    info "downloading tarball"
    info "  ${_C_DIM}${url}${_C_RST}"
    curl -fsSL --retry 3 --retry-delay 2 -o "${out}" "${url}" \
        || die "could not download tarball"

    info "verifying sha256"
    local actual
    actual="$(sha256sum "${out}" | awk '{print $1}')"
    if [[ "${actual}" != "${expected_digest}" ]]; then
        die "sha256 mismatch — expected ${expected_digest}, got ${actual}"
    fi
    ok "sha256 OK (${actual:0:12}…)"
}

# ── cosign verify (or documented skip) ────────────────────────────────────
fetch_sidecar() {
    local label="$1" url="$2" out="$3"
    info "downloading ${label}"
    info "  ${_C_DIM}${url}${_C_RST}"
    curl -fsSL --retry 3 --retry-delay 2 -o "${out}" "${url}" \
        || die "could not download ${label}"
}

cosign_verify() {
    local tarball="$1" sig="$2" cert="$3" identity="$4" issuer="$5"

    if ! command -v cosign >/dev/null 2>&1; then
        if [[ "${HAL0_UPDATE_SKIP_COSIGN:-0}" == "1" ]]; then
            warn "cosign not installed AND HAL0_UPDATE_SKIP_COSIGN=1 — skipping signature verify"
            warn "this is only safe if you trust the network path to ${HAL0_RELEASES_URL}"
            return 0
        fi
        die "cosign is required to verify the release signature.
   install it from https://docs.sigstore.dev/cosign/installation/, or
   re-run with HAL0_UPDATE_SKIP_COSIGN=1 to bypass (NOT recommended)"
    fi

    info "verifying signature with cosign keyless OIDC"
    info "  identity-regex: ${_C_DIM}${identity}${_C_RST}"
    info "  issuer:         ${_C_DIM}${issuer}${_C_RST}"
    # cosign 3.x requires the Fulcio-issued cert via --certificate
    # alongside the .sig; --certificate-identity-regexp is checked
    # against the cert's SAN.
    if ! cosign verify-blob \
            --signature "${sig}" \
            --certificate "${cert}" \
            --certificate-identity-regexp "${identity}" \
            --certificate-oidc-issuer "${issuer}" \
            "${tarball}" >/dev/null 2>&1; then
        die "cosign signature verification FAILED — refusing to install"
    fi
    ok "cosign verify OK"
}

# ── main ──────────────────────────────────────────────────────────────────
main() {
    banner
    preflight

    local work
    work="$(mktemp -d -t hal0-install-XXXXXX)"
    if [[ "${HAL0_BOOTSTRAP_KEEP_TMP:-0}" != "1" ]]; then
        trap 'rm -rf "${work}"' EXIT
    else
        warn "HAL0_BOOTSTRAP_KEEP_TMP=1 — leaving work dir ${work}"
    fi

    local manifest="${work}/manifest.json"
    fetch_manifest "${manifest}"

    local version url sig_url cert_url digest identity issuer
    version="$(parse_manifest_field "${manifest}" version)"
    url="$(parse_manifest_field "${manifest}" url)"
    sig_url="$(parse_manifest_field "${manifest}" sig_url)"
    cert_url="$(parse_manifest_field "${manifest}" cert_url)"
    digest="$(parse_manifest_field "${manifest}" digest_sha256)"
    identity="$(parse_manifest_field "${manifest}" signer_identity)"
    issuer="$(parse_manifest_field "${manifest}" signer_issuer)"

    info "release: ${_C_BLD}hal0 v${version}${_C_RST} (${HAL0_CHANNEL})"

    local tarball="${work}/hal0-${version}.tar.gz"
    local sig="${tarball}.sig"
    local cert="${tarball}.crt"
    fetch_and_hash_check "${url}" "${digest}" "${tarball}"
    fetch_sidecar "signature" "${sig_url}" "${sig}"
    fetch_sidecar "certificate" "${cert_url}" "${cert}"
    cosign_verify "${tarball}" "${sig}" "${cert}" "${identity}" "${issuer}"

    info "extracting tarball"
    tar -xzf "${tarball}" -C "${work}"
    local unpacked="${work}/hal0-${version}"
    [[ -x "${unpacked}/installer/install.sh" ]] \
        || die "extracted tree is missing installer/install.sh — corrupt tarball?"

    ok "ready — handing off to installer"
    printf '\n'

    # Pass through stdin so install.sh's interactive prompts work when
    # the user invoked us as `sudo bash install.sh`. When invoked via
    # curl|bash, stdin is closed and install.sh falls back to defaults.
    exec bash "${unpacked}/installer/install.sh" "$@"
}

main "$@"
