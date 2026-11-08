"""xdna runner argv: fixed NPU launch geometry (xdna-runner plan §1)."""

from __future__ import annotations

from hal0.providers.container import _llama_argv_segments
from hal0.slots.argv import resolve_argv


def _resolved(**kwargs):
    segments = _llama_argv_segments(**kwargs)
    return resolve_argv(segments).argv


def test_xdna_runner_emits_fixed_npu_flags() -> None:
    argv = _resolved(
        port=8123,
        model_path="/models/foo.gguf",
        context_size=8192,
        runner_key="xdna",
    )
    tail = argv[argv.index("--model") :]
    assert "--device" in tail and tail[tail.index("--device") + 1] == "XDNA0"
    assert "-ngl" in tail and tail[tail.index("-ngl") + 1] == "99"
    assert "-fa" in tail and tail[tail.index("-fa") + 1] == "on"
    assert "--poll" in tail and tail[tail.index("--poll") + 1] == "0"
    assert "-np" in tail and tail[tail.index("-np") + 1] == "1"
    assert "--cache-reuse" in tail and tail[tail.index("--cache-reuse") + 1] == "256"
    # Base/port/ctx args still present.
    assert "--port" in argv and "--ctx-size" in argv


def test_non_xdna_runner_gets_no_fixed_npu_flags() -> None:
    argv = _resolved(
        port=8123,
        model_path="/models/foo.gguf",
        context_size=8192,
        runner_key="cuda",
    )
    assert "XDNA0" not in argv
    assert "--cache-reuse" not in argv


def test_xdna_slot_reasoning_can_still_override_after_fixed_flags() -> None:
    """``slot_reasoning`` lands after the xdna fixed tokens (last-wins)."""
    argv = _resolved(
        port=8123,
        model_path="/models/foo.gguf",
        context_size=8192,
        runner_key="xdna",
        slot_reasoning=["-np", "2"],
    )
    tail = argv[argv.index("--model") :]
    assert tail[tail.index("-np") + 1] == "2"
