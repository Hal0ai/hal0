#!/usr/bin/env bash
# hal0 — pinned-cosign drift detector + verified bumper (issue #1363 follow-up).
#
# Owner: installer team.
# Triggered by: .github/workflows/cosign-pin.yml (weekly + dispatch),
#               and operators running locally to preview or apply a bump.
#
# installer/bootstrap.sh fetches a digest-pinned cosign when the host has
# none (see its "pinned cosign" block). Those three constants —
# _COSIGN_VERSION, _COSIGN_SHA256_LINUX_AMD64, _COSIGN_SHA256_LINUX_ARM64 —
# were maintained entirely by hand, which means they rot silently: nothing
# in the repo notices when sigstore cuts a release, and nothing re-checks
# that the digests still describe the bytes GitHub actually serves.
#
# This script is that missing mechanism. It never invents or trusts a
# digest it has not authenticated.
#
# ── chain of trust ──────────────────────────────────────────────────────
# Every digest this script is willing to write is backed end-to-end:
#
#   1. VERIFIER. cosign itself does the verifying. A system cosign is used
#      as-is. Otherwise we bootstrap one from the pin ALREADY IN THE FILE —
#      the digest a human reviewed and merged — and check its sha256 before
#      executing it. The automation therefore introduces no trust root that
#      bootstrap.sh did not already have. This is a ratchet: pin N verifies
#      the evidence for pin N+1.
#
#   2. EVIDENCE. Sigstore publishes `cosign_checksums.txt` on each release
#      plus a keyless Sigstore bundle `cosign_checksums.txt.sigstore.json`.
#      We fetch both over TLS from the official release host.
#
#   3. SIGNATURE. `cosign verify-blob` checks that bundle against the
#      checksums file, pinned to the sigstore release signing identity:
#
#          --certificate-identity   keyless@projectsigstore.iam.gserviceaccount.com
#          --certificate-oidc-issuer https://accounts.google.com
#
#      (Confirmed against the v3.1.2 bundle's Fulcio certificate SAN; this
#      is the identity sigstore documents for release verification. It is
#      NOT a GitHub Actions OIDC subject.) Verification also requires the
#      Rekor transparency-log inclusion proof carried in the bundle, so a
#      forged-but-unlogged signature fails. A hostile mirror, a hijacked
#      CDN edge, or a compromised release-asset upload cannot produce a
#      checksums file that passes this step.
#
#   4. DELIVERY. Only after the signature verifies do we read digests out
#      of the checksums file. We then download each release binary and
#      sha256sum it, confirming the bytes GitHub serves today match the
#      signed manifest — the exact operation bootstrap.sh performs on a
#      user's machine. This costs ~200 MB per run and has no opt-out flag;
#      a pin that is only "probably" right is the failure this exists to
#      prevent.
#
#   5. REVIEW. The rewrite lands on a branch and a pull request. Nothing
#      here merges anything. A security pin does not change on `main`
#      without a human approving the diff.
#
# ── fail-closed ─────────────────────────────────────────────────────────
# Every value is resolved and validated in a temp dir first. installer/
# bootstrap.sh is rewritten exactly once, atomically (tmpfile + rename),
# after all three constants are known good. Any failure at any stage —
# unreachable checksums file, missing bundle, bad signature, absent
# architecture, malformed digest, duplicated digest across architectures,
# served bytes that disagree with the signed manifest — exits non-zero
# with the file byte-identical to how it was found. There is no partial
# write and no placeholder.
#
# ── exit codes ──────────────────────────────────────────────────────────
#   0 — pins are current and verified (check mode), or were rewritten
#       and verified (--bump).
#   1 — check mode only: drift. The pins do not match the verified truth
#       for the target release. Never returned by --bump.
#   2 — operational error (bad arguments, no usable cosign, download
#       failure, signature failure, malformed or missing digest, refused
#       downgrade). Pins untouched.
#
# ── operator entry points ───────────────────────────────────────────────
#   scripts/update-cosign-pin.sh                     # check against latest
#   scripts/update-cosign-pin.sh --version v3.2.0    # check a specific tag
#   scripts/update-cosign-pin.sh --bump              # verify + rewrite pins
#   scripts/update-cosign-pin.sh --bump --version v3.2.0
#
# ⚠️  A successful --bump edits installer/bootstrap.sh, which is mirrored
# byte-for-byte by Hal0ai/hal0-web:public/install.sh (the live
# hal0.dev/install.sh). scripts/check-bootstrap-parity.sh will report drift
# until hal0-web is synced. The generated PR body says so.
#
# Requirements: bash, curl, python3, sha256sum. cosign is used if present;
# otherwise it is bootstrapped from the in-tree pin.

