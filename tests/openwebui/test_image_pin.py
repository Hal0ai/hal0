"""Unit tests for the pure OpenWebUI image-pin helpers.

These never touch the network, a subprocess, or a privileged path — they only
exercise the text seam that ``hal0 update owui`` and the pin-consistency check
build on.
"""

from __future__ import annotations

import pytest

from hal0.openwebui import image_pin

_D1 = "sha256:" + "a" * 64
_D2 = "sha256:" + "b" * 64

# A trimmed stand-in for the two pin sites in hal0-openwebui.service.
_UNIT = f"""\
[Service]
ExecStartPre=-/usr/bin/podman pull ghcr.io/open-webui/open-webui@{_D1}
ExecStart=/usr/bin/podman run --rm ghcr.io/open-webui/open-webui@{_D1}
"""


def test_find_owui_digests_returns_every_occurrence() -> None:
    assert image_pin.find_owui_digests(_UNIT) == [_D1, _D1]


def test_parse_pinned_digest_when_all_agree() -> None:
    assert image_pin.parse_pinned_digest(_UNIT) == _D1


def test_parse_pinned_digest_none_when_no_pin() -> None:
    assert image_pin.parse_pinned_digest("no image here") is None


def test_parse_pinned_digest_none_when_pins_disagree() -> None:
    mixed = _UNIT.replace(
        f"run --rm ghcr.io/open-webui/open-webui@{_D1}",
        f"run --rm ghcr.io/open-webui/open-webui@{_D2}",
    )
    assert image_pin.parse_pinned_digest(mixed) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (_D1, _D1),
        ("A" * 64, "sha256:" + "a" * 64),  # bare hex, upper-cased
        ("sha256:" + "F" * 64, "sha256:" + "f" * 64),
        ("  " + _D2 + "  ", _D2),  # surrounding whitespace tolerated
    ],
)
def test_normalize_digest_accepts_valid_forms(value: str, expected: str) -> None:
    assert image_pin.normalize_digest(value) == expected


@pytest.mark.parametrize(
    "value", ["", "sha256:zzz", "sha512:" + "a" * 64, "a" * 63, "not-a-digest"]
)
def test_normalize_digest_rejects_garbage(value: str) -> None:
    assert image_pin.normalize_digest(value) is None


def test_is_sha256_digest() -> None:
    assert image_pin.is_sha256_digest(_D1)
    assert not image_pin.is_sha256_digest("a" * 64)  # unprefixed is not canonical
    assert not image_pin.is_sha256_digest("sha256:xyz")


def test_pinned_ref() -> None:
    assert image_pin.pinned_ref(_D1) == f"ghcr.io/open-webui/open-webui@{_D1}"


def test_repin_unit_text_rewrites_all_occurrences() -> None:
    new_text, count = image_pin.repin_unit_text(_UNIT, _D2)
    assert count == 2
    assert _D1 not in new_text
    assert new_text.count(_D2) == 2
    # Round-trips cleanly through the parser.
    assert image_pin.parse_pinned_digest(new_text) == _D2


def test_repin_unit_text_rejects_bad_digest() -> None:
    with pytest.raises(ValueError, match="not a sha256 digest"):
        image_pin.repin_unit_text(_UNIT, "a" * 64)


def test_installed_unit_path_honours_hal0_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HOME", "/tmp/hal0home")
    p = image_pin.installed_unit_path()
    assert p.as_posix() == "/tmp/hal0home/etc/systemd/system/hal0-openwebui.service"

    monkeypatch.delenv("HAL0_HOME", raising=False)
    assert image_pin.installed_unit_path().as_posix() == (
        "/etc/systemd/system/hal0-openwebui.service"
    )
