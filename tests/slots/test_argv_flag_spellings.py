"""The profile-layer screen catches every llama.cpp spelling of a flag.

llama-server accepts short and alternative spellings for most flags
(``common/arg.cpp`` at the runner pin, b11574: ``-m``/``--model``,
``-hf``/``-hfr``/``--hf-repo``, ``-md``/``--model-draft``/``--spec-draft-model``
…) and rewrites ``_`` to ``-`` in a ``--long`` flag before matching. A screen
keyed on one spelling let ``-m /etc/shadow`` redirect the model (llama-server
keeps the last ``-m``). These tests drive every denied flag, in every
spelling, through the write-side screen, the launch-side screen and the
blocked-slot pinned path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hal0.errors import BadRequest, Hal0Error
from hal0.profiles import screen_profile_flags
from hal0.slots.argv import (
    FLAG_ALIASES,
    HOST_OWNED_FLAGS,
    MANAGED_ARGS_DENYLIST,
    resolve_argv,
)

#: canonical flag → every spelling llama.cpp b11574 accepts for it
#: (``common/arg.cpp``), for the flags hal0 manages or screens.
SPELLINGS: dict[str, tuple[str, ...]] = {
    # managed (base layer)
    "--model": ("-m", "--model"),
    "--alias": ("-a", "--alias"),
    "--ctx-size": ("-c", "--ctx-size"),
    "--host": ("--host",),
    "--port": ("--port",),
    # hardware (slot layer)
    "--n-gpu-layers": ("-ngl", "--gpu-layers", "--n-gpu-layers"),
    "--device": ("-dev", "--device"),
    "--threads": ("-t", "--threads"),
    # model facts / derived placement
    "--mmproj": ("-mm", "--mmproj"),
    "--spec-draft-device": ("-devd", "--device-draft", "--spec-draft-device"),
    "--mmproj-device": ("-mmdev", "--mmproj-device"),
    # remote model fetches
    "--model-url": ("-mu", "--model-url"),
    "--mmproj-url": ("-mmu", "--mmproj-url"),
    "--hf-repo": ("-hf", "-hfr", "--hf-repo"),
    "--hf-file": ("-hff", "--hf-file"),
    "--hf-token": ("-hft", "--hf-token"),
    "--docker-repo": ("-dr", "--docker-repo"),
    "--spec-draft-hf": ("-hfd", "-hfrd", "--hf-repo-draft", "--spec-draft-hf"),
    # host files
    "--spec-draft-model": ("-md", "--model-draft", "--spec-draft-model"),
    "--file": ("-f", "--file"),
    "--system-prompt-file": ("-sysf", "--system-prompt-file"),
    "--json-schema-file": ("-jf", "--json-schema-file"),
    "--binary-file": ("-bf", "--binary-file"),
    "--output-file": ("-o", "--output", "--output-file"),
    "--image": ("--image", "--audio", "--video"),
    "--webui-config-file": ("--ui-config-file", "--webui-config-file"),
    "--api-key-file": ("--api-key-file",),
    "--ssl-key-file": ("--ssl-key-file",),
    "--ssl-cert-file": ("--ssl-cert-file",),
    "--path": ("--path",),
    "--media-path": ("--media-path",),
    "--slot-save-path": ("--slot-save-path",),
    "--log-file": ("--log-file",),
    "--grammar-file": ("--grammar-file",),
    "--prompt-cache": ("--prompt-cache",),
    "--lora": ("--lora",),
    "--control-vector": ("--control-vector",),
    # listeners / auth
    "--rpc": ("--rpc",),
    "--api-key": ("--api-key",),
    "--api-prefix": ("--api-prefix",),
    "--cors-origins": ("--cors-origins",),
    "--tools": ("--tools",),
    # the shortcut for ``--tools all`` plus the MCP proxy
    "--agent": ("-ag", "--agent"),
    "--webui-mcp-proxy": ("--ui-mcp-proxy", "--webui-mcp-proxy"),
}

_EXPECTED_CODE = {
    **dict.fromkeys(
        ("--model", "--alias", "--ctx-size", "--host", "--port"), "slot.managed_arg_denied"
    ),
    **dict.fromkeys(("--n-gpu-layers", "--device", "--threads"), "slot.hardware_flag_denied"),
    **dict.fromkeys(
        ("--mmproj", "--spec-draft-device", "--mmproj-device"), "profiles.model_fact_flag_denied"
    ),
}


def _cases() -> list[tuple[str, list[str]]]:
    """Every denied flag in every spelling: ``-x v``, ``-x=v``, and for long
    flags the ``_`` form llama.cpp rewrites (``--api_key v``)."""
    out: list[tuple[str, list[str]]] = []
    for canon, spellings in SPELLINGS.items():
        for spelling in spellings:
            out.append((canon, [spelling, "/etc/shadow"]))
            out.append((canon, [f"{spelling}=/etc/shadow"]))
            if spelling.startswith("--") and "-" in spelling[2:]:
                out.append((canon, ["--" + spelling[2:].replace("-", "_"), "/etc/shadow"]))
    return out


CASES = _cases()


def _code_for(canon: str) -> str:
    return _EXPECTED_CODE.get(canon, "slot.host_owned_flag_denied")


def test_every_spelling_is_aliased_to_its_canonical_flag() -> None:
    for canon, spellings in SPELLINGS.items():
        for spelling in spellings:
            assert FLAG_ALIASES.get(spelling, spelling) == canon, spelling


def test_the_screened_sets_cover_every_case() -> None:
    from hal0.slots.argv import (
        DERIVED_PLACEMENT_FLAGS,
        MODEL_FACT_FLAGS,
        SLOT_HARDWARE_FLAGS,
    )

    screened = (
        MANAGED_ARGS_DENYLIST
        | SLOT_HARDWARE_FLAGS
        | MODEL_FACT_FLAGS
        | DERIVED_PLACEMENT_FLAGS
        | HOST_OWNED_FLAGS
    )
    assert set(SPELLINGS) <= screened


@pytest.mark.parametrize(
    ("canon", "tokens"), CASES, ids=lambda v: " ".join(v) if isinstance(v, list) else v
)
def test_profile_write_screen_refuses_every_spelling(canon: str, tokens: list[str]) -> None:
    with pytest.raises(BadRequest) as exc:
        screen_profile_flags(" ".join(["-fa", "auto", *tokens]))
    assert exc.value.code == _code_for(canon)


@pytest.mark.parametrize(
    ("canon", "tokens"), CASES, ids=lambda v: " ".join(v) if isinstance(v, list) else v
)
def test_launch_screen_refuses_every_spelling(canon: str, tokens: list[str]) -> None:
    with pytest.raises(BadRequest) as exc:
        resolve_argv(
            [
                ("base", ["--host", "0.0.0.0", "--port", "8081", "--model", "/models/m.gguf"]),
                ("profile", ["-fa", "auto", *tokens]),
            ]
        )
    assert exc.value.code == _code_for(canon)


@pytest.mark.parametrize("tokens", [["/etc/shadow"], ["/m.gguf", "-fa", "auto"], ["-1"]])
def test_bare_positional_args_are_refused(tokens: list[str]) -> None:
    with pytest.raises(BadRequest) as exc:
        screen_profile_flags(" ".join(tokens))
    assert exc.value.code == "slot.positional_arg_denied"
    with pytest.raises(BadRequest) as exc:
        resolve_argv([("profile", tokens)])
    assert exc.value.code == "slot.positional_arg_denied"


@pytest.mark.parametrize(
    "tokens",
    [
        ["--temp=0.7", "/etc/shadow"],
        ["-b=512", "/models/evil.gguf"],
        ["--reasoning-budget=-1", "/etc/shadow", "-fa", "auto"],
    ],
)
def test_a_bare_argument_after_a_joined_flag_is_positional(tokens: list[str]) -> None:
    """A joined ``--flag=value`` already carries its value, so the next bare
    token is positional — not a second value the screen would skip."""
    with pytest.raises(BadRequest) as exc:
        screen_profile_flags(" ".join(tokens))
    assert exc.value.code == "slot.positional_arg_denied"
    assert tokens[1] in exc.value.details["args"]
    with pytest.raises(BadRequest) as exc:
        resolve_argv([("profile", tokens)])
    assert exc.value.code == "slot.positional_arg_denied"


def test_a_joined_flag_followed_by_a_flag_is_fine() -> None:
    screen_profile_flags("--temp=0.7 -fa auto --top-k=40")
    assert resolve_argv([("profile", ["--temp=0.7", "-fa", "auto"])]).argv == [
        "--temp=0.7",
        "-fa",
        "auto",
    ]


#: The preset switches arg.cpp b11574 declares in its "presets" group (each
#: sets a model and a port; most download weights). Pinned here so a new
#: preset in a runner bump is a deliberate addition.
PRESETS = [
    "--embd-gemma-default",
    "--fim-qwen-1.5b-default",
    "--fim-qwen-3b-default",
    "--fim-qwen-7b-default",
    "--fim-qwen-7b-spec",
    "--fim-qwen-14b-spec",
    "--fim-qwen-30b-default",
    "--gpt-oss-20b-default",
    "--gpt-oss-120b-default",
    "--vision-gemma-4b-default",
    "--vision-gemma-12b-default",
    "--spec-default",
]


def test_the_preset_set_is_pinned() -> None:
    from hal0.slots.argv import LLAMA_PRESET_FLAGS

    assert set(PRESETS) == LLAMA_PRESET_FLAGS
    assert LLAMA_PRESET_FLAGS <= HOST_OWNED_FLAGS


@pytest.mark.parametrize("preset", PRESETS)
def test_each_preset_switch_is_refused(preset: str) -> None:
    for flags in (preset, f"-fa auto {preset}", f"--{preset[2:].replace('-', '_')}"):
        with pytest.raises(BadRequest) as exc:
            screen_profile_flags(flags)
        assert exc.value.code == "slot.host_owned_flag_denied"
        with pytest.raises(BadRequest) as exc:
            resolve_argv([("profile", flags.split())])
        assert exc.value.code == "slot.host_owned_flag_denied"


def test_the_preset_switch_is_refused_on_the_pinned_path() -> None:
    with pytest.raises(Hal0Error) as exc:
        _pinned(["-fa", "auto", "--gpt-oss-20b-default"])
    assert exc.value.code == "slot.host_owned_flag_denied"


@pytest.mark.parametrize(
    "tokens",
    [
        ["--metrics", "/etc/shadow"],
        ["--embedding", "/m.gguf"],
        ["--no-context-shift", "/etc/shadow", "-fa", "auto"],
        ["--cont_batching", "x"],
        ["-kvu", "/etc/shadow"],
        ["--no-warmup", "-1"],
    ],
)
def test_a_token_after_a_valueless_switch_is_positional(tokens: list[str]) -> None:
    """llama.cpp declares these switches with no value (arg.cpp b11574), so the
    next token is a separate argument — a bare one the screen refuses."""
    with pytest.raises(BadRequest) as exc:
        screen_profile_flags(" ".join(tokens))
    assert exc.value.code == "slot.positional_arg_denied"
    assert tokens[1] in exc.value.details["args"]
    with pytest.raises(BadRequest) as exc:
        resolve_argv([("profile", tokens)])
    assert exc.value.code == "slot.positional_arg_denied"


def test_valueless_switches_from_the_runner_table() -> None:
    """Spot-check the arity table against arg.cpp b11574: switches are
    valueless, flags with a value hint are not."""
    from hal0.slots.argv import VALUELESS_FLAGS

    for switch in ("--metrics", "--jinja", "--no-jinja", "-kvu", "--embedding", "--mtp", "-ag"):
        assert switch in VALUELESS_FLAGS, switch
    for valued in ("-fa", "--reasoning", "--cache-reuse", "--temp", "-b", "--spec-type", "-ngld"):
        assert valued not in VALUELESS_FLAGS, valued


def test_an_unknown_flag_keeps_the_value_heuristic() -> None:
    """A flag llama.cpp does not declare still takes the next token as its
    value (the screen does not guess at arity it has no table for)."""
    screen_profile_flags(
        "--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx --model_arch base"
    )


@pytest.mark.parametrize("flags", ["-ag", "--agent", "-fa auto -ag", "--agent -fa auto"])
def test_the_agent_shortcut_is_refused_as_a_bare_switch(flags: str) -> None:
    """``-ag`` / ``--agent`` enables the MCP proxy and every built-in tool
    (shell execution included) in one switch."""
    import shlex

    with pytest.raises(BadRequest) as exc:
        screen_profile_flags(flags)
    assert exc.value.code == "slot.host_owned_flag_denied"
    with pytest.raises(BadRequest) as exc:
        resolve_argv([("profile", shlex.split(flags))])
    assert exc.value.code == "slot.host_owned_flag_denied"


@pytest.mark.parametrize("flags", ["--no-agent", "-no-ag", "--no-webui-mcp-proxy"])
def test_turning_the_agent_tools_off_is_allowed(flags: str) -> None:
    screen_profile_flags(flags)


def test_underscore_spelling_dedups_against_its_dash_form() -> None:
    """``--n_predict`` and ``--n-predict`` are one flag to llama.cpp; the last
    one wins, as for any other duplicate."""
    res = resolve_argv([("profile", ["--n-predict", "10", "--n_predict", "20"])])
    assert res.argv == ["--n_predict", "20"]


def test_reasoning_format_none_is_stripped_in_its_underscore_join_form() -> None:
    res = resolve_argv([("profile", ["--reasoning_format=none", "-b", "512"])])
    assert res.argv == ["-b", "512"]


# ── shipped tunes still pass ────────────────────────────────────────────────


_REPO = Path(__file__).resolve().parents[2]


def _seed_flags() -> list[tuple[str, str]]:
    from hal0.config.schema import SEED_PROFILES

    return [(name, str(raw.get("flags", ""))) for name, raw in sorted(SEED_PROFILES.items())]


def _addon_flags() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    def walk(node: Any, label: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "flags" and isinstance(value, str):
                    out.append((label, value))
                walk(value, label)
        elif isinstance(node, list):
            for item in node:
                walk(item, label)

    for path in sorted((_REPO / "community" / "addons").glob("*.hal0profile.json")):
        walk(json.loads(path.read_text(encoding="utf-8")), path.name)
    return out


@pytest.mark.parametrize(("name", "flags"), _seed_flags())
def test_every_seed_passes_the_screen(name: str, flags: str) -> None:
    import shlex

    screen_profile_flags(flags)
    resolve_argv([("profile", shlex.split(flags))])


def test_shipped_addons_exist() -> None:
    assert _addon_flags(), "no shipped addon profile found"


@pytest.mark.parametrize(("name", "flags"), _addon_flags())
def test_every_shipped_addon_passes_the_screen(name: str, flags: str) -> None:
    import shlex

    screen_profile_flags(flags)
    resolve_argv([("profile", shlex.split(flags))])


@pytest.mark.parametrize(
    "flags",
    [
        "-fa auto -b 2048 -ub 512 -ctk q8_0 -ctv q8_0 --no-context-shift",
        "--temp 0.7 --top-p 0.95 --top-k 40 --min-p 0 --presence-penalty 0 --seed 123",
        "--reasoning off --reasoning-budget -1 --reasoning-format deepseek",
        "--parallel 2 -kvu --cache-reuse 256 --rope-scaling yarn --rope-freq-base 1e6",
        "--spec-type draft-mtp --spec-draft-n-max 8 --spec-draft-p-min 0.0 -ngld all",
        "--override-kv tokenizer.ggml.add_bos_token=bool:false --no-warmup --cont-batching",
        "--metrics --no-context-shift --cache-reuse 256 --mtp",
    ],
)
def test_legitimate_tuning_flags_still_pass(flags: str) -> None:
    import shlex

    screen_profile_flags(flags)
    resolve_argv([("profile", shlex.split(flags))])


# ── the blocked-slot pinned path ────────────────────────────────────────────


def _pinned(argv: list[str], *, jinja: bool = True) -> list[str]:
    """Resolve a blocked slot's recorded tune the way the launch does."""
    from hal0.providers.container import _pinned_segments

    scalars = {
        "model_alias": "real-model",
        "context_size": 8192,
        "chat_template_path": None,
        "mmproj": None,
        "slot_n_gpu_layers": 99,
        "slot_threads": None,
        "slot_metrics": True,
        "backend": "rocm",
        "jinja": jinja,
    }
    segments = _pinned_segments(
        {"argv": argv}, {"name": "agent"}, 8081, "/models/real.gguf", scalars
    )
    return resolve_argv(segments).argv


