<script setup>
/**
 * Settings.vue — typed config editor backed by /api/settings.
 *
 * Backend contract (Team C):
 *   GET  /api/settings          → { meta, slots, dispatcher, telemetry }
 *   PUT  /api/settings (partial, deep-merged) → updated config
 *   POST /api/settings/reload   → re-read hal0.toml into the running process
 *   GET  /api/settings/schema   → pydantic JSON schema (used to populate
 *                                 type / description / constraints below)
 *
 * The schema lives in src/hal0/config/schema.py — sections:
 *   - meta.schema_version          int >= 1  (restart on bump)
 *   - slots.max_slots              int >= 0 (0 = unlimited)
 *   - slots.port_range_start/end   1024–65535
 *   - dispatcher.prefetch_timeout_s float > 0
 *   - dispatcher.prefetch_parallel_cap int >= 1
 *   - telemetry.enabled            bool
 *   - telemetry.channel            'stable' | 'nightly'  (restart-required)
 *
 * Validation failures from PUT come back as { error: { code:
 * "config.invalid", details: { "field.path": "msg" }}}. useApi.js's
 * fetch wrapper surfaces those on the Error.details map so we can render
 * inline per-field reasons.
 */
import { ref, computed, reactive, onMounted } from 'vue'
import { useToastsStore } from '../stores/toasts.js'
import { api } from '../composables/useApi.js'
import PageHeader from '../components/PageHeader.vue'
import Card from '../components/Card.vue'
import LoadingSkeleton from '../components/LoadingSkeleton.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'

const toasts = useToastsStore()

const loading = ref(true)
const saving  = ref(false)
const error   = ref(null)

// Original snapshot (for diff + revert) and live form values.
const orig = ref({})
const form = reactive({
  meta:       { schema_version: 1 },
  slots:      { max_slots: 0, port_range_start: 8081, port_range_end: 8099 },
  dispatcher: { prefetch_timeout_s: 8.0, prefetch_parallel_cap: 4 },
  telemetry:  { enabled: false, channel: 'stable' },
})

// Per-field error map keyed by pydantic field path (e.g.
// "dispatcher.prefetch_timeout_s"). Populated when PUT returns
// code: "config.invalid".
const fieldErrors = ref({})

// Keys (as dot-paths) whose change requires an API restart to take effect.
const RESTART_REQUIRED = new Set([
  'telemetry.channel',
  'meta.schema_version',
])

// Show a local "restart required" banner after a successful save when
// any restart-required key actually changed. Resets on next save.
const pendingRestart = ref(false)
const restartedKeys  = ref([])

