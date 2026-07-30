"""The pinned cosign in bootstrap.sh gets bumped by a tool, never by hand.

``installer/bootstrap.sh`` fetches a digest-pinned cosign when the host has
none. Those constants are what turn "a binary off a CDN" into "the verifier
hal0 chose", so they must never be edited on faith — and, being hand-edited,
they used to rot with nothing watching.

``scripts/update-cosign-pin.sh`` is the watcher and the bumper. Its whole
value is that it refuses to write a digest it has not authenticated:

  * the release's ``cosign_checksums.txt`` is only read after
    ``cosign verify-blob`` accepts the keyless
    ``cosign_checksums.txt.sigstore.json`` bundle for it, pinned to
    sigstore's release identity/issuer;
  * each pinned binary is then downloaded and hashed against that signed
    manifest;
  * every failure leaves ``bootstrap.sh`` byte-identical and exits non-zero.

These tests drive the real script under a hermetic PATH (the technique
``tests/installer/test_bootstrap_cosign_fetch.py`` uses) so they need no
network. curl, cosign and sha256sum are the stubbed boundary; every
assertion about "fails closed" is checked against the actual bytes of a
throwaway ``bootstrap.sh`` copy.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "update-cosign-pin.sh"
_BOOTSTRAP = _REPO_ROOT / "installer" / "bootstrap.sh"
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "cosign-pin.yml"

_RELEASE_BASE = "https://github.com/sigstore/cosign/releases/download"
_LATEST_API = "https://api.github.com/repos/sigstore/cosign/releases/latest"
_SIGNER_IDENTITY = "keyless@projectsigstore.iam.gserviceaccount.com"
_SIGNER_ISSUER = "https://accounts.google.com"

# Exit-code contract (mirrors scripts/check-bootstrap-parity.sh).
_OK, _DRIFT, _OPERROR = 0, 1, 2

# A plausible "next" release. The digests are obvious fixtures — the point of
# the harness is that the script cannot tell them apart from real ones, so
# only its verification steps decide whether they get written.
_NEXT_VERSION = "v3.2.0"
_NEXT_AMD64 = "a" * 64
_NEXT_ARM64 = "c" * 64


def _pinned(name: str, text: str | None = None) -> str:
    source = _BOOTSTRAP.read_text(encoding="utf-8") if text is None else text
    match = re.search(rf"^{name}='([^']*)'$", source, re.MULTILINE)
    assert match is not None, f"{name} not found"
    return match.group(1)


def _write_executable(path: Path, body: str) -> None:
    # Overwriting a symlink into /usr/bin would try to write the real tool.
    path.unlink(missing_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _checksums(entries: dict[str, str], *, extra: str = "") -> str:
    body = "".join(f"{digest}  {asset}\n" for asset, digest in entries.items())
    return body + extra


def _next_checksums() -> str:
    return _checksums(
        {"cosign-linux-amd64": _NEXT_AMD64, "cosign-linux-arm64": _NEXT_ARM64},
        extra="deadbeef" * 8 + "  cosign-darwin-amd64\n",
    )


def _current_checksums() -> str:
    return _checksums(
        {
            "cosign-linux-amd64": _pinned("_COSIGN_SHA256_LINUX_AMD64"),
            "cosign-linux-arm64": _pinned("_COSIGN_SHA256_LINUX_ARM64"),
        }
    )


class Harness:
    """A throwaway bootstrap.sh plus a hermetic PATH the script must use."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.bootstrap = tmp_path / "bootstrap.sh"
        shutil.copy2(_BOOTSTRAP, self.bootstrap)
        self.original = self.bootstrap.read_bytes()
        self.curl_log = tmp_path / "curl.log"
        self.cosign_log = tmp_path / "cosign.log"
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.env: dict[str, str] = {}

    # ── stubs ──────────────────────────────────────────────────────────
    def build(
        self,
        *,
        latest_tag: str = _NEXT_VERSION,
        latest_ok: bool = True,
        checksums: str | None = None,
        checksums_ok: bool = True,
        bundle_ok: bool = True,
        verify_rc: int = 0,
        has_new_bundle_format: bool = True,
        system_cosign: bool = True,
        binary_download_ok: bool = True,
        served: dict[str, str] | None = None,
    ) -> None:
        for tool in (
            "awk",
            "bash",
            "cat",
            "chmod",
            "cp",
            "dirname",
            "grep",
            "mkdir",
            "mktemp",
            "python3",
            "rm",
            "sed",
            "uname",
            "wc",
        ):
            target = shutil.which(tool)
            assert target is not None, f"test host is missing {tool}"
            os.symlink(target, self.bin / tool)

        # The bootstrapped-verifier path downloads a cosign and executes it;
        # the payload has to be a real runnable program for that to be a
        # meaningful test of the ratchet.
        payload = self.tmp / "cosign.payload"
        _write_executable(payload, self._cosign_body())

        cp = shutil.which("cp")
        assert cp is not None
        _write_executable(
            self.bin / "curl",
            f"""#!/usr/bin/env bash
set -uo pipefail
out=""
url=""
while (($#)); do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        --retry|--retry-delay|-H) shift 2 ;;
        --url) url="$2"; shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
printf '%s\\n' "$url" >> "$CURL_LOG"
emit() {{ if [[ -n "$out" ]]; then printf '%s' "$1" > "$out"; else printf '%s' "$1"; fi; }}
case "$url" in
    *releases/latest)
        [[ "$LATEST_OK" == "1" ]] || exit 22
        emit "{{\\"tag_name\\": \\"$LATEST_TAG\\"}}"
        ;;
    *.sigstore.json)
        [[ "$BUNDLE_OK" == "1" ]] || exit 22
        emit "$BUNDLE_BODY"
        ;;
    *cosign_checksums.txt)
        [[ "$CHECKSUMS_OK" == "1" ]] || exit 22
        cat "$CHECKSUMS_FILE" > "${{out:-/dev/stdout}}"
        ;;
    *cosign-linux-*)
        [[ "$BINARY_OK" == "1" ]] || exit 22
        {cp} "$COSIGN_PAYLOAD" "$out"
        ;;
    *) exit 22 ;;
esac
""",
        )

        # sha256sum is stubbed so the harness decides what the "served" bytes
        # hash to; forging a preimage is not an option and stubbing it is
        # what lets one harness drive both the accept and reject branches.
        _write_executable(
            self.bin / "sha256sum",
            """#!/usr/bin/env bash
f="$1"
b="${f##*/}"
var="SERVED_${b//[^A-Za-z0-9]/_}"
val="${!var:-}"
if [[ -n "$val" ]]; then
    printf '%s  %s\\n' "$val" "$f"
else
    printf '%s  %s\\n' "ff" "$f"
fi
""",
        )

        if system_cosign:
            _write_executable(self.bin / "cosign", self._cosign_body())

        checksums_file = self.tmp / "checksums.txt"
        checksums_file.write_text(
            _next_checksums() if checksums is None else checksums, encoding="utf-8"
        )

        served = dict(served or {})
        served.setdefault("cosign-linux-amd64", _NEXT_AMD64)
        served.setdefault("cosign-linux-arm64", _NEXT_ARM64)
        # What the *bootstrapped* verifier binary hashes to; matching the
        # in-tree pin by default so the ratchet accepts it.
        served.setdefault("cosign", _pinned("_COSIGN_SHA256_LINUX_AMD64"))

        self.env = {
            "PATH": str(self.bin),
            "TMPDIR": str(self.tmp),
            "CURL_LOG": str(self.curl_log),
            "COSIGN_LOG": str(self.cosign_log),
            "LATEST_TAG": latest_tag,
            "LATEST_OK": "1" if latest_ok else "0",
            "CHECKSUMS_FILE": str(checksums_file),
            "CHECKSUMS_OK": "1" if checksums_ok else "0",
            "BUNDLE_OK": "1" if bundle_ok else "0",
            "BUNDLE_BODY": '{"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json"}',
            "BINARY_OK": "1" if binary_download_ok else "0",
            "COSIGN_PAYLOAD": str(payload),
            "COSIGN_VERIFY_RC": str(verify_rc),
            "COSIGN_HAS_NEW_BUNDLE": "1" if has_new_bundle_format else "0",
        }
        for name, digest in served.items():
            self.env[f"SERVED_{re.sub(r'[^A-Za-z0-9]', '_', name)}"] = digest

    @staticmethod
    def _cosign_body() -> str:
        return """#!/usr/bin/env bash
if [[ "${1:-}" == "verify-blob" && "${2:-}" == "--help" ]]; then
    printf 'HELP\\n' >> "$COSIGN_LOG"
    printf 'Usage: cosign verify-blob\\n'
    [[ "$COSIGN_HAS_NEW_BUNDLE" == "1" ]] && printf '      --new-bundle-format\\n'
    exit 0
fi
case "${1:-}" in
    version) printf 'VERSION\\n' >> "$COSIGN_LOG"; exit 0 ;;
    verify-blob)
        { printf 'VERIFY'; printf ' %s' "$@"; printf '\\n'; } >> "$COSIGN_LOG"
        printf 'stub verify-blob\\n'
        exit "$COSIGN_VERIFY_RC"
        ;;
esac
printf 'OTHER %s\\n' "$*" >> "$COSIGN_LOG"
exit 0
"""

    # ── driving ────────────────────────────────────────────────────────
    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(_SCRIPT), *args, str(self.bootstrap)],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    # ── assertions ─────────────────────────────────────────────────────
    def assert_untouched(self) -> None:
        assert self.bootstrap.read_bytes() == self.original, "pins were modified on a failure path"
        leftovers = list(self.tmp.glob("bootstrap.sh.*tmp"))
        assert leftovers == [], f"left a partial write behind: {leftovers}"

    @property
    def urls(self) -> list[str]:
        if not self.curl_log.exists():
            return []
        return self.curl_log.read_text(encoding="utf-8").split()

    @property
    def cosign_calls(self) -> list[str]:
        if not self.cosign_log.exists():
            return []
        return self.cosign_log.read_text(encoding="utf-8").splitlines()


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


