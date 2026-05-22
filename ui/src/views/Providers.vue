<script setup>
/**
 * Providers.vue — external upstream LLM providers.
 *
 * Backend contracts:
 *   GET  /api/upstreams              → list of upstream entries
 *     [{ name, kind, url, auth_style, auth_value_env, auth_configured,
 *        timeout_seconds, slot_name, warmup_strategy, advertise_models,
 *        models: [...cached model ids...] }, ...]
 *   GET  /api/upstreams/{name}       → single entry
 *   POST /api/upstreams/{name}/test  → { ok, status?, latency_ms,
 *                                        models_count?, error?, body_excerpt? }
 *   GET  /api/providers/catalog      → { [catalog_id]: catalog entry }
 *     Each catalog entry: { id, name, base_url, auth, auth_header_name,
 *                           models_path, default_models, default_model,
 *                           capabilities, docs_url, category, notes }
 *
 * Add/edit/delete: the API does not currently expose write paths for
 * upstreams (Phase 1 PLAN §6 keeps /etc/hal0/upstreams.toml as the
 * source of truth; a fully reactive editor lands later). The Add modal
 * captures the form fields for offline use — it surfaces a clear hint
 * that the user needs to drop the values into upstreams.toml + reload,
 * and copies a TOML snippet to the clipboard so they can do it in one
 * paste. Once a write endpoint lands, swap the clipboard path for a
 * POST and the rest of the UI stays put.
 *
 * API keys are never sent over the wire (the backend only stores the
 * env var *name* in auth_value_env). Display masks the auth value so
 * leaked screenshots don't reveal a key — though, again, hal0 never
 * has the key itself, only the env var pointer.
 */
import { ref, reactive, computed, onMounted } from 'vue'
import { useToastsStore } from '../stores/toasts.js'
import { api } from '../composables/useApi.js'
import PageHeader from '../components/PageHeader.vue'
import Card from '../components/Card.vue'
import LoadingSkeleton from '../components/LoadingSkeleton.vue'
import EmptyState from '../components/EmptyState.vue'

const toasts = useToastsStore()

const upstreams = ref([])     // /api/upstreams response
const catalog   = ref({})     // /api/providers/catalog
const loading   = ref(true)
const error     = ref(null)

// Per-upstream test result keyed by name.
const testResults = ref({})   // { [name]: { ok, latency_ms, models_count?, error?, status? } | 'testing' }

// Add / edit modal state.
const showForm  = ref(false)
const editing   = ref(null)   // name of upstream being edited, or null
const form = reactive({
  name: '',
  catalog_id: 'openrouter',
  base_url: '',
  auth_value_env: '',
  warmup_strategy: 'none',
})
const formErrors = ref({})

// ── loaders ──────────────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value = null
  try {
    const [ups, cat] = await Promise.all([
      api('/api/upstreams'),
      api('/api/providers/catalog'),
    ])
    upstreams.value = Array.isArray(ups) ? ups : []
    catalog.value   = cat || {}
  } catch (e) {
    error.value = e.message
    upstreams.value = []
    catalog.value = {}
  } finally {
    loading.value = false
  }
}

// ── test connectivity ────────────────────────────────────────────────
async function testUpstream(u) {
  testResults.value[u.name] = 'testing'
  try {
    const res = await api(`/api/upstreams/${encodeURIComponent(u.name)}/test`, { method: 'POST' })
    testResults.value[u.name] = res
    if (res.ok) {
      const ct = res.models_count != null ? ` · ${res.models_count} models` : ''
      toasts.success(`${u.name} reachable in ${res.latency_ms}ms${ct}`)
    } else {
      toasts.error(`${u.name}: ${res.error ?? `HTTP ${res.status ?? 'fail'}`}`)
    }
  } catch (e) {
    testResults.value[u.name] = { ok: false, error: e.message, latency_ms: 0 }
    toasts.error(e.message)
  }
}

function testDotColor(name) {
  const r = testResults.value[name]
  if (r === 'testing') return 'var(--color-warning)'
  if (!r) return null
  return r.ok ? 'var(--color-success)' : 'var(--color-danger)'
}