// ── load + apply ─────────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value = null
  try {
    const data = await api('/api/settings')
    applyServerData(data)
    snapshotOrig()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function applyServerData(data) {
  // Deep-copy the four known sections; preserve unknown keys via
  // `extra="allow"` round-trip by stashing them on the section objects.
  const sections = ['meta', 'slots', 'dispatcher', 'telemetry']
  for (const key of sections) {
    const src = data?.[key] ?? {}
    form[key] = { ...form[key], ...src }
  }
}

function snapshotOrig() {
  orig.value = JSON.parse(JSON.stringify({
    meta: form.meta,
    slots: form.slots,
    dispatcher: form.dispatcher,
    telemetry: form.telemetry,
  }))
}

// ── diff helpers ─────────────────────────────────────────────────────
function valueChanged(section, key) {
  return String(form[section][key]) !== String(orig.value?.[section]?.[key] ?? '')
}

const changedFields = computed(() => {
  const out = []
  for (const section of ['meta', 'slots', 'dispatcher', 'telemetry']) {
    const before = orig.value?.[section] ?? {}
    const after  = form[section] ?? {}
    for (const key of Object.keys(after)) {
      const a = before[key]
      const b = after[key]
      if (String(a ?? '') !== String(b ?? '')) {
        out.push({ path: `${section}.${key}`, from: a, to: b })
      }
    }
  }
  return out
})

// ── save ─────────────────────────────────────────────────────────────
function buildPatch() {
  // Send only the dotted paths that actually changed, deep-merge-safe.
  const patch = {}
  for (const ch of changedFields.value) {
    const [section, key] = ch.path.split('.')
    if (!patch[section]) patch[section] = {}
    patch[section][key] = ch.to
  }
  return patch
}

async function save() {
  if (changedFields.value.length === 0) return
  fieldErrors.value = {}
  saving.value = true
  try {
    const body = JSON.stringify(buildPatch())
    const updated = await api('/api/settings', { method: 'PUT', body })
    const changedRestartKeys = changedFields.value
      .map((c) => c.path)
      .filter((p) => RESTART_REQUIRED.has(p))
    applyServerData(updated)
    snapshotOrig()
    toasts.success('Settings saved')
    if (changedRestartKeys.length > 0) {
      pendingRestart.value = true
      restartedKeys.value  = changedRestartKeys
    }
  } catch (e) {
    if (e.code === 'config.invalid' && e.details && typeof e.details === 'object') {
      // Render per-field inline error messages exactly where the form
      // says "field-err" today. Pydantic returns paths like
      // "dispatcher.prefetch_timeout_s" — match those verbatim.
      fieldErrors.value = e.details
      toasts.error('Settings did not validate — see inline errors')
    } else {
      toasts.error(e.message)
    }
  } finally {
    saving.value = false
  }
}

async function reload() {
  // POST /api/settings/reload re-reads hal0.toml from disk into the
  // running process — useful after an out-of-band editor change.
  try {
    const data = await api('/api/settings/reload', { method: 'POST' })
    applyServerData(data)
    snapshotOrig()
    fieldErrors.value = {}
    toasts.success('Settings reloaded from disk')
  } catch (e) {
    toasts.error(e.message)
  }
}

function revert() {
  applyServerData(orig.value)
  fieldErrors.value = {}
}

function dismissRestart() {
  pendingRestart.value = false
  restartedKeys.value  = []
}

// ── field declarations (used by the template) ───────────────────────
// Built off the pydantic schema in src/hal0/config/schema.py. The
// schema endpoint /api/settings/schema is available if we want to be
// fully data-driven later; keeping these explicit keeps the form
// human-readable and lets us write per-field hints. extra keys
// preserved server-side via `extra="allow"` will still round-trip even
// though they don't render here.
const SECTIONS = [
  {
    id: 'meta',
    title: 'Meta',
    fields: [
      {
        key: 'schema_version',
        label: 'Schema version',
        type: 'number',
        hint: 'Bumped by config migrations. Restart required when manually edited.',
      },
    ],
  },
  {
    id: 'slots',
    title: 'Slots',
    fields: [
      { key: 'max_slots', label: 'Max concurrent slots', type: 'number',
        hint: '0 means unlimited.' },
      { key: 'port_range_start', label: 'Port range start', type: 'number',
        hint: 'First port available to slots (default 8081).' },
      { key: 'port_range_end', label: 'Port range end', type: 'number',
        hint: 'Last port (inclusive, default 8099).' },
    ],
  },
  {
    id: 'dispatcher',
    title: 'Dispatcher',
    fields: [
      { key: 'prefetch_timeout_s', label: 'Prefetch timeout (s)', type: 'number', step: '0.5',
        hint: 'Cold-cache prefetch deadline. Default 8s (PLAN §5 Tier 2).' },
      { key: 'prefetch_parallel_cap', label: 'Prefetch parallel cap', type: 'number',
        hint: 'Max concurrent upstream prefetches. Default 4.' },
    ],
  },
  {
    id: 'telemetry',
    title: 'Telemetry',
    fields: [
      { key: 'enabled', label: 'Telemetry enabled', type: 'checkbox',
        hint: 'Off by default — anonymous opt-in ping (PLAN §14).' },
      { key: 'channel', label: 'Update channel', type: 'select',
        options: ['stable', 'nightly'],
        hint: 'Stable = tagged releases; nightly = every main push. Restart-required.' },
    ],
  },
]

// ── Authentication panel (v0.2 auth POC, Team J) ─────────────────────
//
// The panel reads /api/auth/status to render the on/off badge and the
// caller's identity, and /api/auth/tokens to list mintable bearer
// tokens. Token CRUD goes through the admin-protected /api/auth/tokens
// subrouter (require_admin) — the dashboard caller is always admin via
// Caddy basic_auth in the deployed config, so the call succeeds.
//
// Important UX note: a freshly-minted token's raw value is shown ONCE
// in `newTokenRaw` and never again. The modal warns the user verbatim,
// then on close clears the raw value from memory.

const authStatus    = ref({ enabled: false, modes: [], managed_via_installer: true })
const authIdentity  = ref({ identity: 'anonymous', scope: 'all', source: 'anonymous' })
const tokens        = ref([])
const tokensLoading = ref(false)
const tokensError   = ref(null)

const showCreateTokenModal = ref(false)
const newTokenLabel        = ref('')
const newTokenScope        = ref('all')
const newTokenRaw          = ref('')          // shown once, cleared on close
const newTokenCopied       = ref(false)
const creatingToken        = ref(false)
const showRevokeModal      = ref(false)
const revokeTarget         = ref(null)        // {id, label}
const showAuthInfoModal    = ref(false)

async function loadAuthState() {
  try {
    authStatus.value = await api('/api/auth/status')
  } catch (e) {
    // Auth status is public; a failure here means the API itself is
    // unhealthy — surface as an error banner via the existing route.
    error.value = e.message
    return
  }
  // /api/auth/me requires a Bearer or X-Forwarded-Email — when the
  // dashboard is loaded directly (no auth) the call 401s and we
  // gracefully fall back to "anonymous". When loaded behind Caddy, the
  // forwarded email gives us the admin identity.
  try {
    authIdentity.value = await api('/api/auth/me')
  } catch {
    authIdentity.value = { identity: 'anonymous', scope: 'all', source: 'anonymous' }
  }
  await loadTokens()
}

async function loadTokens() {
  tokensLoading.value = true
  tokensError.value = null
  try {
    const data = await api('/api/auth/tokens')
    tokens.value = data.tokens || []
  } catch (e) {
    // 401 / 403 here is "you're not admin" or "auth disabled" — render
    // a clear hint rather than a noisy toast. We still want the panel
    // to render so the operator can see the auth state.
    if (e.code === 'auth.required' || e.code === 'auth.forbidden') {
      tokens.value = []
      tokensError.value = 'Token management is admin-only. Sign in via the Caddy basic_auth prompt or use an admin Bearer token.'
    } else if (e.code === 'system.http_404') {
      // Auth router not mounted — older API, ignore.
      tokens.value = []
    } else {
      tokensError.value = e.message
    }
  } finally {
    tokensLoading.value = false
  }
}

function openCreateTokenModal() {
  newTokenLabel.value = ''
  newTokenScope.value = 'all'
  newTokenRaw.value   = ''
  newTokenCopied.value = false
  showCreateTokenModal.value = true
}

function closeCreateTokenModal() {
  // Clear the raw token from memory before the modal unmounts. Vue's
  // reactivity won't expose it any more once the v-if flips false, but
  // we want the local ref empty too in case dev tools snapshot state.
  newTokenRaw.value = ''
  newTokenLabel.value = ''
  newTokenCopied.value = false
  showCreateTokenModal.value = false
}

async function submitCreateToken() {
  if (!newTokenLabel.value.trim()) {
    toasts.error('Token label is required')
    return
  }
  creatingToken.value = true
  try {
    const body = JSON.stringify({
      label: newTokenLabel.value.trim(),
      scope: newTokenScope.value,
    })
    const result = await api('/api/auth/tokens', { method: 'POST', body })
    newTokenRaw.value = result.token
    toasts.success(`Token "${result.label}" created — copy it now`)
    await loadTokens()
  } catch (e) {
    toasts.error(e.message)
  } finally {
    creatingToken.value = false
  }
}

async function copyTokenToClipboard() {
  if (!newTokenRaw.value) return
  try {
    await navigator.clipboard.writeText(newTokenRaw.value)
    newTokenCopied.value = true
    toasts.success('Token copied to clipboard')
  } catch {
    toasts.error('Could not copy — select the value manually')
  }
}

function openRevokeModal(token) {
  revokeTarget.value = token
  showRevokeModal.value = true
}

async function confirmRevokeToken() {
  if (!revokeTarget.value) return
  try {
    await api(`/api/auth/tokens/${revokeTarget.value.id}`, { method: 'DELETE' })
    toasts.success(`Token "${revokeTarget.value.label}" revoked`)
    showRevokeModal.value = false
    revokeTarget.value = null
    await loadTokens()
  } catch (e) {
    toasts.error(e.message)
  }
}

function formatTimestamp(iso) {
  if (!iso) return 'never'
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return iso
    return d.toLocaleString()
  } catch {
    return iso
  }
}

