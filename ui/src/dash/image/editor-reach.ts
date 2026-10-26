// Can the ComfyUI editor link work from this page? Every place that links to the editor asks this
// one question: the Engine tab's editor card, the Models tab's "Open in ComfyUI", the status
// line's "Open editor" and the Services page.
//
// `GET /api/config/urls` answers `comfyui`, the editor link, plus two flags. The link is
// `http://<the host the dashboard was reached on>:8188` unless the operator declared
// HAL0_COMFYUI_PUBLIC_URL (a reverse proxy in front of the editor); `comfyui_public` is true only
// for that declared URL, which answers wherever the operator declared it for. `comfyui_lan` says
// whether ComfyUI binds the LAN ([network].comfyui_listen = "lan", the default, ADR-0041): then
// the derived link answers from any machine. Under "loopback" (the host-net fence in
// providers/container.py) the derived link answers a browser on the box, or one that tunnels to
// it, and no other; the link itself says nothing about which, so the flags, not the link, decide.

/** ComfyUI's port. */
export const EDITOR_PORT = 8188

/** What an off-box viewer is told while ComfyUI is fenced to the box's loopback. */
export const OFF_BOX_NOTE = 'Only reachable on the box itself'


// `location.hostname` keeps the brackets of an IPv6 literal ("[::1]"); URL.hostname does too.
const bare = (host: string) => host.trim().toLowerCase().replace(/^\[(.*)\]$/, '$1')

/** The page's host is this machine's own loopback. */
export function isLoopbackHost(hostname: string): boolean {
  const host = bare(hostname)
  return host === 'localhost' || host === '127.0.0.1' || host === '::1'
}

/**
 * Whether the ComfyUI editor can be opened from a page served at `pageHostname`.
 * `comfyuiPublic` and `comfyuiLan` are `/api/config/urls` `comfyui_public` and `comfyui_lan`.
 *
 * - A loopback page is on the box.
 * - A public URL the operator declared is reachable from where it was declared.
 * - While ComfyUI binds the LAN, the derived link answers from any machine.
 * - Anything else is not: the link hal0 derives from the page's own host does not answer from
 *   another machine. A server that sends neither flag (an older hal0) counts as fenced and
 *   not declared.
 */
export function isOnBox(
  pageHostname: string,
  comfyuiPublic: boolean | null | undefined,
  comfyuiLan: boolean | null | undefined = false,
): boolean {
  return isLoopbackHost(pageHostname) || comfyuiPublic === true || comfyuiLan === true
}


/** The command that forwards the editor's port to this machine. `<user>` is the operator's. */
export function tunnelCommand(pageHostname: string): string {
  return `ssh -L ${EDITOR_PORT}:127.0.0.1:${EDITOR_PORT} <user>@${bare(pageHostname)}`
}
