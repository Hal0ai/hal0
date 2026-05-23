<script setup>
/**
 * LemonadeAdmin.vue — Settings → Lemonade admin panel (PR-13).
 *
 * Backend contract (src/hal0/api/routes/lemonade_admin.py):
 *   GET  /api/lemonade/config   → snapshot + _hal0.{effects,locked}
 *   POST /api/lemonade/config   → body {key: value, ...}
 *                                  resp {applied, effects:{immediate,deferred}}
 *
 * Plan §2.2 key partition:
 *   immediate: port, host, log_level, global_timeout, no_broadcast,
 *              extra_models_dir
 *   deferred:  max_loaded_models, ctx_size, llamacpp_backend,
 *              llamacpp_args, sdcpp_backend, whispercpp_backend, steps,
 *              cfg_scale, width, height, flm_args
 *
 * Lemonade's /internal/config returns nested keys (llamacpp.{args,
 * backend}, sdcpp.{backend,steps,...}, whispercpp.backend, flm.args)
 * but /internal/set takes flat key names (llamacpp_args,
 * llamacpp_backend, sdcpp_backend, ...). We map both directions in
 * unpackConfig / buildPatch so the form binding stays flat.
 *
 * Locked invariants surfaced inline by the backend's _hal0.locked
 * block + per-validator hints:
 *   - llamacpp_args must contain --threads N where N >= 2
 *     (hal0_lemonade_threads_deadlock)
 *   - flm_args must contain --asr 1 AND --embed 1 (plan §5 trio)
 *   - extra_models_dir must equal /var/lib/hal0/models (plan §3 + §6.1)
 *
 * No new pinia stores; #178's stores landed on feat/dash-v2-rework, not
 * main. We use the existing toasts.js store for save UX and the api()
 * composable for fetches.
 */
import { ref, reactive, computed, onMounted } from 'vue'
import { useToastsStore } from '../../stores/toasts.js'
import { api } from '../../composables/useApi.js'
import PageHeader from '../../components/PageHeader.vue'
import Card from '../../components/Card.vue'
import LoadingSkeleton from '../../components/LoadingSkeleton.vue'

const toasts = useToastsStore()

const loading = ref(true)
const saving = ref(false)
const error = ref(null)

// Server-provided metadata (filled on GET):
//   effects.immediate / effects.deferred — which keys take effect now
//   vs at next load. Keeps the partition in one place (the backend)
//   so a future plan §2.2 change doesn't require a frontend release.
const serverEffects = ref({ immediate: [], deferred: [] })
const lockedInvariants = ref({ extra_models_dir: '/var/lib/hal0/models' })

// Flat form state. Keys match what /internal/set accepts; we unpack
// nested lemond config (llamacpp.args → llamacpp_args, etc.) on load
// and re-build the patch from the flat keys on save.
const form = reactive({
  // service
  host: '',
  port: 13305,
  log_level: 'info',
  global_timeout: 900,
  no_broadcast: true,
  // concurrency + serving
  max_loaded_models: 4,
  ctx_size: 4096,
  extra_models_dir: '/var/lib/hal0/models',
  // llama.cpp
  llamacpp_backend: 'rocm',
  llamacpp_args: '--parallel 1 --threads 8',
  // FLM (NPU)
  flm_args: '--asr 1 --embed 1',
  // whisper.cpp
  whispercpp_backend: 'vulkan',
  // Stable Diffusion
  sdcpp_backend: 'rocm',
  steps: 20,
  cfg_scale: 7.0,
  width: 512,
  height: 512,
})

// Original snapshot (for diff + revert).
const orig = ref({})

// Per-key inline validation errors — populated from POST 400 details.
const fieldErrors = ref({})

// ── form section catalog ─────────────────────────────────────────────
// Grouped per the brief: Service / Concurrency+serving / llama.cpp /
// FLM / whisper.cpp / Stable Diffusion. Each field carries the metadata
// the template needs to render labels, hints, and the locked-invariant
// inline error text up front (not just after a failed save).

