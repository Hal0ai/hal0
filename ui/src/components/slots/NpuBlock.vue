<script setup>
/**
 * NpuBlock.vue — single-card NPU trio rollup.
 *
 * Mirrors slots.jsx::NpuBlock (lines 152-204) from the v2 design source.
 * The Strix Halo FLM toolbox multiplexes chat (llm) + transcription (asr)
 * + embedding on one process backed by the XDNA NPU, so we render the
 * three slots as sub-rows of a single card to make the coresident
 * relationship legible without re-reading three SlotCards.
 *
 * Dormant state ('no agent model'): rendered when the chat sub-row's
 * slot has no model — the swap dropdown becomes a CTA instead of a
 * static label and the trio meta line stays muted.
 */
import { computed } from 'vue'

const props = defineProps({
  /** All NPU-backed slots, in trio order: chat (llm) first, then passengers. */
  slots: { type: Array, required: true },
  /**
   * Optional NPU swap-in-progress signal from /api/npu/swap-status.
   * Shape: `{ in_progress, from_model, to_model }`. When `in_progress`
   * is true, the chat sub-row swaps its static label for a spinner +
   * "Loading <to_model>..." line. PR-20 / plan §5.3 / ADR-0009.
   */
  swapStatus: {
    type: Object,
    default: () => ({ in_progress: false, from_model: null, to_model: null }),
  },
})

const emit = defineEmits(['swap-chat'])

const swapInProgress = computed(() => !!props.swapStatus?.in_progress)
const swapTargetModel = computed(() => props.swapStatus?.to_model || '')

const chat = computed(() => props.slots.find((s) => deviceOf(s) === 'npu' && typeOf(s) === 'llm'))
const stt  = computed(() => props.slots.find((s) => deviceOf(s) === 'npu' && typeOf(s) === 'transcription'))
const emb  = computed(() => props.slots.find((s) => deviceOf(s) === 'npu' && typeOf(s) === 'embedding'))

const dormant = computed(() => !chat.value || !modelOf(chat.value))

function deviceOf(s)  { return s?.device || 'npu' }
function typeOf(s)    {
  // Prefer the canonical capability tag (`type`) over the concrete
  // provider tag (`kind`) — `kind` is often the implementation slug
  // (e.g. 'local', 'flm') and doesn't always map to a single capability.
  const candidates = [s?.type, s?.kind].map((v) => String(v || '').toLowerCase())
  const matches = (...vals) => candidates.some((c) => vals.includes(c))
  if (matches('flm', 'llm', 'llama-server')) return 'llm'
  if (matches('embed', 'embedding')) return 'embedding'
  if (matches('stt', 'transcription', 'moonshine', 'whispercpp')) return 'transcription'
  return candidates[0] || 'llm'
}
function modelOf(s)   { return s?.model_id || s?.model || s?.model_name || '' }
function metricsOf(s) { return s?.metrics || {} }
function pid(s)       { return s?.pid || s?.lemonade_pid || '—' }
function port(s)      { return s?.port || s?.backend_port || 0 }

const headerSub = computed(() => {
  if (dormant.value) return 'no agent model · trio dormant'
  return `one process · three roles · ${modelOf(chat.value)} active`
})
</script>

