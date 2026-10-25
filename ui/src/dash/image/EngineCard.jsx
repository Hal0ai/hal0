// The Engine tab's engine card: what the ComfyUI engine is doing, where it runs and listens, the
// state of its GPU gate, and the buttons that act on it (Restart engine, Logs, Stop image mode).
//
// Restart and Stop are the status line's own actions (useStatusLineActions): the calls, the
// unpin-first order and the confirm before a running render is dropped live there once.
import { useEffect, useRef, useState } from 'react'
import { useComfyuiLogs } from '@/api/hooks/useComfyui'
import { Modal } from '@/dash/primitives.jsx'
import { EDITOR_PORT } from './editor-reach'
import {
  STOPPED_BODY,
  STOPPED_HEAD,
  UNREACHABLE_HEAD,
  engineNotice,
  gateRow,
} from './engine-pure'
import { ForceStopConfirm } from './ImageStatusLine'
import { actionFacts } from './status-line-pure'
import { useStatusLineActions } from './useStatusLineActions'

// Lines in the Logs modal, and in the notice under an engine that does not answer.
const LOG_TAIL = 200
const NOTICE_LINES = 3
const NOTICE_REFRESH_MS = 5_000

const PILL = {
  running: 'running',
  generating: 'generating',
  starting: 'starting…',
  stopped: 'stopped',
  error: 'error',
}