const SECTIONS = [
  {
    id: 'service',
    title: 'Service',
    hint: 'Where lemond binds and how loud it logs. All immediate-effect.',
    fields: [
      { key: 'host', label: 'Host', type: 'text',
        hint: 'Bind address. 127.0.0.1 = loopback only (default).' },
      { key: 'port', label: 'Port', type: 'number',
        hint: 'Default 13305. Changing this requires reconfiguring hal0-api.' },
      { key: 'log_level', label: 'Log level', type: 'select',
        options: ['debug', 'info', 'warn', 'error'],
        hint: 'Verbosity of lemond logs (visible in the journal panel).' },
      { key: 'global_timeout', label: 'Global timeout (s)', type: 'number',
        hint: 'Inference request timeout. Default 900 (15 min).' },
      { key: 'no_broadcast', label: 'Disable LAN broadcast', type: 'checkbox',
        hint: 'On = lemond does not advertise itself on the LAN. Recommended.' },
    ],
  },
  {
    id: 'concurrency',
    title: 'Concurrency + serving',
    hint: 'Per-type budget + where lemond looks for models.',
    fields: [
      { key: 'max_loaded_models', label: 'Max loaded models (per type)', type: 'number',
        hint: 'LRU budget per model type (llm / embedding / image / ...).' },
      { key: 'ctx_size', label: 'Default ctx_size (tokens)', type: 'number',
        hint: 'Used as the default when a slot does not override.' },
      { key: 'extra_models_dir', label: 'Extra models dir', type: 'text',
        locked: true,
        hint: 'Locked to the symlink farm root. Must equal /var/lib/hal0/models.' },
    ],
  },
  {
    id: 'llamacpp',
    title: 'llama.cpp',
    hint: 'GGUF backend used for primary, agent, coder, embed, rerank slots on GPU.',
    fields: [
      { key: 'llamacpp_backend', label: 'Backend', type: 'select',
        options: ['rocm', 'vulkan', 'cpu'],
        hint: 'rocm = Strix Halo iGPU (recommended). Pin, not nightly.' },
      { key: 'llamacpp_args', label: 'Extra args', type: 'text',
        invariantHint: 'Must include --threads N where N >= 2.',
        hint: 'Default "--parallel 1 --threads N" — required to avoid the LXC oversubscribe deadlock.' },
    ],
  },
  {
    id: 'flm',
    title: 'FLM (NPU)',
    hint: 'AMDXDNA NPU trio — chat + ASR + embed packed into one flm process.',
    fields: [
      { key: 'flm_args', label: 'FLM args', type: 'text',
        invariantHint: 'Must include both --asr 1 AND --embed 1 (FLM trio).',
        hint: 'The trio flags are mandatory in v0.2 — dropping either leaves the NPU stt-npu or embed-npu slot without a backend.' },
    ],
  },
  {
    id: 'whisper',
    title: 'whisper.cpp',
    hint: 'STT backend for the cpu/gpu stt slot (NPU stt-npu uses FLM instead).',
    fields: [
      { key: 'whispercpp_backend', label: 'Backend', type: 'select',
        options: ['vulkan', 'rocm', 'cpu'],
        hint: 'vulkan is the default; switch if whisper.cpp builds for vulkan fail on your box.' },
    ],
  },
  {
    id: 'sdcpp',
    title: 'Stable Diffusion',
    hint: 'Image generation defaults — slot overrides win at request time.',
    fields: [
      { key: 'sdcpp_backend', label: 'Backend', type: 'select',
        options: ['rocm', 'vulkan', 'cpu'],
        hint: 'rocm matches the llama.cpp default on Strix Halo.' },
      { key: 'steps', label: 'Default steps', type: 'number',
        hint: 'Sampler iterations. 20 is a reasonable speed/quality default.' },
      { key: 'cfg_scale', label: 'CFG scale', type: 'number', step: '0.1',
        hint: 'Classifier-free guidance strength. 7.0 default.' },
      { key: 'width', label: 'Default width (px)', type: 'number',
        hint: 'Per-request override at the API layer always wins.' },
      { key: 'height', label: 'Default height (px)', type: 'number',
        hint: 'Per-request override at the API layer always wins.' },
    ],
  },
]

// Effect labels keyed off serverEffects so the source-of-truth is the
// backend's _hal0.effects block, not a frontend constant.
function effectFor(key) {
  if (serverEffects.value.immediate?.includes(key)) return 'immediate'
  if (serverEffects.value.deferred?.includes(key)) return 'deferred'
  return null
}
function effectLabel(key) {
  const eff = effectFor(key)
  if (eff === 'immediate') return 'Immediate'
  if (eff === 'deferred') return 'Deferred (next load)'
  return ''
}

// ── unpack / pack helpers ────────────────────────────────────────────
//
// lemond's /internal/config returns nested keys (llamacpp.args, ...);
// /internal/set takes flat keys (llamacpp_args, ...). Flatten on load,
// stay flat in the form, build a flat patch on save.

