"""RATIFIED 2026-07-18 (deliverable 4) — convergent podman AppArmor preflight.

Detect the unconfined-LXC apparmor profile-load failure from the podman SMOKE
FAILURE (halo150 R4), write the containers.conf fix once, retry — all against
recorded fakes, no real podman or /etc writes.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from hal0.agents import containers_apparmor as ca


def _proc(returncode: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


_APPARMOR_STDERR = (
    "Error: default OCI runtime spec: install profile containers-default apparmor: exit status 243"
)


def _recorder(sequence: list[subprocess.CompletedProcess]):
    calls: list[list[str]] = []
    it = iter(sequence)

    def _run(argv, **_kw):
        calls.append(list(argv))
        return next(it)

    return _run, calls


def test_smoke_passes_leaves_config_untouched(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    run, calls = _recorder([_proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    assert result.outcome == "ok"
    assert not result.wrote_config
    assert not conf.exists()
    assert len(calls) == 1  # only the initial smoke


def test_apparmor_failure_writes_config_and_retries(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    run, calls = _recorder([_proc(243, _APPARMOR_STDERR), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    assert result.outcome == "fixed"
    assert result.wrote_config
    assert conf.exists()
    assert 'apparmor_profile = "unconfined"' in conf.read_text()
    assert "[containers]" in conf.read_text()
    assert len(calls) == 2  # smoke, then retry after the write


def test_unrelated_failure_is_not_touched(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    run, _calls = _recorder([_proc(125, "Error: unable to pull image: not found")])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    assert result.outcome == "unrelated"
    assert not result.wrote_config
    assert not conf.exists()


def test_idempotent_when_already_unconfined(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    conf.write_text('[containers]\napparmor_profile = "unconfined"\n')
    run, _calls = _recorder([_proc(243, _APPARMOR_STDERR), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    # Config already carried the fix → no rewrite, but retry still attempted.
    assert result.outcome == "already"
    assert not result.wrote_config


def test_second_run_after_fix_is_ok(tmp_path: Path) -> None:
    # Convergence: after the box is fixed, a rerun re-smokes, passes, no-ops.
    conf = tmp_path / "containers.conf"
    run1, _ = _recorder([_proc(243, _APPARMOR_STDERR), _proc(0)])
    ca.ensure_podman_apparmor_usable(run=run1, conf_path=conf)
    run2, calls2 = _recorder([_proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run2, conf_path=conf)
    assert result.outcome == "ok"
    assert len(calls2) == 1


def test_write_preserves_existing_containers_section(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    conf.write_text('[containers]\nlog_driver = "journald"\n[network]\ndns = ["1.1.1.1"]\n')
    ca._write_apparmor_unconfined(conf)
    text = conf.read_text()
    assert 'log_driver = "journald"' in text
    assert 'apparmor_profile = "unconfined"' in text
    assert "[network]" in text
    assert ca._apparmor_already_unconfined(conf)


def test_no_podman_reports_cleanly(tmp_path: Path) -> None:
    def _run(argv, **_kw):
        raise FileNotFoundError("podman")

    result = ca.ensure_podman_apparmor_usable(run=_run, conf_path=tmp_path / "c.conf")
    assert result.outcome == "no_podman"


# ── Codex #1728 P1 follow-ups ────────────────────────────────────────────────


def test_apparmor_parser_access_denied_shape_is_also_recognized(tmp_path: Path) -> None:
    # The raw `apparmor_parser` failure text (no "install profile
    # containers-default" wrapper) is the OTHER real-world signature shape —
    # installer/lib/preflight.sh's `_is_apparmor_profile_load_failure`
    # recognizes it too; this module's own detection must stay in lockstep or
    # the shell gate remediates while this helper reports "unrelated" and
    # never writes the fix.
    conf = tmp_path / "containers.conf"
    stderr = "apparmor_parser: Warning ... apparmor_parser: profile load failed: Access denied"
    run, calls = _recorder([_proc(243, stderr), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    assert result.outcome == "fixed"
    assert result.wrote_config
    assert len(calls) == 2


def test_default_smoke_argv_honours_smoke_image_env(monkeypatch) -> None:
    monkeypatch.delenv("HAL0_CONTAINER_SMOKE_IMAGE", raising=False)
    assert ca._default_smoke_argv() == ("podman", "run", "--rm", "quay.io/podman/hello")
    monkeypatch.setenv("HAL0_CONTAINER_SMOKE_IMAGE", "registry.local/mirror/hello")
    assert ca._default_smoke_argv() == ("podman", "run", "--rm", "registry.local/mirror/hello")


def test_write_preserves_commented_section_header(tmp_path: Path) -> None:
    # A header with a trailing inline comment (`[containers] # ...`) used to
    # fail the naive `stripped.endswith("]")` check, so the writer never
    # recognized the existing [containers] table and appended a SECOND one —
    # invalid TOML ("Cannot declare ('containers',) twice").
    conf = tmp_path / "containers.conf"
    conf.write_text('[containers] # operator settings\nlog_driver = "journald"\n')
    ca._write_apparmor_unconfined(conf)
    text = conf.read_text()
    assert text.count("[containers]") == 1  # no duplicate table header appended
    assert "# operator settings" in text
    assert 'log_driver = "journald"' in text
    assert 'apparmor_profile = "unconfined"' in text
    # Must still be valid, single-table TOML.
    import tomllib

    parsed = tomllib.loads(text)
    assert parsed["containers"]["apparmor_profile"] == "unconfined"
    assert parsed["containers"]["log_driver"] == "journald"


# ── #3234: unprivileged-LXC guest `flags=(unconfined)` profiles ──────────────
#
# On an UNPRIVILEGED Ubuntu 24.04 LXC the guest's own apparmor profiles for
# podman/crun/buildah/runc/rootlesskit/slirp4netns are `flags=(unconfined)`
# userns-gated stubs that deny every podman socket(). The fix symlinks them
# under <apparmor.d>/disable/ and unloads them with `apparmor_parser -R`.

_SOCKET_DENIED_STDERR = "Error: netavark: failed to create socket: socket: permission denied"
_PROFILE_NAMES = ("podman", "crun", "buildah", "runc", "rootlesskit", "slirp4netns")
_UNCONFINED_PROFILE = "abi <abi/4.0>,\ninclude <tunables/global>\nprofile {n} /usr/bin/{n} flags=(unconfined) {{\n  userns,\n}}\n"
_ENFORCE_PROFILE = (
    "abi <abi/4.0>,\ninclude <tunables/global>\nprofile {n} /usr/bin/{n} {{\n  userns,\n}}\n"
)


def _seed_profiles(apparmor_d: Path, names=_PROFILE_NAMES, *, enforce=()) -> None:
    apparmor_d.mkdir(parents=True, exist_ok=True)
    for n in names:
        tpl = _ENFORCE_PROFILE if n in enforce else _UNCONFINED_PROFILE
        (apparmor_d / n).write_text(tpl.format(n=n))


def _is_parser_remove(argv) -> bool:
    return list(argv)[:2] == ["apparmor_parser", "-R"]


@pytest.fixture(autouse=True)
def _nested_apparmor_ns(monkeypatch, tmp_path: Path) -> Path:
    """Pretend to be inside a nested AppArmor policy namespace (.ns_level=1).

    The #3234 remediation only mutates when /sys/kernel/security/apparmor/.ns_level
    is > 0; tests must never depend on the machine running the suite.
    """
    ns = tmp_path / "ns_level"
    ns.write_text("1\n")
    monkeypatch.setenv("HAL0_APPARMOR_NS_LEVEL_FILE", str(ns))
    return ns


def test_socket_denied_disables_all_six_unconfined_profiles_and_retries(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)
    seq = [_proc(126, _SOCKET_DENIED_STDERR)] + [_proc(0)] * len(_PROFILE_NAMES) + [_proc(0)]
    run, calls = _recorder(seq)

    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf, apparmor_d=aa)

    assert result.outcome == "guest_profiles_disabled"
    assert result.fixed is True
    assert not result.wrote_config
    assert not conf.exists()  # #1563 path must not fire for this signature
    for n in _PROFILE_NAMES:
        link = aa / "disable" / n
        assert link.is_symlink()
        assert Path(os.readlink(link)) == aa / n
        assert n in result.disabled
    parser_calls = [c for c in calls if _is_parser_remove(c)]
    assert parser_calls == [["apparmor_parser", "-R", str(aa / n)] for n in _PROFILE_NAMES]
    # smoke, six parser calls, then exactly one retry smoke (last call)
    assert len(calls) == 1 + len(_PROFILE_NAMES) + 1
    assert calls[0][:2] == ["podman", "run"]
    assert calls[-1][:2] == ["podman", "run"]


def test_socket_denied_retry_still_failing_reports_not_fixed(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",))
    run, calls = _recorder(
        [
            _proc(126, _SOCKET_DENIED_STDERR),
            _proc(0),  # apparmor_parser -R
            _proc(126, _SOCKET_DENIED_STDERR),  # retry smoke
            _proc(0),  # rollback: apparmor_parser -r
        ]
    )
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.outcome == "guest_profiles_disabled"
    assert result.fixed is False
    # the retry's own failure text is kept (like the #1563 path), not the stale first one
    assert "socket: permission denied" in (result.detail or "")
    # a false positive does not leave the profile disabled for nothing
    assert result.rolled_back == ["podman"]
    assert result.disabled == []
    assert not (aa / "disable" / "podman").is_symlink()
    assert calls[-1] == ["apparmor_parser", "-r", str(aa / "podman")]
    assert "rolled back: podman" in (result.detail or "")


def test_rollback_only_touches_links_this_run_created(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun"))
    (aa / "disable").mkdir()
    (aa / "disable" / "podman").symlink_to(aa / "podman")  # the operator's own link
    run, calls = _recorder(
        [
            _proc(126, _SOCKET_DENIED_STDERR),
            _proc(0),
            _proc(0),
            _proc(126, _SOCKET_DENIED_STDERR),
            _proc(0),
        ]
    )
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.fixed is False
    assert result.preexisting == ["podman"]
    assert result.rolled_back == ["crun"]
    assert result.disabled == ["podman"]
    assert (aa / "disable" / "podman").is_symlink()  # kept
    assert not (aa / "disable" / "crun").is_symlink()
    assert [c for c in calls if c[:2] == ["apparmor_parser", "-r"]] == [
        ["apparmor_parser", "-r", str(aa / "crun")]
    ]


def test_rollback_reload_failure_still_removes_the_link(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",))
    run, _ = _recorder(
        [_proc(126, _SOCKET_DENIED_STDERR), _proc(0), _proc(126, _SOCKET_DENIED_STDERR), _proc(1)]
    )
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.rolled_back == ["podman"]
    assert not (aa / "disable" / "podman").is_symlink()


def test_failed_unload_keeps_the_links_even_when_the_retry_fails(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun"))
    run, calls = _recorder(
        [
            _proc(126, _SOCKET_DENIED_STDERR),
            _proc(0),
            _proc(1, "unable to remove"),
            _proc(126, _SOCKET_DENIED_STDERR),
        ]
    )
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.fixed is False
    assert result.rolled_back == []
    assert result.disabled == ["podman", "crun"]
    assert not any(c[:2] == ["apparmor_parser", "-r"] for c in calls)


def test_successful_retry_never_rolls_back(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",))
    run, calls = _recorder([_proc(126, _SOCKET_DENIED_STDERR), _proc(0), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.fixed is True
    assert result.rolled_back == []
    assert (aa / "disable" / "podman").is_symlink()
    assert not any(c[:2] == ["apparmor_parser", "-r"] for c in calls)


def test_one_unlinkable_entry_does_not_abort_the_rest(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun", "runc"))
    stuck = aa / "disable" / "crun"
    stuck.mkdir(parents=True)
    (stuck / "keep").write_text("x")  # a non-empty dir: unlink() raises OSError
    run, _ = _recorder([_proc(126, _SOCKET_DENIED_STDERR), _proc(0), _proc(0), _proc(0)])

    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)

    assert result.outcome == "guest_profiles_disabled"
    assert result.link_failed == ["crun"]
    assert result.disabled == ["podman", "runc"]
    assert (aa / "disable" / "podman").is_symlink()
    assert (aa / "disable" / "runc").is_symlink()
    assert "could not disable: crun" in (result.detail or "")


def test_every_link_failing_is_unrelated_and_says_why(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",))
    (aa / "disable").write_text("a file where the directory should be")  # mkdir fails
    run, calls = _recorder([_proc(126, _SOCKET_DENIED_STDERR)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.outcome == "unrelated"
    assert "could not create the disable link for podman" in (result.detail or "")
    assert len(calls) == 1  # no parser call, no retry


def test_socket_denied_signature_is_case_insensitive(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",))
    run, _calls = _recorder(
        [_proc(126, "dial udp 9.9.9.9:53: Socket: Permission Denied"), _proc(0), _proc(0)]
    )
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.outcome == "guest_profiles_disabled"
    assert result.fixed is True


def test_profile_without_unconfined_flag_is_not_disabled(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, enforce=("crun",))
    others = [n for n in _PROFILE_NAMES if n != "crun"]
    run, calls = _recorder(
        [_proc(126, _SOCKET_DENIED_STDERR)] + [_proc(0)] * len(others) + [_proc(0)]
    )

    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)

    assert result.outcome == "guest_profiles_disabled"
    assert not (aa / "disable" / "crun").exists()
    assert not (aa / "disable" / "crun").is_symlink()
    for n in others:
        assert (aa / "disable" / n).is_symlink()
    assert [c for c in calls if _is_parser_remove(c)] == [
        ["apparmor_parser", "-R", str(aa / n)] for n in others
    ]
    assert "crun" not in result.disabled


def test_socket_denied_without_profile_files_does_nothing(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    aa.mkdir()
    run, calls = _recorder([_proc(126, _SOCKET_DENIED_STDERR)])

    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)

    assert result.outcome == "unrelated"
    assert "no unconfined guest" in (result.detail or "").lower()
    assert not (aa / "disable").exists()
    assert len(calls) == 1  # no parser call, no retry


def test_socket_denied_with_missing_apparmor_d_does_nothing(tmp_path: Path) -> None:
    run, calls = _recorder([_proc(126, _SOCKET_DENIED_STDERR)])
    result = ca.ensure_podman_apparmor_usable(
        run=run, conf_path=tmp_path / "c.conf", apparmor_d=tmp_path / "nope"
    )
    assert result.outcome == "unrelated"
    assert not (tmp_path / "nope").exists()
    assert len(calls) == 1


def test_1563_signature_keeps_precedence_over_socket_denied(tmp_path: Path) -> None:
    # Regression guard: the #1563 branch still writes containers.conf and never
    # touches the guest profiles — even when the apparmor dir has candidates
    # and the output ALSO mentions a socket denial.
    conf = tmp_path / "containers.conf"
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)
    both = _APPARMOR_STDERR + "\nError: dial: socket: permission denied"
    run, calls = _recorder([_proc(243, both), _proc(0)])

    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf, apparmor_d=aa)

    assert result.outcome == "fixed"
    assert result.wrote_config
    assert 'apparmor_profile = "unconfined"' in conf.read_text()
    assert not (aa / "disable").exists()
    assert not any(_is_parser_remove(c) for c in calls)


def test_unrelated_failure_leaves_guest_profiles_alone(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)
    run, calls = _recorder([_proc(125, "Error: write /var/lib/containers: Disk quota exceeded")])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.outcome == "unrelated"
    assert not (aa / "disable").exists()
    assert len(calls) == 1


def test_disable_is_idempotent_and_still_retries(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)
    n = len(_PROFILE_NAMES)

    run1, _ = _recorder([_proc(126, _SOCKET_DENIED_STDERR)] + [_proc(0)] * n + [_proc(0)])
    first = ca.ensure_podman_apparmor_usable(run=run1, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert first.fixed is True
    snapshot = sorted(p.name for p in (aa / "disable").iterdir())
    targets = {p.name: os.readlink(p) for p in (aa / "disable").iterdir()}

    run2, calls2 = _recorder([_proc(126, _SOCKET_DENIED_STDERR)] + [_proc(0)] * n + [_proc(0)])
    second = ca.ensure_podman_apparmor_usable(
        run=run2, conf_path=tmp_path / "c.conf", apparmor_d=aa
    )

    assert second.outcome == "guest_profiles_disabled"
    assert second.fixed is True
    assert sorted(p.name for p in (aa / "disable").iterdir()) == snapshot  # no new files
    assert {p.name: os.readlink(p) for p in (aa / "disable").iterdir()} == targets
    assert calls2[0][:2] == ["podman", "run"]
    assert calls2[-1][:2] == ["podman", "run"]  # still re-smoked


def test_disable_replaces_stale_link_and_regular_file(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun"))
    (aa / "disable").mkdir()
    (aa / "disable" / "podman").symlink_to(tmp_path / "elsewhere")  # stale link
    (aa / "disable" / "crun").write_text("junk")  # regular file in the way
    run, _ = _recorder([_proc(0), _proc(0)])

    handled = ca.disable_guest_unconfined_profiles(aa, run)

    assert handled == ["podman", "crun"]
    assert Path(os.readlink(aa / "disable" / "podman")) == aa / "podman"
    assert Path(os.readlink(aa / "disable" / "crun")) == aa / "crun"


def test_parser_nonzero_exit_is_not_fatal(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun"))
    run, calls = _recorder([_proc(1, "apparmor_parser: not loaded"), _proc(0)])

    handled = ca.disable_guest_unconfined_profiles(aa, run)

    assert handled == ["podman", "crun"]  # failed unload still counts as handled
    assert (aa / "disable" / "podman").is_symlink()
    assert (aa / "disable" / "crun").is_symlink()
    assert len(calls) == 2


def test_parser_missing_is_not_fatal(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",))

    def _run(argv, **_kw):
        raise FileNotFoundError("apparmor_parser")

    handled = ca.disable_guest_unconfined_profiles(aa, _run)
    assert handled == ["podman"]
    assert (aa / "disable" / "podman").is_symlink()


def test_guest_unconfined_profiles_present_filters_and_preserves_order(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("slirp4netns", "podman", "runc"), enforce=("runc",))
    (aa / "unrelated").write_text("profile unrelated flags=(unconfined) {}\n")  # not in the list
    assert ca.guest_unconfined_profiles_present(aa) == ["podman", "slirp4netns"]
    assert ca.guest_unconfined_profiles_present(tmp_path / "missing") == []


def test_unconfined_flag_must_be_exact_token(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    aa.mkdir()
    (aa / "podman").write_text("profile podman /usr/bin/podman flags=(complain) {}\n")
    assert ca.guest_unconfined_profiles_present(aa) == []


def test_is_unconfined_profile_network_denial() -> None:
    f = ca._is_unconfined_profile_network_denial
    assert f(_proc(126, _SOCKET_DENIED_STDERR))
    assert f(_proc(126, stdout="x: SOCKET: PERMISSION DENIED y"))
    # the verbatim line from the issue
    assert f(_proc(126, "dial udp 9.9.9.9:53: socket: permission denied"))
    # anchored: a path ending in `.socket` is NOT the signature
    assert not f(_proc(126, "open /run/podman/podman.socket: permission denied"))
    assert not f(_proc(126, "Error: Disk quota exceeded"))
    # #1563 shape wins even if it also says socket: permission denied
    assert not f(_proc(243, _APPARMOR_STDERR + "; dial: socket: permission denied"))
    assert not f(_proc(0, _SOCKET_DENIED_STDERR))


def test_apparmor_d_root_honours_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("HAL0_APPARMOR_D", raising=False)
    assert ca.apparmor_d_root() == Path("/etc/apparmor.d")
    monkeypatch.setenv("HAL0_APPARMOR_D", str(tmp_path / "aa"))
    assert ca.apparmor_d_root() == tmp_path / "aa"


def test_env_seam_is_used_when_apparmor_d_not_passed(monkeypatch, tmp_path: Path) -> None:
    aa = tmp_path / "aa"
    _seed_profiles(aa, ("podman",))
    monkeypatch.setenv("HAL0_APPARMOR_D", str(aa))
    run, _ = _recorder([_proc(126, _SOCKET_DENIED_STDERR), _proc(0), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf")
    assert result.outcome == "guest_profiles_disabled"
    assert (aa / "disable" / "podman").is_symlink()


def test_main_prints_summary_for_guest_profiles_disabled(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    aa = tmp_path / "aa"
    monkeypatch.setenv("HAL0_APPARMOR_D", str(aa))

    def _fake(**_kw):
        return ca.ApparmorPreflightResult(
            outcome="guest_profiles_disabled",
            disabled=["podman", "crun"],
            fixed=True,
        )

    monkeypatch.setattr(ca, "ensure_podman_apparmor_usable", _fake)
    monkeypatch.setenv("HAL0_APPARMOR_CONF", str(tmp_path / "c.conf"))
    assert ca._main() == 0
    out = capsys.readouterr().out
    assert (
        "apparmor remediation applied: disabled guest profiles podman, crun - retry passed" in out
    )
    assert "\u2014" not in out  # ASCII only: no em dash on a non-UTF-8 stdout
    # ends with the revert command for exactly the profiles that were disabled
    assert f"revert: rm {aa}/disable/podman && apparmor_parser -r {aa}/podman" in out
    assert f"rm {aa}/disable/crun && apparmor_parser -r {aa}/crun" in out
    assert "slirp4netns" not in out

    def _fake_bad(**_kw):
        return ca.ApparmorPreflightResult(
            outcome="guest_profiles_disabled", disabled=["podman"], fixed=False, detail="boom"
        )

    monkeypatch.setattr(ca, "ensure_podman_apparmor_usable", _fake_bad)
    # an unresolved remediation exits non-zero so install.sh's late re-check notices
    assert ca._main() == ca.EXIT_GUEST_FIX_UNRESOLVED == 3
    out = capsys.readouterr().out
    assert "retry still failing" in out
    assert "apparmor-preflight-detail: boom" in out
    assert ".ns_level=1" in out  # the value the guard read is visible in the log


def test_main_revert_names_only_what_this_run_disabled(monkeypatch, tmp_path: Path, capsys) -> None:
    aa = tmp_path / "aa"
    monkeypatch.setenv("HAL0_APPARMOR_D", str(aa))
    monkeypatch.setenv("HAL0_APPARMOR_CONF", str(tmp_path / "c.conf"))

    def _fake(**_kw):
        return ca.ApparmorPreflightResult(
            outcome="guest_profiles_disabled",
            disabled=["podman", "crun"],
            preexisting=["podman"],  # the operator's own link
            fixed=True,
        )

    monkeypatch.setattr(ca, "ensure_podman_apparmor_usable", _fake)
    assert ca._main() == 0
    out = capsys.readouterr().out
    assert f"revert: rm {aa}/disable/crun && apparmor_parser -r {aa}/crun" in out
    assert "rm " + str(aa) + "/disable/podman" not in out


def test_main_summary_reports_rollback_and_no_revert(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("HAL0_APPARMOR_D", str(tmp_path / "aa"))
    monkeypatch.setenv("HAL0_APPARMOR_CONF", str(tmp_path / "c.conf"))

    def _fake(**_kw):
        return ca.ApparmorPreflightResult(
            outcome="guest_profiles_disabled", rolled_back=["podman"], fixed=False
        )

    monkeypatch.setattr(ca, "ensure_podman_apparmor_usable", _fake)
    assert ca._main() == 3
    out = capsys.readouterr().out
    assert "rolled back (not disabled): podman" in out
    assert "revert:" not in out  # nothing left disabled to revert


# ── final-review wave: namespace guard, anchored token, comments ─────────────


@pytest.mark.parametrize(
    ("content", "shown"),
    [("0\n", "0"), (None, "unreadable"), ("abc\n", "abc"), ("\n", "")],
)
def test_root_namespace_never_mutates(
    monkeypatch, tmp_path: Path, content: str | None, shown: str
) -> None:
    # On bare metal / a privileged LXC the guest `flags=(unconfined)` profiles
    # are the userns grant (kernel.apparmor_restrict_unprivileged_userns=1);
    # disabling them would silently break the operator's own rootless podman.
    ns = tmp_path / "ns_level_case"
    if content is not None:
        ns.write_text(content)
    monkeypatch.setenv("HAL0_APPARMOR_NS_LEVEL_FILE", str(ns))
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)
    run, calls = _recorder([_proc(126, _SOCKET_DENIED_STDERR)])

    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)

    assert result.outcome == "unrelated"
    assert "root namespace" in (result.detail or "")
    assert f".ns_level={shown}" in (result.detail or "")
    assert "nothing changed" in (result.detail or "")
    assert not (aa / "disable").exists()
    assert len(calls) == 1  # no parser call, no retry
    assert not any(_is_parser_remove(c) for c in calls)


def test_nested_namespace_level_two_still_remediates(monkeypatch, tmp_path: Path) -> None:
    ns = tmp_path / "ns_level_two"
    ns.write_text("2\n")
    monkeypatch.setenv("HAL0_APPARMOR_NS_LEVEL_FILE", str(ns))
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",))
    run, _ = _recorder([_proc(126, _SOCKET_DENIED_STDERR), _proc(0), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.outcome == "guest_profiles_disabled"


def test_ns_level_file_default_path(monkeypatch) -> None:
    monkeypatch.delenv("HAL0_APPARMOR_NS_LEVEL_FILE", raising=False)
    assert ca.apparmor_ns_level_file() == Path("/sys/kernel/security/apparmor/.ns_level")


def test_issue_verbatim_line_is_remediated(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",))
    run, _ = _recorder(
        [_proc(126, "dial udp 9.9.9.9:53: socket: permission denied"), _proc(0), _proc(0)]
    )
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.outcome == "guest_profiles_disabled"
    assert result.fixed is True


def test_podman_socket_path_denial_is_not_the_signature(tmp_path: Path) -> None:
    # `open /run/podman/podman.socket: permission denied` is a plain file
    # permission error, not the AppArmor socket() denial: untouched.
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)
    run, calls = _recorder([_proc(125, "Error: open /run/podman/podman.socket: permission denied")])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.outcome == "unrelated"
    assert not (aa / "disable").exists()
    assert len(calls) == 1


def test_commented_out_unconfined_flag_does_not_count(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    aa.mkdir()
    (aa / "podman").write_text(
        "# was flags=(unconfined) before the operator tightened it\n"
        "profile podman /usr/bin/podman {  # flags=(unconfined)\n  userns,\n}\n"
    )
    (aa / "crun").write_text(
        "profile crun /usr/bin/crun flags=(unconfined) { # trailing comment\n  userns,\n}\n"
    )
    assert ca.guest_unconfined_profiles_present(aa) == ["crun"]


# ── Greptile round: whole-file safety, unload outcome ────────────────────────
#
# The disable symlink and `apparmor_parser -R` act on a WHOLE profile file, so a
# file is only selected when EVERY profile block header in it carries
# `flags=(unconfined)`. The stock Ubuntu 24.04 stubs have one-line headers
# (`profile podman /usr/bin/podman flags=(unconfined) {`); a header split across
# lines is not recognised and the file is skipped (and logged), never guessed.

_STOCK_STUB = (
    "# This profile allows everything and only exists to give the\n"
    '# application a name instead of having the label "unconfined"\n'
    "\n"
    "abi <abi/4.0>,\n"
    "include <tunables/global>\n"
    "\n"
    "profile podman /usr/bin/podman flags=(unconfined) {\n"
    "  userns,\n"
    "\n"
    "  # Site-specific additions and overrides. See local/README for details.\n"
    "  include if exists <local/podman>\n"
    "}\n"
)


class _LogRecorder:
    """Stand-in for the module logger: records ``(level, event, kw)``."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def info(self, event: str, **kw) -> None:
        self.events.append(("info", event, kw))

    def warning(self, event: str, **kw) -> None:
        self.events.append(("warning", event, kw))

    def warnings(self) -> list[tuple[str, dict]]:
        return [(e, kw) for lvl, e, kw in self.events if lvl == "warning"]


