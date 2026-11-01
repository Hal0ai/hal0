"""The installer's slot-model update binds only a resolvable model id (#3195).

``PUT /api/install/slots/{name}/model`` was the one remaining slot write that
stored any model id: #3194 closed the door on slot create and edit, but an id
the registry does not know posted here still landed in the slot TOML, and the
next ``load`` served a same-capability stand-in under a ``ready`` state. The
route now runs the same :func:`hal0.slots.config_write.check_model_registered`
gate; a curated id and a re-sent id the slot already carries still pass, and
NPU/FLM tags stay exempt.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from tests.fixtures.model_registration import register_model_file

CODE = "validation.model_not_registered"
UNKNOWN = "not-a-registered-id"


def _seed_slot(tmp_hal0_home: str, name: str, default: str) -> Path:
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text(
        f'name = "{name}"\nport = 8081\ndevice = "gpu-vulkan"\nprovider = "llama-server"\n'
        f'runtime = "container"\nprofile = "vulkan-radv"\n[model]\ndefault = "{default}"\n',
        encoding="utf-8",
    )
    return path


def _saved_default(path: Path) -> str:
    return tomllib.loads(path.read_text(encoding="utf-8"))["model"]["default"]


def test_install_slot_model_refuses_unregistered_id(tmp_hal0_home: str, client: TestClient) -> None:
    toml_path = _seed_slot(tmp_hal0_home, "chat", "gguf-a")
    r = client.put("/api/install/slots/chat/model", json={"model_id": UNKNOWN})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == CODE
    assert r.json()["error"]["details"]["model_id"] == UNKNOWN
    assert _saved_default(toml_path) == "gguf-a", "the slot file must not be touched"


def test_install_slot_model_accepts_registered_id(tmp_hal0_home: str, client: TestClient) -> None:
    toml_path = _seed_slot(tmp_hal0_home, "chat", "gguf-a")
    register_model_file(client, tmp_hal0_home, "gguf-b")
    r = client.put("/api/install/slots/chat/model", json={"model_id": "gguf-b"})
    assert r.status_code == 200, r.text
    assert _saved_default(toml_path) == "gguf-b"


def test_install_slot_model_accepts_unchanged_id(tmp_hal0_home: str, client: TestClient) -> None:
    """Re-sending the id the slot already carries is not a new bind (#2668):
    a slot file written before the guard still saves its other fields."""
    toml_path = _seed_slot(tmp_hal0_home, "chat", UNKNOWN)
    r = client.put("/api/install/slots/chat/model", json={"model_id": UNKNOWN})
    assert r.status_code == 200, r.text
    assert _saved_default(toml_path) == UNKNOWN
