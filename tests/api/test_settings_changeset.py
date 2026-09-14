"""One ChangeSet for preview and apply — #1967, #2195, #2203, #1511 fixtures.

``compute_settings_changeset`` (``hal0.api._settings_changeset``) is the one
function both ``POST /api/settings/preview`` and ``PUT /api/settings`` call.
These tests exercise it at the HTTP layer with fixtures shaped after four
bugs found in adjacent parts of the codebase — none of which are touched by
this suite; they're reproduced here as scenarios the general settings
surface must not repeat:

  * #2203 — a route emitted a hardcoded ``changed_fields`` list even for a
    no-op re-stamp. Here: re-PUTting an identical value must produce an
    EMPTY changeset for that key, not a phantom "changed" entry.
  * #1967 — a round-trip silently dropped keys nobody told it to keep.
    Here: a validator's silent normalisation (``tool_model``'s empty-string
    coercion) must still surface as a real changeset entry, because the
    diff runs over the actual post-validation values, not the caller's
    touched-key list.
  * #2195 — a preview billed added/changed flags but never removed ones.
    Here: clearing a ``[slots].default_images`` family via the documented
    null-idiom must appear in the changeset as a ``"removed"`` entry.
  * #1511 — a preview and its apply read different projections of the same
    operation. Here: preview and apply, given the same body against the
    same starting config, must produce byte-identical changesets.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app


@pytest.fixture
def isolated_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


def test_preview_and_apply_report_the_same_changeset(isolated_client: TestClient) -> None:
    """#1511: preview cannot show something apply wouldn't do — same body,
    same starting config, byte-identical changeset."""
    body = {"telemetry": {"enabled": True}, "slots": {"idle_timeout_s": 120}}

    preview = isolated_client.post("/api/settings/preview", json=body)
    assert preview.status_code == 200, preview.text
    preview_changeset = preview.json()["changeset"]

    apply = isolated_client.put("/api/settings", json=body)
    assert apply.status_code == 200, apply.text
    apply_changeset = apply.json()["_hal0"]["changeset"]

    assert preview_changeset == apply_changeset
    assert preview_changeset["changes"], "expected at least one real change"


def test_preview_does_not_write_to_disk(isolated_client: TestClient) -> None:
    """A preview is a dry run — hal0.toml must be untouched afterwards."""
    isolated_client.post("/api/settings/preview", json={"telemetry": {"enabled": True}})
    r = isolated_client.get("/api/settings")
    assert r.json()["telemetry"]["enabled"] is False  # still the shipped default


def test_reapplying_the_same_value_produces_no_changes(isolated_client: TestClient) -> None:
    """#2203 shape: a route that hardcodes 'changed' even for a no-op
    re-stamp lies about what happened. Applying an already-current value
    a second time must report an EMPTY changeset for that key."""
    isolated_client.put("/api/settings", json={"telemetry": {"enabled": True}})

    r = isolated_client.put("/api/settings", json={"telemetry": {"enabled": True}})
    assert r.status_code == 200, r.text
    changes = r.json()["_hal0"]["changeset"]["changes"]
    assert changes == [], f"re-applying an unchanged value must diff to nothing, got {changes}"
    # The touched-keys apply_plan bucket still fires (back-compat contract,
    # tests/api/test_settings_apply.py) — only the stricter changeset is empty.
    assert r.json()["_hal0"]["apply_plan"]["immediate"] == ["telemetry.enabled"]


def test_validator_normalisation_surfaces_as_a_real_change(isolated_client: TestClient) -> None:
    """#1967 shape: a lossy round-trip silently drops what the caller
    thought it was setting. Here the opposite failure mode is guarded
    against: BrainChatConfig._normalise_tool_model coerces an explicit
    empty string back to the default (schema.py's tool_model validator) —
    the changeset must show the REAL post-validation value, not silently
    report "no change" or echo the caller's raw empty string as `after`."""
    isolated_client.put("/api/settings", json={"brain_chat": {"tool_model": "off"}})

    r = isolated_client.put("/api/settings", json={"brain_chat": {"tool_model": ""}})
    assert r.status_code == 200, r.text
    changes = {c["path"]: c for c in r.json()["_hal0"]["changeset"]["changes"]}
    assert "brain_chat.tool_model" in changes
    change = changes["brain_chat.tool_model"]
    assert change["before"] == "off"
    # Coerced back to the default — never the raw "" the caller sent.
    assert change["after"] == "hal0/agent"
    assert change["kind"] == "changed"


def test_default_images_null_clear_reports_a_removal(isolated_client: TestClient) -> None:
    """#2195 shape: a preview that bills additions/changes but not removals
    under-reports the real delta. Clearing one family via the documented
    null-idiom (schema.py's `_default_images_known_families` validator)
    must appear in the changeset as a ``"removed"`` entry, not be silently
    absent or folded into a same-key "changed" that hides the family name."""
    isolated_client.put(
        "/api/settings", json={"slots": {"default_images": {"rocmfpx": "ghcr.io/hal0ai/hal0:test"}}}
    )

    r = isolated_client.put("/api/settings", json={"slots": {"default_images": {"rocmfpx": None}}})
    assert r.status_code == 200, r.text
    changes = {c["path"]: c for c in r.json()["_hal0"]["changeset"]["changes"]}
    assert "slots.default_images.rocmfpx" in changes
    change = changes["slots.default_images.rocmfpx"]
    assert change["kind"] == "removed"
    assert change["after"] is None
    assert change["before"] == "ghcr.io/hal0ai/hal0:test"


def test_unknown_touched_key_is_classified_unknown_not_dropped(isolated_client: TestClient) -> None:
    """A forward-compat top-level key (Hal0Config's extra="allow") has no
    apply-plan registry entry — it must land in the changeset with
    apply_class None / kind "added", never be silently dropped."""
    r = isolated_client.put("/api/settings", json={"totally_new_field": {"foo": "bar"}})
    assert r.status_code == 200, r.text
    changes = {c["path"]: c for c in r.json()["_hal0"]["changeset"]["changes"]}
    assert "totally_new_field.foo" in changes
    assert changes["totally_new_field.foo"]["kind"] == "added"
    assert changes["totally_new_field.foo"]["apply_class"] is None


def test_preview_validation_error_matches_put_envelope(isolated_client: TestClient) -> None:
    """Preview and apply share one validation path — an invalid patch fails
    the same way on both, with the same error code and field-path details."""
    body = {"telemetry": {"channel": "nonsense"}}

    preview = isolated_client.post("/api/settings/preview", json=body)
    apply = isolated_client.put("/api/settings", json=body)

    assert preview.status_code == 400, preview.text
    assert apply.status_code == 400, apply.text
    assert preview.json()["error"]["code"] == "config.invalid"
    assert apply.json()["error"]["code"] == "config.invalid"
    assert preview.json()["error"]["details"].keys() == apply.json()["error"]["details"].keys()


def test_secret_leaf_is_redacted_in_the_changeset(isolated_client: TestClient) -> None:
    """A sensitive-named leaf's before/after must be masked in the
    changeset the same way GET /api/settings redacts it (#553) — a
    forward-compat extra key is the only Hal0Config-reachable path that can
    carry a secret-shaped name today."""
    r = isolated_client.put("/api/settings", json={"integration_api_key": "sk-super-secret"})
    assert r.status_code == 200, r.text
    changes = {c["path"]: c for c in r.json()["_hal0"]["changeset"]["changes"]}
    assert "integration_api_key" in changes
    after = changes["integration_api_key"]["after"]
    assert after != "sk-super-secret"
    assert "sk-super-secret" not in str(after)