function unpackConfig(snapshot) {
  // Service + concurrency keys live at the top level — copy verbatim.
  for (const key of [
    'host', 'port', 'log_level', 'global_timeout', 'no_broadcast',
    'max_loaded_models', 'ctx_size', 'extra_models_dir',
  ]) {
    if (snapshot[key] !== undefined) form[key] = snapshot[key]
  }
  // Nested tables — flatten into the form keys lemond accepts on /set.
  if (snapshot.llamacpp) {
    if (snapshot.llamacpp.args !== undefined) form.llamacpp_args = snapshot.llamacpp.args
    if (snapshot.llamacpp.backend !== undefined) form.llamacpp_backend = snapshot.llamacpp.backend
  }
  if (snapshot.flm) {
    if (snapshot.flm.args !== undefined) form.flm_args = snapshot.flm.args
  }
  if (snapshot.whispercpp) {
    if (snapshot.whispercpp.backend !== undefined) form.whispercpp_backend = snapshot.whispercpp.backend
  }
  if (snapshot.sdcpp) {
    if (snapshot.sdcpp.backend !== undefined) form.sdcpp_backend = snapshot.sdcpp.backend
    if (snapshot.sdcpp.steps !== undefined) form.steps = snapshot.sdcpp.steps
    if (snapshot.sdcpp.cfg_scale !== undefined) form.cfg_scale = snapshot.sdcpp.cfg_scale
    if (snapshot.sdcpp.width !== undefined) form.width = snapshot.sdcpp.width
    if (snapshot.sdcpp.height !== undefined) form.height = snapshot.sdcpp.height
  }
}

function snapshotOrig() {
  orig.value = JSON.parse(JSON.stringify(form))
}

function valueChanged(key) {
  return String(form[key] ?? '') !== String(orig.value?.[key] ?? '')
}

const changedKeys = computed(() => {
  return Object.keys(form).filter((k) => valueChanged(k))
})

function buildPatch() {
  const patch = {}
  for (const k of changedKeys.value) patch[k] = form[k]
  return patch
}

// ── load + save ──────────────────────────────────────────────────────

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = await api('/api/lemonade/config')
    // Extract _hal0 metadata BEFORE unpacking — unpackConfig only looks
    // at known keys but keeping the meta block out of the form makes
    // the round-trip explicit.
    serverEffects.value = data?._hal0?.effects ?? { immediate: [], deferred: [] }
    lockedInvariants.value = data?._hal0?.locked ?? { extra_models_dir: '/var/lib/hal0/models' }
    unpackConfig(data)
    snapshotOrig()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function save() {
  if (changedKeys.value.length === 0) return
  fieldErrors.value = {}
  saving.value = true
  try {
    const body = JSON.stringify(buildPatch())
    const resp = await api('/api/lemonade/config', { method: 'POST', body })
    const eff = resp?.effects ?? { immediate: [], deferred: [] }
    const nI = eff.immediate?.length ?? 0
    const nD = eff.deferred?.length ?? 0
    if (nI && nD) {
      toasts.success(`Saved — ${nI} immediate, ${nD} deferred until next load`)
    } else if (nI) {
      toasts.success(`Saved — ${nI} immediate`)
    } else if (nD) {
      toasts.success(`Saved — ${nD} deferred until next load`)
    } else {
      toasts.success('Saved')
    }
    // Refetch so the form reflects what's actually persisted (and any
    // server-side normalisation lemond applied).
    await load()
  } catch (e) {
    if (e.code === 'lemonade.config_invalid' && e.details && typeof e.details === 'object') {
      // Backend returns details keyed by the flat lemond key (e.g.
      // "llamacpp_args"). Render inline.
      fieldErrors.value = e.details
      toasts.error('Some settings did not validate — see inline errors')
    } else {
      toasts.error(e.message)
    }
  } finally {
    saving.value = false
  }
}

function revert() {
  if (!orig.value) return
  for (const k of Object.keys(orig.value)) {
    form[k] = orig.value[k]
  }
  fieldErrors.value = {}
}

onMounted(load)
</script>

