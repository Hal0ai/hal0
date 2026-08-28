"""Fresh installs persist the bootstrap channel for the updater — #2083.

``installer/bootstrap.sh`` admits and cosign-verifies the release against
``HAL0_CHANNEL``, but nothing used to hand that channel to the installed
system — the updater then starts life on its ``stable`` default, whose
manifest pointer is 404 until GA (#1530). Every preview/nightly fresh
install therefore failed the #2066 install-time update-check probe with a
false "the update path is broken" alarm, and an rc box could never see the
next rc via ``hal0 update`` without a manual channel flip.

The fix mirrors the ``HAL0_BOOTSTRAP_COSIGN`` pattern from #2058:

* ``bootstrap.sh`` exports ``HAL0_CHANNEL`` so the admitted channel
  survives the ``exec`` into install.sh.
* ``persist_bootstrap_channel`` (installer/lib/preflight.sh, called from
  install.sh's Configuration step right after hal0.toml exists and before
  the hal0-api restart) writes ``telemetry.channel`` into hal0.toml — the
  exact key ``PUT /api/updates/channel`` persists and the updater reads.
  It never overwrites an explicit existing value (it warns instead), and
  it validates the channel name before touching the file.

Technique: real-bash subprocess against the shell function (the
``test_install_cosign_persist.py`` pattern), stubbing ONLY the reporters
ui.sh actually defines (info/warn/err/die — no ``ok()``, #2081).
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREFLIGHT = _REPO_ROOT / "installer" / "lib" / "preflight.sh"
_BOOTSTRAP = _REPO_ROOT / "installer" / "bootstrap.sh"
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"

_STUBS = """
info() { printf 'INFO:%s\\n' "$*"; }
warn() { printf 'WARN:%s\\n' "$*" >&2; }
err()  { printf 'ERR:%s\\n' "$*" >&2; }
die()  { err "$*"; exit 1; }
"""

_FRESH_TOML = """\
[meta]
schema_version = 1

[slots]
port_range_start = 8081
port_range_end = 8099

