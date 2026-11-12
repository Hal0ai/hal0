"""#3492 — root-side allow-list for ``write-hindsight-showcase-dropin``.

Exercises the REAL bash wrapper through its side-effect-free ``check-dropin
showcase`` verb (the validator the write arm calls), like
``test_systemctl_dropin_validation.py``. Legitimate bodies come from the real
renderer (:func:`render_showcase_env`) so a renderer change the allow-list
would refuse fails here, not on a production host.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hal0.config.schema import (
    MemoryDecisionConfig,
    MemoryLaneOpConfig,
    MemoryLanesConfig,
    MemoryModelsConfig,
)
from hal0.memory.extraction_env import render_showcase_env

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "installer" / "wrappers" / "hal0-systemctl"


def _check(body: str, kind: str = "showcase") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WRAPPER), "check-dropin", kind],
        input=body,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )


def _accepts(body: str) -> None:
    proc = _check(body)
    assert proc.returncode == 0, f"rejected: {proc.stderr}"
    assert proc.stdout == body


def _rejects(body: str, kind: str = "showcase") -> None:
    proc = _check(body, kind)
    assert proc.returncode == 64, f"accepted (rc={proc.returncode}): {body!r}"
    assert "drop-in rejected" in proc.stderr


def _body(**over) -> str:
    return render_showcase_env(
        models=over.get("models", MemoryModelsConfig()),
        decision=over.get("decision", MemoryDecisionConfig()),
        lanes=over.get("lanes", MemoryLanesConfig()),
        # Render the decision lane so the allow-list sees its keys (#3519).
        decision_unit_installed=True,
    )


_EVERY_OP = MemoryLaneOpConfig(
    max_concurrent=2,
    virtual_model="hal0/utility",
    timeout_s=600,
    reasoning_effort="low",
    strict_schema=True,
)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_body(), id="defaults"),
        pytest.param(
            _body(decision=MemoryDecisionConfig(enabled=True, provider="local")),
            id="decision-local",
        ),
        pytest.param(
            _body(decision=MemoryDecisionConfig(enabled=True, provider="hindsight-cloud")),
            id="decision-cloud",
        ),
        pytest.param(
            _body(decision=MemoryDecisionConfig(provider="off")),
            id="decision-off",
        ),
        pytest.param(
            _body(
                lanes=MemoryLanesConfig(
                    retain=_EVERY_OP,
                    consolidation=_EVERY_OP,
                    reflect=_EVERY_OP,
                    mental_model_refresh=_EVERY_OP,
                    strict_schema=False,
                    reasoning_effort="high",
                )
            ),
            id="every-per-op-override",
        ),
    ],
)
def test_rendered_showcase_bodies_are_accepted(body: str) -> None:
    _accepts(body)


_HEAD = "[Service]\n"


@pytest.mark.parametrize(
    "line",
    [
        "ExecStartPre=/bin/sh -c id",
        "User=root",
        "EnvironmentFile=/etc/shadow",
        "Environment=LD_PRELOAD=/tmp/x.so",
        "Environment=PYTHONPATH=/tmp",
        "Environment=HINDSIGHT_API_LLM_API_KEY=sk-live",
        "Environment=HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=a b",
        "Environment=HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=../../etc",
        "Environment=HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=/abs/path",
        "Environment=HINDSIGHT_API_DECISION_MODEL_BASE_URL=http://10.0.0.5:9000",
        "Environment=HINDSIGHT_API_DECISION_MODEL_BASE_URL=https://evil.example",
        "Environment=HINDSIGHT_API_DECISION_MODEL_API_KEY=sk-real",
        "Environment=HINDSIGHT_API_DECISION_MODEL_PROVIDER=openai",
        "Environment=HINDSIGHT_API_LLM_MAX_CONCURRENT=-1",
        "Environment=HINDSIGHT_API_LLM_STRICT_SCHEMA=yes",
        "Environment=HINDSIGHT_API_LLM_REASONING_EFFORT=$(id)",
        "Environment=HINDSIGHT_API_LLM_REASONING_EFFORT=High",
        "Environment=HINDSIGHT_API_REFLECT_MAX_COMPLETION_TOKENS=-1",
        "Environment=HINDSIGHT_API_CONSOLIDATION_MAX_COMPLETION_TOKENS=8k",
        "Environment=HINDSIGHT_API_MENTAL_MODEL_REFRESH_MAX_COMPLETION_TOKENS=8192",
        "Environment=HINDSIGHT_API_RETAIN_LLM_MODEL=gpt-4o",
        "Environment=HINDSIGHT_API_RETAIN_LLM_MODEL=hal0/x HINDSIGHT_API_LLM_API_KEY=y",
    ],
)
def test_out_of_allowlist_lines_are_rejected(line: str) -> None:
    _rejects(_HEAD + line + "\n")


def test_showcase_names_are_not_accepted_by_the_extraction_kind() -> None:
    """The two kinds stay disjoint: the older extraction verb must not become
    a second way to write showcase env (or vice versa for its LLM_MODEL)."""
    _rejects(_HEAD + "Environment=HINDSIGHT_API_EMBEDDINGS_PROVIDER=local\n", kind="hindsight")
    _rejects(_HEAD + "Environment=HINDSIGHT_API_LLM_MODEL=hal0/memory\n")


def test_write_arm_validates_and_writes_the_reconstruction() -> None:
    text = WRAPPER.read_text()
    arm = text.split("  write-hindsight-showcase-dropin)", 1)[1].split("\n    ;;", 1)[0]
    assert "validate_dropin_body showcase" in arm
    assert '"$HINDSIGHT_SHOWCASE_DROPIN_FILE"' in arm
    assert "DROPIN_BODY" in arm
    assert "cat > " not in arm


def test_write_arm_retires_only_the_literal_legacy_cap_drop_in() -> None:
    """#3520: the showcase write removes the hand-maintained H0-98 drop-in,
    which sorts later and would otherwise win. The path is a literal in the
    same root-owned directory (no glob, no caller-supplied name), and the
    removal runs only after the new drop-in is in place."""
    text = WRAPPER.read_text()
    assert (
        'HINDSIGHT_LEGACY_CAP_DROPIN_FILE="${HINDSIGHT_DROPIN_DIR}/zz-h0-98-llm-global-cap.conf"'
        in text
    )
    arm = text.split("  write-hindsight-showcase-dropin)", 1)[1].split("\n    ;;", 1)[0]
    rm_line = 'rm -f -- "$HINDSIGHT_LEGACY_CAP_DROPIN_FILE"'
    assert rm_line in arm
    assert arm.index('mv -f "$tmp" "$HINDSIGHT_SHOWCASE_DROPIN_FILE"') < arm.index(rm_line)
    # No other arm deletes it, and the arm takes no positional argument.
    assert text.count("$HINDSIGHT_LEGACY_CAP_DROPIN_FILE") == 1
    assert "$1" not in arm and "${1" not in arm


def test_default_body_carries_the_completion_caps_and_is_accepted() -> None:
    """#3596: the shipped consolidation + reflect caps pass the allow-list."""
    body = _body()
    assert "Environment=HINDSIGHT_API_CONSOLIDATION_MAX_COMPLETION_TOKENS=8192\n" in body
    assert "Environment=HINDSIGHT_API_REFLECT_MAX_COMPLETION_TOKENS=8192\n" in body
    _accepts(body)
