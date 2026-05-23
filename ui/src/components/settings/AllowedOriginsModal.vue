<script setup>
/**
 * components/settings/AllowedOriginsModal.vue — edit the CORS allowlist
 * for hal0-api. Wraps primitives/Modal.
 *
 * The Auth section opens this to add / remove URLs that may call
 * /api/* with the dashboard token. Backed by /api/auth/allowed-origins
 * when present; falls back to client-only mock state otherwise.
 *
 * Emits
 * ─────
 *   close
 *   save(string[])  — full replacement list
 */
import { ref, watch } from 'vue'
import Modal from '../primitives/Modal.vue'

const props = defineProps({
  open:    { type: Boolean, default: false },
  origins: { type: Array, default: () => [] },
})

const emit = defineEmits(['close', 'save'])

const list = ref([...props.origins])
const draft = ref('')

watch(() => props.open, (v) => {
  if (v) {
    list.value = [...props.origins]
    draft.value = ''
  }
})

watch(() => props.origins, (v) => { list.value = [...v] })

function add() {
  const trimmed = draft.value.trim()
  if (!trimmed) return
  if (!list.value.includes(trimmed)) list.value.push(trimmed)
  draft.value = ''
}

function remove(i) { list.value.splice(i, 1) }

function onClose() { emit('close') }

function save() { emit('save', [...list.value]) }
</script>

<template>
  <Modal
    :open="open"
    :on-close="onClose"
    title="Allowed origins"
    eyebrow="CORS"
    :width="540"
  >
    <div class="body" data-testid="allowed-origins-modal">
      <p class="hint">
        URLs allowed to call <code class="mono">/api/*</code> with this dashboard's
        bearer token. <span class="mono">localhost</span> + the dashboard host are
        always trusted; this list extends the allowlist for embedded UIs and
        IDE plugins.
      </p>
      <div class="origins">
        <div v-for="(o, i) in list" :key="`${o}-${i}`" class="row">
          <span class="mono val">{{ o }}</span>
          <button type="button" class="btn-ghost-sm" @click="remove(i)">Remove</button>
        </div>
        <div v-if="list.length === 0" class="empty mono">No extra origins.</div>
      </div>
      <div class="add-row">
        <input
          v-model="draft"
          class="field-input mono"
          type="url"
          placeholder="https://chat.example.lan"
          @keydown.enter.prevent="add"
        />
        <button type="button" class="btn-ghost-sm" @click="add">+ Add</button>
      </div>
    </div>
    <template #foot>
      <span class="hint mono">Changes apply on save — running sessions keep their token.</span>
      <span class="actions">
        <button type="button" class="btn ghost sm" @click="onClose">Cancel</button>
        <button type="button" class="btn sm" @click="save">Save</button>
      </span>
    </template>
  </Modal>
</template>

<style scoped>
.body { display: flex; flex-direction: column; gap: 14px; }
.hint { font-size: 12px; color: var(--fg-3, var(--color-fg-muted)); margin: 0; line-height: 1.55; }
.hint code { background: var(--bg, var(--color-surface)); padding: 1px 4px; border-radius: 3px; }
.mono { font-family: var(--jbm, var(--font-mono)); }
.origins { display: flex; flex-direction: column; gap: 6px; }
.row {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  padding: 8px 10px;
  background: var(--bg, var(--color-surface));
  border: 1px solid var(--line-soft, var(--color-border));
  border-radius: var(--rad-sm, 4px);
}
.row .val { font-size: 12px; color: var(--fg-2, var(--color-fg-muted)); word-break: break-all; }
.empty { font-size: 11px; color: var(--fg-4, var(--color-fg-faint)); padding: 6px 0; }
.add-row { display: flex; gap: 8px; }
.field-input {
  flex: 1;
  background: var(--bg, var(--color-surface));
  border: 1px solid var(--line, var(--color-border));
  border-radius: var(--rad-sm, 4px);
  padding: 7px 10px;
  font-size: 12.5px;
  color: var(--fg, var(--color-fg));
}
.btn-ghost-sm {
  background: transparent;
  border: 1px solid var(--line, var(--color-border));
  color: var(--fg-2, var(--color-fg-muted));
  border-radius: var(--rad-sm, 4px);
  padding: 6px 12px;
  font-family: var(--jbm, var(--font-mono));
  font-size: 11px;
  cursor: pointer;
}
.btn {
  background: var(--accent, var(--hal0-accent));
  border: 1px solid var(--accent, var(--hal0-accent));
  color: #0a0a0a;
  border-radius: var(--rad-sm, 4px);
  padding: 7px 14px;
  font-family: var(--jbm, var(--font-mono));
  font-size: 12px;
  cursor: pointer;
}
.btn.ghost { background: transparent; color: var(--fg-2, var(--color-fg-muted)); }
.btn.sm { padding: 5px 11px; font-size: 11px; }
.actions { display: inline-flex; gap: 8px; }
</style>
