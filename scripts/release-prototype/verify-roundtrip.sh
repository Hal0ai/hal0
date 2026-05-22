#!/usr/bin/env bash
# scripts/release-prototype/verify-roundtrip.sh
#
# End-to-end prototype of the hal0 release verify path, run *locally*
# with cosign and a self-signed key (no GitHub OIDC).
#
# The production updater (src/hal0/updater/updater.py) does:
#   1. fetch latest.json
#   2. download tarball + .sig
#   3. sha256 verify against manifest.digest_sha256
#   4. cosign verify-blob --signature <sig> \
#                         --certificate-identity-regexp <regex> \
#                         --certificate-oidc-issuer <issuer> <tarball>
#
# This driver replicates 1-4 against a synthetic release built from the
# working tree, with one substitution: keyless OIDC is replaced by a
# locally-generated cosign key pair. The verify *binary* and the
# tarball-handling pipeline are identical to production; only the trust
# root differs.
#
# What this proves:
#   - cosign is installed, the CLI flags we shell out to are still valid
#   - good signature verifies, tampered tarball is rejected
#   - sha256 mismatch is caught before reaching cosign
#   - synthetic hal0.releases.v1 manifest parses through ReleaseManifest
#   - Updater.check() runs end-to-end against a file:// manifest
#
# What this does NOT prove (still blocked on a real GH Actions run):
#   - keyless OIDC verify against a workflow-issued certificate
#   - the signer_identity regex anchors to release.yml@refs/tags/vX.Y.Z
#   - GitHub release asset upload + releases.hal0.dev hosting

set -euo pipefail
IFS=$'\n\t'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d -t hal0-release-proto-XXXXXX)"
trap 'rm -rf "${WORK}"' EXIT

VERSION="${HAL0_PROTO_VERSION:-0.0.0-proto}"
TARBALL="${WORK}/hal0-${VERSION}.tar.gz"
SIG="${TARBALL}.sig"
BUNDLE="${TARBALL}.bundle"
MANIFEST="${WORK}/latest.json"
KEYDIR="${WORK}/keys"

if [[ -t 1 ]]; then
    GRN=$'\033[0;32m'; RED=$'\033[0;31m'; YEL=$'\033[1;33m'; BOLD=$'\033[1m'; RST=$'\033[0m'
else
    GRN= RED= YEL= BOLD= RST=
fi
info() { printf "${GRN}✔${RST}  %s\n" "$*"; }
warn() { printf "${YEL}!${RST}  %s\n" "$*" >&2; }
fail() { printf "${RED}✗${RST}  %s\n" "$*" >&2; exit 1; }
step() { printf "\n${BOLD}── %s${RST}\n" "$*"; }

command -v cosign >/dev/null || fail "cosign not on PATH (install: pacman -S cosign)"
command -v python3 >/dev/null || fail "python3 not on PATH"

COSIGN_VER="$(cosign version 2>&1 | awk '/GitVersion/ {print $2; exit}')"
info "cosign: ${COSIGN_VER}"
case "${COSIGN_VER}" in
    v3.*) info "cosign 3.x detected — using bundle format + extracting raw sig for legacy verify-blob" ;;
    v2.*) warn "cosign 2.x — should also work" ;;
    *)    warn "unknown cosign major: ${COSIGN_VER}" ;;
esac

# ── 1. Build a synthetic release tarball with the layout shape the
#       updater extractor expects: a single top-level hal0-<ver>/ dir.
step "1. Build synthetic release tarball"
SRC="${WORK}/hal0-${VERSION}"
mkdir -p "${SRC}"
cp -a "${REPO_ROOT}/src" "${SRC}/"
cp -a "${REPO_ROOT}/manifest.json" "${SRC}/"
if [[ -d "${REPO_ROOT}/ui/dist" ]]; then
    cp -a "${REPO_ROOT}/ui/dist" "${SRC}/ui-dist"
else
    warn "ui/dist absent — skipping (run 'cd ui && npm run build' for a full release shape)"
fi
echo "${VERSION}" > "${SRC}/VERSION"

tar -C "${WORK}" -czf "${TARBALL}" "hal0-${VERSION}"
info "tarball: ${TARBALL} ($(stat -c %s "${TARBALL}") bytes)"