# ── the script and the file it edits agree ─────────────────────────────────


def test_script_and_bootstrap_agree_on_the_release_host(harness: Harness) -> None:
    # A pin pointing somewhere the script does not verify is the one thing
    # the automation must never paper over.
    script = _SCRIPT.read_text(encoding="utf-8")
    assert f"COSIGN_RELEASE_BASE_URL='{_RELEASE_BASE}'" in script
    assert _pinned("_COSIGN_BASE_URL") == _RELEASE_BASE


def test_bootstrap_maintenance_block_points_at_the_tool() -> None:
    # The old block said "there is no automated bump path in this repo".
    # Leaving that in place is the rot this change exists to remove.
    text = _BOOTSTRAP.read_text(encoding="utf-8")
    assert "scripts/update-cosign-pin.sh" in text
    assert "There is no automated bump path" not in text


def test_bash_syntax_check() -> None:
    proc = subprocess.run(["bash", "-n", str(_SCRIPT)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


# ── happy paths ────────────────────────────────────────────────────────────


def test_pin_already_current_reverifies_and_exits_zero(harness: Harness) -> None:
    harness.build(
        latest_tag=_pinned("_COSIGN_VERSION"),
        checksums=_current_checksums(),
        served={
            "cosign-linux-amd64": _pinned("_COSIGN_SHA256_LINUX_AMD64"),
            "cosign-linux-arm64": _pinned("_COSIGN_SHA256_LINUX_ARM64"),
        },
    )

    proc = harness.run()

    assert proc.returncode == _OK, proc.stderr
    assert "Pinned cosign is current" in proc.stdout
    harness.assert_untouched()
    # "Current" is a verified statement, not a string compare: the signature
    # was checked and both binaries were re-hashed.
    assert any(call.startswith("VERIFY ") for call in harness.cosign_calls)
    assert any(url.endswith("/cosign-linux-arm64") for url in harness.urls)


def test_check_mode_reports_drift_without_touching_the_pins(harness: Harness) -> None:
    harness.build()

    proc = harness.run()

    assert proc.returncode == _DRIFT, proc.stderr
    assert f"`{_pinned('_COSIGN_VERSION')}` → `{_NEXT_VERSION}`" in proc.stdout
    assert _NEXT_AMD64 in proc.stdout
    assert _NEXT_ARM64 in proc.stdout
    harness.assert_untouched()


def test_bump_rewrites_exactly_the_three_pinned_constants(harness: Harness) -> None:
    harness.build()

    proc = harness.run("--bump")

    assert proc.returncode == _OK, proc.stderr
    updated = harness.bootstrap.read_text(encoding="utf-8")
    assert _pinned("_COSIGN_VERSION", updated) == _NEXT_VERSION
    assert _pinned("_COSIGN_SHA256_LINUX_AMD64", updated) == _NEXT_AMD64
    assert _pinned("_COSIGN_SHA256_LINUX_ARM64", updated) == _NEXT_ARM64

    before = harness.original.decode("utf-8").splitlines()
    after = updated.splitlines()
    assert len(before) == len(after)
    changed = [b for b, a in zip(before, after, strict=True) if a != b]
    assert len(changed) == 3, changed
    # Nothing else moved — not the base URL, not the comment block.
    assert _pinned("_COSIGN_BASE_URL", updated) == _RELEASE_BASE
    assert "scripts/update-cosign-pin.sh" in updated
    # And the result is still a parseable shell script.
    syntax = subprocess.run(
        ["bash", "-n", str(harness.bootstrap)], capture_output=True, text=True, check=False
    )
    assert syntax.returncode == 0, syntax.stderr


def test_bump_is_idempotent_when_the_pin_is_already_current(harness: Harness) -> None:
    harness.build(
        latest_tag=_pinned("_COSIGN_VERSION"),
        checksums=_current_checksums(),
        served={
            "cosign-linux-amd64": _pinned("_COSIGN_SHA256_LINUX_AMD64"),
            "cosign-linux-arm64": _pinned("_COSIGN_SHA256_LINUX_ARM64"),
        },
    )

    proc = harness.run("--bump")

    assert proc.returncode == _OK, proc.stderr
    harness.assert_untouched()


def test_explicit_version_skips_the_latest_release_lookup(harness: Harness) -> None:
    harness.build(latest_ok=False)

    proc = harness.run("--version", _NEXT_VERSION)

    assert proc.returncode == _DRIFT, proc.stderr
    assert not any(url == _LATEST_API for url in harness.urls)


# ── the signature is what makes a digest readable ──────────────────────────


def test_verify_blob_is_pinned_to_the_sigstore_release_identity(harness: Harness) -> None:
    harness.build()

    proc = harness.run()

    assert proc.returncode == _DRIFT, proc.stderr
    verify = [c for c in harness.cosign_calls if c.startswith("VERIFY ")]
    assert len(verify) == 1, harness.cosign_calls
    call = verify[0]
    assert f"--certificate-identity {_SIGNER_IDENTITY}" in call
    assert f"--certificate-oidc-issuer {_SIGNER_ISSUER}" in call
    assert "--bundle " in call
    assert call.rstrip().endswith("cosign_checksums.txt")


@pytest.mark.parametrize("supported", [True, False])
def test_new_bundle_format_flag_tracks_what_the_verifier_supports(
    harness: Harness, supported: bool
) -> None:
    # cosign >= 2.4 gates .sigstore.json behind --new-bundle-format; passing
    # it blindly breaks older verifiers, omitting it breaks newer ones.
    harness.build(has_new_bundle_format=supported)

    proc = harness.run()

    assert proc.returncode == _DRIFT, proc.stderr
    call = next(c for c in harness.cosign_calls if c.startswith("VERIFY "))
    assert ("--new-bundle-format" in call) is supported


def test_signature_failure_fails_closed_before_any_digest_is_read(harness: Harness) -> None:
    harness.build(verify_rc=1)

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "signature verification FAILED" in proc.stderr
    assert "refusing to read a digest out of an unauthenticated checksums file" in proc.stderr
    harness.assert_untouched()
    # Not even the delivery cross-check ran: the release binaries were never
    # requested, so no unauthenticated digest was ever in play.
    assert not any("cosign-linux-" in url for url in harness.urls)


def test_missing_signature_bundle_fails_closed(harness: Harness) -> None:
    harness.build(bundle_ok=False)

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "could not download cosign_checksums.txt.sigstore.json" in proc.stderr
    assert "unsigned evidence" in proc.stderr
    harness.assert_untouched()
    assert not any(c.startswith("VERIFY ") for c in harness.cosign_calls)


# ── evidence failures ──────────────────────────────────────────────────────


def test_checksums_file_unreachable_fails_closed(harness: Harness) -> None:
    harness.build(checksums_ok=False)

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "could not download cosign_checksums.txt" in proc.stderr
    harness.assert_untouched()


def test_empty_checksums_file_fails_closed(harness: Harness) -> None:
    harness.build(checksums="")

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "is empty" in proc.stderr
    harness.assert_untouched()


def test_latest_release_lookup_failure_fails_closed(harness: Harness) -> None:
    harness.build(latest_ok=False)

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "could not query the latest sigstore/cosign release" in proc.stderr
    harness.assert_untouched()


def test_latest_release_without_a_tag_name_fails_closed(harness: Harness) -> None:
    harness.build(latest_tag="")

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "carried no tag_name" in proc.stderr
    harness.assert_untouched()


@pytest.mark.parametrize("asset", ["cosign-linux-amd64", "cosign-linux-arm64"])
def test_missing_digest_for_an_architecture_fails_closed(harness: Harness, asset: str) -> None:
    entries = {"cosign-linux-amd64": _NEXT_AMD64, "cosign-linux-arm64": _NEXT_ARM64}
    del entries[asset]
    harness.build(checksums=_checksums(entries))

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert f"lists no entry for {asset}" in proc.stderr
    assert "dropped an architecture hal0 pins" in proc.stderr
    harness.assert_untouched()


@pytest.mark.parametrize(
    "digest",
    [
        "a" * 63,  # too short
        "a" * 65,  # too long
        "A" * 64,  # uppercase — bootstrap.sh compares against sha256sum output
        "g" * 64,  # not hex
        "",
    ],
    ids=["short", "long", "uppercase", "nonhex", "empty"],
)
def test_malformed_digest_fails_closed(harness: Harness, digest: str) -> None:
    harness.build(
        checksums=_checksums({"cosign-linux-amd64": digest, "cosign-linux-arm64": _NEXT_ARM64})
    )

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    # Either the line no longer parses as a checksum entry at all, or it
    # parses and then fails the lowercase-sha256 gate. Both are fail-closed.
    assert (
        "lists no entry for cosign-linux-amd64" in proc.stderr
        or "cosign-linux-amd64 @ v3.2.0 has a malformed sha256" in proc.stderr
    ), proc.stderr
    harness.assert_untouched()
    # A digest that never passed validation must never reach the delivery
    # check either — nothing downloaded, nothing written.
    assert not any("cosign-linux-" in url for url in harness.urls)


def test_duplicate_entry_for_one_asset_fails_closed(harness: Harness) -> None:
    harness.build(
        checksums=_checksums(
            {"cosign-linux-amd64": _NEXT_AMD64, "cosign-linux-arm64": _NEXT_ARM64},
            extra=f"{'b' * 64}  cosign-linux-amd64\n",
        )
    )

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "lists cosign-linux-amd64 2 times" in proc.stderr
    harness.assert_untouched()


def test_identical_digests_for_both_architectures_fail_closed(harness: Harness) -> None:
    # tests/installer/test_bootstrap_cosign_fetch.py asserts this invariant on
    # the committed file; catching it here means CI is never the first to know.
    harness.build(
        checksums=_checksums({"cosign-linux-amd64": _NEXT_AMD64, "cosign-linux-arm64": _NEXT_AMD64})
    )

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "same digest" in proc.stderr
    harness.assert_untouched()


# ── delivery failures ──────────────────────────────────────────────────────


def test_served_bytes_disagreeing_with_the_signed_manifest_fail_closed(
    harness: Harness,
) -> None:
    harness.build(served={"cosign-linux-arm64": "d" * 64})

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "does not match the SIGNED checksum" in proc.stderr
    assert "release-asset integrity failure" in proc.stderr
    harness.assert_untouched()


def test_release_binary_download_failure_fails_closed(harness: Harness) -> None:
    harness.build(binary_download_ok=False)

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "to confirm its digest" in proc.stderr
    harness.assert_untouched()


# ── the verifier ratchet ───────────────────────────────────────────────────


def test_without_a_system_cosign_the_in_tree_pin_bootstraps_the_verifier(
    harness: Harness,
) -> None:
    harness.build(system_cosign=False)

    proc = harness.run()

    assert proc.returncode == _DRIFT, proc.stderr
    assert "bootstrapping the in-tree pin" in proc.stderr
    # It fetched the *currently pinned* version, not the target.
    assert harness.urls[0] == (f"{_RELEASE_BASE}/{_pinned('_COSIGN_VERSION')}/cosign-linux-amd64")
    # And it smoke-tested the binary before trusting it as the verifier.
    assert harness.cosign_calls[0] == "VERSION"
    harness.assert_untouched()


def test_bootstrapped_verifier_digest_mismatch_fails_closed(harness: Harness) -> None:
    harness.build(system_cosign=False, served={"cosign": "e" * 64})

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "in-tree pinned cosign sha256 mismatch" in proc.stderr
    assert "refusing to verify anything with an unverified binary" in proc.stderr
    harness.assert_untouched()
    # Never executed, and no evidence was ever fetched with it.
    assert harness.cosign_calls == []
    assert not any("checksums" in url for url in harness.urls)


def test_bootstrapped_verifier_download_failure_fails_closed(harness: Harness) -> None:
    harness.build(system_cosign=False, binary_download_ok=False)

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "could not download the in-tree pinned cosign" in proc.stderr
    harness.assert_untouched()


def test_unsupported_architecture_fails_closed_with_no_system_cosign(
    harness: Harness,
) -> None:
    harness.build(system_cosign=False)
    _write_executable(
        harness.bin / "uname",
        "#!/usr/bin/env bash\nprintf 'riscv64\\n'\n",
    )

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "pins no build for riscv64" in proc.stderr
    harness.assert_untouched()
    assert harness.urls == []


# ── refusing to go backwards, and argument hygiene ─────────────────────────


def test_a_latest_release_older_than_the_pin_is_refused(harness: Harness) -> None:
    harness.build(latest_tag="v3.0.0")

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "older than the pin" in proc.stderr
    assert "refusing to roll the pin backwards" in proc.stderr
    harness.assert_untouched()


def test_an_explicit_version_may_deliberately_go_backwards(harness: Harness) -> None:
    harness.build(latest_ok=False, checksums=_next_checksums())

    proc = harness.run("--version", "v3.0.0")

    # It proceeds to the full verification chain rather than refusing up front.
    assert proc.returncode == _DRIFT, proc.stderr
    assert any("/v3.0.0/cosign_checksums.txt" in url for url in harness.urls)


@pytest.mark.parametrize("bad", ["3.1.2", "v3.1", "latest", "v3.1.2-rc.1"])
def test_a_target_that_is_not_a_release_tag_is_refused(harness: Harness, bad: str) -> None:
    harness.build()

    proc = harness.run("--version", bad)

    assert proc.returncode == _OPERROR
    assert "not a vX.Y.Z release tag" in proc.stderr
    harness.assert_untouched()


def test_unknown_flag_is_an_operational_error(harness: Harness) -> None:
    harness.build()

    proc = harness.run("--force")

    assert proc.returncode == _OPERROR
    assert "unknown argument" in proc.stderr
    harness.assert_untouched()


def test_missing_bootstrap_file_is_an_operational_error(harness: Harness) -> None:
    harness.build()
    harness.bootstrap.unlink()

    proc = harness.run()

    assert proc.returncode == _OPERROR
    assert "bootstrap script not found" in proc.stderr


def test_a_pin_pointing_at_an_unverified_host_is_refused(harness: Harness) -> None:
    harness.build()
    harness.bootstrap.write_text(
        harness.original.decode("utf-8").replace(
            f"_COSIGN_BASE_URL='{_RELEASE_BASE}'",
            "_COSIGN_BASE_URL='https://mirror.example.invalid/cosign'",
        ),
        encoding="utf-8",
    )

    proc = harness.run("--bump")

    assert proc.returncode == _OPERROR
    assert "refusing to bump a pin that points somewhere this script does not verify" in (
        proc.stderr
    )


# ── the workflow never merges anything ─────────────────────────────────────


def test_workflow_opens_a_pr_and_never_merges() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    assert "gh pr create" in workflow
    # Auto-merge in any spelling would silently change a security pin on main.
    for forbidden in (
        "gh pr merge",
        "--auto",
        "--admin",
        "automerge",
        "enablePullRequestAutoMerge",
    ):
        assert forbidden not in workflow, forbidden


def test_workflow_runs_the_pin_contract_tests_before_opening_the_pr() -> None:
    yaml = pytest.importorskip("yaml")
    steps = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["bump"]["steps"]
    names = [step["name"] for step in steps]
    contract = names.index("Run the pin's contract tests")
    assert contract < names.index("Open the review pull request")
    run = steps[contract]["run"]
    assert "tests/installer/test_bootstrap_cosign_fetch.py" in run
    # tests/conftest.py imports the hal0 package this job does not install.
    assert "--noconftest" in run


def test_workflow_treats_operational_error_as_failure_not_drift() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    # Same exit-code contract the script documents, same shape as
    # hermes-sdk-diff.yml: only exit 1 means "act", anything else fails.
    assert 'echo "drift=false" >> "$GITHUB_OUTPUT"' in workflow
    assert 'echo "drift=true" >> "$GITHUB_OUTPUT"' in workflow
    assert "exited with operational error" in workflow


def test_workflow_is_scheduled_and_dispatchable_only() -> None:
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML resolves the bare `on:` key to the boolean True.
    triggers = parsed[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] == [{"cron": "0 13 * * 1"}]
    # Never on push/pull_request: a fork PR must not get write credentials.
    assert parsed["permissions"] == {"contents": "read"}
    job = parsed["jobs"]["bump"]
    assert job["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert parsed["concurrency"]["group"] == "cosign-pin"
