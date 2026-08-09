"""upload.py — the explicit, opt-in push of a bundle to the public bench API.

stdlib urllib only (bench-path rule). Nothing here runs automatically: upload
is a verb the operator invokes, consistent with the project's no-telemetry
stance. The server dedupes on the manifest's content-addressed bundle_id, so
retrying a flaky upload is always safe.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_API_BASE = "https://api.hal0.dev"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse all redirects on the upload POST: the Authorization: Bearer
    header must never follow a redirect off-host — urllib's default redirect
    handler would happily re-send it to whatever Location the server names.
    Returning None here makes urllib raise HTTPError for any 3xx response,
    which the existing HTTPError handler below turns into an UploadError."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


class UploadError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def api_base() -> str:
    return os.environ.get("HAL0_BENCH_API_BASE", "").rstrip("/") or DEFAULT_API_BASE


def upload_bundle(
    path: Path | str, api: str | None = None, token: str | None = None
) -> dict[str, Any]:
    """POST the bundle to ``<api>/v1/bundles``; return the parsed JSON reply.

    Raises UploadError with the HTTP status and the server's machine-readable
    ``errors``/``error`` payload — the CLI prints these verbatim so a rejected
    bundle is diagnosable without server logs.
    """
    tok = token or os.environ.get("HAL0_BENCH_TOKEN", "")
    if not tok:
        raise UploadError("no token: set HAL0_BENCH_TOKEN or pass --token")
    base = (api or api_base()).rstrip("/")
    data = Path(path).read_bytes()
    req = urllib.request.Request(
        f"{base}/v1/bundles",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/gzip",
        },
    )
    try:
        with _opener.open(req, timeout=120) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode(errors="replace") or "{}")
        except json.JSONDecodeError:
            payload = {}
        detail = payload.get("errors") or payload.get("error") or exc.reason
        raise UploadError(f"upload rejected ({exc.code}): {detail}", status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise UploadError(f"cannot reach {base}: {exc.reason}") from exc
