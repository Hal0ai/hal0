"""#3717 — engine reranker settings in [memory.models].

The candidate cap, the local cross-encoder knobs and a remote provider
(``litellm`` against a Cohere-style ``/rerank``, hal0's own included, or
``tei``) are rendered into the showcase drop-in. Schema validation refuses the
combinations the engine could not start on, and every rendered body passes the
root-side allow-list of the real ``hal0-systemctl`` wrapper.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from hal0.api._settings_apply import REGISTRY, apply_plan
from hal0.config.schema import (
    MemoryDecisionConfig,
    MemoryLanesConfig,
    MemoryModelsConfig,
)
from hal0.memory.extraction_env import render_reranker_env, render_showcase_env

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "installer" / "wrappers" / "hal0-systemctl"

HAL0_RERANK = {
    "reranker_provider": "litellm",
    "reranker_base_url": "http://127.0.0.1:8080/v1",
    "reranker_api_model": "builtin.jina-reranker-v1-tiny-en-q8",
}


def _env(**kw) -> dict[str, str]:
    lines = render_reranker_env(MemoryModelsConfig(**kw))
    out: dict[str, str] = {}
    for line in lines:
        assert line.startswith("Environment=")
        name, _, value = line.removeprefix("Environment=").partition("=")
        assert name not in out, f"duplicate {name}"
        out[name] = value
    return out


# ── defaults ────────────────────────────────────────────────────────────────


def test_defaults_keep_todays_local_env_and_add_the_cap() -> None:
    assert _env() == {
        "HINDSIGHT_API_RERANKER_PROVIDER": "local",
        "HINDSIGHT_API_RERANKER_MAX_CANDIDATES": "60",
        "HINDSIGHT_API_RERANKER_LOCAL_MODEL": "hindsight-ai/hs-rerank-1",
        "HINDSIGHT_API_RERANKER_LOCAL_BUCKET_BATCHING": "true",
    }


def test_default_showcase_body_carries_the_cap() -> None:
    body = render_showcase_env(
        models=MemoryModelsConfig(),
        decision=MemoryDecisionConfig(),
        lanes=MemoryLanesConfig(),
        decision_unit_installed=False,
    )
    assert "Environment=HINDSIGHT_API_RERANKER_MAX_CANDIDATES=60\n" in body


# ── local knobs ─────────────────────────────────────────────────────────────


def test_local_knobs_render_only_when_set() -> None:
    env = _env(
        reranker_local_max_concurrent=2,
        reranker_local_batch_size=16,
        reranker_local_timeout_s=0,
        reranker_max_candidates_low=20,
        reranker_max_candidates_high=120,
    )
    assert env["HINDSIGHT_API_RERANKER_LOCAL_MAX_CONCURRENT"] == "2"
    assert env["HINDSIGHT_API_RERANKER_LOCAL_BATCH_SIZE"] == "16"
    # 0 is the engine's "no ceiling", a real value, not "unset".
    assert env["HINDSIGHT_API_RERANKER_LOCAL_TIMEOUT"] == "0"
    assert env["HINDSIGHT_API_RERANKER_MAX_CANDIDATES_LOW"] == "20"
    assert env["HINDSIGHT_API_RERANKER_MAX_CANDIDATES_HIGH"] == "120"
    assert "HINDSIGHT_API_RERANKER_MAX_CANDIDATES_MID" not in env


def test_remote_keys_are_ignored_for_local() -> None:
    env = _env(reranker_base_url="http://127.0.0.1:8080/v1", reranker_timeout_s=5)
    assert not any("LITELLM" in k or "TEI" in k for k in env)


# ── remote providers ────────────────────────────────────────────────────────


def test_litellm_targets_hal0s_own_rerank_endpoint() -> None:
    env = _env(**HAL0_RERANK, reranker_timeout_s=15, reranker_local_batch_size=8)
    assert env == {
        "HINDSIGHT_API_RERANKER_PROVIDER": "litellm",
        "HINDSIGHT_API_RERANKER_MAX_CANDIDATES": "60",
        "HINDSIGHT_API_RERANKER_LITELLM_API_BASE": "http://127.0.0.1:8080/v1",
        "HINDSIGHT_API_RERANKER_LITELLM_MODEL": "builtin.jina-reranker-v1-tiny-en-q8",
        "HINDSIGHT_API_RERANKER_LITELLM_TIMEOUT": "15",
    }
    # No key is rendered: the engine sends no Authorization header when unset.
    assert not any("API_KEY" in k for k in env)


def test_tei_renders_its_url_and_timeout() -> None:
    env = _env(
        reranker_provider="tei", reranker_base_url="http://rerank.lan:8081/", reranker_timeout_s=9
    )
    assert env["HINDSIGHT_API_RERANKER_TEI_URL"] == "http://rerank.lan:8081"
    assert env["HINDSIGHT_API_RERANKER_TEI_HTTP_TIMEOUT"] == "9"
    assert "HINDSIGHT_API_RERANKER_LOCAL_MODEL" not in env


def test_rrf_renders_only_provider_and_caps() -> None:
    assert _env(reranker_provider="rrf") == {
        "HINDSIGHT_API_RERANKER_PROVIDER": "rrf",
        "HINDSIGHT_API_RERANKER_MAX_CANDIDATES": "60",
    }


# ── validation ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ({"reranker_provider": "cohere"}, "must be one of"),
        (
            {"reranker_provider": "litellm", "reranker_api_model": "m"},
            "needs memory.models.reranker_base_url",
        ),
        (
            {"reranker_provider": "litellm", "reranker_base_url": "http://127.0.0.1:8080/v1"},
            "needs memory.models.reranker_api_model",
        ),
        ({"reranker_provider": "tei"}, "reranker_base_url"),
        ({"reranker_base_url": "http://user:pw@host/v1"}, "credentials"),
        ({"reranker_base_url": "ftp://host"}, "plain"),
        ({"reranker_base_url": "http://host/v1?key=x"}, "plain"),
        ({"reranker_base_url": "http://host/a b"}, "plain"),
        ({"reranker_base_url": "http://host/../etc"}, "plain"),
        ({"reranker_api_model": "a b"}, "reranker_api_model"),
        ({"reranker_max_candidates": 0}, "greater than or equal"),
        ({"reranker_max_candidates_mid": 0}, "greater than or equal"),
        ({"reranker_local_timeout_s": -1}, "greater than or equal"),
        ({"reranker_timeout_s": 0}, "greater than or equal"),
    ],
)
def test_invalid_combinations_are_refused(body: dict, needle: str) -> None:
    with pytest.raises(ValidationError, match=needle):
        MemoryModelsConfig(**body)


def test_empty_strings_read_as_unset() -> None:
    cfg = MemoryModelsConfig(reranker_base_url="", reranker_api_model="")
    assert cfg.reranker_base_url is None and cfg.reranker_api_model is None


# ── root-side allow-list ────────────────────────────────────────────────────


def _check(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WRAPPER), "check-dropin", "showcase"],
        input=body,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )


@pytest.mark.parametrize(
    "models",
    [
        pytest.param({}, id="defaults"),
        pytest.param(
            {
                "reranker_local_max_concurrent": 2,
                "reranker_local_batch_size": 16,
                "reranker_local_timeout_s": 120,
                "reranker_max_candidates": 300,
                "reranker_max_candidates_low": 20,
                "reranker_max_candidates_mid": 60,
                "reranker_max_candidates_high": 200,
            },
            id="local-all-knobs",
        ),
        pytest.param({**HAL0_RERANK, "reranker_timeout_s": 30}, id="litellm-hal0"),
        pytest.param(
            {"reranker_provider": "tei", "reranker_base_url": "https://tei.example.com/x"},
            id="tei",
        ),
        pytest.param({"reranker_provider": "rrf"}, id="rrf"),
    ],
)
def test_rendered_bodies_pass_the_wrapper_allow_list(models: dict) -> None:
    body = render_showcase_env(
        models=MemoryModelsConfig(**models),
        decision=MemoryDecisionConfig(),
        lanes=MemoryLanesConfig(),
        decision_unit_installed=True,
    )
    proc = _check(body)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == body


@pytest.mark.parametrize(
    "line",
    [
        "Environment=HINDSIGHT_API_RERANKER_LITELLM_API_BASE=http://u:p@host/v1",
        "Environment=HINDSIGHT_API_RERANKER_LITELLM_API_BASE=http://host/v1?x=1",
        "Environment=HINDSIGHT_API_RERANKER_LITELLM_API_BASE=file:///etc/passwd",
        "Environment=HINDSIGHT_API_RERANKER_TEI_URL=http://host/../x",
        "Environment=HINDSIGHT_API_RERANKER_MAX_CANDIDATES=-1",
        "Environment=HINDSIGHT_API_RERANKER_MAX_CANDIDATES=6O",
        "Environment=HINDSIGHT_API_RERANKER_LITELLM_MODEL=../x",
        "Environment=HINDSIGHT_API_RERANKER_LITELLM_API_KEY=sk-live",
        "Environment=HINDSIGHT_API_RERANKER_COHERE_API_KEY=sk-live",
    ],
)
def test_wrapper_refuses_bad_reranker_lines(line: str) -> None:
    proc = _check("[Service]\n" + line + "\n")
    assert proc.returncode == 64, proc.stdout
    assert "drop-in rejected" in proc.stderr


# ── settings apply ──────────────────────────────────────────────────────────

_NEW_KEYS = [
    "memory.models.reranker_max_candidates",
    "memory.models.reranker_max_candidates_low",
    "memory.models.reranker_max_candidates_mid",
    "memory.models.reranker_max_candidates_high",
    "memory.models.reranker_local_max_concurrent",
    "memory.models.reranker_local_batch_size",
    "memory.models.reranker_local_timeout_s",
    "memory.models.reranker_base_url",
    "memory.models.reranker_api_model",
    "memory.models.reranker_timeout_s",
]


@pytest.mark.parametrize("key", _NEW_KEYS)
def test_new_keys_apply_immediately_like_their_siblings(key: str) -> None:
    assert REGISTRY[key] == REGISTRY["memory.models.reranker_model"]
    assert apply_plan([key])["immediate"] == [key]


def test_every_models_field_has_a_registry_row() -> None:
    for name in MemoryModelsConfig.model_fields:
        assert f"memory.models.{name}" in REGISTRY, name
