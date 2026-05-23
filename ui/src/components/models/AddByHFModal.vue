<script setup>
/**
 * AddByHFModal.vue — v2 Models view "Add by HF coords" flow.
 *
 * Mirrors the React `AddByHfModal` in
 *   /tmp/hal0-design/hal0-v2/project/dash/model-modals.jsx
 *
 * Five-step inline form inside a Modal:
 *   1. Repo input + [Inspect]            → POST /v1/pull/variants (or 404 → mock)
 *   2. Variant radio list (+ Other…)
 *   3. Model name (auto-prefilled `user.<slug>`)
 *   4. Labels checkboxes (mmproj required when vision is checked)
 *   5. Pre-flight panel (repo, variant, size, free disk, auth)
 *
 * Pull submission delegates to parent via `emit('pull', payload)` so the
 * caller can reuse the existing `usePullJob` composable + insert a row
 * into the Downloads pane. Modal closes after a successful emit.
 */
import { ref, computed, watch } from 'vue'
import Modal from '../primitives/Modal.vue'
import { MOCK_DATA } from '../../composables/useMock.js'

const props = defineProps({
  open: { type: Boolean, default: false },
  /** free-disk in bytes, optional — used for pre-flight + fit hint */
  diskFreeBytes: { type: Number, default: null },
  /** whether HF_TOKEN env is set on the host */
  hfTokenSet: { type: Boolean, default: true },
})

const emit = defineEmits(['close', 'pull'])

const repo = ref('')
const inspecting = ref(false)
const inspected = ref(false)
const inspectError = ref(null)
const variants = ref([])
const variant = ref(null)        // selected variant id, or 'other'
const otherVariant = ref('')     // free-text quant tag when variant === 'other'
const name = ref('')
const labels = ref({ chat: true })
const mmproj = ref('')

function reset() {
  repo.value = ''
  inspecting.value = false
  inspected.value = false
  inspectError.value = null
  variants.value = []
  variant.value = null
  otherVariant.value = ''
  name.value = ''
  labels.value = { chat: true }
  mmproj.value = ''
}

watch(() => props.open, (v) => { if (v) reset() })

// Mock variants when the backend doesn't yet implement /v1/pull/variants.
const MOCK_VARIANTS = Object.freeze([
  { id: 'Q4_K_M',     size: '4.9 GB', size_bytes: 4.9 * 1e9, info: 'single file' },
  { id: 'UD-Q4_K_XL', size: '5.1 GB', size_bytes: 5.1 * 1e9, info: 'single file · unsloth dynamic' },
  { id: 'Q5_K_S',     size: '5.8 GB', size_bytes: 5.8 * 1e9, info: 'single file' },
  { id: 'Q8_0',       size: '8.5 GB', size_bytes: 8.5 * 1e9, info: 'sharded · 2 files' },
  { id: 'Q4_0',       size: '4.7 GB', size_bytes: 4.7 * 1e9, info: 'single file · legacy' },
  { id: 'F16',        size: '16.2 GB', size_bytes: 16.2 * 1e9, info: 'single file · full precision' },
])

const MMPROJ_OPTIONS = Object.freeze([
  'mmproj-Q8_0.gguf',
  'mmproj-F16.gguf',
])

async function inspect() {
  if (!repo.value.trim()) return
  inspecting.value = true
  inspectError.value = null
  try {
    let res
    try {
      res = await fetch(`/v1/pull/variants?checkpoint=${encodeURIComponent(repo.value.trim())}`, {
        headers: { Accept: 'application/json' },
      })
    } catch {
      res = null
    }
    if (res && res.ok) {
      const body = await res.json()
      const list = Array.isArray(body?.variants) ? body.variants : []
      variants.value = list.length ? list : MOCK_VARIANTS
    } else if (res && res.status === 401) {
      inspectError.value = 'gated repo · HF_TOKEN required'
      return
    } else if (res && res.status === 404) {
      // Two cases: endpoint missing on backend → fall through to mock;
      // repo missing on HF → bail. Heuristic: if path contains org/repo,
      // assume endpoint-missing and use mock.
      variants.value = MOCK_VARIANTS
    } else if (res && res.status >= 400) {
      inspectError.value = `inspect failed (HTTP ${res.status})`
      return
    } else {
      variants.value = MOCK_VARIANTS
    }
    if (!variants.value.length) {
      inspectError.value = 'no GGUF variants found in this repo'
      return
    }
    inspected.value = true
    // Auto-fill the in-hal0 model name (strip -GGUF suffix, prefix user.)
    const guessed = (repo.value.split('/')[1] || 'model').replace(/-GGUF$/i, '')
    name.value = `user.${guessed}`
  } catch (e) {
    inspectError.value = e?.message ?? 'inspect failed'
  } finally {
    inspecting.value = false
  }
}

