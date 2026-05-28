"""δ-harness integration tests for v0.3 hermes integration.

These tests drive the FULL chat round-trip from the hal0-api WebSocket
proxy down to a mock hermes server bound to a random loopback port —
the same shape PR-9 and PR-10's production code use, but with a fake
hermes so no real GGUF download or hermes install is needed.

The mock-hermes seam (``FakeWsServer``) is intentionally minimal:

* Accepts WS upgrades on ``/api/events`` (server-to-client event mirror)
  and ``/api/ws`` (bidi JSON-RPC), the same paths real hermes serves.
* Records every inbound frame so tests can assert prompt + persona
  metadata reached upstream.
* Lets tests dispatch outbound frames (``message.delta``,
  ``message.complete``, ``persona.switched``) so the client-side
  assertion surface stays under test control.

Why Python + FastAPI rather than the existing shell harness
-----------------------------------------------------------
The existing ``tests/harness/*.sh`` rows drive the install + CLI + slot
lifecycle against systemctl + curl. v0.3's chat round-trip needs a
real WebSocket peer and live JSON-RPC framing — better expressed in
Python with TestClient than in bash with curl. These tests follow the
δ-tier convention (full end-to-end through hal0-api, mocking only what
would require GPU + GGUF + a real agent process) and emit findings
into ``tests/harness/FINDINGS.md`` via row entries (catalogued in
FINDINGS.md §25 + §26 after first run).
"""
