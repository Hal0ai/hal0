"""test_benchmarks_routes.py — GET /api/benchmarks/runs row shape.

The dashboard's Benchmarks accordion plots decode AND prefill trend lines
from this run-list row (RunSummary on the frontend) — it must carry
``prefill_ts_med`` alongside ``decode_ts_med``, sourced from the same
``record["summary"]`` the way decode already is. Missing prefill here
silently dropped the accordion's prefill series (#benchmarks lane-color
follow-up).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hal0.bench.store import Store


def _ok_rec(run_id: str, model_id: str, decode: float, prefill: float | None) -> dict:
    return {
        "run_id": run_id,
        "cell_key": f"{model_id}|rocm|tg|2048|default",
        "suite": "roster",
        "trigger": "manual",
        "config": "default",
        "identity": {
            "model": {"id": model_id},
            "lane": "rocm",
            "workload": {"kind": "tg", "depth": 2048},
        },
        "host": {"hal0_version": "1.0.0-rc.3"},
        "outcome": "ok",
        "summary": {"decode_ts_med": decode, "prefill_ts_med": prefill},
        "reps": [{"decode_ts": decode}],
    }


def test_run_summary_row_includes_prefill_ts_med(isolated_client: TestClient) -> None:
    store = Store()
    store.append_record(_ok_rec("2026-08-07T09:00:00Z-abc123", "qwen3.6-35b-a3b", 71.4, 812.3))

    resp = isolated_client.get("/api/benchmarks/runs", params={"model": "qwen3.6-35b-a3b"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    row = body["runs"][0]
    assert row["decode_ts_med"] == 71.4
    # The field this test guards — must mirror decode_ts_med's sourcing
    # (record["summary"]["prefill_ts_med"]), not be silently dropped.
    assert row["prefill_ts_med"] == 812.3


def test_run_summary_row_prefill_ts_med_is_none_when_absent(isolated_client: TestClient) -> None:
    """A record with no prefill measurement (e.g. a pp-only or failed sweep)
    must serialize prefill_ts_med as null, not omit the key or raise."""
    store = Store()
    store.append_record(_ok_rec("2026-08-07T09:05:00Z-def456", "qwen3.6-35b-a3b", 70.0, None))

    resp = isolated_client.get("/api/benchmarks/runs", params={"model": "qwen3.6-35b-a3b"})
    assert resp.status_code == 200
    row = resp.json()["runs"][0]
    assert row["prefill_ts_med"] is None


def _model_rec(run_id: str, model_id: str, gguf: str, decode: float) -> dict:
    rec = _ok_rec(run_id, model_id, decode, None)
    rec["cell_key"] = f"{model_id}|{gguf}|rocm|tg|2048|default"
    rec["identity"]["model"] = {"id": model_id, "gguf": gguf}
    return rec


def test_roster_attributes_runs_and_registry_per_file_when_basenames_collide(
    isolated_client: TestClient, monkeypatch
) -> None:
    """#1825: per-model directories store every pull as ``<dir>/model.gguf``.
    The roster must give each its own row, its own run count, its own registry
    name — and must not hide an installed-but-unmeasured model behind a
    measured one that merely shares the basename."""
    from hal0.api.routes import benchmarks as routes

    store = Store()
    store.append_record(
        _model_rec("2026-10-01T00:00:00Z-a1", "grug", "/m/Grug-12B/model.gguf", 40.0)
    )
    store.append_record(
        _model_rec("2026-10-02T00:00:00Z-a2", "grug", "/m/Grug-12B/model.gguf", 41.0)
    )
    store.append_record(
        _model_rec("2026-10-03T00:00:00Z-b1", "minicpm", "/m/MiniCPM5-1B/model.gguf", 90.0)
    )

    registry = [
        {"id": "grug", "name": "Grug 12B", "path": "/m/Grug-12B/model.gguf", "installed": True},
        {
            "id": "minicpm",
            "name": "MiniCPM5 1B",
            "path": "/m/MiniCPM5-1B/model.gguf",
            "installed": True,
        },
        {
            "id": "vibe",
            "name": "VibeThinker 3B",
            "path": "/m/VibeThinker-3B/model.gguf",
            "installed": True,
        },
    ]
    monkeypatch.setattr(routes, "fetch_registry_models", lambda api: registry)
    monkeypatch.setattr(routes, "_is_tier_a_incompatible", lambda m: False)
    monkeypatch.setattr(routes, "_model_caps", lambda m: {"chat"})

    resp = isolated_client.get("/api/benchmarks/roster")
    assert resp.status_code == 200
    by_id = {m["id"]: m for m in resp.json()["models"]}
    assert set(by_id) == {"grug", "minicpm", "vibe"}
    assert (by_id["grug"]["runs"], by_id["grug"]["name"]) == (2, "Grug 12B")
    assert by_id["grug"]["last_run"] == "2026-10-02"
    assert (by_id["minicpm"]["runs"], by_id["minicpm"]["name"]) == (1, "MiniCPM5 1B")
    assert by_id["vibe"]["measured"] is False


def test_roster_v1_path_id_still_matches_registry_by_unique_basename(
    isolated_client: TestClient, monkeypatch
) -> None:
    """The basename fallback the v1 path-like ids rely on keeps working where
    the basename is unambiguous: no duplicate unmeasured row, registry name
    attached, runs counted."""
    from hal0.api.routes import benchmarks as routes

    store = Store()
    store.append_record(
        _model_rec(
            "2026-10-01T00:00:00Z-v1",
            "chat/Qwen3-8B-Q4_K_M.gguf",
            "chat/Qwen3-8B-Q4_K_M.gguf",
            30.0,
        )
    )
    registry = [
        {
            "id": "qwen3-8b",
            "name": "Qwen3 8B",
            "path": "/m/chat/Qwen3-8B-Q4_K_M.gguf",
            "installed": True,
        },
    ]
    monkeypatch.setattr(routes, "fetch_registry_models", lambda api: registry)
    monkeypatch.setattr(routes, "_is_tier_a_incompatible", lambda m: False)
    monkeypatch.setattr(routes, "_model_caps", lambda m: {"chat"})

    models = isolated_client.get("/api/benchmarks/roster").json()["models"]
    assert [m["id"] for m in models] == ["chat/Qwen3-8B-Q4_K_M.gguf"]
    assert (models[0]["name"], models[0]["runs"]) == ("Qwen3 8B", 1)


def _stub_registry(monkeypatch, registry: list[dict]) -> None:
    from hal0.api.routes import benchmarks as routes

    monkeypatch.setattr(routes, "fetch_registry_models", lambda api: registry)
    monkeypatch.setattr(routes, "_is_tier_a_incompatible", lambda m: False)
    monkeypatch.setattr(routes, "_model_caps", lambda m: {"chat"})


def test_roster_relative_v1_path_joins_its_absolute_registry_path_under_a_collision(
    isolated_client: TestClient, monkeypatch
) -> None:
    """A legacy relative ``A/model.gguf`` names /m/A/model.gguf even though the
    basename is shared with /m/B/model.gguf: it gets A's metadata, and A is not
    re-added as an unmeasured row."""
    store = Store()
    store.append_record(_model_rec("2026-10-01T00:00:00Z-v1", "A/model.gguf", "A/model.gguf", 30.0))
    _stub_registry(
        monkeypatch,
        [
            {"id": "a", "name": "Model A", "path": "/m/A/model.gguf", "installed": True},
            {"id": "b", "name": "Model B", "path": "/m/B/model.gguf", "installed": True},
        ],
    )

    by_id = {m["id"]: m for m in isolated_client.get("/api/benchmarks/roster").json()["models"]}
    assert set(by_id) == {"A/model.gguf", "b"}
    assert by_id["A/model.gguf"]["name"] == "Model A"
    assert by_id["b"]["measured"] is False


def test_roster_basename_fallback_requires_a_unique_basename_in_the_store(
    isolated_client: TestClient, monkeypatch
) -> None:
    """B has left the registry; A is still there. B's row must not borrow A's
    name/hf_repo just because ``model.gguf`` is now unique in the registry."""
    store = Store()
    store.append_record(_model_rec("2026-10-01T00:00:00Z-a", "a", "/m/A/model.gguf", 30.0))
    store.append_record(_model_rec("2026-10-01T00:00:01Z-b", "b", "/m/B/model.gguf", 40.0))
    _stub_registry(
        monkeypatch,
        [
            {
                "id": "a",
                "name": "Model A",
                "hf_repo": "org/a",
                "path": "/m/A/model.gguf",
                "installed": True,
            }
        ],
    )

    by_id = {m["id"]: m for m in isolated_client.get("/api/benchmarks/roster").json()["models"]}
    assert set(by_id) == {"a", "b"}
    assert (by_id["a"]["name"], by_id["a"]["hf_repo"]) == ("Model A", "org/a")
    assert (by_id["b"]["name"], by_id["b"]["hf_repo"]) == (None, None)


def test_queue_view_lists_items_the_worker_could_not_run(isolated_client: TestClient) -> None:
    """#2387: a queued run that resolved to no model is surfaced on the queue
    view with its failure outcome instead of vanishing."""
    from hal0.bench import control

    control.enqueue({"id": "u1", "label": "nope.gguf", "model": "nope.gguf"})
    control.fail("u1", "unknown model 'nope.gguf'", "2026-10-08T00:00:00Z")

    resp = isolated_client.get("/api/benchmarks/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert [(f["id"], f["outcome"], f["note"]) for f in body["failed"]] == [
        ("u1", "failed", "unknown model 'nope.gguf'")
    ]
