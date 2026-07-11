"""Unit tests for the Honcho compose env renderer.

``render_env`` turns ``Hal0Config.honcho`` into the ``/etc/hal0/honcho.env``
contents the Honcho v3 docker compose stack ``env_file:``-mounts. Every LLM
feature route defaults to the local hal0-api gateway (keyless); operators can
point individual features at a cloud upstream via base_url/api_key_env.
"""

from __future__ import annotations

from pathlib import Path

import hal0.memory.honcho_env as he
from hal0.config.schema import Hal0Config
from hal0.memory.honcho_env import LOCAL_BASE_URL

#: Guaranteed-absent path — stands in for "no operator secrets file yet".
_NO_SECRETS = Path("/nonexistent-honcho-secrets-test-path/honcho.env")


def _honcho_cfg(**honcho_kwargs) -> Hal0Config:
    return Hal0Config.model_validate({"honcho": honcho_kwargs} if honcho_kwargs else {})


class TestRenderEnvDefaults:
    def test_all_features_use_local_defaults(self, monkeypatch):
        monkeypatch.setattr(he, "SECRETS_PATH", _NO_SECRETS)
        out = he.render_env(_honcho_cfg())

        assert "LLM_OPENAI_API_KEY=hal0-local-noauth" in out
        assert "AUTH_USE_AUTH=false" in out
        assert "EMBED_MESSAGES=true" in out
        assert "EMBEDDING_VECTOR_DIMENSIONS=1024" in out

        assert "EMBEDDING_MODEL_CONFIG__TRANSPORT=openai" in out
        assert "EMBEDDING_MODEL_CONFIG__MODEL=qwen3-embedding-0-6b-q8-0" in out
        assert f"EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL={LOCAL_BASE_URL}" in out

        assert "DERIVER_MODEL_CONFIG__MODEL=hal0/utility" in out
        assert f"DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL={LOCAL_BASE_URL}" in out
        assert "SUMMARY_MODEL_CONFIG__MODEL=hal0/utility" in out

    def test_dream_emits_both_deduction_and_induction(self, monkeypatch):
        monkeypatch.setattr(he, "SECRETS_PATH", _NO_SECRETS)
        out = he.render_env(_honcho_cfg())

        assert "DREAM_DEDUCTION_MODEL_CONFIG__MODEL=hal0/utility" in out
        assert "DREAM_INDUCTION_MODEL_CONFIG__MODEL=hal0/utility" in out
        assert f"DREAM_DEDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL={LOCAL_BASE_URL}" in out
        assert f"DREAM_INDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL={LOCAL_BASE_URL}" in out

    def test_all_five_dialectic_levels_present(self, monkeypatch):
        monkeypatch.setattr(he, "SECRETS_PATH", _NO_SECRETS)
        out = he.render_env(_honcho_cfg())

        for level in ("minimal", "low", "medium", "high", "max"):
            prefix = f"DIALECTIC_LEVELS__{level}__MODEL_CONFIG"
            assert f"{prefix}__MODEL=hal0/agent" in out
            assert f"{prefix}__OVERRIDES__BASE_URL={LOCAL_BASE_URL}" in out

    def test_no_api_key_env_lines_when_unset(self, monkeypatch):
        monkeypatch.setattr(he, "SECRETS_PATH", _NO_SECRETS)
        out = he.render_env(_honcho_cfg())
        assert "API_KEY_ENV" not in out


class TestRenderEnvCloudOverride:
    def test_dialectic_cloud_override_leaves_other_features_local(self, monkeypatch):
        monkeypatch.setattr(he, "SECRETS_PATH", _NO_SECRETS)
        cfg = _honcho_cfg(
            llm={
                "dialectic": {
                    "transport": "openai",
                    "model": "x",
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_key_env": "OPENROUTER_API_KEY",
                }
            }
        )
        out = he.render_env(cfg)

        assert "DIALECTIC_LEVELS__minimal__MODEL_CONFIG__MODEL=x" in out
        assert (
            "DIALECTIC_LEVELS__minimal__MODEL_CONFIG__OVERRIDES__BASE_URL="
            "https://openrouter.ai/api/v1" in out
        )
        assert (
            "DIALECTIC_LEVELS__max__MODEL_CONFIG__OVERRIDES__API_KEY_ENV=OPENROUTER_API_KEY"
            in out
        )
        # Other features unaffected — still local.
        assert "DERIVER_MODEL_CONFIG__MODEL=hal0/utility" in out
        assert f"DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL={LOCAL_BASE_URL}" in out
        assert "DERIVER_MODEL_CONFIG__OVERRIDES__API_KEY_ENV" not in out


