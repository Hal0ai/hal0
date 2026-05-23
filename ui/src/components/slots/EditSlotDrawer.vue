<script setup>
/**
 * EditSlotDrawer.vue — slide-in drawer for editing a single slot.
 *
 * Mirrors slot-modals.jsx::EditSlotDrawer (lines 194-307). Route-driven:
 * the parent owns `/slots/:name` and toggles `open` based on the slot
 * lookup. 520px wide via the primitives/Drawer shell. Restart-required
 * markers ⟳ on fields the backend can only apply after a child restart.
 *
 * Effective-flags preview is read-only and merges:
 *   lemond baseline → backend default → model recipe → slot extra_args
 *
 * Save dispatches to PUT /api/slots/{name}/config. The fast model-swap
 * path (PUT only the model field on a live slot) is still owned by the
 * parent Slots.vue submitter — this drawer just collects the form data
 * and emits.
 */
import { computed, ref, watch } from 'vue'
import Drawer from '../primitives/Drawer.vue'
import { api } from '../../composables/useApi.js'
import { useToastsStore } from '../../stores/toasts.js'

const props = defineProps({
  open:  { type: Boolean, default: false },
  slot:  { type: Object, default: null },
  models: { type: Array, default: () => [] },
})

const emit = defineEmits(['close', 'saved', 'delete'])

const toasts = useToastsStore()

const BACKENDS = ['vulkan', 'rocm', 'cuda', 'cpu', 'flm']
const BUILTIN_SLOTS = new Set(['primary', 'embed', 'stt', 'tts'])

const RESTART_FIELDS = new Set(['ctx_size', 'n_gpu_layers', 'backend'])
const RUNNING_STATES = new Set(['running', 'ready', 'serving', 'idle'])

// Form state — re-seeded each open.
const form = ref({
  backend: 'vulkan',
  model: '',
  ctx_size: 4096,
  idle_timeout_s: 900,
  workers: 1,
  extra_args: '',
})
const orig = ref({})
const saving = ref(false)
const advOpen = ref(false)

watch(() => [props.open, props.slot], () => {
  if (!props.open || !props.slot) return
  const s = props.slot
  const initial = {
    backend:        s.backend ?? 'vulkan',
    model:          s.model_id ?? s.model ?? '',
    ctx_size:       s.context_size ?? s.ctx_size ?? 4096,
    idle_timeout_s: s.idle_timeout_s ?? 900,
    workers:        s.workers ?? 1,
    extra_args:     s.extra_args ?? '',
  }
  form.value = { ...initial }
  orig.value = { ...initial }
  advOpen.value = false
}, { immediate: true })

const compatibleModels = computed(() => {
  return (props.models || []).filter((m) => {
    const backends = Array.isArray(m.backends) ? m.backends.map((b) => String(b).toLowerCase()) : []
    return backends.length === 0 || backends.includes(form.value.backend)
  })
})

const changedFields = computed(() => {
  const ch = new Set()
  for (const k of Object.keys(form.value)) {
    if (form.value[k] !== orig.value[k]) ch.add(k)
  }
  return ch
})

const isLive = computed(() => !!(props.slot && RUNNING_STATES.has(props.slot.status)))

// Effective flags preview — fully read-only, mirrors slot-modals.jsx's strip.
const effectiveFlags = computed(() => {
  const baseline = '--parallel 1 --threads 8'
  const slotExtra = (form.value.extra_args || '').trim()
  const ctx = form.value.ctx_size || 4096
  const model = form.value.model || props.slot?.model_id || props.slot?.model || '—'
  const port = props.slot?.port || '8092'
  return `${baseline}${slotExtra ? ' ' + slotExtra : ''}\n--ctx-size ${ctx}\n-m ${model}\n--port ${port}\n-ngl 999`
})

