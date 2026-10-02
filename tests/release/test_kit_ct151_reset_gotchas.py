"""ct151 post-rollback checklist stays documented in the validation kit (#2064).

The rc.9 run relearned three ct151 provisioning facts the hard way: ``pct
rollback 151 pristine`` deletes the dev0/dev3 passthrough entries outright
(the pristine snapshot predates them), an in-container resolv.conf fix does
not survive a boot (PVE regenerates it — the fix is ``pct set --nameserver``),
and the pristine minimal Ubuntu 26.04 image ships with neither curl nor jq.
These tests pin the kit docs so a future boxes.example.toml/README curation pass
cannot silently drop the checklist and send the next operator through the
same three opaque failures. The operator's real ``boxes.toml`` is gitignored
(#2271); the tracked template ``boxes.example.toml`` carries the checklist.
"""

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

BOXES = Path("tests/release-validation/boxes.example.toml")
README = Path("tests/release-validation/README.md")


def _ct151_notes() -> str:
    return tomllib.loads(BOXES.read_text())["boxes"]["ct151-cpu-fresh"]["notes"]


def test_ct151_notes_say_rollback_deletes_dev_passthrough() -> None:
    notes = _ct151_notes()
    assert "DELETES" in notes, (
        "ct151 notes lost the fact that `pct rollback 151 pristine` deletes "
        "the dev0/dev3 passthrough entries (not just gid drift)"
    )
    assert (
        "pct set 151 --dev0 /dev/dri/renderD128,gid=991 --dev3 /dev/accel/accel0,gid=991" in notes
    ), "ct151 notes lost the exact re-add command for the dev0/dev3 passthrough entries"


def test_ct151_notes_prescribe_pct_level_nameserver_fix() -> None:
    notes = _ct151_notes()
    assert "pct set 151 --nameserver" in notes, (
        "ct151 notes lost the pct-level DNS fix — an in-container resolv.conf "
        "edit does not survive a boot (PVE regenerates it)"
    )


def test_ct151_notes_include_curl_jq_preinstall() -> None:
    assert re.search(r"apt install -y curl jq", _ct151_notes()), (
        "ct151 notes lost the `apt install -y curl jq` step — the pristine "
        "minimal Ubuntu 26.04 image ships with neither"
    )


def test_readme_reset_step_carries_the_checklist() -> None:
    readme = README.read_text()
    for fragment in ("--nameserver", "gid=991", "curl jq"):
        assert fragment in readme, (
            f"README's ct151 reset step lost the post-rollback checklist item {fragment!r}"
        )


def test_example_fleet_file_has_schema_and_no_private_details() -> None:
    text = BOXES.read_text()
    data = tomllib.loads(text)
    for key in ("hypervisor", "ssh_key", "ssh_user"):
        assert key in data, f"boxes.example.toml lost top-level key {key!r}"
    assert data["boxes"], "boxes.example.toml defines no boxes"
    for name, box in data["boxes"].items():
        for key in ("role", "ctid", "hostname", "ip", "api"):
            assert key in box, f"[boxes.{name}] lost key {key!r}"
    private = re.compile(
        r"\b(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b"
    )
    assert not private.search(text), "boxes.example.toml carries an RFC 1918 address (#2271)"
    assert data["ssh_key"] == "~/.ssh/<your-key>", "ssh_key must stay a placeholder"
    assert "~/.ssh/" not in text.replace("~/.ssh/<your-key>", ""), "real key path in example"


def test_operator_fleet_file_is_not_tracked() -> None:
    vcs = shutil.which("git")
    if vcs is None:
        pytest.skip("git not available")
    probe = subprocess.run(
        [vcs, "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, check=False
    )
    if probe.returncode != 0:
        pytest.skip("not in a git checkout")
    tracked = subprocess.run(
        [vcs, "ls-files", "--", "tests/release-validation/boxes.toml"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert not tracked, "tests/release-validation/boxes.toml must stay untracked (#2271)"
