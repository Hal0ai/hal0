"""The ``utility`` slot name is retired from new defaults and kept for old boxes.

A fresh install seeds ``agent``, ``brain`` and ``memory``. Nothing a new box
writes or defaults to may name ``utility``. A box that still has a slot called
``utility``, a config naming it (``memory.graph.extraction_slot``, written by
the v4 migration) or agents asking for ``hal0/utility`` keeps working exactly
as before; every such compatibility site carries a ``HAL0-SUNSET: v2.0.0``
marker.
"""

from __future__ import annotations

import inspect
import re
import tomllib
from importlib.resources import files
from pathlib import Path

import pytest

from hal0.agents import hermes_provision as hp
from hal0.agents.role_slots import resolve_role_slots
from hal0.cli import doctor_all as da
from hal0.config.migrations.utility_pin import pin_utility_slot
from hal0.config.paths import slots_config_dir
from hal0.config.schema import Hal0Config
from hal0.config.seeds import seed_stacks
from hal0.dispatcher import lane_pin
from hal0.install.static_seeds import FRESH_INSTALL_SEEDS, SEED_TEMPLATES
from hal0.normalize.resolver import SlotView, resolve_chain
from hal0.profiles.generate import LlmCallContext
from hal0.slots import reaper
from hal0.slots.state import SlotState

SRC = Path(hp.__file__).resolve().parents[1]


# ── converted defaults ────────────────────────────────────────────────────────


def test_no_new_code_path_defaults_to_utility() -> None:
    aux = inspect.signature(hp._resolve_auxiliary_tasks).parameters["extraction_slot"]
    assert aux.default == "memory"
    role = inspect.signature(resolve_role_slots).parameters["auxiliary_slot"]
    assert role.default == "memory"
    assert LlmCallContext().model == "hal0/memory"

    seed_text = files("hal0.config.data").joinpath("seed_stacks.toml").read_text(encoding="utf-8")
    assert "utility" not in seed_text.lower()
    for slug, stack in seed_stacks().items():
        assert "utility" not in stack.description.lower(), slug
        assert all(slot.slot != "utility" for slot in stack.slots), slug


def test_seed_stacks_place_models_on_existing_fresh_slots() -> None:
    raw = tomllib.loads(
        files("hal0.config.data").joinpath("seed_stacks.toml").read_text(encoding="utf-8")
    )
    for slug, stack in raw["stack"].items():
        named = [entry["slot"] for entry in stack["slots"]]
        # A stack creates no slot of its own: every slot it fills is one a
        # fresh install already has.
        assert set(named) <= set(FRESH_INSTALL_SEEDS), (slug, named)
        assert len(named) == len(set(named)), slug
    assert "utility" not in SEED_TEMPLATES


def test_hermes_unreadable_config_falls_back_to_memory_not_utility() -> None:
    # The `hermes_provision.memory_target_unreadable` branch assigns the
    # fallback inline; read it from the function's source, not from a spy.
    source = inspect.getsource(hp)
    assert 'extraction_slot, extraction_model = "memory", None' in source
    assert 'extraction_slot, extraction_model = "utility", None' not in source


def test_persona_does_not_mention_utility_or_npu_for_memory() -> None:
    rendered = hp._render_template(
        "SOUL.md.j2",
        env={"cpu": {}, "ram": {}, "npu": {}, "gpu": {}, "container": {}},
        hal0_version="1.0",
        hermes_version="0.1",
        oauth_providers=[],
    )
    assert "utility" not in rendered.lower()
    (principle,) = [ln for ln in rendered.splitlines() if ln.startswith("5. **Smallest model")]
    block = rendered[rendered.index(principle) : rendered.index("# Boundaries")]
    assert "`memory`" in block
    assert "NPU" not in block


# ── the migration ─────────────────────────────────────────────────────────────

_UTILITY_SEED = """\
# hal0 utility slot (operator comment, must survive).
name = "utility"
type = "llm"
device = "gpu-rocm"
port = 8090

[model]
id = "tiny"
"""


def _write(name: str, body: str) -> Path:
    d = slots_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_existing_utility_slot_gets_explicit_pin(tmp_hal0_home: str) -> None:
    path = _write("utility", _UTILITY_SEED)
    mode_before = path.stat().st_mode

    assert pin_utility_slot() == 1

    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert data["pinned"] is True
    assert data["model"] == {"id": "tiny"}
    assert data["port"] == 8090
    assert "operator comment, must survive" in text
    assert path.stat().st_mode == mode_before
    # The reaper reads the key: an explicit pin, not the default set.
    assert reaper.is_pinned("utility", data) is True


