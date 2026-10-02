"""Issue #2256: hal0 owns a dynamic env key only while it has a claim on it.

``write_openwebui_env`` used to treat an explicit ``None`` override as "delete
this key", which cannot tell a stale claim hal0 wrote from a line the operator
wrote — and ``_search_provider_lookup()`` returns ``None`` on every box, so a
hand-set self-hosted SearXNG was reverted on every converge (and the companion
restarted because the file changed) while the header promised hand edits were
preserved.

The rule these tests pin: a ``None`` removes a key only if hal0 recorded
writing it (the ``# hal0-managed:`` header line); otherwise it is the
operator's value and is left alone. While hal0 does have a claim it owns the
key, and says so in the header.
"""

from __future__ import annotations

from pathlib import Path

from hal0.openwebui.env_writer import (
    _DYNAMIC_ENV_KEYS,
    _MANAGED_MARKER,
    default_openwebui_env,
    dynamic_env_overrides,
    write_openwebui_env,
)

#: What the operator hand-sets for a self-hosted SearXNG.
_OPERATOR_SEARCH = {
    "ENABLE_WEB_SEARCH": "True",
    "WEB_SEARCH_ENGINE": "searxng",
    "SEARXNG_QUERY_URL": "http://searx.lan:8080/search?q=<query>",
    "WEB_SEARCH_RESULT_COUNT": "8",
    "ENABLE_SEARCH_QUERY_GENERATION": "False",
}

_RAG_KEYS = (
    "RAG_EMBEDDING_ENGINE",
    "RAG_OPENAI_API_BASE_URL",
    "RAG_OPENAI_API_KEY",
    "RAG_EMBEDDING_MODEL",
)


def _parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"')
    return out


def _marker(path: Path) -> set[str]:
    """The keys the ``# hal0-managed:`` header line lists."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(_MANAGED_MARKER):
            return {k for k in line[len(_MANAGED_MARKER) :].replace(" ", "").split(",") if k}
    raise AssertionError("no hal0-managed marker line in the header")


def _overrides(*, embed: str | None = None) -> dict[str, str | None]:
    """The renderer's output for a box with only (optionally) an embed slot."""
    return dynamic_env_overrides(
        embed_model_id=embed,
        image_model_id=None,
        image_workflow_json=None,
        image_workflow_nodes_json=None,
        search_provider=None,
    )


def _operator_edit(path: Path, extra: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for key, value in extra.items():
            fh.write(f"{key}={value}\n")


# ── an operator's value survives a render where hal0 has nothing to claim ───


def test_operator_value_among_the_dynamic_keys_survives_an_all_none_render(
    tmp_path: Path,
) -> None:
    """The #2256 repro. Every dynamic key is ``None`` (nothing bound, no search
    provider) and the operator's hand-set values must still be there."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, overrides=_overrides(), preserve_existing=True)
    _operator_edit(target, _OPERATOR_SEARCH)

    write_openwebui_env(target, overrides=_overrides(), preserve_existing=True)

    parsed = _parse(target)
    for key, value in _OPERATOR_SEARCH.items():
        assert parsed[key] == value, f"{key} was dropped"


def test_operator_value_on_a_rag_key_survives_while_no_embed_slot_is_bound(
    tmp_path: Path,
) -> None:
    """Not specific to web search: any of the sixteen keys is the operator's
    until hal0 itself claims it."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, preserve_existing=True)
    _operator_edit(target, {"RAG_EMBEDDING_MODEL": "my-own-embedder"})

    write_openwebui_env(target, overrides=_overrides(), preserve_existing=True)

    assert _parse(target)["RAG_EMBEDDING_MODEL"] == "my-own-embedder"


def test_every_dynamic_key_is_operator_owned_until_hal0_writes_it(tmp_path: Path) -> None:
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, preserve_existing=True)
    _operator_edit(target, {key: f"hand-{i}" for i, key in enumerate(_DYNAMIC_ENV_KEYS)})

    write_openwebui_env(target, overrides=_overrides(), preserve_existing=True)

    parsed = _parse(target)
    assert {k: parsed.get(k) for k in _DYNAMIC_ENV_KEYS} == {
        key: f"hand-{i}" for i, key in enumerate(_DYNAMIC_ENV_KEYS)
    }
    assert _marker(target) == set()


