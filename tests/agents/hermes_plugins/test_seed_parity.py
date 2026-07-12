"""The installer seed MUST be a byte-identical copy of the canonical source.

History: the seed at ``installer/agents/hermes/plugins/hal0-memory/`` and the
source at ``src/hal0/agents/hermes/plugins/memory_hindsight/`` drifted apart
(sync-client rework landed only in the seed; a three-tier prompt landed only
on a live box). This lock keeps them unified so ``hal0 agent bootstrap
hermes --repair`` can never clobber behavior that isn't in the repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src" / "hal0" / "agents" / "hermes" / "plugins" / "memory_hindsight"
_SEED = _REPO_ROOT / "installer" / "agents" / "hermes" / "plugins" / "hal0-memory"

_FILES = ["__init__.py", "_client.py", "provider.py", "plugin.yaml"]


@pytest.mark.parametrize("name", _FILES)
def test_seed_file_matches_source(name: str) -> None:
    src = (_SRC / name).read_bytes()
    seed = (_SEED / name).read_bytes()
    assert src == seed, (
        f"installer seed {name} drifted from canonical source — "
        f"copy src/hal0/agents/hermes/plugins/memory_hindsight/{name} to "
        f"installer/agents/hermes/plugins/hal0-memory/{name}"
    )


def test_no_unexpected_seed_files() -> None:
    extra = {
        p.name
        for p in _SEED.iterdir()
        if p.is_file() and p.suffix != ".pyc" and p.name not in _FILES
    }
    assert not extra, f"unexpected files in installer seed: {sorted(extra)}"
