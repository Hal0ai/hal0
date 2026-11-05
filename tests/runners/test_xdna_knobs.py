"""The xdna knob set: GGML_XDNA_* allow-list, defaults, pmode (xdna-runner §3)."""

from __future__ import annotations

import pytest

from hal0.errors import UnprocessableEntity
from hal0.runners.knobs import (
    XDNA_ENV_DEFAULTS,
    XDNA_ENV_DOCS,
    XDNA_PMODES,
    screen_xdna_env,
    validate_xdna_pmode,
    xdna_env_defaults,
)


def test_xdna_env_docs_cover_every_default() -> None:
    for name in XDNA_ENV_DEFAULTS:
        if name.startswith("GGML_XDNA_"):
            assert name in XDNA_ENV_DOCS


def test_xdna_env_defaults_are_pgemm_glu_a_off_and_omp_passive() -> None:
    defaults = xdna_env_defaults()
    assert defaults["GGML_XDNA_PGEMM_GLU_A"] == "0"
    assert defaults["OMP_WAIT_POLICY"] == "PASSIVE"
    # The returned dict is a copy — mutating it must not affect the module data.
    defaults["GGML_XDNA_PGEMM_GLU_A"] = "1"
    assert xdna_env_defaults()["GGML_XDNA_PGEMM_GLU_A"] == "0"


def test_screen_xdna_env_accepts_known_names() -> None:
    screen_xdna_env({"GGML_XDNA_HEAD": "1", "OMP_WAIT_POLICY": "ACTIVE"})


def test_screen_xdna_env_rejects_unknown_ggml_xdna_name() -> None:
    with pytest.raises(UnprocessableEntity) as exc:
        screen_xdna_env({"GGML_XDNA_NOT_A_REAL_KNOB": "1"})
    assert exc.value.code == "runners.xdna_env_unknown"


def test_screen_xdna_env_ignores_unrelated_keys() -> None:
    screen_xdna_env({"HSA_OVERRIDE_GFX_VERSION": "11.0.0"})


def test_screen_xdna_env_none_and_empty_pass() -> None:
    screen_xdna_env(None)
    screen_xdna_env({})


@pytest.mark.parametrize("pmode", list(XDNA_PMODES))
def test_validate_xdna_pmode_accepts_every_documented_value(pmode: str) -> None:
    assert validate_xdna_pmode(pmode) == pmode


def test_validate_xdna_pmode_none_passes() -> None:
    assert validate_xdna_pmode(None) is None


def test_validate_xdna_pmode_rejects_unknown_value() -> None:
    with pytest.raises(UnprocessableEntity) as exc:
        validate_xdna_pmode("ludicrous")
    assert exc.value.code == "runners.xdna_pmode_invalid"