set -euo pipefail

# ── repo root resolution ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── exit-code constants ─────────────────────────────────────────────────
EXIT_OK=0
EXIT_DRIFT=1
EXIT_OPERROR=2

# ── sigstore constants ──────────────────────────────────────────────────
# Deliberately literal, not environment-overridable: an override is how a
# verification host quietly becomes attacker-controlled.
COSIGN_RELEASE_BASE_URL='https://github.com/sigstore/cosign/releases/download'
COSIGN_LATEST_API='https://api.github.com/repos/sigstore/cosign/releases/latest'
COSIGN_CHECKSUMS_ASSET='cosign_checksums.txt'
COSIGN_BUNDLE_ASSET='cosign_checksums.txt.sigstore.json'
COSIGN_SIGNER_IDENTITY='keyless@projectsigstore.iam.gserviceaccount.com'
COSIGN_SIGNER_ISSUER='https://accounts.google.com'

# Pinned architectures, in the order they are reported. Each maps a
# sigstore release asset onto the bootstrap.sh constant that holds it.
COSIGN_ASSETS=(cosign-linux-amd64 cosign-linux-arm64)
COSIGN_CONSTANTS=(_COSIGN_SHA256_LINUX_AMD64 _COSIGN_SHA256_LINUX_ARM64)

err() { printf '%s\n' "$*" >&2; }
info() { printf '%s\n' "$*" >&2; }

# ── argument parsing ────────────────────────────────────────────────────
MODE="check"
TARGET_VERSION=""
EXPLICIT_VERSION=0
BOOTSTRAP=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bump)
            MODE="bump"
            shift
            ;;
        --version)
            TARGET_VERSION="${2:-}"
            EXPLICIT_VERSION=1
            if [[ -z "${TARGET_VERSION}" ]]; then
                err "error: --version requires a release tag, e.g. v3.1.2"
                exit "${EXIT_OPERROR}"
            fi
            shift 2
            ;;
        -h|--help)
            sed -n '2,95p' "${BASH_SOURCE[0]}"
            exit "${EXIT_OK}"
            ;;
        -*)
            err "error: unknown argument '$1'"
            exit "${EXIT_OPERROR}"
            ;;
        *)
            if [[ -n "${BOOTSTRAP}" ]]; then
                err "error: unexpected extra argument '$1'"
                exit "${EXIT_OPERROR}"
            fi
            BOOTSTRAP="$1"
            shift
            ;;
    esac
done

BOOTSTRAP="${BOOTSTRAP:-${REPO_ROOT}/installer/bootstrap.sh}"

# ── preconditions ───────────────────────────────────────────────────────
for tool in awk curl grep python3 sed sha256sum; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        err "ERROR: ${tool} is required but not found on PATH."
        exit "${EXIT_OPERROR}"
    fi
done

if [[ ! -f "${BOOTSTRAP}" ]]; then
    err "ERROR: bootstrap script not found: ${BOOTSTRAP}"
    exit "${EXIT_OPERROR}"
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/hal0-cosign-pin-XXXXXX")"
cleanup() { rm -rf -- "${WORK}"; }
trap cleanup EXIT

