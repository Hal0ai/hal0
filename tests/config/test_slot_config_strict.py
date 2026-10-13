"""``SlotConfig`` forbids unknown keys (ADR-0029 §5).

``ModelConfig`` and ``ServerConfig`` already did; ``SlotConfig`` was the last
slot model that let a stray key ride along. These tests pin the contract:

* a key the schema does not declare fails the load, and the error names the
  key and the file (the "name the key" half of the guard);
* there is no escape hatch: no catch-all ``extra`` field, no write-boundary
  allow-list beyond the declared fields;
* every key hal0 itself writes is a declared field (the writers themselves are
  exercised in ``tests/slots/test_slot_writers_load.py``).

Targeted run:
    uv run pytest tests/config/test_slot_config_strict.py -q
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hal0.config.loader import (
    ConfigParseError,
    _flatten_slot_toml,
    load_slot_config,
    save_slot_config,
)
from hal0.config.schema import REMOVED_SLOT_KEYS, SlotConfig
from hal0.errors import BadRequest
from hal0.slot_config import unknown_slot_config_keys
from hal0.slots.config_write import guard_slot_write_payload

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEED_SLOTS = sorted((_REPO_ROOT / "installer" / "etc-hal0" / "slots").glob("*.toml"))


def _write(tmp_path: Path, body: str, name: str = "brain") -> Path:
    path = tmp_path / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


_BASE = 'name = "brain"\nport = 8089\ndevice = "gpu-rocm"\nprofile = "brain"\n'


# ── the error names the key and the file ─────────────────────────────────────


def test_unknown_top_level_key_names_the_key_and_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path, _BASE + 'colour = "blue"\n')
    with pytest.raises(ConfigParseError) as exc:
        load_slot_config("brain", path=path)
    message = str(exc.value)
    assert "'colour'" in message
    assert str(path) in message
    assert exc.value.details["unknown_keys"] == ["colour"]
    assert exc.value.details["path"] == str(path)


@pytest.mark.parametrize(
    ("table", "key"),
    [("model", "ctxx"), ("server", "port"), ("npu", "vision"), ("image", "steps")],
)
def test_unknown_key_inside_a_table_is_named_with_its_table(
    tmp_path: Path, table: str, key: str
) -> None:
    path = _write(tmp_path, _BASE + f"[{table}]\n{key} = 1\n")
    with pytest.raises(ConfigParseError) as exc:
        load_slot_config("brain", path=path)
    assert f"'[{table}].{key}'" in str(exc.value)
    assert exc.value.details["unknown_keys"] == [f"[{table}].{key}"]


def test_unknown_table_is_an_unknown_key(tmp_path: Path) -> None:
    """A table the schema does not declare no longer parks in a catch-all."""
    path = _write(tmp_path, _BASE + "[custom]\nthreads = 12\n")
    with pytest.raises(ConfigParseError) as exc:
        load_slot_config("brain", path=path)
    assert exc.value.details["unknown_keys"] == ["custom"]


def test_every_unknown_key_is_reported_not_just_the_first(tmp_path: Path) -> None:
    path = _write(tmp_path, _BASE + 'one = 1\ntwo = 2\n[model]\ndefault = "m"\nthree = 3\n')
    with pytest.raises(ConfigParseError) as exc:
        load_slot_config("brain", path=path)
    assert exc.value.details["unknown_keys"] == ["[model].three", "one", "two"]


def test_id_keyed_loader_names_the_key_too(tmp_path: Path) -> None:
    from hal0.config.loader import load_slot_config_by_id

    path = _write(tmp_path, 'id = 7\nname = "brain"\nport = 8089\ncolour = "blue"\n', name="7")
    with pytest.raises(ConfigParseError) as exc:
        load_slot_config_by_id(7, path=path)
    assert "'colour'" in str(exc.value)
    assert str(path) in str(exc.value)


@pytest.mark.parametrize("key", sorted(REMOVED_SLOT_KEYS))
def test_a_removed_key_names_its_replacement(tmp_path: Path, key: str) -> None:
    value = '"vulkan"' if key == "backend" else "true"
    path = _write(tmp_path, _BASE + f"{key} = {value}\n")
    with pytest.raises(ConfigParseError) as exc:
        load_slot_config("brain", path=path)
    assert f"'{key}'" in str(exc.value)
    assert REMOVED_SLOT_KEYS[key] in str(exc.value)


def test_a_bare_image_ref_points_at_image_pin(tmp_path: Path) -> None:
    path = _write(tmp_path, _BASE + 'image = "ghcr.io/foo:v1"\n')
    with pytest.raises(ConfigParseError) as exc:
        load_slot_config("brain", path=path)
    assert "'image'" in str(exc.value)
    assert "image_pin" in str(exc.value)


def test_a_bad_value_and_a_stray_key_are_both_reported(tmp_path: Path) -> None:
    path = _write(tmp_path, 'name = "brain"\nport = 1\ncolour = "blue"\n')
    with pytest.raises(ConfigParseError) as exc:
        load_slot_config("brain", path=path)
    assert "'colour'" in str(exc.value)
    assert "port" in str(exc.value)


def test_an_unconverted_file_still_reports_slot_unconverted(tmp_path: Path) -> None:
    """A tuning key is the converter's, not an "unknown key": same code as ever."""
    path = _write(tmp_path, _BASE + "parallel = 4\n")
    with pytest.raises(ConfigParseError) as exc:
        load_slot_config("brain", path=path)
    assert "slot.unconverted" in str(exc.value)
    assert "parallel" in str(exc.value)


