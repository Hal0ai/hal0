"""The hal0-provider installer seed MUST be byte-identical to the canonical src.

Two copies exist by design (mirroring hal0-memory):

* canonical source (importable): ``src/hal0/agents/hermes/plugins/provider_hal0/``
* installer seed (hyphen dir, shipped verbatim into
  ``$HERMES_HOME/plugins/model-providers/hal0/``):
  ``installer/agents/hermes/plugins/hal0-provider/``

This lock keeps them unified so ``hal0 agent bootstrap hermes --repair`` can
never clobber behaviour that isn't in the repo. Edit both dirs together.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SRC = _REPO_ROOT / "src" / "hal0" / "agents" / "hermes" / "plugins" / "provider_hal0"
_SEED = _REPO_ROOT / "installer" / "agents" / "hermes" / "plugins" / "hal0-provider"

# README.md is source-only (the seed ships no docs), matching hal0-memory.
_FILES = ["__init__.py", "profile.py", "plugin.yaml"]


@pytest.mark.parametrize("name", _FILES)
def test_seed_file_matches_source(name: str) -> None:
    src = (_SRC / name).read_bytes()
    seed = (_SEED / name).read_bytes()
    assert src == seed, (
        f"installer seed {name} drifted from canonical source — copy "
        f"src/hal0/agents/hermes/plugins/provider_hal0/{name} to "
        f"installer/agents/hermes/plugins/hal0-provider/{name}"
    )


def test_no_unexpected_seed_files() -> None:
    extra = {
        p.name
        for p in _SEED.iterdir()
        if p.is_file() and p.suffix != ".pyc" and p.name not in _FILES
    }
    assert not extra, f"unexpected files in installer seed: {sorted(extra)}"


def test_seed_has_all_expected_files() -> None:
    for name in _FILES:
        assert (_SEED / name).is_file(), f"missing seed file: {name}"
