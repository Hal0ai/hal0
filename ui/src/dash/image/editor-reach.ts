// Is this page on the box itself? That decides whether the ComfyUI editor link can work from here.
//
// ComfyUI listens on the box's loopback only (the host-net fence in providers/container.py), so
// its web UI answers a browser on the box, or one that tunnels to it, and no other. Every place
// that links to the editor asks this one question: the Engine tab's editor card, the Models tab's
// "Open in ComfyUI", the status line's "Open editor" and the Services page.
//
// `GET /api/config/urls` answers `comfyui`, the editor link, and `comfyui_public`. The link is
// `http://<the host the dashboard was reached on>:8188` unless the operator declared
// HAL0_COMFYUI_PUBLIC_URL (a reverse proxy in front of the editor); the flag is true only for
// that declared URL. The derived link says nothing about whether the port answers from where the
// page is, so the flag, not the link, is what tells the two apart. A declared URL answers wherever
// the operator declared it for.

/** ComfyUI's port, on the box's loopback. */
export const EDITOR_PORT = 8188

/** What an off-box viewer is told. */
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
 * `comfyuiPublic` is `/api/config/urls` `comfyui_public`.
 *
 * - A loopback page is on the box.
 * - A public URL the operator declared is reachable from where it was declared.
 * - Anything else is not: the link hal0 derives from the page's own host does not answer from
 *   another machine. A server that does not send the flag (an older hal0) counts as not declared.
 */
export function isOnBox(pageHostname: string, comfyuiPublic: boolean | null | undefined): boolean {
  return isLoopbackHost(pageHostname) || comfyuiPublic === true
}

/** The command that forwards the editor's port to this machine. `<user>` is the operator's. */
export function tunnelCommand(pageHostname: string): string {
  return `ssh -L ${EDITOR_PORT}:127.0.0.1:${EDITOR_PORT} <user>@${bare(pageHostname)}`
}