def test_direct_validation_raises_pydantic_extra_forbidden() -> None:
    with pytest.raises(ValidationError) as exc:
        SlotConfig.model_validate({"name": "x", "port": 8081, "colour": "blue"})
    assert exc.value.errors()[0]["type"] == "extra_forbidden"


# ── no escape hatch ──────────────────────────────────────────────────────────


def test_there_is_no_catch_all_field() -> None:
    assert SlotConfig.model_config["extra"] == "forbid"
    assert "extra" not in SlotConfig.model_fields
    with pytest.raises(ValidationError):
        SlotConfig.model_validate({"name": "x", "port": 8081, "extra": {"anything": 1}})


def test_the_write_boundary_has_no_allow_list_beyond_the_declared_fields() -> None:
    """Retired and never-read keys the old allow-list tolerated are refused;
    the declared ``type`` / ``default`` are accepted because they are fields."""
    refused = unknown_slot_config_keys(
        {
            "lru": True,
            "default_language": "Auto",
            "extra": {"x": 1},
            "backend": "rocm",
            "enabled": True,
            "type": "llm",
            "default": True,
        }
    )
    assert refused == ["backend", "default_language", "enabled", "extra", "lru"]


@pytest.mark.parametrize(
    "payload",
    [
        {"colour": "blue"},
        {"model": {"default": "m", "colour": "blue"}},
        {"server": {"colour": "blue"}},
        {"npu": {"colour": "blue"}},
        {"image": {"colour": "blue"}},
        {"image": "ghcr.io/foo:v1"},
        {"slot": {"colour": "blue"}},
        {"extra": {"anything": 1}},
    ],
)
def test_the_in_process_write_guard_refuses_what_the_load_would(payload: dict[str, Any]) -> None:
    """A writer that never passes a FastAPI handler cannot persist a file
    that then fails to load."""
    assert unknown_slot_config_keys(payload)
    with pytest.raises(BadRequest) as exc:
        guard_slot_write_payload(payload)
    assert exc.value.code == "validation.unknown_keys"


def test_a_removed_key_is_refused_with_its_replacement_not_a_spelling_hint() -> None:
    with pytest.raises(BadRequest) as exc:
        guard_slot_write_payload({"lru": True})
    assert exc.value.code == "slot.removed_key_denied"
    assert "priority" in exc.value.details["hint"]


# ── what hal0 writes is declared ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("type", "llm"),
        ("default", True),
        ("served_by", "flm"),
        ("output_sanity", False),
        ("system_prompt", "You are terse."),
        ("default_voice", "af_bella"),
        ("default_speed", 1.0),
        ("default_response_format", "mp3"),
        ("autoload", True),
        ("pinned", True),
        ("priority", 10),
        ("idle_timeout_s", 0),
        ("gpu_index", 1),
        ("n_gpu_layers", 0),
        ("threads", 8),
        ("metrics", False),
        ("binary", "llamacpp"),
        ("image_pin", "ghcr.io/foo:v1"),
        ("runtime", "container"),
        ("provider", "comfyui"),
    ],
)
def test_every_key_a_writer_emits_is_a_declared_field(key: str, value: Any) -> None:
    cfg = SlotConfig.model_validate({"name": "x", "port": 8081, key: value})
    assert getattr(cfg, key) == value
    assert not unknown_slot_config_keys({key: value})