# ── pin reader ──────────────────────────────────────────────────────────
# Anchored on the exact assignment shape tests/installer/
# test_bootstrap_cosign_fetch.py asserts, so a reformatted constant is a
# loud failure here rather than a silent no-op bump.
read_pin() {
    local name="$1" value
    value="$(sed -n "s/^${name}='\\([^']*\\)'\$/\\1/p" "${BOOTSTRAP}")"
    if [[ -z "${value}" || "$(printf '%s\n' "${value}" | wc -l)" -ne 1 ]]; then
        err "ERROR: could not read a single ${name}='...' assignment from ${BOOTSTRAP}"
        return 1
    fi
    printf '%s\n' "${value}"
}

is_release_tag() { [[ "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; }
is_sha256() { [[ "$1" =~ ^[0-9a-f]{64}$ ]]; }

CURRENT_VERSION="$(read_pin _COSIGN_VERSION)" || exit "${EXIT_OPERROR}"
CURRENT_BASE_URL="$(read_pin _COSIGN_BASE_URL)" || exit "${EXIT_OPERROR}"

if ! is_release_tag "${CURRENT_VERSION}"; then
    err "ERROR: in-tree _COSIGN_VERSION is not a release tag: ${CURRENT_VERSION}"
    exit "${EXIT_OPERROR}"
fi
if [[ "${CURRENT_BASE_URL}" != "${COSIGN_RELEASE_BASE_URL}" ]]; then
    err "ERROR: in-tree _COSIGN_BASE_URL is ${CURRENT_BASE_URL}, expected ${COSIGN_RELEASE_BASE_URL}"
    err "       refusing to bump a pin that points somewhere this script does not verify."
    exit "${EXIT_OPERROR}"
fi

CURRENT_DIGESTS=()
for constant in "${COSIGN_CONSTANTS[@]}"; do
    digest="$(read_pin "${constant}")" || exit "${EXIT_OPERROR}"
    if ! is_sha256 "${digest}"; then
        err "ERROR: in-tree ${constant} is not a lowercase sha256: ${digest}"
        exit "${EXIT_OPERROR}"
    fi
    CURRENT_DIGESTS+=("${digest}")
done

# ── step 1: the verifier ────────────────────────────────────────────────
# A system cosign is preferred (it rides its distro's own update track).
# Otherwise bootstrap one from the pin already in the file, checking its
# sha256 before executing it — pin N verifies the evidence for pin N+1.
COSIGN_BIN=""

ensure_verifier() {
    if command -v cosign >/dev/null 2>&1; then
        COSIGN_BIN="cosign"
        info "verifier: system cosign ($(command -v cosign))"
        return 0
    fi

    local machine asset expected out actual
    machine="$(uname -m)"
    case "${machine}" in
        x86_64|amd64)  asset="cosign-linux-amd64"; expected="${CURRENT_DIGESTS[0]}" ;;
        aarch64|arm64) asset="cosign-linux-arm64"; expected="${CURRENT_DIGESTS[1]}" ;;
        *)
            err "ERROR: no cosign on PATH and bootstrap.sh pins no build for ${machine}."
            err "       install cosign and re-run:"
            err "         https://docs.sigstore.dev/cosign/system_config/installation/"
            return 1
            ;;
    esac

    out="${WORK}/cosign"
    info "verifier: no system cosign — bootstrapping the in-tree pin ${CURRENT_VERSION} (${asset})"
    if ! curl -fsSL --retry 3 --retry-delay 2 -o "${out}" \
            --url "${COSIGN_RELEASE_BASE_URL}/${CURRENT_VERSION}/${asset}"; then
        rm -f -- "${out}"
        err "ERROR: could not download the in-tree pinned cosign ${CURRENT_VERSION} (${asset})."
        return 1
    fi

    actual="$(sha256sum "${out}" | awk '{print $1}')"
    if [[ "${actual}" != "${expected}" ]]; then
        rm -f -- "${out}"
        err "ERROR: in-tree pinned cosign sha256 mismatch — expected ${expected}, got ${actual}."
        err "       refusing to verify anything with an unverified binary."
        return 1
    fi

    chmod +x "${out}"
    if ! "${out}" version >/dev/null 2>&1; then
        rm -f -- "${out}"
        err "ERROR: bootstrapped cosign could not be executed from ${WORK}"
        err "       (is that filesystem mounted noexec? retry with TMPDIR=/var/tmp)"
        return 1
    fi
    COSIGN_BIN="${out}"
    info "verifier: bootstrapped cosign ${CURRENT_VERSION} sha256 OK (${actual:0:12}…)"
}