def test_pin_lands_in_the_slot_table_of_a_nested_file(tmp_hal0_home: str) -> None:
    path = _write("utility", '[slot]\nname = "utility"\ntype = "llm"\n\n[model]\nid = "tiny"\n')

    assert pin_utility_slot() == 1

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["slot"]["pinned"] is True
    assert "pinned" not in data
    assert data["model"] == {"id": "tiny"}


def test_pin_finds_a_utility_slot_by_name_in_an_id_keyed_file(tmp_hal0_home: str) -> None:
    path = _write("143", 'name = "utility"\ntype = "llm"\n')

    assert pin_utility_slot() == 1
    assert tomllib.loads(path.read_text(encoding="utf-8"))["pinned"] is True


def test_migration_is_idempotent(tmp_hal0_home: str) -> None:
    path = _write("utility", _UTILITY_SEED)

    assert pin_utility_slot() == 1
    once = path.read_bytes()
    assert pin_utility_slot() == 0
    assert path.read_bytes() == once


@pytest.mark.parametrize("explicit", ["false", "true"])
def test_migration_leaves_explicit_pin_alone(tmp_hal0_home: str, explicit: str) -> None:
    body = _UTILITY_SEED.replace("port = 8090\n", f"port = 8090\npinned = {explicit}\n")
    path = _write("utility", body)
    before = path.read_bytes()

    assert pin_utility_slot() == 0

    assert path.read_bytes() == before


def test_migration_touches_nothing_else(tmp_hal0_home: str) -> None:
    other = _write("agent", 'name = "agent"\ntype = "llm"\n')
    memory = _write("memory", 'name = "memory"\ntype = "llm"\npinned = false\n')
    before = (other.read_bytes(), memory.read_bytes())
    files_before = sorted(p.name for p in slots_config_dir().iterdir())

    assert pin_utility_slot() == 0

    assert (other.read_bytes(), memory.read_bytes()) == before
    # Never creates, renames or deletes a slot.
    assert sorted(p.name for p in slots_config_dir().iterdir()) == files_before


def test_migration_on_a_box_with_no_slot_directory(tmp_hal0_home: str) -> None:
    assert not slots_config_dir().exists()
    assert pin_utility_slot() == 0
    assert not slots_config_dir().exists()


def test_migration_skips_an_unreadable_slot_file(tmp_hal0_home: str) -> None:
    broken = _write("broken", "not = [toml")
    utility = _write("utility", _UTILITY_SEED)

    assert pin_utility_slot() == 1

    assert broken.read_text(encoding="utf-8") == "not = [toml"
    assert tomllib.loads(utility.read_text(encoding="utf-8"))["pinned"] is True


def test_the_update_runs_the_pin_pass() -> None:
    from hal0.updater import updater

    assert "pin_utility_slot" in inspect.getsource(updater.run_post_activation_migrations)


# ── kept for existing boxes ───────────────────────────────────────────────────


def _views(*names: str) -> list[SlotView]:
    return [
        SlotView(name=n, device="gpu-rocm", model_id=f"{n}-model", context_length=1) for n in names
    ]


def test_hal0_utility_resolves_with_and_without_a_utility_slot() -> None:
    loaded = {"agent-model", "memory-model", "utility-model"}

    # A box with a utility slot: exactly today's chain, utility first.
    with_utility = resolve_chain("hal0/utility", _views("agent", "memory", "utility"), loaded)
    assert with_utility is not None
    assert (with_utility.model_id, with_utility.matched_name) == ("utility-model", "utility")

    # A box without one resolves what hal0/memory resolves on that box.
    for names in (("agent", "memory"), ("agent",), ("agent", "brain")):
        slots = _views(*names)
        util = resolve_chain("hal0/utility", slots, loaded)
        mem = resolve_chain("hal0/memory", slots, loaded)
        assert util == mem, names
    bound = resolve_chain("hal0/utility", _views("agent", "memory"), loaded)
    assert bound is not None and bound.matched_name == "memory"

    # Today's chain for a box whose utility slot has no model stays the agent.
    assert resolve_chain("hal0/utility", _views("agent"), loaded).matched_name == "agent"


def test_hal0_npu_chain_drops_utility_only_when_there_is_no_utility_slot() -> None:
    loaded: set[str] = set()
    npu = SlotView(name="npu", device="npu", model_id="npu-model", context_length=1)
    without = resolve_chain("hal0/npu", [*_views("agent", "memory"), npu], loaded)
    assert without is not None and without.matched_name == "npu"
    # No npu slot and no utility slot: the agent, never the memory slot.
    only_agent = resolve_chain("hal0/npu", _views("agent", "memory"), loaded)
    assert only_agent is not None and only_agent.matched_name == "agent"
    # With a utility slot the chain is today's (npu, utility, agent).
    with_utility = resolve_chain("hal0/npu", _views("agent", "memory", "utility"), loaded)
    assert with_utility is not None and with_utility.matched_name == "utility"


