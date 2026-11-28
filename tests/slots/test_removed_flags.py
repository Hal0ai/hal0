"""Flags llama-server has removed are refused when a profile is saved (#4063)."""

from __future__ import annotations

import pytest

from hal0.errors import UnprocessableEntity
from hal0.profiles import runner_runtime_family, screen_profile_flags
from hal0.slots.argv import (
    FLAG_ALIASES,
    REMOVED_FLAGS,
    VALUELESS_FLAGS,
    deny_removed_flags,
    normalize_argv,
)


def test_removed_flags_share_no_key_with_the_live_tables() -> None:
    """The alias and valueless tables describe flags the server has; the
    removed map describes flags it lacks. One spelling cannot be in both."""
    removed = set(REMOVED_FLAGS)
    assert not removed & set(FLAG_ALIASES)
    assert not removed & set(FLAG_ALIASES.values())
    assert not removed & set(VALUELESS_FLAGS)


@pytest.mark.parametrize("flag", sorted(REMOVED_FLAGS))
def test_profile_save_refuses_each_removed_flag(flag: str) -> None:
    with pytest.raises(UnprocessableEntity) as exc_info:
        screen_profile_flags(f"-fa on {flag} -b 2048", runtime_family="llama-server")
    err = exc_info.value
    assert err.code == "slot.removed_flag_denied"
    assert err.details["flags"] == [flag]
    assert err.details["replacements"] == {flag: REMOVED_FLAGS[flag]}
    assert REMOVED_FLAGS[flag] in str(err)


def test_refusal_names_the_replacement_for_every_offender_once() -> None:
    with pytest.raises(UnprocessableEntity) as exc_info:
        deny_removed_flags(
            ["--no-mmap", "--mlock", "-b", "512", "--no-mmap"], segment="profile flags"
        )
    err = exc_info.value
    assert err.details["flags"] == ["--no-mmap", "--mlock"]
    assert "`-lm none`" in str(err)
    assert "`-lm mmap+mlock`" in str(err)


@pytest.mark.parametrize("spelling", ["--no_mmap", "--no-mmap=1", "--direct_io"])
def test_refusal_sees_the_spellings_llama_cpp_would_fold(spelling: str) -> None:
    """Underscores in a long flag and the joined ``flag=value`` form reach the
    same option in llama.cpp, so they reach the same refusal here."""
    with pytest.raises(UnprocessableEntity) as exc_info:
        deny_removed_flags([spelling], segment="profile flags")
    assert exc_info.value.details["flags"] == [spelling]


@pytest.mark.parametrize(
    "flags",
    ["-fa on -lm none", "--load-mode mmap+mlock -b 2048", "-lm dio", "", "--mmproj-offload"],
)
def test_the_replacement_and_unrelated_flags_are_accepted(flags: str) -> None:
    screen_profile_flags(flags, runtime_family="llama-server")
    deny_removed_flags(flags.split(), segment="profile flags")


def test_load_mode_spellings_dedupe_against_each_other() -> None:
    out = normalize_argv(["-lm", "none", "-b", "512", "--load-mode", "mmap"])
    assert out.argv == ["-b", "512", "--load-mode", "mmap"]
    assert out.removed == 1


@pytest.mark.parametrize("runner", ["standard", "cpu", "cuda", "xdna"])
def test_every_llama_server_runner_gets_the_refusal(runner: str) -> None:
    """Each of these runners' pinned option tables lacks the removed
    spellings (see the comment on ``REMOVED_FLAGS``), the NPU fork included."""
    family = runner_runtime_family(runner)
    assert family == "llama-server"
    with pytest.raises(UnprocessableEntity):
        screen_profile_flags("--no-mmap", runtime_family=family)


def test_halogen_profiles_keep_their_own_screen() -> None:
    """A halogen profile's flags are environment tokens; the llama-server map
    does not apply to them."""
    screen_profile_flags("HALOGEN_ROPE_YARN=1", runtime_family="halogen")
