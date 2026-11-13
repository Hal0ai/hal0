"""render_showcase_env — the H0-98 fix (global + per-op LLM lane caps)."""

from __future__ import annotations

from hal0.config.schema import MemoryDecisionConfig, MemoryLanesConfig, MemoryModelsConfig
from hal0.memory.extraction_env import render_showcase_env


def test_render_showcase_env_defaults_set_global_and_every_op_cap() -> None:
    out = render_showcase_env(
        models=MemoryModelsConfig(),
        decision=MemoryDecisionConfig(),
        lanes=MemoryLanesConfig(),
        decision_unit_installed=True,
    )
    assert "Environment=HINDSIGHT_API_LLM_MAX_CONCURRENT=1" in out
    for prefix in ("RETAIN", "CONSOLIDATION", "REFLECT", "MENTAL_MODEL_REFRESH"):
        # H0-98: every op gets an explicit cap, not just RETAIN.
        assert f"Environment=HINDSIGHT_API_{prefix}_LLM_MAX_CONCURRENT=1" in out
    assert "Environment=HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=hindsight-ai/hs-embed-1" in out
    assert "Environment=HINDSIGHT_API_RERANKER_LOCAL_MODEL=hindsight-ai/hs-rerank-1" in out
    assert "Environment=HINDSIGHT_API_DECISION_MODEL_PROVIDER=hindsight" in out
    assert "Environment=HINDSIGHT_API_DECISION_MODEL_BASE_URL=http://127.0.0.1:9178" in out
    # Required even for a local base URL (verified gotcha, zz-showcase-1.2.conf).
    assert "Environment=HINDSIGHT_API_DECISION_MODEL_API_KEY=hal0-local-noauth" in out


def test_render_showcase_env_per_op_override_wins_over_global() -> None:
    lanes = MemoryLanesConfig(
        llm_max_concurrent=2,
        reflect={"max_concurrent": 5, "virtual_model": "hal0/utility", "timeout_s": 900},
    )
    out = render_showcase_env(
        models=MemoryModelsConfig(), decision=MemoryDecisionConfig(), lanes=lanes
    )
    assert "Environment=HINDSIGHT_API_REFLECT_LLM_MAX_CONCURRENT=5" in out
    assert "Environment=HINDSIGHT_API_REFLECT_LLM_MODEL=hal0/utility" in out
    assert "Environment=HINDSIGHT_API_REFLECT_LLM_TIMEOUT=900" in out
    # Untouched ops still inherit the (new) global cap.
    assert "Environment=HINDSIGHT_API_CONSOLIDATION_LLM_MAX_CONCURRENT=2" in out


def test_render_showcase_env_decision_off_omits_decision_env() -> None:
    out = render_showcase_env(
        models=MemoryModelsConfig(),
        decision=MemoryDecisionConfig(provider="off"),
        lanes=MemoryLanesConfig(),
    )
    assert "DECISION_MODEL" not in out


def test_render_showcase_env_is_deterministic() -> None:
    args = dict(
        models=MemoryModelsConfig(), decision=MemoryDecisionConfig(), lanes=MemoryLanesConfig()
    )
    assert render_showcase_env(**args) == render_showcase_env(**args)


# ── #3519: render the decision env only when the decision lane can answer ──


def test_render_showcase_env_local_without_the_unit_omits_decision_env() -> None:
    out = render_showcase_env(
        models=MemoryModelsConfig(),
        decision=MemoryDecisionConfig(provider="local"),
        lanes=MemoryLanesConfig(),
        decision_unit_installed=False,
    )
    assert "DECISION_MODEL" not in out
    # The rest of the showcase env is unaffected.
    assert "Environment=HINDSIGHT_API_LLM_MAX_CONCURRENT=1" in out


def test_render_showcase_env_probes_the_unit_path_by_default(tmp_path, monkeypatch) -> None:
    import hal0.memory.extraction_env as ee

    unit = tmp_path / "hal0-hs-decide.service"
    monkeypatch.setattr(ee, "DECISION_UNIT_PATH", unit)
    args = dict(
        models=MemoryModelsConfig(), decision=MemoryDecisionConfig(), lanes=MemoryLanesConfig()
    )
    assert "DECISION_MODEL" not in render_showcase_env(**args)
    unit.write_text("[Unit]\n")
    assert (
        "Environment=HINDSIGHT_API_DECISION_MODEL_BASE_URL=http://127.0.0.1:9178"
        in render_showcase_env(**args)
    )


def test_render_showcase_env_explicit_loopback_base_url_renders_as_configured() -> None:
    out = render_showcase_env(
        models=MemoryModelsConfig(),
        decision=MemoryDecisionConfig(base_url="http://127.0.0.1:9300/"),
        lanes=MemoryLanesConfig(),
        decision_unit_installed=False,
    )
    assert "Environment=HINDSIGHT_API_DECISION_MODEL_BASE_URL=http://127.0.0.1:9300" in out


def test_render_showcase_env_base_url_the_allow_list_refuses_is_omitted() -> None:
    out = render_showcase_env(
        models=MemoryModelsConfig(),
        decision=MemoryDecisionConfig(base_url="http://decide.example:9178"),
        lanes=MemoryLanesConfig(),
        decision_unit_installed=True,
    )
    assert "DECISION_MODEL" not in out


def test_render_showcase_env_cloud_does_not_need_the_unit() -> None:
    out = render_showcase_env(
        models=MemoryModelsConfig(),
        decision=MemoryDecisionConfig(provider="hindsight-cloud"),
        lanes=MemoryLanesConfig(),
        decision_unit_installed=False,
    )
    assert "Environment=HINDSIGHT_API_DECISION_MODEL_BASE_URL=https://api.hindsight.ai" in out


def test_render_showcase_env_disabled_ignores_the_unit() -> None:
    out = render_showcase_env(
        models=MemoryModelsConfig(),
        decision=MemoryDecisionConfig(enabled=False),
        lanes=MemoryLanesConfig(),
        decision_unit_installed=True,
    )
    assert "DECISION_MODEL" not in out
