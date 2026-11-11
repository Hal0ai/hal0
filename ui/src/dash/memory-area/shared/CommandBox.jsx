// A CLI command the operator may copy: the command in mono, and an icon-only copy button.
//
// The button has no visible text, so it is named by `aria-label`. The outcome of a copy is
// written into a `role="status"` region that is empty until the button is used: nothing is
// announced when the page loads, and "Copied" is announced once the clipboard really holds the
// command. A copy the browser refuses says so instead of claiming success.
import { useEffect, useRef, useState } from 'react'
import '../memory-area.css'
import { copyTextToClipboard } from '@/lib/clipboard'

/** How long the outcome stays on screen. */
const STATUS_MS = 2500

/**
 * @param {{
 *   command: string,
 *   copyLabel?: string,
 *   'data-testid'?: string,
 * }} props
 */
export function CommandBox({ command, copyLabel = 'Copy command', 'data-testid': testId }) {
  const [status, setStatus] = useState('')
  const timer = useRef(null)

  useEffect(() => () => clearTimeout(timer.current), [])

  const copy = async () => {
    const ok = await copyTextToClipboard(command)
    setStatus(ok ? 'Copied' : 'Could not copy')
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setStatus(''), STATUS_MS)
  }

  return (
    <div className="ma-cmd" data-testid={testId}>
      <span className="ma-cmd-prompt" aria-hidden="true">
        $
      </span>
      <code className="ma-cmd-text">{command}</code>
      <span className="ma-cmd-status" role="status">
        {status}
      </span>
      <button type="button" className="btn ghost sm ma-icon-btn" aria-label={copyLabel} onClick={copy}>
        <svg
          aria-hidden="true"
          width="13"
          height="13"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
          <path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2" />
        </svg>
      </button>
    </div>
  )
}
