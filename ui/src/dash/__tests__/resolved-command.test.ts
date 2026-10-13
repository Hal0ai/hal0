// resolvedCommandText (resolved-command.js) — the slot drawer's "Resolved
// command" box. GET /api/slots/{name}/resolved answers `argv: null` with empty
// provenance for a slot llama-server does not launch (ADR-0029); the box must
// render that as an answer, not as "still loading", and never throw.
import { describe, expect, it } from 'vitest'
import {
  NOT_LLAMA_TEXT,
  NOT_LOADED_TEXT,
  resolvedCommandText,
} from '../resolved-command.js'

describe('resolvedCommandText', () => {
  it('joins the deduped argv from /resolved', () => {
    const data = { name: 'chat', argv: ['img', '--model', 'm'], provenance: [], removed: 0 }
    expect(resolvedCommandText(data, { resolved_command: ['stale'] })).toBe(
      'img \\\n  --model \\\n  m',
    )
  })

  it('says the command is not llama-server composed when argv is null', () => {
    const data = { name: 'voice', argv: null, provenance: [], removed: 0 }
    expect(resolvedCommandText(data, { name: 'voice' })).toBe(NOT_LLAMA_TEXT)
    expect(resolvedCommandText(data, { name: 'voice', resolved_command: null })).toBe(
      NOT_LLAMA_TEXT,
    )
  })

  it('falls back to the list payload command when /resolved has no argv', () => {
    const data = { name: 'x', argv: null, provenance: [], removed: 0 }
    expect(resolvedCommandText(data, { resolved_command: ['img', '-b', '8'] })).toBe(
      'img \\\n  -b \\\n  8',
    )
  })

  it('shows the not-loaded sentinel until /resolved has answered', () => {
    expect(resolvedCommandText(undefined, { name: 'x' })).toBe(NOT_LOADED_TEXT)
    expect(resolvedCommandText(undefined, undefined)).toBe(NOT_LOADED_TEXT)
  })
})