// ── derived ──────────────────────────────────────────────────────────
const catalogList = computed(() => Object.values(catalog.value))

function catalogFor(u) {
  // Best-effort match: try common shapes. The upstream itself doesn't
  // carry a catalog_id today (would be a nice forward addition), but
  // host-name matching is enough to show a friendly type chip.
  for (const c of catalogList.value) {
    if (c.base_url && u.url && u.url.startsWith(c.base_url)) return c
  }
  return null
}

// Mask the auth env var name's surrounding for display — the value is
// never on the wire so we mask the *env-var name* visually as a hint
// the secret is configured but not displayed.
function maskedAuth(u) {
  if (!u.auth_value_env) return 'none'
  const name = u.auth_value_env
  // env names look like HAL0_OPENROUTER_API_KEY — mask everything but
  // the last 4 chars so the user can still recognise which key it is.
  if (name.length <= 4) return '••••'
  return '••••••••••' + name.slice(-4)
}

// ── add / edit form ──────────────────────────────────────────────────
function openAddForm() {
  editing.value = null
  form.name = ''
  form.catalog_id = 'openrouter'
  applyCatalogDefaults('openrouter')
  formErrors.value = {}
  showForm.value = true
}

function openEditForm(u) {
  editing.value = u.name
  form.name = u.name
  // Find best-match catalog so type stays meaningful.
  const cat = catalogFor(u)
  form.catalog_id = cat?.id ?? 'custom'
  form.base_url = u.url ?? ''
  form.auth_value_env = u.auth_value_env ?? ''
  form.warmup_strategy = u.warmup_strategy ?? 'none'
  formErrors.value = {}
  showForm.value = true
}

function applyCatalogDefaults(cid) {
  const cat = catalog.value[cid]
  if (!cat) return
  form.base_url = cat.base_url ?? ''
  // Suggest a sensible env-var name; user can override.
  if (!form.auth_value_env || editing.value === null) {
    const slug = (cat.id || cid).toUpperCase().replace(/[^A-Z0-9]/g, '_')
    form.auth_value_env = cat.auth === 'none' ? '' : `HAL0_${slug}_API_KEY`
  }
}

