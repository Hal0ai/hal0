"""Pure-helper tests for installer/bench/server_ab.py.

server_ab.py is a stdlib-only script (runs bare on the box, no hal0 venv), not
an importable package — load it by path. These cover the new MTP-sweep helpers
(depth prompt builder, per-request speculative override, sampler body, CSV
parsers) added for the ROCmFPX bench runbook. The HTTP/mode functions need a
live slot and are exercised on-box, not here.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "installer" / "bench" / "server_ab.py"
_spec = importlib.util.spec_from_file_location("server_ab", _PATH)
assert _spec and _spec.loader
server_ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server_ab)


# ── depth prompt builder ──────────────────────────────────────────────────────


def test_build_prompt_scales_with_depth():
    short = server_ab._build_prompt(2000)
    deep = server_ab._build_prompt(32768)
    assert len(deep) > len(short)
    # ~50 tok/rep, ~ _PARA chars/rep — deep is ~16x the reps of short.
    assert server_ab._PARA in short
    assert deep.count(server_ab._PARA) > short.count(server_ab._PARA) * 10


def test_build_prompt_floor_is_one_rep():
    # A sub-rep depth still yields a non-empty prompt (max(1, ...)).
    assert server_ab._build_prompt(0) == server_ab._PARA
    assert server_ab._build_prompt(5) == server_ab._PARA


# ── per-request speculative override ─────────────────────────────────────────


def test_spec_override_builds_speculative_block():
    assert server_ab._spec_override(4, 0.25, 0) == {
        "speculative": {"n_max": 4, "p_min": 0.25, "n_min": 0}
    }


def test_spec_override_omits_unset_and_empties_when_all_none():
    assert server_ab._spec_override(2, None, None) == {"speculative": {"n_max": 2}}
    assert server_ab._spec_override(None, None, None) == {}  # nothing to override


# ── sampler body (greedy vs production) ──────────────────────────────────────


def _ns(**kw) -> argparse.Namespace:
    base = {"temp": 0.0, "top_p": None, "top_k": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_sampler_body_greedy_default():
    assert server_ab._sampler_body(_ns()) == {"temperature": 0.0}


def test_sampler_body_production_sampler():
    body = server_ab._sampler_body(_ns(temp=0.6, top_p=0.95, top_k=20))
    assert body == {"temperature": 0.6, "top_p": 0.95, "top_k": 20}


# ── CSV parsers ──────────────────────────────────────────────────────────────


def test_csv_parsers_tolerate_spaces_and_blanks():
    assert server_ab._csv_ints("1, 2 ,4,, 8") == [1, 2, 4, 8]
    assert server_ab._csv_floats("0.0,0.25, 0.5 ") == [0.0, 0.25, 0.5]
