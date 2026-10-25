"""``strip_retired_image_keys``: take ``[image].idle_restore_minutes`` out of slot TOMLs.

ADR-0038: image mode ends when generation stops, so nothing reads the idle
window and ``ImageGenConfig`` refuses the key. An upgraded box still carries
the key the installer used to seed (``idle_restore_minutes = 60``), and the
slot loader rejects an unknown key, so the update removes it. These tests pin
the pass: the line goes, the file's comments stay, a second run changes
nothing, and one unreadable file does not stop the rest.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from hal0.config.loader import load_slot_config
from hal0.config.paths import slots_config_dir
from hal0.updater.updater import strip_retired_image_keys

# The body the installer used to seed for the img slot (comments included),
# which is what an existing box has on disk.
_SHIPPED_IMG_SEED = """\
# hal0 image-generation slot — ComfyUI in a podman container (hal0-slot@img).
# ComfyUI's models are freed (POST /free) when LLM slots come back after
# [image].idle_restore_minutes with no jobs (spec §7).

name = "img"
type = "image"
provider = "comfyui"
device = "gpu-rocm"
runtime = "container"
profile = "comfyui"
port = 8188
n_gpu_layers = -1
threads = 0

[image]                       # persisted image-gen settings (#599)
idle_restore_minutes = 60
# WxH an image request gets when it names no size; "" means the model's native size.
default_size = ""
default_steps = 0
"""


def _write(name: str, body: str) -> Path:
    d = slots_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_strip_removes_the_key_and_keeps_the_comments(tmp_hal0_home: str) -> None:
    path = _write("img", _SHIPPED_IMG_SEED)

    assert strip_retired_image_keys() == 1

    text = path.read_text(encoding="utf-8")
    assert "idle_restore_minutes" not in tomllib.loads(text)["image"]
    assert "# WxH an image request gets" in text
    assert "# persisted image-gen settings (#599)" in text
    # The slot loads again: the key was the only reason it did not.
    cfg = load_slot_config("img")
    assert cfg.image_gen.default_steps == 0


def test_strip_is_idempotent(tmp_hal0_home: str) -> None:
    path = _write("img", _SHIPPED_IMG_SEED)
    assert strip_retired_image_keys() == 1
    after_first = path.read_bytes()

    assert strip_retired_image_keys() == 0
    assert path.read_bytes() == after_first


def test_strip_leaves_files_without_the_key_untouched(tmp_hal0_home: str) -> None:
    chat = _write("chat", 'name = "chat"\nport = 8081\n')
    img = _write("img", 'name = "img"\nport = 8188\n\n[image]\ndefault_steps = 4\n')
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (chat, img)}

    assert strip_retired_image_keys() == 0

    for p, (data, mtime) in before.items():
        assert p.read_bytes() == data
        assert p.stat().st_mtime_ns == mtime


def test_strip_rewrites_an_inline_image_table_without_the_key(tmp_hal0_home: str) -> None:
    """A shape the line edit cannot handle is re-serialised without the key."""
    path = _write(
        "img",
        'name = "img"\nport = 8188\nimage = { idle_restore_minutes = 5, default_steps = 4 }\n',
    )

    assert strip_retired_image_keys() == 1

    assert tomllib.loads(path.read_text(encoding="utf-8"))["image"] == {"default_steps": 4}


def test_strip_rewrites_a_dotted_key_without_it(tmp_hal0_home: str) -> None:
    path = _write(
        "img",
        'name = "img"\nport = 8188\nimage.idle_restore_minutes = 5\nimage.default_steps = 4\n',
    )

    assert strip_retired_image_keys() == 1

    assert tomllib.loads(path.read_text(encoding="utf-8"))["image"] == {"default_steps": 4}


def test_strip_only_touches_the_key_inside_the_image_table(tmp_hal0_home: str) -> None:
    """A same-named key in another table is not the image setting."""
    path = _write(
        "img",
        'name = "img"\nport = 8188\n\n[server.env]\nidle_restore_minutes = "keep"\n\n'
        "[image]  # keep this comment\nidle_restore_minutes = 60\ndefault_steps = 4\n",
    )

    assert strip_retired_image_keys() == 1

    text = path.read_text(encoding="utf-8")
    # The line edit, not a re-serialisation, did the work: the comment survives.
    assert "# keep this comment" in text
    raw = tomllib.loads(text)
    assert raw["server"]["env"] == {"idle_restore_minutes": "keep"}
    assert raw["image"] == {"default_steps": 4}


def test_strip_skips_an_unreadable_slot_file(tmp_hal0_home: str) -> None:
    bad = _write("bad", "not = [valid")
    img = _write("img", _SHIPPED_IMG_SEED)

    assert strip_retired_image_keys() == 1

    assert bad.read_text(encoding="utf-8") == "not = [valid"
    assert "idle_restore_minutes" not in tomllib.loads(img.read_text(encoding="utf-8"))["image"]


def test_strip_with_no_slots_directory_changes_nothing(tmp_hal0_home: str) -> None:
    assert not os.path.exists(slots_config_dir())
    assert strip_retired_image_keys() == 0


def test_a_slot_file_the_strip_cannot_rewrite_is_a_reported_pass_warning(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each file's failure is caught inside the pass, so only its own event can
    reach hal0 update's summary: it must be one the update reports."""
    from hal0.cli.update_commands import _MIGRATION_PASS_LABELS
    from hal0.updater.updater import _NON_FATAL_MIGRATION_FAILURE_EVENTS

    img = _write("img", _SHIPPED_IMG_SEED)

    def _read_only(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Permission denied", str(img))

    monkeypatch.setattr("hal0.config.migrations.runner_system._replace_file", _read_only)
    with capture_logs() as logs:
        assert strip_retired_image_keys() == 0

    assert "idle_restore_minutes" in img.read_text(encoding="utf-8")
    events = [entry["event"] for entry in logs]
    assert "updater.image_keys_write_failed" in events
    for event in ("updater.image_keys_write_failed", "updater.image_keys_slot_unreadable"):
        assert event in _NON_FATAL_MIGRATION_FAILURE_EVENTS
        assert event in _MIGRATION_PASS_LABELS
    assert "idle_restore_minutes" in _MIGRATION_PASS_LABELS["updater.image_keys_write_failed"]
