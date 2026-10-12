"""Builders for an install as a pre-ADR-0029 release left it.

The profile-centric conversion reads raw files and raw registry rows, so the
tests build the old shapes directly: a registry migrated only up to 011 (the
tuning columns still on ``model``), slot TOMLs carrying tuning keys, and an
old-shape ``profiles.toml``. ``write_db(..., upgraded=True)`` then applies
the shipped migrations on top, which moves the tuning into the holding table
exactly as an upgrade does before the converter runs.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from importlib.resources import files
from pathlib import Path
from typing import Any

import tomli_w

from hal0.config import paths

THINKING = (
    "--jinja -fa auto -b 2048 -ub 512 --reasoning on --reasoning-format deepseek "
    "--temp 1.0 --top-p 0.95 --no-mmproj"
)
CODING_TUNE = "--jinja -fa auto -b 2048 -ub 512 --reasoning off --temp 0.7 --top-k 40 --no-mmproj"

#: Model-row keys that a pre-012 registry stored as tuning.
_TUNING_COLUMNS = ("profile", "extra_args", "context_size", "rope_freq_base", "n_gpu_layers")


def write_toml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tomli_w.dumps(data).encode())


def write_slot(stem: str, data: dict[str, Any]) -> None:
    write_toml(paths.slots_config_dir() / f"{stem}.toml", data)


def old_model(
    model_id: str = "m",
    *,
    path: str | None = None,
    mmproj: str | None = None,
    tags: list[str] | None = None,
    capabilities: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One ``model`` row as a pre-012 registry stored it.

    ``metadata`` and ``defaults`` take the keys the old ``Model`` carried:
    ``metadata.context_length`` lands in its column and the rest in ``extra``;
    ``defaults`` may hold profile, extra_args, context_size, rope_freq_base,
    n_gpu_layers, chat_template, mtp, jinja, enable_thinking and vision.
    """
    meta = dict(metadata or {})
    dflt = dict(defaults or {})
    extra = {k: v for k, v in meta.items() if k != "context_length"}
    for key in ("enable_thinking", "vision"):
        if dflt.get(key) is not None:
            extra[f"_{key}"] = dflt.pop(key)
    row: dict[str, Any] = {
        "id": model_id,
        "path": path or f"/models/{model_id}/model.gguf",
        "mmproj": mmproj,
        "context_length": meta.get("context_length"),
        "mtp": dflt.pop("mtp", None),
        "jinja": dflt.pop("jinja", None),
        "chat_template": dflt.pop("chat_template", None),
        "name": model_id,
        "size_bytes": 0,
        "license": "unknown",
        "hf_filename": "",
        "capabilities": json.dumps(capabilities or []),
        "tags": json.dumps(tags or []),
        "extra": json.dumps(extra) if extra else None,
    }
    for key in _TUNING_COLUMNS:
        row[key] = dflt.pop(key, None)
    if dflt:
        raise ValueError(f"unknown old model defaults: {sorted(dflt)}")
    return row


def _migrations_upto(target: Path, last: int) -> Path:
    out = target.parent / f"_migrations_upto_{last:03d}"
    out.mkdir(exist_ok=True)
    for entry in files("hal0.db.migrations").iterdir():
        name = entry.name
        if name.endswith(".sql") and int(name.split("_", 1)[0]) <= last:
            (out / name).write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
    return out


def write_db(rows: list[dict[str, Any]], *, upgraded: bool = False, db: Path | None = None) -> Path:
    """A registry holding ``rows`` in the pre-012 shape.

    With ``upgraded`` the shipped migrations then run on top, so the tuning
    sits in the holding table and the model columns are gone: the state an
    install is in when the converter first runs after an upgrade.
    """
    from hal0.db.connection import connect
    from hal0.db.migrate import migrate

    target = db or paths.db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    upto = _migrations_upto(target, 11)
    try:
        with connect(target) as conn:
            migrate(conn, upto)
            for row in rows:
                keys = list(row)
                conn.execute(
                    f"INSERT INTO model ({', '.join(keys)}) "
                    f"VALUES ({', '.join('?' for _ in keys)})",
                    [row[k] for k in keys],
                )
            conn.commit()
            if upgraded:
                migrate(conn)
    finally:
        shutil.rmtree(upto)
    return target


def model_columns(db: Path | None = None) -> list[str]:
    conn = sqlite3.connect(db or paths.db_path())
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(model)")]
    finally:
        conn.close()


