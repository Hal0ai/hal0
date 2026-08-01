"""Issue #1514: `openwebui.env` is not clobbered on re-run.

`installer/README.md:49` promises *"Existing files are **never clobbered**
on re-run"* for `hal0.toml`, `api.env`, `upstreams.toml` **and**
`openwebui.env`. The first three keep that promise — `hal0.toml` and
`upstreams.toml` are `[[ ! -f ]]`-guarded, and `api.env` rewrites only a
marker-delimited network block. `openwebui.env` was regenerated from
defaults on every run, so any operator edit — a custom
`WEBUI_AUTH_TRUSTED_EMAIL_HEADER`, a changed `AUDIO_TTS_ENGINE`, a
repointed `OPENAI_API_BASE_URLS` — was gone on the next repair or upgrade.

That is also what made #1515's documented mitigation self-defeating: the
one remaining way to set the trusted-header posture was to hand-edit this
file, and the installer erased it.

**Merge, not a `[[ ! -f ]]` guard.** Skipping the write entirely would
protect edits but freeze the file at whatever the box was installed with —
a release that adds a new default (the voice/Call-mode keys were exactly
that) would never reach an existing install, and the operator would get a
half-configured OpenWebUI with no signal. Merging keeps every existing
value and adds only genuinely new keys, which satisfies the README and the
upgrade path at once.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.openwebui.env_writer import default_openwebui_env, write_openwebui_env


def _parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"')
    return out


# ── the promise ─────────────────────────────────────────────────────────────


def test_preserve_keeps_an_operator_edit(tmp_path: Path) -> None:
    """The #1514 repro: this value used to be gone after the second call."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target)
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "AUDIO_TTS_ENGINE=openai", "AUDIO_TTS_ENGINE=elevenlabs"
        ),
        encoding="utf-8",
    )

    write_openwebui_env(target, preserve_existing=True)
    assert _parse(target)["AUDIO_TTS_ENGINE"] == "elevenlabs"


def test_preserve_keeps_a_key_hal0_does_not_ship(tmp_path: Path) -> None:
    """An operator's own addition — e.g. the trusted-header pair #1515
    tells them to set — must survive, not just edits to known keys."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target)
    with target.open("a", encoding="utf-8") as fh:
        fh.write('WEBUI_AUTH_TRUSTED_EMAIL_HEADER="X-Forwarded-Email"\n')

    write_openwebui_env(target, preserve_existing=True)
    assert _parse(target)["WEBUI_AUTH_TRUSTED_EMAIL_HEADER"] == "X-Forwarded-Email"


def test_preserve_still_adds_newly_shipped_defaults(tmp_path: Path) -> None:
    """The reason this merges instead of skipping the write: a box installed
    before a key existed must still receive it on upgrade."""
    target = tmp_path / "openwebui.env"
    partial = {k: v for k, v in default_openwebui_env().items() if k != "AUDIO_TTS_VOICE"}
    from hal0.config.env import write_env_atomic

    write_env_atomic(target, partial)

    write_openwebui_env(target, preserve_existing=True)
    assert _parse(target)["AUDIO_TTS_VOICE"] == default_openwebui_env()["AUDIO_TTS_VOICE"]


def test_preserve_on_a_missing_file_writes_the_full_defaults(tmp_path: Path) -> None:
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, preserve_existing=True)
    assert _parse(target) == {k: v for k, v in default_openwebui_env().items()}


def test_preserve_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, preserve_existing=True)
    first = target.read_text(encoding="utf-8")
    write_openwebui_env(target, preserve_existing=True)
    assert target.read_text(encoding="utf-8") == first


# ── the default is still a full render ──────────────────────────────────────


def test_default_call_still_replaces(tmp_path: Path) -> None:
    """`preserve_existing` is opt-in: the plain call keeps its old
    full-render semantics so no existing caller changes behaviour."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target)
    with target.open("a", encoding="utf-8") as fh:
        fh.write("CUSTOM_KEY=keep-me\n")

    write_openwebui_env(target)
    assert "CUSTOM_KEY" not in _parse(target)


def test_overrides_still_win_over_a_preserved_value(tmp_path: Path) -> None:
    """An explicit override is the caller stating intent; a preserved value
    is an absent one. Intent wins."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, overrides={"WEBUI_NAME": "old"})
    write_openwebui_env(target, overrides={"WEBUI_NAME": "new"}, preserve_existing=True)
    assert _parse(target)["WEBUI_NAME"] == "new"


# ── the installer uses it ───────────────────────────────────────────────────


def test_main_preserves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`main()` is what `install.sh` invokes — the guarantee has to hold on
    that path, not merely be available on the API."""
    from hal0.openwebui import env_writer

    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    target = tmp_path / "etc" / "hal0" / "openwebui.env"
    env_writer.main()
    with target.open("a", encoding="utf-8") as fh:
        fh.write("WEBUI_NAME=my-box\n")

    env_writer.main()
    assert _parse(target)["WEBUI_NAME"] == "my-box"


# ── the file no longer claims it will be overwritten ────────────────────────


def test_header_does_not_tell_operators_their_edits_will_be_lost(tmp_path: Path) -> None:
    """`write_env_atomic`'s generic header says "Do not edit manually;
    changes will be overwritten on next slot load" — written verbatim into
    openwebui.env, which is neither a slot nor (now) overwritten."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, preserve_existing=True)
    text = target.read_text(encoding="utf-8")
    assert "overwritten on next slot load" not in text
    assert "openwebui" in text.lower()


def test_slot_env_header_is_unchanged(tmp_path: Path) -> None:
    """Negative control: slot env files ARE regenerated every load, so their
    header must keep saying so."""
    from hal0.config.env import write_env_atomic

    target = tmp_path / "slot.env"
    write_env_atomic(target, {"HAL0_PORT": "8081"})
    assert "overwritten on next slot load" in target.read_text(encoding="utf-8")
