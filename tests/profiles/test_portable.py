"""Tests for portable profiles: export envelope + checksum + import.

Mirrors tests/stacks/test_export.py + test_import.py — profiles carry no
models/slots, so there is no embedding/resolve pass to assert.

Targeted file run only (full suite hangs):
    ~/dev/hal0/.venv/bin/python -m pytest tests/profiles/test_portable.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config.schema import PROFILE_SCHEMA_VERSION_CURRENT, ProfileConfig
from hal0.errors import BadRequest, Conflict
from hal0.profiles import ProfileCatalog
from hal0.profiles.portable import (
    ENVELOPE_KIND,
    export_envelope,
    import_profile,
    parse_envelope,
    verify_checksum,
)


def _profile() -> ProfileConfig:
    """A custom (non-seed) profile with a representative mix of fields."""
    return ProfileConfig(
        flags="-fa on",
        mtp=True,
        device_class="gpu",
        backend="rocm",
        cloned_from="vulkan",
        intent="My workload",
        quant="Q5_K_M",
    )


def _catalog(home: str, name: str = "profiles.toml") -> ProfileCatalog:
    """Isolated catalog backed by its own profiles.toml under ``home``."""
    path = Path(home) / "etc" / "hal0" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return ProfileCatalog(path=path)


# ── round-trip ──────────────────────────────────────────────────────────────


class TestRoundTrip:
    def test_export_import_round_trips_fields(self, tmp_hal0_home: str) -> None:
        src = _catalog(tmp_hal0_home, "src.toml")
        src.create("orig", _profile())

        env = export_envelope("orig", _profile(), exported_at="t")

        # Fresh, fully isolated catalog → import under a new name.
        dst = _catalog(tmp_hal0_home, "dst.toml")
        resolved = import_profile(env, "copied", dst)

        assert resolved.name == "copied"
        assert any(p.name == "copied" for p in dst.list())

        imported = dst.resolve("copied")
        original = _profile()
        assert imported.flags == original.flags
        assert imported.mtp == original.mtp
        assert imported.device_class == original.device_class
        assert imported.backend == original.backend
        assert imported.cloned_from == original.cloned_from
        assert imported.intent == original.intent
        assert imported.quant == original.quant


# ── export envelope shape ────────────────────────────────────────────────────


class TestExportEnvelope:
    def test_envelope_shape(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", _profile(), exported_at="2026-06-20T00:00:00Z")
        assert env["kind"] == ENVELOPE_KIND
        assert env["kind"] == "hal0.profile"
        assert env["schema_version"] == PROFILE_SCHEMA_VERSION_CURRENT
        assert env["name"] == "orig"
        assert env["exported_at"] == "2026-06-20T00:00:00Z"
        assert env["checksum"].startswith("sha256:")

    def test_profile_body_has_expected_fields(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", _profile(), exported_at="t")
        body = env["profile"]
        assert body["flags"] == "-fa on"
        assert body["mtp"] is True
        assert body["device_class"] == "gpu"
        assert body["backend"] == "rocm"

    def test_exclude_none_drops_unset_optional_fields(self, tmp_hal0_home: str) -> None:
        # A bare profile leaves backend/cloned_from None → exclude_none drops them.
        env = export_envelope("bare", ProfileConfig(), exported_at="t")
        body = env["profile"]
        assert None not in body.values()
        assert "backend" not in body
        assert "cloned_from" not in body


# ── checksum ─────────────────────────────────────────────────────────────────


class TestVerifyChecksum:
    def test_intact_checksum_verifies(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", _profile(), exported_at="t")
        assert verify_checksum(env) is True

    def test_tampered_body_fails(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", _profile(), exported_at="t")
        env["profile"]["flags"] = "-fa off TAMPERED"
        assert verify_checksum(env) is False

    def test_checksum_is_deterministic_and_ignores_exported_at(self, tmp_hal0_home: str) -> None:
        a = export_envelope("orig", _profile(), exported_at="2026-06-20T00:00:00Z")
        b = export_envelope("orig", _profile(), exported_at="2099-01-01T00:00:00Z")
        assert a["checksum"] == b["checksum"], (
            "checksum must cover the profile body only, not exported_at"
        )

    def test_checksum_is_field_order_independent(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", _profile(), exported_at="t")
        # Rebuild the body dict with keys inserted in reverse order.
        reordered = dict(reversed(list(env["profile"].items())))
        env_reordered = {**env, "profile": reordered}
        assert verify_checksum(env_reordered) is True


# ── parse_envelope ───────────────────────────────────────────────────────────


class TestParseEnvelope:
    def test_valid_envelope_parses(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", _profile(), exported_at="t")
        parsed = parse_envelope(env)
        assert parsed.kind == "hal0.profile"
        assert parsed.profile.flags == "-fa on"

    def test_non_dict_rejected(self, tmp_hal0_home: str) -> None:
        with pytest.raises(BadRequest) as exc:
            parse_envelope("nope")  # type: ignore[arg-type]
        assert exc.value.code == "profiles.bad_envelope"

    def test_wrong_kind_rejected(self, tmp_hal0_home: str) -> None:
        with pytest.raises(BadRequest) as exc:
            parse_envelope({"kind": "not-a-profile", "profile": {}})
        assert exc.value.code == "profiles.bad_envelope"

    def test_missing_profile_rejected(self, tmp_hal0_home: str) -> None:
        with pytest.raises(BadRequest) as exc:
            parse_envelope({"kind": ENVELOPE_KIND})
        assert exc.value.code == "profiles.bad_envelope"


# ── import_profile ───────────────────────────────────────────────────────────


class TestImportProfile:
    def test_too_new_schema_rejected(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", _profile(), exported_at="t")
        env["schema_version"] = PROFILE_SCHEMA_VERSION_CURRENT + 1
        with pytest.raises(BadRequest) as exc:
            import_profile(env, "copied", _catalog(tmp_hal0_home))
        assert exc.value.code == "profiles.envelope_too_new"

    def test_bad_envelope_rejected(self, tmp_hal0_home: str) -> None:
        with pytest.raises(BadRequest) as exc:
            import_profile({"kind": "nope"}, "copied", _catalog(tmp_hal0_home))
        assert exc.value.code == "profiles.bad_envelope"

    def test_duplicate_name_raises_conflict(self, tmp_hal0_home: str) -> None:
        catalog = _catalog(tmp_hal0_home)
        catalog.create("taken", ProfileConfig())
        env = export_envelope("orig", _profile(), exported_at="t")
        with pytest.raises(Conflict) as exc:
            import_profile(env, "taken", catalog)
        assert exc.value.code == "profiles.exists"

    def test_managed_flag_rejected(self, tmp_hal0_home: str) -> None:
        """§21.7: import routes through the catalog write seam, so an envelope
        carrying a hal0-managed flag (-c/--port/…) is rejected — the import
        path must not bypass the screens the create/update routes enforce."""
        env = export_envelope("orig", ProfileConfig(flags="-fa on -c 131072"), exported_at="t")
        with pytest.raises(BadRequest) as exc:
            import_profile(env, "copied", _catalog(tmp_hal0_home))
        assert exc.value.code == "slot.managed_arg_denied"

    def test_slot_hardware_flag_rejected(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", ProfileConfig(flags="-fa on -ngl 999"), exported_at="t")
        with pytest.raises(BadRequest) as exc:
            import_profile(env, "copied", _catalog(tmp_hal0_home))
        assert exc.value.code == "slot.hardware_flag_denied"


# ── the SHIPPED artifact: exact-checksum round-trip guarantee ────────────────

#: The published brain profile, byte-for-byte as it ships in
#: ``Hal0ai/hal0-brain-sft-ROCmFPX-GGUF/chat-long-context.hal0profile.json``.
#: Its ``device_class: "gpu"`` is exactly the field the v1.0 tuning-only rule
#: neutralised, so this artifact is the canary for that change.
_PUBLISHED_CHAT_LONG_CONTEXT: dict = {
    "kind": "hal0.profile",
    "schema_version": 1,
    "hal0_version": "1.0.0a1",
    "name": "chat-long-context",
    "checksum": "sha256:241af4cd2636ac1da32a8a7ca0d856724445242cfcde88a208b702b155bdee47",
    "profile": {
        "flags": (
            "-fa on -ctk q8_0 -ctv q8_0 -b 2048 -ub 512 --parallel 1 --no-mmap "
            "--no-context-shift --poll 100 --poll-batch 1 --metrics --no-webui"
        ),
        "mtp": False,
        "device_class": "gpu",
        "intent": "Long-context chat",
        "quant": "",
    },
}


class TestPublishedBrainProfileRoundTrip:
    """The shipped ``chat-long-context`` artifact must import and round-trip
    with a STABLE checksum (SHARED-BRIEF §"The shipped brain profile").

    This is the hard constraint on the v1.0 tuning-only rule. ``device_class``
    and ``backend`` were neutralised to inert match-only hints, but they could
    NOT be removed from :class:`ProfileConfig`: ``export_envelope`` checksums
    ``model_dump(exclude_none=True)``, so deleting a field the artifact carries
    — or changing which fields serialize — silently changes the checksum and
    breaks every published profile in the wild. These tests fail loudly if
    anyone later "cleans up" those fields.
    """

    def test_published_checksum_verifies(self) -> None:
        assert verify_checksum(_PUBLISHED_CHAT_LONG_CONTEXT) is True

    def test_reexport_reproduces_the_published_checksum_exactly(self) -> None:
        """Parse → re-export must land on the same sha256. ``exported_at`` is
        excluded from the checksum, so a different clock cannot move it."""
        env = parse_envelope(_PUBLISHED_CHAT_LONG_CONTEXT)
        again = export_envelope(env.name, env.profile, exported_at="2099-01-01T00:00:00Z")
        assert again["checksum"] == _PUBLISHED_CHAT_LONG_CONTEXT["checksum"]
        assert again["profile"] == _PUBLISHED_CHAT_LONG_CONTEXT["profile"]

    def test_device_class_gpu_survives_the_round_trip(self) -> None:
        """The inert hint is PRESERVED, not stripped — stripping it would change
        the checksum."""
        env = parse_envelope(_PUBLISHED_CHAT_LONG_CONTEXT)
        assert env.profile.device_class == "gpu"
        body = export_envelope(env.name, env.profile, exported_at="t")["profile"]
        assert body["device_class"] == "gpu"

    def test_published_artifact_imports_and_is_resolvable(self, tmp_hal0_home: str) -> None:
        """A real commit-path import: its flags are all genuine model-tuning
        flags, so it passes the §5 hardware / §21.7 managed screens.

        Imported under a NON-seed name — see
        ``test_cannot_import_under_its_own_name_because_a_seed_owns_it``."""
        catalog = _catalog(tmp_hal0_home)
        resolved = import_profile(_PUBLISHED_CHAT_LONG_CONTEXT, "brain-long-ctx", catalog)
        assert resolved.name == "brain-long-ctx"
        assert resolved.device_class == "gpu"
        assert "--no-webui" in resolved.flags

    def test_cannot_import_under_its_own_name_because_a_seed_owns_it(
        self, tmp_hal0_home: str
    ) -> None:
        """KNOWN LIMITATION, pinned so it is not mistaken for a regression.

        A SEED profile is also named ``chat-long-context``, and it is a
        DIFFERENT profile — the seed's flags are the short
        ``-fa on -ctk q8_0 -ctv q8_0 -b 2048 -ub 512 --no-context-shift`` with
        ``device_class`` unset, so it checksums to a different sha256 than the
        published artifact. Importing the published file under its own name
        therefore 409s, and even if it did not, ``save_profiles_config`` strips
        seed-named keys before writing, so it could never persist there. An
        operator must import it under a different name.
        """
        catalog = _catalog(tmp_hal0_home)
        with pytest.raises(Conflict) as exc:
            import_profile(_PUBLISHED_CHAT_LONG_CONTEXT, "chat-long-context", catalog)
        assert exc.value.code == "profiles.exists"

    def test_imported_then_exported_still_matches_published(self, tmp_hal0_home: str) -> None:
        """Full loop through the on-disk catalog: import → persist → resolve →
        export reproduces the published checksum. Catches a catalog write that
        drops or coerces a field."""
        catalog = _catalog(tmp_hal0_home)
        import_profile(_PUBLISHED_CHAT_LONG_CONTEXT, "brain-long-ctx", catalog)
        r = catalog.resolve("brain-long-ctx")
        again = export_envelope(
            "brain-long-ctx",
            ProfileConfig(
                flags=r.flags,
                mtp=r.mtp,
                device_class=r.device_class,
                backend=r.backend,
                cloned_from=r.cloned_from,
                intent=r.intent,
                quant=r.quant,
            ),
            exported_at="t",
        )
        assert again["checksum"] == _PUBLISHED_CHAT_LONG_CONTEXT["checksum"]

    def test_published_flags_carry_no_hardware_or_managed_flag(self) -> None:
        """Why the artifact survives the tuning-only rule at all: every entry in
        its ``flags`` is a model-tuning flag. Guards against a future seed/screen
        widening that would strand the shipped profile."""
        flags = _PUBLISHED_CHAT_LONG_CONTEXT["profile"]["flags"]
        for banned in ("-ngl", "--device", "--threads", "-dev", "-c ", "--ctx-size", "--port"):
            assert banned not in flags
