"""The bind host survives systemd's reading of ``openwebui.env``.

``hal0-openwebui.service`` reads ``openwebui.env`` twice: podman gets the whole
file through ``--env-file`` (values verbatim), and systemd gets it through
``EnvironmentFile=`` to expand ``${HAL0_OWUI_BIND_HOST}`` into ``podman run -p``.
The unit defaults that variable to ``0.0.0.0``, so a bind-host line systemd does
not see means an unauthenticated Open WebUI published on the LAN on a box set to
``127.0.0.1``.

systemd's parser is not line oriented. A value that starts with ``'`` or ``"``
stays open across newlines until its closing partner, and a trailing ``\\``
continues the value onto the next line. With values written verbatim, an
operator value that sorts before ``HAL0_OWUI_BIND_HOST`` and starts with an
unclosed quote, or ends in an odd run of backslashes, would swallow the
bind-host line. The writer therefore
places that line first, ahead of every operator value, and these tests parse
the rendered file with a port of systemd's state machine to prove it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config import env as env_primitive
from hal0.openwebui.env_writer import BIND_HOST_KEY, write_openwebui_env
from tests.openwebui._podman_env import parse_podman_env

# ── systemd's EnvironmentFile= parser ────────────────────────────────────────

_WHITESPACE = " \t\n\r"
_NEWLINE = "\n\r"
_COMMENTS = "#;"
_SHELL_NEED_ESCAPE = '"\\`$'


def parse_systemd_env(text: str) -> dict[str, str]:
    """Port of ``parse_env_file_internal`` in systemd v255 ``src/basic/env-file.c``
    (the state machine at ``:56-255``) plus the later-assignment-wins merge that
    ``EnvironmentFile=`` applies. States, transitions and the whitespace
    chomping follow the C one for one; names are the C enumerators."""
    out: dict[str, str] = {}
    key: list[str] = []
    value: list[str] = []
    last_key_ws: int | None = None
    last_value_ws: int | None = None
    state = "PRE_KEY"

    def push(*, chomp_value: bool) -> None:
        name = "".join(key[:last_key_ws] if last_key_ws is not None else key)
        val = value[:last_value_ws] if chomp_value and last_value_ws is not None else value
        out[name] = "".join(val)
        key.clear()
        value.clear()

    for c in text:
        if state == "PRE_KEY":
            if c in _COMMENTS:
                state = "COMMENT"
            elif c not in _WHITESPACE:
                state = "KEY"
                last_key_ws = None
                key.append(c)
        elif state == "KEY":
            if c in _NEWLINE:
                state = "PRE_KEY"
                key.clear()
            elif c == "=":
                state = "PRE_VALUE"
                last_value_ws = None
            else:
                if c not in _WHITESPACE:
                    last_key_ws = None
                elif last_key_ws is None:
                    last_key_ws = len(key)
                key.append(c)
        elif state == "PRE_VALUE":
            if c in _NEWLINE:
                state = "PRE_KEY"
                push(chomp_value=False)
            elif c == "'":
                state = "SINGLE_QUOTE_VALUE"
            elif c == '"':
                state = "DOUBLE_QUOTE_VALUE"
            elif c == "\\":
                state = "VALUE_ESCAPE"
            elif c not in _WHITESPACE:
                state = "VALUE"
                value.append(c)
        elif state == "VALUE":
            if c in _NEWLINE:
                state = "PRE_KEY"
                push(chomp_value=True)
            elif c == "\\":
                state = "VALUE_ESCAPE"
                last_value_ws = None
            else:
                if c not in _WHITESPACE:
                    last_value_ws = None
                elif last_value_ws is None:
                    last_value_ws = len(value)
                value.append(c)
        elif state == "VALUE_ESCAPE":
            state = "VALUE"
            if c not in _NEWLINE:  # an escaped newline is eaten whole
                value.append(c)
        elif state == "SINGLE_QUOTE_VALUE":
            if c == "'":
                state = "PRE_VALUE"
            else:
                value.append(c)
        elif state == "DOUBLE_QUOTE_VALUE":
            if c == '"':
                state = "PRE_VALUE"
            elif c == "\\":
                state = "DOUBLE_QUOTE_VALUE_ESCAPE"
            else:
                value.append(c)
        elif state == "DOUBLE_QUOTE_VALUE_ESCAPE":
            state = "DOUBLE_QUOTE_VALUE"
            if c in _SHELL_NEED_ESCAPE:
                value.append(c)
            elif c != "\n":
                value.extend(("\\", c))
        elif state == "COMMENT":
            if c == "\\":
                state = "COMMENT_ESCAPE"
            elif c in _NEWLINE:
                state = "PRE_KEY"
        elif state == "COMMENT_ESCAPE":
            state = "PRE_KEY" if c in _NEWLINE else "COMMENT"

    if state in {
        "PRE_VALUE",
        "VALUE",
        "VALUE_ESCAPE",
        "SINGLE_QUOTE_VALUE",
        "DOUBLE_QUOTE_VALUE",
        "DOUBLE_QUOTE_VALUE_ESCAPE",
    }:
        push(chomp_value=state == "VALUE")
    return out


def test_the_systemd_parser_port_reads_the_documented_shapes() -> None:
    """Pin the port itself against the rules it is meant to model."""
    text = (
        "# comment\n"
        "; other comment\n"
        "PLAIN=abc\n"
        "SPACED = a b  \n"
        'DOUBLE="x y"\n'
        "SINGLE='x y'\n"
        'ESC="a\\"b\\\\c\\nd"\n'
        "JOIN=ab\\\ncd\n"
        'MID=it\'s "q"\n'
        "EMPTY=\n"
        "LATER=1\n"
        "LATER=2\n"
    )
    assert parse_systemd_env(text) == {
        "PLAIN": "abc",
        "SPACED": "a b",
        "DOUBLE": "x y",
        "SINGLE": "x y",
        "ESC": 'a"b\\c\\nd',
        "JOIN": "abcd",
        "MID": 'it\'s "q"',  # a quote inside a value is literal
        "EMPTY": "",
        "LATER": "2",
    }


@pytest.mark.parametrize(
    "swallowing_line",
    [
        'AAA="unclosed',
        "AAA='unclosed",
        'AAA="',
        "AAA='",
        'AAA= "unclosed',
        'AAA="closed"\\',
        "AAA=trailing\\",
        "AAA=odd\\\\\\",
    ],
)
def test_the_port_shows_a_swallowed_line(swallowing_line: str) -> None:
    """Negative control: with the bind host AFTER such a line, systemd never
    sees it. This is the failure the writer's ordering exists to prevent."""
    text = f"{swallowing_line}\nHAL0_OWUI_BIND_HOST=127.0.0.1\nZZZ=1\n"
    assert parse_systemd_env(text).get("HAL0_OWUI_BIND_HOST") != "127.0.0.1"