def test_a_file_from_before_the_marker_existed_is_all_operator_owned(
    tmp_path: Path,
) -> None:
    """No ``# hal0-managed:`` line means hal0 never recorded a claim in this
    file, so nothing in it is hal0's to remove."""
    target = tmp_path / "openwebui.env"
    target.write_text(
        "# hal0 OpenWebUI environment — written by hal0.openwebui.env_writer\n"
        "WEB_SEARCH_ENGINE=searxng\n"
        "RAG_EMBEDDING_MODEL=old-model\n",
        encoding="utf-8",
    )

    write_openwebui_env(target, overrides=_overrides(), preserve_existing=True)

    parsed = _parse(target)
    assert parsed["WEB_SEARCH_ENGINE"] == "searxng"
    assert parsed["RAG_EMBEDDING_MODEL"] == "old-model"


# ── a hal0-written claim IS cleared when the capability goes away ───────────


def test_hal0_written_claim_is_cleared_when_the_capability_goes_away(tmp_path: Path) -> None:
    """The mechanism the maintainer called deliberate and correct: a stale
    capability claim must not survive a re-render."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, overrides=_overrides(embed="nomic"), preserve_existing=True)
    assert _parse(target)["RAG_EMBEDDING_MODEL"] == "nomic"
    assert _marker(target) == set(_RAG_KEYS)

    write_openwebui_env(target, overrides=_overrides(), preserve_existing=True)

    parsed = _parse(target)
    assert not set(_RAG_KEYS) & set(parsed)
    assert _marker(target) == set()


def test_clearing_hal0s_claim_leaves_the_operators_other_keys_alone(tmp_path: Path) -> None:
    """One render, two kinds of ``None``: the RAG claim hal0 wrote goes, the
    SearXNG lines the operator wrote stay."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, overrides=_overrides(embed="nomic"), preserve_existing=True)
    _operator_edit(target, _OPERATOR_SEARCH)

    write_openwebui_env(target, overrides=_overrides(), preserve_existing=True)

    parsed = _parse(target)
    assert not set(_RAG_KEYS) & set(parsed)
    for key, value in _OPERATOR_SEARCH.items():
        assert parsed[key] == value


def test_an_override_less_pass_keeps_the_record_of_what_hal0_wrote(tmp_path: Path) -> None:
    """``install.sh`` runs ``main()`` — a merge with no overrides — between
    converges. It must not forget which keys are hal0's, or the next render
    could never clear the claim and it would survive as stale."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, overrides=_overrides(embed="nomic"), preserve_existing=True)

    write_openwebui_env(target, preserve_existing=True)  # the installer's pass
    assert _marker(target) == set(_RAG_KEYS)
    assert _parse(target)["RAG_EMBEDDING_MODEL"] == "nomic"

    write_openwebui_env(target, overrides=_overrides(), preserve_existing=True)
    assert not set(_RAG_KEYS) & set(_parse(target))


def test_while_hal0_has_a_claim_it_replaces_a_hand_edit_and_owns_the_key(
    tmp_path: Path,
) -> None:
    """The other half of the rule, which the header states: with a backend to
    point at, hal0 writes the key — and clears it when that backend goes."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, preserve_existing=True)
    _operator_edit(target, {"RAG_EMBEDDING_MODEL": "my-own-embedder"})

    write_openwebui_env(target, overrides=_overrides(embed="nomic"), preserve_existing=True)
    assert _parse(target)["RAG_EMBEDDING_MODEL"] == "nomic"
    assert "RAG_EMBEDDING_MODEL" in _marker(target)

    write_openwebui_env(target, overrides=_overrides(), preserve_existing=True)
    assert "RAG_EMBEDDING_MODEL" not in _parse(target)


def test_an_operator_deleting_a_managed_line_drops_it_from_the_record(tmp_path: Path) -> None:
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, overrides=_overrides(embed="nomic"), preserve_existing=True)
    kept = [
        line
        for line in target.read_text(encoding="utf-8").splitlines()
        if not line.startswith("RAG_EMBEDDING_MODEL=")
    ]
    target.write_text("\n".join(kept) + "\n", encoding="utf-8")

    # No embed slot any more, so hal0 re-adds nothing.
    write_openwebui_env(target, overrides=_overrides(), preserve_existing=True)

    assert "RAG_EMBEDDING_MODEL" not in _marker(target)


