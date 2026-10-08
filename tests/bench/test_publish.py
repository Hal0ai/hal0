"""test_publish.py — build_roster's one-row-per-physical-model collapse (#1825).

Models pulled into a per-model directory are all stored as ``<dir>/model.gguf``,
so the gguf BASENAME is not an identity: grouping on it folded unrelated models
into one roster row. The collapse must still fold the case it was written for —
one file carrying a clean registry id AND a v1 path-like id.
"""

from __future__ import annotations

from typing import Any

from hal0.bench.publish import build_roster, physical_model_keyer
from hal0.bench.store import Store


def _rec(run_id: str, model_id: str, gguf: str, decode: float, kind: str = "tg") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "cell_key": f"{model_id}|{gguf}|rocm|{kind}|2048|default",
        "suite": "roster",
        "trigger": "manual",
        "config": "default",
        "identity": {
            "model": {"id": model_id, "gguf": gguf},
            "lane": "rocm",
            "workload": {"kind": kind, "depth": 2048},
        },
        "host": {"hal0_version": "1.4.0"},
        "outcome": "ok",
        "summary": {"decode_ts_med": decode, "prefill_ts_med": decode * 10},
        "reps": [{"decode_ts": decode}],
    }


def test_distinct_models_sharing_basename_model_gguf_each_get_a_row(tmp_path) -> None:
    store = Store(tmp_path)
    store.append_record(
        _rec("2026-10-01T00:00:00Z-a", "Grug-12B", "/m/chat/Grug-12B/model.gguf", 40.0)
    )
    store.append_record(
        _rec("2026-10-02T00:00:00Z-b", "MiniCPM5-1B", "/m/chat/MiniCPM5-1B/model.gguf", 90.0)
    )
    store.append_record(
        _rec("2026-10-03T00:00:00Z-c", "model", "/m/chat/VibeThinker-3B/model.gguf", 60.0)
    )

    roster = build_roster(store, host={})

    by_id = {m["id"]: m for m in roster["models"]}
    assert set(by_id) == {"Grug-12B", "MiniCPM5-1B", "model"}
    assert by_id["Grug-12B"]["gguf"] == "/m/chat/Grug-12B/model.gguf"
    assert by_id["Grug-12B"]["decode_ts"] == 40.0
    assert by_id["MiniCPM5-1B"]["decode_ts"] == 90.0
    # prefill is folded per physical model too, not across the collision
    assert by_id["Grug-12B"]["prefill_ts"] is None


def test_prefill_folds_into_the_right_colliding_model(tmp_path) -> None:
    store = Store(tmp_path)
    store.append_record(_rec("2026-10-01T00:00:00Z-a", "A", "/m/A/model.gguf", 40.0))
    store.append_record(_rec("2026-10-01T00:00:01Z-b", "B", "/m/B/model.gguf", 50.0))
    pp = _rec("2026-10-02T00:00:00Z-c", "B", "/m/B/model.gguf", 50.0, kind="pp")
    store.append_record(pp)

    by_id = {m["id"]: m for m in build_roster(store, host={})["models"]}
    assert by_id["A"]["prefill_ts"] is None
    assert by_id["B"]["prefill_ts"] == 500.0


def test_same_file_under_clean_id_and_v1_path_id_still_collapses(tmp_path) -> None:
    store = Store(tmp_path)
    gguf = "/m/chat/Qwen3-8B/Qwen3-8B-Q4_K_M.gguf"
    store.append_record(_rec("2026-09-01T00:00:00Z-a", "chat/Qwen3-8B-Q4_K_M.gguf", gguf, 30.0))
    store.append_record(_rec("2026-10-01T00:00:00Z-b", "qwen3-8b", gguf, 31.0))

    models = build_roster(store, host={})["models"]
    assert [m["id"] for m in models] == ["qwen3-8b"]  # newest tg wins the row


def test_keyer_keeps_basename_for_unique_files_and_paths_for_collisions() -> None:
    key = physical_model_keyer(
        [
            {"id": "x", "gguf": "/m/x/Unique.gguf"},
            {"id": "a", "gguf": "/m/a/model.gguf"},
            {"id": "b", "gguf": "/m/b/model.gguf"},
            {"id": "a-v1", "gguf": "/m/a/model.gguf"},
        ]
    )
    assert key({"id": "x", "gguf": "/m/x/Unique.gguf"}) == "Unique.gguf"
    assert key({"id": "a", "gguf": "/m/a/model.gguf"}) == "/m/a/model.gguf"
    assert key({"id": "a-v1", "gguf": "/m/a/model.gguf"}) == "/m/a/model.gguf"
    assert key({"id": "b", "gguf": "/m/b/model.gguf"}) == "/m/b/model.gguf"
    assert key({"id": "no-file"}) == "no-file"
