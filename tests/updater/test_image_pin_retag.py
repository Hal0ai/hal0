"""#1948 B1 — a folded box pins the runner in `image_pin`, not `image`.

`hal0 slot migrate-hw --apply` folds a bare `image` string into the typed
`image_pin` field, recording the then-current default as a "deliberate pin"
because it was not yet in STALE_RUNNER_IMAGE_REFS. A sweep that reads only the
bare key is blind to that, so the slot would keep serving a retired runner
forever — behind the release that replaces it.
"""

from __future__ import annotations

import pytest

from hal0.config.schema import DEFAULT_ROCMFPX_IMAGE
from hal0.updater.updater import retag_stale_slot_images

RETIRED = "ghcr.io/hal0ai/hal0-rocmfpx:ade07ba"


@pytest.fixture
def slots_dir(tmp_path, monkeypatch):
    d = tmp_path / "slots"
    d.mkdir()
    monkeypatch.setattr("hal0.config.paths.slots_config_dir", lambda: d)
    return d


def test_a_stale_image_pin_is_retagged(slots_dir) -> None:
    (slots_dir / "brain.toml").write_text(
        f'device = "gpu-rocm"\nimage_pin = "{RETIRED}"\n', encoding="utf-8"
    )
    assert retag_stale_slot_images() == 1
    body = (slots_dir / "brain.toml").read_text(encoding="utf-8")
    assert RETIRED not in body
    assert DEFAULT_ROCMFPX_IMAGE in body


def test_a_nested_stale_image_pin_is_retagged(slots_dir) -> None:
    (slots_dir / "agent.toml").write_text(
        f'[slot]\ndevice = "gpu-rocm"\nimage_pin = "{RETIRED}"\n', encoding="utf-8"
    )
    assert retag_stale_slot_images() == 1
    assert RETIRED not in (slots_dir / "agent.toml").read_text(encoding="utf-8")


def test_the_bare_image_key_still_works(slots_dir) -> None:
    """The original shape must not regress while adding the typed one."""
    (slots_dir / "code.toml").write_text(
        f'device = "gpu-rocm"\nimage = "{RETIRED}"\n', encoding="utf-8"
    )
    assert retag_stale_slot_images() == 1
    assert RETIRED not in (slots_dir / "code.toml").read_text(encoding="utf-8")


def test_a_deliberate_image_pin_is_never_touched(slots_dir) -> None:
    """The stale-set match is the whole safety property: an operator's own pin
    is never an exact former default, so a debug build must survive."""
    (slots_dir / "brain.toml").write_text(
        'device = "gpu-rocm"\nimage_pin = "ghcr.io/example/my-debug-build:v9"\n',
        encoding="utf-8",
    )
    assert retag_stale_slot_images() == 0
    assert "my-debug-build:v9" in (slots_dir / "brain.toml").read_text(encoding="utf-8")


def test_it_is_idempotent(slots_dir) -> None:
    (slots_dir / "brain.toml").write_text(
        f'device = "gpu-rocm"\nimage_pin = "{RETIRED}"\n', encoding="utf-8"
    )
    assert retag_stale_slot_images() == 1
    assert retag_stale_slot_images() == 0


def test_both_keys_present_the_pin_wins_like_the_resolver(slots_dir) -> None:
    """With BOTH key shapes on one slot, the sweep must judge the key the
    runtime serves. ``_resolve_image_ref`` reads ``image_pin`` first and no
    longer reads the bare key at all — so a stale pin behind a current-looking
    bare ``image`` was invisible to a bare-key-first sweep, and the slot kept
    serving the retired runner behind the release replacing it (#1959 review
    nit; the inverse ordering also logged retags of a value nothing reads)."""
    (slots_dir / "agent.toml").write_text(
        f'device = "gpu-rocm"\nimage = "{DEFAULT_ROCMFPX_IMAGE}"\nimage_pin = "{RETIRED}"\n',
        encoding="utf-8",
    )
    assert retag_stale_slot_images() == 1
    after = (slots_dir / "agent.toml").read_text(encoding="utf-8")
    assert RETIRED not in after
    assert f'image_pin = "{DEFAULT_ROCMFPX_IMAGE}"' in after


def test_both_keys_with_a_deliberate_pin_is_untouched(slots_dir) -> None:
    """The mirror case: a deliberate (non-stale) pin with stale bare-key
    debris behind it. The runtime serves the pin, which is fine — retagging
    the inert bare key would log a retag that changes nothing served."""
    (slots_dir / "code.toml").write_text(
        f'device = "gpu-rocm"\nimage = "{RETIRED}"\n'
        'image_pin = "ghcr.io/example/my-debug-build:v9"\n',
        encoding="utf-8",
    )
    before = (slots_dir / "code.toml").read_text(encoding="utf-8")
    assert retag_stale_slot_images() == 0
    assert (slots_dir / "code.toml").read_text(encoding="utf-8") == before