@pytest.fixture
def _log(monkeypatch) -> _LogRecorder:
    rec = _LogRecorder()
    monkeypatch.setattr(ca, "log", rec)
    return rec


def test_stock_stub_is_selected(tmp_path: Path, _log: _LogRecorder) -> None:
    aa = tmp_path / "apparmor.d"
    aa.mkdir()
    (aa / "podman").write_text(_STOCK_STUB)
    assert ca.guest_unconfined_profiles_present(aa) == ["podman"]
    assert _log.warnings() == []


def test_mixed_enforced_and_unconfined_profiles_in_one_file_is_skipped(
    tmp_path: Path, _log: _LogRecorder
) -> None:
    # Disabling the file would unload the ENFORCED profile too: leave it alone.
    aa = tmp_path / "apparmor.d"
    aa.mkdir()
    (aa / "podman").write_text(
        _STOCK_STUB + "\nprofile podman-hardened /usr/local/bin/podman-h {\n  userns,\n}\n"
    )
    (aa / "crun").write_text(_STOCK_STUB.replace("podman", "crun"))

    assert ca.guest_unconfined_profiles_present(aa) == ["crun"]
    skipped = [kw for ev, kw in _log.warnings() if ev == "containers_apparmor.profile_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["profile"] == "podman"
    assert "flags=(unconfined)" in skipped[0]["reason"]
    assert "podman-hardened" in skipped[0]["reason"]


def test_mixed_file_is_not_disabled_end_to_end(tmp_path: Path, _log: _LogRecorder) -> None:
    aa = tmp_path / "apparmor.d"
    aa.mkdir()
    (aa / "podman").write_text(_STOCK_STUB + "\nprofile enforced /usr/bin/e {\n  userns,\n}\n")
    run, calls = _recorder([_proc(126, _SOCKET_DENIED_STDERR)])

    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)

    assert result.outcome == "unrelated"
    assert not (aa / "disable").exists()
    assert len(calls) == 1  # no parser call, no retry