function validateForm() {
  const errs = {}
  if (!form.name.trim()) errs.name = 'Required'
  else if (!/^[a-zA-Z0-9._-]+$/.test(form.name)) errs.name = 'Use alnum + .-_ only'
  if (!form.base_url.trim()) errs.base_url = 'Required'
  else if (!/^https?:\/\//.test(form.base_url)) errs.base_url = 'Must start with http:// or https://'
  formErrors.value = errs
  return Object.keys(errs).length === 0
}

// Render the form values as a TOML snippet matching upstreams.toml's
// `[[upstream]]` shape. The user pastes this into /etc/hal0/upstreams.toml
// (or sets the env var first), then hits "Reload from disk" on Settings.
const tomlSnippet = computed(() => {
  const cat = catalog.value[form.catalog_id]
  const auth = cat?.auth === 'none' ? 'none' : (cat?.auth === 'anthropic' ? 'header' : 'bearer')
  const lines = [
    '[[upstream]]',
    `name = "${form.name}"`,
    'kind = "remote"',
    `url = "${form.base_url}"`,
    `auth_style = "${auth}"`,
  ]
  if (form.auth_value_env) lines.push(`auth_value_env = "${form.auth_value_env}"`)
  lines.push(`warmup_strategy = "${form.warmup_strategy}"`)
  return lines.join('\n') + '\n'
})

async function copyTomlSnippet() {
  if (!validateForm()) return
  try {
    await navigator.clipboard.writeText(tomlSnippet.value)
    toasts.success('TOML snippet copied — paste into /etc/hal0/upstreams.toml then reload')
  } catch {
    toasts.error('Clipboard unavailable; copy the snippet manually')
  }
}

onMounted(load)
</script>

<template>
  <div class="providers-page">
    <PageHeader eyebrow="Upstreams" title="Providers" subtitle="External LLM routing — OpenRouter, Anthropic, OpenAI, custom">
      <template #actions>
        <button class="btn-ghost" type="button" @click="load" title="Refresh from server">
          <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
          Reload
        </button>
        <button class="btn-primary" type="button" @click="openAddForm">
          <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/>
          </svg>
          Add upstream
        </button>
      </template>
    </PageHeader>

    <div class="page-body">
      <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

      <template v-if="loading">
        <Card v-for="i in 3" :key="i"><LoadingSkeleton :lines="2" /></Card>
      </template>

      <template v-else-if="upstreams.length === 0">
        <Card :padded="false">
          <EmptyState
            icon="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9"
            title="No upstreams configured."
            description="Wire up OpenRouter, Anthropic, OpenAI, or any OpenAI-compatible endpoint to route requests beyond the slots running locally."
            cta-label="Add upstream"
            @cta="openAddForm"
          />
        </Card>
      </template>

      <template v-else>
        <div class="upstreams-list">
          <div v-for="u in upstreams" :key="u.name" class="upstream-row">
            <div class="up-left">
              <span
                class="status-dot"
                :style="{ background: testDotColor(u.name) ?? (u.auth_configured ? 'var(--color-fg-faint)' : 'transparent'), border: testDotColor(u.name) ? 'none' : '1px solid var(--color-border-hi)' }"
                :title="testResults[u.name] === 'testing' ? 'testing' : (testResults[u.name]?.ok ? 'reachable' : (testResults[u.name]?.error ?? 'not tested'))"
                aria-hidden="true"
              />
              <div class="up-info">
                <div class="up-name-row">
                  <span class="up-name">{{ u.name }}</span>
                  <span class="up-kind mono">{{ u.kind }}</span>
                  <span v-if="catalogFor(u)" class="up-catalog mono">{{ catalogFor(u).name }}</span>
                </div>
                <span class="up-url mono">{{ u.url }}</span>
                <div class="up-meta-row">
                  <span class="up-meta mono">auth: {{ u.auth_style }}</span>
                  <span class="up-meta mono">env: {{ maskedAuth(u) }}</span>
                  <span v-if="u.models?.length" class="up-meta mono">{{ u.models.length }} models cached</span>
                  <span v-if="testResults[u.name] && testResults[u.name] !== 'testing' && testResults[u.name].latency_ms != null"
                        class="up-meta mono"
                        :class="testResults[u.name].ok ? 'meta-good' : 'meta-bad'">
                    {{ testResults[u.name].latency_ms }}ms
                  </span>
                </div>
              </div>
            </div>
            <div class="up-actions">
              <button
                class="act-btn"
                type="button"
                :disabled="testResults[u.name] === 'testing'"
                @click="testUpstream(u)"
                title="Probe /v1/models with configured auth"
              >
                <span v-if="testResults[u.name] === 'testing'" class="spinner" aria-hidden="true" />
                <template v-else>Test</template>
              </button>
              <button
                class="act-btn"
                type="button"
                @click="openEditForm(u)"
                title="Edit"
                :aria-label="`Edit upstream ${u.name}`"
              >
                <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                </svg>
              </button>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- Add / edit modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showForm" class="modal-overlay" @click.self="showForm = false">
          <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="up-form-title">
            <div class="modal-header">
              <h2 id="up-form-title" class="modal-title">
                {{ editing ? `Edit upstream "${editing}"` : 'Add upstream' }}
              </h2>
              <button class="modal-close" type="button" @click="showForm = false" aria-label="Close">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                </svg>
              </button>
            </div>
            <div class="modal-body">
              <p class="hint-block">
                Add and edit go through <code class="mono">/etc/hal0/upstreams.toml</code>.
                Fill the form, copy the TOML snippet below, append it to the file, then click
                <strong>Reload from disk</strong> on the Settings view. The hal0 daemon doesn't expose a
                write endpoint for upstreams yet — file is the source of truth.
              </p>

              <div class="field">
                <label class="field-label" for="up-type">Provider type</label>
                <select id="up-type" v-model="form.catalog_id" class="field-input" @change="applyCatalogDefaults(form.catalog_id)">
                  <option v-for="c in catalogList" :key="c.id" :value="c.id">{{ c.name }} <span v-if="c.category">({{ c.category }})</span></option>
                </select>
                <p v-if="catalog[form.catalog_id]?.notes" class="field-hint">{{ catalog[form.catalog_id].notes }}</p>
              </div>

              <div class="field">
                <label class="field-label" for="up-name">Name <span class="req">*</span></label>
                <input id="up-name" v-model="form.name" :disabled="!!editing" class="field-input mono" :class="{ 'field-error': formErrors.name }" placeholder="e.g. openrouter-personal" />
                <p v-if="formErrors.name" class="field-err">{{ formErrors.name }}</p>
              </div>

              <div class="field">
                <label class="field-label" for="up-url">Base URL <span class="req">*</span></label>
                <input id="up-url" v-model="form.base_url" class="field-input mono" :class="{ 'field-error': formErrors.base_url }" placeholder="https://api.example.com/v1" />
                <p v-if="formErrors.base_url" class="field-err">{{ formErrors.base_url }}</p>
              </div>

              <div class="field">
                <label class="field-label" for="up-env">API key env var</label>
                <input id="up-env" v-model="form.auth_value_env" class="field-input mono" placeholder="HAL0_OPENROUTER_API_KEY" />
                <p class="field-hint">
                  Name of the env var that holds the API key. hal0 reads the value at request time and never stores the key on disk. Leave blank for unauthenticated upstreams.
                </p>
              </div>

              <div class="field">
                <label class="field-label" for="up-warmup">Warmup strategy</label>
                <select id="up-warmup" v-model="form.warmup_strategy" class="field-input">
                  <option value="none">none — wait until first request</option>
                  <option value="lazy">lazy — warm on dashboard load</option>
                  <option value="eager">eager — warm at startup</option>
                </select>
              </div>

              <div class="field">
                <label class="field-label">TOML snippet</label>
                <pre class="toml-preview mono">{{ tomlSnippet }}</pre>
              </div>
            </div>
            <div class="modal-footer">
              <button class="btn-ghost" type="button" @click="showForm = false">Close</button>
              <button class="btn-primary" type="button" @click="copyTomlSnippet">
                Copy TOML
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.providers-page { display: flex; flex-direction: column; min-height: 100%; }
.page-body      { padding: 20px 24px; display: flex; flex-direction: column; gap: 8px; }

.error-banner { padding: 10px 16px; border-radius: var(--radius-lg); background: color-mix(in oklch, var(--color-danger) 10%, var(--color-surface)); border: 1px solid color-mix(in oklch, var(--color-danger) 30%, transparent); color: var(--color-danger); font-size: 13px; }

.upstreams-list { display: flex; flex-direction: column; gap: 4px; }
.upstream-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 13px 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}
.upstream-row:hover { border-color: var(--color-border-hi); }