// ── Model locations panel ───────────────────────────────────────────
//
// GET /api/config/models returns { roots, auto_scan_on_start, file_extensions }.
// PUT /api/config/models persists the same shape and immediately re-scans;
// the response carries a `scan` sub-object the toast can summarise.
const modelsCfg = reactive({
  pull_root: '/var/lib/hal0/models',
  roots: [],
  auto_scan_on_start: true,
  file_extensions: ['.gguf', '.safetensors'],
})
const modelsCfgLoading = ref(true)
const modelsCfgSaving  = ref(false)
const modelsCfgError   = ref(null)

async function loadModelsCfg() {
  modelsCfgLoading.value = true
  modelsCfgError.value = null
  try {
    const data = await api('/api/config/models')
    modelsCfg.pull_root = typeof data.pull_root === 'string' && data.pull_root
      ? data.pull_root
      : '/var/lib/hal0/models'
    // Filter pull_root out of the editable scan-roots list — the API
    // auto-includes it on save, and showing it twice would let the user
    // remove it accidentally.
    const rawRoots = Array.isArray(data.roots) ? data.roots : []
    modelsCfg.roots = rawRoots.filter((r) => r !== modelsCfg.pull_root)
    modelsCfg.auto_scan_on_start = !!data.auto_scan_on_start
    modelsCfg.file_extensions = Array.isArray(data.file_extensions)
      ? [...data.file_extensions]
      : ['.gguf', '.safetensors']
  } catch (e) {
    modelsCfgError.value = e.message
  } finally {
    modelsCfgLoading.value = false
  }
}

function addModelRoot() {
  modelsCfg.roots.push('')
}

function removeModelRoot(i) {
  modelsCfg.roots.splice(i, 1)
}

async function saveAndScanModelRoots() {
  modelsCfgSaving.value = true
  try {
    const body = JSON.stringify({
      pull_root: (modelsCfg.pull_root || '').trim() || '/var/lib/hal0/models',
      roots: modelsCfg.roots.map((r) => r.trim()).filter((r) => r.length > 0),
      auto_scan_on_start: modelsCfg.auto_scan_on_start,
      file_extensions: modelsCfg.file_extensions,
    })
    const updated = await api('/api/config/models', { method: 'PUT', body })
    modelsCfg.pull_root = typeof updated.pull_root === 'string' && updated.pull_root
      ? updated.pull_root
      : '/var/lib/hal0/models'
    const rawRoots = Array.isArray(updated.roots) ? updated.roots : []
    modelsCfg.roots = rawRoots.filter((r) => r !== modelsCfg.pull_root)
    modelsCfg.auto_scan_on_start = !!updated.auto_scan_on_start
    modelsCfg.file_extensions = [...(updated.file_extensions || [])]
    const scan = updated.scan || { added: [], skipped: [] }
    const added = (scan.added || []).length
    const skipped = (scan.skipped || []).length
    toasts.success(`Scanned — ${added} added, ${skipped} skipped`)
  } catch (e) {
    if (e.code === 'config.invalid' && e.details && typeof e.details === 'object') {
      const first = Object.entries(e.details)[0]
      const msg = first ? `${first[0]}: ${first[1]}` : e.message
      toasts.error(`Invalid: ${msg}`)
    } else {
      toasts.error(e.message)
    }
  } finally {
    modelsCfgSaving.value = false
  }
}

// ── Proxmox integration ──────────────────────────────────────────────
// Powers the "Proxmox host" segment on the Dashboard memory bar.
// Config lives at /etc/hal0/proxmox.json (separate from hal0.toml).
// Endpoints: GET / PUT / DELETE /api/settings/proxmox, POST .../test.
const pveLoading = ref(false)
const pveSaving  = ref(false)
const pveTesting = ref(false)
const pveError   = ref(null)
const pveTestResult = ref(null)
const pveStatus  = ref(null)
const pveConfigured = ref(false)
const pveTokenSet = ref(false)
const pveForm = reactive({
  host: '',
  port: 8006,
  user: '',
  token_name: '',
  token_value: '',
  verify_ssl: false,
})

async function loadProxmox() {
  pveLoading.value = true
  pveError.value = null
  try {
    const data = await api('/api/settings/proxmox')
    pveConfigured.value = !!data.configured
    pveTokenSet.value = !!data.token_value_set
    pveForm.host = data.host || ''
    pveForm.port = data.port || 8006
    pveForm.user = data.user || ''
    pveForm.token_name = data.token_name || ''
    pveForm.verify_ssl = !!data.verify_ssl
    // Never echo the secret — keep the field empty on load. Operators
    // re-enter it only when rotating credentials.
    pveForm.token_value = ''
    pveStatus.value = data.status || null
  } catch (e) {
    pveError.value = e.message
  } finally {
    pveLoading.value = false
  }
}

async function testProxmox() {
  pveTesting.value = true
  pveError.value = null
  pveTestResult.value = null
  try {
    if (!pveForm.token_value) {
      pveTestResult.value = { ok: false, error: 'enter the token value to test' }
      return
    }
    const body = JSON.stringify({
      host: pveForm.host.trim(),
      port: Number(pveForm.port) || 8006,
      user: pveForm.user.trim(),
      token_name: pveForm.token_name.trim(),
      token_value: pveForm.token_value,
      verify_ssl: !!pveForm.verify_ssl,
    })
    const result = await api('/api/settings/proxmox/test', { method: 'POST', body })
    pveTestResult.value = result
    if (result.ok) toasts.success(`Reached ${result.node} — ${result.tenants_total} tenants visible`)
    else toasts.error(`Test failed: ${result.error}`)
  } catch (e) {
    pveTestResult.value = { ok: false, error: e.message }
    toasts.error(e.message)
  } finally {
    pveTesting.value = false
  }
}