def test_every_header_flagged_in_a_multi_profile_file_is_selected(
    tmp_path: Path, _log: _LogRecorder
) -> None:
    aa = tmp_path / "apparmor.d"
    aa.mkdir()
    (aa / "runc").write_text(
        _STOCK_STUB.replace("podman", "runc")
        + "\nprofile runc-alt /usr/bin/runc-alt flags=(unconfined) {\n  userns,\n}\n"
    )
    assert ca.guest_unconfined_profiles_present(aa) == ["runc"]


def test_bare_path_header_counts_and_must_carry_the_flag(
    tmp_path: Path, _log: _LogRecorder
) -> None:
    aa = tmp_path / "apparmor.d"
    aa.mkdir()
    (aa / "podman").write_text("/usr/bin/podman flags=(unconfined) {\n  userns,\n}\n")
    (aa / "crun").write_text(
        "/usr/bin/crun flags=(unconfined) {\n  userns,\n}\n/usr/bin/other {\n  userns,\n}\n"
    )
    assert ca.guest_unconfined_profiles_present(aa) == ["podman"]


def test_only_profile_block_headers_are_headers() -> None:
    text = (
        "abi <abi/4.0>,\n"
        "include <tunables/global>\n"
        "include if exists <local/podman>\n"
        "profile podman /usr/bin/podman flags=(unconfined) {  # trailing comment\n"
        "  userns,\n"
        "  network inet stream,\n"
        "  /etc/{a,b} r,\n"
        "  # profile commented /usr/bin/x {\n"
        "}\n"
    )
    assert ca._profile_headers(text) == ["profile podman /usr/bin/podman flags=(unconfined) {"]
    assert ca._profile_headers("include <tunables/global>\n}\n") == []


