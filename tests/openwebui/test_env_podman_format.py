"""``openwebui.env`` is written the way podman's ``--env-file`` reads it (#3045).

The unit hands the file to ``podman run --env-file``, which keeps quotes and
backslashes literally. hal0 used to quote values in systemd style, so a value
with a space, quote, ``$``, ``#`` or nothing at all reached Open WebUI wrapped
in quote characters. These tests read the rendered file with a re-implemented
podman parser (``tests/openwebui/_podman_env.py``) and compare what the
container would see with what hal0 meant to send.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.openwebui.env_writer import _read_existing_env, write_openwebui_env
from tests.openwebui._podman_env import parse_podman_env as _podman_env


@pytest.fixture(autouse=True)
def _no_ambient_hal0_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    # The root conftest pins HAL0_BIND_HOST=127.0.0.1 for the whole suite.
    monkeypatch.delenv("HAL0_BIND_HOST", raising=False)


def test_every_value_reaches_podman_unchanged(tmp_path: Path) -> None:
    values: dict[str, str | None] = {
        "IMAGE_GENERATION_MODEL": "ckpt/My Model v2",
        "IMAGE_SIZE": "",
        "WEBUI_NAME": 'lab "one" $HOME #1',
    }
    target = write_openwebui_env(tmp_path / "openwebui.env", overrides=values)
    env = _podman_env(target.read_text(encoding="utf-8"))
    for key, value in values.items():
        assert env[key] == value, key
    assert env["OPENAI_API_BASE_URLS"] == "http://host.docker.internal:8080/v1"
    assert env["HAL0_OWUI_BIND_HOST"] == "0.0.0.0"


def test_a_preserved_operator_value_is_kept_byte_for_byte(tmp_path: Path) -> None:
    target = tmp_path / "openwebui.env"
    target.write_text('WEBUI_NAME=My "box" \nCUSTOM_FLAG= spaced\n', encoding="utf-8")
    write_openwebui_env(target, preserve_existing=True)
    env = _podman_env(target.read_text(encoding="utf-8"))
    assert env["WEBUI_NAME"] == 'My "box" '
    assert env["CUSTOM_FLAG"] == " spaced"


def test_a_value_with_a_line_break_leaves_the_file_alone(tmp_path: Path) -> None:
    target = write_openwebui_env(tmp_path / "openwebui.env")
    before = target.read_bytes()
    with pytest.raises(ValueError, match="line break"):
        write_openwebui_env(target, overrides={"WEBUI_NAME": "a\nb"}, preserve_existing=True)
    assert target.read_bytes() == before


def test_the_merge_reads_the_file_the_way_podman_does(tmp_path: Path) -> None:
    """`_read_existing_env` agrees with podman on everything but its own two
    conveniences: a line with no ``=`` is skipped, and blanks around a name are
    trimmed. Quotes stay in the value, as they do for the container."""
    target = tmp_path / "openwebui.env"
    text = (
        "# comment\n"
        "\n"
        "   INDENTED=kept\n"
        'QUOTED="still quoted"\n'
        "SPACED= a b \n"
        "EMPTY=\n"
        "EQUALS=a=b=c\n"
        "CRLF=value\r\n"
        "no-equals-sign\n"
    )
    target.write_text(text, encoding="utf-8")
    assert _read_existing_env(target) == _podman_env(text)


def test_an_older_hal0_quoted_value_is_read_with_its_quotes(tmp_path: Path) -> None:
    """A value an older hal0 quoted is what podman has been handing Open WebUI,
    quotes included; the merge reports it as podman sees it."""
    target = tmp_path / "openwebui.env"
    target.write_text('WEBUI_NAME="my box"\n', encoding="utf-8")
    assert _read_existing_env(target) == {"WEBUI_NAME": '"my box"'}


def test_a_missing_file_reads_as_empty(tmp_path: Path) -> None:
    assert _read_existing_env(tmp_path / "absent.env") == {}


def test_the_podman_parser_helper_matches_podmans_rules() -> None:
    text = "# c\r\n  A=1\r\nB = 2\nC=\nD\nE=x=y\n"
    assert _podman_env(text) == {"A": "1", "B ": " 2", "C": "", "E": "x=y"}
    with pytest.raises(ValueError, match="invalid environment variable"):
        _podman_env("=value\n")


def test_a_lone_carriage_return_inside_a_preserved_value_is_refused(tmp_path: Path) -> None:
    """podman reads ``A=one\\rtwo`` as one variable whose value holds a CR, so the
    merge reads it that way too; the writer then refuses the value rather than
    splitting the line, and leaves the file as it was."""
    target = tmp_path / "openwebui.env"
    target.write_bytes(b"A=one\rtwo\n")
    assert _read_existing_env(target) == {"A": "one\rtwo"}
    with pytest.raises(ValueError, match="'A' cannot contain a line break or NUL"):
        write_openwebui_env(target, preserve_existing=True)
    assert target.read_bytes() == b"A=one\rtwo\n"
