"""Tests for ``hal0 doctor bundle`` (§21.4 §3).

Runs the real ``build_bundle`` orchestration against ``tmp_hal0_home`` with
no live API. Tests that assert missing-tool degradation force that condition
explicitly instead of depending on binaries installed in the test sandbox.
Asserts: the layout lands, the manifest is well-formed, ``commands.tsv`` has
one row per probe, and a sensitive-keyed ``hal0.toml`` value gets redacted
rather than echoed.
"""

from __future__ import annotations

import json as jsonlib
from pathlib import Path

import pytest

from hal0.cli import doctor_bundle
from hal0.cli.doctor_bundle import build_bundle


@pytest.fixture(autouse=True)
def _no_live_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every diagnostics/*.json GET degrades to unreachable — no API is up."""
    from hal0.cli import _shared

    def _boom(path: str, *, base=None, **kw):
        raise _shared.CliApiError(f"{path} unreachable")

    monkeypatch.setattr(_shared, "api_get", _boom)


def _force_rocminfo_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make only rocminfo unavailable while preserving every other real probe."""
    original_run = doctor_bundle.subprocess.run

    def run_without_rocminfo(argv, *args, **kwargs):
        if argv[0] == "rocminfo":
            raise FileNotFoundError("forced missing rocminfo")
        return original_run(argv, *args, **kwargs)

    monkeypatch.setattr(doctor_bundle.subprocess, "run", run_without_rocminfo)


def test_bundle_layout_matches_spec(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_rocminfo_missing(monkeypatch)
    out = tmp_path / "bundle"
    written, _failed = build_bundle(out)

    assert written == out
    assert (out / "manifest.json").is_file()
    assert (out / "commands.tsv").is_file()
    assert (out / "system").is_dir()
    assert (out / "diagnostics").is_dir()
    assert (out / "doctor-summary.txt").is_file()
    # Every _CORE_PROBES command produced SOME file (present or a stub).
    assert (out / "system" / "uname.txt").is_file()
    # The deterministically missing rocminfo binary degrades to a stub.
    assert "not found" in (out / "system" / "rocminfo.txt").read_text()


def test_bundle_manifest_shape(tmp_hal0_home: str, tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    build_bundle(out)
    manifest = jsonlib.loads((out / "manifest.json").read_text())
    assert manifest["schema_version"] == 1
    assert manifest["hostname"]
    assert "generated_at_utc" in manifest
    assert set(manifest["sections"]) >= {"manifest.json", "commands.tsv", "system/", "config/"}
    assert isinstance(manifest["redaction_applied"], list)
    assert "SECRET" in manifest["redaction_policy"]
    assert manifest["command_count"] > 0


def test_commands_tsv_has_header_and_one_row_per_probe(tmp_hal0_home: str, tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    build_bundle(out, include_rocm_smi=False)
    lines = (out / "commands.tsv").read_text().splitlines()
    assert lines[0].split("\t") == [
        "command",
        "exit_code",
        "stdout_bytes",
        "stderr_bytes",
        "ts_utc",
        "duration_ms",
    ]
    # header + one row per _CORE_PROBES entry (rocm probes skipped).
    from hal0.cli.doctor_bundle import _CORE_PROBES

    assert len(lines) == 1 + len(_CORE_PROBES)


def test_bundle_skips_rocm_probes_when_disabled(tmp_hal0_home: str, tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    build_bundle(out, include_rocm_smi=False)
    assert not (out / "system" / "rocminfo.txt").exists()


def test_bundle_redacts_sensitive_toml_keys(tmp_hal0_home: str, tmp_path: Path) -> None:
    from hal0.config import paths as cfg_paths

    hal0_toml = cfg_paths.hal0_toml()
    hal0_toml.parent.mkdir(parents=True, exist_ok=True)
    hal0_toml.write_text(
        '[upstreams.openai]\napi_key = "sk-super-secret-value"\nbase_url = "https://api.openai.com"\n'
    )

    out = tmp_path / "bundle"
    build_bundle(out, include_rocm_smi=False)

    dumped = (out / "config" / "hal0.toml").read_text()
    assert "sk-super-secret-value" not in dumped
    assert "REDACTED" in dumped
    # non-sensitive keys pass through unredacted.
    assert "api.openai.com" in dumped

    manifest = jsonlib.loads((out / "manifest.json").read_text())
    assert "config/hal0.toml" in manifest["redaction_applied"]


def test_bundle_redacts_api_env_values_but_keeps_key_names(
    tmp_hal0_home: str, tmp_path: Path
) -> None:
    from hal0.config import paths as cfg_paths

    api_env = cfg_paths.etc() / "api.env"
    api_env.parent.mkdir(parents=True, exist_ok=True)
    api_env.write_text("HAL0_ADMIN_TOKEN=abc123\nHAL0_BIND_ADDR=0.0.0.0\n")

    out = tmp_path / "bundle"
    build_bundle(out, include_rocm_smi=False)

    dumped = (out / "config" / "api.env").read_text()
    assert "abc123" not in dumped
    assert "HAL0_ADMIN_TOKEN=" in dumped
    assert "HAL0_BIND_ADDR=0.0.0.0" in dumped


def test_bundle_diagnostics_section_writes_expected_files(
    tmp_hal0_home: str, tmp_path: Path
) -> None:
    out = tmp_path / "bundle"
    build_bundle(out, include_rocm_smi=False)
    diag_dir = out / "diagnostics"
    for name in ("perms.json", "migrations.json", "profiles.json", "verify.json", "models.json"):
        assert (diag_dir / name).is_file(), name
    perms = jsonlib.loads((diag_dir / "perms.json").read_text())
    assert isinstance(perms, list) and perms  # never empty — HAL0-DOCTOR-OK at minimum
    models = jsonlib.loads((diag_dir / "models.json").read_text())
    assert models == {"_unavailable": "/api/models"}  # API is down in this test


def test_bundle_profile_repair_is_device_aware(tmp_hal0_home: str, tmp_path: Path) -> None:
    """The bundle's profiles.json must recommend the same repair doctor does.

    #1830: a profile-less capability slot is drift, and the repair is
    device-keyed — an ``npu`` embedding slot runs the FLM runtime, so telling
    the operator to write llama-server's ``embedding`` profile would move it
    onto the wrong runtime family. The bundle path built its rows without the
    slot devices while the interactive path passed them.
    """
    slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "npu-embed.toml").write_text(
        'name = "npu-embed"\ntype = "embedding"\ndevice = "npu"\nport = 8099\n'
    )

    out = tmp_path / "bundle"
    build_bundle(out, include_rocm_smi=False)

    profiles = jsonlib.dumps(jsonlib.loads((out / "diagnostics" / "profiles.json").read_text()))
    assert "hal0 slot edit npu-embed --profile flm" in profiles


def test_bundle_no_logs_flag_skips_logs_dir(tmp_hal0_home: str, tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    build_bundle(out, include_rocm_smi=False, include_logs=False)
    assert not (out / "logs").exists()
    manifest = jsonlib.loads((out / "manifest.json").read_text())
    assert "logs/" not in manifest["sections"]


def test_bundle_returns_nonzero_failed_count_when_probes_missing(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_rocminfo_missing(monkeypatch)
    out = tmp_path / "bundle"
    _, failed = build_bundle(out, include_rocm_smi=True)
    # The forced rocminfo failure guarantees at least one failed probe.
    assert failed > 0
