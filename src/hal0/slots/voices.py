"""Per-slot TTS voice-list proxy.

Extracted from ``routes/slots.py::get_slot_voices`` (P3-routers §J) so the
route layer is a thin request→service→envelope shell. TTS engines (Kokoro,
qwen3tts) expose ``GET /v1/audio/voices``; the dashboard's Voice settings
populate the default-voice picker from it instead of hardcoding the pack.

Interface contract:

    fetch_for_slot(name, port) -> dict[str, Any]
        ``{"name": name, "voices": [str, ...], "source": "live"|"offline"}``.
        Fail-soft: a cold/unreachable slot (or an engine without the route, or
        a missing/invalid port) returns ``{"voices": [], "source":
        "offline"}`` rather than raising — the UI falls back to a seed list.

The ``httpx`` module is referenced module-globally so tests can monkeypatch it.
"""

from __future__ import annotations

from typing import Any

import httpx


async def fetch_for_slot(name: str, port: int | None) -> dict[str, Any]:
    """Proxy a slot's ``GET /v1/audio/voices`` on loopback.

    Returns the live voice list, or the offline fallback shape on any error
    (invalid port, unreachable engine, non-list payload). Never raises.
    """
    if not isinstance(port, int) or port <= 0:
        return {"name": name, "voices": [], "source": "offline"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.0)) as client:
            resp = await client.get(f"http://127.0.0.1:{port}/v1/audio/voices")
            resp.raise_for_status()
            payload = resp.json()
    except Exception:
        return {"name": name, "voices": [], "source": "offline"}
    voices = payload.get("voices") if isinstance(payload, dict) else None
    if not isinstance(voices, list):
        voices = []
    return {
        "name": name,
        "voices": [str(v) for v in voices if isinstance(v, str | int)],
        "source": "live",
    }
