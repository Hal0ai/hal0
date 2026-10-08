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


def test_keyer_folds_a_relative_v1_path_into_its_absolute_path() -> None:
    """A v1 record's relative gguf (``chat/Foo.gguf``) and a later absolute one
    (``/m/chat/Foo.gguf``) name the same file: the basename stays unambiguous,
    so both keep the basename key and collapse into one row."""
    key = physical_model_keyer(
        [
            {"id": "chat/Foo.gguf", "gguf": "chat/Foo.gguf"},
            {"id": "foo", "gguf": "/m/chat/Foo.gguf"},
        ]
    )
    assert key({"id": "chat/Foo.gguf", "gguf": "chat/Foo.gguf"}) == "Foo.gguf"
    assert key({"id": "foo", "gguf": "/m/chat/Foo.gguf"}) == "Foo.gguf"


def test_keyer_folds_relative_v1_paths_within_a_real_collision() -> None:
    """Under a genuine ``model.gguf`` collision, a relative v1 path still keys
    to the absolute path it is a suffix of, not to a third row."""
    key = physical_model_keyer(
        [
            {"id": "a", "gguf": "/m/a/model.gguf"},
            {"id": "a-v1", "gguf": "a/model.gguf"},
            {"id": "b", "gguf": "/m/b/model.gguf"},
        ]
    )
    assert key({"id": "a-v1", "gguf": "a/model.gguf"}) == "/m/a/model.gguf"
    assert key({"id": "a", "gguf": "/m/a/model.gguf"}) == "/m/a/model.gguf"
    assert key({"id": "b", "gguf": "/m/b/model.gguf"}) == "/m/b/model.gguf"


def test_registry_path_change_under_one_id_stays_one_row(tmp_path) -> None:
    """A registry ``update`` can move an entry from /old/model.gguf to
    /new/model.gguf and keep its id. Records from before and after are one
    model: two rows with the same ``id`` would break every id-keyed consumer
    on the dashboard (React key, cache, detail filter, queue reference)."""
    store = Store(tmp_path)
    store.append_record(_rec("2026-09-01T00:00:00Z-a", "x", "/old/x/model.gguf", 20.0))
    store.append_record(_rec("2026-10-01T00:00:00Z-b", "x", "/new/x/model.gguf", 25.0))
    store.append_record(_rec("2026-10-01T00:00:01Z-c", "y", "/m/y/model.gguf", 60.0))

    models = build_roster(store, host={})["models"]
    ids = [m["id"] for m in models]
    assert sorted(ids) == ["x", "y"]
    x = next(m for m in models if m["id"] == "x")
    assert (x["gguf"], x["decode_ts"]) == ("/new/x/model.gguf", 25.0)
