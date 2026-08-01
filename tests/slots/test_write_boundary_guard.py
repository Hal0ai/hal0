"""Unit tests for the in-process slot-write boundary (`guard_slot_write_payload`).

The HTTP slot-config routes have always enforced the slot/model key partition
(``routes/slots._reject_model_owned_config_keys`` +
``_reject_unknown_config_keys``). Nothing enforced it for a writer that reaches
slot TOML IN-PROCESS without passing a FastAPI handler — which is exactly what
the stacks apply engine does. ``reconcile_and_guard_slot_config`` was named
"…and_guard…" but checked only two cross-slot invariants (NPU exclusivity,
default uniqueness), so ``POST /api/stacks/{slug}/apply`` could persist keys
``PUT /api/slots/{name}/config`` 400s on.

These pin the guard at that shared seam. The end-to-end proof that a stack apply
can no longer land them lives in ``tests/api/test_stacks_routes.py``.

Targeted file run:
    uv run pytest tests/slots/test_write_boundary_guard.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.errors import BadRequest
from hal0.slot_config import MODEL_OWNED_SLOT_KEYS
from hal0.slots.config_write import (
    guard_slot_write_payload,
    reconcile_and_guard_slot_config,
)


def _slots_dir(home: str) -> Path:
    d = Path(home) / "etc" / "hal0" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── the partition itself ─────────────────────────────────────────────────────


@pytest.mark.parametrize("key", sorted(MODEL_OWNED_SLOT_KEYS))
def test_guard_rejects_every_model_owned_key(key: str) -> None:
    """All three of ``mtp`` / ``enable_thinking`` / ``vision`` are refused.

    Parametrized off ``MODEL_OWNED_SLOT_KEYS`` rather than a hand-written list,
    so a fourth key added to the partition is covered without touching this
    test.
    """
    with pytest.raises(BadRequest) as exc:
        guard_slot_write_payload({key: True})
    assert exc.value.code == "slot.model_owned_key_denied"
    assert exc.value.details["keys"] == [key]


def test_guard_rejects_a_removed_key() -> None:
    """``enabled`` (#1369) gets its migration pointer, not a spelling hint."""
    with pytest.raises(BadRequest) as exc:
        guard_slot_write_payload({"enabled": False})
    assert exc.value.code == "slot.removed_key_denied"


def test_guard_reports_model_owned_before_removed() -> None:
    """Ordering matches the routes: the partition message wins."""
    with pytest.raises(BadRequest) as exc:
        guard_slot_write_payload({"enabled": False, "mtp": True})
    assert exc.value.code == "slot.model_owned_key_denied"


def test_guard_allows_a_legitimate_payload() -> None:
    """The guard must not become a general-purpose schema check.

    It owns three partitions and nothing else — an ordinary device/profile/model
    write passes untouched, and so does an unrecognised key (that is
    ``unknown_slot_config_keys``' job at the HTTP boundary).
    """
    guard_slot_write_payload(
        {
            "device": "gpu-rocm",
            "profile": "moe",
            "model": {"default": "m", "context_size": 8192},
            "server": {"extra_args": "--jinja -fa on"},
        }
    )


def test_guard_ignores_a_nested_model_owned_lookalike() -> None:
    """The partition is TOP-LEVEL only.

    ``[model].vision`` is the model's own field — rejecting it here would refuse
    the very location the error message tells the operator to use.
    """
    guard_slot_write_payload({"model": {"default": "m", "vision": True}})


# ── freeform [server].extra_args screen ──────────────────────────────────────


@pytest.mark.parametrize(
    "flags", ["-ngl 99", "--n-gpu-layers 99", "--threads 8", "-t 8", "-dev cpu"]
)
def test_guard_rejects_hardware_flags_in_extra_args(flags: str) -> None:
    """Grid-owned hardware flags get the "belongs on the slot" message.

    Both spellings are caught: ``_split_pairs`` canonicalises the short forms
    onto their long partners.
    """
    with pytest.raises(BadRequest) as exc:
        guard_slot_write_payload({"server": {"extra_args": flags}})
    assert exc.value.code == "slot.hardware_flag_denied"


@pytest.mark.parametrize("flags", ["--port 9999", "--model /tmp/x.gguf", "--host 0.0.0.0"])
def test_guard_rejects_managed_flags_in_extra_args(flags: str) -> None:
    """hal0-computed structural flags can't be smuggled through extra_args."""
    with pytest.raises(BadRequest) as exc:
        guard_slot_write_payload({"server": {"extra_args": flags}})
    assert exc.value.code == "slot.managed_arg_denied"


def test_hardware_flags_are_reported_before_managed_ones() -> None:
    """``-ngl`` is in BOTH denylists — the actionable message must win.

    Mirrors the ordering in ``models_service.screen_model_write`` and
    ``routes/profiles._screen_profile_flags``, so all three surfaces render one
    story for the same flag.
    """
    with pytest.raises(BadRequest) as exc:
        guard_slot_write_payload({"server": {"extra_args": "-ngl 99 --port 1"}})
    assert exc.value.code == "slot.hardware_flag_denied"


@pytest.mark.parametrize("value", ["", "   ", None, 42, ["-ngl", "99"]])
def test_extra_args_screen_no_ops_on_non_string_or_blank(value: object) -> None:
    """Blank / absent / non-string extra_args is not this guard's error to raise.

    ``ServerConfig.extra_args`` is typed ``str | None``; a wrong type is the
    schema layer's 422. A list is deliberately NOT screened here so the guard
    can never half-inspect a shape it doesn't own.
    """
    guard_slot_write_payload({"server": {"extra_args": value}})


def test_unparseable_quoting_defers_to_the_schema_layer() -> None:
    """A malformed flag string is not masked with a partition message."""
    guard_slot_write_payload({"server": {"extra_args": '--jinja "unclosed'}})


# ── placement: the guard sees the PAYLOAD, never the merged result ────────────


def test_legacy_key_already_on_disk_does_not_block_an_unrelated_write(
    tmp_hal0_home: str,
) -> None:
    """A pre-partition slot TOML stays writable.

    Guarding the MERGED config instead of the payload would brick every legacy
    box: a slot carrying ``mtp = true`` from before the partition would refuse
    every subsequent edit, including the model swap that is the whole point.
    Converging the old shape is ``config.migrations.model_owned_caps``' job.
    """
    slots = _slots_dir(tmp_hal0_home)
    (slots / "agent.toml").write_text(
        'name = "agent"\nport = 8087\nmtp = true\nvision = true\n', encoding="utf-8"
    )
    before = {"name": "agent", "port": 8087, "mtp": True, "vision": True}

    merged = reconcile_and_guard_slot_config(
        "agent", before, {"model": {"default": "new"}}, slots_dir=slots
    )

    assert merged["model"]["default"] == "new"
    assert merged["mtp"] is True, "the legacy value survives; the guard didn't fire"


def test_reconcile_and_guard_refuses_a_model_owned_write(tmp_hal0_home: str) -> None:
    """The shared pipeline is where the guard lives, not the stacks call site.

    Placing it here means every future in-process writer that routes through
    ``reconcile_and_guard_slot_config`` inherits the partition by construction —
    that function's stated contract is "a writer can no longer persist what
    ``update_config`` would refuse".
    """
    slots = _slots_dir(tmp_hal0_home)
    (slots / "agent.toml").write_text('name = "agent"\nport = 8087\n', encoding="utf-8")
    with pytest.raises(BadRequest) as exc:
        reconcile_and_guard_slot_config(
            "agent", {"name": "agent", "port": 8087}, {"mtp": True}, slots_dir=slots
        )
    assert exc.value.code == "slot.model_owned_key_denied"


def test_guard_runs_before_the_merge(tmp_hal0_home: str) -> None:
    """A rejected write leaves the caller's ``base`` dict untouched.

    The guard is the first statement in the pipeline, so a violation costs no
    merge work and can't leave a half-projected dict behind for a caller that
    catches the error and carries on (which ``StackApplyEngine.plan`` does).
    """
    slots = _slots_dir(tmp_hal0_home)
    (slots / "agent.toml").write_text('name = "agent"\nport = 8087\n', encoding="utf-8")
    base = {"name": "agent", "port": 8087, "model": {"default": "old"}}
    snapshot = {"name": "agent", "port": 8087, "model": {"default": "old"}}
    with pytest.raises(BadRequest):
        reconcile_and_guard_slot_config(
            "agent", base, {"model": {"default": "new"}, "vision": True}, slots_dir=slots
        )
    assert base == snapshot, "base must never be mutated, rejected or not"
