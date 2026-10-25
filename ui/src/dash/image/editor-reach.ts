// Is this page on the box itself? That decides whether the ComfyUI editor link can work from here.
//
// ComfyUI listens on the box's loopback only (the host-net fence in providers/container.py), so
// its web UI answers a browser on the box, or one that tunnels to it, and no other. Every place
// that links to the editor asks this one question: the Engine tab's editor card, the Models tab's
// "Open in ComfyUI", the status line's "Open editor" and the Services page.
//
// `GET /api/config/urls` (`comfyui`) is `http://<the host the dashboard was reached on>:8188`
// unless the operator declared HAL0_COMFYUI_PUBLIC_URL (a reverse proxy in front of the editor).
// So the link's host is the page's host on a stock install, and says nothing about whether the
// port answers from where the page is. It answers on the box and, for a public URL the operator
// declared, wherever they declared it for.

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
 * Whether the editor link `editorUrl` (`/api/config/urls` `comfyui`) works from a page served at
 * `pageHostname`.
 *
 * - A loopback page is on the box.
 * - A link that is not the one hal0 derives from the page's own host (`http://<page host>:8188`)
 *   was declared by the operator, as a public URL, and is reachable from where it was declared.
 * - The derived link is not reachable from any other machine; no link is nothing to open.
 */
export function isOnBox(pageHostname: string, editorUrl: string | null | undefined): boolean {
  if (isLoopbackHost(pageHostname)) return true
  if (!editorUrl) return false
  let link: URL
  try {
    link = new URL(editorUrl)
  } catch {
    return false
  }
  const derived =
    link.protocol === 'http:' &&
    bare(link.hostname) === bare(pageHostname) &&
    link.port === String(EDITOR_PORT) &&
    (link.pathname === '/' || link.pathname === '')
  return !derived
}

/** The command that forwards the editor's port to this machine. `<user>` is the operator's. */
export function tunnelCommand(pageHostname: string): string {
  return `ssh -L ${EDITOR_PORT}:127.0.0.1:${EDITOR_PORT} <user>@${bare(pageHostname)}`
}