ensure_verifier || exit "${EXIT_OPERROR}"

# ── step 2: target release ──────────────────────────────────────────────
resolve_latest_version() {
    local body tag
    if ! body="$(curl -fsSL --retry 3 --retry-delay 2 \
            -H 'Accept: application/vnd.github+json' \
            --url "${COSIGN_LATEST_API}")"; then
        err "ERROR: could not query the latest sigstore/cosign release."
        return 1
    fi
    tag="$(printf '%s' "${body}" | python3 -c \
        'import json,sys; print((json.load(sys.stdin).get("tag_name") or "").strip())' \
        2>/dev/null || true)"
    if [[ -z "${tag}" ]]; then
        err "ERROR: sigstore/cosign latest-release response carried no tag_name."
        return 1
    fi
    printf '%s\n' "${tag}"
}

if [[ -z "${TARGET_VERSION}" ]]; then
    TARGET_VERSION="$(resolve_latest_version)" || exit "${EXIT_OPERROR}"
fi

if ! is_release_tag "${TARGET_VERSION}"; then
    err "ERROR: target version is not a vX.Y.Z release tag: ${TARGET_VERSION}"
    exit "${EXIT_OPERROR}"
fi

# A "latest" that is older than what we already pin means the API lied or
# something upstream was re-tagged. Never silently roll a security pin
# backwards; an operator who genuinely wants that passes --version.
if [[ "${EXPLICIT_VERSION}" -eq 0 ]]; then
    if ! python3 -c '
import sys

def parts(tag: str) -> tuple[int, ...]:
    return tuple(int(p) for p in tag.lstrip("v").split("."))

sys.exit(0 if parts(sys.argv[2]) >= parts(sys.argv[1]) else 1)
' "${CURRENT_VERSION}" "${TARGET_VERSION}"; then
        err "ERROR: resolved latest release ${TARGET_VERSION} is older than the pin ${CURRENT_VERSION}."
        err "       refusing to roll the pin backwards. Pass --version to override deliberately."
        exit "${EXIT_OPERROR}"
    fi
fi

info "target: sigstore/cosign ${TARGET_VERSION} (in tree: ${CURRENT_VERSION})"

# ── step 3: signed evidence ─────────────────────────────────────────────
CHECKSUMS="${WORK}/${COSIGN_CHECKSUMS_ASSET}"
BUNDLE="${WORK}/${COSIGN_BUNDLE_ASSET}"

if ! curl -fsSL --retry 3 --retry-delay 2 -o "${CHECKSUMS}" \
        --url "${COSIGN_RELEASE_BASE_URL}/${TARGET_VERSION}/${COSIGN_CHECKSUMS_ASSET}"; then
    err "ERROR: could not download ${COSIGN_CHECKSUMS_ASSET} for ${TARGET_VERSION}."
    exit "${EXIT_OPERROR}"
fi
if [[ ! -s "${CHECKSUMS}" ]]; then
    err "ERROR: ${COSIGN_CHECKSUMS_ASSET} for ${TARGET_VERSION} is empty."
    exit "${EXIT_OPERROR}"
fi

if ! curl -fsSL --retry 3 --retry-delay 2 -o "${BUNDLE}" \
        --url "${COSIGN_RELEASE_BASE_URL}/${TARGET_VERSION}/${COSIGN_BUNDLE_ASSET}"; then
    err "ERROR: could not download ${COSIGN_BUNDLE_ASSET} for ${TARGET_VERSION}."
    err "       the checksums file is unsigned evidence without it — refusing to read digests."
    exit "${EXIT_OPERROR}"