async function saveProxmox() {
  pveSaving.value = true
  pveError.value = null
  try {
    const body = {
      host: pveForm.host.trim(),
      port: Number(pveForm.port) || 8006,
      user: pveForm.user.trim(),
      token_name: pveForm.token_name.trim(),
      verify_ssl: !!pveForm.verify_ssl,
    }
    // Only send token_value when the operator actually entered one —
    // omission means "keep the existing token on disk."
    if (pveForm.token_value) body.token_value = pveForm.token_value
    const data = await api('/api/settings/proxmox', {
      method: 'PUT',
      body: JSON.stringify(body),
    })
    pveConfigured.value = !!data.configured
    pveTokenSet.value = !!data.token_value_set
    pveStatus.value = data.status || null
    pveForm.token_value = ''
    pveTestResult.value = null
    toasts.success('Proxmox integration saved')
  } catch (e) {
    if (e.code === 'proxmox.config_invalid' && e.details && typeof e.details === 'object') {
      const first = Object.entries(e.details)[0]
      const msg = first ? `${first[0]}: ${first[1]}` : e.message
      toasts.error(`Invalid: ${msg}`)
    } else {
      toasts.error(e.message)
    }
  } finally {
    pveSaving.value = false
  }
}

async function removeProxmox() {
  if (!confirm('Remove Proxmox integration? The dashboard will stop showing host-pressure data.')) return
  pveSaving.value = true
  pveError.value = null
  try {
    await api('/api/settings/proxmox', { method: 'DELETE' })
    pveConfigured.value = false
    pveTokenSet.value = false
    pveStatus.value = { configured: false }
    pveForm.host = ''
    pveForm.port = 8006
    pveForm.user = ''
    pveForm.token_name = ''
    pveForm.token_value = ''
    pveForm.verify_ssl = false
    pveTestResult.value = null
    toasts.success('Proxmox integration removed')
  } catch (e) {
    toasts.error(e.message)
  } finally {
    pveSaving.value = false
  }
}

const pveStatusLine = computed(() => {
  const s = pveStatus.value
  if (!s) return null
  if (s.configured === false) return 'Not configured.'
  if (s.ok === false) return `Cannot reach Proxmox: ${s.error || 'unknown error'}`
  const totalGb = (s.host_mem_total_mb / 1024).toFixed(0)
  const usedGb  = (s.host_mem_used_mb  / 1024).toFixed(1)
  return `${s.node} · ${usedGb} / ${totalGb} GB used · ${s.tenants_running} of ${s.tenants_total} tenants running`
})

onMounted(async () => {
  await load()
  await loadAuthState()
  await loadModelsCfg()
  await loadProxmox()
})
</script>

