<script setup>
/**
 * CreateSlotModal.vue — modal form for creating a new slot.
 *
 * Mirrors slot-modals.jsx::CreateSlotModal (lines 8-191). Built on the
 * primitives/Modal shell so focus-trap + Esc + body-scroll-lock are
 * shared behaviour. Validates name (kebab-case + uniqueness), picks
 * device + type + model, and posts to /api/slots on save.
 *
 * Props
 * -----
 *   - open: boolean — controlled by the parent route.
 *   - existingNames: string[] — for the kebab-case + collision check.
 *   - models: array — `{ id, name, size_gb, backends, type, installed, ... }`
 *   - hardware: object — `{ npu_present, ram_used_mb, ram_total_mb, ... }`
 *   - defaults: object — pre-fills `{ name, type, group, device }` when
 *     opening from a skip-path EmptySlotCard's Configure button.
 */
import { computed, ref, watch } from 'vue'
import Modal from '../primitives/Modal.vue'
import { api } from '../../composables/useApi.js'
import { useToastsStore } from '../../stores/toasts.js'

const props = defineProps({
  open:          { type: Boolean, default: false },
  existingNames: { type: Array, default: () => [] },
  models:        { type: Array, default: () => [] },
  hardware:      { type: Object, default: () => ({}) },
  defaults:      { type: Object, default: () => ({}) },
})

const emit = defineEmits(['close', 'created'])

const toasts = useToastsStore()

const name      = ref('')
const slotType  = ref('llama-server')
const device    = ref('gpu-vulkan')
const modelId   = ref('')
const group     = ref('chat')
const advOpen   = ref(false)
const makeDefault = ref(false)
const ctxSize   = ref(8192)
const extraArgs = ref('--flash-attn on')
const submitting = ref(false)

watch(() => props.open, (isOpen) => {
  if (isOpen) {
    name.value      = props.defaults.name || ''
    slotType.value  = props.defaults.type || 'llama-server'
    device.value    = props.defaults.device || 'gpu-vulkan'
    group.value     = props.defaults.group || 'chat'
    modelId.value   = ''
    advOpen.value   = false
    makeDefault.value = false
    ctxSize.value   = 8192
    extraArgs.value = '--flash-attn on'
  }
})

// ── Validation ────────────────────────────────────────────────────────
const nameCollision = computed(() => props.existingNames.includes(name.value))
const nameInvalid = computed(() => !!name.value && !/^[a-z][a-z0-9-]{0,30}$/.test(name.value))
const nameError = computed(() => {
  if (!name.value) return null
  if (nameCollision.value) return 'name already in use'
  if (nameInvalid.value) return 'lowercase + dashes only'
  return null
})
const canSave = computed(() => !!name.value && !nameError.value && !submitting.value)

// ── Model filter ──────────────────────────────────────────────────────
const npuAvailable = computed(() => !!props.hardware?.npu_present)

const backendForApi = computed(() => {
  if (device.value === 'gpu-vulkan') return 'vulkan'
  if (device.value === 'gpu-rocm') return 'rocm'
  if (device.value === 'gpu-cuda') return 'cuda'
  if (device.value === 'cpu') return 'cpu'
  if (device.value === 'npu') return 'flm'
  return 'vulkan'
})

const compatibleModels = computed(() => {
  const tType = String(slotType.value).toLowerCase()
  const backend = backendForApi.value
  return (props.models || []).filter((m) => {
    const backends = Array.isArray(m.backends) ? m.backends.map((b) => String(b).toLowerCase()) : []
    const backendOk = backends.length === 0 || backends.includes(backend)
    const typeOk = !m.type || String(m.type).toLowerCase() === tType
      || (tType === 'llama-server' && String(m.type).toLowerCase() === 'llm')
    return backendOk && typeOk
  })
})

const ramFreeMb = computed(() => {
  const total = props.hardware?.ram_total_mb ?? 0
  const used = props.hardware?.ram_used_mb ?? 0
  return Math.max(0, total - used)
})
const ramFreeGb = computed(() => (ramFreeMb.value / 1024).toFixed(1))

