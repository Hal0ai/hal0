<script setup>
/**
 * components/settings/AddSecretModal.vue — wraps primitives/Modal for
 * adding a new secret to the Secrets section.
 *
 * The Secrets section maps to /api/secrets; when the endpoint 404s the
 * parent stores the secret in a local in-memory list so the dashboard
 * still works on Lemonade-only deployments that haven't shipped the
 * secrets vault yet.
 *
 * Emits
 * ─────
 *   close — operator cancelled or hit Esc / backdrop
 *   save({name, value, description}) — submit pressed
 */
import { ref, watch } from 'vue'
import Modal from '../primitives/Modal.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'save'])

const name = ref('')
const value = ref('')
const description = ref('')
const reveal = ref(false)

watch(() => props.open, (v) => {
  if (v) {
    name.value = ''
    value.value = ''
    description.value = ''
    reveal.value = false
  }
})

function onClose() { emit('close') }

function submit() {
  if (!name.value.trim() || !value.value) return
  emit('save', {
    name: name.value.trim(),
    value: value.value,
    description: description.value.trim(),
  })
}
</script>

<template>
  <Modal
    :open="open"
    :on-close="onClose"
    title="Add secret"
    eyebrow="Encrypted at rest"
    :width="520"
  >
    <div class="form" data-testid="add-secret-modal">
      <label class="field">
        <span class="lbl mono">NAME</span>
        <input
          v-model="name"
          class="field-input mono"
          type="text"
          placeholder="HF_TOKEN"
          autocomplete="off"
          spellcheck="false"
          data-testid="add-secret-name"
        />
      </label>
      <label class="field">
        <span class="lbl mono">VALUE</span>
        <div class="row">
          <input
            v-model="value"
            class="field-input mono"
            :type="reveal ? 'text' : 'password'"
            placeholder="paste value"
            autocomplete="off"
            spellcheck="false"
            data-testid="add-secret-value"
          />
          <button type="button" class="btn-ghost-sm" @click="reveal = !reveal">
            {{ reveal ? 'Hide' : 'Show' }}
          </button>
        </div>
      </label>
      <label class="field">
        <span class="lbl mono">DESCRIPTION (optional)</span>
        <input
          v-model="description"
          class="field-input"
          type="text"
          placeholder="What is this used for?"
        />
      </label>
    </div>
    <template #foot>
      <span class="hint mono">Stored under /etc/hal0/secrets — never logged.</span>
      <span class="actions">
        <button type="button" class="btn ghost sm" @click="onClose">Cancel</button>
        <button
          type="button"
          class="btn sm"
          :disabled="!name.trim() || !value"
          data-testid="add-secret-submit"
          @click="submit"
        >
          Save secret
        </button>
      </span>
    </template>
  </Modal>
</template>

<style scoped>
.form { display: flex; flex-direction: column; gap: 14px; }
.field { display: flex; flex-direction: column; gap: 6px; }
.lbl { font-size: 10px; letter-spacing: 0.08em; color: var(--fg-4, var(--color-fg-faint)); text-transform: uppercase; }
.row { display: flex; gap: 8px; align-items: center; }
.field-input {
  flex: 1;
  background: var(--bg, var(--color-surface));
  border: 1px solid var(--line, var(--color-border));
  border-radius: var(--rad-sm, 4px);
  padding: 7px 10px;
  font-size: 13px;
  color: var(--fg, var(--color-fg));
}
.mono { font-family: var(--jbm, var(--font-mono)); }
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
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.hint { color: var(--fg-4, var(--color-fg-faint)); }
.actions { display: inline-flex; gap: 8px; }
</style>
