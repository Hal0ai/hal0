# write_gateway_secrets_dropin

> 16 nodes

## Key Concepts

- **write_gateway_secrets_dropin()** (9 connections) — `src/hal0/agents/hermes_provision.py`
- **_install_hermes_gateway()** (7 connections) — `src/hal0/cli/agent_commands.py`
- **_detect_foreign_gateways()** (4 connections) — `src/hal0/agents/hermes_provision.py`
- **_gateway_dropin_body()** (3 connections) — `src/hal0/agents/hermes_provision.py`
- **GatewayDropinResult** (3 connections) — `src/hal0/agents/hermes_provision.py`
- **_privileged_systemctl()** (3 connections) — `src/hal0/agents/hermes_provision.py`
- **_hermes_venv_ready()** (3 connections) — `src/hal0/cli/agent_commands.py`
- **_wait_active_unit()** (3 connections) — `src/hal0/cli/agent_commands.py`
- **Render the gateway secrets drop-in body.      Mirrors the live drop-in: a why-co** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Outcome of :func:`write_gateway_secrets_dropin`.      ``outcome`` is one of ``"w** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Idempotently write the gateway secrets drop-in + ``daemon-reload`` (#437).** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Run one hal0-systemctl seam verb as root via ``sudo -n``.      ``body`` (when gi** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Retired foreign-gateway scan — now a no-op shim.      hal0's single-managed-gate** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **``-x /var/lib/hal0/venvs/hermes/bin/hermes`` — install.sh's own gate     for whe** (1 connections) — `src/hal0/cli/agent_commands.py`
- **`hermes gateway install --system --run-as-user hal0` + enable the unit.      Por** (1 connections) — `src/hal0/cli/agent_commands.py`
- **Poll ``systemctl is-active --quiet <unit>`` — Python port of     installer/insta** (1 connections) — `src/hal0/cli/agent_commands.py`

## Relationships

- [hermes_provision.py](hermes_provision.py.md) (5 shared connections)
- [agent_commands.py](agent_commands.py.md) (4 shared connections)
- [Any](Any.md) (2 shared connections)
- [Path](Path.md) (1 shared connections)
- [_StepCtx](_StepCtx.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes_provision.py`
- `src/hal0/cli/agent_commands.py`

## Audit Trail

- EXTRACTED: 39 (91%)
- INFERRED: 4 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*