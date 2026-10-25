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
  const link = useConfigUrls().data?.comfyui || null
  const host = typeof window === 'undefined' ? '' : window.location.hostname
  const onBox = isOnBox(host, link)
  // Without a link only a loopback page is on the box (isOnBox), and it knows where its own
  // editor is before /api/config/urls answers.
  const url = onBox ? (link ?? `http://${host}:${EDITOR_PORT}`) : null
  return { onBox, url, tunnel: tunnelCommand(host) }
}
