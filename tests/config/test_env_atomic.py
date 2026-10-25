"""``write_env_atomic`` writes values byte for byte, in podman's format.

``openwebui.env`` reaches Open WebUI through ``podman run --env-file``, which
keeps quotes and backslashes literally (#3045), so the writer must not quote.
It also refuses what a one-line-per-variable file cannot hold, and pins the one
value systemd reads from the file (``HAL0_OWUI_BIND_HOST``) to the first line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config.env import BIND_HOST_KEY, write_env_atomic


def test_values_are_written_verbatim(tmp_path: Path) -> None:
    target = tmp_path / "x.env"
    write_env_atomic(target, {"A": 'x "y" $z #w \\q', "B": "", "C": "a b"}, header=("# h",))
    assert target.read_text(encoding="utf-8").splitlines() == [
        "# h",
        "",
        'A=x "y" $z #w \\q',
        "B=",
        "C=a b",
    ]


@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\0b"])
def test_a_line_break_or_nul_is_refused_and_nothing_is_written(tmp_path: Path, bad: str) -> None:
    target = tmp_path / "x.env"
    target.write_text("KEEP=1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="'A' cannot contain a line break or NUL"):
        write_env_atomic(target, {"A": bad})
    assert target.read_text(encoding="utf-8") == "KEEP=1\n"
    assert list(tmp_path.iterdir()) == [target]  # no orphaned tmpfile


def test_a_non_string_value_is_still_a_type_error(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="'A' must be str"):
        write_env_atomic(tmp_path / "x.env", {"A": 1})  # type: ignore[dict-item]


def test_keys_are_written_in_sorted_order(tmp_path: Path) -> None:
    target = tmp_path / "x.env"
    write_env_atomic(target, {"B": "2", "A": "1", "C": "3"}, header=())
    assert target.read_text(encoding="utf-8").splitlines() == ["", "A=1", "B=2", "C=3"]


# ── the bind host: first value line, plain host only ─────────────────────────


def test_the_bind_host_is_the_first_value_line(tmp_path: Path) -> None:
    target = tmp_path / "x.env"
    write_env_atomic(
        target,
        {"AAA": "1", BIND_HOST_KEY: "127.0.0.1", "ZZZ": "2"},
        header=("# h",),
    )
    assert target.read_text(encoding="utf-8").splitlines() == [
        "# h",
        "",
        "HAL0_OWUI_BIND_HOST=127.0.0.1",
        "AAA=1",
        "ZZZ=2",
    ]


def test_a_file_without_a_bind_host_is_written_as_before(tmp_path: Path) -> None:
    """No bind host in the dict means nothing to place first and nothing to
    validate; hal0 invents no default (the unit carries its own)."""
    target = tmp_path / "x.env"
    write_env_atomic(target, {"B": "2", "A": "1"}, header=())
    text = target.read_text(encoding="utf-8")
    assert BIND_HOST_KEY not in text
    assert text.splitlines() == ["", "A=1", "B=2"]


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "127.0.0.1",
        "192.168.1.5",
        "::1",
        "fe80::1",
        "localhost",
        "box.example.org",
        "a-b",
    ],
)
def test_a_plain_ip_or_hostname_is_accepted(tmp_path: Path, host: str) -> None:
    target = tmp_path / "x.env"
    write_env_atomic(target, {BIND_HOST_KEY: host}, header=())
    assert f"{BIND_HOST_KEY}={host}" in target.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    "bad",
    [
        "",
        " ",
        "127.0.0.1 ",
        " 127.0.0.1",
        "127.0.0.1 # lan",
        '0.0.0.0"',
        '"0.0.0.0"',
        "'127.0.0.1'",
        "'",
        '"',
        "127.0.0.1\\",
        "a b",
        "host;name",
        "127.0.0.1:3001",
        "[::1]",
        "fe80::1%eth0",
        "fe80::1%'",
        "-leading.example",
        "trailing-.example",
        "double..dot",
        "under_score",
        "$HOME",
        "300.300.300.300",
        "a" * 64 + ".example",
        "a.b." + "c" * 250,
    ],
)
def test_anything_but_a_plain_ip_or_hostname_is_refused_before_writing(
    tmp_path: Path, bad: str
) -> None:
    target = tmp_path / "x.env"
    target.write_text("KEEP=1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="HAL0_OWUI_BIND_HOST"):
        write_env_atomic(target, {BIND_HOST_KEY: bad, "A": "1"})
    assert target.read_text(encoding="utf-8") == "KEEP=1\n"
    assert list(tmp_path.iterdir()) == [target]  # no orphaned tmpfile