def test_extraction_slot_utility_still_resolves() -> None:
    # Config written by the v4 migration still loads.
    cfg = Hal0Config.model_validate({"memory": {"graph": {"extraction_slot": "utility"}}})
    assert cfg.memory.graph.extraction_slot == "utility"

    # Hermes aux routing for such a box still lands on the utility slot's model.
    slots = [
        {"name": "agent", "type": "llm", "state": "ready", "model_id": "big", "context_length": 8},
        {
            "name": "utility",
            "type": "llm",
            "state": "ready",
            "model_id": "tiny",
            "context_length": 8,
        },
    ]
    base = "http://127.0.0.1:8080/v1"
    aux = hp._resolve_auxiliary_tasks(slots, hal0_base_url=base, extraction_slot="utility")
    assert aux["compression"]["model"] == "tiny"
    # A custom extraction target on a box that has a utility slot keeps the same preference.
    custom = hp._resolve_auxiliary_tasks(slots, hal0_base_url=base, extraction_slot="nano")
    assert custom["compression"]["model"] == "tiny"
    # Without a utility slot a custom target prefers the memory slot.
    memory = [
        slots[0],
        {"name": "memory", "type": "llm", "state": "ready", "model_id": "mem", "context_length": 8},
    ]
    nano = hp._resolve_auxiliary_tasks(memory, hal0_base_url=base, extraction_slot="nano")
    assert nano["compression"]["model"] == "hal0/memory"


def test_reaper_still_pins_a_utility_slot_that_has_no_explicit_key() -> None:
    assert reaper.is_pinned("utility", None) is True
    assert reaper.is_pinned("utility", {"name": "utility"}) is True
    assert reaper.is_pinned("utility", {"pinned": False}) is False
    assert reaper.is_pinned("memory", None) is False


def test_doctor_still_reads_a_bare_utility_task_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "openwebui.env"
    env.write_text("TASK_MODEL_EXTERNAL=utility\n", encoding="utf-8")
    monkeypatch.setattr(da.paths, "openwebui_env", lambda: env)

    assert da.check_openwebui_task_model([{"name": "agent"}, {"name": "utility"}]).status == "pass"
    assert da.check_openwebui_task_model([{"name": "agent"}, {"name": "memory"}]).status == "warn"


class _Slots:
    def __init__(self, in_flight: dict[str, int]) -> None:
        self._in_flight = in_flight

    def state(self, name: str) -> SlotState:
        return SlotState.READY

    def in_flight_count(self, name: str) -> int:
        return self._in_flight.get(name, 0)


def test_lane_pin_still_ranks_an_idle_sibling_ahead_of_utility() -> None:
    sm = _Slots({})
    assert lane_pin.preferred_slot(sm, ["utility", "agent"]) == "agent"
    # The pin wins for an explicit hal0/utility request.
    assert lane_pin.preferred_slot(sm, ["utility", "agent"], lane_slot="utility") == "utility"
    # A busy sibling does not outrank an idle utility.
    busy = _Slots({"agent": 1})
    assert lane_pin.preferred_slot(busy, ["utility", "agent"]) == "utility"


# ── sunset markers ────────────────────────────────────────────────────────────

_MARKER = re.compile(r"HAL0-SUNSET:\s*v2\.0\.0")

#: (file under src/hal0, a token on the kept line). The marker sits on the
#: same line or in the comment block directly above it.
_KEPT_SITES = [
    ("normalize/resolver.py", '"hal0/utility": ("utility"'),
    ("normalize/resolver.py", "def _without_utility"),
    ("slots/reaper.py", "_PINNED_BY_DEFAULT: frozenset"),
    ("cli/doctor_all.py", 'target == "utility"'),
    ("dispatcher/lane_pin.py", 'UTILITY_SLOT_NAME = "utility"'),
    ("agents/hermes_provision.py", 'auxiliary_slot = "utility" if'),
    ("config/migrations/utility_pin.py", "def pin_utility_slot"),
]


@pytest.mark.parametrize(("rel", "token"), _KEPT_SITES)
def test_sunset_markers_present(rel: str, token: str) -> None:
    lines = (SRC / rel).read_text(encoding="utf-8").splitlines()
    (at,) = [i for i, ln in enumerate(lines) if token in ln][:1] or [None]
    assert at is not None, f"{rel}: {token!r} not found"
    window = lines[max(0, at - 8) : at + 1]
    assert any(_MARKER.search(ln) for ln in window), f"{rel}:{at + 1} has no HAL0-SUNSET marker"
