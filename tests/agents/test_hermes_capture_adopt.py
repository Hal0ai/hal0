"""Capture/adoption hardening for `hal0 agent bootstrap hermes`.

Two audits found the bootstrap couldn't safely capture an existing (foreign)
hermes install: the claim guard was cosmetic (run-all meant later phases
clobbered the foreign config anyway), no tokens were imported, a foreign
gateway went undetected, and config.yaml/SOUL.md/the wrapper were overwritten
with no backup. These tests pin the surgical fixes — fatal-abort claim,
``--adopt`` (backup + token import + marker), foreign-gateway preflight,
ownership reconcile, and the wrapper/config snapshots.

All seams (subprocess, os.chown, the vault + agents-dir path constants) are
injected or monkeypatched to a tmp path so the suite stays hermetic — no real
systemd, no writes under /var/lib/hal0.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hal0.agents import hermes_provision as hp


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


# ── PhaseResult.fatal ────────────────────────────────────────────────────────


def test_phase_result_fatal_serialises_only_when_true() -> None:
    plain = hp.PhaseResult(status=hp.PhaseStatus.FAIL, reason="x")
    assert "fatal" not in plain.to_dict()
    fatal = hp.PhaseResult(status=hp.PhaseStatus.FAIL, reason="x", fatal=True)
    assert fatal.to_dict()["fatal"] is True


# ── _home_is_foreign / claim refusal + adopt ─────────────────────────────────


def test_home_is_foreign_only_for_populated_unmarked_dir(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert hp._home_is_foreign(empty) is False  # populated? no

    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / hp._HAL0_MANAGED_MARKER).write_text("x")
    (managed / "config.yaml").write_text("a: 1")
    assert hp._home_is_foreign(managed) is False  # marker present

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "config.yaml").write_text("a: 1")
    assert hp._home_is_foreign(foreign) is True


def test_claim_refuses_foreign_home_without_adopt(tmp_path: Path) -> None:
    home = tmp_path / "hh"
    home.mkdir()
    (home / "config.yaml").write_text("user: config")
    claimed, reason, details = hp._claim_hermes_home(home, adopt=False)
    assert claimed is False
    assert details is None
    assert "not hal0-managed" in (reason or "")
    assert "--adopt" in (reason or "")
    # It must NOT have stamped the marker on refusal.
    assert not (home / hp._HAL0_MANAGED_MARKER).exists()


def test_claim_adopts_foreign_home_when_adopt(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "hh"
    home.mkdir()
    (home / "config.yaml").write_text("user: config\n")
    (home / "SOUL.md").write_text("# user soul\n")
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=abc123\nUNRELATED=keep-me\n")
    vault = tmp_path / "vault" / "hermes.env"
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", vault)

    claimed, reason, details = hp._claim_hermes_home(home, adopt=True)
    assert claimed is True
    assert reason is None
    assert details is not None
    # Marker stamped so subsequent phases proceed.
    assert (home / hp._HAL0_MANAGED_MARKER).is_file()
    # Full-tree backup with the pre-hal0 prefix.
    backup = Path(details["backup_dir"])
    assert backup.is_dir()
    assert backup.name.startswith("hh.pre-hal0-")
    assert (backup / "config.yaml").read_text() == "user: config\n"
    # Sidecar snapshots inside the home.
    assert (home / "config.yaml.pre-hal0").read_text() == "user: config\n"
    assert (home / "SOUL.md.pre-hal0").read_text() == "# user soul\n"
    # Token imported into the vault; original .env untouched.
    assert details["tokens_imported"] == ["TELEGRAM_BOT_TOKEN"]
    assert "TELEGRAM_BOT_TOKEN=abc123" in vault.read_text()
    assert (home / ".env").read_text() == "TELEGRAM_BOT_TOKEN=abc123\nUNRELATED=keep-me\n"


# ── .env token import — prefix filter + vault preservation ───────────────────


def test_parse_env_secrets_only_recognized_prefixes(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "# comment",
                "TELEGRAM_BOT_TOKEN=tg",
                "export DISCORD_TOKEN=dc",
                "OPENROUTER_API_KEY=or",
                "OPENAI_API_KEY=oa",
                "ANTHROPIC_API_KEY=an",
                "FAL_KEY=fk",
                "HERMES_SECRET=hs",
                "NOT_A_SECRET=nope",
                "PATH=/usr/bin",
                "malformed line without equals",
                "",
            ]
        )
    )
    got = hp._parse_env_secrets(env)
    assert got == {
        "TELEGRAM_BOT_TOKEN": "tg",
        "DISCORD_TOKEN": "dc",
        "OPENROUTER_API_KEY": "or",
        "OPENAI_API_KEY": "oa",
        "ANTHROPIC_API_KEY": "an",
        "FAL_KEY": "fk",
        "HERMES_SECRET": "hs",
    }


def test_parse_env_secrets_missing_file_is_empty(tmp_path: Path) -> None:
    assert hp._parse_env_secrets(tmp_path / "nope.env") == {}


def test_adopt_token_import_preserves_existing_vault_lines(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "hh"
    home.mkdir()
    (home / "config.yaml").write_text("x: 1\n")
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=new\n")
    vault = tmp_path / "vault" / "hermes.env"
    vault.parent.mkdir(parents=True)
    # Pre-existing vault line an operator/earlier phase wrote — must survive.
    vault.write_text("STT_OPENAI_BASE_URL=http://127.0.0.1:8087/v1\nTELEGRAM_BOT_TOKEN=old\n")
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", vault)

    hp._adopt_foreign_home(home)
    body = vault.read_text()
    assert "STT_OPENAI_BASE_URL=http://127.0.0.1:8087/v1" in body  # preserved
    assert "TELEGRAM_BOT_TOKEN=new" in body  # replaced
    assert "TELEGRAM_BOT_TOKEN=old" not in body


# ── wrapper backup ───────────────────────────────────────────────────────────


def _read_wrapper_src() -> Path:
    """The canonical managed wrapper source used by the install phase."""
    return hp.REPO_ROOT_FOR_INSTALLER / "installer" / "wrappers" / "hermes"


def test_copy_wrapper_backs_up_foreign_binary(tmp_path: Path) -> None:
    src = _read_wrapper_src()
    dst = tmp_path / "bin" / "hermes"
    dst.parent.mkdir(parents=True)
    # A pre-existing FOREIGN hermes (upstream shim / binary) with no hal0 marker.
    dst.write_text("#!/bin/sh\n# upstream hermes, hand-installed\nexec /opt/hermes \"$@\"\n")

    hp._copy_wrapper(src, dst)

    backup = dst.with_name("hermes.pre-hal0")
    assert backup.is_file()
    assert "hand-installed" in backup.read_text()
    # The live wrapper is now the hal0-managed one (carries the marker).
    assert hp._MANAGED_WRAPPER_MARKER in dst.read_text()


def test_copy_wrapper_no_backup_for_managed_wrapper(tmp_path: Path) -> None:
    src = _read_wrapper_src()
    dst = tmp_path / "bin" / "hermes"
    dst.parent.mkdir(parents=True)
    # Pre-existing dst is already ours (steady-state re-run) — no backup churn.
    dst.write_text(src.read_text())

    hp._copy_wrapper(src, dst)
    assert not dst.with_name("hermes.pre-hal0").exists()


def test_copy_wrapper_no_backup_on_fresh_install(tmp_path: Path) -> None:
    src = _read_wrapper_src()
    dst = tmp_path / "bin" / "hermes"  # doesn't exist yet
    hp._copy_wrapper(src, dst)
    assert dst.is_file()
    assert not dst.with_name("hermes.pre-hal0").exists()


# ── config.yaml.bak snapshot before mutation ─────────────────────────────────


def test_config_write_snapshots_existing_config_before_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    from ._hermes_fakes import fake_hermes_run

    hermes_home = tmp_path / "hh"
    hermes_home.mkdir()
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "hermes").write_text("#!/bin/sh\n")
    original = "hand: edited\nmodel:\n  default: keepme\n"
    (hermes_home / "config.yaml").write_text(original)

    state = hp.BootstrapState(venv=str(venv), hermes_home=str(hermes_home))
    io = hp.PhaseIO(
        run=fake_hermes_run(),
        fetch_slots=lambda: [],
        fetch_model_contexts=lambda: {},
    )
    out = hp._phase_config_write(hp.context_for("config_write", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    bak = hermes_home / "config.yaml.bak"
    assert bak.is_file()
    # The .bak holds the PRE-mutation content, byte-for-byte.
    assert bak.read_text() == original


def test_config_write_no_bak_when_no_existing_config(tmp_path: Path) -> None:
    from ._hermes_fakes import fake_hermes_run

    hermes_home = tmp_path / "hh"
    hermes_home.mkdir()
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "hermes").write_text("#!/bin/sh\n")
    state = hp.BootstrapState(venv=str(venv), hermes_home=str(hermes_home))
    io = hp.PhaseIO(run=fake_hermes_run(), fetch_slots=lambda: [], fetch_model_contexts=lambda: {})
    hp._phase_config_write(hp.context_for("config_write", state, io=io))
    # No pre-existing config → nothing to back up (migrate creates it fresh).
    assert not (hermes_home / "config.yaml.bak").exists()


# ── foreign-gateway detection ────────────────────────────────────────────────


def _fake_run_factory(*, active: bool, procs: str = ""):
    def _run(argv: Any, *_a: Any, **_kw: Any) -> Any:
        head = list(argv[:2])
        if head == ["systemctl", "is-active"]:
            return _Completed(returncode=0 if active else 3, stdout="active" if active else "inactive")
        if argv and argv[0] == "pgrep":
            return _Completed(returncode=0 if procs else 1, stdout=procs)
        return _Completed()

    return _run


def test_detect_foreign_gateway_user_scope_unit(tmp_path: Path) -> None:
    user_dir = tmp_path / "root" / ".config" / "systemd" / "user"
    user_dir.mkdir(parents=True)
    (user_dir / hp.GATEWAY_UNIT_NAME).write_text("[Unit]\n")

    found = hp._detect_foreign_gateways(
        run=_fake_run_factory(active=False),
        scan_globs=(str(tmp_path / "root" / ".config" / "systemd" / "user"),),
        dropin_file=tmp_path / "absent-dropin.conf",
    )
    assert len(found) == 1
    assert found[0]["scope"] == "user"
    assert "systemctl --user disable --now" in found[0]["stop_cmd"]


def test_detect_foreign_gateway_system_scope_without_dropin(tmp_path: Path) -> None:
    found = hp._detect_foreign_gateways(
        run=_fake_run_factory(active=True, procs="146 hermes gateway run"),
        scan_globs=(str(tmp_path / "none"),),
        dropin_file=tmp_path / "absent-dropin.conf",
    )
    assert len(found) == 1
    assert found[0]["scope"] == "system"
    # pgrep output attaches as corroborating evidence.
    assert "hermes gateway run" in found[0].get("processes", "")


def test_detect_no_foreign_gateway_when_hal0_dropin_present(tmp_path: Path) -> None:
    dropin = tmp_path / "10-hal0-secrets.conf"
    dropin.write_text("[Service]\n")  # hal0 manages the system unit here
    found = hp._detect_foreign_gateways(
        run=_fake_run_factory(active=True, procs="146 hermes gateway run"),
        scan_globs=(str(tmp_path / "none"),),
        dropin_file=dropin,
    )
    assert found == []


def test_detect_foreign_gateway_best_effort_on_subprocess_error(tmp_path: Path) -> None:
    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise OSError("systemctl missing")

    # No user units, systemctl raises → best-effort empty, never raises.
    found = hp._detect_foreign_gateways(
        run=_boom, scan_globs=(str(tmp_path / "none"),), dropin_file=tmp_path / "absent.conf"
    )
    assert found == []


# ── preflight: foreign gateway → fatal without adopt, warning with adopt ──────


def _preflight_ctx(state: hp.BootstrapState, *, adopt: bool, run: Any) -> hp.PhaseContext:
    return hp.context_for("preflight", state, adopt=adopt, io=hp.PhaseIO(http_get=lambda *_a, **_k: 200, run=run))


def test_preflight_fatal_on_foreign_gateway_without_adopt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(hp, "MIN_FREE_GIB", 0)
    user_dir = tmp_path / "root" / ".config" / "systemd" / "user"
    user_dir.mkdir(parents=True)
    (user_dir / hp.GATEWAY_UNIT_NAME).write_text("[Unit]\n")
    monkeypatch.setattr(hp, "_USER_SYSTEMD_SCAN_GLOBS", (str(user_dir),))
    monkeypatch.setattr(hp, "GATEWAY_SYSTEMD_DROPIN_FILE", tmp_path / "absent.conf")

    state = hp.BootstrapState(venv=str(tmp_path / "v"), hermes_home=str(tmp_path / "hh"))
    out = hp._phase_preflight(_preflight_ctx(state, adopt=False, run=_fake_run_factory(active=False)))
    assert out.status == hp.PhaseStatus.FAIL
    assert out.fatal is True
    assert "foreign hermes gateway" in (out.reason or "")
    assert out.details["foreign_gateways"]


def test_preflight_warns_not_fatal_on_foreign_gateway_with_adopt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(hp, "MIN_FREE_GIB", 0)
    user_dir = tmp_path / "root" / ".config" / "systemd" / "user"
    user_dir.mkdir(parents=True)
    (user_dir / hp.GATEWAY_UNIT_NAME).write_text("[Unit]\n")
    monkeypatch.setattr(hp, "_USER_SYSTEMD_SCAN_GLOBS", (str(user_dir),))
    monkeypatch.setattr(hp, "GATEWAY_SYSTEMD_DROPIN_FILE", tmp_path / "absent.conf")

    state = hp.BootstrapState(venv=str(tmp_path / "v"), hermes_home=str(tmp_path / "hh"))
    out = hp._phase_preflight(_preflight_ctx(state, adopt=True, run=_fake_run_factory(active=False)))
    # Adopt downgrades the abort to a warning: preflight still passes.
    assert out.status == hp.PhaseStatus.OK
    assert out.fatal is False
    assert "foreign_gateway_warning" in out.details
    assert "will NOT auto-stop" in out.details["foreign_gateway_warning"]


# ── _phase_install: claim before any mutation (blocking review finding) ──────


def test_install_aborts_before_any_mutation_on_foreign_home_without_adopt(
    tmp_path: Path, monkeypatch
) -> None:
    """A foreign (populated, unmarked) home without --adopt must fatal-abort
    BEFORE building the venv or swapping /usr/local/bin/hermes — a true no-op
    abort, not one that swaps the system entrypoint and only then bails."""
    hermes_home = tmp_path / "hh"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("operator: cfg\n")  # foreign, unmarked

    venv = tmp_path / "venv"
    wrapper_dst = tmp_path / "usr" / "bin" / "hermes"
    wrapper_dst.parent.mkdir(parents=True)
    foreign_wrapper = "#!/bin/sh\n# foreign upstream hermes\nexec /opt/hermes \"$@\"\n"
    wrapper_dst.write_text(foreign_wrapper)
    monkeypatch.setattr(hp, "HERMES_CLI_INSTALL_PATH", wrapper_dst)
    monkeypatch.setattr(hp, "WRAPPER_INSTALL_PATH", tmp_path / "usr" / "bin" / "hal0-hermes")

    install_calls: list[Any] = []
    io = hp.PhaseIO(install_venv=lambda *a, **_k: install_calls.append(a))
    state = hp.BootstrapState(venv=str(venv), hermes_home=str(hermes_home))

    out = hp._phase_install(hp.context_for("install", state, io=io))  # adopt defaults False

    assert out.status == hp.PhaseStatus.FAIL
    assert out.fatal is True
    # Nothing was mutated: venv not built, the system `hermes` not swapped (nor
    # even backed up), and the home stays unclaimed.
    assert install_calls == []
    assert not (venv / "bin").exists()
    assert wrapper_dst.read_text() == foreign_wrapper
    assert not wrapper_dst.with_name("hermes.pre-hal0").exists()
    assert not (hermes_home / hp._HAL0_MANAGED_MARKER).exists()
