// One line that is true of the whole page or card under it: "writes are paused", "shown as of
// 5 Nov 2026 until a refresh succeeds".
//
// The bar itself is always neutral. The chip in it carries the tone and says the state as a
// word, so the bar reads the same without colour.
//
// A notice that is on the page when it loads has no live role. Pass `live` only for one that
// appears because of something the operator just did, so it is announced once.
import '../memory-area.css'
import { StatusChip } from './StatusChip'

/**
 * @param {{
 *   tone?: 'ok'|'warn'|'err'|'info'|'neutral',
 *   status: string,
 *   children?: import('react').ReactNode,
 *   action?: import('react').ReactNode,
 *   inCard?: boolean,
 *   live?: boolean,
 *   'data-testid'?: string,
 * }} props `status` is the chip's word. `children` is one sentence. `action` is one link.
 */
export function NoticeBar({
  tone = 'neutral',
  status,
  children,
  action,
  inCard = false,
  live = false,
  'data-testid': testId,
}) {
  return (
    <div
      className={inCard ? 'ma-notice ma-notice-incard' : 'ma-notice'}
      role={live ? 'status' : undefined}
      data-testid={testId}
    >
      <StatusChip tone={tone}>{status}</StatusChip>
      <p className="ma-notice-text">{children}</p>
      {action}
    </div>
  )
}
