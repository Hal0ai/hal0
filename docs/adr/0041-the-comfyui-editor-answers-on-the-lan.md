# ADR-0041: ComfyUI's editor answers on the LAN by default

## Status

**ACCEPTED (2026-10-26).** An operator decision: people on the box's network
reach ComfyUI's web UI at `http://<host>:8188` and `http://<host>.local:8188`
the way they reach Open WebUI, without a reverse proxy or an SSH tunnel.

It amends the host-net loopback fence (`podman-unprivileged-findings.md`,
Issue 1, which `[slots].network_mode` documents) with one named exception, and
it supersedes the line in #3049 that the loopback bind "should stay" for
ComfyUI. The authenticated proxy #3049 asks for remains a possible option for a
box that keeps the fence; it is no longer the only supported path to the editor.

## Context

Every slot container hal0 renders binds its port to `127.0.0.1` under host
networking: `_loopback_fence_command` in `hal0.providers.container` rewrites
`--host 0.0.0.0` and `--listen 0.0.0.0` on every host-net plan, because a raw
slot port (llama-server, FLM, Kokoro, ComfyUI) has no authentication and
hal0-api is the authenticated front door for all of them. The img slot runs
host-networked on every box, so since aa46045e ComfyUI's web UI answered only on
the box itself.

That changed what the product had shipped. Before the fence ComfyUI ran with
`--listen 0.0.0.0`, the Services registry marked it mDNS-capable, and the
Discovery card announced `hal0-addon-comfyui` on `:8188` (#1037). After the
fence the announcement led other machines to a refused connection; #3078 then
removed it, called ComfyUI loopback-only everywhere, and taught the dashboard
to disable the editor link off the box beside an SSH command.

ComfyUI is not like the other slots. Its port is a web UI people use, not an
API hal0 fronts: the image-generation overhaul (ADR-0037, ADR-0038) relies on
the editor for video, image edit, upscale, custom workflows and plugins, and
the GPU gate (ADR-0040) already makes an editor prompt a first-class, guarded
request. An operator who installs hal0 for a household or a small team expects
the editor to open from a laptop on the same network, as Open WebUI does. The
SSH tunnel is a workaround for people who know what one is; the reverse proxy
needs DNS and a certificate the install never asked for.

ComfyUI has no sign-in of its own, so on the LAN anyone who can reach the port
can queue a workflow, read the output directory through its API, and manage
custom nodes if the Manager extension is installed. Open WebUI, the service the
operator compares it to, has accounts. The decision below accepts that
difference for the box's own network and keeps the fence available.

## Decision

1. **`[network].comfyui_listen`** in `hal0.toml` is the one knob: `"lan"`
   (the default) or `"loopback"`. `hal0.comfyui.reach` is its only reader;
   `comfyui_listen()` fails soft to the default so a config that cannot load
   never fences an editor the operator did not ask to fence.

2. **The plan says so, the renderer obeys.** `RuntimeLaunchPlan.lan_bind`
   (default `False`) tells the Quadlet renderer to leave a host-net plan's bind
   alone. Only the ComfyUI provider sets it, from the knob. Every other
   provider leaves it `False`, so the fence's invariant, "no LLM, TTS or STT
   slot ever renders a `0.0.0.0` bind under host net", still holds; the golden
   ComfyUI unit now carries `--listen 0.0.0.0`.

3. **The registry answers live.** `ServiceDef.mdns` says a service *can* be
   LAN-published; `hal0.services.registry.is_mdns_capable` says whether it *is*
   right now, and for ComfyUI that is the knob. Announce, the Discovery card's
   list and the row's `.local` URL all go through it, so a `"loopback"` box
   never announces a port nothing answers on, and a file announced in LAN mode
   is removed at the next Announce or Withdraw as before.

4. **The dashboard learns the bind from the API.** `GET /api/config/urls`
   adds `comfyui_lan` beside `comfyui_public`. `editor-reach.ts` opens the
   derived `http://<host>:8188` link when the page is on the box, the operator
   declared a public URL, or ComfyUI binds the LAN; the Engine tab's Network
   and Editor rows read `listens on the LAN` / `<host>:8188` or
   `listens on loopback` / `127.0.0.1:8188` accordingly. The SSH tunnel copy
   stays for `"loopback"`. An older server that sends neither flag reads as
   fenced.

5. **The change is a restart away.** A running img container keeps the bind it
   started with; the knob applies at the next `hal0 slot restart img`. The
   CHANGELOG carries that as a migration step for boxes updating from 1.7.0.

## Consequences

- On a stock install `http://<host>:8188` and, with Discovery on,
  `http://<host>.local:8188` open ComfyUI from any machine on the network. The
  Services page shows ComfyUI's `.local` URL again and the editor button is
  enabled everywhere.
- Anyone on that network can use ComfyUI without signing in. The docs say so
  where the knob is explained, and `"loopback"` restores the previous posture
  with the same SSH-tunnel and `HAL0_COMFYUI_PUBLIC_URL` paths.
- `[slots].publish_host` is unchanged and still a no-op under host net; the
  ComfyUI exception is its own knob so widening the editor never widens an
  LLM slot, and vice versa.
- #3049 stays open as an enhancement for `"loopback"` boxes, no longer as the
  only way to reach the editor off the box.

## Alternatives considered

- **An authenticated hal0-api proxy for ComfyUI (#3049's fix).** Keeps the
  fence, but ComfyUI's frontend and `/ws` would have to work under a path prefix
  or get their own port, and every editor request would cross hal0-api. The
  operator wanted the plain port for end users; the proxy is deferred, not
  rejected.
- **Honour `[slots].publish_host` under host net.** Consistent with its bridge
  meaning, but all-or-nothing: putting the editor on the LAN would put every
  LLM port there too. Rejected.
- **A per-slot `[image]` key in `img.toml`.** The slot table is tuning and
  placement (ADR-0029); where a service answers on the network is box posture,
  which `[network]` already holds. Rejected.
- **Default `"loopback"` with an opt-in.** Safer by default, but the editor
  would still look broken to every new install, which is the complaint this
  record answers. The operator chose `"lan"` as the default.

## References

- #3049 — the dashboard's Open ComfyUI link is refused from other machines.
- #3078 — removed the dead mDNS announcement and documented loopback.
- aa46045e — the host-net loopback fence (`podman-unprivileged-findings.md`).
- #1037 — unified companion-service management: registry, `/api/services`, mDNS.
- ADR-0037, ADR-0038, ADR-0040 — the image-generation overhaul this editor serves.
