/**
 * useNpuSwapStatus — poll /api/npu/swap-status and reflect into the
 * slots-scoped banner store.
 *
 * Background — PR-20 / plan §5.3 / ADR-0009:
 *   When the operator picks a new NPU chat model the FLM trio must
 *   tear down the current process and warm a new one. Lemonade's
 *   /v1/health returns the prior model in ``loaded[]`` for ~14s while
 *   the new model loads. This composable polls a hal0-side merge of
 *   the slot TOML state + lemond health snapshot so the dashboard can
 *   surface a single "Swap incoming" banner during that window.
 *
 * Polling cadence — 2s. Trades off latency (banner appears within 2s
 * of swap start) against load on lemond /v1/health (single GET per
 * tick, identical to the other dashboard pollers). The banner uses
 * the `npu-swap` catalog entry; copy is overridden per poll to carry
 * the live ``from_model`` → ``to_model`` strings.
 *
 * Caller contract — call once from Slots.vue's onMounted. Returns a
 * reactive ``status`` ref + the disconnect handle for unmount cleanup.
 * The composable owns the banner show/dismiss lifecycle; the caller
 * does not need to wire anything else.
 */
import { onUnmounted, ref } from 'vue'
import { api } from './useApi.js'
import { useBannerStore } from '../stores/banner.js'

const POLL_INTERVAL_MS = 2000
const BANNER_ID = 'npu-swap'

export function useNpuSwapStatus({ intervalMs = POLL_INTERVAL_MS } = {}) {
  const banners = useBannerStore()
  const status = ref({ in_progress: false, from_model: null, to_model: null })
  let timer = null
  let stopped = false

  async function poll() {
    try {
      const body = await api('/api/npu/swap-status')
      // Defensive: every miss degrades to "no swap" so the banner never
      // sticks on a transient error.
      const next = {
        in_progress: !!body?.in_progress,
        from_model: body?.from_model ?? null,
        to_model: body?.to_model ?? null,
      }
      status.value = next
      if (next.in_progress) {
        banners.show(BANNER_ID, {
          heading: `Swapping NPU chat: ${next.from_model || '—'} → ${next.to_model || '—'}`,
          body: 'Voice + embed paused while FLM restarts. Coresident slots will resume automatically.',
        })
      } else if (banners.isActive(BANNER_ID)) {
        banners.dismiss(BANNER_ID)
      }
    } catch (_err) {
      // Network blip / 5xx — treat as "no swap" but DON'T flap the
      // banner away on a single failed poll if it was already up. The
      // next successful poll will reconcile.
      status.value = { in_progress: false, from_model: null, to_model: null }
    }
  }

  async function start() {
    if (timer != null) return
    await poll()
    timer = setInterval(() => {
      if (stopped) return
      void poll()
    }, intervalMs)
  }

  function stop() {
    stopped = true
    if (timer != null) {
      clearInterval(timer)
      timer = null
    }
    // Clean up our banner on unmount so a stale "swap incoming" doesn't
    // linger when the view changes.
    if (banners.isActive(BANNER_ID)) banners.dismiss(BANNER_ID)
  }

  onUnmounted(stop)

  return { status, start, stop, poll }
}