fi
if [[ ! -s "${BUNDLE}" ]]; then
    err "ERROR: ${COSIGN_BUNDLE_ASSET} for ${TARGET_VERSION} is empty."
    exit "${EXIT_OPERROR}"
fi

# cosign >= 2.4 gates the `.sigstore.json` bundle format behind
# --new-bundle-format; cosign 3.x accepts it too. Probe rather than guess,
# so the script works with whatever cosign the host or the ratchet supplied.
VERIFY_ARGS=(
    verify-blob
    --bundle "${BUNDLE}"
    --certificate-identity "${COSIGN_SIGNER_IDENTITY}"
    --certificate-oidc-issuer "${COSIGN_SIGNER_ISSUER}"
)
if "${COSIGN_BIN}" verify-blob --help 2>&1 | grep -q -- '--new-bundle-format'; then
    VERIFY_ARGS+=(--new-bundle-format)
fi

info "verifying ${COSIGN_CHECKSUMS_ASSET} signature"
info "  identity: ${COSIGN_SIGNER_IDENTITY}"
info "  issuer:   ${COSIGN_SIGNER_ISSUER}"
if ! "${COSIGN_BIN}" "${VERIFY_ARGS[@]}" "${CHECKSUMS}" >"${WORK}/verify.log" 2>&1; then
    err "ERROR: signature verification FAILED for ${COSIGN_CHECKSUMS_ASSET} @ ${TARGET_VERSION}."
    err "       refusing to read a digest out of an unauthenticated checksums file."
    sed 's/^/       | /' "${WORK}/verify.log" >&2 || true
    exit "${EXIT_OPERROR}"
fi
info "signature OK"

# ── step 4: authenticated digests ───────────────────────────────────────
# Only reached once the checksums file is signed evidence.
checksum_for_asset() {
    local asset="$1" line count
    count="$(grep -cE "^[0-9a-fA-F]{64}[[:space:]]+\\*?${asset}\$" "${CHECKSUMS}" || true)"
    if [[ "${count}" -eq 0 ]]; then
        err "ERROR: ${COSIGN_CHECKSUMS_ASSET} @ ${TARGET_VERSION} lists no entry for ${asset}."
        err "       the release dropped an architecture hal0 pins — refusing to bump."
        return 1
    fi
    if [[ "${count}" -ne 1 ]]; then
        err "ERROR: ${COSIGN_CHECKSUMS_ASSET} @ ${TARGET_VERSION} lists ${asset} ${count} times."
        return 1
    fi
    line="$(grep -E "^[0-9a-fA-F]{64}[[:space:]]+\\*?${asset}\$" "${CHECKSUMS}")"
    printf '%s\n' "${line%%[[:space:]]*}"
}

TARGET_DIGESTS=()
for i in "${!COSIGN_ASSETS[@]}"; do
    asset="${COSIGN_ASSETS[$i]}"
    digest="$(checksum_for_asset "${asset}")" || exit "${EXIT_OPERROR}"
    if ! is_sha256 "${digest}"; then
        err "ERROR: ${asset} @ ${TARGET_VERSION} has a malformed sha256: ${digest}"
        err "       expected 64 lowercase hex characters."
        exit "${EXIT_OPERROR}"
    fi
    TARGET_DIGESTS+=("${digest}")
done

# Two architectures sharing a digest means the upstream manifest is wrong
# or we parsed the wrong lines. tests/installer/test_bootstrap_cosign_fetch.py
# asserts the same invariant on the committed file; catch it before writing.
if [[ "${TARGET_DIGESTS[0]}" == "${TARGET_DIGESTS[1]}" ]]; then
    err "ERROR: amd64 and arm64 resolved to the same digest (${TARGET_DIGESTS[0]})."
    err "       refusing to write a pin that cannot be right."
    exit "${EXIT_OPERROR}"