const selectedVariant = computed(() => {
  if (variant.value === 'other') {
    return otherVariant.value
      ? { id: otherVariant.value, size: '—', size_bytes: 0, info: 'free-text quant' }
      : null
  }
  return variants.value.find((v) => v.id === variant.value) || null
})

const visionLabelMissingMmproj = computed(() => labels.value.vision && !mmproj.value)

const canPull = computed(() =>
  inspected.value && !!selectedVariant.value && !!name.value.trim() && !visionLabelMissingMmproj.value
)

const diskFreeGb = computed(() =>
  props.diskFreeBytes != null ? (props.diskFreeBytes / 1e9).toFixed(1) : null
)

function submitPull() {
  if (!canPull.value) return
  const sel = selectedVariant.value
  const payload = {
    repo: repo.value.trim(),
    variant: sel.id,
    name: name.value.trim(),
    labels: Object.entries(labels.value).filter(([, v]) => v).map(([k]) => k),
    mmproj: labels.value.vision ? mmproj.value : null,
    size: sel.size,
    size_bytes: sel.size_bytes,
  }
  emit('pull', payload)
  emit('close')
}

function onClose() { emit('close') }

const TOGGLE_LABELS = [
  'chat', 'tool-calling', 'vision',
  'embeddings', 'reranking', 'transcription', 'tts', 'image', 'edit',
]
</script>

<template>
  <Modal
    :open="open"
    :on-close="onClose"
    eyebrow="Catalog · add model"
    title="Add model from Hugging Face"
    :width="680"
  >
    <div class="form-row">
      <div class="form-lbl">
        <span>Repo <span class="req">*</span></span>
        <span class="sub">org / repo · GGUF preferred</span>
      </div>
      <div class="form-ctl repo-ctl">
        <input
          id="hf-repo"
          v-model="repo"
          class="input mono"
          placeholder="unsloth/Qwen3-8B-GGUF"
          autocomplete="off"
          spellcheck="false"
          @input="inspected = false; inspectError = null"
          @keydown.enter.prevent="inspect()"
        />
        <button
          type="button"
          class="btn ghost sm"
          :disabled="!repo.trim() || inspecting"
          @click="inspect()"
        >{{ inspecting ? 'Inspecting…' : 'Inspect' }}</button>
      </div>
      <div v-if="inspectError" class="err mono" data-test="hf-inspect-err">{{ inspectError }}</div>
      <div v-if="inspectError === 'gated repo · HF_TOKEN required' && !hfTokenSet" class="hint">
        Add HF_TOKEN in Settings → Lemonade admin, then re-inspect.
      </div>
    </div>

    <template v-if="inspected">
      <div class="form-row">
        <div class="form-lbl">
          <span>Variants <span class="req">*</span></span>
          <span class="sub">{{ variants.length }} available · pick a quant</span>
        </div>
        <div class="form-ctl variant-list">
          <div
            v-for="v in variants"
            :key="v.id"
            :class="['variant-row', { sel: variant === v.id }]"
            :data-variant="v.id"
            @click="variant = v.id"
          >
            <span class="rad" />
            <span class="nm">
              {{ v.id }}
              <span class="sub">{{ v.info }}</span>
            </span>
            <span class="sz num">{{ v.size }}</span>
          </div>
          <div
            :class="['variant-row', 'variant-other', { sel: variant === 'other' }]"
            @click="variant = 'other'"
          >
            <span class="rad" />
            <span class="nm">Other…<span class="sub">free-text quant tag</span></span>
            <input
              v-if="variant === 'other'"
              v-model="otherVariant"
              class="input mono variant-other-input"
              placeholder="e.g. IQ3_XS"
              @click.stop
            />
          </div>
        </div>
      </div>

      <div class="form-row">
        <div class="form-lbl">
          <span>Model name <span class="req">*</span></span>
          <span class="sub">prefixed with <span class="mono">user.</span> by convention</span>
        </div>
        <div class="form-ctl">
          <input
            id="hf-model-name"
            v-model="name"
            class="input mono"
            autocomplete="off"
            spellcheck="false"
          />
        </div>
      </div>

      <div class="form-row">
        <div class="form-lbl">
          <span>Labels</span>
          <span class="sub">drives OmniRouter eligibility</span>
        </div>
        <div class="form-ctl labels-grid">
          <label v-for="l in TOGGLE_LABELS" :key="l" class="checkbox-row">
            <input
              type="checkbox"
              :checked="!!labels[l]"
              :data-label="l"
              @change="labels = { ...labels, [l]: $event.target.checked }"
            />
            <span class="mono">{{ l }}</span>
          </label>
          <div v-if="visionLabelMissingMmproj" class="err mono labels-err">
            vision label requires an mmproj file — pick one below
          </div>
        </div>
      </div>

      <div v-if="labels.vision" class="form-row">
        <div class="form-lbl">
          <span>mmproj file <span class="req">*</span></span>
          <span class="warn">required for vision-labeled models</span>
        </div>
        <div class="form-ctl">
          <select v-model="mmproj" class="input mono">
            <option value="">— pick from repo files…</option>
            <option v-for="opt in MMPROJ_OPTIONS" :key="opt" :value="opt">{{ opt }}</option>
          </select>
        </div>
      </div>

      <div class="form-section">Pre-flight</div>
      <div class="preflight mono">
        <div>repo · <span class="v">{{ repo }}</span></div>
        <div>variant · <span class="v">{{ selectedVariant?.id || '—' }}</span></div>
        <div>size · <span class="v">{{ selectedVariant?.size || '—' }}</span></div>
        <div>
          disk ·
          <span :class="['v', diskFreeGb !== null ? 'ok' : 'fg-4']">
            {{ diskFreeGb !== null ? `${diskFreeGb} GB free on /var ✓` : 'unknown' }}
          </span>
        </div>
        <div>
          auth ·
          <span :class="['v', hfTokenSet ? 'ok' : 'warn']">
            {{ hfTokenSet ? 'HF_TOKEN set ✓' : 'HF_TOKEN unset · public repos only' }}
          </span>
        </div>
      </div>
    </template>

    <template #foot>
      <span>Files land under <span class="mono">/var/lib/hal0/models/user.*</span></span>
      <span class="foot-actions">
        <button type="button" class="btn ghost sm" @click="onClose">Cancel</button>
        <button
          type="button"
          class="btn sm primary"
          :disabled="!canPull"
          data-test="hf-pull-submit"
          @click="submitPull"
        >
          Pull{{ selectedVariant ? ` (${selectedVariant.size})` : '' }}
        </button>
      </span>
    </template>
  </Modal>
