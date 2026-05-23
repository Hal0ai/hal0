<script setup>
/**
 * dashboard/Composer.vue — slice #169.
 *
 * Chat composer with 5 states:
 *   idle       — input + Send (amber) + attach + mic
 *   sending    — input disabled + spinner
 *   streaming  — Send replaced with ✕ Stop (red)
 *   swap       — dimmed input + above-input swap banner
 *   no-tools   — persona "no tools" chip + attach/mic disabled tooltip
 *   offline    — dimmed row + above-input offline banner with restart
 *
 * Persona placement is host-controlled — `personaPlacement="above"`
 * renders a slot above the bar, `"inline"` renders inside the bar.
 *
 * Emits:
 *   submit   — { text } when Send pressed (idle only)
 *   stop     — when Stop pressed (streaming only)
 *   restart  — when the offline banner's Restart button clicked
 */
import { ref, computed, watch, nextTick } from 'vue'

const props = defineProps({
  state: {
    type: String,
    default: 'idle', // 'idle' | 'sending' | 'streaming' | 'swap' | 'no-tools' | 'offline'
  },
  swapTarget: { type: String, default: '' },
  personaPlacement: {
    type: String,
    default: 'inline', // 'above' | 'inline'
  },
  placeholder: { type: String, default: 'Ask hal0 anything…' },
})

const emit = defineEmits(['submit', 'stop', 'restart'])

const text = ref('')
const inputEl = ref(null)

const isOffline   = computed(() => props.state === 'offline')
const isSwap      = computed(() => props.state === 'swap')
const isSending   = computed(() => props.state === 'sending')
const isStreaming = computed(() => props.state === 'streaming')
const isNoTools   = computed(() => props.state === 'no-tools')
const isIdle      = computed(() => props.state === 'idle' || isNoTools.value)

const dimmed = computed(() => isOffline.value || isSwap.value)
const inputDisabled = computed(() => isSending.value || isStreaming.value || dimmed.value)
const toolsDisabled = computed(() => isNoTools.value || dimmed.value)

function trySubmit() {
  if (!isIdle.value) return
  const t = text.value.trim()
  if (!t) return
  emit('submit', { text: t })
  text.value = ''
  nextTick(() => inputEl.value?.focus())
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    trySubmit()
  }
}

watch(() => props.state, (s) => {
  if (s === 'idle') nextTick(() => inputEl.value?.focus())
})

defineExpose({ text, focus: () => inputEl.value?.focus() })
</script>

<template>
  <div
    class="composer"
    :class="{ dimmed }"
    data-testid="composer"
    :data-state="state"
  >
    <!-- Above-input banners (swap / offline) -->
    <div
      v-if="isSwap"
      class="composer-banner warn"
      data-testid="composer-banner-swap"
      role="status"
    >
      <span>⟳</span>
      <span>
        Swapping NPU chat to <b>{{ swapTarget || 'new model' }}</b>.
        Voice + embed pause ~14s.
      </span>
    </div>
    <div
      v-if="isOffline"
      class="composer-banner err"
      data-testid="composer-banner-offline"
      role="alert"
    >
      <span>⚠</span>
      <span><b>lemond offline</b> — inference unavailable.</span>
      <button
        type="button"
        class="banner-btn"
        @click="emit('restart')"
      >Restart lemond</button>
    </div>

    <!-- Persona above row (host slots in) -->
    <div v-if="personaPlacement === 'above'" class="composer-persona-row">
      <slot name="persona" />
    </div>

    <div class="composer-bar">
      <!-- Persona inline (host slots in) -->
      <slot v-if="personaPlacement === 'inline'" name="persona" />

      <!-- Attach -->
      <button
        type="button"
        class="composer-ic"
        :class="{ disabled: toolsDisabled }"
        :disabled="toolsDisabled"
        :title="isNoTools ? 'This persona has no tool access' : 'Attach a file'"
        aria-label="Attach file"
        data-testid="composer-attach"
      >📎</button>

      <!-- Input -->
      <div class="composer-input-wrap">
        <input
          ref="inputEl"
          v-model="text"
          type="text"
          class="composer-input"
          :placeholder="placeholder"
          :disabled="inputDisabled"
          :aria-busy="isSending || isStreaming"
          data-testid="composer-input"
          @keydown="onKeydown"
        />
      </div>

      <!-- Mic -->
      <button
        type="button"
        class="composer-ic"
        :class="{ disabled: toolsDisabled }"
        :disabled="toolsDisabled"
        :title="isNoTools ? 'This persona has no tool access' : 'Voice input'"
        aria-label="Voice input"
        data-testid="composer-mic"
      >🎤</button>

      <!-- Send / Stop -->
      <button
        v-if="isStreaming"
        type="button"
        class="composer-stop"
        data-testid="composer-stop"
        aria-label="Stop streaming"
        @click="emit('stop')"
      >
        <span class="stop-sq" aria-hidden="true" />
        Stop
      </button>
      <button
        v-else
        type="button"
        class="composer-send"
        :disabled="inputDisabled"
        :aria-label="isSending ? 'Sending' : 'Send'"
        data-testid="composer-send"
        @click="trySubmit"
      >
        <span v-if="isSending" class="spinner-sm" aria-hidden="true" />
        <span v-else aria-hidden="true">➤</span>
      </button>
    </div>

    <div v-if="isSending" class="composer-meta" data-testid="composer-meta-sending">
      <span>sending…</span>
    </div>
  </div>