@pytest.mark.parametrize(
    "tokens",
    [
        ["-m", "/etc/shadow"],
        ["-m=/etc/shadow"],
        ["--model=/etc/shadow"],
        ["--model", "/etc/shadow"],
        ["-a", "evil"],
        ["--port", "9"],
        ["--host=0.0.0.0"],
    ],
)
def test_pinned_tune_cannot_redirect_the_base(tokens: list[str]) -> None:
    """A recorded (or hand-edited) tune's managed flags are dropped, in any
    spelling, and rebuilt from today's slot and model."""
    argv = _pinned(["--host", "127.0.0.1", "-fa", "auto", *tokens])
    assert argv[argv.index("--model") + 1] == "/models/real.gguf"
    assert "/etc/shadow" not in argv
    assert argv[argv.index("--alias") + 1] == "real-model"
    assert argv[argv.index("--port") + 1] == "8081"
    assert "-m" not in argv and "-a" not in argv
    assert argv.count("--port") == 1


_PINNED_REFUSED = [(canon, tokens) for canon, tokens in CASES if canon in HOST_OWNED_FLAGS]


@pytest.mark.parametrize(
    ("canon", "tokens"), _PINNED_REFUSED, ids=lambda v: " ".join(v) if isinstance(v, list) else v
)
def test_pinned_tune_with_a_host_owned_flag_refuses_to_launch(
    canon: str, tokens: list[str]
) -> None:
    with pytest.raises(Hal0Error) as exc:
        _pinned(["-fa", "auto", *tokens])
    assert exc.value.code == "slot.host_owned_flag_denied"


