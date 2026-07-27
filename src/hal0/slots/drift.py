"""Config-drift comparator (P3-slots §1c).

Compares a slot's *live* container argv against the argv a restart would
render from the current TOML, so an operator who edits ``slots/<name>.toml``
without restarting the container gets a visible signal that the running
process is stale.

**Kept, not deleted** — see the P3-slots decomposition spec §6 investigation.
The spec's hypothesis was "running argv equals rendered argv by
construction" (which would make this whole comparator deletable once
§11.2 PortAuthority issues ``--model``/``--alias``/port authoritatively).
That PortAuthority seam does **not exist yet** (plan §23.2 S-seam table:
"note the interface; don't build it"), so the id/path drift class this
module resolves (#1226) is still live. More importantly,
``tests/slots/test_config_drift_aliases.py::test_real_drift_still_detected_across_spellings``
asserts a genuine value divergence (``-b 2048`` rendered vs ``--batch-size
512`` running) is still flagged — proving by existing test coverage that
"running ≡ rendered by construction" is false today: a container can run
stale argv after a TOML edit without a restart, and that is exactly the
condition this module exists to detect. Revisit the delete option jointly
with the P3-quadlet owner (``expected_argv``) once §11.2 lands.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Protocol

from hal0.slots._cfg_helpers import _model_default
from hal0.slots.npu.trio import is_npu_trio_shadow

# Config-drift comparison keys. Spelling no longer needs to match the launch
# renderer exactly: both sides of the comparison are canonicalized through
# slots.argv.FLAG_ALIASES (so ``--batch-size`` in a running argv matches a
# rendered ``-b`` instead of reporting false drift).
#
# ``--port`` is here because it is the field #1224 was actually reported
# against: ``PUT /config {"port": N}`` then ``slot load`` left the container on
# the old port. It is rendered verbatim from the slot's port
# (``providers.container`` builds ``["--host", "0.0.0.0", "--port",
# str(port)]``), so a stale unit shows up as a plain value divergence — and
# ``SlotManager._should_converge`` reads this same comparator to decide whether
# an explicit load must re-render the unit.
_CONFIG_DRIFT_KEYS: tuple[str, ...] = (
    "--ctx-size",
    "--model",
    "--alias",
    "--port",
    "-b",
    "-ub",
)


class DriftHost(Protocol):
    """Narrow seam :func:`compute_config_drift` needs from ``SlotManager``."""

    async def _is_active(self, slot_name: str) -> bool: ...
    async def _maybe_load_config(self, slot_name: str) -> dict[str, Any] | None: ...
    async def _resolve_model_info(self, model_id: str | None) -> dict[str, Any]: ...
    def _resolve_servable_model(self, model_id: str, cfg: Any) -> str: ...


def _argv_values(argv: list[str], keys: tuple[str, ...]) -> dict[str, str | None]:
    """Return the last value for each flag key in argv, alias-aware.

    Both the requested ``keys`` and the argv tokens are canonicalized
    through :data:`hal0.slots.argv.FLAG_ALIASES` before comparison, so a
    running ``--batch-size 512`` matches a rendered ``-b 512`` (and vice
    versa) instead of reporting false config drift. The result stays keyed
    by the caller's original ``keys`` spelling (the drift payload contract).

    Last value wins because slot ``[server].extra_args`` intentionally follows
    profile flags and can override them.
    """
    from hal0.slots.argv import FLAG_ALIASES

    canon_to_key = {FLAG_ALIASES.get(k, k): k for k in keys}
    out: dict[str, str | None] = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        flag, eq, inline = token.partition("=")
        key = canon_to_key.get(FLAG_ALIASES.get(flag, flag))
        if key is None:
            i += 1
            continue
        if eq:
            out[key] = inline
            i += 1
        else:
            out[key] = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
    return out


def _resolve_drift_flags(
    flags: dict[str, str | None],
    model_key: str | None,
    model_path: str | None,
) -> dict[str, str | None]:
    """Canonicalize the id-bearing drift flags the way the renderer does.

    ``--model`` and ``--alias`` carry a model identity that the unit renderer
    resolves (registry id → on-disk path for ``--model``; slugified id for
    ``--alias``). A raw id surfaced on either side of the drift comparison is
    put through the SAME resolution so a slot created with a registry id does
    not permanently false-warn (#1226). Values already resolved (a real path,
    an already-slugified alias) pass through unchanged.
    """
    from hal0.registry.discover import _normalise_id

    out = dict(flags)
    model_val = out.get("--model")
    # Rendered/running may carry the bare registry id instead of the resolved
    # path; substitute the known on-disk path so the realpath compare matches.
    #
    # Matched on the NORMALISED id, not the raw string (#1226). The slot TOML
    # keeps the catalog spelling (``Qwopus3.5-4B-Coder-MTP-Q6_K``) while the
    # registry key — and therefore the running container's ``--alias`` — is the
    # slug (``qwopus3-5-4b-coder-mtp-q6-k``), so the ``==`` compare never fired
    # and the substitution never happened. The operator saw a permanent, bogus
    # ``config drift: --model: running=/mnt/ai-models/....gguf
    # rendered=Qwopus3.5-4B-Coder-MTP-Q6_K``. A resolved path never normalises
    # onto a bare id, so this cannot mask real path drift.
    if (
        model_val is not None
        and model_path
        and model_key
        and _normalise_id(model_val) == _normalise_id(str(model_key))
    ):
        out["--model"] = str(model_path)
    alias_val = out.get("--alias")
    if alias_val is not None:
        # Slug is idempotent: slugifying an already-slugified alias is a no-op,
        # so both a raw id and a rendered slug collapse to the same token.
        out["--alias"] = _normalise_id(alias_val)
    return out


def _config_drift_values_equal(key: str, running: str | None, rendered: str | None) -> bool:
    if key == "--model" and running is not None and rendered is not None:
        return os.path.realpath(running) == os.path.realpath(rendered)
    return running == rendered


async def compute_config_drift(
    host: DriftHost,
    slot_name: str,
    *,
    cfg: dict[str, Any] | None = None,
    active: bool | None = None,
) -> dict[str, Any] | None:
    """Compare live container argv to the command a restart would render.

    Returns a structured payload when the comparison is meaningful, or
    None when the slot is inactive, lacks a config, is an NPU trio shadow,
    or the provider cannot read either side of the comparison.
    """
    if active is None:
        active = await host._is_active(slot_name)
    if not active:
        return None
    if cfg is None:
        cfg = await host._maybe_load_config(slot_name)
    if not cfg or is_npu_trio_shadow(cfg):
        return None

    # Resolve the model the SAME way the launch path does before asking the
    # renderer what it would emit (#1226). ``load()`` runs the configured id
    # through ``_resolve_servable_model`` (catalog id → the locally-registered
    # id that actually has a file on disk) before spawning, so the container
    # carries the SERVABLE model's path. The drift check used the raw TOML id:
    # for exactly the slots this matters for — a catalog id that landed locally
    # under a different id — the registry lookup missed, the renderer fell back
    # to emitting the bare id, and the comparison against the running
    # container's real ``--model`` warned forever.
    model_default = _model_default(cfg)
    if model_default:
        try:
            model_default = host._resolve_servable_model(model_default, cfg)
        except Exception:
            # Status must never fail because a fallback heuristic raised — fall
            # back to the raw id and let the comparison proceed as before.
            model_default = _model_default(cfg)
    model_info = await host._resolve_model_info(model_default)
    from hal0.providers.container import container_provider

    provider = container_provider()
    loop = asyncio.get_event_loop()
    running, rendered = await asyncio.gather(
        loop.run_in_executor(None, provider.running_argv, slot_name),
        loop.run_in_executor(None, provider.expected_argv, cfg, model_info),
    )
    if not running or not rendered:
        return None

    running_flags = _argv_values(running, _CONFIG_DRIFT_KEYS)
    rendered_flags = _argv_values(rendered, _CONFIG_DRIFT_KEYS)
    # #1226: the renderer resolves a registry model id to its on-disk path
    # (``--model``) and slugifies it for the advertised ``--alias``. A raw
    # id on either side must be run through the SAME resolution before
    # comparison, else a slot created with a registry id permanently
    # false-warns (running path/slug vs rendered id). Resolve both sides.
    model_key = model_info.get("_model_key") or model_default
    model_path = model_info.get("path")
    running_flags = _resolve_drift_flags(running_flags, model_key, model_path)
    rendered_flags = _resolve_drift_flags(rendered_flags, model_key, model_path)
    diffs = [
        {"key": key, "running": running_flags.get(key), "rendered": rendered_flags.get(key)}
        for key in _CONFIG_DRIFT_KEYS
        if not _config_drift_values_equal(key, running_flags.get(key), rendered_flags.get(key))
    ]
    return {"drifted": bool(diffs), "diffs": diffs}


__all__ = [
    "_CONFIG_DRIFT_KEYS",
    "DriftHost",
    "compute_config_drift",
]