</template>

<style scoped>
.composer {
  border-top: 1px solid var(--color-border, var(--line, #2a2a2a));
  background: var(--color-surface, var(--bg-1, #111));
}
.composer.dimmed .composer-bar { opacity: 0.5; pointer-events: none; }
.composer.dimmed .composer-input { background: var(--color-surface-3, var(--bg-3, #202020)); }
/* Keep the banner's own Restart button clickable even though the bar is dimmed */
.composer.dimmed .composer-banner { opacity: 1; pointer-events: auto; }

.composer-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
}
.composer-persona-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px 4px;
  border-bottom: 1px dashed var(--color-border, var(--line-soft, #1d1d1d));
}

.composer-input-wrap {
  flex: 1;
  position: relative;
}
.composer-input {
  width: 100%;
  background: transparent;
  border: none;
  color: var(--color-fg, var(--fg, #e5e5e5));
  font-family: var(--font-sans, var(--geist, system-ui));
  font-size: 13.5px;
  padding: 7px 0;
  outline: none;
  min-height: 28px;
  line-height: 1.45;
}
.composer-input::placeholder { color: var(--color-fg-faint, var(--fg-4, #777)); }

.composer-ic {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-fg-muted, var(--fg-3, #888));
  background: transparent;
  border: none;
  cursor: pointer;
  border-radius: 4px;
  flex-shrink: 0;
}
.composer-ic:hover {
  color: var(--color-fg, var(--fg, #e5e5e5));
  background: var(--color-surface-2, var(--bg-2, #181818));
}
.composer-ic.disabled, .composer-ic:disabled {
  opacity: 0.35;
  pointer-events: none;
  cursor: not-allowed;
}

.composer-send {
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--hal0-accent, var(--accent, #feaf00));
  color: #0a0a0a;
  border-radius: 4px;
  border: none;
  cursor: pointer;
  flex-shrink: 0;
  font-size: 14px;
}
.composer-send:hover:not(:disabled) { filter: brightness(1.06); }
.composer-send:disabled { opacity: 0.5; cursor: not-allowed; }

.composer-stop {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px 12px;
  background: color-mix(in oklab, var(--color-danger, #ef6b6b) 18%, transparent);
  color: var(--color-danger, #ef6b6b);
  border: 1px solid var(--color-danger, #ef6b6b);
  border-radius: 4px;
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.composer-stop:hover { background: color-mix(in oklab, var(--color-danger, #ef6b6b) 28%, transparent); }
.composer-stop .stop-sq {
  display: inline-block;
  width: 8px;
  height: 8px;
  background: currentColor;
  border-radius: 1px;
}

.composer-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
  border-bottom: 1px solid var(--color-border, var(--line-soft, #1d1d1d));
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 12px;
}
.composer-banner b { font-weight: 500; }
.composer-banner.warn {
  background: color-mix(in oklab, var(--color-warning, #f59e0b) 18%, transparent);
  color: var(--color-warning, #f59e0b);
}
.composer-banner.err {
  background: color-mix(in oklab, var(--color-danger, #ef6b6b) 18%, transparent);
  color: var(--color-danger, #ef6b6b);
}
.composer-banner.info {
  background: color-mix(in oklab, var(--hal0-accent, #feaf00) 14%, transparent);
  color: var(--hal0-accent, #feaf00);
}
.composer-banner .banner-btn {
  margin-left: auto;
  padding: 4px 10px;
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 11px;
  background: transparent;
  color: inherit;
  border: 1px solid currentColor;
  border-radius: 3px;
  cursor: pointer;
}
.composer-banner .banner-btn:hover { background: color-mix(in oklab, currentColor 12%, transparent); }

.composer-meta {
  padding: 4px 14px 8px;
  display: flex;
  align-items: center;
  gap: 14px;
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 10px;
  color: var(--color-fg-faint, var(--fg-5, #555));
}

.spinner-sm {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 1.5px solid rgba(10, 10, 10, 0.25);
  border-top-color: #0a0a0a;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
