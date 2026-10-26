// Where the ComfyUI editor can be opened from this page: the link when the page is on the box (or
// the operator declared a public URL), else how to reach it. The rule is editor-reach.ts's.

import { useConfigUrls } from '@/api/hooks/useConfigUrls'
import { EDITOR_PORT, isOnBox, tunnelCommand } from './editor-reach'

export interface EditorAccess {
  /** The editor can be opened from this page. */
  onBox: boolean
  /** The link to open when `onBox`, else null: an off-box page has nothing to link to. */
  url: string | null
  /** The command that forwards the editor's port here (for the page that is not on the box). */
  tunnel: string
}

export function useEditorAccess(): EditorAccess {
  const urls = useConfigUrls().data
  const link = urls?.comfyui || null
  const host = typeof window === 'undefined' ? '' : window.location.hostname
  const onBox = isOnBox(host, urls?.comfyui_public)
  // Before /api/config/urls answers, a page on the box still knows where its own editor is. (An
  // off-box page gets no link until the answer says it was declared; that brief state reads as
  // off the box.)
  const url = onBox ? (link ?? `http://${host}:${EDITOR_PORT}`) : null
  return { onBox, url, tunnel: tunnelCommand(host) }
}