<template>
  <div :class="['card', 'npu-card', dormant ? 'npu-dormant' : 'live']" data-testid="npu-block">
    <div class="npu-h">
      <span class="npu-glyph mono">NPU</span>
      <span class="title mono">
        FLM trio<span class="sub">{{ headerSub }}</span>
      </span>
      <div class="right">
        <span class="chip coresident">
          <span class="dot coresident-dot" />
          coresident
        </span>
        <span v-if="chat" class="pid mono">pid {{ pid(chat) }} · port {{ port(chat) || '—' }}</span>
      </div>
    </div>

    <div class="npu-body">
      <!-- Chat (lead) -->
      <div v-if="chat" class="npu-subrow lead" :class="{ 'npu-subrow-swapping': swapInProgress }" data-testid="npu-chat-row">
        <span class="dot" :class="swapInProgress ? 'loading' : 'ready'" />
        <div class="role mono">
          {{ chat.name }}
          <span class="sub">llm · default</span>
        </div>
        <div class="model mono">
          <template v-if="swapInProgress">
            <span class="spinner" aria-hidden="true" />
            <span class="m-text" data-testid="npu-swap-progress">Loading {{ swapTargetModel || 'new model' }}…</span>
          </template>
          <template v-else>
            <span class="m-text">{{ modelOf(chat) || 'no model' }}</span>
            <button class="chev-btn" type="button" @click="emit('swap-chat', chat)" title="Swap chat model">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true">
                <path stroke-linecap="round" stroke-linejoin="round" d="M6 9l6 6 6-6"/>
              </svg>
            </button>
          </template>
        </div>
        <div class="met mono">
          <span v-if="swapInProgress" class="dim">awaiting trio reload</span>
          <span v-else><b>{{ (metricsOf(chat).toks ?? metricsOf(chat).tokens_per_sec ?? 0).toFixed?.(0) ?? '0' }}</b> tok/s · TTFT <b>{{ metricsOf(chat).ttft ?? '—' }}</b>ms · KV <b>{{ metricsOf(chat).kv ?? '—' }}</b>%</span>
        </div>
        <div class="st">
          <span v-if="swapInProgress" class="chip chip-warn">swapping</span>
          <span v-else class="chip chip-ok">ready · default</span>
        </div>
      </div>

      <!-- STT passenger -->
      <div v-if="stt" class="npu-subrow">
        <span class="dot coresident" />
        <div class="role mono">
          {{ stt.name }}
          <span class="sub">transcription</span>
        </div>
        <div class="model mono">
          <span class="m-text">{{ modelOf(stt) || 'no model' }}</span>
        </div>
        <div class="met mono">
          <span><b>{{ metricsOf(stt).xrt ?? '—' }}</b> xrt · {{ metricsOf(stt).precision ?? 'int8' }}</span>
        </div>
        <div class="st">
          <span class="chip chip-npu">coresident</span>
        </div>
      </div>

      <!-- Embed passenger -->
      <div v-if="emb" class="npu-subrow">
        <span class="dot coresident" />
        <div class="role mono">
          {{ emb.name }}
          <span class="sub">embedding</span>
        </div>
        <div class="model mono">
          <span class="m-text">{{ modelOf(emb) || 'no model' }}</span>
        </div>
        <div class="met mono">
          <span>{{ metricsOf(emb).dim ?? '—' }}-dim · ready</span>
        </div>
        <div class="st">
          <span class="chip chip-npu">coresident</span>
        </div>
      </div>
    </div>

    <div class="npu-foot mono">
      <span class="item"><b>~2 GB</b> NPU memory</span>
      <span class="sep">·</span>
      <span class="item"><b>~14s</b> swap penalty on chat-model change</span>
      <span class="sep">·</span>
      <span class="item">disabling passengers frees a role at next FLM restart</span>
    </div>
  </div>
</template>

<style scoped>
.npu-card {
  background: linear-gradient(135deg, var(--color-surface) 0%, color-mix(in oklab, rgba(200, 150, 255, 0.06) 100%, var(--color-surface)) 100%);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  position: relative;
}
.npu-card.live { border-color: rgba(200, 150, 255, 0.30); }
.npu-card.npu-dormant { opacity: 0.92; }
.npu-card::before {
  content: ""; position: absolute; inset: 0;
  background: radial-gradient(circle at 0% 0%, rgba(200, 150, 255, 0.08), transparent 50%);
  pointer-events: none;
}

.npu-h {
  padding: 14px 18px;
  border-bottom: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  gap: 14px;
  position: relative;
}
.npu-h .npu-glyph {
  width: 32px; height: 32px;
  border: 1px solid rgba(200, 150, 255, 0.40);
  border-radius: 3px;
  background: rgba(200, 150, 255, 0.08);
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--hal0-accent);
  display: inline-flex; align-items: center; justify-content: center;
  letter-spacing: 0.05em;
  font-weight: 600;
}
.npu-h .title { font-family: var(--font-mono); font-size: 14px; font-weight: 500; color: var(--color-fg); }
.npu-h .title .sub { color: var(--color-fg-muted); font-weight: 400; font-size: 11.5px; margin-left: 8px; }
.npu-h .right { margin-left: auto; display: flex; align-items: center; gap: 10px; }
.npu-h .pid {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--color-fg-faint);
  padding: 2px 7px;
  border: 1px solid var(--color-border);
  border-radius: 3px;
}