def test_the_tables_are_declared_too() -> None:
    cfg = SlotConfig.model_validate(
        {
            "name": "x",
            "port": 8081,
            "model": {"default": "m"},
            "server": {"env": {"A": "b"}},
            "npu": {"asr": True},
            "image": {"idle_restore_minutes": 5},
        }
    )
    assert cfg.server.env == {"A": "b"}
    assert cfg.npu is not None and cfg.npu.asr is True
    assert cfg.image_gen.idle_restore_minutes == 5


# ── the two on-disk shapes ───────────────────────────────────────────────────


def test_nested_shape_loads_and_slot_table_wins_over_a_root_scalar(tmp_path: Path) -> None:
    """The launch path hoists ``[slot]`` over the root; the typed load agrees."""
    path = _write(
        tmp_path,
        'profile = "stale"\ntype = "llm"\n'
        '[slot]\nname = "brain"\nport = 8089\nprofile = "brain"\n'
        '[model]\ndefault = "m"\n',
    )
    cfg = load_slot_config("brain", path=path)
    assert cfg.profile == "brain"  # [slot] wins
    assert cfg.type == "llm"  # a root scalar beside [slot] is still read
    assert cfg.model.default == "m"


def test_flatten_carries_tables_under_their_own_names() -> None:
    flat = _flatten_slot_toml(
        {
            "name": "x",
            "port": 8081,
            "model": {"default": "m"},
            "server": {"env": {"A": "b"}},
            "npu": {"asr": True},
            "image": {"default_steps": 4},
        },
        "x",
    )
    assert flat["server"] == {"env": {"A": "b"}}
    assert flat["npu"] == {"asr": True}
    assert flat["image"] == {"default_steps": 4}
    assert "extra" not in flat


# ── shipped seeds and the save path ──────────────────────────────────────────


@pytest.mark.parametrize("seed", _SEED_SLOTS, ids=lambda p: p.stem)
def test_every_shipped_seed_loads(seed: Path, tmp_hal0_home: str) -> None:
    cfg = load_slot_config(seed.stem, path=seed)
    assert cfg.name == seed.stem
    raw = tomllib.loads(seed.read_text(encoding="utf-8"))
    # Nothing the seed says was set aside: every key is a declared field.
    assert not unknown_slot_config_keys(raw)


@pytest.mark.parametrize("seed", _SEED_SLOTS, ids=lambda p: p.stem)
def test_load_save_load_is_a_fixed_point(seed: Path, tmp_path: Path, tmp_hal0_home: str) -> None:
    first = load_slot_config(seed.stem, path=seed)
    dest = tmp_path / f"{seed.stem}.toml"
    save_slot_config(first, path=dest)
    second = load_slot_config(seed.stem, path=dest)
    assert second == first
    # And what was written carries no key the schema does not declare.
    assert not unknown_slot_config_keys(tomllib.loads(dest.read_text(encoding="utf-8")))


def test_save_keeps_the_profile_and_the_type(tmp_path: Path, tmp_hal0_home: str) -> None:
    cfg = SlotConfig.model_validate(
        {"name": "x", "port": 8081, "profile": "agent", "type": "llm", "pinned": True}
    )
    dest = tmp_path / "x.toml"
    save_slot_config(cfg, path=dest)
    raw = tomllib.loads(dest.read_text(encoding="utf-8"))
    assert raw["slot"]["profile"] == "agent"
    assert raw["slot"]["type"] == "llm"
    assert raw["slot"]["pinned"] is True
    assert "server" not in raw and "npu" not in raw and "image" not in raw


def test_a_dumped_config_uses_the_toml_table_names() -> None:
    cfg = SlotConfig.model_validate(
        {"name": "x", "port": 8081, "npu": {"asr": True}, "image": {"default_steps": 4}}
    )
    dumped = cfg.model_dump(mode="python")
    assert dumped["npu"]["asr"] is True
    assert dumped["image"]["default_steps"] == 4
    assert "image_gen" not in dumped
    assert "extra" not in dumped
    bare = SlotConfig.model_validate({"name": "x", "port": 8081}).model_dump(mode="python")
    assert "server" not in bare and "npu" not in bare and "image" not in bare
