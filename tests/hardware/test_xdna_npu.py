"""xdna NPU vs CPU correctness gate: top-1 agreement across prompt lengths.

Ports the spike's bisection logic (`/var/lib/hal0/work/xdna-spike/xdna_bisect.py`)
into a pytest test: greedy-decode the same prompt, truncated to nine lengths,
against two already-running llama-servers (one on the xdna NPU runner, one on
CPU) and assert the NPU's top-1 token matches the CPU reference at every
length. This is the test that would have caught the `GGML_XDNA_PGEMM_GLU_A=1`
correctness bug the spike found (see the ggml-xdna ADR and
docs/superpowers/plans/xdna-runner.md).

SKIPS by default: it needs a real XDNA2 NPU and two running servers, neither
of which exist in CI. Set ``HAL0_XDNA_HW=1``, point ``HAL0_XDNA_SERVER_URL``
at the NPU server and ``HAL0_XDNA_CPU_URL`` at the CPU reference server
(``--device none``), and have ``/dev/accel/accel0`` present, to run it.
"""

from __future__ import annotations

import os

import pytest

#: Prompt lengths (in tokens) the spike bisected at. Chosen to straddle the
#: kernel's tile boundaries; kept identical so results are comparable.
PROMPT_LENGTHS = (2, 4, 8, 16, 31, 32, 40, 64, 96)

_TEXT = (
    "The neural processing unit in the Ryzen AI Max is an XDNA2 array of compute tiles "
    "arranged in columns, each with local memory, connected by a streaming interconnect. "
    "Developers program it through XRT by loading an xclbin and submitting instruction "
    "streams, while the host CPU handles everything the array cannot run efficiently. "
) * 2

_HW_ENABLED = os.environ.get("HAL0_XDNA_HW") == "1"
_ACCEL_DEVICE = "/dev/accel/accel0"
_NPU_URL = os.environ.get("HAL0_XDNA_SERVER_URL")
_CPU_URL = os.environ.get("HAL0_XDNA_CPU_URL")

_SKIP_REASON = (
    "set HAL0_XDNA_HW=1, HAL0_XDNA_SERVER_URL and HAL0_XDNA_CPU_URL, with "
    f"{_ACCEL_DEVICE} present, to run the NPU-vs-CPU correctness gate"
)


def _skip_reason() -> str | None:
    if not _HW_ENABLED:
        return _SKIP_REASON
    if not os.path.exists(_ACCEL_DEVICE):
        return f"{_ACCEL_DEVICE} not present"
    if not _NPU_URL:
        return "set HAL0_XDNA_SERVER_URL"
    if not _CPU_URL:
        return "set HAL0_XDNA_CPU_URL"
    return None


def _tokenize(base_url: str, text: str) -> list[int]:
    import httpx

    r = httpx.post(f"{base_url.rstrip('/')}/tokenize", json={"content": text}, timeout=60)
    r.raise_for_status()
    return r.json()["tokens"]


#: Largest CPU top-1/top-2 logprob gap (nats) still counted as a near-tie. When
#: the reference itself can barely separate its two best tokens, ordinary
#: NPU-vs-CPU numeric noise may swap them; the GLU_A=1 corruption this gate
#: exists for moves logprobs by whole nats, far beyond this.
NEAR_TIE_NATS = 0.25


def _top2(base_url: str, tokens: list[int]) -> list[tuple[str, float]]:
    """Greedy top-2 tokens + their logprobs for a truncated token prompt.

    Uses ``/completion`` directly (the llama.cpp native endpoint, not the
    OpenAI-shaped ``/v1/completions``) with ``n_probs=2`` so the server
    returns the two best tokens' logprobs, the call the spike's bisection
    script made, widened by one so near-ties can be told apart from errors.
    """
    import httpx

    r = httpx.post(
        f"{base_url.rstrip('/')}/completion",
        json={
            "prompt": tokens,
            "n_predict": 1,
            "n_probs": 2,
            "temperature": 0,
            "cache_prompt": False,
        },
        timeout=180,
    )
    r.raise_for_status()
    top = r.json()["completion_probabilities"][0]["top_logprobs"]
    return [(t["token"], t["logprob"]) for t in top]


@pytest.mark.network
@pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")
def test_xdna_npu_top1_agrees_with_cpu_reference() -> None:
    cpu_tokens = _tokenize(_CPU_URL, _TEXT)  # type: ignore[arg-type]
    npu_tokens = _tokenize(_NPU_URL, _TEXT)  # type: ignore[arg-type]
    assert cpu_tokens == npu_tokens, "tokenizer disagreement before any inference ran"

    mismatches: list[str] = []
    for n in PROMPT_LENGTHS:
        prompt = cpu_tokens[:n]
        cpu_top = _top2(_CPU_URL, prompt)  # type: ignore[arg-type]
        npu_top = _top2(_NPU_URL, prompt)  # type: ignore[arg-type]
        (cpu_tok, cpu_lp), (npu_tok, npu_lp) = cpu_top[0], npu_top[0]
        if cpu_tok != npu_tok:
            cpu_margin = cpu_lp - cpu_top[1][1] if len(cpu_top) > 1 else float("inf")
            swapped_near_tie = (
                len(cpu_top) > 1 and npu_tok == cpu_top[1][0] and cpu_margin < NEAR_TIE_NATS
            )
            if not swapped_near_tie:
                mismatches.append(
                    f"len={n}: cpu top1={cpu_tok} (lp={cpu_lp:.2f}, margin={cpu_margin:.2f}) "
                    f"!= npu top1={npu_tok} (lp={npu_lp:.2f})"
                )
        else:
            delta = abs(cpu_lp - npu_lp)
            assert delta < 1.0, f"len={n}: top1 agrees but |Δlogprob|={delta:.3f} is too large"

    assert not mismatches, "NPU/CPU top-1 disagreement:\n" + "\n".join(mismatches)