# ── the rendered file ────────────────────────────────────────────────────────

#: Operator values that, written verbatim, could swallow the lines after them:
#: an unclosed quote at the start of the value (after any blanks), a quote
#: closed and then followed by a backslash, an odd run of trailing backslashes.
#: The two with a quote inside the value are harmless to systemd and pin that.
_ADVERSARIAL_VALUES = [
    '"unclosed',
    "'unclosed",
    '"',
    "'",
    ' "unclosed',
    "\t'unclosed",
    '"closed"\\',
    "it's",
    'say "hi',
    "trailing\\",
    "odd\\\\\\",
    "\\",
    "x # y\\",
]

#: Keys that sort before ``H``, so a sorted file would put them ahead of the
#: bind host. The first four are the prefixes the preflight named.
_EARLY_KEYS = ["AUDIO_TTS_VOICE", "COMFYUI_BASE_URL", "DATA_DIR", "ENABLE_THING", "AAA", "GZ"]


@pytest.fixture(autouse=True)
def _loopback_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    monkeypatch.setenv("HAL0_BIND_HOST", "127.0.0.1")


def _assert_bind_host_is_loopback(text: str) -> None:
    assert parse_podman_env(text)[BIND_HOST_KEY] == "127.0.0.1"
    assert parse_systemd_env(text).get(BIND_HOST_KEY) == "127.0.0.1", text


@pytest.mark.parametrize("value", _ADVERSARIAL_VALUES)
@pytest.mark.parametrize("key", _EARLY_KEYS)
def test_an_override_that_sorts_early_cannot_hide_the_bind_host(
    tmp_path: Path, key: str, value: str
) -> None:
    target = write_openwebui_env(tmp_path / "openwebui.env", overrides={key: value})
    text = target.read_text(encoding="utf-8")
    assert parse_podman_env(text)[key] == value  # podman still gets it verbatim
    _assert_bind_host_is_loopback(text)


@pytest.mark.parametrize("value", _ADVERSARIAL_VALUES)
@pytest.mark.parametrize("key", _EARLY_KEYS)
def test_a_preserved_operator_line_that_sorts_early_cannot_hide_the_bind_host(
    tmp_path: Path, key: str, value: str
) -> None:
    target = tmp_path / "openwebui.env"
    target.write_text(f"{key}={value}\nWEBUI_NAME=box\n", encoding="utf-8")
    write_openwebui_env(target, preserve_existing=True)
    text = target.read_text(encoding="utf-8")
    assert parse_podman_env(text)[key] == value
    _assert_bind_host_is_loopback(text)
    # Re-rendering the already-rendered file must not move the line either.
    write_openwebui_env(target, preserve_existing=True)
    _assert_bind_host_is_loopback(target.read_text(encoding="utf-8"))


