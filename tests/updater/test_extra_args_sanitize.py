"""sanitize_model_extra_args — the managed-flag registry-heal migration.

``defaults.extra_args`` rides the untrusted ``model_extra_args`` argv segment,
so a row carrying a §21.7 managed flag (``-c``/``--port``/…) hard-fails every
launch with ``slot.managed_arg_denied``. Such rows were minted by the
pre-screen profile stamp of the since-removed
``POST /api/models/{id}/duplicate`` back when 8 seed profiles carried
``-c <N>``. The migration's contract under test:

  - managed flags (+ values) are stripped, either spelling, and persisted,
  - clean rows / rows without defaults are untouched,
  - token-exact matching: lookalikes (``--model_path``/``--threads-batch``)
    and functional slot-hardware flags (``--threads``) survive.
"""

from __future__ import annotations

from hal0.registry.model import Model, ModelDefaults
from hal0.registry.store import ModelRegistry
from hal0.updater.updater import sanitize_model_extra_args


def _register(model_id: str, extra_args: str | None) -> None:
    ModelRegistry().add(
        Model(
            id=model_id,
            path=f"/tmp/{model_id}.gguf",
            capabilities=["chat"],
            defaults=ModelDefaults(extra_args=extra_args) if extra_args is not None else None,
        )
    )


def test_strips_managed_flags_and_persists(tmp_hal0_home: str) -> None:
    _register("bricked", "--jinja -fa on -c 131072 --port 9999 -b 2048")
    _register("clean", "-fa on -b 2048")
    _register("no-defaults", None)

    assert sanitize_model_extra_args() == 1

    reg = ModelRegistry()
    assert reg.get("bricked").defaults.extra_args == "--jinja -fa on -b 2048"
    assert reg.get("clean").defaults.extra_args == "-fa on -b 2048"
    assert reg.get("no-defaults").defaults is None


def test_leaves_lookalike_and_hardware_flags_alone(tmp_hal0_home: str) -> None:
    """--model_path/--threads-batch are not --model/--threads; --threads itself
    is slot-hardware, functional at launch, and NOT stripped by this migration."""
    _register("tts", "--model_path /models/kokoro --threads-batch 8 --threads 4")

    assert sanitize_model_extra_args() == 0
    assert (
        ModelRegistry().get("tts").defaults.extra_args
        == "--model_path /models/kokoro --threads-batch 8 --threads 4"
    )


def test_empty_registry_is_noop(tmp_hal0_home: str) -> None:
    assert sanitize_model_extra_args() == 0
