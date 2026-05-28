"""hal0-api plugin host (v0.3, PR-7).

The dashboard adopts upstream Hermes's plugin manifest contract verbatim.
hal0-api becomes a thin reverse proxy in front of the Hermes dashboard
server (loopback 127.0.0.1:9119 by default) for two endpoints:

* ``GET /api/dashboard/plugins``  → upstream's manifest list
* ``GET /dashboard-plugins/<name>/<file>``  → upstream's plugin static asset

Both endpoints carry SRI verification (when the manifest declares
``integrity``), a path-traversal validator ported verbatim from
GHSA-5qr3-c538-wm9j, inbound ``Authorization``/``Cookie`` stripping
(no hal0 session bleeds to upstream), and outbound ``X-hal0-Agent``
injection per ADR-0012.

The manifest endpoint additionally sets
``Content-Security-Policy: script-src 'self' 'strict-dynamic'`` per
DA-sec-ops MUST-FIX #4.

The actual plugin SDK + tab host live UI-side in
``ui/src/dash/agents/plugin-host.jsx`` +
``ui/src/dash/agents/plugin-sdk-shim.js``. This package is hal0-api's
side of that contract only.
"""

from hal0.api.plugins.manifest_proxy import router

__all__ = ["router"]
