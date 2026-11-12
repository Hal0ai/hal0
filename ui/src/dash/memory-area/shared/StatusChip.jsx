// A status, said as a word: a dot, then one or two lower-case words.
//
// The tone only tints the chip. The word carries the meaning, so the chip reads the same without
// colour: never pass a tone and leave the word generic. Words come from STATUS_WORDS and nowhere
// else; a chip is never a sentence and never a bare number. The two counted forms are
// `waiting on step N` and `N failures in 6 h`, where the count is the status.
import '../memory-area.css'

/** The fixed status vocabulary. A caller picks one of these (or a counted form, see above). */
export const STATUS_WORDS = Object.freeze([
  'reachable',
  'unreachable',
  'working',
  'failing',
  'failed',
  'stale',
  'fresh',
  'mixed',
  'waiting',
  'running',
  'done',
  'stopped',
  'not started',
  'empty',
  'off',
])

// ok: working, done, fresh. warn: behind, stale, mixed; no data lost. err: failing, stopped,
// unreachable. info: in progress. neutral: waiting, not started, off.
const TONE_CLASS = {
  ok: 'ok',
  warn: 'warn',
  err: 'err',
  info: 'info',
  neutral: 'ma-chip-neutral',
}

/**
 * @param {{
 *   tone?: 'ok'|'warn'|'err'|'info'|'neutral',
 *   children?: import('react').ReactNode,
 *   'data-testid'?: string,
 * }} props
 */
export function StatusChip({ tone = 'neutral', children, 'data-testid': testId }) {
  const toneClass = TONE_CLASS[tone] ?? TONE_CLASS.neutral
  const word = typeof children === 'string' ? children.toLowerCase() : children
  return (
    <span className={`chip ma-chip ${toneClass}`} data-tone={tone} data-testid={testId}>
      <span className="ma-chip-dot" aria-hidden="true" />
      {word}
    </span>
  )
}
