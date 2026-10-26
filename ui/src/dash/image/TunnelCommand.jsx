// The copyable SSH command that forwards the ComfyUI editor's port to the viewer's machine, for a
// page that is not on the box (editor-reach.ts has the rule and the command).
//
// ComfyUI listens on the box's loopback only. One block for every surface that has to say so: the
// Engine tab's editor card and the Services page.
import './image-engine.css'
import { copyTextToClipboard } from '@/lib/clipboard'
import { EDITOR_PORT } from './editor-reach'

const note = (msg, kind) => window.__hal0Toast && window.__hal0Toast(msg, kind)

/**
 * @param {object} props
 * @param {string} props.tunnel  the command (`useEditorAccess().tunnel`)
 * @param {string} [props.testid]  prefix of the data-testids (`<testid>-tunnel`, `<testid>-tunnel-copy`)
 */
export function TunnelCommand({ tunnel, testid = 'editor' }) {
  // The page that needs the command is off the box, often plain http on a LAN, where there is no
  // navigator.clipboard: the shared helper falls back to execCommand and answers what happened.
  const copy = async () => {
    if (await copyTextToClipboard(tunnel)) note('Command copied', 'ok')
    else note('Could not copy: select the command and copy it by hand', 'err')
  }
  return (
    <div className="ie-tunnel-block">
      <div className="ie-tunnel">
        <code data-testid={`${testid}-tunnel`}>{tunnel}</code>
        <button type="button" className="btn ghost sm" data-testid={`${testid}-tunnel-copy`} onClick={copy}>
          Copy
        </button>
      </div>
      <p className="ie-muted">Run it on your own machine, then open http://127.0.0.1:{EDITOR_PORT} there.</p>
    </div>
  )
}