def tables(db: Path | None = None) -> set[str]:
    conn = sqlite3.connect(db or paths.db_path())
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def production_install() -> None:
    """An install in the shapes found on a production box (names and paths
    anonymised): a slot whose ``[server].extra_args`` never reached the
    launch, a divergent profile overlay, a slot with no profile, slots naming
    profiles that do not exist, an unhonoured context ceiling, an FLM thinking
    default, a name-keyed slot file next to id-keyed ones, a TTS slot whose
    model has no registry row, and stack rows carrying tuning keys."""
    write_toml(
        paths.profiles_toml(),
        {
            "legacy_seeds_migrated": True,
            "legacy_seeds_adopted": ["coding", "thinking"],
            "profile": {
                "thinking": {"flags": THINKING, "mtp": False, "quant": ""},
                "coding": {"flags": CODING_TUNE, "mtp": False, "quant": ""},
            },
        },
    )
    write_slot(
        "1",
        {
            "id": 1,
            "name": "agent",
            "type": "llm",
            "device": "gpu-vulkan",
            "backend": "vulkan",
            "profile": "thinking",
            "port": 8082,
            "parallel": 1,
            "threads": 16,
            "model": {"default": "big-chat", "context_size": 96000},
            "server": {"extra_args": "-ctk q8_0 -ctv q8_0"},
        },
    )
    write_slot(
        "2",
        {
            "id": 2,
            "name": "brain",
            "type": "llm",
            "device": "gpu-vulkan",
            "profile": "brain",
            "port": 8087,
            "chat_template": "auto",
            "model": {"default": "small-chat", "context_size": 66000},
        },
    )
    write_slot(
        "3",
        {
            "id": 3,
            "name": "code",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "rocm-dense-gone",
            "port": 8085,
            "model": {"default": "", "context_size": 96000},
        },
    )
    write_slot(
        "5",
        {
            "id": 5,
            "name": "embed",
            "type": "embedding",
            "device": "gpu-rocm",
            "port": 8089,
            "model": {"default": "embedder", "context_size": 8192},
        },
    )
    write_slot(
        "8",
        {
            "id": 8,
            "name": "flm",
            "type": "llm",
            "device": "npu",
            "profile": "flm",
            "port": 8088,
            "model": {"default": "npu-chat"},
            "npu": {"chat": True, "asr": True, "embed": False},
        },
    )
    write_slot(
        "15",
        {
            "id": 15,
            "name": "utility",
            "type": "llm",
            "device": "gpu-vulkan",
            "profile": "chat",
            "port": 8081,
            "model": {"default": "no-native", "context_size": 98304},
        },
    )
    write_slot(
        "14",
        {
            "id": 14,
            "name": "tts",
            "type": "tts",
            "device": "gpu-rocm",
            "profile": "qwen3-tts",
            "port": 8084,
            "model": {"default": "qwen3-tts", "context_size": 16000},
        },
    )
    write_slot(
        "coder",
        {
            "name": "coder",
            "type": "llm",
            "device": "gpu-vulkan",
            "profile": "coding",
            "port": 8083,
            "model": {"context_size": 32768},
        },
    )
    write_slot(
        "16",
        {
            "id": 16,
            "name": "clean",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "chat",
            "port": 8090,
            "model": {"default": "untuned"},
        },
    )
    write_toml(
        paths.stacks_toml(),
        {
            "stack": {
                "s1": {
                    "slots": [
                        {
                            "slot": "agent",
                            "model": "big-chat",
                            "profile": "chat",
                            "vision": False,
                            "mtp": True,
                        },
                        {"slot": "brain", "profile": "brain", "server_extra_args": "-b 64"},
                    ]
                }
            }
        },
    )
    write_db(
        [
            old_model(
                "big-chat", mmproj="/models/big-chat/mm.gguf", metadata={"context_length": 262144}
            ),
            old_model(
                "small-chat",
                metadata={"context_length": 131072},
                defaults={"profile": "coding", "extra_args": CODING_TUNE},
            ),
            old_model("embedder"),
            old_model("npu-chat", defaults={"enable_thinking": True}),
            old_model("no-native"),
            old_model("untuned", metadata={"context_length": 32768}),
            old_model("unbound", defaults={"context_size": 98304}),
            old_model("mtp-only", tags=["mtp"], defaults={"mtp": True}),
        ]
    )
