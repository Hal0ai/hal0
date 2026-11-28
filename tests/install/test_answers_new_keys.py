"""The loader accepts the installer's new answer sections (access, maintenance,
models, hardware, memory) as known keys; contents are validated elsewhere."""

from __future__ import annotations

import json
import textwrap
import warnings

import pytest

from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.install.answers import AnswersError, load_answers

_NEW_KEYS = ("access", "maintenance", "models", "hardware", "memory")


def _hw():
    return HardwareInfo(
        platform="strix-halo",
        ram_mb=96 * 1024,
        ram_available_mb=96 * 1024,
        unified_memory_mb=96 * 1024,
        gpus=[GPUInfo(vendor="amd", vram_mb=512, compute_capable=True, vulkan_capable=True)],
        npu=NPUInfo(present=True, validated=True),
    )


def _write(tmp_path, text, name="hal0-setup.yaml"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(text))
    return str(p)


_BASE = """
version: 1
model_store:
  path: /var/lib/hal0/models
slots:
  - capability: chat
    name: chat
    port: 8081
    model_id: auto
"""

_NEW_SECTIONS = {k: {"anything": "goes", "n": 1} for k in _NEW_KEYS}


def test_strict_document_with_new_keys_loads(tmp_path):
    body = _BASE + "strict: true\n" + "".join(f"{k}:\n  anything: goes\n" for k in _NEW_KEYS)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        sel = load_answers(_write(tmp_path, body), _hw())
    assert sel.storage_dir == "/var/lib/hal0/models"


def test_json_document_with_new_keys_loads_strict(tmp_path):
    doc = {
        "version": 1,
        "strict": True,
        "model_store": {"path": "/var/lib/hal0/models"},
        **_NEW_SECTIONS,
    }
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(doc))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        sel = load_answers(str(path), _hw())
    assert sel.storage_dir == "/var/lib/hal0/models"


def test_non_strict_document_with_new_keys_is_silent(tmp_path):
    body = _BASE + "access:\n  mode: lan\n"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        load_answers(_write(tmp_path, body), _hw())


@pytest.mark.parametrize("key", _NEW_KEYS)
def test_new_key_must_be_a_mapping(tmp_path, key):
    body = _BASE + f'{key}: "x"\n'
    with pytest.raises(AnswersError, match=key):
        load_answers(_write(tmp_path, body), _hw())


def test_existing_documents_unchanged(tmp_path):
    plain = load_answers(_write(tmp_path, _BASE), _hw())
    with_new = load_answers(
        _write(tmp_path, _BASE + "".join(f"{k}:\n  a: 1\n" for k in _NEW_KEYS), "b.yaml"),
        _hw(),
    )
    assert plain == with_new


@pytest.mark.parametrize("key", _NEW_KEYS)
def test_null_new_key_is_an_empty_mapping(tmp_path, key):
    body = _BASE + f"strict: true\n{key}:\n"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        sel = load_answers(_write(tmp_path, body), _hw())
    assert sel.storage_dir == "/var/lib/hal0/models"
