"""FLM (NPU) catalog probe for the slot drawer's model dropdowns.

Extracted from ``routes/slots.py::list_flm_models`` (P3-routers §J) so the
route layer is a thin request→service→envelope shell. The dashboard shows the
WHOLE catalog (installed or not) so the operator can pick any tag and trigger a
download for the ones not yet on disk; each entry carries an ``installed`` flag
so the UI can mark/act on the difference.

One source: :func:`hal0.providers.flm.flm_served_models`, whose ``flm list``
runs inside the flm runner image (#2993) — ``podman exec`` into the running FLM
slot, else a one-shot container of the slot's image with the slot's store
mount. So the tags are the runner's FastFlowLM catalogue and ``installed``
reflects the store the slot serves from, whether or not the slot is up. (This
module used to ``podman exec`` a hardcoded ``hal0-slot-flm`` itself, which as
the unprivileged service user reached the wrong podman store and missed
id-keyed slots, so it always fell through to the host binary.)

Interface contract:

    list_models() -> list[dict[str, Any]]
        ``[{"model": tag, "installed": bool, "capabilities": [...],
        "family": str}]`` — ``model``/``installed`` keep the dashboard filter
        contract. Fail-soft: a probe that gave no answer yields ``[]`` rather
        than raising.
"""

from __future__ import annotations

from typing import Any


def list_models() -> list[dict[str, Any]]:
    """Return the full FLM catalog for the drawer's NPU model dropdowns.

    Never raises — a fully-unreachable probe returns ``[]``.
    """
    from hal0.providers.flm import flm_served_models

    try:
        catalog = flm_served_models()
    except Exception:
        catalog = []
    return [
        {
            "model": e.get("tag"),
            "installed": bool(e.get("installed")),
            "capabilities": e.get("capabilities", []),
            "family": e.get("family", ""),
        }
        for e in catalog
        if e.get("tag")
    ]