<template>
  <div class="settings-page">
    <PageHeader eyebrow="Config" title="Settings" subtitle="hal0.toml runtime configuration">
      <template #actions>
        <span v-if="changedFields.length > 0" class="change-count">
          {{ changedFields.length }} unsaved change{{ changedFields.length !== 1 ? 's' : '' }}
        </span>
        <button class="btn-ghost" type="button" @click="reload" :disabled="saving" title="Re-read /etc/hal0/hal0.toml from disk">
          Reload from disk
        </button>
        <button class="btn-ghost" type="button" @click="revert" :disabled="saving || changedFields.length === 0">
          Revert
        </button>
        <button class="btn-primary" type="button" @click="save" :disabled="saving || changedFields.length === 0">
          <span v-if="saving" class="spinner" aria-hidden="true" />
          {{ saving ? 'Saving…' : 'Save changes' }}
        </button>
      </template>
    </PageHeader>

    <!-- Restart-required banner (rendered after a successful save when
         a restart-required key actually changed). Matches the styling
         of components/RestartBanner.vue so the visual language is
         consistent — that component is wired to system store updates
         and stays our home for update-available banners. -->
    <Transition name="slide-up">
      <div v-if="pendingRestart" class="restart-banner" role="alert">
        <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
        </svg>
        <span>
          Restart required for: <strong class="mono">{{ restartedKeys.join(', ') }}</strong>. Slots will keep running across an API restart.
        </span>
        <button type="button" class="banner-btn" @click="dismissRestart">Dismiss</button>
      </div>
    </Transition>

    <div class="page-body">
      <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

      <!-- ── Authentication panel (Team J / v0.2 auth POC) ────────── -->
      <Card>
        <div class="auth-header">
          <h3 class="section-title">Authentication</h3>
          <div class="auth-status-row">
            <span class="auth-badge" :class="authStatus.enabled ? 'auth-on' : 'auth-off'">
              {{ authStatus.enabled ? 'Enabled' : 'Disabled' }}
            </span>
            <span class="auth-identity mono" v-if="authStatus.enabled">
              {{ authIdentity.identity }} · scope=<strong>{{ authIdentity.scope }}</strong>
            </span>
            <button class="btn-ghost btn-small" type="button" @click="showAuthInfoModal = true">
              How does this work?
            </button>
          </div>
        </div>

        <div v-if="!authStatus.enabled" class="auth-disabled-hint">
          Auth is currently <strong>off</strong> — the API and chat bind public ports
          with no credentials required. Re-run the installer with
          <code class="mono">--auth=basic</code> to bring up Caddy, basic_auth at the
          edge, and bearer tokens for the OpenAI-compatible API. Toggling auth here
          alone would lock you out without a Caddy front; the installer wires both
          sides atomically.
        </div>

        <div v-else>
          <div class="tokens-row">
            <div>
              <h4 class="subsection-title">Bearer tokens</h4>
              <p class="field-hint">
                Programmatic clients (OpenWebUI bridge, third-party OpenAI SDKs)
                authenticate by sending <code class="mono">Authorization: Bearer hal0_…</code>.
                Browser sessions go through Caddy basic_auth — no token needed.
              </p>
            </div>
            <button class="btn-primary btn-small" type="button" @click="openCreateTokenModal">
              + Create token
            </button>
          </div>

          <div v-if="tokensError" class="error-banner" role="alert">{{ tokensError }}</div>
          <div v-else-if="tokensLoading" class="loading-row">Loading tokens…</div>
          <div v-else-if="tokens.length === 0" class="empty-row">
            No tokens yet. Create one to authenticate the OpenWebUI bridge or any
            external OpenAI client.
          </div>
          <table v-else class="token-table">
            <thead>
              <tr>
                <th>Label</th>
                <th>Scope</th>
                <th>Created</th>
                <th>Last used</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in tokens" :key="t.id">
                <td class="mono">{{ t.label }}</td>
                <td><span class="scope-badge">{{ t.scope }}</span></td>
                <td class="mono dim">{{ formatTimestamp(t.created_at) }}</td>
                <td class="mono dim">{{ formatTimestamp(t.last_used_at) }}</td>
                <td class="row-actions">
                  <button class="btn-danger btn-small" type="button"
                          @click="openRevokeModal(t)" title="Revoke token">
                    Revoke
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Card>

      <!-- ── Model locations panel ───────────────────────────────── -->
      <Card>
        <h3 class="section-title">Model locations</h3>
        <p class="field-hint">
          <strong>Pull destination</strong> is where <code class="mono">hal0 model pull</code>
          (and the dashboard's pull buttons) write new files —
          <code class="mono">&lt;pull_root&gt;/&lt;model_id&gt;/&lt;filename&gt;</code>.
          <strong>Scan roots</strong> are extra directories walked for already-present
          model files (.gguf, .safetensors). The pull destination is always scanned
          automatically, so you only need extra roots for read-only stores like
          <code class="mono">/mnt/ai-models</code>.
        </p>
        <div v-if="modelsCfgError" class="error-banner" role="alert">{{ modelsCfgError }}</div>
        <div v-else-if="modelsCfgLoading" class="loading-row">Loading…</div>
        <div v-else class="roots-list">
          <div class="pull-root-row">
            <label for="pull-root" class="field-label">Pull destination</label>
            <input
              id="pull-root"
              v-model="modelsCfg.pull_root"
              type="text"
              class="field-input"
              placeholder="/var/lib/hal0/models"
              :disabled="modelsCfgSaving"
            />
          </div>
          <div class="roots-section-label">Extra scan roots</div>
          <div v-for="(_, i) in modelsCfg.roots" :key="i" class="root-row">
            <input
              v-model="modelsCfg.roots[i]"
              type="text"
              class="field-input"
              placeholder="/mnt/ai-models"
              :disabled="modelsCfgSaving"
            />
            <button
              type="button"
              class="btn-ghost btn-small"
              @click="removeModelRoot(i)"
              :disabled="modelsCfgSaving"
              title="Remove root"
            >
              Remove
            </button>
          </div>
          <div v-if="modelsCfg.roots.length === 0" class="empty-row">
            No extra roots — only the pull destination is scanned.
          </div>
          <div class="roots-actions">
            <button
              type="button"
              class="btn-ghost"
              @click="addModelRoot"
              :disabled="modelsCfgSaving"
            >
              + Add root
            </button>
            <label class="auto-scan-label">
              <input
                type="checkbox"
                v-model="modelsCfg.auto_scan_on_start"
                :disabled="modelsCfgSaving"
              />
              <span>Auto-scan on startup</span>
            </label>
            <button
              type="button"
              class="btn-primary btn-small"
              @click="saveAndScanModelRoots"
              :disabled="modelsCfgSaving"
            >
              <span v-if="modelsCfgSaving" class="spinner" aria-hidden="true" />
              {{ modelsCfgSaving ? 'Scanning…' : 'Save & re-scan' }}
            </button>
          </div>
        </div>
      </Card>

      <!-- ── Proxmox integration panel ───────────────────────────── -->
      <Card>
        <div class="proxmox-header">
          <h3 class="section-title">Proxmox integration</h3>
          <span class="auth-badge" :class="pveConfigured ? 'auth-on' : 'auth-off'">
            {{ pveConfigured ? 'Configured' : 'Off' }}
          </span>
        </div>
        <p class="field-hint">
          Optional. When hal0 runs as a Proxmox LXC or VM, configure a
          read-only <code class="mono">PVEAuditor</code> (or root) API token
          so the dashboard's memory bar can show RAM consumed by other
          tenants and the host kernel — not just this LXC's slice. Leave
          off on bare-metal installs.
          <br />
          Create the token at
          <code class="mono">Datacenter → Permissions → API Tokens</code>
          (uncheck "Privilege Separation" or grant the token
          <code class="mono">PVEAuditor</code> on <code class="mono">/</code>).
        </p>

        <div v-if="pveError" class="error-banner" role="alert">{{ pveError }}</div>
        <div v-else-if="pveLoading" class="loading-row">Loading…</div>
        <div v-else>
          <p v-if="pveStatusLine" class="proxmox-status mono" :class="{ 'text-success': pveStatus?.ok, 'text-danger': pveStatus?.configured && !pveStatus?.ok }">
            {{ pveStatusLine }}
          </p>

          <div class="proxmox-grid">
            <div class="field-row proxmox-field">
              <label for="pve-host" class="field-label">Host</label>
              <input id="pve-host" v-model="pveForm.host" type="text" class="field-input" placeholder="10.0.1.110" :disabled="pveSaving" />
            </div>
            <div class="field-row proxmox-field proxmox-field-narrow">
              <label for="pve-port" class="field-label">Port</label>
              <input id="pve-port" v-model.number="pveForm.port" type="number" class="field-input" min="1" max="65535" :disabled="pveSaving" />
            </div>
            <div class="field-row proxmox-field">
              <label for="pve-user" class="field-label">User</label>
              <input id="pve-user" v-model="pveForm.user" type="text" class="field-input" placeholder="root@pam" :disabled="pveSaving" />
            </div>
            <div class="field-row proxmox-field">
              <label for="pve-token-name" class="field-label">Token name</label>
              <input id="pve-token-name" v-model="pveForm.token_name" type="text" class="field-input" placeholder="hal0-readonly" :disabled="pveSaving" />
            </div>
            <div class="field-row proxmox-field proxmox-field-wide">
              <label for="pve-token-value" class="field-label">
                Token value
                <span v-if="pveTokenSet && !pveForm.token_value" class="restart-badge" title="A token is on disk; leave blank to keep it">on disk</span>
              </label>
              <input id="pve-token-value" v-model="pveForm.token_value" type="password" class="field-input mono"
                     :placeholder="pveTokenSet ? '••••••••  (leave blank to keep existing)' : 'paste the token UUID'"
                     :disabled="pveSaving" autocomplete="off" />
            </div>
            <div class="field-row proxmox-field proxmox-field-toggle">
              <label class="toggle-label">
                <input type="checkbox" class="toggle-checkbox" v-model="pveForm.verify_ssl" :disabled="pveSaving" />
                <span>Verify TLS certificate</span>
              </label>
              <p class="field-hint dim">Off by default — most home Proxmox installs use the self-signed cert.</p>
            </div>
          </div>

          <p v-if="pveTestResult && !pveTestResult.ok" class="field-err" role="alert">
            Test failed: {{ pveTestResult.error }}
          </p>

          <div class="proxmox-actions">
            <button class="btn-ghost" type="button" @click="testProxmox" :disabled="pveTesting || pveSaving || !pveForm.host || !pveForm.user || !pveForm.token_name">
              <span v-if="pveTesting" class="spinner" aria-hidden="true" />
              {{ pveTesting ? 'Testing…' : 'Test connection' }}
            </button>
            <button class="btn-primary btn-small" type="button" @click="saveProxmox"
                    :disabled="pveSaving || !pveForm.host || !pveForm.user || !pveForm.token_name || (!pveTokenSet && !pveForm.token_value)">
              <span v-if="pveSaving" class="spinner" aria-hidden="true" />
              {{ pveSaving ? 'Saving…' : 'Save' }}
            </button>
            <button v-if="pveConfigured" class="btn-danger btn-small" type="button" @click="removeProxmox" :disabled="pveSaving">
              Remove
            </button>
          </div>
        </div>
      </Card>

      <template v-if="loading">
        <Card v-for="i in 3" :key="i"><LoadingSkeleton :lines="3" /></Card>
      </template>

      <template v-else>
        <Card v-for="section in SECTIONS" :key="section.id">
          <h3 class="section-title">{{ section.title }}</h3>
          <div class="fields">
            <div v-for="field in section.fields" :key="field.key" class="field-row">
              <div class="field-meta">
                <label :for="`f-${section.id}-${field.key}`" class="field-label">
                  {{ field.label }}
                  <span v-if="RESTART_REQUIRED.has(`${section.id}.${field.key}`)"
                        class="restart-badge"
                        title="Requires API restart">restart</span>
                </label>
                <p v-if="field.hint" class="field-hint">{{ field.hint }}</p>
                <p v-if="fieldErrors[`${section.id}.${field.key}`]" class="field-err" role="alert">
                  {{ fieldErrors[`${section.id}.${field.key}`] }}
                </p>
              </div>
              <div class="field-input-wrap">
                <template v-if="field.type === 'checkbox'">
                  <label class="toggle-label">
                    <input
                      :id="`f-${section.id}-${field.key}`"
                      type="checkbox"
                      class="toggle-checkbox"
                      v-model="form[section.id][field.key]"
                    />
                    <span class="toggle-track">
                      <span class="toggle-thumb" />
                    </span>
                    <span class="toggle-text">{{ form[section.id][field.key] ? 'Enabled' : 'Disabled' }}</span>
                  </label>
                </template>
                <template v-else-if="field.type === 'select'">
                  <select
                    :id="`f-${section.id}-${field.key}`"
                    v-model="form[section.id][field.key]"
                    class="field-input"
                    :class="{
                      'field-changed': valueChanged(section.id, field.key),
                      'field-error':   !!fieldErrors[`${section.id}.${field.key}`],
                    }"
                  >
                    <option v-for="opt in field.options" :key="opt" :value="opt">{{ opt }}</option>
                  </select>
                </template>
                <template v-else>
                  <input
                    :id="`f-${section.id}-${field.key}`"
                    v-model="form[section.id][field.key]"
                    :type="field.type"
                    :step="field.step"
                    class="field-input"
                    :class="{
                      'field-changed': valueChanged(section.id, field.key),
                      'field-error':   !!fieldErrors[`${section.id}.${field.key}`],
                    }"
                  />
                </template>
              </div>
            </div>
          </div>
        </Card>
      </template>
    </div>

    <!-- ── Create-token modal (Team J) ─────────────────────────── -->
    <Teleport to="body">
      <Transition name="fade">
        <div
          v-if="showCreateTokenModal"
          class="dialog-overlay"
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-token-title"
          @click.self="closeCreateTokenModal"
        >
          <div class="dialog-box">
            <h3 id="create-token-title" class="dialog-title">Create bearer token</h3>

            <div v-if="!newTokenRaw" class="dialog-body">
              <p class="field-hint">
                The token's raw value is shown <strong>once</strong> and never
                retrievable afterwards. Copy it into a secret manager
                immediately after creation.
              </p>
              <div class="field-row dialog-field">
                <label for="new-token-label" class="field-label">Label</label>
                <input
                  id="new-token-label"
                  v-model="newTokenLabel"
                  type="text"
                  class="field-input"
                  placeholder="e.g. openwebui-bridge"
                  :disabled="creatingToken"
                />
              </div>
              <div class="field-row dialog-field">
                <label for="new-token-scope" class="field-label">Scope</label>
                <select
                  id="new-token-scope"
                  v-model="newTokenScope"
                  class="field-input"
                  :disabled="creatingToken"
                >
                  <option value="all">all (chat + admin)</option>
                  <option value="admin">admin (token CRUD only)</option>
                  <option value="v1-only">v1-only (chat / embed / etc.)</option>
                  <option value="read-only">read-only (probes + listings)</option>
                </select>
              </div>
            </div>

            <div v-else class="dialog-body">
              <p class="field-hint warning">
                Copy this token now — it will not be shown again.
              </p>
              <div class="raw-token-box mono" role="textbox" aria-readonly="true">
                {{ newTokenRaw }}
              </div>
              <button class="btn-ghost btn-small" type="button"
                      @click="copyTokenToClipboard">
                {{ newTokenCopied ? 'Copied' : 'Copy to clipboard' }}
              </button>
            </div>

            <div class="dialog-actions">
              <template v-if="!newTokenRaw">
                <button class="btn-ghost" type="button"
                        @click="closeCreateTokenModal" :disabled="creatingToken">
                  Cancel
                </button>
                <button class="btn-primary" type="button"
                        @click="submitCreateToken" :disabled="creatingToken">
                  {{ creatingToken ? 'Creating…' : 'Create token' }}
                </button>
              </template>
              <template v-else>
                <button class="btn-primary" type="button"
                        @click="closeCreateTokenModal">
                  I've saved this token; close
                </button>
              </template>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- ── Revoke-token confirm modal ──────────────────────────── -->
    <ConfirmDialog
      v-model:open="showRevokeModal"
      title="Revoke token?"
      :message="`Revoke '${revokeTarget?.label ?? ''}'? Any client using this token will fail to authenticate immediately.`"
      :danger="true"
      confirm-label="Revoke"
      @confirm="confirmRevokeToken"
    />

    <!-- ── Auth info modal (How does this work?) ───────────────── -->
    <Teleport to="body">
      <Transition name="fade">
        <div
          v-if="showAuthInfoModal"
          class="dialog-overlay"
          role="dialog"
          aria-modal="true"
          @click.self="showAuthInfoModal = false"
        >
          <div class="dialog-box">
            <h3 class="dialog-title">How hal0 auth works</h3>
            <div class="dialog-body">
              <p class="field-hint">
                Two surfaces share the same identity model:
              </p>
              <ul class="field-hint">
                <li>
                  <strong>Browser sessions</strong> — Caddy basic_auth at the edge.
                  Caddy forwards <code class="mono">X-Forwarded-Email</code> to hal0
                  and OpenWebUI; both treat the header as the authenticated
                  identity. No second login.
                </li>
                <li>
                  <strong>Programmatic clients</strong> — send
                  <code class="mono">Authorization: Bearer hal0_…</code>. Tokens
                  are minted here, hashed with argon2id at
                  <code class="mono">/etc/hal0/tokens.toml</code>, and revocable
                  any time.
                </li>
              </ul>
              <p class="field-hint">
                The Caddy front + the <code class="mono">HAL0_AUTH_ENABLED=1</code>
                flag are wired together by <code class="mono">install.sh --auth=basic</code>.
                Toggling <code class="mono">HAL0_AUTH_ENABLED</code> alone (without
                Caddy in front) is intentionally NOT exposed in this UI — it would
                lock you out of the dashboard with no recovery path other than
                editing <code class="mono">/etc/hal0/api.env</code> by hand.
              </p>
            </div>
            <div class="dialog-actions">
              <button class="btn-primary" type="button"
                      @click="showAuthInfoModal = false">Got it</button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.settings-page { display: flex; flex-direction: column; min-height: 100%; }