async function save() {
  if (!props.slot) return
  saving.value = true
  try {
    const ch = changedFields.value
    if (ch.size === 0) {
      emit('close')
      return
    }
    // Fast path: only the model changed on a live slot → POST /swap.
    if (ch.size === 1 && ch.has('model') && isLive.value && form.value.model) {
      const slotName = props.slot.name
      const targetModel = form.value.model
      toasts.success(`Swapping "${slotName}" → ${targetModel}…`)
      emit('saved', { slot: slotName, kind: 'swap', model: targetModel })
      emit('close')
      api(`/api/slots/${slotName}/swap`, {
        method: 'POST',
        body: JSON.stringify({ model_id: targetModel }),
      })
        .then(() => toasts.success(`"${slotName}" ready on ${targetModel}`))
        .catch((e) => toasts.error(`Swap "${slotName}" failed: ${e?.message || e}`))
      return
    }
    // Slow path: PUT full config.
    await api(`/api/slots/${props.slot.name}/config`, {
      method: 'PUT',
      body: JSON.stringify(form.value),
    })
    toasts.success(`Slot "${props.slot.name}" updated`)
    emit('saved', { slot: props.slot.name, kind: 'config' })
    emit('close')
  } catch (e) {
    toasts.error(e?.message || 'failed to save')
  } finally {
    saving.value = false
  }
}

const canDelete = computed(() => props.slot && !BUILTIN_SLOTS.has(props.slot.name))
</script>

<template>
  <Drawer
    :open="open"
    :on-close="() => emit('close')"
    :eyebrow="slot ? `Slots · /slots/${slot.name}` : 'Slots'"
    :title="slot ? `Edit ${slot.name}` : 'Edit slot'"
    :width="520"
    title-id="edit-slot-title"
  >
    <template v-if="slot">
      <!-- Provider + port + state strip -->
      <div class="ro-strip" data-testid="edit-slot-readonly">
        <div class="ro-cell">
          <div class="k mono">provider</div>
          <div class="v mono">{{ slot.provider || slot.type || 'lemonade' }}</div>
        </div>
        <div class="ro-cell">
          <div class="k mono">port</div>
          <div class="v mono">{{ slot.port ?? '—' }}</div>
        </div>
        <div class="ro-cell">
          <div class="k mono">state</div>
          <div class="v mono"><span class="chip ok">{{ slot.status ?? 'offline' }}</span></div>
        </div>
      </div>

      <div class="form-row">
        <div class="form-lbl">
          <label for="edit-slot-name">Name</label>
          <span class="sub">seeded slots can't be renamed</span>
        </div>
        <div class="form-ctl">
          <input id="edit-slot-name" :value="slot.name" class="input mono" disabled />
        </div>
      </div>

      <div class="form-row">
        <div class="form-lbl"><label for="edit-slot-type">Type</label></div>
        <div class="form-ctl">
          <select id="edit-slot-type" :value="slot.type || slot.kind" class="input mono" disabled>
            <option>{{ slot.type || slot.kind || '—' }}</option>
          </select>
          <div class="hint">Type is immutable. Create a new slot to change.</div>
        </div>
      </div>

      <div class="form-row">
        <div class="form-lbl">
          <label for="edit-slot-backend">Backend</label>
          <span class="warn">⟳ restart required</span>
        </div>
        <div class="form-ctl">
          <select id="edit-slot-backend" v-model="form.backend" class="input mono">
            <option v-for="b in BACKENDS" :key="b" :value="b">{{ b }}</option>
          </select>
        </div>
      </div>

      <div class="form-row">
        <div class="form-lbl">
          <label for="edit-slot-model">Model</label>
          <span class="sub">use inline swap from the card for live changes</span>
        </div>
        <div class="form-ctl">
          <select id="edit-slot-model" v-model="form.model" class="input mono">
            <option value="">— None</option>
            <option v-for="m in compatibleModels" :key="m.id" :value="m.id">
              {{ m.name ?? m.id }}{{ m.size_gb ? ` · ${m.size_gb}G` : '' }}
            </option>
          </select>
        </div>
      </div>

      <button
        type="button"
        class="form-section"
        :aria-expanded="advOpen"
        @click="advOpen = !advOpen"
      >
        <span :class="['chev', { open: advOpen }]" aria-hidden="true">›</span>
        <span>Advanced</span>
      </button>

      <template v-if="advOpen">
        <div class="form-row">
          <div class="form-lbl">
            <label for="edit-slot-ctx">ctx_size</label>
            <span class="warn">⟳ restart required</span>
          </div>
          <div class="form-ctl">
            <input id="edit-slot-ctx" v-model.number="form.ctx_size" type="number" min="256" max="131072" step="512" class="input mono" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-lbl">
            <label for="edit-slot-idle">idle_timeout_s</label>
            <span class="sub">unload after N seconds idle</span>
          </div>
          <div class="form-ctl">
            <input id="edit-slot-idle" v-model.number="form.idle_timeout_s" type="number" min="0" step="30" class="input mono" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-lbl">
            <label for="edit-slot-workers">workers</label>
            <span class="sub">concurrent inflight per slot · 1 = serial</span>
          </div>
          <div class="form-ctl">
            <input id="edit-slot-workers" v-model.number="form.workers" type="number" min="1" step="1" class="input mono" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-lbl">
            <label for="edit-slot-extra">extra_args</label>
            <span class="sub">slot-level llamacpp_args overlay</span>
          </div>
          <div class="form-ctl">
            <input id="edit-slot-extra" v-model="form.extra_args" class="input mono" />
            <div class="hint">Merged with model recipe defaults + the global baseline.</div>
          </div>
        </div>
      </template>

      <div class="form-row">
        <div class="form-lbl"><label>Effective flags preview</label></div>
        <div class="form-ctl">
          <pre class="flags mono">{{ effectiveFlags }}</pre>
          <div class="hint">Merge order: lemond baseline → backend default → model recipe → slot extra_args. Read-only.</div>
        </div>
      </div>
    </template>

    <template #foot>
      <button
        v-if="canDelete"
        class="btn danger sm"
        type="button"
        @click="emit('delete', slot)"
      >Delete slot</button>
      <span v-else class="dim mono">seeded · cannot delete</span>
      <span class="foot-actions">
        <button class="btn ghost sm" type="button" @click="emit('close')">Cancel</button>
        <button class="btn sm primary" type="button" :disabled="saving" @click="save">
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
      </span>
    </template>
  </Drawer>
