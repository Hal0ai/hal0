"""#1199: curated ComfyUI model-set orchestration.

The orchestration loop is exercised with an injected ``runner`` so no real
subprocess/download runs — we assert sequencing, per-step exit logging, the
optional-vs-required failure policy, and the operator log contents.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.comfyui.orchestrate import (
    OPTIONAL_FAMILIES,
    curated_set,
    orchestrate_models,
)


def _noop_workflow(_variant):
    return None


def _all_ok_runner(cmd, env, fh):
    fh.write(f"[fake] ran {' '.join(cmd)}\n")
    return 0


def _make_runner(fail_families):
    """Runner that returns exit=1 whenever the invoked script matches a family."""

    def runner(cmd, env, fh):
        script = cmd[1]  # ["bash", <script_path>, *args]
        for fam_script in fail_families:
            if fam_script in script:
                return 1
        return 0

    return runner


def test_curated_set_covers_all_capabilities():
    pairs = curated_set()
    cap_ids = [c for c, _ in pairs]
    assert cap_ids == ["txt2img", "img2img", "txt2video", "img2video", "image_upscale"]
    # esrgan is the curated upscale default and must be flagged optional.
    assert any(v.family in OPTIONAL_FAMILIES for _, v in pairs)


def test_all_success(tmp_path):
    log = tmp_path / "run.log"
    result = orchestrate_models(
        provision_workflow=_noop_workflow,
        runner=_all_ok_runner,
        log_path=log,
        env_fn=lambda: {},
    )
    assert result.ok
    assert not result.failed_required
    assert not result.failed_optional
    assert result.landed  # every family landed
    assert result.log_path == str(log)

    text = log.read_text()
    assert "curated ComfyUI model set" in text
    assert "summary:" in text
    assert f"log written to: {log}" in text


def test_optional_failure_tolerated(tmp_path):
    log = tmp_path / "run.log"
    result = orchestrate_models(
        provision_workflow=_noop_workflow,
        runner=_make_runner({"get_esrgan.sh"}),
        log_path=log,
        env_fn=lambda: {},
    )
    # Optional (esrgan) failure must NOT fail the overall run.
    assert result.ok
    assert "esrgan" in result.failed_optional
    assert "esrgan" not in result.failed_required
    assert "[optional — skipped]" in log.read_text()


def test_required_failure_fails_run_but_continues(tmp_path):
    log = tmp_path / "run.log"
    # Fail the SDXL/Qwen txt2img family (required); later families must still run.
    pairs = curated_set()
    first_script = pairs[0][1].fetch_script
    result = orchestrate_models(
        pairs,
        provision_workflow=_noop_workflow,
        runner=_make_runner({first_script}),
        log_path=log,
        env_fn=lambda: {},
    )
    assert not result.ok
    assert result.failed_required
    # Unrelated families still attempted: esrgan (last) recorded a result.
    families_run = {r.family for r in result.results}
    assert "esrgan" in families_run


def test_default_log_path_used_when_unspecified(tmp_path, monkeypatch):
    # Point the model store at tmp so the default <store>/comfyui/logs path lands
    # under tmp rather than the real appliance path.
    import hal0.comfyui.orchestrate as orch

    monkeypatch.setattr("hal0.config.paths.model_store_root", lambda: str(tmp_path), raising=False)
    result = orch.orchestrate_models(
        provision_workflow=_noop_workflow,
        runner=_all_ok_runner,
        env_fn=lambda: {},
        clock=lambda: 0.0,
    )
    assert result.log_path is not None
    assert Path(result.log_path).exists()
    assert "comfyui/logs" in result.log_path


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-x", "-q"])