.npu-body { display: flex; flex-direction: column; }
.npu-subrow {
  display: grid;
  grid-template-columns: 14px 120px 1fr 130px 100px;
  gap: 14px;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid var(--color-border);
  font-family: var(--font-mono);
  position: relative;
}
.npu-subrow:last-child { border-bottom: none; }
.npu-subrow.lead { background: rgba(200, 150, 255, 0.04); }
.npu-subrow .role { color: var(--color-fg); font-size: 13px; font-weight: 500; }
.npu-subrow .role .sub { color: var(--color-fg-faint); font-size: 10px; display: block; margin-top: 2px; font-weight: 400; }
.npu-subrow .model { color: var(--color-fg-muted); font-size: 12px; display: flex; align-items: center; gap: 8px; min-width: 0; }
.npu-subrow .model .m-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.npu-subrow .chev-btn {
  background: transparent;
  border: 1px solid transparent;
  color: var(--color-fg-faint);
  padding: 2px 4px;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.npu-subrow .chev-btn:hover { color: var(--color-fg); border-color: var(--color-border); }
.npu-subrow .met { color: var(--color-fg-muted); font-size: 11.5px; }
.npu-subrow .met b { color: var(--color-fg); font-weight: 500; }
.npu-subrow .st { display: flex; justify-content: flex-end; }

.npu-foot {
  padding: 12px 18px;
  background: var(--color-surface-2);
  border-top: 1px solid var(--color-border);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-fg-faint);
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.npu-foot .item { display: inline-flex; align-items: center; gap: 6px; }
.npu-foot .item b { color: var(--color-fg-muted); font-weight: 500; }
.npu-foot .sep { color: var(--color-fg-faint); }

.dot {
  width: 8px; height: 8px; border-radius: 50%;
  display: inline-block; flex-shrink: 0;
}
.dot.ready { background: var(--color-success); box-shadow: 0 0 6px var(--color-success); }
.dot.coresident { background: rgba(200, 150, 255, 0.95); box-shadow: 0 0 6px rgba(200, 150, 255, 0.7); }

.chip {
  font-family: var(--font-mono);
  font-size: 9px;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  color: var(--color-fg-muted);
  border: 1px solid var(--color-border);
  letter-spacing: 0.04em;
  display: inline-flex; align-items: center; gap: 4px;
}
.chip.coresident {
  color: var(--hal0-accent);
  border-color: color-mix(in srgb, var(--hal0-accent) 40%, transparent);
  background: color-mix(in srgb, var(--hal0-accent) 10%, transparent);
}
.chip .coresident-dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: currentColor; box-shadow: 0 0 4px currentColor;
}
.chip.chip-ok {
  color: var(--color-success);
  border-color: color-mix(in oklch, var(--color-success), transparent 60%);
  background: color-mix(in oklch, var(--color-success), transparent 88%);
}
.chip.chip-npu {
  color: rgba(200, 150, 255, 0.95);
  border-color: rgba(200, 150, 255, 0.30);
  background: rgba(200, 150, 255, 0.06);
}
.chip.chip-warn {
  color: var(--color-warn, #f4b942);
  border-color: color-mix(in oklch, var(--color-warn, #f4b942), transparent 60%);
  background: color-mix(in oklch, var(--color-warn, #f4b942), transparent 88%);
}
.dot.loading {
  background: var(--color-warn, #f4b942);
  box-shadow: 0 0 6px var(--color-warn, #f4b942);
  animation: npu-pulse 1.4s ease-in-out infinite;
}
@keyframes npu-pulse {
  0%, 100% { opacity: 0.5; }
  50%      { opacity: 1.0; }
}
.npu-subrow-swapping { background: color-mix(in oklch, var(--color-warn, #f4b942), transparent 95%); }
.npu-subrow .model .spinner {
  width: 11px; height: 11px;
  border: 2px solid color-mix(in oklch, var(--color-warn, #f4b942), transparent 60%);
  border-top-color: var(--color-warn, #f4b942);
  border-radius: 50%;
  animation: npu-spin 0.7s linear infinite;
  flex-shrink: 0;
}
@keyframes npu-spin { to { transform: rotate(360deg); } }
.dim { color: var(--color-fg-faint); }
</style>
