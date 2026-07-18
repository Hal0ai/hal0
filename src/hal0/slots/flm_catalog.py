"""FLM (NPU) catalog probe for the slot drawer's model dropdowns.

Extracted from ``routes/slots.py::list_flm_models`` (P3-routers §J) so the
route layer is a thin request→service→envelope shell. The dashboard shows the
WHOLE catalog (installed or not) so the operator can pick any tag and trigger a
download for the ones not yet on disk; each entry carries an ``installed`` flag
so the UI can mark/act on the difference.

Two sources, container-exec primary:

  1. ``<runtime> exec`` into the running FLM container and run ``flm list``.
     This reads the store the slot ACTUALLY serves with — on a box that
     relocates the model store (``[models].flm_store``) the store is a
     bind-mount that only exists inside the container, so the container is the
     only place ``installed`` is accurate. Full list + correct flags.
  2. When the container is down (cold/disabled slot), fall back to the HOST
     ``flm list`` probe (:func:`hal0.providers.flm.flm_served_models`). It
     still knows every catalog tag (from the bundled ``model_list.json``) so
     the dropdowns populate; ``installed`` may read false on relocated-store
     boxes since the host can't see the container-only mount — acceptable for
     the cold case, and correct on default-store boxes.

Interface contract:

    list_models() -> list[dict[str, Any]]
        ``[{"model": tag, "installed": bool, "capabilities": [...],
        "family": str}]`` — ``model``/``installed`` keep the dashboard filter
        contract. Fail-soft: every probe error degrades to the next source (or
        an empty list) rather than raising.

The ``subprocess`` module is referenced module-globally so tests can
monkeypatch it.
"""

from __future__ import annotations

import json as _json
import subprocess
from typing import Any


def _from_container() -> list[dict[str, Any]] | None:
    """Probe the running FLM container's ``flm list`` (authoritative source).

    Returns the full catalog with correct ``installed`` flags, or ``None``
    when the container is down / unreachable so the caller falls back to the
    host probe.
    """
    from hal0.providers.flm import _classify_flm_model

    # Container name convention: hal0-slot-<name>; the NPU anchor is "flm".
    try:
        raw = subprocess.run(
            ["podman", "exec", "hal0-slot-flm", "/opt/fastflowlm/bin/flm", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        raw.check_returncode()
        data = _json.loads(raw.stdout)
    except Exception:
        return None
    entries = data if isinstance(data, list) else data.get("models", [])
    out: list[dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        tag = e.get("model") or e.get("name")
        if not tag:
            continue
        details = e.get("details") if isinstance(e.get("details"), dict) else {}
        out.append(
            {
                "model": tag,
                "installed": bool(e.get("installed")),
                "capabilities": _classify_flm_model(e),
                "family": str(details.get("family") or ""),
            }
        )
    return out or None


def list_models() -> list[dict[str, Any]]:
    """Return the full FLM catalog for the drawer's NPU model dropdowns.

    Container-exec primary, host-probe fallback (see module docstring). Never
    raises — a fully-unreachable probe returns ``[]``.
    """
    from hal0.providers.flm import flm_served_models

    models = _from_container()
    if models is None:
        # Cold-slot fallback — host probe knows every tag; installed may be
        # understated on relocated-store boxes (see module docstring).
        try:
            catalog = flm_served_models()
        except Exception:
            catalog = []
        models = [
            {
                "model": e.get("tag"),
                "installed": bool(e.get("installed")),
                "capabilities": e.get("capabilities", []),
                "family": e.get("family", ""),
            }
            for e in catalog
            if e.get("tag")
        ]
    return models
