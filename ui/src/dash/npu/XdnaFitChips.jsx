// The xdna fit as chips, the one rendering for every GGML surface (NPU Models rows, the GGML
// slot form, GGML lane cards): a fit chip (`✓ fused` / `per-op` / `—`), one chip naming what keeps a
// per-op model off the fused path, and — when a requantize fixes it and `fix` is set — a
// "copy fix" chip that copies the llama-quantize command. Full reasons live in the tooltips.
import { copyTextToClipboard } from '@/lib/clipboard'
import { GGML_REQUANT_CMD, xdnaBlocker } from './ggml-slot-pure'

const toast = (msg, kind = 'info') => typeof window !== 'undefined' && window.__hal0Toast && window.__hal0Toast(msg, kind)

const FIT_TITLE = {
  fused: 'Runs as one fused NPU kernel (fast)',
  'per-op': 'Runs on the NPU op by op (slower)',
}

export function copyRequantFix() {
  return copyTextToClipboard(GGML_REQUANT_CMD).then((ok) =>
    ok ? toast('Requantize command copied', 'info') : toast(`Copy failed; run: ${GGML_REQUANT_CMD}`, 'warn'),
  )
}

/**
 * @param {object} props
 * @param {{xdna_path: string | null, reason?: string} | null | undefined} props.row  the fit row; nullish = no fit data
 * @param {string} props.testid  test-id stem: `<stem>-fit`, `<stem>-why`, `<stem>-fix`
 * @param {boolean} [props.fix]  render the "copy fix" chip (off inside another button)
 */
export function XdnaFitChips({ row, testid, fix = true }) {
  const path = row?.xdna_path ?? null
  const blocker = xdnaBlocker(row)
  const tone = path === 'fused' ? ' ok' : path === 'per-op' ? ' warn' : ''
  return (
    <>
      <span className={`chip mono${tone}`} data-testid={`${testid}-fit`} data-fit={path || 'none'} title={FIT_TITLE[path] || row?.reason || 'no fit data'}>
        {path === 'fused' ? '✓ fused' : path || '—'}
      </span>
      {blocker && (
        <span className="chip mono" data-testid={`${testid}-why`} title={blocker.title}>
          {blocker.label}
        </span>
      )}
      {!blocker && path === 'per-op' && row?.reason && (
        <span className="chip mono" data-testid={`${testid}-why`} title={row.reason}>
          why?
        </span>
      )}
      {fix && blocker?.fix && (
        <button type="button" className="chip mono amber npt-fix" data-testid={`${testid}-fix`} title={`Copy: ${GGML_REQUANT_CMD}`} onClick={copyRequantFix}>
          copy fix
        </button>
      )}
    </>
  )
}
