"""``hf_token_from_env``: the one reader of the Hugging Face token environment variables."""

from __future__ import annotations

from pathlib import Path

import pytest

import hal0
from hal0.upstreams.huggingface import hf_token_from_env


def test_hf_token_from_env_prefers_hf_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "primary")
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "secondary")
    assert hf_token_from_env() == "primary"


def test_hf_token_from_env_falls_back_to_the_hub_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "secondary")
    assert hf_token_from_env() == "secondary"
    monkeypatch.setenv("HF_TOKEN", "")  # empty counts as unset
    assert hf_token_from_env() == "secondary"


def test_hf_token_from_env_is_none_when_unset_or_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    assert hf_token_from_env() is None
    monkeypatch.setenv("HF_TOKEN", "")
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "")
    assert hf_token_from_env() is None


def test_only_the_owner_reads_the_hub_token_name_from_os_environ() -> None:
    """A second copy of the lookup is how the two names drift apart; call the helper instead."""
    root = Path(hal0.__file__).parent
    readers = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if 'os.environ.get("HUGGING_FACE_HUB_TOKEN")' in path.read_text(encoding="utf-8")
    )
    assert readers == ["upstreams/huggingface.py"]
