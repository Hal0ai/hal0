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
# bootstrap dependency. See docs/internal/release-manifest.md.
#
# Schema reference: docs/internal/release-manifest.md (hal0.releases.v1).

set -euo pipefail
IFS=$'\n\t'

HAL0_CHANNEL="${HAL0_CHANNEL:-stable}"
HAL0_RELEASES_URL="${HAL0_RELEASES_URL:-}"

# Keep this trust root in lockstep with src/hal0/updater/updater.py. These
# values authenticate the channel pointer itself; signer claims inside that
# unauthenticated JSON are trusted only after this verification succeeds.
_MANIFEST_SIGNER_IDENTITY_REGEXP='^https://github\.com/(Hal0ai|hal0ai)/hal0/\.github/workflows/release\.yml@(refs/tags/v\d+\.\d+\.\d+(-(alpha|beta|rc)\.(0|[1-9]\d*)|-nightly\.\d{14})?|refs/heads/main)$'
_MANIFEST_SIGNER_ISSUER='https://token.actions.githubusercontent.com'

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

validate_channel() {
    case "${HAL0_CHANNEL}" in
        stable|preview|nightly) ;;
        *) die "HAL0_CHANNEL must be one of: stable, preview, nightly (got ${HAL0_CHANNEL})" ;;
    esac
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
    if ! curl -fsSL --retry 3 --retry-delay 2 -o "${out}" "${HAL0_RELEASES_URL}"; then
        die "could not download release manifest from ${HAL0_RELEASES_URL}"
    fi
}

verify_release_manifest() {
    local manifest="$1" bundle="$2"
    command -v cosign >/dev/null 2>&1 \
        || die "cosign is required to verify the release manifest but is not installed.
   install it from https://docs.sigstore.dev/cosign/installation/"

    info "verifying release manifest with pinned workflow identity"
    if ! cosign verify-blob \
            --bundle "${bundle}" \
            --certificate-identity-regexp "${_MANIFEST_SIGNER_IDENTITY_REGEXP}" \
            --certificate-oidc-issuer "${_MANIFEST_SIGNER_ISSUER}" \
            "${manifest}" >/dev/null 2>&1; then
        die "release manifest signature verification FAILED — refusing to trust artifact URLs"
    fi
    ok "release manifest signature OK"
}

