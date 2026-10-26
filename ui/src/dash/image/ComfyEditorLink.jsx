// The ComfyUI editor link on the Services surfaces (the Services page, the Services board card and
// the Overview widget), under the one rule the Image Gen pane uses: the link opens from any page
// while ComfyUI binds the LAN (the default), and otherwise from a page on the box or a public URL
// the operator declared, and from nowhere else. Where it cannot open, the button is disabled and
// the page says why and gives the SSH command. `useEditorAccess` (editor-reach.ts has the rule)
// answers all of it; this file only draws it, so no surface keeps a rule of its own.
//
// The link is `useEditorAccess().url`, the one `GET /api/config/urls` derives for the browser. A
// service row's own `url` is the API's view of ComfyUI (the host the request named) and says
// nothing about whether this page can open it.

import './image-engine.css'
import { OFF_BOX_NOTE } from './editor-reach'
import { TunnelCommand } from './TunnelCommand'
import { useEditorAccess } from './useEditorAccess'

/**
 * The icon button that opens the editor: a live button on the box, a disabled one elsewhere (its
 * wrapper carries the reason, since a disabled button shows no tooltip of its own).
 *
 * @param {object} props
 * @param {string} props.testid  data-testid of the button (`<testid>-wrap` on the off-box wrapper)
 * @param {string} [props.className]
 * @param {import('react').ReactNode} props.children  the icon
 */
export function EditorOpenButton({ testid, className = 'svc-btn', children }) {
  const access = useEditorAccess()
  if (access.onBox && access.url) {
    const url = access.url
    return (
      <button
        type="button"
        className={className}
        title="Open ComfyUI"
        data-testid={testid}
        onClick={() => window.open(url, '_blank', 'noopener')}
      >
        {children}
      </button>
    )
  }
  return (
    <span title={`${OFF_BOX_NOTE}. ${access.tunnel}`} data-testid={`${testid}-wrap`}>
      <button
        type="button"
        className={className}
        disabled
        aria-label={`Open ComfyUI. ${OFF_BOX_NOTE}`}
        data-testid={testid}
      >
        {children}
      </button>
    </span>
  )
}

/**
 * What an off-box page is told: the note and the command. Nothing on the box.
 *
 * @param {object} props
 * @param {string} props.testid  prefix of the data-testids (`<testid>-offbox`, `<testid>-tunnel`)
 */
export function EditorOffBox({ testid }) {
  const access = useEditorAccess()
  if (access.onBox) return null
  return (
    <div className="ie-tunnel-block">
      <span className="ie-offbox" data-testid={`${testid}-offbox`}>
        {OFF_BOX_NOTE}
      </span>
      <TunnelCommand tunnel={access.tunnel} testid={testid} />
    </div>
  )
}