def test_the_bind_host_is_the_first_value_line_of_a_rendered_file(tmp_path: Path) -> None:
    target = write_openwebui_env(tmp_path / "openwebui.env", overrides={"AAA": "1"})
    values = [
        line
        for line in target.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert values[0] == "HAL0_OWUI_BIND_HOST=127.0.0.1"
    assert "AAA=1" in values[1:]


def test_every_hal0_written_comment_line_leaves_systemd_between_entries(tmp_path: Path) -> None:
    """The header (managed-keys line, fingerprint line) sits ahead of the bind
    host, so none of its lines may carry systemd out of its comment state."""
    (tmp_path / "api.env").write_text("HAL0_CLIENT_KEY=client-key-0123456789abcdef\n")
    target = write_openwebui_env(
        tmp_path / "openwebui.env",
        overrides={"RAG_EMBEDDING_MODEL": "m", "RAG_EMBEDDING_ENGINE": "openai"},
    )
    text = target.read_text(encoding="utf-8")
    assert "# hal0-managed:" in text
    assert "# hal0-client-key-fingerprint:" in text
    _assert_bind_host_is_loopback(text)


# ── a bind host that is not a plain host never reaches the file ──────────────


@pytest.mark.parametrize("bad", ['0.0.0.0"', "127.0.0.1\\", "'x", "a b", "127.0.0.1 # lan", ""])
def test_an_operator_bind_host_that_is_not_plain_is_refused(tmp_path: Path, bad: str) -> None:
    target = write_openwebui_env(tmp_path / "openwebui.env")
    before = target.read_bytes()
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "HAL0_OWUI_BIND_HOST=127.0.0.1", f"HAL0_OWUI_BIND_HOST={bad}"
        ),
        encoding="utf-8",
    )
    mangled = target.read_bytes()
    assert mangled != before
    with pytest.raises(ValueError, match="HAL0_OWUI_BIND_HOST"):
        write_openwebui_env(target, preserve_existing=True)
    assert target.read_bytes() == mangled  # nothing was rewritten


def test_a_bad_bind_host_from_the_environment_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_BIND_HOST", '127.0.0.1"; evil')
    with pytest.raises(ValueError, match="HAL0_OWUI_BIND_HOST"):
        write_openwebui_env(tmp_path / "openwebui.env")
    assert list(tmp_path.iterdir()) == []


def test_a_bad_bind_host_override_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HAL0_OWUI_BIND_HOST"):
        write_openwebui_env(tmp_path / "openwebui.env", overrides={BIND_HOST_KEY: "'0.0.0.0"})
    assert list(tmp_path.iterdir()) == []


def test_the_writer_and_the_primitive_name_the_same_bind_host_key() -> None:
    """env_writer cannot import the primitive's package on its cold path, so it
    carries its own copy of the name; the primitive orders and validates by it."""
    assert env_primitive.BIND_HOST_KEY == BIND_HOST_KEY == "HAL0_OWUI_BIND_HOST"


# ── a CR hands systemd a second, later bind-host assignment ──────────────────

#: An operator file whose second line podman reads as one variable, named
#: ``X\rHAL0_OWUI_BIND_HOST``, but systemd reads as ``X`` (dropped) followed by a
#: second ``HAL0_OWUI_BIND_HOST=0.0.0.0``. systemd keeps the later assignment.
_SPLIT_BY_CR = [
    pytest.param(
        b"HAL0_OWUI_BIND_HOST=127.0.0.1\nX\rHAL0_OWUI_BIND_HOST=0.0.0.0\n",
        id="cr-in-key",
    ),
    pytest.param(
        b"HAL0_OWUI_BIND_HOST=127.0.0.1\nX=1\rHAL0_OWUI_BIND_HOST=0.0.0.0\n",
        id="cr-in-value",
    ),
]


@pytest.mark.parametrize("raw", _SPLIT_BY_CR)
def test_the_cr_hazard_is_real_systemd_keeps_the_later_assignment(raw: bytes) -> None:
    """Control: without a guard, the file reads loopback to podman and to a
    reader of the first line, but systemd publishes on every interface."""
    text = raw.decode("utf-8")
    assert parse_podman_env(text)[BIND_HOST_KEY] == "127.0.0.1"
    assert parse_systemd_env(text)[BIND_HOST_KEY] == "0.0.0.0"


@pytest.mark.parametrize("raw", _SPLIT_BY_CR)
def test_a_preserved_line_that_systemd_would_split_is_refused(tmp_path: Path, raw: bytes) -> None:
    target = tmp_path / "openwebui.env"
    target.write_bytes(raw)
    with pytest.raises(ValueError, match=r"line break|cannot be empty or contain"):
        write_openwebui_env(target, preserve_existing=True)
    assert target.read_bytes() == raw  # untouched
    assert list(tmp_path.iterdir()) == [target]  # no tmpfile left behind