.up-left { display: flex; align-items: flex-start; gap: 12px; min-width: 0; flex: 1; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; margin-top: 6px; transition: background 0.2s; }
.up-info { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.up-name-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.up-name { font-family: var(--font-mono); font-size: 13px; font-weight: 600; color: var(--color-fg); }
.up-kind { font-size: 10px; padding: 1px 6px; border-radius: 3px; background: var(--color-surface-2); color: var(--color-fg-faint); border: 1px solid var(--color-border); text-transform: uppercase; letter-spacing: 0.06em; }
.up-catalog { font-size: 10px; padding: 1px 6px; border-radius: 3px; background: color-mix(in srgb, var(--hal0-accent) 12%, transparent); color: var(--hal0-accent); border: 1px solid color-mix(in srgb, var(--hal0-accent) 35%, transparent); text-transform: uppercase; letter-spacing: 0.06em; }
.up-url { font-size: 11.5px; color: var(--color-fg-muted); word-break: break-all; }
.up-meta-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.up-meta { font-size: 10.5px; color: var(--color-fg-faint); }
.up-meta.meta-good { color: var(--color-success); }
.up-meta.meta-bad  { color: var(--color-danger); }

.up-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.act-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 11.5px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.act-btn:hover { border-color: var(--color-border-hi); color: var(--color-fg); }
.act-btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* Modal */
.modal-overlay { position: fixed; inset: 0; z-index: 200; background: rgba(0,0,0,0.6); backdrop-filter: blur(4px); display: flex; align-items: center; justify-content: center; padding: 16px; }
.modal-box { background: var(--color-surface); border: 1px solid var(--color-border-hi); border-radius: var(--radius-xl); width: min(520px, 100%); max-height: 90vh; display: flex; flex-direction: column; box-shadow: 0 24px 64px rgba(0,0,0,0.6); overflow: hidden; }
.modal-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--color-border); }
.modal-title { font-size: 15px; font-weight: 600; color: var(--color-fg); margin: 0; }
.modal-close { width: 28px; height: 28px; border-radius: var(--radius); background: transparent; border: 1px solid transparent; color: var(--color-fg-faint); cursor: pointer; display: grid; place-items: center; }
.modal-close:hover { background: var(--color-surface-2); color: var(--color-fg); }
.modal-body { padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; flex: 1; }
.modal-footer { padding: 16px 20px; border-top: 1px solid var(--color-border); display: flex; justify-content: flex-end; gap: 8px; }