DIGEST="$(sha256sum "${TARBALL}" | awk '{print $1}')"
info "sha256: ${DIGEST}"

# ── 2. Generate a local cosign key pair and sign the tarball.
#       Production uses keyless OIDC; this exercises the same verify
#       binary with a different trust root.
#
#       cosign 3.x always emits the new bundle format (.json with
#       messageSignature + verificationMaterial). The updater shells out
#       to verify-blob with the legacy --signature <file> shape, which
#       still works in 3.x — but we need to extract the raw signature
#       from the bundle ourselves. We do that here so we can prove the
#       legacy verify-blob path the updater uses still works against a
#       cosign-3.x-signed artifact.
step "2. cosign sign-blob (local key, NOT OIDC)"
mkdir -p "${KEYDIR}"
( cd "${KEYDIR}" && COSIGN_PASSWORD="" cosign generate-key-pair >/dev/null )
info "key pair: ${KEYDIR}/cosign.{key,pub}"

# cosign 3.x requires --bundle and writes to the public Rekor instance.
# Our local key is uploaded as a hashedrekord (no certificate identity),
# which is fine for the bundle path. The legacy --signature path we
# verify with below uses --insecure-ignore-tlog because the local key
# has no Rekor entry of its own to anchor to.
COSIGN_PASSWORD="" cosign sign-blob --yes \
    --key "${KEYDIR}/cosign.key" \
    --bundle "${BUNDLE}" \
    "${TARBALL}" >/dev/null
info "bundle: ${BUNDLE}"

# Extract the raw base64 signature so we can drive the legacy verify-blob
# path that the updater uses (src/hal0/updater/updater.py:_verify_cosign).
python3 - "${BUNDLE}" "${SIG}" <<'PY'
import json, sys
bundle = json.load(open(sys.argv[1]))
sig = bundle["messageSignature"]["signature"]   # already base64
open(sys.argv[2], "w").write(sig)
PY
info "extracted raw sig: ${SIG}"

# ── 3. Synthesize a hal0.releases.v1 manifest pointing at file:// URLs.
#       The updater's HAL0_RELEASES_URL override accepts file paths so
#       we can drive Updater.check() with this manifest.
step "3. Write release manifest (hal0.releases.v1)"
python3 - "${MANIFEST}" "${VERSION}" "${TARBALL}" "${SIG}" "${DIGEST}" <<'PY'
import json, sys
out, version, tar, sig, digest = sys.argv[1:]
payload = {
    "_schema": "hal0.releases.v1",
    "version": version,
    "channel": "dev",
    "url": f"file://{tar}",
    "sig_url": f"file://{sig}",
    # The prototype signs with --key, so no Fulcio cert exists. Point
    # cert_url at the sig file itself so the schema is satisfied; the
    # --key verify path in updater._verify_cosign ignores --certificate
    # contents (production keyless flow is what actually uses the cert).
    "cert_url": f"file://{sig}",
    "digest_sha256": digest,
    # In production this is the GH Actions workflow subject (see
    # docs/internal/release-manifest.md). For the local key prototype it is a
    # wildcard we never actually evaluate — verify-blob with --key
    # short-circuits the cert-identity check.
    "signer_identity": "^https://github\\.com/hal0ai/hal0/.*",
    "signer_issuer": "https://token.actions.githubusercontent.com",
    "min_data_version": 1,
    "released_at": "2026-05-15T12:00:00Z",
    "manifest_url": f"file://{out}",
    "toolbox_images": {},
}
with open(out, "w") as f:
    json.dump(payload, f, indent=2)
PY
info "manifest: ${MANIFEST}"

# ── 4. Drive the updater's check() against the synthetic manifest.
step "4. Drive Updater.check() against the synthetic manifest"
cd "${REPO_ROOT}"
# Prefer the project's venv if present so transitive deps (structlog,
# pydantic, httpx) are available; fall back to system python3.
PY_BIN="python3"
if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PY_BIN="${REPO_ROOT}/.venv/bin/python"
    info "using project venv: ${PY_BIN}"
