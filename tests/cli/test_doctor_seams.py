"""#1465 — ``hal0 doctor all`` must render the privileged-seam verdict.

The defect was that *every* doctor surface reported green on a box whose sudo
grants failed to install. These tests pin the new row: a broken required seam is
an actionable ``fail`` (exit 1), a broken optional seam or an untestable grant
is an advisory ``warn``, and only a fully-proved seam set passes.
"""

from __future__ import annotations

from hal0.cli.doctor_all import check_seams, overall_verdict
from hal0.cli.doctor_verify import _FAIL, _PASS, _WARN
from hal0.system.seam_check import SEAMS, SeamSpec, SeamStatus

_REQUIRED = next(s for s in SEAMS if s.required)
_OPTIONAL = next(s for s in SEAMS if not s.required)


def _status(
    spec: SeamSpec,
    *,
    binary_ok: bool = True,
    sudoers_ok: bool = True,
    grant_ok: bool | None = True,
    binary_detail: str = "wrapper ok",
    sudoers_detail: str = "sudoers grant ok",
    grant_detail: str = "",
) -> SeamStatus:
    return SeamStatus(
        spec=spec,
        binary_ok=binary_ok,
        binary_detail=binary_detail,
        sudoers_ok=sudoers_ok,
        sudoers_detail=sudoers_detail,
        grant_ok=grant_ok,
        grant_detail=grant_detail,
    )


def test_all_seams_proved_passes() -> None:
    check = check_seams([_status(s) for s in SEAMS])
    assert check.status == _PASS
    assert check.key == "seams"


def test_missing_required_grant_is_an_actionable_fail() -> None:
    """The install.sh warn-only path, now visible instead of silently green."""
    rows = [
        _status(
            _REQUIRED,
            sudoers_ok=False,
            sudoers_detail="sudoers grant /etc/sudoers.d/hal0-systemctl is missing",
        )
    ]
    check = check_seams(rows)

    assert check.status == _FAIL
    assert check.critical is False  # actionable, not "hal0 is down"
    assert "/etc/sudoers.d/hal0-systemctl is missing" in check.detail
    assert _REQUIRED.role in check.detail
    assert "install.sh" in check.detail
    assert overall_verdict([check]) == "fail"


def test_a_grant_that_does_not_apply_is_a_fail_even_when_both_files_exist() -> None:
    rows = [
        _status(
            _REQUIRED,
            grant_ok=False,
            grant_detail="hal0-systemctl: `sudo -n hal0-systemctl help` exited 1 as hal0",
        )
    ]
    check = check_seams(rows)
    assert check.status == _FAIL
    assert "exited 1 as hal0" in check.detail


def test_broken_optional_seam_is_only_advisory() -> None:
    rows = [
        _status(_REQUIRED),
        _status(_OPTIONAL, binary_ok=False, binary_detail="wrapper /x is missing"),
    ]
    check = check_seams(rows)
    assert check.status == _WARN
    assert overall_verdict([check]) == "ok"


def test_untested_grant_warns_rather_than_claiming_success() -> None:
    """Running `hal0 doctor` as a random user cannot prove the hal0 grant."""
    rows = [_status(s, grant_ok=None) for s in SEAMS]
    check = check_seams(rows)
    assert check.status == _WARN
    assert "re-run as root" in check.detail


def test_empty_inventory_never_claims_pass() -> None:
    assert check_seams([]).status == _WARN
