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


# ── GGML_XDNA_PGEMM_GLU_A is pinned to 0 (ADR-0057 decision 3) ───────────────


@pytest.mark.parametrize("value", ["1", " 1", "true", "2", ""])
def test_refuse_xdna_pinned_env_rejects_glu_a_other_than_zero(value: str) -> None:
    from hal0.errors import BadRequest
    from hal0.runners.knobs import refuse_xdna_pinned_env

    with pytest.raises(BadRequest) as exc:
        refuse_xdna_pinned_env({"GGML_XDNA_PGEMM_GLU_A": value})
    assert exc.value.status == 400
    assert exc.value.code == "validation.xdna_env_pinned"
    assert exc.value.details["key"] == "GGML_XDNA_PGEMM_GLU_A"
    assert "GGML_XDNA_PGEMM_GLU_A" in str(exc.value)
    assert "prefill" in str(exc.value)


def test_refuse_xdna_pinned_env_allows_restating_zero_and_other_knobs() -> None:
    from hal0.runners.knobs import refuse_xdna_pinned_env

    refuse_xdna_pinned_env({"GGML_XDNA_PGEMM_GLU_A": "0", "GGML_XDNA_HEAD": "1"})
    refuse_xdna_pinned_env(None)


def test_knob_schema_marks_glu_a_fixed() -> None:
    from hal0.runners.knobs import xdna_knob_schema

    fixed = {e["name"] for e in xdna_knob_schema()["env"] if e["fixed"]}
    assert fixed == {"GGML_XDNA_PGEMM_GLU_A"}
