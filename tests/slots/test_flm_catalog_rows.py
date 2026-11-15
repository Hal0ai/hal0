"""The dashboard's FLM catalogue rows carry the download size (``size_bytes``)."""

from __future__ import annotations

from hal0.slots import flm_catalog


def test_rows_carry_size_bytes_and_default_to_zero() -> None:
    rows = flm_catalog._model_rows(
        [
            {
                "tag": "qwen3.5:9b",
                "installed": False,
                "capabilities": ["chat"],
                "family": "qwen3.5",
                "size_bytes": 6_400_000_000,
            },
            {"tag": "x:1b", "installed": True},
        ]
    )
    assert rows[0] == {
        "model": "qwen3.5:9b",
        "installed": False,
        "capabilities": ["chat"],
        "family": "qwen3.5",
        "size_bytes": 6_400_000_000,
    }
    assert rows[1]["size_bytes"] == 0