.page-body     { padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; }

.restart-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 24px;
  background: color-mix(in oklch, var(--color-warning) 14%, var(--color-surface));
  border-bottom: 1px solid color-mix(in oklch, var(--color-warning) 30%, transparent);
  color: var(--color-warning);
  font-size: 13px;
}
.banner-btn {
  margin-left: auto;
  padding: 4px 12px;
  border-radius: var(--radius);
  background: var(--color-warning);
  color: var(--color-bg);
  font-size: 12px;
  font-weight: 600;
  border: none;
  cursor: pointer;
  flex-shrink: 0;
}
.banner-btn:hover { opacity: 0.9; }
.slide-up-enter-active { transition: all 0.2s ease; }
.slide-up-leave-active { transition: all 0.15s ease; }
.slide-up-enter-from   { opacity: 0; transform: translateY(-100%); }
.slide-up-leave-to     { opacity: 0; transform: translateY(-100%); }

.error-banner { padding: 10px 16px; border-radius: var(--radius-lg); background: color-mix(in oklch, var(--color-danger) 10%, var(--color-surface)); border: 1px solid color-mix(in oklch, var(--color-danger) 30%, transparent); color: var(--color-danger); font-size: 13px; }

.section-title { font-family: var(--font-mono); font-size: 11px; font-weight: 500; color: var(--hal0-accent); margin: 0 0 14px; letter-spacing: 0.08em; text-transform: uppercase; }

