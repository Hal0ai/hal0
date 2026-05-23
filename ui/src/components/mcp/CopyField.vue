<script setup>
/**
 * mcp/CopyField.vue — inline copy-to-clipboard pill.
 *
 * Mirrors the React `CopyField` in
 *   /tmp/hal0-design-v3/dash/mcp.jsx (lines 185–208).
 * The "copied" affordance flashes for 1400ms then reverts to the icon.
 *
 * Defensive: navigator.clipboard may be undefined under jsdom or
 * insecure contexts; fall back to a hidden textarea + execCommand so
 * the affordance still works in test environments.
 */
import { ref } from 'vue'

const props = defineProps({
  value: { type: String, default: '' },
})

const copied = ref(false)

function copy(e) {
  e.stopPropagation()
  const v = props.value || ''
  if (!v) return
  try {
    if (navigator?.clipboard?.writeText) {
      navigator.clipboard.writeText(v).catch(() => {})
    } else {
      const ta = document.createElement('textarea')
      ta.value = v
      document.body.appendChild(ta)
      ta.select()
      try { document.execCommand('copy') } catch {}
      document.body.removeChild(ta)
    }
  } catch {
    // swallow — copy is best-effort.
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 1400)
}
</script>

<template>
  <div class="mcp-copy">
    <span class="mono mcp-copy-val" :title="value">{{ value || '—' }}</span>
    <button
      type="button"
      class="mcp-copy-btn mono"
      :data-testid="`copy-btn-${value}`"
      title="Copy"
      @click="copy"
    >
      <span v-if="copied">copied</span>
      <svg v-else width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="5" y="5" width="9" height="9" rx="1.5" />
        <path d="M3 11V3a1 1 0 0 1 1-1h8" />
      </svg>
    </button>
  </div>
</template>

<style scoped>
.mcp-copy {
  display: inline-flex;
  align-items: center;
  gap: 0;
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  background: var(--bg);
  overflow: hidden;
  max-width: 100%;
}
.mcp-copy-val {
  padding: 4px 8px;
  font-size: 11.5px;
  color: var(--fg-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.mcp-copy-btn {
  border: none;
  border-left: 1px solid var(--line);
  background: var(--bg-2);
  color: var(--fg-3);
  font-size: 10.5px;
  padding: 4px 8px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  font-family: var(--hal0-font-mono);
}
.mcp-copy-btn:hover { color: var(--accent); }
</style>