fi

# ── step 5: confirm delivery ────────────────────────────────────────────
# The signed manifest says what the bytes should be. This confirms that is
# what GitHub actually serves — the same download+sha256sum bootstrap.sh
# performs on a user's machine. No opt-out: a pin nobody re-derived is the
# failure mode this script exists to remove.
for i in "${!COSIGN_ASSETS[@]}"; do
    asset="${COSIGN_ASSETS[$i]}"
    expected="${TARGET_DIGESTS[$i]}"
    blob="${WORK}/${asset}"
    info "confirming served bytes for ${asset}"
    if ! curl -fsSL --retry 3 --retry-delay 2 -o "${blob}" \
            --url "${COSIGN_RELEASE_BASE_URL}/${TARGET_VERSION}/${asset}"; then
        err "ERROR: could not download ${asset} @ ${TARGET_VERSION} to confirm its digest."
        exit "${EXIT_OPERROR}"
    fi
    actual="$(sha256sum "${blob}" | awk '{print $1}')"
    if [[ "${actual}" != "${expected}" ]]; then
        err "ERROR: served ${asset} @ ${TARGET_VERSION} does not match the SIGNED checksum."
        err "       signed:  ${expected}"
        err "       served:  ${actual}"
        err "       this is a release-asset integrity failure, not a bump — do not proceed."
        exit "${EXIT_OPERROR}"
    fi
    rm -f -- "${blob}"
done
info "served bytes match the signed manifest for all pinned architectures"

# ── step 6: report ──────────────────────────────────────────────────────
# Markdown on stdout, suitable verbatim as a PR/issue body (hermes-sdk-diff
# idiom). Diagnostics stay on stderr so the two never interleave.
DRIFT=0
if [[ "${TARGET_VERSION}" != "${CURRENT_VERSION}" ]]; then
    DRIFT=1
fi
for i in "${!COSIGN_CONSTANTS[@]}"; do
    if [[ "${TARGET_DIGESTS[$i]}" != "${CURRENT_DIGESTS[$i]}" ]]; then
        DRIFT=1
    fi
done

{
    if [[ "${DRIFT}" -eq 0 ]]; then
        echo "## Pinned cosign is current"
        echo ""
        echo "\`installer/bootstrap.sh\` pins sigstore/cosign \`${TARGET_VERSION}\`, which is"
        echo "the target release, and both pinned digests re-verified against the"
        echo "release's signed \`${COSIGN_CHECKSUMS_ASSET}\`."
    else
        echo "## Pinned cosign drift: \`${CURRENT_VERSION}\` → \`${TARGET_VERSION}\`"
        echo ""
        echo "| constant | in tree | verified for \`${TARGET_VERSION}\` |"
        echo "| --- | --- | --- |"
        echo "| \`_COSIGN_VERSION\` | \`${CURRENT_VERSION}\` | \`${TARGET_VERSION}\` |"
        for i in "${!COSIGN_CONSTANTS[@]}"; do
            echo "| \`${COSIGN_CONSTANTS[$i]}\` | \`${CURRENT_DIGESTS[$i]}\` | \`${TARGET_DIGESTS[$i]}\` |"
        done
    fi
    echo ""
    echo "### Chain of trust"
    echo ""
    echo "- Digests read from \`${COSIGN_CHECKSUMS_ASSET}\` on the sigstore/cosign"
    echo "  \`${TARGET_VERSION}\` release, **after** \`cosign verify-blob\` accepted"
    echo "  \`${COSIGN_BUNDLE_ASSET}\` for it against identity"
    echo "  \`${COSIGN_SIGNER_IDENTITY}\` / issuer \`${COSIGN_SIGNER_ISSUER}\`."
    echo "- Each pinned release binary was then downloaded and \`sha256sum\`-ed;"
    echo "  the served bytes match the signed manifest."
    echo "- The verifier was \`${COSIGN_BIN}\`."
} > "${WORK}/report.md"