.fields { display: flex; flex-direction: column; gap: 14px; }
.field-row { display: grid; grid-template-columns: 1fr 240px; align-items: start; gap: 16px; }
@media (max-width: 640px) { .field-row { grid-template-columns: 1fr; } }

.field-meta { display: flex; flex-direction: column; gap: 3px; }
.field-label { font-size: 13px; font-weight: 500; color: var(--color-fg); display: flex; align-items: center; gap: 8px; }
.field-hint { font-size: 11.5px; color: var(--color-fg-faint); margin: 0; font-family: var(--font-mono); }
.field-err  { font-size: 11.5px; color: var(--color-danger); margin: 0; }
.restart-badge { font-family: var(--font-mono); font-size: 9.5px; padding: 1px 5px; border-radius: 3px; background: color-mix(in oklch, var(--color-warning) 15%, transparent); color: var(--color-warning); border: 1px solid color-mix(in oklch, var(--color-warning) 30%, transparent); white-space: nowrap; }

.field-input-wrap { display: flex; flex-direction: column; }
.field-input {
  padding: 7px 10px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-size: 13px;
  outline: none;
  transition: border-color 0.1s;
  width: 100%;
  box-sizing: border-box;
}
.field-input:focus { border-color: var(--color-border-hi); }
.field-changed { border-color: color-mix(in srgb, var(--hal0-accent) 55%, var(--color-border)) !important; box-shadow: inset 2px 0 0 var(--hal0-accent); }
.field-error   { border-color: var(--color-danger) !important; }

.toggle-label { display: flex; align-items: center; gap: 10px; cursor: pointer; }
.toggle-checkbox { display: none; }
.toggle-track {
  width: 36px; height: 20px; border-radius: 10px;
  background: var(--color-surface-3); border: 1px solid var(--color-border);
  position: relative; flex-shrink: 0; transition: background 0.15s;
}
.toggle-checkbox:checked + .toggle-track { background: var(--hal0-accent); border-color: var(--hal0-accent); }
.toggle-thumb {
  position: absolute; left: 2px; top: 2px;
  width: 14px; height: 14px; border-radius: 50%;
  background: white; transition: transform 0.15s;
}
.toggle-checkbox:checked + .toggle-track .toggle-thumb { transform: translateX(16px); }
.toggle-text { font-size: 13px; color: var(--color-fg-muted); }

.change-count { font-family: var(--font-mono); font-size: 11px; color: var(--hal0-accent); text-transform: uppercase; letter-spacing: 0.06em; }
.mono { font-family: var(--font-mono); }