[telemetry]
enabled = false
"""


def _run_persist(
    toml_path: Path,
    *,
    channel: str | None,
) -> subprocess.CompletedProcess[str]:
    """Drive persist_bootstrap_channel with a controlled HAL0_CHANNEL."""
    env = dict(os.environ)
    env.pop("HAL0_CHANNEL", None)
    if channel is not None:
        env["HAL0_CHANNEL"] = channel
    script = _STUBS + f'source "{_PREFLIGHT}"\n' + f'persist_bootstrap_channel "{toml_path}"\n'
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _telemetry_channel(toml_path: Path) -> str | None:
    with toml_path.open("rb") as fh:
        data = tomllib.load(fh)
    return data.get("telemetry", {}).get("channel")


class TestPersistBootstrapChannel:
    def test_writes_channel_into_existing_telemetry_section(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text(_FRESH_TOML, encoding="utf-8")
        proc = _run_persist(toml, channel="preview")
        assert proc.returncode == 0, proc.stderr
        assert _telemetry_channel(toml) == "preview", toml.read_text()
        # The rest of the config must survive the edit intact.
        with toml.open("rb") as fh:
            data = tomllib.load(fh)
        assert data["telemetry"]["enabled"] is False
        assert data["slots"]["port_range_start"] == 8081
        assert "INFO:" in proc.stdout

    def test_appends_telemetry_section_when_absent(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text("[meta]\nschema_version = 1\n", encoding="utf-8")
        proc = _run_persist(toml, channel="nightly")
        assert proc.returncode == 0, proc.stderr
        assert _telemetry_channel(toml) == "nightly", toml.read_text()

    def test_noop_when_channel_unset(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text(_FRESH_TOML, encoding="utf-8")
        proc = _run_persist(toml, channel=None)
        assert proc.returncode == 0, proc.stderr
        assert toml.read_text(encoding="utf-8") == _FRESH_TOML
        assert "WARN:" not in proc.stderr

    def test_noop_when_value_already_matches(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text(_FRESH_TOML + 'channel = "preview"\n', encoding="utf-8")
        before = toml.read_text(encoding="utf-8")
        proc = _run_persist(toml, channel="preview")
        assert proc.returncode == 0, proc.stderr
        assert toml.read_text(encoding="utf-8") == before
        assert "WARN:" not in proc.stderr

    def test_never_overwrites_a_differing_explicit_channel(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text(_FRESH_TOML + 'channel = "nightly"\n', encoding="utf-8")
        proc = _run_persist(toml, channel="preview")
        assert proc.returncode == 0, proc.stderr
        assert _telemetry_channel(toml) == "nightly", toml.read_text()
        # Must say so honestly, and point at the supported way to switch.
        assert "WARN:" in proc.stderr
        assert "hal0 update --channel" in proc.stderr

    def test_rejects_invalid_channel_without_writing(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text(_FRESH_TOML, encoding="utf-8")
        proc = _run_persist(toml, channel="jelly")
        assert proc.returncode == 0, proc.stderr
        assert toml.read_text(encoding="utf-8") == _FRESH_TOML
        assert "WARN:" in proc.stderr

    def test_warns_and_survives_when_toml_is_missing(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        proc = _run_persist(toml, channel="preview")
        assert proc.returncode == 0, proc.stderr
        assert not toml.exists()
        assert "WARN:" in proc.stderr


class TestValidTomlVariants:
    """Review findings on PR #2087: regex blind spots must degrade to
    no-op/warn, never to a duplicate-key config the daemon cannot parse."""

    def test_inline_comment_on_existing_channel_is_seen(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text(_FRESH_TOML + 'channel = "preview"  # set by ops\n', encoding="utf-8")
        before = toml.read_text(encoding="utf-8")
        proc = _run_persist(toml, channel="preview")
        assert proc.returncode == 0, proc.stderr
        # Same value: no duplicate key appended, no warn.
        assert toml.read_text(encoding="utf-8") == before
        assert "WARN:" not in proc.stderr
        _telemetry_channel(toml)  # must still parse

    def test_inline_comment_differing_channel_warns_not_duplicates(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text(_FRESH_TOML + 'channel = "nightly"  # set by ops\n', encoding="utf-8")
        proc = _run_persist(toml, channel="preview")
        assert proc.returncode == 0, proc.stderr
        assert _telemetry_channel(toml) == "nightly"
        assert "WARN:" in proc.stderr

    def test_single_quoted_existing_value_is_seen_as_equal(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text(_FRESH_TOML + "channel = 'preview'\n", encoding="utf-8")
        before = toml.read_text(encoding="utf-8")
        proc = _run_persist(toml, channel="preview")
        assert proc.returncode == 0, proc.stderr
        assert toml.read_text(encoding="utf-8") == before
        assert "WARN:" not in proc.stderr

    def test_section_followed_by_list_valued_table_is_not_spliced(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text(
            "[telemetry]\nenabled = false\n\n[models]\nroots = [\"/x\", \"/y\"]\n",
            encoding="utf-8",
        )
        proc = _run_persist(toml, channel="preview")
        assert proc.returncode == 0, proc.stderr
        with toml.open("rb") as fh:
            data = tomllib.load(fh)
        assert data["telemetry"]["channel"] == "preview"
        assert data["models"]["roots"] == ["/x", "/y"]

    def test_commented_section_header_never_corrupts(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text("[telemetry]  # hal0-owned\nenabled = false\n", encoding="utf-8")
        proc = _run_persist(toml, channel="preview")
        assert proc.returncode == 0, proc.stderr
        # Whatever happened, the file must still parse...
        data = tomllib.loads(toml.read_text(encoding="utf-8"))
        # ...and with the comment-tolerant header match, the key lands.
        assert data["telemetry"]["channel"] == "preview"

    def test_unparsable_existing_config_is_left_alone(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text("[telemetry\nenabled = ???\n", encoding="utf-8")
        before = toml.read_text(encoding="utf-8")
        proc = _run_persist(toml, channel="preview")
        assert proc.returncode == 0, proc.stderr
        assert toml.read_text(encoding="utf-8") == before
        assert "WARN:" in proc.stderr


class TestStableDefault:
    """A silently-persisted default must not poison the never-overwrite rule
    (review on PR #2087): default bootstrap -> no key; a later deliberate
    HAL0_CHANNEL=preview re-bootstrap must persist cleanly."""

    def test_stable_with_no_existing_key_is_not_persisted(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text(_FRESH_TOML, encoding="utf-8")
        proc = _run_persist(toml, channel="stable")
        assert proc.returncode == 0, proc.stderr
        assert toml.read_text(encoding="utf-8") == _FRESH_TOML
        assert "WARN:" not in proc.stderr

    def test_default_install_then_preview_rebootstrap_persists(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text(_FRESH_TOML, encoding="utf-8")
        assert _run_persist(toml, channel="stable").returncode == 0
        proc = _run_persist(toml, channel="preview")
        assert proc.returncode == 0, proc.stderr
        assert _telemetry_channel(toml) == "preview"
        assert "WARN:" not in proc.stderr

    def test_explicit_stable_on_disk_still_respected_over_preview(self, tmp_path: Path) -> None:
        toml = tmp_path / "hal0.toml"
        toml.write_text(_FRESH_TOML + 'channel = "stable"\n', encoding="utf-8")
        proc = _run_persist(toml, channel="preview")
        assert proc.returncode == 0, proc.stderr
        assert _telemetry_channel(toml) == "stable"
        assert "WARN:" in proc.stderr



class TestWiring:
    def test_bootstrap_exports_the_admitted_channel(self) -> None:
        code = "\n".join(
            line
            for line in _BOOTSTRAP.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "export HAL0_CHANNEL" in code

    def test_install_sh_persists_after_config_write_before_smoke(self) -> None:
        text = _INSTALL_SH.read_text(encoding="utf-8")
        call = text.index("persist_bootstrap_channel")
        assert call > text.index('ui_step "Configuration"')
        assert call < text.index("smoke_update_check")