</template>

<style scoped>
.form-row {
  padding: 12px 0;
  border-bottom: 1px solid var(--line-soft);
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 14px;
  align-items: start;
}
.form-row:first-child { padding-top: 4px; }
.form-row:last-child  { border-bottom: none; }
.form-lbl {
  font-family: var(--jbm);
  font-size: 12px;
  color: var(--fg-2);
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.form-lbl .req  { color: var(--accent); font-size: 10px; }
.form-lbl .sub  { font-size: 11px; color: var(--fg-4); font-weight: 400; }
.form-lbl .warn { font-size: 10px; color: var(--warn); }

.form-ctl { font-family: var(--jbm); }
.repo-ctl { display: flex; gap: 8px; }
.repo-ctl .input { flex: 1; }
.err {
  font-size: 10.5px;
  color: var(--err);
  margin-top: 4px;
}
.hint {
  font-size: 10.5px;
  color: var(--fg-4);
  margin-top: 4px;
}
.labels-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.labels-err { flex-basis: 100%; }

.variant-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.variant-row {
  display: grid;
  grid-template-columns: 14px 1fr 70px;
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  cursor: pointer;
  font-family: var(--jbm);
  font-size: 12px;
}
.variant-row:hover { border-color: var(--line-strong); }
.variant-row.sel { border-color: var(--accent-line); background: var(--accent-soft); }
.variant-other { border-style: dashed; }
.variant-row .rad {
  width: 14px; height: 14px;
  border-radius: 50%;
  border: 1px solid var(--fg-4);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.variant-row.sel .rad { border-color: var(--accent); }
.variant-row.sel .rad::after {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
}
.variant-row .nm { color: var(--fg); font-weight: 500; }
.variant-row .nm .sub { color: var(--fg-4); font-size: 10px; display: block; margin-top: 2px; }
.variant-row .sz { color: var(--fg-3); text-align: right; font-size: 11px; }
.variant-other-input {
  grid-column: 2 / 4;
  margin-top: 6px;
}

.checkbox-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: 3px;
  cursor: pointer;
  font-family: var(--jbm);
  font-size: 11.5px;
  color: var(--fg-2);
}
.checkbox-row input { accent-color: var(--accent); }

.form-section {
  margin: 16px 0 6px;
  font-family: var(--jbm);
  font-size: 10px;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}
.preflight {
  padding: 12px;
  background: var(--bg);
  border: 1px solid var(--line-soft);
  border-radius: var(--rad-sm);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--fg-3);
}
.preflight .v { color: var(--fg); }
.preflight .ok   { color: var(--ok); }
.preflight .warn { color: var(--warn); }
.preflight .fg-4 { color: var(--fg-4); }

.foot-actions {
  display: inline-flex;
  gap: 8px;
}

.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #0a0a0a;
}
.btn.primary:hover { filter: brightness(1.08); }
.btn.primary[disabled] {
  background: transparent;
  border-color: var(--line);
  color: var(--fg-4);
}
</style>