validate_manifest_for_channel() {
    python3 - "$1" "$2" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as manifest_file:
    manifest = json.load(manifest_file)
requested_channel = sys.argv[2]
manifest_channel = manifest.get("channel", "stable")
release_kind = manifest.get("release_kind", "stable")
accepted_kinds = {
    "stable": {"stable"},
    "preview": {"preview", "stable"},
    "nightly": {"nightly"},
}
if manifest_channel != requested_channel:
    sys.exit(
        f"manifest channel {manifest_channel} does not match requested channel "
        f"{requested_channel}"
    )
if release_kind not in accepted_kinds[requested_channel]:
    sys.exit(
        f"release kind {release_kind} is not accepted for channel {requested_channel}"
    )
PY
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

# Like parse_manifest_field but prints nothing (rather than dying) when the
# field is absent — used for the transition-window bundle_url/sig_url/
# cert_url fields, which are mutually optional (see cosign_verify below).
parse_manifest_field_optional() {
    local file="$1" field="$2"
    python3 -c "
import json
v = json.load(open('${file}')).get('${field}')
print(v if v is not None else '')
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

# ── tarball cosign verify (defense-in-depth) ───────────────────────────────
fetch_sidecar() {
    local label="$1" url="$2" out="$3"
    info "downloading ${label}"
    info "  ${_C_DIM}${url}${_C_RST}"
    curl -fsSL --retry 3 --retry-delay 2 -o "${out}" "${url}" \
        || die "could not download ${label}"
}

cosign_verify() {
    # bundle may be empty — in that case sig and cert must both be set (the
    # transition-window fallback for manifests without bundle_url). See main().
    local tarball="$1" bundle="$2" sig="$3" cert="$4" identity="$5" issuer="$6"

    command -v cosign >/dev/null 2>&1 \
        || die "cosign disappeared after release manifest verification — refusing to install"

    info "verifying signature with cosign keyless OIDC"
    info "  identity-regex: ${_C_DIM}${identity}${_C_RST}"
    info "  issuer:         ${_C_DIM}${issuer}${_C_RST}"

    local -a verify_args
    if [[ -n "${bundle}" ]]; then
        # Keyless verification uses a Sigstore bundle. The bundle carries the
        # Fulcio cert, the signature, AND the Rekor Signed Entry Timestamp
        # (SET) — the trusted timestamp that lets verify-blob succeed after
        # the short-lived (~10 min) signing cert has expired, which is
        # always the case by the time a user runs the installer.
        # --certificate-identity-regexp is matched against the cert SAN
        # carried in the bundle. (A detached .sig + .crt had no SET and
        # failed on every client — #1159.)
        verify_args=(--bundle "${bundle}")
    else
        # Transition-window fallback: manifest had no bundle_url (older
        # release, or a manifest generated before #1159 shipped). This path
        # inherits the known post-expiry failure the bundle fixes — kept
        # only so manifests without bundle_url still attempt verification
        # instead of erroring outright.
        verify_args=(--signature "${sig}" --certificate "${cert}")
    fi
    if ! cosign verify-blob \
            "${verify_args[@]}" \
            --certificate-identity-regexp "${identity}" \
            --certificate-oidc-issuer "${issuer}" \
            "${tarball}" >/dev/null 2>&1; then
        die "cosign signature verification FAILED — refusing to install"
    fi
    ok "cosign verify OK"
}

# ── main ──────────────────────────────────────────────────────────────────
main() {
    validate_channel
    banner
    preflight
    resolve_release_manifest_url

    local work
    work="$(mktemp -d -t hal0-install-XXXXXX)"
    if [[ "${HAL0_BOOTSTRAP_KEEP_TMP:-0}" != "1" ]]; then
        trap "rm -rf '${work}'" EXIT
    else
        warn "HAL0_BOOTSTRAP_KEEP_TMP=1 — leaving work dir ${work}"
    fi

    local manifest="${work}/manifest.json"
    local manifest_bundle="${work}/manifest.json.bundle"
    fetch_manifest "${manifest}"
    fetch_sidecar \
        "release manifest signature bundle" \
        "$(release_manifest_bundle_url "${HAL0_RELEASES_URL}")" \
        "${manifest_bundle}"
    verify_release_manifest "${manifest}" "${manifest_bundle}"
    validate_manifest_for_channel "${manifest}" "${HAL0_CHANNEL}"

    local version url bundle_url sig_url cert_url digest identity issuer
    version="$(parse_manifest_field "${manifest}" version)"
    url="$(parse_manifest_field "${manifest}" url)"
    bundle_url="$(parse_manifest_field_optional "${manifest}" bundle_url)"
    sig_url="$(parse_manifest_field_optional "${manifest}" sig_url)"
    cert_url="$(parse_manifest_field_optional "${manifest}" cert_url)"
    digest="$(parse_manifest_field "${manifest}" digest_sha256)"
    identity="$(parse_manifest_field "${manifest}" signer_identity)"
    issuer="$(parse_manifest_field "${manifest}" signer_issuer)"

    if [[ -z "${bundle_url}" && ( -z "${sig_url}" || -z "${cert_url}" ) ]]; then
        die "manifest has no usable signing scheme (need bundle_url, or both sig_url and cert_url)"
    fi

    info "release: ${_C_BLD}hal0 v${version}${_C_RST} (${HAL0_CHANNEL})"

    local tarball="${work}/hal0-${version}.tar.gz"
    fetch_and_hash_check "${url}" "${digest}" "${tarball}"

    # Prefer the Sigstore bundle (survives cert expiry, #1159); fall back to
    # the transition-window sig_url/cert_url pair when bundle_url is absent.
    local bundle="" sig="" cert=""
    if [[ -n "${bundle_url}" ]]; then
        bundle="${tarball}.bundle"
        fetch_sidecar "signature bundle" "${bundle_url}" "${bundle}"
    else
        sig="${tarball}.sig"
        cert="${tarball}.crt"
        fetch_sidecar "signature" "${sig_url}" "${sig}"
        fetch_sidecar "certificate" "${cert_url}" "${cert}"
    fi
    cosign_verify "${tarball}" "${bundle}" "${sig}" "${cert}" "${identity}" "${issuer}"

    info "extracting tarball"
    tar -xzf "${tarball}" -C "${work}"
    local unpacked="${work}/hal0-${version}"
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
