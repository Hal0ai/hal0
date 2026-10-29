"""Skill library behind the bridge's teach tools (ADR-0043 decision 10).

The library lists skills from hal0's shipped mirror (read-only; its entries
are symlinks into ``/usr/share/hal0/skills``) and from the operator drop-in
directory, and writes only to the drop-in directory. These tests use two
``tmp_path`` roots and pin every validation rule, the atomic swap, and the
refusal to follow a symlink or touch a shipped skill.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from hal0.bridge.errors import BridgeError
from hal0.bridge.skills import SkillLibrary
from hal0.security import nofollow


def md(name: str = "demo", description: str = "Does a thing.", body: str = "Body.\n") -> str:
    """A well-formed SKILL.md."""
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"


@pytest.fixture
def lib(tmp_path: Path) -> SkillLibrary:
    shipped = tmp_path / "shipped"
    dropin = tmp_path / "dropin"
    shipped.mkdir()
    dropin.mkdir()
    return SkillLibrary(shipped_root=shipped, dropin_root=dropin)


def dropin(lib: SkillLibrary) -> Path:
    return lib._dropin_root


def shipped(lib: SkillLibrary) -> Path:
    return lib._shipped_root


def make_skill(root: Path, name: str, text: str | None = None, **files: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text if text is not None else md(name))
    for fname, content in files.items():
        (d / fname).write_text(content)
    return d


def invalid(
    lib: SkillLibrary, name: str, skill_md: str, files: dict[str, str] | None = None
) -> str:
    """Run validate and return the message of the ``skill_invalid`` it must raise."""
    with pytest.raises(BridgeError) as exc:
        lib.validate(name, skill_md, files)
    assert exc.value.code == "bridge.skill_invalid"
    assert exc.value.status == 422
    return exc.value.message


# ── validate ────────────────────────────────────────────────────────────────


def test_validate_accepts_a_good_skill(lib: SkillLibrary) -> None:
    lib.validate("demo", md(), None)
    lib.validate("demo", md(), {"notes.md": "x", "script.sh": "echo hi\n"})


@pytest.mark.parametrize("name", ["a", "a1", "my-skill", "0day", "x" * 64])
def test_validate_accepts_good_names(lib: SkillLibrary, name: str) -> None:
    lib.validate(name, md(name), None)


@pytest.mark.parametrize(
    "name",
    ["", "Demo", "-demo", "de_mo", "de mo", "../demo", "a/b", ".demo", "x" * 65, "démo"],
)
def test_validate_rejects_bad_names(lib: SkillLibrary, name: str) -> None:
    assert "name" in invalid(lib, name, md("demo"))


def test_validate_rejects_missing_frontmatter(lib: SkillLibrary) -> None:
    assert "frontmatter" in invalid(lib, "demo", "# Just a heading\n")


def test_validate_rejects_unterminated_frontmatter(lib: SkillLibrary) -> None:
    assert "frontmatter" in invalid(lib, "demo", "---\nname: demo\ndescription: x\n")


def test_validate_rejects_empty_frontmatter(lib: SkillLibrary) -> None:
    assert "frontmatter" in invalid(lib, "demo", "---\n---\nbody\n")


def test_validate_rejects_unparsable_yaml(lib: SkillLibrary) -> None:
    assert "YAML" in invalid(lib, "demo", "---\nname: [unclosed\ndescription: x\n---\n")


def test_validate_rejects_non_mapping_frontmatter(lib: SkillLibrary) -> None:
    assert "mapping" in invalid(lib, "demo", "---\n- a\n- b\n---\n")


def test_validate_rejects_frontmatter_name_mismatch(lib: SkillLibrary) -> None:
    msg = invalid(lib, "demo", md("other"))
    assert "name" in msg
    assert "other" in msg


def test_validate_rejects_missing_frontmatter_name(lib: SkillLibrary) -> None:
    assert "name" in invalid(lib, "demo", "---\ndescription: x\n---\nbody\n")


def test_validate_rejects_missing_description(lib: SkillLibrary) -> None:
    assert "description" in invalid(lib, "demo", "---\nname: demo\n---\nbody\n")


@pytest.mark.parametrize("description", ["''", '"   "', "null", "[a, b]", "12"])
def test_validate_rejects_blank_or_non_string_description(
    lib: SkillLibrary, description: str
) -> None:
    text = f"---\nname: demo\ndescription: {description}\n---\nbody\n"
    assert "description" in invalid(lib, "demo", text)


@pytest.mark.parametrize(
    "key",
    ["a/b", "sub/file.md", "a\\b", "..", "x..y", "../escape", ".hidden", ".env", "", "a\0b"],
)
def test_validate_rejects_unsafe_file_keys(lib: SkillLibrary, key: str) -> None:
    assert "file" in invalid(lib, "demo", md(), {key: "x"})


def test_validate_rejects_a_file_named_like_the_skill_file(lib: SkillLibrary) -> None:
    assert "SKILL.md" in invalid(lib, "demo", md(), {"SKILL.md": "x"})


def test_validate_rejects_non_string_file_content(lib: SkillLibrary) -> None:
    bad: Any = {"a.md": 5}
    assert "text" in invalid(lib, "demo", md(), bad)


def test_validate_caps_the_file_count_at_16(lib: SkillLibrary) -> None:
    lib.validate("demo", md(), {f"f{i}.md": "x" for i in range(16)})
    assert "16" in invalid(lib, "demo", md(), {f"f{i}.md": "x" for i in range(17)})


def test_validate_caps_skill_md_at_256_kib(lib: SkillLibrary) -> None:
    head = md(body="")
    lib.validate("demo", head + "a" * (262144 - len(head)), None)
    assert "SKILL.md" in invalid(lib, "demo", head + "a" * (262144 - len(head) + 1))


def test_validate_caps_each_file_at_256_kib(lib: SkillLibrary) -> None:
    lib.validate("demo", md(), {"big.txt": "a" * 262144})
    assert "big.txt" in invalid(lib, "demo", md(), {"big.txt": "a" * 262145})


def test_validate_counts_utf8_bytes_not_characters(lib: SkillLibrary) -> None:
    # 131073 two-byte characters: fewer characters than the cap, more bytes.
    assert "big.txt" in invalid(lib, "demo", md(), {"big.txt": "é" * 131073})


def test_validate_rejects_text_that_is_not_utf8(lib: SkillLibrary) -> None:
    assert "UTF-8" in invalid(lib, "demo", md(), {"bad.txt": "\ud800"})


def test_validate_rejects_an_oversized_data_uri(lib: SkillLibrary) -> None:
    big = "data:image/png;base64," + "A" * 1100
    assert "data:" in invalid(lib, "demo", md(body=f"![x]({big})\n"))


def test_validate_allows_a_small_data_uri(lib: SkillLibrary) -> None:
    small = "data:text/plain;base64," + "A" * 200
    lib.validate("demo", md(body=f"![x]({small})\n"), None)


# ── put ─────────────────────────────────────────────────────────────────────


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_put_creates_skill_and_files_with_modes(lib: SkillLibrary) -> None:
    got = lib.put("demo", md(), {"notes.md": "hello\n"})
    d = dropin(lib) / "demo"
    assert (d / "SKILL.md").read_text() == md()
    assert (d / "notes.md").read_text() == "hello\n"
    assert mode(d / "SKILL.md") == 0o644
    assert mode(d / "notes.md") == 0o644
    assert mode(d) == 0o755
    assert got["name"] == "demo"
    assert got["source"] == "dropin"
    assert got["skill_md"] == md()
    assert got["files"] == ["notes.md"]


def test_put_without_files(lib: SkillLibrary) -> None:
    got = lib.put("demo", md(), None)
    assert got["files"] == []
    assert os.listdir(dropin(lib) / "demo") == ["SKILL.md"]


def test_put_replaces_an_existing_skill_atomically(lib: SkillLibrary) -> None:
    lib.put("demo", md(description="old"), {"old.md": "old"})
    lib.put("demo", md(description="new"), {"new.md": "new"})
    d = dropin(lib) / "demo"
    assert sorted(os.listdir(d)) == ["SKILL.md", "new.md"]
    assert "new" in (d / "SKILL.md").read_text()
    # No .tmp-/.old- leftovers beside the skill.
    assert os.listdir(dropin(lib)) == ["demo"]


def test_put_validates_before_touching_disk(lib: SkillLibrary) -> None:
    with pytest.raises(BridgeError) as exc:
        lib.put("demo", md("other"), None)
    assert exc.value.code == "bridge.skill_invalid"
    assert os.listdir(dropin(lib)) == []


def test_put_refuses_a_symlinked_target(lib: SkillLibrary, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    make_skill(tmp_path, "elsewhere")
    (dropin(lib) / "demo").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(BridgeError) as exc:
        lib.put("demo", md(), {"x.md": "x"})
    assert exc.value.code == "bridge.skill_invalid"
    assert "symlink" in exc.value.message
    assert (dropin(lib) / "demo").is_symlink()
    assert sorted(os.listdir(elsewhere)) == ["SKILL.md"]
    assert os.listdir(dropin(lib)) == ["demo"]


def test_put_refuses_a_name_shipped_in_the_mirror(lib: SkillLibrary, tmp_path: Path) -> None:
    real = make_skill(tmp_path / "usr-share", "hal0-bench")
    (shipped(lib) / "hal0-bench").symlink_to(real, target_is_directory=True)
    with pytest.raises(BridgeError) as exc:
        lib.put("hal0-bench", md("hal0-bench"), None)
    assert exc.value.code == "bridge.skill_readonly"
    assert exc.value.status == 403
    assert os.listdir(dropin(lib)) == []


def test_put_refuses_a_categorised_shipped_name(lib: SkillLibrary) -> None:
    make_skill(shipped(lib) / "homelab", "bench")
    with pytest.raises(BridgeError) as exc:
        lib.put("bench", md("bench"), None)
    assert exc.value.code == "bridge.skill_readonly"


def test_put_refuses_to_replace_a_category_directory(lib: SkillLibrary) -> None:
    make_skill(dropin(lib) / "ops", "inner")
    with pytest.raises(BridgeError) as exc:
        lib.put("ops", md("ops"), None)
    assert exc.value.code == "bridge.skill_invalid"
    assert (dropin(lib) / "ops" / "inner" / "SKILL.md").is_file()


def test_put_refuses_to_replace_a_plain_file(lib: SkillLibrary) -> None:
    (dropin(lib) / "demo").write_text("not a dir")
    with pytest.raises(BridgeError) as exc:
        lib.put("demo", md(), None)
    assert exc.value.code == "bridge.skill_invalid"
    assert (dropin(lib) / "demo").read_text() == "not a dir"


def test_put_updates_a_categorised_dropin_skill_in_place(lib: SkillLibrary) -> None:
    make_skill(dropin(lib) / "ops", "deploy", md("deploy", "old"), old="x")
    got = lib.put("deploy", md("deploy", "new"), {"new.md": "n"})
    assert got["category"] == "ops"
    assert not (dropin(lib) / "deploy").exists()
    assert sorted(os.listdir(dropin(lib) / "ops" / "deploy")) == ["SKILL.md", "new.md"]
    assert sorted(os.listdir(dropin(lib) / "ops")) == ["deploy"]


def test_put_creates_the_dropin_root_when_missing(tmp_path: Path) -> None:
    lib = SkillLibrary(shipped_root=tmp_path / "s", dropin_root=tmp_path / "var" / "skills")
    lib.put("demo", md(), None)
    assert (tmp_path / "var" / "skills" / "demo" / "SKILL.md").is_file()


def test_put_refuses_a_symlinked_dropin_root_under_a_group_writable_parent(
    tmp_path: Path,
) -> None:
    # /var/lib/hal0 is group-writable, so a link planted there is not followed.
    state = tmp_path / "state"
    state.mkdir()
    state.chmod(0o775)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (state / "skills").symlink_to(elsewhere, target_is_directory=True)
    lib = SkillLibrary(shipped_root=tmp_path / "shipped", dropin_root=state / "skills")
    with pytest.raises(BridgeError) as exc:
        lib.put("demo", md(), None)
    assert exc.value.code == "bridge.skill_invalid"
    assert os.listdir(elsewhere) == []


def test_put_failure_midway_leaves_the_old_skill_and_no_debris(
    lib: SkillLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib.put("demo", md(description="old"), {"keep.md": "keep"})
    real = nofollow.write_file
    calls = {"n": 0}

    def flaky(path: Path | str, content: str | bytes, **kw: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        real(path, content, **kw)

    monkeypatch.setattr(nofollow, "write_file", flaky)
    with pytest.raises(OSError, match="disk full"):
        lib.put("demo", md(description="new"), {"a.md": "a", "b.md": "b"})
    d = dropin(lib) / "demo"
    assert sorted(os.listdir(d)) == ["SKILL.md", "keep.md"]
    assert "old" in (d / "SKILL.md").read_text()
    assert os.listdir(dropin(lib)) == ["demo"]


# ── list ────────────────────────────────────────────────────────────────────


def test_list_sees_both_roots_and_categories(lib: SkillLibrary, tmp_path: Path) -> None:
    real = make_skill(tmp_path / "usr-share", "hal0-bench", md("hal0-bench", "Benchmarks."))
    (shipped(lib) / "hal0-bench").symlink_to(real, target_is_directory=True)
    make_skill(dropin(lib), "mine", md("mine", "Mine."), extra="e")
    make_skill(dropin(lib) / "ops", "deploy", md("deploy", "Deploys."))
    # Debris and non-skills are not listed.
    (dropin(lib) / ".tmp-x-1").mkdir()
    (dropin(lib) / ".old-x-1").mkdir()
    (dropin(lib) / "empty-dir").mkdir()
    (dropin(lib) / "stray.txt").write_text("x")

    rows = {(r["name"], r["source"]): r for r in lib.list()}
    assert set(rows) == {("hal0-bench", "shipped"), ("mine", "dropin"), ("deploy", "dropin")}

    bench = rows[("hal0-bench", "shipped")]
    assert bench["category"] is None
    assert bench["description"] == "Benchmarks."
    assert bench["files"] == []
    assert isinstance(bench["mtime"], float)

    mine = rows[("mine", "dropin")]
    assert mine["category"] is None
    assert mine["description"] == "Mine."
    assert mine["files"] == ["extra"]

    deploy = rows[("deploy", "dropin")]
    assert deploy["category"] == "ops"
    assert deploy["description"] == "Deploys."


def test_list_includes_a_name_present_in_both_roots(lib: SkillLibrary) -> None:
    make_skill(shipped(lib), "both", md("both", "Shipped one."))
    make_skill(dropin(lib), "both", md("both", "Dropin one."))
    rows = [r for r in lib.list() if r["name"] == "both"]
    assert {(r["source"], r["description"]) for r in rows} == {
        ("shipped", "Shipped one."),
        ("dropin", "Dropin one."),
    }


def test_list_does_not_follow_symlinks_in_the_dropin_root(
    lib: SkillLibrary, tmp_path: Path
) -> None:
    real = make_skill(tmp_path / "elsewhere", "linked")
    (dropin(lib) / "linked").symlink_to(real, target_is_directory=True)
    make_skill(dropin(lib), "plain")
    assert [r["name"] for r in lib.list()] == ["plain"]


def test_list_tolerates_a_broken_skill_md(lib: SkillLibrary) -> None:
    make_skill(dropin(lib), "broken", "no frontmatter at all\n")
    rows = lib.list()
    assert [r["name"] for r in rows] == ["broken"]
    assert rows[0]["description"] == ""


def test_list_with_missing_roots_is_empty(tmp_path: Path) -> None:
    lib = SkillLibrary(shipped_root=tmp_path / "nope", dropin_root=tmp_path / "nada")
    assert lib.list() == []


# ── get ─────────────────────────────────────────────────────────────────────


def test_get_prefers_dropin_over_shipped(lib: SkillLibrary) -> None:
    make_skill(shipped(lib), "both", md("both", "Shipped one."))
    make_skill(dropin(lib), "both", md("both", "Dropin one."))
    got = lib.get("both")
    assert got["source"] == "dropin"
    assert "Dropin one." in got["skill_md"]


def test_get_falls_back_to_shipped_through_a_mirror_symlink(
    lib: SkillLibrary, tmp_path: Path
) -> None:
    real = make_skill(tmp_path / "usr-share", "hal0-bench", md("hal0-bench"), ref="r")
    (shipped(lib) / "hal0-bench").symlink_to(real, target_is_directory=True)
    got = lib.get("hal0-bench")
    assert got == {
        "name": "hal0-bench",
        "source": "shipped",
        "category": None,
        "skill_md": md("hal0-bench"),
        "files": ["ref"],
    }


def test_get_finds_a_categorised_skill(lib: SkillLibrary) -> None:
    make_skill(dropin(lib) / "ops", "deploy")
    got = lib.get("deploy")
    assert got["category"] == "ops"
    assert got["source"] == "dropin"


def test_get_lists_nested_files_without_skill_md(lib: SkillLibrary) -> None:
    d = make_skill(dropin(lib), "demo", notes="n")
    (d / "refs").mkdir()
    (d / "refs" / "a.md").write_text("a")
    assert lib.get("demo")["files"] == ["notes", "refs/a.md"]


def test_get_unknown_is_not_found(lib: SkillLibrary) -> None:
    with pytest.raises(BridgeError) as exc:
        lib.get("nope")
    assert exc.value.code == "bridge.skill_not_found"
    assert exc.value.status == 404


@pytest.mark.parametrize("name", ["../etc", "a/b", "", ".hidden"])
def test_get_rejects_malformed_names(lib: SkillLibrary, name: str) -> None:
    with pytest.raises(BridgeError) as exc:
        lib.get(name)
    assert exc.value.code == "bridge.skill_invalid"


def test_get_does_not_read_through_a_dropin_symlink(lib: SkillLibrary, tmp_path: Path) -> None:
    real = make_skill(tmp_path / "elsewhere", "linked")
    (dropin(lib) / "linked").symlink_to(real, target_is_directory=True)
    with pytest.raises(BridgeError) as exc:
        lib.get("linked")
    assert exc.value.code == "bridge.skill_not_found"


# ── delete ──────────────────────────────────────────────────────────────────


def test_delete_removes_the_dropin_dir(lib: SkillLibrary) -> None:
    lib.put("demo", md(), {"a.md": "a"})
    lib.delete("demo")
    assert os.listdir(dropin(lib)) == []


def test_delete_removes_a_categorised_dropin_skill(lib: SkillLibrary) -> None:
    make_skill(dropin(lib) / "ops", "deploy")
    lib.delete("deploy")
    assert not (dropin(lib) / "ops" / "deploy").exists()
    assert (dropin(lib) / "ops").is_dir()


def test_delete_shipped_is_readonly(lib: SkillLibrary, tmp_path: Path) -> None:
    real = make_skill(tmp_path / "usr-share", "hal0-bench")
    (shipped(lib) / "hal0-bench").symlink_to(real, target_is_directory=True)
    with pytest.raises(BridgeError) as exc:
        lib.delete("hal0-bench")
    assert exc.value.code == "bridge.skill_readonly"
    assert (real / "SKILL.md").is_file()
    assert (shipped(lib) / "hal0-bench").is_symlink()


def test_delete_unknown_is_not_found(lib: SkillLibrary) -> None:
    with pytest.raises(BridgeError) as exc:
        lib.delete("nope")
    assert exc.value.code == "bridge.skill_not_found"


def test_delete_removes_the_dropin_copy_of_a_shipped_name(lib: SkillLibrary) -> None:
    make_skill(shipped(lib), "both")
    make_skill(dropin(lib), "both")
    lib.delete("both")
    assert not (dropin(lib) / "both").exists()
    assert (shipped(lib) / "both" / "SKILL.md").is_file()


def test_delete_refuses_a_symlinked_dir(lib: SkillLibrary, tmp_path: Path) -> None:
    real = make_skill(tmp_path / "elsewhere", "linked")
    (dropin(lib) / "linked").symlink_to(real, target_is_directory=True)
    with pytest.raises(BridgeError) as exc:
        lib.delete("linked")
    assert exc.value.code == "bridge.skill_invalid"
    assert "symlink" in exc.value.message
    assert (real / "SKILL.md").is_file()
    assert (dropin(lib) / "linked").is_symlink()


@pytest.mark.parametrize("name", ["../x", "a/b", ""])
def test_delete_rejects_malformed_names(lib: SkillLibrary, name: str) -> None:
    with pytest.raises(BridgeError) as exc:
        lib.delete(name)
    assert exc.value.code == "bridge.skill_invalid"
