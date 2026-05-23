"""Static checks on the tool_definitions.json contract.

We pin the eight-tool count + each tool's target/labels/endpoint
shape so a careless edit to the JSON surface trips CI.
"""

from __future__ import annotations

import json
from pathlib import Path

from hal0.omni_router.tools import (
    TOOL_DEFINITIONS,
    ToolDefinition,
    tools_by_name,
)

# The eight v0.2 tools — ADR-0008 §8 + plan §7.2.
EXPECTED_TOOLS = {
    "generate_image": ("image", ("image",), "/v1/images/generations", "upstream"),
    "edit_image": ("image", ("edit",), "/v1/images/edits", "upstream"),
    "text_to_speech": ("tts", ("tts",), "/v1/audio/speech", "upstream"),
    "transcribe_audio": (
        "transcription",
        ("transcription",),
        "/v1/audio/transcriptions",
        "upstream",
    ),
    "analyze_image": ("llm", ("vision",), "/v1/chat/completions", "upstream"),
    "embed_text": ("embedding", ("embeddings",), "/v1/embeddings", "hal0"),
    "rerank_documents": ("reranking", ("reranking",), "/v1/rerank", "hal0"),
    "route_to_chat": ("llm", (), None, "hal0"),
}


def test_tool_count_is_eight() -> None:
    """Plan §7.2: v0.2 ships exactly 8 tools. ``recall_memory`` is v0.3+."""
    assert len(TOOL_DEFINITIONS) == 8


def test_tool_names_match_expected() -> None:
    names = {t.name for t in TOOL_DEFINITIONS}
    assert names == set(EXPECTED_TOOLS.keys())


def test_each_tool_shape() -> None:
    by_name = tools_by_name()
    for name, (slot_type, labels, endpoint, source) in EXPECTED_TOOLS.items():
        tool = by_name[name]
        assert tool.target_slot_type == slot_type, name
        assert tool.required_model_labels == labels, name
        assert tool.endpoint == endpoint, name
        assert tool.source == source, name


def test_definitions_are_frozen() -> None:
    """``ToolDefinition`` is frozen — mutating must raise."""
    import dataclasses

    tool = TOOL_DEFINITIONS[0]
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        tool.name = "renamed"  # type: ignore[misc]


def test_openai_tool_shape() -> None:
    """``to_openai_tool`` returns the OpenAI ``tools=[...]`` wire shape."""
    tool = tools_by_name()["generate_image"]
    rendered = tool.to_openai_tool()
    assert rendered["type"] == "function"
    fn = rendered["function"]
    assert fn["name"] == "generate_image"
    assert "description" in fn
    assert fn["parameters"]["type"] == "object"
    assert "prompt" in fn["parameters"]["properties"]
    assert "prompt" in fn["parameters"]["required"]


def test_parameters_are_valid_json_schema_objects() -> None:
    """Every tool's parameters must be an object-typed schema with
    ``properties`` + ``required`` arrays (OpenAI's accepted shape)."""
    for tool in TOOL_DEFINITIONS:
        params = tool.parameters
        assert params["type"] == "object", tool.name
        assert isinstance(params.get("properties"), dict), tool.name
        # ``required`` is allowed empty for tools where every arg is
        # optional, but every tool we ship has at least one required
        # field.
        assert isinstance(params.get("required"), list), tool.name
        assert len(params["required"]) >= 1, tool.name


def test_required_fields_are_listed_in_properties() -> None:
    """A required field must also be declared in ``properties``."""
    for tool in TOOL_DEFINITIONS:
        props = set(tool.parameters["properties"].keys())
        req = set(tool.parameters["required"])
        assert req.issubset(props), (tool.name, req, props)


def test_pin_block_is_present() -> None:
    """The ``_pin`` block — plan §7.5 — must accompany the tools list
    so the drift-detection script can locate the upstream provenance.
    """
    raw_path = Path(__file__).parent.parent.parent
    raw_path = raw_path / "src" / "hal0" / "omni_router" / "tool_definitions.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert "_pin" in raw, "tool_definitions.json missing _pin block"
    pin = raw["_pin"]
    for key in ("upstream_repo", "upstream_path", "last_reviewed"):
        assert key in pin, key


def test_route_to_chat_has_no_endpoint() -> None:
    """route_to_chat is internal — no Lemonade endpoint."""
    assert tools_by_name()["route_to_chat"].endpoint is None


def test_only_route_to_chat_has_no_endpoint() -> None:
    """Every other tool MUST have a Lemonade endpoint."""
    missing = [t.name for t in TOOL_DEFINITIONS if t.endpoint is None]
    assert missing == ["route_to_chat"]


def test_tools_by_name_returns_fresh_dict() -> None:
    """``tools_by_name`` rebuilds the dict per call (tests can mutate)."""
    a = tools_by_name()
    b = tools_by_name()
    assert a is not b
    assert a == b


def test_immutable_tool_objects_shared_across_calls() -> None:
    """The ToolDefinition instances themselves are shared (frozen)."""
    a = tools_by_name()
    b = tools_by_name()
    for name in a:
        assert a[name] is b[name]


def test_label_tuples_are_tuples_not_lists() -> None:
    """``required_model_labels`` is a tuple (frozen-friendly)."""
    for tool in TOOL_DEFINITIONS:
        assert isinstance(tool.required_model_labels, tuple)


def test_to_openai_tool_omits_hal0_metadata() -> None:
    """The OpenAI wire shape must not leak hal0-internal fields like
    ``source`` or ``target_slot_type`` — Lemonade would reject them."""
    rendered = tools_by_name()["embed_text"].to_openai_tool()
    fn = rendered["function"]
    assert "source" not in fn
    assert "target_slot_type" not in fn
    assert "required_model_labels" not in fn
    assert "endpoint" not in fn


def test_endpoint_path_format() -> None:
    """Non-null endpoints start with ``/v1/``."""
    for tool in TOOL_DEFINITIONS:
        if tool.endpoint is None:
            continue
        assert tool.endpoint.startswith("/v1/"), tool.name


def test_label_gated_tools_carry_at_least_one_label() -> None:
    """Every label-gated tool has at least one required label.

    ``route_to_chat`` is the sole exception — its caller-side label
    (``tool-calling``) is gated by the filter on the caller, not the
    target, so the tool itself has zero required target labels.
    """
    for tool in TOOL_DEFINITIONS:
        if tool.name == "route_to_chat":
            assert tool.required_model_labels == ()
        else:
            assert len(tool.required_model_labels) >= 1, tool.name


def test_target_slot_types_are_canonical() -> None:
    """Match plan §12.6's six type enum (LRU budget)."""
    canonical = {"llm", "embedding", "reranking", "transcription", "tts", "image"}
    for tool in TOOL_DEFINITIONS:
        assert tool.target_slot_type in canonical, tool.name


def test_definitions_round_trip_through_isinstance() -> None:
    for tool in TOOL_DEFINITIONS:
        assert isinstance(tool, ToolDefinition)
