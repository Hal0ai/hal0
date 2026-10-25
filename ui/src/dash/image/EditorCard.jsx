// The Engine tab's editor card: the ComfyUI editor, and the workflows it has for the installed
// models (plus the operator's own files).
//
// ComfyUI listens on the box's loopback only, so its editor opens from a page on the box and, from
// anywhere else, through an SSH tunnel (editor-reach.ts has the rule). Off the box the button and
// the chips are disabled, and the card says so and gives the command. A chip is not a deep link:
// ComfyUI does not open a workflow from a link, so every chip opens the editor and the workflow is
// picked in its Workflows sidebar.
import { useComfyuiModels, useComfyuiWorkflows } from '@/api/hooks/useComfyui'
import { OFF_BOX_NOTE } from './editor-reach'
import { EDITOR_NOTE, WORKFLOW_HINT, workflowChips } from './engine-pure'
import { useEditorAccess } from './useEditorAccess'

const note = (msg, kind) => window.__hal0Toast && window.__hal0Toast(msg, kind)

const OPEN_LABEL = 'Open ComfyUI editor ↗'

function Chip({ chip, url }) {
  const body = (
    <>
      <span>{chip.label}</span>
      {chip.custom && <span className="tag">custom</span>}
    </>
  )
  const common = {
    className: 'ie-chip',
    'data-testid': 'editor-chip',
    'data-workflow': chip.id,
    'data-custom': String(chip.custom),
    title: chip.custom ? `${chip.id}.json` : undefined,
  }
  return url ? (
    <a {...common} href={url} target="_blank" rel="noopener noreferrer">
      {body}
    </a>
  ) : (
    <span {...common} aria-disabled="true">
      {body}
    </span>
  )
}

export function EditorCard() {
  const models = useComfyuiModels().data
  const workflows = useComfyuiWorkflows().data
  const access = useEditorAccess()
  const chips = workflowChips(models?.models, workflows)
  const url = access.onBox ? access.url : null

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(access.tunnel)
      note('Command copied', 'ok')
    } catch {
      note('Could not copy: select the command and copy it by hand', 'err')
    }
  }

  return (
    <section className="ie-card" data-testid="editor-card" aria-label="ComfyUI editor">
      <header className="ie-card-h">
        <span className="ie-title">ComfyUI editor</span>
        <span className="ie-sub">video, edit and upscale run here</span>
      </header>
      <div className="ie-card-b">
        {chips.length > 0 ? (
          <div className="ie-chips" data-testid="editor-chips">
            {chips.map((c) => (
              <Chip key={`${c.custom ? 'custom' : 'model'}:${c.id}`} chip={c} url={url} />
            ))}
          </div>
        ) : models ? (
          <div className="ie-muted" data-testid="editor-no-chips">
            No workflows yet. Installing a model on the Models tab adds its workflow.
          </div>
        ) : null}

        <div className="ie-open">
          {url ? (
            <a className="btn sm" href={url} target="_blank" rel="noopener noreferrer" data-testid="editor-open">
              {OPEN_LABEL}
            </a>
          ) : (
            <button
              type="button"
              className="btn sm"
              disabled
              aria-describedby="editor-offbox"
              data-testid="editor-open"
            >
              {OPEN_LABEL}
            </button>
          )}
          {!access.onBox && (
            <span className="ie-offbox" id="editor-offbox" data-testid="editor-offbox">
              {OFF_BOX_NOTE}
            </span>
          )}
        </div>

        {!access.onBox && (
          <div className="ie-tunnel-block">
            <div className="ie-tunnel">
              <code data-testid="editor-tunnel">{access.tunnel}</code>
              <button type="button" className="btn ghost sm" data-testid="editor-tunnel-copy" onClick={copy}>
                Copy
              </button>
            </div>
            <p className="ie-muted">Run it on your own machine, then open http://127.0.0.1:8188 there.</p>
          </div>
        )}

        <p className="ie-muted" data-testid="editor-note">
          {EDITOR_NOTE}
        </p>
        <p className="ie-muted" data-testid="editor-workflow-hint">
          {WORKFLOW_HINT}
        </p>
      </div>
    </section>
  )
}
