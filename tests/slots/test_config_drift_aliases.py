"""Config-drift comparison is flag-alias aware.

``_argv_values`` used to compare raw argv tokens, so a running container
started with ``--batch-size 2048`` read as drifted against a rendered
``-b 2048`` (and vice versa) even though llama-server treats the two
spellings identically. Both sides are now canonicalized through
``hal0.slots.argv.FLAG_ALIASES`` before comparison.
"""

from __future__ import annotations

from pathlib import Path

from hal0.slots.manager import _CONFIG_DRIFT_KEYS, SlotManager, _argv_values
from tests.slots.conftest import FakeContainerProvider


def test_argv_values_matches_long_spelling_for_short_key() -> None:
    """``-b``/``-ub`` keys pick up ``--batch-size``/``--ubatch-size`` tokens."""
    argv = ["--batch-size", "2048", "--ubatch-size", "512", "--ctx-size", "8192"]
    out = _argv_values(argv, _CONFIG_DRIFT_KEYS)
    assert out["-b"] == "2048"
    assert out["-ub"] == "512"
    assert out["--ctx-size"] == "8192"


def test_argv_values_matches_short_spelling_for_long_key() -> None:
    """A ``--ctx-size`` key matches the ``-c`` short alias (and inline =)."""
    assert _argv_values(["-c", "4096"], ("--ctx-size",)) == {"--ctx-size": "4096"}
    assert _argv_values(["--ctx-size=4096"], ("--ctx-size",)) == {"--ctx-size": "4096"}
    assert _argv_values(["--batch-size=64"], ("-b",)) == {"-b": "64"}


def test_argv_values_last_value_wins_across_spellings() -> None:
    """extra_args overriding the profile with the other spelling still wins."""
    argv = ["-b", "512", "--batch-size", "2048"]
    assert _argv_values(argv, ("-b",)) == {"-b": "2048"}


async def test_no_false_drift_between_alias_spellings(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """Running ``--batch-size`` vs rendered ``-b`` must NOT report drift."""
    container_stub.expected_argv_by_slot["chat"] = [
        "--model",
        "/mnt/ai-models/qwen.gguf",
        "--alias",
        "qwen3-4b-q4_k_m",
        "--ctx-size",
        "131072",
        "-b",
        "2048",
        "-ub",
        "512",
    ]
    container_stub.running_argv_by_slot["chat"] = [
        "--model",
        "/mnt/ai-models/qwen.gguf",
        "--alias",
        "qwen3-4b-q4_k_m",
        "-c",
        "131072",
        "--batch-size",
        "2048",
        "--ubatch-size",
        "512",
    ]

    sm = SlotManager()
    await sm.load("chat")
    snap = await sm.status("chat", include_config_drift=True)

    assert snap.metadata.get("config_drift") == {"drifted": False, "diffs": []}


async def test_real_drift_still_detected_across_spellings(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A real value difference is still flagged even with mixed spellings."""
    container_stub.expected_argv_by_slot["chat"] = ["-b", "2048"]
    container_stub.running_argv_by_slot["chat"] = ["--batch-size", "512"]

    sm = SlotManager()
    await sm.load("chat")
    snap = await sm.status("chat", include_config_drift=True)

    drift = snap.metadata.get("config_drift")
    assert drift is not None and drift["drifted"] is True
    assert {"key": "-b", "running": "512", "rendered": "2048"} in drift["diffs"]
