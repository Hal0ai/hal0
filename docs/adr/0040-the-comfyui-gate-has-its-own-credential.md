# ADR-0040: ComfyUI's GPU gate has its own credential

## Status

**ACCEPTED (2026-10-25).** An operator decision: image generation must keep
working on a box that enforces auth, and the 1.7 tag waits for it (#3058).

It adds a credential to the KB-1 auth layer (`src/hal0/api/auth.py`) without
adding a tier. ADR-0038 made the gate fail closed; this record gives it a way
to stay guarded on a box that enforces auth.

## Context

The `hal0_gpu_gate` custom node runs inside the `img` container. Before every
image prompt (the editor's, hal0's own `/v1` renders, Open WebUI's and the omni
loop's) it calls three hal0-api routes: `GET /api/comfyui/arbiter`,
`POST /api/comfyui/prompt-handoff` and `POST /api/comfyui/switchover`. They
sit under `/api/comfyui`, which `hal0.security.exposure` classifies ADMIN.

Two kinds of box refused those calls:

- **Auth enforced** (`hal0 auth require on` or `HAL0_REQUIRE_AUTH=1`). ADMIN
  routes need an admin session or key, from loopback too.
- **Bound to one address, with an admin key.** The gate dials the address
  hal0-api binds, so its requests do not come from loopback, and the
  posture-coupled LAN rule for ADMIN routes applies.

The gate sent no credential, so since ADR-0038 it refused every image prompt
on such a box, and the only way back was `HAL0_COMFYUI_GATE_FAIL_OPEN=1`,
which also drops the guard against rendering next to the LLM slots.

Giving the container the admin key was ruled out from the start: anything
running in ComfyUI (a custom node, a workflow's code) could read it, and it
opens every route.

## Decision

1. **hal0 mints the gate a token of its own.** `secrets.token_urlsafe(32)`,
   written to `<var_lib>/comfyui-gate/token` (`0600`, in a `0700`
   directory), owned by the service user, through the no-follow writers in
   `hal0.security.nofollow`. A root writer (the CLI building the `img` unit)
   hands both to the `hal0` account so hal0-api can read them. The directory
   sits outside every directory the container mounts read-write.
2. **The token reaches the container as a read-only directory mount.** The
   `img` slot's spec mints the token if there is none, mounts its directory at
   `/opt/hal0-gate` read-only, and names the file in
   `HAL0_COMFYUI_GATE_TOKEN_FILE`. The value never enters the unit file or the
   container's environment. The directory is mounted rather than the file, so
   a replaced token reaches a running container.
3. **The token opens exactly the gate's three requests.** The enforcement
   middleware matches the exact method and path
   (`hal0.api.auth._GATE_REQUESTS`), HTTP only, after the normal decision
   refused the request. It is not a tier: `resolve_principal` never returns
   it, so it opens no other route, no WebSocket and no stream. A request it
   admitted carries `GATE_SCOPE_KEY` on its ASGI scope, and the switch handler
   holds such a request to `mode: generation` without `force` or `pin`
   (`403 auth.forbidden` otherwise), which is all the gate ever asks for.
4. **The gate reads the token on every request and sends it to hal0-api
   only.** It is an unredirected `Authorization: Bearer` header, sent through
   an opener with no proxy and no redirects. Without a usable token the gate
   sends no credential, as before.
5. **Replacing the token is deleting it.** The next `img` start mints a new
   one. Nothing rotates it on a schedule: its reach is the three requests
   above, which the gate needs anyway.

## Consequences

- Auth-enforced boxes, and boxes bound to one address with an admin key,
  generate images with the GPU guard in place. `HAL0_COMFYUI_GATE_FAIL_OPEN`
  is no longer needed for them.
- An `img` container started before the token existed sends none. It needs
  one `hal0 slot restart img`, the same restart ADR-0038 already asks for
  after an update.
- Code running inside ComfyUI can read the token. It can then read the
  arbiter's state, hold the GPU for a prompt for about 3 seconds, and ask for
  image mode, which unloads the LLM slots: what the gate itself does. It
  cannot pin image mode, force a switch into LLM mode, or reach any other
  route.
- After a rollback to 1.6.x, a box that enforces auth refuses image prompts:
  1.6.x does not know the token.
- A new gate request needs its route added to `_GATE_REQUESTS`, with a test,
  in the change that adds the route.

## Alternatives considered

- **The admin key in the container.** Rejected: it opens every route to
  anything that runs in ComfyUI.
- **The client key.** It opens every CLIENT route (`/v1` and the rest) and
  still not the ADMIN routes the gate calls.
- **The token in the unit's `Environment=`.** Unit files are world-readable,
  and `podman inspect` shows a container's environment.
- **Exempting the gate's routes for loopback peers.** Every local process
  would get them, and a box bound to one address would still refuse the gate.

## References

- #3058; ADR-0038 (the gate fails closed); `docs/operate/auth.mdx`,
  "ComfyUI's GPU gate credential".
- `src/hal0/comfyui/gate_credential.py` (mint, read, verify),
  `src/hal0/api/auth.py` (`_GATE_REQUESTS`, `_gate_admits`, `GATE_SCOPE_KEY`),
  `src/hal0/api/routes/comfyui.py` (`comfyui_switchover`),
  `src/hal0/providers/comfyui.py` (`_gate_credential_mount`),
  `src/hal0/comfyui/assets/hal0_gpu_gate.py` (`_read_token`, `_hal0_request`).
