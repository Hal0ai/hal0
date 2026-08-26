"""ct151 post-rollback checklist stays documented in the validation kit (#2064).

The rc.9 run relearned three ct151 provisioning facts the hard way: ``pct
rollback 151 pristine`` deletes the dev0/dev3 passthrough entries outright
(the pristine snapshot predates them), an in-container resolv.conf fix does
not survive a boot (PVE regenerates it — the fix is ``pct set --nameserver``),
and the pristine minimal Ubuntu 26.04 image ships with neither curl nor jq.
These tests pin the kit docs so a future boxes.toml/README curation pass
cannot silently drop the checklist and send the next operator through the
same three opaque failures.
"""

import re
import tomllib
from pathlib import Path

BOXES = Path("tests/release-validation/boxes.toml")
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
