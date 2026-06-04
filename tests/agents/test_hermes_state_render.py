"""Unit tests for the live STATE.md render path (Hermes auto-render)."""

from __future__ import annotations

from hal0.agents import hermes_provision as hp


def _slot(name, type_, model, *, state="ready", backend=None):
    d = {"name": name, "type": type_, "model_id": model, "status": state}
    if backend:
        d["backend"] = backend
    return d


def test_collect_capability_rollup_filters_and_maps_types():
    slots = [
        _slot("primary", "llm", "qwen3-25b", backend="vulkan"),  # chat -> excluded here
        _slot("embed", "embedding", "bge-m3", backend="vulkan"),
        _slot("stt", "stt", "moonshine", backend="vulkan"),
        _slot("tts", "tts", "kokoro", backend="vulkan"),
        _slot("img", "image", "sdxl", backend="rocm"),
        _slot("rerank", "rerank", "bge-reranker", backend="vulkan"),
        _slot("cold", "embedding", "unused", state="stopped"),  # not ready -> excluded
    ]
    rollup = hp._collect_capability_rollup(slots)
    caps = {r["capability"]: r for r in rollup}
    assert set(caps) == {"embed", "voice-stt", "voice-tts", "img", "rerank"}
    assert caps["img"]["backend"] == "rocm"
    assert caps["embed"]["model_id"] == "bge-m3"
    assert "unused" not in {r.get("model_id") for r in rollup}


def test_state_template_renders_full_state():
    body = hp._render_template(
        "STATE.md.j2",
        primary={
            "alias": "primary",
            "model_id": "qwen3-25b",
            "backend_url": "http://127.0.0.1:8080/v1",
            "context_length": 32768,
            "backend": "vulkan",
        },
        capabilities=[{"capability": "embed", "model_id": "bge-m3", "backend": "vulkan"}],
        npu={"present": True, "model_id": "qwen3-it-4b-FLM"},
        igpu_sclk_mhz=2900,
        dashboard_url="https://hal0.thinmint.dev",
        lemonade_base="http://127.0.0.1:13305",
        daemon="reachable",
        as_of="2026-06-04T15:00:00+00:00",
    )
    assert "qwen3-25b" in body
    assert "32768" in body
    assert "embed" in body and "bge-m3" in body
    assert "vulkan" in body
    assert "qwen3-it-4b-FLM" in body
    assert "2900" in body
    assert "reachable" in body
    assert body.rstrip().splitlines()[-1].startswith("_as_of: 2026-06-04T15:00:00")


def test_state_template_degraded_no_primary():
    body = hp._render_template(
        "STATE.md.j2",
        primary=None,
        capabilities=[],
        npu={"present": False, "model_id": None},
        igpu_sclk_mhz=None,
        dashboard_url="https://hal0.thinmint.dev",
        lemonade_base="http://127.0.0.1:13305",
        daemon="degraded",
        as_of="2026-06-04T15:00:00+00:00",
    )
    assert "degraded" in body
    assert "no chat model loaded" in body.lower()


def test_igpu_sclk_mhz_parses_active_line_and_scans_cards(tmp_path):
    # card0 readable but no active line -> must fall through to card1.
    (tmp_path / "card0" / "device").mkdir(parents=True)
    (tmp_path / "card0" / "device" / "pp_dpm_sclk").write_text("0: 400Mhz\n1: 800Mhz\n")
    (tmp_path / "card1" / "device").mkdir(parents=True)
    (tmp_path / "card1" / "device" / "pp_dpm_sclk").write_text("0: 800Mhz\n2: 2900Mhz *\n")
    assert hp._igpu_sclk_mhz(sysfs_root=tmp_path) == 2900


def test_igpu_sclk_mhz_returns_none_when_absent(tmp_path):
    assert hp._igpu_sclk_mhz(sysfs_root=tmp_path) is None