def test_plain_call_still_deletes_a_shipped_default_on_none(tmp_path: Path) -> None:
    """The documented ``None`` contract for the non-preserving call is
    unchanged: there is no operator line to protect, so the default goes."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, overrides={"WEBUI_NAME": None})
    assert "WEBUI_NAME" not in _parse(target)
    assert "WEBUI_NAME" in default_openwebui_env()


# ── web search: hal0 has no opinion while no provider exists ────────────────


def test_web_search_keys_are_untouched_and_the_file_is_stable_without_a_provider(
    tmp_hal0_home: str,
) -> None:
    """End to end through the real resolver, which returns no search provider
    on every box today: an operator's SearXNG config survives, and a second
    render produces identical bytes — so the converge arm, which restarts the
    companion only when the file changed, does not restart it."""
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    (Path(tmp_hal0_home) / "etc" / "hal0").mkdir(parents=True, exist_ok=True)
    (Path(tmp_hal0_home) / "etc" / "hal0" / "capabilities.toml").write_text(
        "schema_version = 2\n", encoding="utf-8"
    )
    target = Path(tmp_hal0_home) / "etc" / "hal0" / "openwebui.env"
    write_openwebui_env(target, overrides=resolve_dynamic_env_overrides(), preserve_existing=True)
    _operator_edit(target, _OPERATOR_SEARCH)

    write_openwebui_env(target, overrides=resolve_dynamic_env_overrides(), preserve_existing=True)
    normalised = target.read_bytes()
    write_openwebui_env(target, overrides=resolve_dynamic_env_overrides(), preserve_existing=True)

    assert target.read_bytes() == normalised
    parsed = _parse(target)
    for key, value in _OPERATOR_SEARCH.items():
        assert parsed[key] == value
    assert _marker(target) == set()


def test_a_search_provider_claim_is_written_then_cleared_when_it_goes(tmp_path: Path) -> None:
    """Forward-looking: once a provider does register, its five keys are
    hal0's, and they clear like any other claim."""
    provider = {"engine": "searxng", "query_url": "http://searxng:8080/search?q=<query>"}
    target = tmp_path / "openwebui.env"
    with_provider = dynamic_env_overrides(
        embed_model_id=None,
        image_model_id=None,
        image_workflow_json=None,
        image_workflow_nodes_json=None,
        search_provider=provider,
    )
    write_openwebui_env(target, overrides=with_provider, preserve_existing=True)
    assert _parse(target)["WEB_SEARCH_ENGINE"] == "searxng"
    assert len(_marker(target)) == 5

    write_openwebui_env(target, overrides=_overrides(), preserve_existing=True)
    assert "WEB_SEARCH_ENGINE" not in _parse(target)
    assert _marker(target) == set()


# ── the header tells the truth ──────────────────────────────────────────────


def test_header_documents_the_managed_keys(tmp_path: Path) -> None:
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, preserve_existing=True)
    header = "\n".join(
        line for line in target.read_text(encoding="utf-8").splitlines() if line.startswith("#")
    )
    for key in _DYNAMIC_ENV_KEYS:
        assert key in header, f"header does not name managed key {key}"
    assert "One exception" in header
    assert "hand edit" in header
    assert _MANAGED_MARKER in header


def test_header_still_promises_preservation_for_everything_else(tmp_path: Path) -> None:
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, preserve_existing=True)
    text = target.read_text(encoding="utf-8")
    assert "Hand edits are PRESERVED" in text
    assert "overwritten on next slot load" not in text


def test_header_is_deterministic_and_marker_is_sorted(tmp_path: Path) -> None:
    """Byte-stability is what keeps the converge arm from restarting the
    companion for a render that changed nothing."""
    target = tmp_path / "openwebui.env"
    write_openwebui_env(target, overrides=_overrides(embed="nomic"), preserve_existing=True)
    first = target.read_bytes()
    write_openwebui_env(target, overrides=_overrides(embed="nomic"), preserve_existing=True)
    assert target.read_bytes() == first
    marker_line = next(
        line for line in first.decode().splitlines() if line.startswith(_MANAGED_MARKER)
    )
    listed = marker_line[len(_MANAGED_MARKER) :].strip().split(",")
    assert listed == sorted(listed)