cat "${WORK}/report.md"

if [[ "${MODE}" == "check" ]]; then
    if [[ "${DRIFT}" -eq 0 ]]; then
        exit "${EXIT_OK}"
    fi
    exit "${EXIT_DRIFT}"
fi

# ── step 7: atomic rewrite ──────────────────────────────────────────────
if [[ "${DRIFT}" -eq 0 ]]; then
    info "no drift — ${BOOTSTRAP} left untouched"
    exit "${EXIT_OK}"
fi

if ! python3 - "${BOOTSTRAP}" "${TARGET_VERSION}" "${TARGET_DIGESTS[0]}" "${TARGET_DIGESTS[1]}" <<'PY'
import os
import re
import stat
import sys

path, version, amd64, arm64 = sys.argv[1:5]

# Re-assert the shapes here too: this is the last gate before bytes that
# vouch for a downloaded cosign change on disk.
if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
    raise SystemExit(f"error: refusing to write malformed version {version!r}")
for digest in (amd64, arm64):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SystemExit(f"error: refusing to write malformed digest {digest!r}")
if amd64 == arm64:
    raise SystemExit("error: refusing to write identical digests for both architectures")

with open(path, encoding="utf-8") as fh:
    original = fh.read()

updated = original
for name, value in (
    ("_COSIGN_VERSION", version),
    ("_COSIGN_SHA256_LINUX_AMD64", amd64),
    ("_COSIGN_SHA256_LINUX_ARM64", arm64),
):
    pattern = re.compile(rf"^{name}='[^']*'$", re.MULTILINE)
    found = pattern.findall(updated)
    if len(found) != 1:
        raise SystemExit(
            f"error: expected exactly one {name}='...' assignment in {path}, found {len(found)}"
        )
    updated = pattern.sub(lambda _m, n=name, v=value: f"{n}='{v}'", updated)

if updated == original:
    raise SystemExit(f"error: rewrite produced no change in {path}")

# Atomic: a crash mid-write leaves the reviewed pin in place, never a
# half-written constant.
tmp = f"{path}.hal0-cosign-pin.tmp"
mode = stat.S_IMODE(os.stat(path).st_mode)
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write(updated)
os.chmod(tmp, mode)
os.replace(tmp, path)
PY
then
    err "ERROR: failed to rewrite ${BOOTSTRAP} — pins left as found."
    rm -f -- "${BOOTSTRAP}.hal0-cosign-pin.tmp"
    exit "${EXIT_OPERROR}"
fi

# Re-read what we just wrote and re-validate it. If bash -n cannot parse
# the result, or the constants did not land, the bump is a failure even
# though the write "succeeded".
if ! bash -n "${BOOTSTRAP}"; then
    err "ERROR: ${BOOTSTRAP} no longer parses after the rewrite."
    exit "${EXIT_OPERROR}"
fi
for i in "${!COSIGN_CONSTANTS[@]}"; do
    written="$(read_pin "${COSIGN_CONSTANTS[$i]}")" || exit "${EXIT_OPERROR}"
    if [[ "${written}" != "${TARGET_DIGESTS[$i]}" ]]; then
        err "ERROR: ${COSIGN_CONSTANTS[$i]} did not land (${written})."
        exit "${EXIT_OPERROR}"
    fi
done
written="$(read_pin _COSIGN_VERSION)" || exit "${EXIT_OPERROR}"
if [[ "${written}" != "${TARGET_VERSION}" ]]; then
    err "ERROR: _COSIGN_VERSION did not land (${written})."
    exit "${EXIT_OPERROR}"
fi

info "bumped ${BOOTSTRAP}: ${CURRENT_VERSION} -> ${TARGET_VERSION}"
info "⚠️  installer/bootstrap.sh is mirrored by Hal0ai/hal0-web:public/install.sh —"
info "    sync that file or scripts/check-bootstrap-parity.sh will report drift."
exit "${EXIT_OK}"
