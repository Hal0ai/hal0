<script setup>
/**
 * NpuReactor.vue — NPU trio rendered as a central FLM disc + 3 spokes.
 *
 * Mirrors slots.jsx::NpuReactor (lines 207-283). Tweak variant of
 * NpuBlock — same data, different visual register. Toggle via
 * useTweaksStore.npuVariant ('block' default → 'reactor').
 */
import { computed } from 'vue'

const props = defineProps({
  slots: { type: Array, required: true },
  flmVersion: { type: String, default: 'v0.9.42' },
  flmArgs: { type: String, default: '--asr 1 --embed 1' },
  /**
   * Optional NPU swap-in-progress signal — see NpuBlock.vue for the
   * full contract. PR-20 / plan §5.3.
   */
  swapStatus: {
    type: Object,
    default: () => ({ in_progress: false, from_model: null, to_model: null }),
  },
})

const emit = defineEmits(['swap-chat'])

const swapInProgress = computed(() => !!props.swapStatus?.in_progress)
const swapTargetModel = computed(() => props.swapStatus?.to_model || '')

function deviceOf(s) { return s?.device || 'npu' }
function typeOf(s) {
  // Prefer `type` (capability) over `kind` (implementation) — see NpuBlock.
  const candidates = [s?.type, s?.kind].map((v) => String(v || '').toLowerCase())
  const matches = (...vals) => candidates.some((c) => vals.includes(c))
  if (matches('flm', 'llm', 'llama-server')) return 'llm'
  if (matches('embed', 'embedding')) return 'embedding'
  if (matches('stt', 'transcription', 'moonshine', 'whispercpp')) return 'transcription'
  return candidates[0] || 'llm'
}
function modelOf(s) { return s?.model_id || s?.model || s?.model_name || '' }
function metricsOf(s) { return s?.metrics || {} }
function pid(s) { return s?.pid || s?.lemonade_pid || '—' }

const chat = computed(() => props.slots.find((s) => deviceOf(s) === 'npu' && typeOf(s) === 'llm'))
const stt  = computed(() => props.slots.find((s) => deviceOf(s) === 'npu' && typeOf(s) === 'transcription'))
const emb  = computed(() => props.slots.find((s) => deviceOf(s) === 'npu' && typeOf(s) === 'embedding'))
const dormant = computed(() => !chat.value || !modelOf(chat.value))
</script>

<template>
  <div :class="['card', 'npu-card', dormant ? 'npu-dormant' : 'live']" data-testid="npu-reactor">
    <div class="npu-h">
      <span class="npu-glyph mono">NPU</span>
      <span class="title mono">FLM trio<span class="sub">reactor view · one process driving three roles</span></span>
      <div class="right">
        <span class="chip coresident">
          <span class="coresident-dot" />
          coresident
        </span>
        <span v-if="chat" class="pid mono">pid {{ pid(chat) }}</span>
      </div>
    </div>

    <div class="npu-reactor">
      <div class="reactor-core">
        <div class="reactor-disc">
          <div class="lbl">
            FLM<b>{{ flmVersion }}</b>
            <div class="args mono">{{ flmArgs }}</div>
          </div>
        </div>
        <div class="reactor-meta mono">XDNA2 · 8 columns · 1 ctx</div>
      </div>

      <div class="reactor-roles">
        <div v-if="chat" class="reactor-role lead" :class="{ 'reactor-role-swapping': swapInProgress }" data-testid="npu-chat-row">
          <span class="dot" :class="swapInProgress ? 'loading' : 'ready'" />
          <div class="lbl">
            {{ chat.name }}
            <span class="sub">chat · llm · default</span>
          </div>
          <div v-if="swapInProgress" class="md md-swap">
            <span class="spinner" aria-hidden="true" />
            <span data-testid="npu-swap-progress">Loading {{ swapTargetModel || 'new model' }}…</span>
          </div>
          <div v-else class="md">{{ modelOf(chat) || 'no model' }}</div>
          <div class="met">
            <template v-if="swapInProgress">
              <div class="dim">awaiting trio reload</div>
            </template>
            <template v-else>
              <div><b>{{ (metricsOf(chat).toks ?? metricsOf(chat).tokens_per_sec ?? 0).toFixed?.(0) ?? '0' }}</b> tok/s</div>
              <div class="dim">KV {{ metricsOf(chat).kv ?? '—' }}%</div>
            </template>
          </div>
        </div>

        <div v-if="stt" class="reactor-role">
          <span class="dot coresident" />
          <div class="lbl">
            {{ stt.name }}
            <span class="sub">transcription · passenger</span>
          </div>
          <div class="md">{{ modelOf(stt) || 'no model' }}</div>
          <div class="met">
            <div><b>{{ metricsOf(stt).xrt ?? '—' }}</b> xrt</div>
            <div class="dim">{{ metricsOf(stt).precision ?? 'int8' }}</div>
          </div>
        </div>

        <div v-if="emb" class="reactor-role">
          <span class="dot coresident" />
          <div class="lbl">
            {{ emb.name }}
            <span class="sub">embedding · passenger</span>
          </div>
          <div class="md">{{ modelOf(emb) || 'no model' }}</div>
          <div class="met">
            <div><b>{{ metricsOf(emb).dim ?? '—' }}</b> dim</div>
            <div class="dim">ready</div>
          </div>
        </div>
      </div>
    </div>

    <div class="npu-foot mono">
      <span class="item"><b>~2 GB</b> NPU memory</span>
      <span class="sep">·</span>
      <span class="item">
        swap <b>{{ chat?.name ?? '—' }}</b>
        <button v-if="chat" class="link" type="button" @click="emit('swap-chat', chat)">change chat model →</button>
      </span>
      <span class="sep">·</span>
      <span class="item">pauses voice + embed ~14s on swap</span>
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

