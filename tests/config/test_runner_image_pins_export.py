"""``exports/runner-image-pins.json`` stays in lockstep with schema.py.

The retention sweep in ``Hal0ai/hal0-runner-images`` (``scripts/retention.py``)
must never delete a GHCR image ref that shipped hal0 code still pulls. Its
``retention-allowlist.json`` ``hal0_code_pins`` list used to be synced by hand
against the constants in :mod:`hal0.config.schema`; now the retention side
fetches ``exports/runner-image-pins.json`` from raw.githubusercontent instead,
and THIS test is the sync mechanism on the hal0 side: bump a pinned image
constant and this test fails until the export file is regenerated (the failure
message prints the exact expected file content — paste it over the file).

Membership rule (why these constants and not the others):

* ``DEFAULT_ROCMFPX_IMAGE`` — the default runner every fresh install pulls.
* ``VULKAN_CAPABLE_IMAGE_REFS`` — the only refs the ``gpu-vulkan`` lane
  preflight admits; delete one and every slot on that lane bricks.
* ``DEFAULT_PROMPTFORGE_IMAGE`` — the bundled fallback for the optional
  promptforge runner (#2132). Its package (``hal0-promptforge``) is inside the
  retention sweep's blast radius, and its tag shape is not release-shaped, so
  the allowlist is its only durable protection once the runner-images
  ``images.json`` entry moves on.

Deliberately excluded: ``STALE_ROCMFPX_IMAGE_REFS`` and
``NATIVE_TOOL_INCOMPATIBLE_IMAGE_REFS`` (string-comparison retag/deny lists —
hal0 never pulls them, so retention deleting them is harmless), and the two
``FALLBACK_*`` images (their packages are outside the sweep: retention only
touches ``hal0-``-prefixed packages in the hal0ai org).
"""

from __future__ import annotations

import json
from pathlib import Path

from hal0.config.schema import (
    DEFAULT_PROMPTFORGE_IMAGE,
    DEFAULT_ROCMFPX_IMAGE,
    VULKAN_CAPABLE_IMAGE_REFS,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXPORT_PATH = _REPO_ROOT / "exports" / "runner-image-pins.json"


def _expected_export_text() -> str:
    pins = sorted(
        {DEFAULT_ROCMFPX_IMAGE, DEFAULT_PROMPTFORGE_IMAGE} | set(VULKAN_CAPABLE_IMAGE_REFS)
    )
    doc = {"source": "src/hal0/config/schema.py", "pins": pins}
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def test_export_file_matches_schema_constants() -> None:
    expected = _expected_export_text()
    actual = _EXPORT_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "exports/runner-image-pins.json is out of date with the image pin "
        "constants in src/hal0/config/schema.py. The retention automation in "
        "Hal0ai/hal0-runner-images fetches this file to build its "
        "never-delete allowlist, so it must be regenerated whenever a pin "
        "changes. Replace the file's content with exactly this:\n\n"
        f"{expected}"
    )


def test_export_is_valid_deterministic_json() -> None:
    # The consumer json.loads() this over raw.githubusercontent — assert the
    # contract shape independently of the constants' current values.
    doc = json.loads(_EXPORT_PATH.read_text(encoding="utf-8"))
    assert set(doc) == {"source", "pins"}
    assert doc["source"] == "src/hal0/config/schema.py"
    assert isinstance(doc["pins"], list)
    assert doc["pins"] == sorted(doc["pins"])
    assert len(doc["pins"]) == len(set(doc["pins"]))
    for ref in doc["pins"]:
        assert ref.startswith("ghcr.io/hal0ai/"), ref
