<script setup>
/**
 * mcp/EditConfigModal.vue — per-server config editor.
 *
 * Mirrors the React `EditConfigModal` in
 *   /tmp/hal0-design-v3/dash/mcp-modals.jsx (lines 152–226).
 *
 * Layout (top-down): read-only meta rows (connect url / transport /
 * version), Environment section (one input per env var; empty values
 * render with the err-line border), Auto-start checkbox, Allowed
 * clients segmented pills.
 *
 * "Save" pushes the env patch through `useMcpStore.updateConfig()`
 * and surfaces a toast — actual restart is the operator's call.
 */
import { ref, watch } from 'vue'
import Modal from '../primitives/Modal.vue'
import { useToastStore } from '../../stores/toast.js'

const props = defineProps({
  open:   { type: Boolean, default: false },
  server: { type: Object, default: null },
})

const emit = defineEmits(['close', 'save'])

const toasts = useToastStore()
const env = ref({})
const autoStart = ref(true)
const allowed = ref('any')

watch(() => props.server, (s) => {
  if (s) {
    env.value = { ...(s.env || {}) }
    autoStart.value = true
    allowed.value = 'any'
  }
}, { immediate: true })

function close() { emit('close') }

function save() {
  const patch = { env: { ...env.value }, autoStart: autoStart.value, allowed: allowed.value }
  emit('save', patch)
  toasts.push(`${props.server?.name || 'server'} config saved · restart to apply`, 'info')
  close()
}
</script>

<template>
  <Modal
    :open="open"
    :on-close="close"
    :eyebrow="`MCP · ${server?.name || ''}`"
    title="Edit server config"
    :width="620"
  >
    <div class="mcp-cfg-grid">
      <div class="mcp-cfg-row">
        <div class="mcp-cfg-l mono">connect url</div>
        <div class="mono mcp-cfg-v">{{ server?.url || '—' }}</div>
      </div>
      <div class="mcp-cfg-row">
        <div class="mcp-cfg-l mono">transport</div>
        <div class="mono mcp-cfg-v">{{ server?.transport }}</div>
      </div>
      <div class="mcp-cfg-row">
        <div class="mcp-cfg-l mono">version</div>
        <div class="mono mcp-cfg-v">v{{ server?.version }}</div>
      </div>

      <div class="mcp-cfg-sec mono">Environment</div>
      <div v-if="Object.keys(env).length === 0" class="mcp-cfg-empty mono">
        No env vars declared by this server.
      </div>
      <div v-else class="mcp-cfg-env">
        <div v-for="(v, k) in env" :key="k" class="mcp-cfg-env-row">
          <span class="mcp-cfg-env-k mono">{{ k }}</span>
          <input
            v-model="env[k]"
            :class="['mcp-cfg-env-input', 'mono', { empty: v === '' }]"
            :data-testid="`mcp-cfg-env-${k}`"
            :placeholder="`set ${k}…`"
          />
        </div>
      </div>

      <div class="mcp-cfg-sec mono">Auto-start</div>
      <label class="mcp-cfg-toggle mono">
        <input v-model="autoStart" type="checkbox" />
        <span>Restart this server when hal0 restarts</span>
      </label>

      <div class="mcp-cfg-sec mono">Allowed clients</div>
      <div class="mcp-cfg-allow mono">
        <span
          :class="['mcp-cfg-allow-pill', { on: allowed === 'any' }]"
          @click="allowed = 'any'"
        >any local client</span>
        <span
          :class="['mcp-cfg-allow-pill', { on: allowed === 'cc' }]"
          @click="allowed = 'cc'"
        >claude-code only</span>
        <span
          :class="['mcp-cfg-allow-pill', { on: allowed === 'token' }]"
          @click="allowed = 'token'"
        >require token</span>
      </div>
    </div>

    <template #foot>
      <span class="mcp-cfg-foot-note">Changes apply on next server restart.</span>
      <span class="mcp-cfg-actions">
        <button type="button" class="mcp-cfg-cancel" @click="close">Cancel</button>
        <button type="button" class="mcp-cfg-save" data-testid="mcp-cfg-save" @click="save">Save</button>
      </span>
    </template>
  </Modal>
</template>

<style scoped>
.mcp-cfg-grid { display: flex; flex-direction: column; gap: 6px; }
.mcp-cfg-row {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 14px;
  padding: 6px 0;
}
.mcp-cfg-l {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fg-4);
  align-self: center;
}
.mcp-cfg-v {
  font-size: 12px;
  color: var(--fg-2);
}
.mcp-cfg-sec {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--accent);
  margin: 14px 0 6px;
  padding-top: 12px;
  border-top: 1px solid var(--line-soft);
}
.mcp-cfg-empty { color: var(--fg-4); font-size: 11.5px; padding: 4px 0; }
.mcp-cfg-env { display: flex; flex-direction: column; gap: 8px; }
.mcp-cfg-env-row {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 12px;
  align-items: center;
}
.mcp-cfg-env-k { color: var(--fg-3); font-size: 11.5px; }
.mcp-cfg-env-input {
  padding: 6px 10px;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  color: var(--fg);
  font-size: 11.5px;
  font-family: var(--hal0-font-mono);
  box-sizing: border-box;
  width: 100%;
}
.mcp-cfg-env-input.empty { border-color: var(--err-line); }
.mcp-cfg-env-input:focus { outline: none; border-color: var(--accent-line); }

.mcp-cfg-toggle {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--fg-2);
  cursor: pointer;
  padding: 4px 0;
}
.mcp-cfg-toggle input { accent-color: var(--accent); }

.mcp-cfg-allow { display: flex; gap: 6px; }
.mcp-cfg-allow-pill {
  font-size: 11px;
  padding: 3px 9px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--fg-3);
  cursor: pointer;
  user-select: none;
}
.mcp-cfg-allow-pill.on { color: var(--accent); border-color: var(--accent-line); background: var(--accent-soft); }

.mcp-cfg-foot-note { color: var(--fg-4); }
.mcp-cfg-actions { display: inline-flex; gap: 8px; }
.mcp-cfg-cancel {
  background: transparent;
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  color: var(--fg-3);
  font-family: var(--hal0-font-mono);
  font-size: 11px;
  padding: 4px 10px;
  cursor: pointer;
}
.mcp-cfg-cancel:hover { color: var(--fg); border-color: var(--line-strong); }
.mcp-cfg-save {
  background: var(--accent);
  border: 1px solid var(--accent);
  border-radius: var(--rad-sm);
  color: #0a0a0a;
  font-family: var(--hal0-font-mono);
  font-size: 11px;
  padding: 4px 10px;
  cursor: pointer;
  font-weight: 500;
}
.mcp-cfg-save:hover { filter: brightness(1.06); }
</style>