def test_pinned_tune_with_a_bare_positional_refuses_to_launch() -> None:
    with pytest.raises(Hal0Error) as exc:
        _pinned(["/etc/shadow", "-fa", "auto"])
    assert exc.value.code == "slot.positional_arg_denied"


@pytest.mark.parametrize("model_jinja", [True, False])
@pytest.mark.parametrize("spelling", [["--no-jinja"], ["--no_jinja"], ["--jinja", "--no-jinja"]])
def test_pinned_tune_with_no_jinja_still_launches(model_jinja: bool, spelling: list[str]) -> None:
    """A recorded ``--no-jinja`` (a model fact the profile layer may not carry)
    is consumed when the pinned launch is rebuilt, and the model's jinja fact
    decides — the slot launches instead of being refused."""
    argv = _pinned(["-fa", "auto", *spelling, "-b", "512"], jinja=model_jinja)
    assert ("--jinja" in argv) is model_jinja
    assert "--no-jinja" not in argv and "--no_jinja" not in argv
    assert argv[argv.index("-b") + 1] == "512"


@pytest.mark.parametrize("spelling", [["-mmdev", "ROCm0"], ["--mmproj-device=none"]])
def test_pinned_tune_with_a_projector_device_still_launches(spelling: list[str]) -> None:
    """The projector device is placement: dropped from the record and left to
    follow the slot's device."""
    argv = _pinned(["-fa", "auto", *spelling])
    assert "-mmdev" not in argv
    assert not any(t.startswith("--mmproj-device") for t in argv)