<template>
  <div class="lemonade-admin-page">
    <PageHeader
      eyebrow="Lemonade"
      title="Lemonade admin"
      subtitle="lemond runtime config — /internal/config + /internal/set"
    >
      <template #actions>
        <span v-if="changedKeys.length > 0" class="change-count">
          {{ changedKeys.length }} unsaved change{{ changedKeys.length !== 1 ? 's' : '' }}
        </span>
        <button
          class="btn-ghost"
          type="button"
          @click="revert"
          :disabled="saving || changedKeys.length === 0"
        >
          Revert
        </button>
        <button
          class="btn-primary"
          type="button"
          @click="save"
          :disabled="saving || changedKeys.length === 0"
          data-testid="lemonade-admin-save"
        >
          <span v-if="saving" class="spinner" aria-hidden="true" />
          {{ saving ? 'Saving…' : 'Save changes' }}
        </button>
      </template>
    </PageHeader>

    <div class="page-body">
      <div v-if="error" class="error-banner" role="alert" data-testid="lemonade-admin-error">
        {{ error }}
      </div>

      <div v-if="loading">
        <LoadingSkeleton />
      </div>

      <template v-else>
        <Card v-for="section in SECTIONS" :key="section.id">
          <div class="section-head">
            <h3 class="section-title">{{ section.title }}</h3>
            <p v-if="section.hint" class="section-hint">{{ section.hint }}</p>
          </div>
          <div class="field-grid">
            <div
              v-for="field in section.fields"
              :key="field.key"
              class="field-row"
              :data-testid="`lemonade-admin-field-${field.key}`"
            >
              <div class="field-meta">
                <label :for="`f-${field.key}`" class="field-label">
                  {{ field.label }}
                  <span
                    v-if="effectFor(field.key)"
                    class="effect-badge"
                    :class="`effect-${effectFor(field.key)}`"
                    :title="effectFor(field.key) === 'immediate'
                      ? 'Takes effect immediately'
                      : 'Persisted now; applies at the next model load'"
                  >
                    {{ effectLabel(field.key) }}
                  </span>
                </label>
                <p v-if="field.invariantHint" class="field-hint invariant-hint">
                  {{ field.invariantHint }}
                </p>
                <p v-else-if="field.hint" class="field-hint">{{ field.hint }}</p>
                <p
                  v-if="fieldErrors[field.key]"
                  class="field-err"
                  role="alert"
                  :data-testid="`lemonade-admin-error-${field.key}`"
                >
                  {{ fieldErrors[field.key] }}
                </p>
              </div>
              <div class="field-input-wrap">
                <template v-if="field.type === 'checkbox'">
                  <label class="toggle-label">
                    <input
                      :id="`f-${field.key}`"
                      type="checkbox"
                      v-model="form[field.key]"
                    />
                    <span>{{ form[field.key] ? 'Enabled' : 'Disabled' }}</span>
                  </label>
                </template>
                <template v-else-if="field.type === 'select'">
                  <select
                    :id="`f-${field.key}`"
                    v-model="form[field.key]"
                    class="field-input"
                    :class="{
                      'field-changed': valueChanged(field.key),
                      'field-error': !!fieldErrors[field.key],
                    }"
                  >
                    <option v-for="opt in field.options" :key="opt" :value="opt">
                      {{ opt }}
                    </option>
                  </select>
                </template>
                <template v-else>
                  <input
                    :id="`f-${field.key}`"
                    v-model="form[field.key]"
                    :type="field.type"
                    :step="field.step"
                    :readonly="field.locked"
                    class="field-input"
                    :class="{
                      'field-changed': valueChanged(field.key),
                      'field-error': !!fieldErrors[field.key],
                      'field-locked': field.locked,
                    }"
                  />
                </template>
              </div>
            </div>
          </div>
        </Card>
      </template>
    </div>
  </div>
</template>

<style scoped>
.lemonade-admin-page {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.page-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px 24px 24px;
}

.error-banner {
  background: color-mix(in srgb, var(--color-danger, #d33) 12%, var(--color-surface));
  border: 1px solid var(--color-danger, #d33);
  color: var(--color-fg);
  padding: 10px 14px;
  border-radius: var(--radius-md);
  font-size: 13px;
}

.section-head {
  margin-bottom: 12px;
}
.section-title {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: var(--color-fg);
}
.section-hint {
  margin: 0;
  font-size: 12px;
  color: var(--color-fg-muted);
}

.field-grid {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.field-row {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.field-meta {
  min-width: 0;
}

.field-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-fg);
}

.effect-badge {
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 2px 6px;
  border-radius: var(--radius-sm, 4px);
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  background: var(--color-surface-alt, transparent);
  font-weight: 500;
}
.effect-immediate {
  color: var(--hal0-accent);
  border-color: color-mix(in srgb, var(--hal0-accent) 40%, var(--color-border));
}
.effect-deferred {
  color: var(--color-fg-muted);
}

.field-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--color-fg-muted);
}

.invariant-hint {
  color: var(--hal0-accent);
  font-weight: 500;
}

.field-err {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--color-danger, #d33);
}

.field-input-wrap {
  display: flex;
  flex-direction: column;
}

.field-input {
  background: var(--color-surface-alt, var(--color-surface));
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 6px);
  padding: 6px 10px;
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--color-fg);
  width: 100%;
}

.field-changed {
  border-color: var(--hal0-accent);
}

.field-error {
  border-color: var(--color-danger, #d33);
}

.field-locked {
  opacity: 0.7;
  cursor: not-allowed;
}

.toggle-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--color-fg);
}

.change-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--hal0-accent);
}

.spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: lemonade-admin-spin 0.8s linear infinite;
  margin-right: 6px;
  vertical-align: -1px;
}

@keyframes lemonade-admin-spin {
  to { transform: rotate(360deg); }
}
</style>
