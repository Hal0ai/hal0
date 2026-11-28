"""The text rules for a model folder."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.install.wizard import paths
from hal0.install.wizard.paths import validate_folder


def test_constants_hold_the_installer_values() -> None:
    assert paths.STORE_PATH_MAX == 4096
    assert paths.STORE_MIN_COMPONENTS == 2
    assert paths.STORE_PATH_PUNCTUATION == "_.+@,:~= /-"
    assert paths.STORE_ALLOWED_TREE == "/var/lib/hal0/models"
    assert paths.STORE_REFUSED_TREES == (
        "/etc",
        "/usr",
        "/bin",
        "/sbin",
        "/lib",
        "/lib32",
        "/lib64",
        "/libx32",
        "/boot",
        "/root",
        "/proc",
        "/sys",
        "/dev",
        "/run",
        "/var/run",
        "/var/log",
        "/var/lib",
    )


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("  ", "the folder is required"),
        ("/srv/a\x01b", "the folder has a control character"),
        ("srv/models", "the folder must start with /"),
        ("/srv/100%/models", "the folder must not contain %"),
        (
            "/srv/é/models",
            "the folder may only contain letters, digits, spaces and _ . + @ , : ~ = / -",
        ),
        ("/srv/../models", "the folder must not contain .."),
        ("/" + "a" * 4096, "the folder is too long"),
        ("/data", "the folder must be at least two levels deep, for example /data/models"),
        ("/", "the folder must be at least two levels deep, for example /data/models"),
        ("/etc/models", "the folder cannot be inside /etc"),
        ("/etc", "the folder must be at least two levels deep, for example /data/models"),
        ("/var/lib/hal0/other", "the folder cannot be inside /var/lib"),
        ("/var/lib", "the folder cannot be inside /var/lib"),
    ],
)
def test_each_rule_gives_its_error(text: str, error: str) -> None:
    assert validate_folder(text)[1] == error


@pytest.mark.parametrize("bad", ["é", "(", "'", "&", "*"])
def test_characters_outside_the_set_are_refused(bad: str) -> None:
    assert validate_folder(f"/srv/a{bad}b")[1] is not None
    assert "may only contain" in str(validate_folder(f"/srv/a{bad}b")[1])


@pytest.mark.parametrize("ok", list("_.+@,:~=- "))
def test_space_and_each_punctuation_character_are_accepted(ok: str) -> None:
    assert validate_folder(f"/srv/a{ok}b")[1] is None


def test_dotdot_is_refused_before_normalising() -> None:
    assert validate_folder("/srv/a/../b")[1] == "the folder must not contain .."


def test_a_name_that_only_contains_dots_is_not_a_dotdot_component() -> None:
    assert validate_folder("/srv/a..b")[1] is None


def test_length_boundary() -> None:
    ok = "/srv/" + "a" * (4096 - 5)
    assert len(ok) == 4096
    assert validate_folder(ok)[1] is None
    assert validate_folder(ok + "a")[1] == "the folder is too long"


def test_depth_boundary() -> None:
    assert validate_folder("/data/models")[1] is None
    assert validate_folder("/data/")[1] is not None


def test_tree_boundaries() -> None:
    assert validate_folder("/var/lib/hal0/models")[1] is None
    assert validate_folder("/var/lib/hal0/models/x")[1] is None
    assert validate_folder("/var/lib/hal0/other")[1] == "the folder cannot be inside /var/lib"
    assert validate_folder("/var/lib")[1] == "the folder cannot be inside /var/lib"
    assert validate_folder("/etcetera/x")[1] is None
    assert validate_folder("/var/run/x")[1] == "the folder cannot be inside /var/run"


def test_the_path_is_returned_normalised() -> None:
    assert validate_folder("  /srv//pool/./models/ ") == ("/srv/pool/models", None)


def test_a_file_in_the_way_is_not_a_folder(tmp_path: Path) -> None:
    f = tmp_path / "file"
    f.write_text("x")
    if validate_folder(str(tmp_path))[1] is not None:
        pytest.skip("this machine's temporary folder is outside the allowed characters")
    assert validate_folder(f"{f}/models")[1] == f"{f} is not a folder"