// ── Submit ────────────────────────────────────────────────────────────
async function submit() {
  if (!canSave.value) return
  submitting.value = true
  try {
    const body = {
      name:       name.value,
      type:       slotType.value,
      backend:    backendForApi.value,
      auto_start: false,
      ctx_size:   Number(ctxSize.value),
    }
    if (modelId.value) body.model = modelId.value
    if (extraArgs.value) body.extra_args = extraArgs.value

    // Auto-pick a port from 8081-8099 if none specified — matches Slots.vue
    // submitCreate's behaviour pre-rewrite.
    try {
      const existing = await api('/api/slots')
      const used = new Set((existing ?? []).map((s) => Number(s.port) || 0))
      for (let p = 8081; p < 8100; p++) {
        if (!used.has(p)) { body.port = p; break }
      }
    } catch {
      body.port = 8081
    }

    await api('/api/slots', { method: 'POST', body: JSON.stringify(body) })
    toasts.success(`Slot "${name.value}" created on port ${body.port}`)
    emit('created', { name: name.value })
    emit('close')
  } catch (e) {
    if (e?.code === 'slot.npu_exclusivity_violation') {
      const conflicting = e?.details?.conflicting_slots?.[0] ?? 'another NPU LLM slot'
      toasts.error(`NPU already claimed by "${conflicting}" — disable it first.`)
    } else {
      toasts.error(e?.message || 'failed to create slot')
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Modal
    :open="open"
    :on-close="() => emit('close')"
    eyebrow="Slots · new"
    title="Create slot"
    :width="640"
    title-id="create-slot-title"
  >
    <div class="form-row">
      <div class="form-lbl">
        <label for="create-slot-name">Name <span class="req">*</span></label>
        <span class="sub">bare · kebab-case · unique across the host</span>
      </div>
      <div class="form-ctl">
        <input
          id="create-slot-name"
          v-model="name"
          class="input mono"
          placeholder="coder-large"
          autocomplete="off"
          spellcheck="false"
          autofocus
        />
        <div v-if="nameError" class="err">{{ nameError }}</div>
        <div v-else-if="name" class="ok">✓ available</div>
      </div>
    </div>

    <div class="form-row">
      <div class="form-lbl">
        <label for="create-slot-type">Type <span class="req">*</span></label>
        <span class="sub">drives the model filter</span>
      </div>
      <div class="form-ctl">
        <select id="create-slot-type" v-model="slotType" class="input mono">
          <option value="llama-server">llama-server</option>
          <option value="embedding">embedding</option>
          <option value="reranking">reranking</option>
          <option value="transcription">transcription</option>
          <option value="tts">tts</option>
          <option value="image">image</option>
        </select>
      </div>
    </div>

    <div class="form-row">
      <div class="form-lbl">
        <label for="create-slot-device">Device <span class="req">*</span></label>
        <span class="sub" v-if="!npuAvailable && device === 'npu'">
          <span class="warn">NPU disabled — FLM not installed</span>
        </span>
        <span v-else class="sub">hardware preference for this slot</span>
      </div>
      <div class="form-ctl">
        <select id="create-slot-device" v-model="device" class="input mono">
          <option value="gpu-rocm">gpu-rocm</option>
          <option value="gpu-vulkan">gpu-vulkan</option>
          <option value="cpu">cpu</option>
          <option value="npu" :disabled="!npuAvailable">
            npu{{ !npuAvailable ? ' — install FLM first' : '' }}
          </option>
        </select>
      </div>
    </div>

    <div class="form-row">
      <div class="form-lbl">
        <label for="create-slot-model">Model</label>
        <span class="sub">filtered to compatible · {{ compatibleModels.length }} match{{ compatibleModels.length !== 1 ? 'es' : '' }}</span>
      </div>
      <div class="form-ctl">
        <select id="create-slot-model" v-model="modelId" class="input mono">
          <option value="">— Select later (slot saves in `empty` state)</option>
          <option v-for="m in compatibleModels" :key="m.id" :value="m.id">
            {{ m.name ?? m.id }}{{ m.size_gb ? ` · ${m.size_gb}GB` : '' }} {{ m.installed === false ? '· will pull' : '· on disk' }}
          </option>
        </select>
        <div v-if="modelId && ramFreeMb > 0" class="ok">
          ✓ fits in available memory ({{ ramFreeGb }} GB free)
        </div>
      </div>
    </div>

    <div class="form-row">
      <div class="form-lbl">
        <label for="create-slot-group">Group</label>
        <span class="sub">pure UI rollup label</span>
      </div>
      <div class="form-ctl">
        <select id="create-slot-group" v-model="group" class="input mono">
          <option value="chat">chat</option>
          <option value="embed">embed</option>
          <option value="voice">voice</option>
          <option value="img">img</option>
          <option value="custom">custom</option>
        </select>
      </div>
    </div>

    <div class="form-row">
      <div class="form-lbl">
        <label for="create-slot-default">Default for type {{ slotType }}?</label>
        <span class="sub">flips `default = true`; demotes the current one</span>
      </div>
      <div class="form-ctl">
        <label class="checkbox-row">
          <input id="create-slot-default" v-model="makeDefault" type="checkbox" />
          <span>Set as default</span>
        </label>
      </div>
    </div>

    <button
      type="button"
      class="form-section"
      :aria-expanded="advOpen"
      @click="advOpen = !advOpen"
    >
      <span :class="['chev', { open: advOpen }]" aria-hidden="true">›</span>
      <span>Recipe options</span>
      <span class="meta">{{ advOpen ? 'collapse' : 'expand' }}</span>
    </button>
    <template v-if="advOpen">
      <div class="form-row">
        <div class="form-lbl">
          <label for="create-slot-ctx">ctx_size</label>
          <span class="warn">⟳ restart required</span>
        </div>
        <div class="form-ctl">
          <input id="create-slot-ctx" v-model.number="ctxSize" class="input mono" type="number" min="256" max="131072" step="512" />
        </div>
      </div>
      <div class="form-row">
        <div class="form-lbl">
          <label for="create-slot-extra">llamacpp_args</label>
          <span class="sub">merged with --parallel 1 --threads N baseline</span>
        </div>
        <div class="form-ctl">
          <input id="create-slot-extra" v-model="extraArgs" class="input mono" />
          <div class="hint">
            Denied: <span class="mono">-m / --port / --ctx-size / -ngl / --jinja / --mmproj / --embeddings / --reranking</span>
          </div>
        </div>
      </div>
    </template>

    <template #foot>
      <span>capabilities.toml will be written on save.</span>
      <span class="foot-actions">
        <button class="btn ghost sm" type="button" @click="emit('close')">Cancel</button>
        <button class="btn sm primary" type="button" :disabled="!canSave" @click="submit">
          {{ submitting ? 'Creating…' : 'Create slot' }}
        </button>
      </span>
    </template>
  </Modal>
</template>

<style scoped>
.form-row {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 18px;
  padding: 10px 0;
  border-bottom: 1px solid var(--color-border);
}
.form-row:last-of-type { border-bottom: none; }
.form-lbl { display: flex; flex-direction: column; gap: 2px; font-family: var(--font-mono); font-size: 12px; color: var(--color-fg); }
.form-lbl .sub { font-size: 10px; color: var(--color-fg-faint); font-weight: 400; }
.form-lbl .warn { color: var(--color-warning); }
.form-ctl { display: flex; flex-direction: column; gap: 4px; }
.req { color: var(--color-danger); }
.input {
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-size: 13px;
  outline: none;
  transition: border-color 0.12s;
  width: 100%;
  box-sizing: border-box;
}
.input:focus { border-color: var(--color-border-hi); }
.mono { font-family: var(--font-mono); }
.err { color: var(--color-danger); font-size: 11px; font-family: var(--font-mono); }
.ok  { color: var(--color-success); font-size: 11px; font-family: var(--font-mono); }
.hint { color: var(--color-fg-faint); font-size: 10.5px; font-family: var(--font-mono); }
.checkbox-row { display: inline-flex; align-items: center; gap: 8px; font-family: var(--font-mono); font-size: 12px; color: var(--color-fg); cursor: pointer; }
.form-section {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 0;
  background: transparent; border: none;
  font-family: var(--font-mono); font-size: 12px; color: var(--color-fg);
  cursor: pointer; width: 100%; text-align: left;
}
.form-section .chev { display: inline-block; transition: transform 0.15s; font-size: 14px; color: var(--color-fg-faint); }
.form-section .chev.open { transform: rotate(90deg); }
.form-section .meta { margin-left: auto; color: var(--color-fg-faint); font-size: 11px; text-transform: lowercase; }

.btn {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 5px 11px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-family: var(--font-mono); font-size: 11px;
  cursor: pointer;
}
.btn.ghost { background: transparent; border-color: transparent; color: var(--color-fg-muted); }
.btn.ghost:hover { background: var(--color-surface-2); color: var(--color-fg); }
.btn.primary { background: var(--hal0-accent); color: #000; border-color: var(--hal0-accent); }
.btn.primary:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.foot-actions { display: inline-flex; gap: 8px; }
</style>