def test_split_header_is_skipped_and_logged_not_guessed(tmp_path: Path, _log: _LogRecorder) -> None:
    # The stock stubs use one-line headers; a header whose `{` sits on the next
    # line is not recognised, so the file is skipped (fail safe) with a reason.
    aa = tmp_path / "apparmor.d"
    aa.mkdir()
    (aa / "podman").write_text(
        "profile podman /usr/bin/podman flags=(unconfined)\n{\n  userns,\n}\n"
    )
    assert ca.guest_unconfined_profiles_present(aa) == []
    skipped = [kw for ev, kw in _log.warnings() if ev == "containers_apparmor.profile_skipped"]
    assert [kw["profile"] for kw in skipped] == ["podman"]
    assert "header" in skipped[0]["reason"]


def test_enforced_profile_is_skipped_silently(tmp_path: Path, _log: _LogRecorder) -> None:
    # An ordinary enforced profile is the normal case, not worth a warning.
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",), enforce=("podman",))
    assert ca.guest_unconfined_profiles_present(aa) == []
    assert _log.warnings() == []


def test_unload_failure_is_recorded_by_name_and_surfaced_in_detail(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun", "runc"))
    # smoke denial; parser: podman ok, crun exits 1, runc exits 1; retry passes
    run, _ = _recorder(
        [
            _proc(126, _SOCKET_DENIED_STDERR),
            _proc(0),
            _proc(1, "apparmor_parser: unable to remove"),
            _proc(1, "apparmor_parser: unable to remove"),
            _proc(0),
        ]
    )

    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)

    assert result.outcome == "guest_profiles_disabled"
    assert result.fixed is True
    assert result.disabled == ["podman", "crun", "runc"]
    assert result.unload_failed == ["crun", "runc"]
    assert "unload failed: crun, runc" in (result.detail or "")
    # symlinks are still in place: the disable persists across reboots
    for n in ("podman", "crun", "runc"):
        assert (aa / "disable" / n).is_symlink()