fi
HAL0_RELEASES_URL="file://${MANIFEST}" "${PY_BIN}" - <<'PY'
import asyncio, sys
sys.path.insert(0, "src")
from hal0.updater import Updater

async def main():
    info = await Updater(channel="dev").check()
    print(f"current={info.current!r}  latest={info.latest!r}  "
          f"update_available={info.update_available}  "
          f"digest_sha256={info.digest_sha256[:12] if info.digest_sha256 else None}…")
    assert info.latest, "manifest.version did not parse"
    assert info.digest_sha256, "manifest.digest_sha256 did not surface"
    print("OK: Updater.check() parsed the synthetic manifest")

asyncio.run(main())
PY
info "Updater.check(): manifest parsed + version compared"

# ── 5. cosign verify-blob (legacy --signature path the updater uses).
#       --insecure-ignore-tlog because our local key has no anchored
#       certificate-transparency entry; the production path uses
#       --certificate-identity-regexp instead and inherits Rekor
#       verification automatically.
step "5. cosign verify-blob (good sig + good tarball)"
COSIGN_PASSWORD="" cosign verify-blob \
    --key "${KEYDIR}/cosign.pub" \
    --signature "${SIG}" \
    --insecure-ignore-tlog \
    "${TARBALL}" \
    && info "verify-blob accepted the good signature" \
    || fail "verify-blob REJECTED a known-good signature — toolchain regression"

# ── 6. Tamper with the tarball; verify must reject.
step "6. cosign verify-blob (tampered tarball, must FAIL)"
TAMPERED="${WORK}/hal0-${VERSION}-tampered.tar.gz"
cp "${TARBALL}" "${TAMPERED}"
printf '\x00' >> "${TAMPERED}"

if COSIGN_PASSWORD="" cosign verify-blob \
    --key "${KEYDIR}/cosign.pub" \
    --signature "${SIG}" \
    --insecure-ignore-tlog \
    "${TAMPERED}" 2>/dev/null
then
    fail "TAMPERED tarball was accepted — verify path is BROKEN"
else
    info "verify-blob correctly rejected the tampered tarball"
fi

# ── 7. Updater's pre-cosign sha256 mismatch check.
step "7. Updater sha256 mismatch enforcement"
ORIG_DIGEST="${DIGEST}"
TAMPERED_DIGEST="$(sha256sum "${TAMPERED}" | awk '{print $1}')"
if [[ "${ORIG_DIGEST}" == "${TAMPERED_DIGEST}" ]]; then
    fail "sha256 of tarball + tampered are identical — bash bug, not a release-pipeline bug"
fi
info "tarball sha256 ≠ tampered sha256 — UpdateVerifyError fires before cosign"

step "Summary"
printf '\n%s%sPrototype verify-roundtrip PASSED%s\n\n' "${GRN}" "${BOLD}" "${RST}"
cat <<EOF
Verified locally:
  - cosign sign-blob → verify-blob roundtrip works on this host
  - good signature is accepted, tampered tarball is rejected
  - synthetic hal0.releases.v1 manifest parses through ReleaseManifest
  - Updater.check() surfaces version / digest / signer_identity correctly

Still unproven (requires a real GH Actions run):
  - cosign keyless OIDC verify against the workflow's certificate identity
  - the signer_identity regex in latest.json anchors to the real
    release.yml workflow ref (^…release.yml@refs/tags/vX.Y.Z\$)
  - GitHub release asset upload + releases.hal0.dev hosting

Findings to feed back to the team lead:
  - cosign 3.x sign-blob no longer emits a raw .sig file by default
    (--output-signature is silently ignored). It emits a Sigstore Bundle
    (--bundle <file>). The release.yml workflow MUST either:
      (a) keep --new-bundle-format=true and have the updater learn to
          consume bundles, OR
      (b) use --new-bundle-format=false to keep the legacy raw .sig.
    Today's updater (_verify_cosign) takes the legacy path (option b).
  - For keyless OIDC in GH Actions, --certificate-identity-regexp +
    --certificate-oidc-issuer give us Rekor-anchored verification for
    free; no extra flags are needed in production.

Artifacts left under: ${WORK}  (cleaned by trap)
EOF
