// Where the ComfyUI editor can be opened from this page: the link when the page is on the box, the
// editor binds the LAN, or the operator declared a public URL; else how to reach it. The rule is
// editor-reach.ts's.


import { useConfigUrls } from '@/api/hooks/useConfigUrls'
import { PROXY_NEEDS_PUBLIC_URL, publicUrlHint } from '@/api/publicUrlHint'
import { EDITOR_PORT, isOnBox, tunnelCommand } from './editor-reach'

export interface EditorAccess {
  /** The editor can be opened from this page. */
  onBox: boolean
  /** ComfyUI binds the LAN (`comfyui_lan`): the derived link answers from any machine. */
  lan: boolean

  /** The link to open when `onBox`, else null: an off-box page has nothing to link to. */
  url: string | null
  /** Server-provided direct IP fallback, when available. */
  ipUrl: string | null
  /** The command that forwards the editor's port here (for the page that is not on the box). */
  tunnel: string
  /**
   * Set when hal0 gave no link because the dashboard is behind a proxy and no
   * HAL0_COMFYUI_PUBLIC_URL is declared: the hint to show in place of the link, else null.
   */
  needsPublicUrl: string | null
}

export function useEditorAccess(): EditorAccess {
  const urls = useConfigUrls().data
  const link = urls?.comfyui || null
  const ipUrl = urls?.comfyui_ip || null
  const host = typeof window === 'undefined' ? '' : window.location.hostname
  const lan = urls?.comfyui_lan === true
  // hal0 sends no link behind a proxy without a declared URL: the page's host:8188 is the proxy's.
  if (urls?.comfyui_reason === PROXY_NEEDS_PUBLIC_URL) {
    return { onBox: false, lan, url: null, ipUrl: null, tunnel: tunnelCommand(host), needsPublicUrl: publicUrlHint('COMFYUI') }
  }
  const onBox = isOnBox(host, urls?.comfyui_public, lan)


  return { onBox, lan, url: onBox ? link : null, ipUrl: onBox ? ipUrl : null, tunnel: tunnelCommand(host), needsPublicUrl: null }

}