def test_missing_parser_counts_as_unload_failed(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun"))
    seen = {"smoke": 0}

    def _run(argv, **_kw):
        if argv[0] == "apparmor_parser":
            raise FileNotFoundError("apparmor_parser")
        seen["smoke"] += 1
        return _proc(126, _SOCKET_DENIED_STDERR) if seen["smoke"] == 1 else _proc(0)

    result = ca.ensure_podman_apparmor_usable(
        run=_run, conf_path=tmp_path / "c.conf", apparmor_d=aa
    )

    assert result.outcome == "guest_profiles_disabled"
    assert result.unload_failed == ["podman", "crun"]


def test_clean_unload_has_no_unload_failed_and_no_detail(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",))
    run, _ = _recorder([_proc(126, _SOCKET_DENIED_STDERR), _proc(0), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.unload_failed == []
    assert result.detail is None


def test_retry_failure_and_unload_failure_are_both_in_detail(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman",))
    run, _ = _recorder(
        [
            _proc(126, _SOCKET_DENIED_STDERR),
            _proc(1, "nope"),
            _proc(126, _SOCKET_DENIED_STDERR),
        ]
    )
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=tmp_path / "c.conf", apparmor_d=aa)
    assert result.fixed is False
    assert "socket: permission denied" in (result.detail or "")
    assert "unload failed: podman" in (result.detail or "")
    # inconclusive retry (the profile may still be loaded): the link is kept
    assert result.rolled_back == []
    assert (aa / "disable" / "podman").is_symlink()


def test_public_disable_helper_still_returns_names_only(tmp_path: Path) -> None:
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun"))
    run, _ = _recorder([_proc(1, "boom"), _proc(0)])
    assert ca.disable_guest_unconfined_profiles(aa, run) == ["podman", "crun"]


def test_main_summary_lists_unload_failures(monkeypatch, tmp_path: Path, capsys) -> None:
    aa = tmp_path / "aa"
    monkeypatch.setenv("HAL0_APPARMOR_D", str(aa))
    monkeypatch.setenv("HAL0_APPARMOR_CONF", str(tmp_path / "c.conf"))

    def _fake(**_kw):
        return ca.ApparmorPreflightResult(
            outcome="guest_profiles_disabled",
            disabled=["podman", "crun", "runc"],
            unload_failed=["crun", "runc"],
            fixed=False,
            detail="boom; unload failed: crun, runc",
        )

    monkeypatch.setattr(ca, "ensure_podman_apparmor_usable", _fake)
    assert ca._main() == 3  # fixed=False -> non-zero
    out = capsys.readouterr().out
    assert "unload failed: crun, runc" in out
    assert "podman, crun, runc" in out  # the disabled list is still reported
    assert "—" not in out  # ASCII only

    def _fake_clean(**_kw):
        return ca.ApparmorPreflightResult(
            outcome="guest_profiles_disabled", disabled=["podman"], fixed=True
        )

    monkeypatch.setattr(ca, "ensure_podman_apparmor_usable", _fake_clean)
    assert ca._main() == 0
    assert "unload failed" not in capsys.readouterr().out


def _exec(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def test_main_end_to_end_with_a_failing_apparmor_parser(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    # Real subprocess path: a `podman` shim (denied once, then fine) and an
    # `apparmor_parser` shim that exits 1.
    shims = tmp_path / "bin"
    shims.mkdir()
    counter = tmp_path / "podman-calls"
    _exec(
        shims / "podman",
        f"""
n=0
[[ -f {counter!s} ]] && n=$(cat {counter!s})
n=$((n + 1))
echo "$n" > {counter!s}
if [[ "$n" -le 1 ]]; then
    echo 'dial udp 9.9.9.9:53: socket: permission denied' >&2
    exit 126
fi
exit 0
""",
    )
    _exec(shims / "apparmor_parser", "echo 'apparmor_parser: unable to remove' >&2\nexit 1")
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun"))
    monkeypatch.setenv("PATH", f"{shims}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HAL0_APPARMOR_D", str(aa))
    monkeypatch.setenv("HAL0_APPARMOR_CONF", str(tmp_path / "c.conf"))

    rc = ca._main()

    out = capsys.readouterr().out
    assert rc == 0
    assert "apparmor-preflight: guest_profiles_disabled" in out
    assert "retry passed" in out
    assert "unload failed: podman, crun" in out
    assert (aa / "disable" / "podman").is_symlink()


# ── --nested-box (podman box launcher) ───────────────────────────────────────


def _seed_box(aa: Path) -> None:
    _seed_profiles(aa, ("podman", "runc"), enforce=("runc",))
    (aa / "usr.bin.pasta").write_text("profile pasta /usr/bin/pasta {\n}\n")
    (aa / "usr.bin.passt").write_text("profile passt /usr/bin/passt {\n}\n")
    (aa / "usr.bin.other").write_text("profile other /usr/bin/other {\n}\n")


def test_nested_only_disables_pasta_profiles(tmp_path: Path) -> None:
    aa = tmp_path / "aa"
    _seed_box(aa)
    nested = ca.nested_disable_list(aa, ns_level=1)
    assert nested == ["podman", "usr.bin.pasta", "usr.bin.passt"]
    assert "usr.bin.other" not in nested and "runc" not in nested
    assert ca.nested_disable_list(aa, ns_level=0) == []


def test_nested_list_skips_absent_pasta_profiles(tmp_path: Path) -> None:
    aa = tmp_path / "aa"
    aa.mkdir()
    assert ca.nested_disable_list(aa, ns_level=2) == []


def _box_env(monkeypatch, tmp_path: Path, level: str) -> Path:
    aa = tmp_path / "aa"
    _seed_box(aa)
    lvl = tmp_path / "ns_level"
    lvl.write_text(level)
    monkeypatch.setenv("HAL0_APPARMOR_D", str(aa))
    monkeypatch.setenv("HAL0_APPARMOR_NS_LEVEL_FILE", str(lvl))
    return aa


def test_nested_box_main_bare_metal_changes_nothing(monkeypatch, tmp_path: Path) -> None:
    aa = _box_env(monkeypatch, tmp_path, "0\n")
    monkeypatch.setattr("sys.argv", ["containers_apparmor.py", "--nested-box"])
    assert ca._main() == 0
    assert not (aa / "disable").exists()


def test_nested_box_main_unreadable_ns_level_changes_nothing(monkeypatch, tmp_path: Path) -> None:
    aa = _box_env(monkeypatch, tmp_path, "0")
    monkeypatch.setenv("HAL0_APPARMOR_NS_LEVEL_FILE", str(tmp_path / "missing"))
    monkeypatch.setattr("sys.argv", ["containers_apparmor.py", "--nested-box"])
    assert ca._main() == 0
    assert not (aa / "disable").exists()


def test_nested_box_main_disables_list(monkeypatch, tmp_path: Path) -> None:
    aa = _box_env(monkeypatch, tmp_path, "1\n")
    calls: list[list[str]] = []

    def _run(argv, **_kw):
        calls.append(list(argv))
        return _proc(0)

    monkeypatch.setattr(ca.subprocess, "run", _run)
    assert ca._nested_box_main(_run) == 0
    for name in ("podman", "usr.bin.pasta", "usr.bin.passt"):
        assert (aa / "disable" / name).is_symlink()
    assert not (aa / "disable" / "usr.bin.other").exists()
    assert not (aa / "disable" / "runc").exists()
    assert [c[0] for c in calls] == ["apparmor_parser"] * 3


def test_nested_box_revert_lines_name_only_what_this_run_disabled(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    # The box launcher records the revert lines and undoes them on uninstall,
    # so a profile the operator had disabled already must not be listed.
    aa = _box_env(monkeypatch, tmp_path, "1\n")
    (aa / "disable").mkdir()
    (aa / "disable" / "podman").symlink_to(aa / "podman")

    assert ca._nested_box_main(lambda argv, **_kw: _proc(0)) == 0
    reverts = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("revert:")]

    assert reverts == [
        f"revert: rm {aa}/disable/{n} && apparmor_parser -r {aa}/{n}"
        for n in ("usr.bin.pasta", "usr.bin.passt")
    ]
    assert (aa / "disable" / "podman").is_symlink()

    # A second run disabled nothing new, so it names nothing to revert.
    assert ca._nested_box_main(lambda argv, **_kw: _proc(0)) == 0
    assert "revert:" not in capsys.readouterr().out


def test_nested_box_runs_as_bare_script_without_hal0(tmp_path: Path) -> None:
    import subprocess
    import sys

    script = Path(ca.__file__)
    lvl = tmp_path / "ns_level"
    lvl.write_text("0\n")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HAL0_APPARMOR_NS_LEVEL_FILE": str(lvl),
        "HAL0_APPARMOR_D": str(tmp_path / "aa"),
    }
    proc = subprocess.run(
        [sys.executable, "-I", str(script), "--nested-box"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "nothing changed" in proc.stdout
