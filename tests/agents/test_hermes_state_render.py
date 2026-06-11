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


def test_state_body_minus_timestamp_ignores_as_of_line():
    a = "# Live system state\n- Chat model: x\n\n_as_of: 2026-06-04T10:00:00+00:00_\n"
    b = "# Live system state\n- Chat model: x\n\n_as_of: 2026-06-04T22:00:00+00:00_\n"
    assert hp._state_body_minus_timestamp(a) == hp._state_body_minus_timestamp(b)


def test_render_live_context_writes_then_skips_when_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "ETC_HAL0_DIR", tmp_path)
    monkeypatch.setattr(hp, "RUNTIME_SNAPSHOT_DIR", tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    slots = [
        {
            "name": "primary",
            "type": "llm",
            "model_id": "qwen3-25b",
            "status": "ready",
            "backend": "vulkan",
        },
        {
            "name": "embed",
            "type": "embedding",
            "model_id": "bge-m3",
            "status": "ready",
            "backend": "vulkan",
        },
    ]
    monkeypatch.setattr(hp, "_fetch_model_contexts", lambda: {"primary": 32768})

    r1 = hp.render_live_context(
        hermes_home=home,
        slots_fetcher=lambda: slots,
        now_iso="2026-06-04T10:00:00+00:00",
    )
    assert r1["state_written"] is True
    assert r1["degraded"] is False
    state = (tmp_path / "STATE.md").read_text()
    assert "qwen3-25b" in state and "bge-m3" in state

    # Same substantive state, different clock-time -> NOT rewritten.
    r2 = hp.render_live_context(
        hermes_home=home,
        slots_fetcher=lambda: slots,
        now_iso="2026-06-04T22:00:00+00:00",
    )
    assert r2["state_written"] is False
    assert "10:00:00" in (tmp_path / "STATE.md").read_text()  # as_of unchanged


def test_render_live_context_degraded_when_daemon_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "ETC_HAL0_DIR", tmp_path)
    monkeypatch.setattr(hp, "RUNTIME_SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(hp, "_http_get", lambda *a, **k: 0)  # daemon down
    home = tmp_path / "home"
    home.mkdir()
    r = hp.render_live_context(
        hermes_home=home,
        slots_fetcher=lambda: [],
        now_iso="2026-06-04T10:00:00+00:00",
    )
    assert r["degraded"] is True
    # First boot (no prior STATE.md) -> a degraded placeholder is written.
    assert "degraded" in (tmp_path / "STATE.md").read_text()


def test_render_live_context_preserves_good_state_when_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "ETC_HAL0_DIR", tmp_path)
    monkeypatch.setattr(hp, "RUNTIME_SNAPSHOT_DIR", tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    good = "# Live system state\n- Chat model: `qwen3-25b` (32768 ctx, vulkan)\n\n_as_of: 2026-06-04T09:00:00+00:00_\n"
    (tmp_path / "STATE.md").write_text(good)
    monkeypatch.setattr(hp, "_http_get", lambda *a, **k: 0)  # daemon down
    r = hp.render_live_context(
        hermes_home=home,
        slots_fetcher=lambda: [],
        now_iso="2026-06-04T10:00:00+00:00",
    )
    assert r["degraded"] is True
    assert r["state_written"] is False
    # Last-good snapshot left intact — NOT clobbered with a degraded body.
    assert (tmp_path / "STATE.md").read_text() == good


def test_render_live_context_reachable_empty_is_not_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "ETC_HAL0_DIR", tmp_path)
    monkeypatch.setattr(hp, "RUNTIME_SNAPSHOT_DIR", tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(hp, "_http_get", lambda *a, **k: 200)  # daemon up, no slots
    r = hp.render_live_context(
        hermes_home=home,
        slots_fetcher=lambda: [],
        now_iso="2026-06-04T10:00:00+00:00",
    )
    assert r["degraded"] is False
    body = (tmp_path / "STATE.md").read_text()
    assert "reachable" in body
    assert "no chat model loaded" in body.lower()


def test_render_live_context_bumps_mtime_when_unchanged_reachable(tmp_path, monkeypatch):
    import os as _os

    monkeypatch.setattr(hp, "ETC_HAL0_DIR", tmp_path)
    monkeypatch.setattr(hp, "RUNTIME_SNAPSHOT_DIR", tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    slots = [
        {
            "name": "primary",
            "type": "llm",
            "model_id": "qwen3-25b",
            "status": "ready",
            "backend": "vulkan",
        }
    ]
    monkeypatch.setattr(hp, "_fetch_model_contexts", lambda: {"primary": 32768})

    r1 = hp.render_live_context(
        hermes_home=home, slots_fetcher=lambda: slots, now_iso="2026-06-04T10:00:00+00:00"
    )
    assert r1["state_written"] is True
    state_path = tmp_path / "STATE.md"
    # Backdate mtime to simulate a stable system past the hook TTL.
    old = state_path.stat().st_mtime - 10_000
    _os.utime(state_path, (old, old))

    r2 = hp.render_live_context(
        hermes_home=home, slots_fetcher=lambda: slots, now_iso="2026-06-04T22:00:00+00:00"
    )
    assert r2["state_written"] is False  # content unchanged
    assert state_path.stat().st_mtime > old  # but mtime bumped -> TTL settles
    assert "09:00" not in state_path.read_text()  # sanity: content (as_of) NOT rewritten
    assert "10:00:00" in state_path.read_text()  # as_of still the first render's


def test_phase_context_link_writes_state_md(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(hp, "ETC_HAL0_DIR", tmp_path)
    monkeypatch.setattr(hp, "RUNTIME_SNAPSHOT_DIR", tmp_path)
    home = tmp_path / "home"
    (home / "memories").mkdir(parents=True)
    # Seed an env snapshot so HERMES.md (env.container/env.cpu) renders, exactly
    # as the env_probe phase would before context_link in the real pipeline.
    (home / "env-1.json").write_text(
        json.dumps(
            {
                "env_report": {
                    "container": {"product_name": "hal0-test", "kind": "lxc"},
                    "cpu": {"model": "AMD Strix Halo", "logical_online": 16},
                    "npu": {"present": True},
                }
            }
        )
    )
    io = hp.PhaseIO(
        fetch_slots=lambda: [
            {
                "name": "primary",
                "type": "llm",
                "model_id": "qwen3-25b",
                "status": "ready",
                "backend": "vulkan",
            }
        ],
        fetch_model_contexts=lambda: {"primary": 32768},
    )
    monkeypatch.setattr(hp, "HAL0_BUNDLED_SKILLS", tmp_path / "nope")

    state = hp.BootstrapState(hermes_home=str(home))
    res = hp._phase_context_link(hp.context_for("context_link", state, io=io))
    assert res.status == hp.PhaseStatus.OK
    # STATE.md written with the live model.
    assert (tmp_path / "STATE.md").exists()
    assert "qwen3-25b" in (tmp_path / "STATE.md").read_text()
    # HERMES.md written, no longer carries the live snapshot heading, and
    # points at STATE.md.
    hermes_md = (tmp_path / "HERMES.md").read_text()
    assert "Live system state" not in hermes_md  # that H1 now lives in STATE.md
    assert "STATE.md" in hermes_md  # pointer present


def test_spawn_context_refresh_is_best_effort(monkeypatch):
    from hal0.agents import hermes_refresh

    calls = {}

    def fake_popen(argv, **kw):
        calls["argv"] = argv
        calls["kw"] = kw

        class _P:  # minimal stand-in
            pass

        return _P()

    monkeypatch.setattr(hermes_refresh.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(hermes_refresh.shutil, "which", lambda _n: "/usr/local/bin/hal0-agent")

    hermes_refresh.spawn_context_refresh("hermes")
    assert calls["argv"] == ["/usr/local/bin/hal0-agent", "hermes", "render-context"]

    # Never raises even if Popen blows up.
    def boom(*a, **k):
        raise OSError("no exec")

    monkeypatch.setattr(hermes_refresh.subprocess, "Popen", boom)
    hermes_refresh.spawn_context_refresh("hermes")  # must not raise