</template>

<style scoped>
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.ro-strip {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  overflow: hidden;
  margin-bottom: 16px;
}
.ro-cell {
  padding: 10px 12px;
  border-right: 1px solid var(--color-border);
  background: var(--color-surface-2);
}
.ro-cell:last-child { border-right: none; }
.ro-cell .k {
  font-size: 9px;
  color: var(--color-fg-faint);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 3px;
}
.ro-cell .v { font-size: 12px; color: var(--color-fg); }
.chip {
  display: inline-block;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  letter-spacing: 0.04em;
}
.chip.ok {
  color: var(--color-success);
  border-color: color-mix(in oklch, var(--color-success), transparent 60%);
  background: color-mix(in oklch, var(--color-success), transparent 88%);
}

.form-row {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 14px;
  padding: 10px 0;
  border-bottom: 1px solid var(--color-border);
}
.form-row:last-of-type { border-bottom: none; }
.form-lbl {
  display: flex; flex-direction: column; gap: 2px;
  font-family: var(--font-mono); font-size: 12px; color: var(--color-fg);
}
.form-lbl .sub { font-size: 10px; color: var(--color-fg-faint); font-weight: 400; }
.form-lbl .warn { color: var(--color-warning); }
.form-ctl { display: flex; flex-direction: column; gap: 4px; }
.input {
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-size: 13px;
  outline: none;
  width: 100%;
  box-sizing: border-box;
}
.input:focus { border-color: var(--color-border-hi); }
.input:disabled { opacity: 0.6; cursor: not-allowed; }
.hint { color: var(--color-fg-faint); font-size: 10.5px; font-family: var(--font-mono); }
.mono { font-family: var(--font-mono); }
.flags {
  margin: 0;
  padding: 10px 12px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-fg-muted);
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}

.form-section {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 0;
  background: transparent; border: none;
  font-family: var(--font-mono); font-size: 12px; color: var(--color-fg);
  cursor: pointer; width: 100%; text-align: left;
}
.form-section .chev { display: inline-block; transition: transform 0.15s; font-size: 14px; color: var(--color-fg-faint); }
.form-section .chev.open { transform: rotate(90deg); }

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
.btn.danger { color: var(--color-danger); border-color: color-mix(in oklch, var(--color-danger), transparent 55%); }
.btn.danger:hover { background: color-mix(in oklch, var(--color-danger), transparent 88%); }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.dim { color: var(--color-fg-faint); font-size: 11px; }
.foot-actions { display: inline-flex; gap: 8px; }
</style>