// The newest log line is the last: keep it in view.
function useStickToEnd(ref, dep) {
  useEffect(() => {
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [ref, dep])
}

// A refetch the effects below may call without depending on its identity.
function useLatest(value) {
  const ref = useRef(value)
  useEffect(() => {
    ref.current = value
  })
  return ref
}

function LogsModal({ open, onClose, container }) {
  const logs = useComfyuiLogs(LOG_TAIL)
  const refetch = useLatest(logs.refetch)
  useEffect(() => {
    if (open) void refetch.current()
  }, [open, refetch])
  const lines = logs.data?.lines ?? []
  const pre = useRef(null)
  useStickToEnd(pre, lines.length)

  let body
  if (logs.isError) {
    body = (
      <div className="ie-fail" role="alert" data-testid="engine-logs-error">
        <span>{logs.error?.message || 'The log could not be read.'}</span>
        <button type="button" className="btn ghost sm" onClick={() => void logs.refetch()}>
          Retry
        </button>
      </div>
    )
  } else if (logs.data === undefined) {
    body = (
      <div className="ie-muted" data-testid="engine-logs-loading">
        Reading the log…
      </div>
    )
  } else if (lines.length === 0) {
    body = (
      <div className="ie-muted" data-testid="engine-logs-empty">
        No log lines. The engine has not logged yet, or journalctl is not available to hal0.
      </div>
    )
  } else {
    body = (
      <pre className="ie-logs" ref={pre} data-testid="engine-logs-pre">
        {lines.join('\n')}
      </pre>
    )
  }
  return (
    <Modal open={open} onClose={onClose} title="ComfyUI logs" eyebrow={container || undefined} width={900}>
      {body}
    </Modal>
  )
}

// The container is up and ComfyUI does not answer on its port: the engine is still booting, or it
// is wedged. The last lines of its journal say which, so they are read while this shows.
function UnreachableNotice({ active }) {
  const logs = useComfyuiLogs(NOTICE_LINES)
  const refetch = useLatest(logs.refetch)
  useEffect(() => {
    if (!active) return undefined
    void refetch.current()
    const id = setInterval(() => void refetch.current(), NOTICE_REFRESH_MS)
    return () => clearInterval(id)
  }, [active, refetch])
  const lines = (logs.data?.lines ?? []).slice(-NOTICE_LINES)
  return (
    <div className="ie-notice warn" data-testid="engine-unreachable">
      <b>{UNREACHABLE_HEAD}</b>
      <p>The container is running, but ComfyUI is not answering on its port.</p>
      {logs.data === undefined ? (
        <div className="ie-muted">Reading the log…</div>
      ) : lines.length === 0 ? (
        <div className="ie-muted">No log lines.</div>
      ) : (
        <pre className="ie-log" data-testid="engine-unreachable-log">
          {lines.join('\n')}
        </pre>
      )}
    </div>
  )
}

/**
 * @param {object} props
 * @param {import('@/api/hooks/useComfyui').ComfyuiStatus | undefined} props.status
 * @param {import('@/api/hooks/useComfyui').ArbiterEnvelope | undefined} props.env
 * @param {boolean} [props.visible]
 */
export function EngineCard({ status, env, visible = true }) {
  const [logsOpen, setLogsOpen] = useState(false)
  const arbiter = env?.arbiter ?? null
  const actions = useStatusLineActions({ resetKey: `${status?.engine ?? ''}|${arbiter?.mode ?? ''}` })
  const ctx = actionFacts(env, status)
  const busy = actions.pending != null

  const notice = engineNotice(status)
  const gate = gateRow(status?.gate)
  const engine = status?.engine

  return (
    <section className="ie-card ie-engine" data-testid="engine-card" aria-label="ComfyUI engine">
      <header className="ie-card-h">
        <span className="ie-title">ComfyUI</span>
        <span className="ie-sub">image-gen engine</span>
        {engine && (
          <span className="ie-pill" data-state={engine} data-testid="engine-pill">
            <span className="dot" aria-hidden="true" />
            {PILL[engine] ?? engine}
          </span>
        )}
        <span className="ie-grow" />
        <div className="ie-actions">
          <button
            type="button"
            className="btn ghost sm"
            data-testid="engine-restart"
            disabled={busy}
            onClick={() => void actions.run('restart_engine')}
          >
            Restart engine
          </button>
          <button type="button" className="btn ghost sm" data-testid="engine-logs" onClick={() => setLogsOpen(true)}>
            Logs
          </button>
          {arbiter?.mode === 'img' && (
            <button
              type="button"
              className="btn sm"
              data-testid="engine-stop"
              disabled={busy}
              onClick={() => void actions.run('stop', ctx)}
            >
              Stop image mode
            </button>
          )}
        </div>
      </header>

      <div className="ie-card-b">
        {!status ? (
          <div className="ie-muted" data-testid="engine-loading">
            Loading engine status…
          </div>
        ) : (
          <>
            <dl className="ie-rows">
              {engine && (
                <div className="ie-row" data-testid="engine-row-state">
                  <dt>State</dt>
                  <dd>{engine}</dd>
                </div>
              )}
              {status.container?.name && (
                <div className="ie-row" data-testid="engine-row-container">
                  <dt>Container</dt>
                  <dd>
                    Quadlet · <code>{status.container.name}</code>
                    {status.container.state ? <span className="ie-dim"> · {status.container.state}</span> : null}
                  </dd>
                </div>
              )}
              <div className="ie-row" data-testid="engine-row-listen">
                <dt>Network</dt>
                <dd>listens on loopback</dd>
              </div>
              <div className="ie-row" data-testid="engine-row-editor">
                <dt>Editor</dt>
                <dd>
                  <code>127.0.0.1:{EDITOR_PORT}</code>
                  <span className="ie-dim"> · on the box itself, or through an SSH tunnel</span>
                </dd>
              </div>
              {gate && (
                <div className="ie-row" data-testid="engine-row-gate" data-gate-state={status.gate.state}>
                  <dt>GPU gate</dt>
                  <dd>
                    <span className={`ie-tone ${gate.tone}`}>{gate.label}</span>
                    {gate.hint ? <span className="ie-dim"> · {gate.hint}</span> : null}
                  </dd>
                </div>
              )}
            </dl>

            {notice === 'stopped' && (
              <div className="ie-notice" data-testid="engine-stopped">
                <b>{STOPPED_HEAD}</b>
                <p>{STOPPED_BODY}</p>
              </div>
            )}
            {notice === 'unreachable' && <UnreachableNotice active={visible} />}
          </>
        )}

        {actions.error && (
          <div className="ie-fail" role="alert" data-testid="engine-error">
            {actions.error}
          </div>
        )}
      </div>

      <LogsModal open={logsOpen} onClose={() => setLogsOpen(false)} container={status?.container?.name} />
      <ForceStopConfirm confirm={actions.confirm} />
    </section>
  )
}
