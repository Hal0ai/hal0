"""``exports/runner-image-pins.json`` stays in lockstep with ``manifest.json``.

The retention sweep in ``Hal0ai/hal0-runner-images`` must never delete a GHCR
image ref that shipped hal0 code still pulls. It fetches
``exports/runner-image-pins.json`` from raw.githubusercontent as its
never-delete allowlist, and THIS test is the sync mechanism on the hal0 side:
change a pin in the ``runners`` table of ``manifest.json`` and this test fails
until the export is regenerated (``python scripts/check_runner_pins.py
--export > exports/runner-image-pins.json``; the failure message prints the
exact expected file content).

The export is :func:`hal0.runners.pins.export_payload`: every PUBLISHED pin's
full ref (``repo:tag@digest``), sorted. Unpublished rows (``tag: null``) have
nothing to retain and are omitted.
"""

from __future__ import annotations

import json
from pathlib import Path

from hal0.runners.pins import export_payload, load_runner_pins

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXPORT_PATH = _REPO_ROOT / "exports" / "runner-image-pins.json"


def _expected_export_text() -> str:
    pins = load_runner_pins(_REPO_ROOT / "manifest.json")
    return json.dumps(export_payload(pins), indent=2) + "\n"


def test_export_file_matches_the_manifest_pins() -> None:
    expected = _expected_export_text()
    actual = _EXPORT_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "exports/runner-image-pins.json is out of date with the `runners` pin "
        "table in manifest.json. The retention automation in "
        "Hal0ai/hal0-runner-images fetches this file to build its "
        "never-delete allowlist, so it must be regenerated whenever a pin "
        "changes. Replace the file's content with exactly this:\n\n"
        f"{expected}"
    )


def test_export_is_valid_deterministic_json() -> None:
    # The consumer json.loads() this over raw.githubusercontent — assert the
    # contract shape independently of the pins' current values.
    doc = json.loads(_EXPORT_PATH.read_text(encoding="utf-8"))
    assert set(doc) == {"source", "pins"}
    assert doc["source"] == "manifest.json"
    assert isinstance(doc["pins"], list)
    assert doc["pins"] == sorted(doc["pins"])
    assert len(doc["pins"]) == len(set(doc["pins"]))
    for ref in doc["pins"]:
        assert ref.startswith(("ghcr.io/hal0ai/", "ghcr.io/ggml-org/")), ref


def test_every_published_pin_is_exported_and_nothing_else() -> None:
    pins = load_runner_pins(_REPO_ROOT / "manifest.json")
    doc = json.loads(_EXPORT_PATH.read_text(encoding="utf-8"))
    assert doc["pins"] == sorted(pin.ref for pin in pins.values() if pin.published)