.btn-primary { display: flex; align-items: center; gap: 6px; padding: 7px 16px; border-radius: var(--radius); background: var(--hal0-accent); color: #000; font-family: var(--font-mono); font-size: 12px; font-weight: 500; border: none; cursor: pointer; transition: background 0.15s; }
.btn-primary:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn-primary:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-ghost { padding: 7px 16px; border-radius: var(--radius); border: 1px solid var(--color-border); background: transparent; color: var(--color-fg-muted); font-family: var(--font-mono); font-size: 12px; cursor: pointer; transition: border-color 0.15s, color 0.15s; }
.btn-ghost:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }
.btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }

.spinner { width: 11px; height: 11px; border: 2px solid rgba(255,255,255,0.3); border-top-color: white; border-radius: 50%; animation: spin 0.7s linear infinite; flex-shrink: 0; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Authentication panel ────────────────────────────────────────────── */

.auth-header { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
.auth-status-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.auth-badge {
  display: inline-block;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.auth-on  { background: color-mix(in srgb, var(--hal0-accent) 14%, transparent); color: var(--hal0-accent); border: 1px solid color-mix(in srgb, var(--hal0-accent) 35%, transparent); }
.auth-off { background: color-mix(in oklch, var(--color-warning) 16%, transparent); color: var(--color-warning); border: 1px solid color-mix(in oklch, var(--color-warning) 30%, transparent); }
.auth-identity { font-size: 11.5px; color: var(--color-fg-muted); }

.auth-disabled-hint {
  padding: 10px 12px;
  border-radius: var(--radius);
  background: color-mix(in oklch, var(--color-warning) 8%, var(--color-surface-2));
  border: 1px solid color-mix(in oklch, var(--color-warning) 22%, transparent);
  color: var(--color-fg-muted);
  font-size: 12.5px;
  line-height: 1.55;
}
.auth-disabled-hint code { padding: 1px 5px; border-radius: 3px; background: var(--color-surface-3); color: var(--color-fg); }

.tokens-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.subsection-title { font-size: 12.5px; font-weight: 600; color: var(--color-fg); margin: 0 0 4px; }

.loading-row, .empty-row {
  padding: 12px;
  text-align: center;
  font-size: 12px;
  color: var(--color-fg-faint);
  background: var(--color-surface-2);
  border-radius: var(--radius);
}

.token-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.token-table th {
  text-align: left;
  font-weight: 500;
  font-size: 11px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--color-fg-faint);
  padding: 6px 8px;
  border-bottom: 1px solid var(--color-border);
}
.token-table td {
  padding: 8px;
  border-bottom: 1px solid color-mix(in oklch, var(--color-border) 60%, transparent);
}
.token-table tr:last-child td { border-bottom: none; }
.token-table .dim { color: var(--color-fg-faint); font-size: 11.5px; }
.scope-badge {
  display: inline-block;
  padding: 1px 7px;
  font-size: 10.5px;
  font-family: var(--font-mono);
  border-radius: 3px;
  background: var(--color-surface-3);
  color: var(--color-fg-muted);
  border: 1px solid var(--color-border);
}
.row-actions { text-align: right; }

.btn-small { padding: 4px 10px; font-size: 11.5px; }
.btn-danger {
  border: 1px solid color-mix(in oklch, var(--color-danger) 40%, var(--color-border));
  background: transparent;
  color: var(--color-danger);
  border-radius: var(--radius);
  cursor: pointer;
}
.btn-danger:hover:not(:disabled) {
  background: color-mix(in oklch, var(--color-danger) 12%, transparent);
}

/* ── Modal styling (mirrors ConfirmDialog.vue) ──────────────────────── */
.dialog-overlay {
  position: fixed; inset: 0;
  background: color-mix(in oklch, var(--color-bg) 70%, transparent);
  backdrop-filter: blur(2px);
  display: flex; align-items: center; justify-content: center;
  z-index: 50;
}
.dialog-box {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 18px 20px;
  width: 100%;
  max-width: 460px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
}
.dialog-title { font-size: 14px; font-weight: 600; margin: 0 0 12px; color: var(--color-fg); }
.dialog-body { display: flex; flex-direction: column; gap: 12px; margin-bottom: 16px; font-size: 13px; }
.dialog-body code { padding: 1px 5px; border-radius: 3px; background: var(--color-surface-3); }
.dialog-body ul { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 8px; }
.dialog-field { display: flex; flex-direction: column; gap: 4px; align-items: stretch; grid-template-columns: none; }
.dialog-actions { display: flex; gap: 8px; justify-content: flex-end; }

.field-hint.warning { color: var(--color-warning); }
.raw-token-box {
  padding: 10px 12px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border-hi);
  border-radius: var(--radius);
  font-size: 12px;
  word-break: break-all;
  user-select: all;
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.12s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

/* ── Model locations ─────────────────────────────────────────────────── */
.roots-list { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
.pull-root-row { display: flex; flex-direction: column; gap: 4px; margin-bottom: 4px; }
.pull-root-row .field-input { font-family: var(--font-mono); }
.roots-section-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--hal0-accent); font-family: var(--font-mono); margin-top: 6px; }
.root-row { display: flex; align-items: center; gap: 8px; }
.root-row .field-input { flex: 1; font-family: var(--font-mono); }
.roots-actions { display: flex; align-items: center; gap: 12px; margin-top: 6px; flex-wrap: wrap; }
.auto-scan-label { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--color-fg-muted); cursor: pointer; }
.auto-scan-label input[type="checkbox"] { accent-color: var(--hal0-accent); }

/* ── Proxmox integration panel ──────────────────────────────────── */
.proxmox-header { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.proxmox-status {
  margin: 8px 0 14px;
  padding: 8px 12px;
  border-radius: var(--radius);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  font-size: 12px;
}
.proxmox-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(200px, 1fr));
  gap: 10px 16px;
  margin-top: 8px;
}
.proxmox-field { display: flex; flex-direction: column; gap: 4px; padding: 0; border-bottom: none; }
.proxmox-field .field-label { font-size: 11.5px; font-weight: 500; }
.proxmox-field .field-input { font-family: var(--font-mono); font-size: 12.5px; padding: 6px 10px; }
.proxmox-field-narrow { max-width: 130px; }
.proxmox-field-wide { grid-column: 1 / -1; }
.proxmox-field-toggle { grid-column: 1 / -1; flex-direction: column; align-items: flex-start; gap: 4px; }
.proxmox-actions { display: flex; gap: 10px; align-items: center; margin-top: 14px; flex-wrap: wrap; }
.dim { color: var(--color-fg-faint); }
</style>
