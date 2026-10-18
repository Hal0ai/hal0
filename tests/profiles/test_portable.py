"""Tests for portable profiles: export envelope + checksum + import.

Mirrors tests/stacks/test_export.py + test_import.py — profiles carry no
models/slots, so there is no embedding/resolve pass to assert.

Targeted file run only (full suite hangs):
    ~/dev/hal0/.venv/bin/python -m pytest tests/profiles/test_portable.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.config.schema import PROFILE_SCHEMA_VERSION_CURRENT, ProfileConfig
from hal0.errors import BadRequest, Conflict
from hal0.profiles import ProfileCatalog
from hal0.profiles.portable import (
    ENVELOPE_KIND,
    envelope_runner_status,
    export_envelope,
    import_profile,
    parse_envelope,
    verify_checksum,
)


def _profile() -> ProfileConfig:
    """A custom (non-seed) profile with a representative mix of fields."""
    return ProfileConfig(
        flags="-fa on",
        ctx_size=65536,
        enable_thinking=True,
        vision="off",
        speculation="off",
        cloned_from="chat",
        intent="My workload",
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
        assert imported.ctx_size == original.ctx_size == 65536
        assert imported.enable_thinking is True
        assert imported.vision == "off"
        assert imported.speculation == "off"
        assert imported.cloned_from == original.cloned_from
        assert imported.intent == original.intent

    def test_export_import_round_trips_runner(self, tmp_hal0_home: str) -> None:
        env = export_envelope(
            "pf-port", ProfileConfig(flags="-fa on", runner="cuda"), exported_at="t"
        )
        assert env["profile"]["runner"] == "cuda"

        dst = _catalog(tmp_hal0_home, "dst-runner.toml")
        resolved = import_profile(env, "pf-port-copy", dst)
        assert dst.resolve(resolved.name).runner == "cuda"


# ── export envelope shape ────────────────────────────────────────────────────


class TestExportEnvelope:
    def test_envelope_shape(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", _profile(), exported_at="2026-06-20T00:00:00Z")
        assert env["kind"] == ENVELOPE_KIND
        assert env["kind"] == "hal0.profile"
        assert env["schema_version"] == PROFILE_SCHEMA_VERSION_CURRENT == 2
        assert env["name"] == "orig"
        assert env["exported_at"] == "2026-06-20T00:00:00Z"
        assert env["checksum"].startswith("sha256:")

    def test_profile_body_has_expected_fields(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", _profile(), exported_at="t")
        body = env["profile"]
        assert body["flags"] == "-fa on"
        assert body["ctx_size"] == 65536
        assert body["enable_thinking"] is True
        assert body["vision"] == "off"
        assert body["speculation"] == "off"
        assert body["cloned_from"] == "chat"

    def test_body_carries_none_of_the_retired_fields(self, tmp_hal0_home: str) -> None:
        body = export_envelope("orig", _profile(), exported_at="t")["profile"]
        assert not {"device_class", "backend", "quant"} & set(body)

    def test_exclude_none_drops_unset_optional_fields(self, tmp_hal0_home: str) -> None:
        # A bare profile leaves ctx_size/enable_thinking/runner/cloned_from None
        # → exclude_none drops them; the auto/auto switches stay.
        env = export_envelope("bare", ProfileConfig(), exported_at="t")
        body = env["profile"]
        assert None not in body.values()
        assert "ctx_size" not in body
        assert "enable_thinking" not in body
        assert "cloned_from" not in body
        assert body["vision"] == "auto"
        assert body["speculation"] == "auto"


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
        assert parsed.profile.ctx_size == 65536

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

    def test_out_of_range_ctx_size_rejected(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", _profile(), exported_at="t")
        env["profile"]["ctx_size"] = 64
        with pytest.raises(BadRequest) as exc:
            parse_envelope(env)
        assert exc.value.code == "profiles.bad_envelope"


# ── schema 1 is refused (ADR-0029) ───────────────────────────────────────────

#: A schema-1 envelope as 1.4 exported it (boolean ``mtp``, ``device_class``,
#: ``backend``, ``quant``).
_SCHEMA_1_ENVELOPE: dict = {
    "kind": "hal0.profile",
    "schema_version": 1,
    "hal0_version": "1.4.0",
    "name": "old-tune",
    "checksum": "sha256:241af4cd2636ac1da32a8a7ca0d856724445242cfcde88a208b702b155bdee47",
    "profile": {
        "flags": "--jinja -fa on -ctk q8_0 -ctv q8_0 --no-mmproj",
        "mtp": False,
        "device_class": "gpu",
        "intent": "Long-context chat",
        "quant": "",
    },
}


class TestSchema1Refused:
    def test_parse_refuses_a_schema_1_envelope(self) -> None:
        with pytest.raises(BadRequest) as exc:
            parse_envelope(_SCHEMA_1_ENVELOPE)
        assert exc.value.code == "profiles.envelope_schema_unsupported"
        assert "re-export from hal0 1.5" in str(exc.value)
        assert exc.value.details["got"] == 1

    def test_import_refuses_a_schema_1_envelope_and_writes_nothing(
        self, tmp_hal0_home: str
    ) -> None:
        catalog = _catalog(tmp_hal0_home)
        with pytest.raises(BadRequest) as exc:
            import_profile(_SCHEMA_1_ENVELOPE, "old-tune", catalog)
        assert exc.value.code == "profiles.envelope_schema_unsupported"
        assert not any(p.name == "old-tune" for p in catalog.list())

    def test_a_schema_1_envelope_is_refused_before_its_body_is_validated(self) -> None:
        """The refusal names the schema, not a pydantic complaint about
        ``device_class`` — and the body is never silently stripped."""
        with pytest.raises(BadRequest) as exc:
            parse_envelope({**_SCHEMA_1_ENVELOPE, "profile": {"flags": "", "quant": "Q4"}})
        assert exc.value.code == "profiles.envelope_schema_unsupported"


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

    def test_model_fact_flag_rejected(self, tmp_hal0_home: str) -> None:
        """ADR-0029: --jinja / --no-mmproj are the model's and the ``vision``
        switch's; an envelope carrying them is refused, not stripped."""
        for flags in ("--jinja -fa on", "--no-mmproj", "--chat-template-file x.jinja"):
            env = export_envelope("orig", ProfileConfig(flags=flags), exported_at="t")
            with pytest.raises(BadRequest) as exc:
                import_profile(env, "copied", _catalog(tmp_hal0_home))
            assert exc.value.code == "profiles.model_fact_flag_denied", flags

    def test_unknown_runner_is_stripped_not_rejected(self, tmp_hal0_home: str) -> None:
        """Import STRIPS a runtime this box does not have, it does not block.

        A portable profile authored on a richer box must still land — its
        flags and tune are the point — but it lands as Auto rather than
        carrying a key this box cannot honour. ``screen_profile_runner``
        stays strict for create/update; only import substitutes.
        """
        env = export_envelope(
            "orig", ProfileConfig(flags="-fa on", ctx_size=32768, runner="nope"), exported_at="t"
        )
        assert verify_checksum(env) is True
        resolved = import_profile(env, "copied", _catalog(tmp_hal0_home))
        assert resolved.runner is None
        # Everything else crosses unchanged.
        assert resolved.flags == "-fa on"
        assert resolved.ctx_size == 32768

    def test_known_runner_survives_import(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", ProfileConfig(flags="", runner="standard"), exported_at="t")
        resolved = import_profile(env, "copied", _catalog(tmp_hal0_home))
        assert resolved.runner == "standard"

    def test_envelope_runner_status_reports_the_substitution(self) -> None:
        assert envelope_runner_status(ProfileConfig()) == (None, False)
        assert envelope_runner_status(ProfileConfig(runner="standard")) == ("standard", False)
        assert envelope_runner_status(ProfileConfig(runner="nope")) == (None, True)


# ── #1416: the COMMIT path verifies the checksum, not just dry_run ───────────


class TestImportVerifiesChecksum:
    """``POST /api/profiles/import`` computed ``checksum_ok`` on a dry run and
    then committed without ever checking it — the integrity signal was advisory
    only. A tampered envelope imported silently."""

    def test_tampered_body_is_rejected_on_commit(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", _profile(), exported_at="t")
        env["profile"]["intent"] = "SOMETHING ELSE"  # checksum now stale
        assert verify_checksum(env) is False
        with pytest.raises(BadRequest) as exc:
            import_profile(env, "copied", _catalog(tmp_hal0_home))
        assert exc.value.code == "profiles.checksum_mismatch"

    def test_tampered_flags_are_rejected_before_anything_is_written(
        self, tmp_hal0_home: str
    ) -> None:
        """The rejection must happen BEFORE ``catalog.create`` — otherwise the
        corrupt profile is already on disk when the error surfaces."""
        catalog = _catalog(tmp_hal0_home)
        env = export_envelope("orig", _profile(), exported_at="t")
        env["profile"]["flags"] = "-fa off"
        with pytest.raises(BadRequest):
            import_profile(env, "copied", catalog)
        assert not any(p.name == "copied" for p in catalog.list())

    def test_wrong_checksum_string_is_rejected(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", _profile(), exported_at="t")
        env["checksum"] = "sha256:" + "0" * 64
        with pytest.raises(BadRequest) as exc:
            import_profile(env, "copied", _catalog(tmp_hal0_home))
        assert exc.value.code == "profiles.checksum_mismatch"
        assert exc.value.details["expected"] == env["checksum"]
        assert exc.value.details["actual"] != env["checksum"]

    def test_absent_checksum_is_still_accepted(self, tmp_hal0_home: str) -> None:
        """A hand-authored envelope legitimately omits the checksum;
        ``ProfileEnvelope.checksum`` defaults to ``""``. Only a checksum that is
        PRESENT and WRONG means corruption."""
        env = export_envelope("orig", _profile(), exported_at="t")
        del env["checksum"]
        resolved = import_profile(env, "copied", _catalog(tmp_hal0_home))
        assert resolved.name == "copied"

    def test_empty_checksum_is_still_accepted(self, tmp_hal0_home: str) -> None:
        env = export_envelope("orig", _profile(), exported_at="t")
        env["checksum"] = ""
        resolved = import_profile(env, "copied", _catalog(tmp_hal0_home))
        assert resolved.name == "copied"

    def test_intact_envelope_still_imports(self, tmp_hal0_home: str) -> None:
        """Guard against the check being too strict — an untouched export must
        still import."""
        env = export_envelope("orig", _profile(), exported_at="t")
        resolved = import_profile(env, "copied", _catalog(tmp_hal0_home))
        assert resolved.name == "copied"


# ── the SHIPPED artifacts: exact-checksum round-trip guarantee ───────────────

_ADDONS = Path(__file__).resolve().parents[2] / "community" / "addons"


def _published(name: str) -> dict:
    return json.loads((_ADDONS / f"{name}.hal0profile.json").read_text(encoding="utf-8"))


class TestPublishedAddonRoundTrip:
    """The shipped ``community/addons`` envelopes must import and round-trip
    with a STABLE checksum: ``export_envelope`` checksums
    ``model_dump(exclude_none=True)``, so changing which fields serialize
    silently changes the checksum and breaks every published profile in the
    wild. These tests fail loudly if a schema change is made without
    regenerating the catalog (``scripts/export_addons.py``)."""

    def test_published_checksum_verifies(self) -> None:
        assert verify_checksum(_published("chat-long-context")) is True

    def test_reexport_reproduces_the_published_checksum_exactly(self) -> None:
        """Parse → re-export must land on the same sha256. ``exported_at`` is
        excluded from the checksum, so a different clock cannot move it."""
        published = _published("chat-long-context")
        env = parse_envelope(published)
        again = export_envelope(env.name, env.profile, exported_at="2099-01-01T00:00:00Z")
        assert again["checksum"] == published["checksum"]
        assert again["profile"] == published["profile"]

    def test_published_artifact_imports_and_is_resolvable(self, tmp_hal0_home: str) -> None:
        """A real commit-path import: its flags are all genuine tuning flags,
        so it passes the hardware / managed / model-fact screens."""
        catalog = _catalog(tmp_hal0_home)
        resolved = import_profile(_published("chat-long-context"), "long-ctx", catalog)
        assert resolved.name == "long-ctx"
        assert resolved.ctx_size == 131072
        assert "--no-context-shift" in resolved.flags

    def test_can_import_under_its_own_name(self, tmp_hal0_home: str) -> None:
        """Addons are not seeds, so the published name is the operator's to take."""
        catalog = _catalog(tmp_hal0_home)
        resolved = import_profile(_published("dense"), "dense", catalog)
        assert resolved.name == "dense"
        assert resolved.seed is False
        assert resolved.vision == "off"

    def test_import_still_409s_against_a_seed_name(self, tmp_hal0_home: str) -> None:
        """The seed-owns-the-name conflict itself is unchanged."""
        catalog = _catalog(tmp_hal0_home)
        with pytest.raises(Conflict) as exc:
            import_profile(_published("dense"), "chat", catalog)
        assert exc.value.code == "profiles.exists"

    def test_imported_then_exported_still_matches_published(self, tmp_hal0_home: str) -> None:
        """Full loop through the on-disk catalog: import → persist → resolve →
        export reproduces the published checksum. Catches a catalog write that
        drops or coerces a field."""
        published = _published("thinking")
        catalog = _catalog(tmp_hal0_home)
        import_profile(published, "thinking-copy", catalog)
        r = catalog.resolve("thinking-copy")
        again = export_envelope(
            "thinking-copy",
            ProfileConfig(
                flags=r.flags,
                ctx_size=r.ctx_size,
                enable_thinking=r.enable_thinking,
                vision=r.vision,  # type: ignore[arg-type]
                speculation=r.speculation,  # type: ignore[arg-type]
                runner=r.runner,
                cloned_from=r.cloned_from,
                intent=r.intent,
            ),
            exported_at="t",
        )
        assert again["checksum"] == published["checksum"]


# ── a v1.4.0 envelope (schema 2, profile ``mtp``) imports, converted ───────


def _v140_envelope(**body: object) -> dict:
    """What hal0 1.4.0 exported: schema 2 with the profile's ``mtp`` tune."""
    return {
        "kind": ENVELOPE_KIND,
        "schema_version": 2,
        "hal0_version": "1.4.0",
        "exported_at": "t",
        "name": "old",
        "profile": {"flags": "-fa on", **body},
    }


class TestV140EnvelopeImport:
    def test_mtp_off_lands_as_speculation_off(self, tmp_hal0_home: str) -> None:
        cat = _catalog(tmp_hal0_home)
        import_profile(_v140_envelope(mtp="off"), "old", cat)
        assert cat.resolve("old").speculation == "off"

    def test_mtp_auto_lands_as_the_auto_default(self, tmp_hal0_home: str) -> None:
        cat = _catalog(tmp_hal0_home)
        import_profile(_v140_envelope(mtp="auto"), "old", cat)
        assert cat.resolve("old").speculation == "auto"

    def test_a_spec_type_flag_converts_too(self, tmp_hal0_home: str) -> None:
        env = _v140_envelope(flags="-fa on --spec-type ngram-mod")
        parsed = parse_envelope(env)
        assert parsed.profile.speculation == "ngram"
        assert parsed.profile.flags == "-fa on"
        assert env["profile"]["flags"] == "-fa on --spec-type ngram-mod"  # input untouched