class TestAuthEnabled:
    def test_auth_enabled_flips_flag_and_notes_secret(self, monkeypatch):
        monkeypatch.setattr(he, "SECRETS_PATH", _NO_SECRETS)
        out = he.render_env(_honcho_cfg(auth_enabled=True))
        assert "AUTH_USE_AUTH=true" in out
        assert "AUTH_JWT_SECRET" in out


class TestSecretsAppend:
    def test_secrets_file_appended_verbatim_when_present(self, monkeypatch, tmp_path: Path):
        secrets = tmp_path / "honcho.env"
        secrets.write_text("OPENROUTER_API_KEY=sk-test-123\n", encoding="utf-8")

        monkeypatch.setattr(he, "SECRETS_PATH", secrets)
        out = he.render_env(_honcho_cfg())

        assert out.endswith("OPENROUTER_API_KEY=sk-test-123\n")
        assert "# --- operator secrets ---" in out

    def test_no_secrets_file_no_trailer(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(he, "SECRETS_PATH", tmp_path / "missing.env")
        out = he.render_env(_honcho_cfg())
        assert "operator secrets" not in out


class TestApplyHonchoEnv:
    def test_writes_atomically_and_detects_change(self, monkeypatch, tmp_path: Path):
        env_path = tmp_path / "honcho.env"
        monkeypatch.setattr(he, "HONCHO_ENV_PATH", env_path)
        monkeypatch.setattr(he, "SECRETS_PATH", tmp_path / "missing.env")

        ran: list[list[str]] = []

        def fake_run(args, **_kw):
            ran.append(list(args))

            class _Done:
                returncode = 0

            return _Done()

        monkeypatch.setattr(he.subprocess, "run", fake_run)

        result = he.apply_honcho_env(_honcho_cfg())
        assert result["written"] is True
        assert result["changed"] is True
        assert result["restarted"] is True
        assert result["error"] is None
        assert env_path.exists()
        assert ran == [["systemctl", "restart", "hal0-honcho"]]

        # Re-applying identical content: written again, but not restarted (unchanged).
        ran.clear()
        result2 = he.apply_honcho_env(_honcho_cfg())
        assert result2["written"] is True
        assert result2["changed"] is False
        assert result2["restarted"] is False
        assert ran == []

    def test_no_restart_skips_systemctl(self, monkeypatch, tmp_path: Path):
        env_path = tmp_path / "honcho.env"
        monkeypatch.setattr(he, "HONCHO_ENV_PATH", env_path)
        monkeypatch.setattr(he, "SECRETS_PATH", tmp_path / "missing.env")

        def boom(*_a, **_k):  # pragma: no cover — must not be called
            raise AssertionError("systemctl should not run when restart=False")

        monkeypatch.setattr(he.subprocess, "run", boom)

        result = he.apply_honcho_env(_honcho_cfg(), restart=False)
        assert result["written"] is True
        assert result["restarted"] is False
        assert result["error"] is None
        assert env_path.exists()


def test_deriver_flush_enabled_by_default(monkeypatch):
    from hal0.memory import honcho_env as he

    out = he.render_env(_honcho_cfg())
    assert "DERIVER_FLUSH_ENABLED=true" in out


def test_deriver_json_object_mode_local_only(monkeypatch):
    from hal0.memory import honcho_env as he

    out = he.render_env(_honcho_cfg())
    assert "DERIVER_MODEL_CONFIG__STRUCTURED_OUTPUT_MODE=json_object" in out

    cfg = _honcho_cfg()
    cfg.honcho.llm.deriver.base_url = "https://openrouter.ai/api/v1"
    out = he.render_env(cfg)
    assert "STRUCTURED_OUTPUT_MODE" not in out