.npu-h {
  padding: 14px 18px;
  border-bottom: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  gap: 14px;
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

.npu-reactor {
  position: relative;
  padding: 24px;
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 24px;
  align-items: center;
}
.reactor-core {
  position: relative;
  display: flex; flex-direction: column; align-items: center;
  gap: 8px;
}
.reactor-disc {
  width: 140px; height: 140px;
  border: 1px solid rgba(200, 150, 255, 0.40);
  border-radius: 50%;
  background:
    radial-gradient(circle at center, rgba(200, 150, 255, 0.12), transparent 70%),
    var(--color-surface-2);
  display: flex; align-items: center; justify-content: center;
  position: relative;
}
.reactor-disc::before, .reactor-disc::after {
  content: ""; position: absolute; inset: 12px;
  border: 1px dashed rgba(200, 150, 255, 0.20);
  border-radius: 50%;
}
.reactor-disc::after { inset: 28px; opacity: 0.5; }
.reactor-disc .lbl {
  font-family: var(--font-mono); font-size: 11px;
  color: var(--hal0-accent); text-align: center;
  letter-spacing: 0.05em; position: relative; z-index: 2;
}
.reactor-disc .lbl b { display: block; font-size: 15px; color: var(--color-fg); margin-top: 2px; }
.reactor-disc .args { margin-top: 4px; color: var(--color-fg-faint); }
.reactor-meta { font-family: var(--font-mono); font-size: 10px; color: var(--color-fg-faint); text-align: center; }

.reactor-roles { display: flex; flex-direction: column; gap: 8px; }
.reactor-role {
  display: grid;
  grid-template-columns: 16px 110px 1fr auto;
  gap: 12px;
  align-items: center;
  padding: 10px 12px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  font-family: var(--font-mono);
  font-size: 12px;
}
.reactor-role.lead { border-color: rgba(200, 150, 255, 0.30); }
.reactor-role .lbl { color: var(--color-fg); font-weight: 500; }
.reactor-role .lbl .sub { color: var(--color-fg-faint); font-size: 10px; display: block; margin-top: 1px; font-weight: 400; }
.reactor-role .md { color: var(--color-fg-muted); font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.reactor-role .met { color: var(--color-fg-muted); font-size: 11px; text-align: right; }
.reactor-role .met b { color: var(--color-fg); font-weight: 500; }
.reactor-role .met .dim { color: var(--color-fg-faint); }

.npu-foot {
  padding: 12px 18px;
  background: var(--color-surface-2);
  border-top: 1px solid var(--color-border);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-fg-faint);
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
}
.npu-foot .item { display: inline-flex; align-items: center; gap: 6px; }
.npu-foot .item b { color: var(--color-fg-muted); font-weight: 500; }
.npu-foot .sep { color: var(--color-fg-faint); }
.npu-foot .link {
  background: transparent; border: none; padding: 0;
  color: var(--hal0-accent); cursor: pointer;
  font-family: inherit; font-size: inherit;
  text-decoration: underline; text-underline-offset: 2px;
}

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
.dot.loading {
  background: var(--color-warn, #f4b942);
  box-shadow: 0 0 6px var(--color-warn, #f4b942);
  animation: reactor-pulse 1.4s ease-in-out infinite;
}
@keyframes reactor-pulse {
  0%, 100% { opacity: 0.5; }
  50%      { opacity: 1.0; }
}
.reactor-role.reactor-role-swapping {
  border-color: color-mix(in oklch, var(--color-warn, #f4b942), transparent 60%);
}
.md.md-swap { display: flex; align-items: center; gap: 7px; color: var(--color-fg-muted); }
.reactor-role .spinner {
  width: 11px; height: 11px;
  border: 2px solid color-mix(in oklch, var(--color-warn, #f4b942), transparent 60%);
  border-top-color: var(--color-warn, #f4b942);
  border-radius: 50%;
  animation: reactor-spin 0.7s linear infinite;
  flex-shrink: 0;
}
@keyframes reactor-spin { to { transform: rotate(360deg); } }
</style>