.field { display: flex; flex-direction: column; gap: 5px; }
.field-label { font-size: 12.5px; font-weight: 600; color: var(--color-fg-muted); }
.req { color: var(--color-danger); }
.field-input { padding: 7px 10px; border-radius: var(--radius); border: 1px solid var(--color-border); background: var(--color-surface-2); color: var(--color-fg); font-size: 13px; outline: none; transition: border-color 0.1s; box-sizing: border-box; width: 100%; }
.field-input:focus { border-color: var(--color-border-hi); }
.field-input.mono { font-family: var(--font-mono); }
.field-input:disabled { opacity: 0.6; cursor: not-allowed; }
.field-error { border-color: var(--color-danger) !important; }
.field-err   { font-size: 11.5px; color: var(--color-danger); margin: 0; }
.field-hint  { font-size: 11.5px; color: var(--color-fg-faint); margin: 0; font-family: var(--font-mono); }
.mono { font-family: var(--font-mono); }

.hint-block {
  font-size: 12px;
  color: var(--color-fg-muted);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  padding: 10px 12px;
  margin: 0;
}
.hint-block code { background: var(--color-surface-3); padding: 1px 4px; border-radius: 3px; }

.toml-preview {
  font-family: var(--font-mono);
  font-size: 11.5px;
  padding: 10px 12px;
  background: oklch(11% 0.01 250);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  color: var(--color-fg-muted);
  white-space: pre-wrap;
  margin: 0;
  max-height: 180px;
  overflow: auto;
}

.btn-primary { display: flex; align-items: center; gap: 6px; padding: 7px 16px; border-radius: var(--radius); background: var(--hal0-accent); color: #000; font-family: var(--font-mono); font-size: 12px; font-weight: 500; border: none; cursor: pointer; transition: background 0.15s; }
.btn-primary:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn-primary:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-ghost { display: flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: var(--radius); border: 1px solid var(--color-border); background: transparent; color: var(--color-fg-muted); font-family: var(--font-mono); font-size: 12px; cursor: pointer; transition: border-color 0.15s, color 0.15s; }
.btn-ghost:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }
.btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }

.spinner { width: 11px; height: 11px; border: 2px solid rgba(255,255,255,0.3); border-top-color: white; border-radius: 50%; animation: spin 0.7s linear infinite; flex-shrink: 0; }
@keyframes spin { to { transform: rotate(360deg); } }

.fade-enter-active, .fade-leave-active { transition: opacity 0.12s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
