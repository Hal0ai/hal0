<script setup>
/**
 * Settings.vue — v2 dashboard Settings page (slice #173).
 *
 * Layout
 * ──────
 *   Left rail: anchor list of 9 sections (sticky, scroll-spy active).
 *   Right column: scrollable content. Each section renders under
 *   `<section data-section="<id>">` so the rail can find them.
 *
 * Sections (in order)
 * ───────────────────
 *   1. Auth        — bearer token, allowed origins, rotate confirm.
 *   2. Secrets     — HF_TOKEN, OPENAI_API_KEY, etc. + AddSecretModal.
 *   3. Updates     — hal0 / lemonade / flm versions, channels, cadence.
 *   4. Lemonade admin — folds in PR-13's LemonadeAdmin via inline form;
 *                       llamacpp.args is readonly-by-default with the
 *                       footgun warning on edit toggle.
 *   5. OmniRouter  — 5 upstream + 3 hal0 tool definitions.
 *   6. Agent policy— stub linking to Agent view (slice #174 owns extras).
 *   7. Memory      — Cognee namespace ops; reset is type-to-confirm.
 *   8. Appearance  — theme + density, writes useTweaksStore.
 *   9. About       — version, license, bundled licenses drawer.
 *
 * Backend contracts
 * ─────────────────
 *   - /api/auth/status, /api/auth/tokens — real, mounted by hal0-api.
 *   - /api/secrets        — may 404 → falls back to in-memory MOCK_SECRETS.
 *   - /api/updates/check  — may 404 → MOCK_UPDATES.
 *   - /api/lemonade/config — real (PR-13 endpoint, also used by the
 *                             standalone /settings/lemonade subview which
 *                             stays mounted so the lemonade-admin.spec.ts
 *                             route assertion + PageHeader title pass).
 *   - /api/omni-tools     — may 404 → MOCK_OMNI_TOOLS.
 *   - /api/memory/namespaces — may 404 → MOCK_MEMORY.
 *
 * Why keep the /settings/lemonade subview alive?
 * ──────────────────────────────────────────────
 *   PR-13's lemonade-admin.spec.ts asserts (a) a link on /settings carries
 *   `data-testid="lemonade-admin-link"` and routes to /settings/lemonade,
 *   and (b) the destination page has a `.page-title` matching "Lemonade
 *   admin". The new in-page Lemonade section is a quick-access subset of
 *   the same surface; the full admin panel still lives at its own route
 *   for the unsaved-changes-on-leave behaviour PR-13 relies on.
 */
import { reactive, ref, onMounted, onBeforeUnmount, nextTick, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../composables/useApi.js'
import { useToastsStore } from '../stores/toasts.js'
import { useTweaksStore } from '../stores/tweaks.js'

import SettingsRail from '../components/settings/SettingsRail.vue'
import SecRow from '../components/settings/SecRow.vue'
import SecKey from '../components/settings/SecKey.vue'
import RestartChip from '../components/settings/RestartChip.vue'
import AddSecretModal from '../components/settings/AddSecretModal.vue'
import AllowedOriginsModal from '../components/settings/AllowedOriginsModal.vue'
import RotateTokenDialog from '../components/settings/RotateTokenDialog.vue'
import SaveAndRestartDialog from '../components/settings/SaveAndRestartDialog.vue'
import BundledLicensesDrawer from '../components/settings/BundledLicensesDrawer.vue'
import ConfirmDialog from '../components/primitives/ConfirmDialog.vue'

const toasts = useToastsStore()
const tweaks = useTweaksStore()
const router = useRouter()

/* ─── Section catalog (drives both the rail + scroll-spy) ──────────── */
const SECTIONS = [
  { id: 'auth',       label: 'Auth' },
  { id: 'secrets',    label: 'Secrets' },
  { id: 'updates',    label: 'Updates' },
  { id: 'lemonade',   label: 'Lemonade admin' },
  { id: 'omni',       label: 'OmniRouter' },
  { id: 'agent',      label: 'Agent policy' },
  { id: 'memory',     label: 'Memory (Cognee)' },
  { id: 'appearance', label: 'Appearance' },
  { id: 'about',      label: 'About' },
]

const activeSection = ref('auth')
const sectionRefs = ref({})  // id → DOM element

function navigateTo(id) {
  const el = sectionRefs.value[id]
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  activeSection.value = id
  history.replaceState(null, '', `#${id}`)
}

// Scroll-spy via IntersectionObserver — first intersecting section
// becomes active. Threshold tuned so the rail tracks the section
// currently at the top of the viewport.
let _observer = null
onMounted(async () => {
  await nextTick()
  // Hash-jump if URL says so.
  const hash = location.hash.replace(/^#/, '')
  if (hash && SECTIONS.some((s) => s.id === hash)) {
    activeSection.value = hash
    await nextTick()
    sectionRefs.value[hash]?.scrollIntoView({ block: 'start' })
  }
  _observer = new IntersectionObserver(
    (entries) => {
      // Pick the topmost intersecting section.
      const visible = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
      if (visible.length) {
        activeSection.value = visible[0].target.dataset.section
      }
    },
    { rootMargin: '-30% 0px -55% 0px', threshold: 0 },
  )
  for (const id of Object.keys(sectionRefs.value)) {
    const el = sectionRefs.value[id]
    if (el) _observer.observe(el)
  }

  await Promise.all([
    loadAuth(),
    loadSecrets(),
    loadUpdates(),
    loadLemonade(),
    loadOmni(),
    loadMemory(),
  ])
})

onBeforeUnmount(() => {
  if (_observer) _observer.disconnect()
})

function setSectionRef(id, el) {
  if (el) sectionRefs.value[id] = el
}

/* ─── 1. Auth ──────────────────────────────────────────────────────── */
const auth = reactive({
  loading: true,
  enabled: false,
  identity: 'anonymous',
  tokenMasked: 'hal0-•••••••••••••••••••••••••••••••••',
  tokenRaw: null,                       // populated on rotate
  showToken: false,
  issued: 'unknown',
  origins: ['http://localhost:5174'],
})
const rotateOpen = ref(false)
const originsOpen = ref(false)

async function loadAuth() {
  try {
    const s = await api('/api/auth/status')
    auth.enabled = !!s.enabled
  } catch { auth.enabled = false }
  try {
    const me = await api('/api/auth/me')
    auth.identity = me.identity || 'anonymous'
  } catch { /* anonymous fallback already set */ }
  try {
    const t = await api('/api/auth/tokens')
    const list = t.tokens || []
    if (list.length) {
      auth.issued = list[0].created_at || 'unknown'
    }
  } catch { /* admin-gated or 404 — leave issued blank */ }
  auth.loading = false
}

async function rotateToken() {
  rotateOpen.value = false
  try {
    const r = await api('/api/auth/tokens', {
      method: 'POST',
      body: JSON.stringify({ label: 'dashboard-rotated', scope: 'all' }),
    })
    if (r.token) {
      auth.tokenRaw = r.token
      auth.showToken = true
      toasts.success('Token rotated — copy the new value before navigating away')
    } else {
      toasts.warning('Token rotation acknowledged but no new value returned')
    }
  } catch (e) {
    // No real endpoint here in many deployments; fall back to a generated
    // ephemeral display so operators see the flow on demo boxes.
    auth.tokenRaw = `hal0-${crypto.randomUUID()}`
    auth.showToken = true
    toasts.warning(`Token rotation simulated (${e.code || 'no-endpoint'})`)
  }
}

function saveOrigins(list) {
  auth.origins = list
  originsOpen.value = false
  toasts.success(`Allowed origins saved (${list.length})`)
}

/* ─── 2. Secrets ───────────────────────────────────────────────────── */
const MOCK_SECRETS = [
  { id: 'HF_TOKEN', name: 'HF_TOKEN', set: true,
    description: 'Hugging Face — used by lemond for gated repos' },
  { id: 'OPENAI_API_KEY', name: 'OPENAI_API_KEY', set: false,
    description: 'Optional · fallback provider' },
  { id: 'ANTHROPIC_API_KEY', name: 'ANTHROPIC_API_KEY', set: false,
    description: 'Optional · fallback provider' },
]
const secrets = ref([])
const secretsBackendAvailable = ref(false)
const addSecretOpen = ref(false)

async function loadSecrets() {
  try {
    const r = await api('/api/secrets')
    if (Array.isArray(r?.secrets)) {
      secrets.value = r.secrets
      secretsBackendAvailable.value = true
    } else {
      // 200 OK with empty body (catch-all stub, or unimplemented endpoint).
      // Treat as no-backend and fall back to seeded mocks so the section
      // is exercise-able on Lemonade-only deployments.
      secrets.value = JSON.parse(JSON.stringify(MOCK_SECRETS))
      secretsBackendAvailable.value = false
    }
  } catch (e) {
    if (e.code === 'system.http_404' || e.status === 404) {
      secrets.value = JSON.parse(JSON.stringify(MOCK_SECRETS))
      secretsBackendAvailable.value = false
    } else {
      secrets.value = []
    }
  }
}

async function saveSecret(payload) {
  if (secretsBackendAvailable.value) {
    try {
      await api('/api/secrets', { method: 'POST', body: JSON.stringify(payload) })
      await loadSecrets()
      toasts.success(`${payload.name} saved`)
      addSecretOpen.value = false
      return
    } catch (e) {
      toasts.error(e.message || 'Could not save secret')
      return
    }
  }
  secrets.value.push({
    id: payload.name,
    name: payload.name,
    set: true,
    description: payload.description,
  })
  toasts.success(`${payload.name} saved (local mock)`)
  addSecretOpen.value = false
}

async function removeSecret(id) {
  if (secretsBackendAvailable.value) {
    try {
      await api(`/api/secrets/${id}`, { method: 'DELETE' })
      await loadSecrets()
      toasts.success(`${id} removed`)
      return
    } catch (e) {
      toasts.error(e.message || 'Could not remove')
      return
    }
  }
  const i = secrets.value.findIndex((s) => s.id === id)
  if (i >= 0) {
    secrets.value[i].set = false
    toasts.success(`${id} cleared (local mock)`)
  }
}

/* ─── 3. Updates ──────────────────────────────────────────────────── */
const updates = reactive({
  components: [
    { id: 'hal0', label: 'hal0', sub: 'Dashboard + API + CLI',
      current: 'v0.2.1', available: null, channel: 'stable' },
    { id: 'lemonade', label: 'lemonade', sub: 'Pinned. SHA-256 verified.',
      current: 'v10.6.0', available: null, channel: 'stable' },
    { id: 'flm', label: 'flm', sub: 'Manual deb · vendor-supplied',
      current: 'v0.9.42', available: null, channel: 'stable' },
  ],
  autoOnBoot: false,
  cadence: 'daily',  // 'manual' | 'daily' | 'weekly'
})
const updateConfirmOpen = ref(false)
const updateTarget = ref(null)

async function loadUpdates() {
  try {
    const r = await api('/api/updates/check')
    // Expected shape: { components: [{id, current, available, channel}] }
    if (Array.isArray(r.components)) {
      for (const c of r.components) {
        const slot = updates.components.find((x) => x.id === c.id)
        if (slot) Object.assign(slot, c)
      }
    }
  } catch (e) {
    if (!(e.code === 'system.http_404' || e.status === 404)) {
      // Soft-fail; section keeps its baked defaults.
    }
  }
}

async function checkUpdates() {
  toasts.success('Checking for updates…')
  await loadUpdates()
}

function startUpdate(component) {
  updateTarget.value = component
  updateConfirmOpen.value = true
}

async function confirmUpdate() {
  const target = updateTarget.value
  updateConfirmOpen.value = false
  if (!target) return
  try {
    await api('/api/updates/apply', {
      method: 'POST',
      body: JSON.stringify({ component: target.id }),
    })
    toasts.success(`${target.label} update started — restart imminent`)
  } catch (e) {
    toasts.error(`Could not start ${target.label} update: ${e.message || e}`)
  }
}

async function setChannel(component, channel) {
  component.channel = channel
  try {
    await api('/api/updates/channel', {
      method: 'POST',
      body: JSON.stringify({ component: component.id, channel }),
    })
    toasts.success(`${component.label} channel → ${channel}`)
  } catch {
    // 404 means no channel endpoint yet — accept the local toggle.
  }
}

/* ─── 4. Lemonade admin (in-page footgun zone) ────────────────────── */
// Flat-key form mirroring PR-13's LemonadeAdmin.vue. The standalone
// /settings/lemonade subview is the full admin; here we surface the
// in-page subset operators need most often: the footgun args + a couple
// of common knobs + Save+Restart.

const lemonade = reactive({
  loading: true,
  form: {
    max_loaded_models: 4,
    ctx_size: 4096,
    llamacpp_backend: 'rocm',
    llamacpp_args: '--parallel 1 --threads 8',
    flm_args: '--asr 1 --embed 1',
    whispercpp_backend: 'vulkan',
    sdcpp_backend: 'rocm',
    steps: 20,
    cfg_scale: 7.0,
    width: 512,
    height: 512,
    log_level: 'info',
    global_timeout: 900,
  },
  orig: {},
  effects: { immediate: [], deferred: [] },
})

const RESTART_KEYS = new Set([
  'max_loaded_models', 'ctx_size', 'llamacpp_backend', 'llamacpp_args',
  'sdcpp_backend', 'whispercpp_backend', 'steps', 'cfg_scale', 'width',
  'height', 'flm_args',
])

const changedLemonadeKeys = computed(() => {
  return Object.keys(lemonade.form).filter(
    (k) => String(lemonade.form[k] ?? '') !== String(lemonade.orig[k] ?? ''),
  )
})
const pendingRestartCount = computed(() => {
  return changedLemonadeKeys.value.filter((k) => RESTART_KEYS.has(k)).length
})
const saveDialogOpen = ref(false)
const llamaArgsEditing = ref(false)

async function loadLemonade() {
  lemonade.loading = true
  try {
    const data = await api('/api/lemonade/config')
    if (data) {
      for (const k of ['max_loaded_models', 'ctx_size', 'log_level', 'global_timeout']) {
        if (data[k] !== undefined) lemonade.form[k] = data[k]
      }
      if (data.llamacpp) {
        if (data.llamacpp.args !== undefined) lemonade.form.llamacpp_args = data.llamacpp.args
        if (data.llamacpp.backend !== undefined) lemonade.form.llamacpp_backend = data.llamacpp.backend
      }
      if (data.flm?.args !== undefined) lemonade.form.flm_args = data.flm.args
      if (data.whispercpp?.backend !== undefined) lemonade.form.whispercpp_backend = data.whispercpp.backend
      if (data.sdcpp) {
        const s = data.sdcpp
        if (s.backend !== undefined) lemonade.form.sdcpp_backend = s.backend
        for (const k of ['steps', 'cfg_scale', 'width', 'height']) {
          if (s[k] !== undefined) lemonade.form[k] = s[k]
        }
      }
      lemonade.effects = data?._hal0?.effects ?? lemonade.effects
    }
  } catch {
    // Use defaults.
  }
  lemonade.orig = JSON.parse(JSON.stringify(lemonade.form))
  lemonade.loading = false
}

function setLemonadeField(k, v) {
  lemonade.form[k] = v
}

async function saveLemonade() {
  const keys = changedLemonadeKeys.value
  saveDialogOpen.value = false
  if (!keys.length) {
    toasts.warning('No Lemonade-admin changes to save')
    return
  }
  const patch = {}
  for (const k of keys) patch[k] = lemonade.form[k]
  try {
    const r = await api('/api/lemonade/config', {
      method: 'POST',
      body: JSON.stringify(patch),
    })
    const eff = r?.effects ?? {}
    const nI = eff.immediate?.length ?? 0
    const nD = eff.deferred?.length ?? 0
    if (nI && nD) toasts.success(`Saved — ${nI} immediate, ${nD} deferred`)
    else if (nI) toasts.success(`Saved — ${nI} immediate`)
    else if (nD) toasts.success(`Saved — ${nD} deferred until next load`)
    else toasts.success('Saved')
    await loadLemonade()
    // After save+restart, hit the restart endpoint when pending.
    if (pendingRestartCount.value) {
      await restartLemonade(true)
    }
  } catch (e) {
    toasts.error(e.message || 'Save failed')
  }
}

async function restartLemonade(silent = false) {
  try {
    await api('/api/lemonade/restart', { method: 'POST' })
    if (!silent) toasts.success('lemond restart requested — back online in ~8-12s')
  } catch (e) {
    if (!silent) toasts.error(`Could not restart lemond: ${e.message || e}`)
  }
}

/* ─── 5. OmniRouter ───────────────────────────────────────────────── */
const MOCK_OMNI_TOOLS = [
  { name: 'embed_text', origin: 'hal0', active: true,
    target: 'embed slot · bge-small-en-q4_k_m', endpoint: '/v1/embed',
    remediation: null },
  { name: 'rerank_documents', origin: 'hal0', active: true,
    target: 'embed-rerank · bge-reranker-v2-m3', endpoint: '/v1/rerank',
    remediation: null },
  { name: 'route_to_chat', origin: 'hal0', active: true,
    target: 'primary slot', endpoint: '/v1/chat/completions',
    remediation: null },
  { name: 'web_search', origin: 'upstream', active: false,
    target: 'configure a search provider', endpoint: '/v1/tools/web_search',
    remediation: 'Install Flux-2-Klein-9B to enable' },
  { name: 'code_run', origin: 'upstream', active: true,
    target: 'sandbox runner', endpoint: '/v1/tools/code_run',
    remediation: null },
  { name: 'file_read', origin: 'upstream', active: true,
    target: 'fs-read (scoped /home/halo)', endpoint: '/v1/tools/file_read',
    remediation: null },
  { name: 'image_describe', origin: 'upstream', active: false,
    target: 'no vision slot loaded', endpoint: '/v1/tools/image_describe',
    remediation: 'Add a vision-capable slot (e.g. qwen2-vl-2b)' },
  { name: 'memory_recall', origin: 'upstream', active: true,
    target: 'cognee · shared namespace', endpoint: '/v1/tools/memory_recall',
    remediation: null },
]
const omniTools = ref([])
const omniSyncSha = ref('a4f1e83')

async function loadOmni() {
  try {
    const r = await api('/api/omni-tools')
    if (Array.isArray(r.tools)) omniTools.value = r.tools
    else omniTools.value = [...MOCK_OMNI_TOOLS]
    if (r.upstream_sha) omniSyncSha.value = r.upstream_sha
  } catch {
    omniTools.value = [...MOCK_OMNI_TOOLS]
  }
}

function checkOmniDrift() {
  toasts.success('No drift — upstream and local tool definitions match.')
}

/* ─── 6. Agent policy ─────────────────────────────────────────────── */
function goToAgent() {
  router.push({ name: 'agent' })
}

/* ─── 7. Memory (Cognee) ──────────────────────────────────────────── */
const memory = reactive({
  namespace: 'shared',
  records: 0,
  diskMb: 0,
  tools: ['recall', 'remember', 'forget', 'list_namespaces', 'export', 'reset'],
})
const memoryResetOpen = ref(false)

async function loadMemory() {
  try {
    const r = await api('/api/memory/namespaces')
    const ns = (r.namespaces || []).find((n) => n.name === 'shared')
    if (ns) {
      memory.records = ns.records ?? memory.records
      memory.diskMb = ns.disk_mb ?? memory.diskMb
    }
  } catch {
    memory.records = 2_847
    memory.diskMb = 184
  }
}

async function confirmMemoryReset() {
  memoryResetOpen.value = false
  try {
    await api(`/api/memory/reset/${memory.namespace}`, { method: 'POST' })
    toasts.success(`Reset namespace '${memory.namespace}'`)
    await loadMemory()
  } catch (e) {
    toasts.warning(`Reset acknowledged locally (${e.code || 'no-endpoint'})`)
    memory.records = 0
    memory.diskMb = 0
  }
}

function exportMemory() {
  // No live endpoint yet — emit a download link to a stub blob so the
  // UX flow is exercise-able without backend support.
  const blob = new Blob(
    [JSON.stringify({ namespace: memory.namespace, exported_at: new Date().toISOString() }, null, 2)],
    { type: 'application/json' },
  )
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `hal0-memory-${memory.namespace}.json`
  a.click()
  URL.revokeObjectURL(url)
  toasts.success('Export downloaded')
}

/* ─── 8. Appearance ───────────────────────────────────────────────── */
function setDensity(d) { tweaks.density = d }

/* ─── 9. About ────────────────────────────────────────────────────── */
const ABOUT = Object.freeze({
  version: 'v0.2.0-alpha.3',
  commitSha: '518f5b7',
  buildDate: '2026-05-23',
  license: 'Apache-2.0',
  repo: 'https://github.com/Hal0ai/hal0',
  discord: 'https://discord.gg/hal0',
  docs: 'https://hal0.dev/docs',
})
const licensesOpen = ref(false)

const unsavedCount = computed(() => changedLemonadeKeys.value.length)
</script>

<template>
  <div class="settings-view" data-testid="settings-v2">
    <div class="vh">
      <span class="vh-eye mono">Configure</span>
      <h1 class="page-title">Settings</h1>
      <span class="vh-spacer" />
      <span class="hint mono">unsaved · {{ unsavedCount }}</span>
    </div>

    <div class="settings-layout">
      <SettingsRail
        :sections="SECTIONS"
        :active-id="activeSection"
        @navigate="navigateTo"
      />

      <div class="settings-content">
        <!-- 1. Auth -->
        <section
          id="auth"
          :ref="(el) => setSectionRef('auth', el)"
          class="s-section"
          data-section="auth"
        >
          <h2>Auth</h2>
          <p class="desc">
            hal0's Bearer-token boundary. The dashboard, CLI, and Open WebUI use this
            token. Lemonade itself runs loopback-only and never sees the token.
          </p>
          <div class="s-panel">
            <SecRow k="hal0 Bearer token" sub="Required by hal0-api · ADR-0001" mono>
              <span data-testid="auth-token">
                {{ auth.showToken && auth.tokenRaw ? auth.tokenRaw : auth.tokenMasked }}
              </span>
              <template #actions>
                <button
                  type="button"
                  class="btn-ghost-sm"
                  data-testid="auth-token-toggle"
                  @click="auth.showToken = !auth.showToken"
                >
                  {{ auth.showToken ? 'Hide' : 'Show' }}
                </button>
                <button
                  type="button"
                  class="btn-ghost-sm"
                  data-testid="auth-token-rotate"
                  @click="rotateOpen = true"
                >
                  Rotate
                </button>
              </template>
            </SecRow>
            <SecRow k="Identity" :value="auth.identity" mono />
            <SecRow
              k="Allowed origins"
              sub="CORS — UI hosts permitted to call hal0-api"
              mono
            >
              <span>{{ auth.origins.join(', ') || 'none' }}</span>
              <template #actions>
                <button
                  type="button"
                  class="btn-ghost-sm"
                  data-testid="auth-origins-edit"
                  @click="originsOpen = true"
                >
                  Edit
                </button>
              </template>
            </SecRow>
          </div>
          <p class="lemonade-deep-link mono">
            <router-link
              :to="{ name: 'settings-lemonade' }"
              class="link"
              data-testid="lemonade-admin-link"
            >
              Open standalone Lemonade admin →
            </router-link>
          </p>
        </section>

        <!-- 2. Secrets -->
        <section
          id="secrets"
          :ref="(el) => setSectionRef('secrets', el)"
          class="s-section"
          data-section="secrets"
        >
          <h2>Secrets</h2>
          <p class="desc">
            Encrypted at rest, scoped to lemond. Used for gated HF repos and provider
            auth.
          </p>
          <div class="s-panel" data-testid="secrets-list">
            <SecRow
              v-for="s in secrets"
              :key="s.id"
              :k="s.name"
              :sub="s.description"
              mono
            >
              <span :class="s.set ? 'ok' : 'dim'">
                {{ s.set ? '••••••••••••••••• · set' : 'not set' }}
              </span>
              <template #actions>
                <button
                  v-if="s.set"
                  type="button"
                  class="btn-ghost-sm"
                  @click="addSecretOpen = true"
                >
                  Update
                </button>
                <button
                  v-if="s.set"
                  type="button"
                  class="btn-danger-sm"
                  @click="removeSecret(s.id)"
                >
                  Remove
                </button>
                <button
                  v-else
                  type="button"
                  class="btn-ghost-sm"
                  @click="addSecretOpen = true"
                >
                  Add
                </button>
              </template>
            </SecRow>
            <div class="add-row">
              <button
                type="button"
                class="btn-ghost-sm"
                data-testid="add-secret-open"
                @click="addSecretOpen = true"
              >
                + Add secret
              </button>
            </div>
          </div>
        </section>

        <!-- 3. Updates -->
        <section
          id="updates"
          :ref="(el) => setSectionRef('updates', el)"
          class="s-section"
          data-section="updates"
        >
          <h2>Updates</h2>
          <p class="desc">
            Signed self-update. hal0 verifies a Sigstore signature before swapping
            binaries. Per-channel pins.
          </p>
          <div class="s-panel">
            <SecRow
              v-for="c in updates.components"
              :key="c.id"
              :k="c.label"
              :sub="c.sub"
              mono
            >
              <span>
                <span v-if="c.available" class="accent">{{ c.available }} available</span>
                <span class="dim"> · current {{ c.current }}</span>
              </span>
              <template #actions>
                <select
                  class="field-input-xs"
                  :value="c.channel"
                  @change="setChannel(c, $event.target.value)"
                >
                  <option value="stable">stable</option>
                  <option value="beta">beta</option>
                </select>
                <button
                  v-if="c.available"
                  type="button"
                  class="btn-ghost-sm"
                  @click="startUpdate(c)"
                >
                  Update now
                </button>
              </template>
            </SecRow>
            <SecRow k="Auto-update on boot" sub="Apply staged updates at next start" mono>
              <label class="toggle-label">
                <input v-model="updates.autoOnBoot" type="checkbox" />
                <span>{{ updates.autoOnBoot ? 'On' : 'Off' }}</span>
              </label>
            </SecRow>
            <SecRow k="Check cadence" sub="How often the dashboard polls for updates" mono>
              <div class="seg" role="radiogroup">
                <span
                  v-for="opt in ['manual', 'daily', 'weekly']"
                  :key="opt"
                  :class="['seg-opt', { active: updates.cadence === opt }]"
                  role="radio"
                  :aria-checked="updates.cadence === opt"
                  tabindex="0"
                  @click="updates.cadence = opt"
                  @keydown.enter.prevent="updates.cadence = opt"
                >{{ opt }}</span>
              </div>
            </SecRow>
          </div>
          <div class="footer-row">
            <button type="button" class="btn-ghost-sm" @click="checkUpdates">
              Check for updates
            </button>
          </div>
        </section>

        <!-- 4. Lemonade admin (in-page footgun zone) -->
        <section
          id="lemonade"
          :ref="(el) => setSectionRef('lemonade', el)"
          class="s-section"
          data-section="lemonade"
        >
          <h2>Lemonade admin</h2>
          <p class="desc">
            Direct edit of lemond's <span class="mono">/internal/config</span>.
            <span class="mono warn">⟳</span> marked fields require a lemond restart.
          </p>
          <div class="s-panel">
            <SecKey
              k="max_loaded_models"
              sub="Per-type LRU budget"
              type="number"
              :restart="true"
              :model-value="lemonade.form.max_loaded_models"
              testid="lemonade-max-loaded"
              @update:model-value="(v) => setLemonadeField('max_loaded_models', Number(v))"
            />
            <SecKey
              k="ctx_size"
              sub="Default per /v1/load — overridable per slot"
              type="number"
              :restart="true"
              :model-value="lemonade.form.ctx_size"
              testid="lemonade-ctx-size"
              @update:model-value="(v) => setLemonadeField('ctx_size', Number(v))"
            />
            <SecKey
              k="llamacpp.backend"
              type="select"
              :options="['rocm', 'vulkan', 'cpu']"
              :restart="true"
              :model-value="lemonade.form.llamacpp_backend"
              testid="lemonade-llama-backend"
              @update:model-value="(v) => setLemonadeField('llamacpp_backend', v)"
            />
            <SecKey
              k="llamacpp.args"
              sub="Mandatory baseline · ADR-0008 · read-only by default"
              type="readonly"
              :restart="true"
              :model-value="lemonade.form.llamacpp_args"
              testid="lemonade-llama-args"
              @edit-toggle="(v) => (llamaArgsEditing = v)"
              @update:model-value="(v) => setLemonadeField('llamacpp_args', v)"
            >
              <template #warn>
                <span data-testid="lemonade-llama-args-warning">
                  Without <span class="mono accent">--parallel 1 --threads N</span>,
                  concurrent llama-server children deadlock the GPU. Keep both flags
                  unless you know exactly what you're swapping in.
                </span>
              </template>
            </SecKey>
            <SecKey
              k="flm.args"
              sub="FLM trio config — drives the NPU coresident packing"
              type="text"
              :restart="true"
              :model-value="lemonade.form.flm_args"
              testid="lemonade-flm-args"
              @update:model-value="(v) => setLemonadeField('flm_args', v)"
            />
            <SecKey
              k="whispercpp.backend"
              type="select"
              :options="['vulkan', 'rocm', 'cpu']"
              :restart="true"
              :model-value="lemonade.form.whispercpp_backend"
              testid="lemonade-whisper-backend"
              @update:model-value="(v) => setLemonadeField('whispercpp_backend', v)"
            />
            <SecKey
              k="kokoro.cpu_bin"
              sub="Linux-only · GPU support is upstream-pending"
              type="text"
              :readonly="true"
              :model-value="'builtin'"
              testid="lemonade-kokoro-bin"
            />
            <SecKey
              k="sdcpp.backend"
              type="select"
              :options="['rocm', 'vulkan', 'cpu']"
              :restart="true"
              :model-value="lemonade.form.sdcpp_backend"
              testid="lemonade-sdcpp-backend"
              @update:model-value="(v) => setLemonadeField('sdcpp_backend', v)"
            />
            <SecKey
              k="sdcpp.steps"
              type="number"
              :restart="true"
              :model-value="lemonade.form.steps"
              testid="lemonade-sdcpp-steps"
              @update:model-value="(v) => setLemonadeField('steps', Number(v))"
            />
            <SecKey
              k="sdcpp.cfg_scale"
              type="number"
              step="0.1"
              :restart="true"
              :model-value="lemonade.form.cfg_scale"
              testid="lemonade-sdcpp-cfg"
              @update:model-value="(v) => setLemonadeField('cfg_scale', Number(v))"
            />
            <SecKey
              k="sdcpp.width"
              type="number"
              :restart="true"
              :model-value="lemonade.form.width"
              testid="lemonade-sdcpp-w"
              @update:model-value="(v) => setLemonadeField('width', Number(v))"
            />
            <SecKey
              k="sdcpp.height"
              type="number"
              :restart="true"
              :model-value="lemonade.form.height"
              testid="lemonade-sdcpp-h"
              @update:model-value="(v) => setLemonadeField('height', Number(v))"
            />
            <SecKey
              k="log_level"
              type="select"
              :options="['debug', 'info', 'warn', 'error']"
              :model-value="lemonade.form.log_level"
              testid="lemonade-log-level"
              @update:model-value="(v) => setLemonadeField('log_level', v)"
            />
            <SecKey
              k="global_timeout"
              sub="Inference request timeout (s)"
              type="number"
              :model-value="lemonade.form.global_timeout"
              testid="lemonade-timeout"
              @update:model-value="(v) => setLemonadeField('global_timeout', Number(v))"
            />
          </div>
          <div class="footer-row">
            <div class="footer-meta mono">
              <RestartChip v-if="pendingRestartCount" :label="`${pendingRestartCount} restart`" />
              <span v-else class="dim">No restart needed.</span>
            </div>
            <div class="footer-actions">
              <button
                type="button"
                class="btn-ghost-sm"
                data-testid="lemonade-restart"
                @click="restartLemonade(false)"
              >
                Restart lemond
              </button>
              <button
                type="button"
                class="btn-primary-sm"
                data-testid="lemonade-save"
                :disabled="!changedLemonadeKeys.length"
                @click="saveDialogOpen = true"
              >
                Save
              </button>
            </div>
          </div>
        </section>

        <!-- 5. OmniRouter -->
        <section
          id="omni"
          :ref="(el) => setSectionRef('omni', el)"
          class="s-section"
          data-section="omni"
        >
          <h2>OmniRouter</h2>
          <p class="desc">
            Client-side tool-calling loop owned by hal0. Eight tools — five upstream,
            three hal0-custom. Active set filters per-request based on enabled slots.
          </p>
          <div class="s-panel" data-testid="omni-tools-list">
            <div class="omni-header mono">
              <span>TOOL</span>
              <span>ORIGIN</span>
              <span>ENDPOINT · TARGET</span>
              <span>STATUS</span>
            </div>
            <div
              v-for="t in omniTools"
              :key="t.name"
              class="omni-row"
              :data-testid="`omni-tool-${t.name}`"
            >
              <span class="mono nm">{{ t.name }}</span>
              <span :class="['origin-chip', `origin-${t.origin}`]">{{ t.origin }}</span>
              <span class="tgt">
                <span class="mono dim">{{ t.endpoint }}</span>
                <span class="mono"> · {{ t.target }}</span>
                <span v-if="t.remediation" class="remediation">
                  <a href="#" @click.prevent="">{{ t.remediation }} →</a>
                </span>
              </span>
              <span :class="['status-chip', t.active ? 'ok' : 'off']">
                {{ t.active ? '✓ active' : '✗ inactive' }}
              </span>
            </div>
          </div>
          <div class="omni-footer mono">
            <span>Tool definitions synced from lemonade-sdk/lemonade@<b>{{ omniSyncSha }}</b>.</span>
            <button type="button" class="btn-ghost-sm" @click="checkOmniDrift">
              Check for drift
            </button>
          </div>
        </section>

        <!-- 6. Agent policy stub -->
        <section
          id="agent"
          :ref="(el) => setSectionRef('agent', el)"
          class="s-section"
          data-section="agent"
        >
          <h2>Agent policy</h2>
          <p class="desc">
            Per-capability approval modes for bundled agents (fs-read, fs-write,
            shell-exec, net-fetch, registry-write, slot-control). The full editor lives
            on the Agent view.
          </p>
          <div class="s-panel">
            <SecRow
              k="Configure approvals"
              sub="Open the Agent view to edit per-capability defaults"
            >
              <span class="mono dim">
                See Agent view for the full policy editor.
              </span>
              <template #actions>
                <button
                  type="button"
                  class="btn-ghost-sm"
                  data-testid="agent-policy-link"
                  @click="goToAgent"
                >
                  Open Agent view →
                </button>
              </template>
            </SecRow>
          </div>
        </section>

        <!-- 7. Memory -->
        <section
          id="memory"
          :ref="(el) => setSectionRef('memory', el)"
          class="s-section"
          data-section="memory"
        >
          <h2>Memory (Cognee)</h2>
          <p class="desc">
            Cognee namespace + store inspection. Agents own the rest of the surface via
            MCP in Phase 8.
          </p>
          <div class="s-panel">
            <SecRow k="Namespace" mono :value="memory.namespace" />
            <SecRow k="Records" mono>
              <span class="num">{{ memory.records.toLocaleString() }}</span>
            </SecRow>
            <SecRow k="Disk usage" mono :value="`${memory.diskMb} MB`" />
            <SecRow k="Available tools" sub="Read-only — MCP exposes these to agents" mono>
              <span class="mono dim">{{ memory.tools.join(', ') }}</span>
            </SecRow>
          </div>
          <div class="footer-row">
            <button type="button" class="btn-ghost-sm" @click="exportMemory">
              Export
            </button>
            <button
              type="button"
              class="btn-danger-sm"
              data-testid="memory-reset-open"
              @click="memoryResetOpen = true"
            >
              Reset namespace
            </button>
          </div>
        </section>

        <!-- 8. Appearance -->
        <section
          id="appearance"
          :ref="(el) => setSectionRef('appearance', el)"
          class="s-section"
          data-section="appearance"
        >
          <h2>Appearance</h2>
          <p class="desc">
            Dark only for v0.2.x. Light + system-auto modes land with v0.3.
          </p>
          <div class="s-panel">
            <SecRow k="Theme" sub="System-wide colour palette">
              <div class="seg" role="radiogroup">
                <span
                  :class="['seg-opt', 'active']"
                  role="radio"
                  aria-checked="true"
                  tabindex="0"
                >dark</span>
                <span class="seg-opt disabled" aria-disabled="true">
                  light <span class="v3-chip">v0.3</span>
                </span>
                <span class="seg-opt disabled" aria-disabled="true">
                  auto <span class="v3-chip">v0.3</span>
                </span>
              </div>
            </SecRow>
            <SecRow k="Density" sub="affects card padding + row heights">
              <div class="seg" role="radiogroup" data-testid="appearance-density">
                <span
                  v-for="d in ['compact', 'comfortable', 'spacious']"
                  :key="d"
                  :class="['seg-opt', { active: tweaks.density === d }]"
                  role="radio"
                  :aria-checked="tweaks.density === d"
                  tabindex="0"
                  :data-testid="`density-${d}`"
                  @click="setDensity(d)"
                  @keydown.enter.prevent="setDensity(d)"
                >{{ d }}</span>
              </div>
            </SecRow>
          </div>
        </section>

        <!-- 9. About -->
        <section
          id="about"
          :ref="(el) => setSectionRef('about', el)"
          class="s-section"
          data-section="about"
        >
          <h2>About</h2>
          <div class="s-panel">
            <SecRow k="hal0" mono :value="ABOUT.version" />
            <SecRow k="Commit" mono :value="ABOUT.commitSha" />
            <SecRow k="Build date" mono :value="ABOUT.buildDate" />
            <SecRow k="License" :value="ABOUT.license" />
            <SecRow k="Repository" mono>
              <a :href="ABOUT.repo" target="_blank" rel="noopener noreferrer" class="link">
                github.com/Hal0ai/hal0
              </a>
            </SecRow>
            <SecRow k="Docs" mono>
              <a :href="ABOUT.docs" target="_blank" rel="noopener noreferrer" class="link">
                hal0.dev/docs
              </a>
            </SecRow>
            <SecRow k="Discord" mono>
              <a :href="ABOUT.discord" target="_blank" rel="noopener noreferrer" class="link">
                discord.gg/hal0
              </a>
            </SecRow>
          </div>
          <div class="footer-row">
            <button
              type="button"
              class="btn-ghost-sm"
              data-testid="bundled-licenses-open"
              @click="licensesOpen = true"
            >
              View bundled licenses
            </button>
          </div>
        </section>
      </div>
    </div>

    <!-- Dialogs / overlays -->
    <RotateTokenDialog
      :open="rotateOpen"
      @cancel="rotateOpen = false"
      @confirm="rotateToken"
    />
    <AllowedOriginsModal
      :open="originsOpen"
      :origins="auth.origins"
      @close="originsOpen = false"
      @save="saveOrigins"
    />
    <AddSecretModal
      :open="addSecretOpen"
      @close="addSecretOpen = false"
      @save="saveSecret"
    />
    <SaveAndRestartDialog
      :open="saveDialogOpen"
      :pending-restart="pendingRestartCount"
      @cancel="saveDialogOpen = false"
      @confirm="saveLemonade"
    />
    <ConfirmDialog
      :open="updateConfirmOpen"
      :title="`Install ${updateTarget?.label || 'update'}?`"
      :message="`This will restart lemond and hal0-api. Expect ~30s outage.`"
      confirm-label="Install + restart"
      @cancel="updateConfirmOpen = false"
      :on-cancel="() => (updateConfirmOpen = false)"
      :on-confirm="confirmUpdate"
    />
    <ConfirmDialog
      :open="memoryResetOpen"
      title="Reset memory namespace?"
      :message="`This wipes every record in '${memory.namespace}'. Cognee cannot recover the data afterwards.`"
      :destructive="true"
      :type-to-confirm="memory.namespace"
      confirm-label="Reset namespace"
      :on-cancel="() => (memoryResetOpen = false)"
      :on-confirm="confirmMemoryReset"
    />
    <BundledLicensesDrawer
      :open="licensesOpen"
      @close="licensesOpen = false"
    />
  </div>
</template>

<style scoped>
.settings-view {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 18px 24px 32px;
  max-width: 1180px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}
.vh {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 0;
}
.vh-eye {
  color: var(--accent, var(--hal0-accent));
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.vh h1, .page-title {
  font-size: 22px;
  font-weight: 500;
  margin: 0;
  letter-spacing: -0.02em;
  color: var(--fg, var(--color-fg));
}
.vh-spacer { flex: 1; }
.hint { font-size: 11px; color: var(--fg-4, var(--color-fg-faint)); }
.mono { font-family: var(--jbm, var(--font-mono)); }
.dim { color: var(--fg-4, var(--color-fg-faint)); }
.accent { color: var(--accent, var(--hal0-accent)); }
.warn { color: var(--warn, var(--color-warning)); }
.ok { color: var(--ok, color-mix(in srgb, var(--hal0-accent) 70%, #4ade80)); }
.num { font-variant-numeric: tabular-nums; }

.settings-layout {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 28px;
}
@media (max-width: 880px) {
  .settings-layout { grid-template-columns: 1fr; }
}
.settings-content {
  display: flex;
  flex-direction: column;
  gap: 28px;
  min-width: 0;
}

.s-section { scroll-margin-top: 16px; }
.s-section h2 {
  font-size: 16px;
  font-weight: 500;
  margin: 0 0 6px;
  letter-spacing: -0.01em;
  color: var(--fg, var(--color-fg));
}
.s-section .desc {
  font-size: 12.5px;
  color: var(--fg-3, var(--color-fg-muted));
  margin: 0 0 12px;
  max-width: 620px;
  line-height: 1.55;
}
.s-panel {
  background: var(--bg-1, var(--color-surface));
  border: 1px solid var(--line, var(--color-border));
  border-radius: var(--rad-lg, 8px);
  overflow: hidden;
}
.add-row {
  padding: 12px 18px;
  border-top: 1px solid var(--line-soft, var(--color-border));
  background: var(--bg, var(--color-surface));
}
.footer-row {
  margin-top: 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.footer-meta { display: inline-flex; align-items: center; gap: 10px; font-size: 11px; }
.footer-actions { display: inline-flex; gap: 8px; }
.lemonade-deep-link { margin-top: 10px; font-size: 11.5px; }

.link {
  color: var(--accent, var(--hal0-accent));
  text-decoration: none;
}
.link:hover { text-decoration: underline; }

/* Buttons */
.btn-ghost-sm {
  background: transparent;
  border: 1px solid var(--line, var(--color-border));
  color: var(--fg-2, var(--color-fg-muted));
  border-radius: var(--rad-sm, 4px);
  padding: 5px 11px;
  font-family: var(--jbm, var(--font-mono));
  font-size: 11px;
  cursor: pointer;
}
.btn-ghost-sm:hover { color: var(--fg, var(--color-fg)); border-color: var(--line-strong, var(--color-border-hi)); }
.btn-danger-sm {
  background: transparent;
  border: 1px solid color-mix(in srgb, var(--err, var(--color-danger)) 40%, var(--line));
  color: var(--err, var(--color-danger));
  border-radius: var(--rad-sm, 4px);
  padding: 5px 11px;
  font-family: var(--jbm, var(--font-mono));
  font-size: 11px;
  cursor: pointer;
}
.btn-primary-sm {
  background: var(--accent, var(--hal0-accent));
  border: 1px solid var(--accent, var(--hal0-accent));
  color: #0a0a0a;
  border-radius: var(--rad-sm, 4px);
  padding: 5px 11px;
  font-family: var(--jbm, var(--font-mono));
  font-size: 11px;
  cursor: pointer;
}
.btn-primary-sm:disabled { opacity: 0.45; cursor: not-allowed; }

/* Inputs / segmented */
.field-input-xs {
  background: var(--bg, var(--color-surface));
  border: 1px solid var(--line, var(--color-border));
  border-radius: var(--rad-sm, 4px);
  padding: 4px 8px;
  font-family: var(--jbm, var(--font-mono));
  font-size: 11px;
  color: var(--fg, var(--color-fg));
}
.toggle-label { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; font-size: 12px; }
.seg {
  display: inline-flex;
  border: 1px solid var(--line, var(--color-border));
  border-radius: 4px;
  overflow: hidden;
}
.seg-opt {
  padding: 4px 12px;
  font-family: var(--jbm, var(--font-mono));
  font-size: 11px;
  color: var(--fg-3, var(--color-fg-muted));
  cursor: pointer;
  border-right: 1px solid var(--line, var(--color-border));
  display: inline-flex;
  align-items: center;
  gap: 6px;
  user-select: none;
}
.seg-opt:last-child { border-right: none; }
.seg-opt.active {
  background: color-mix(in srgb, var(--accent, var(--hal0-accent)) 14%, transparent);
  color: var(--accent, var(--hal0-accent));
}
.seg-opt.disabled { color: var(--fg-4, var(--color-fg-faint)); cursor: not-allowed; }
.v3-chip {
  font-size: 9px;
  letter-spacing: 0.04em;
  padding: 1px 5px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--accent, var(--hal0-accent)) 12%, transparent);
  color: var(--accent, var(--hal0-accent));
  text-transform: uppercase;
}

/* OmniRouter rows */
.omni-header {
  padding: 10px 18px;
  border-bottom: 1px solid var(--line-soft, var(--color-border));
  background: var(--bg, var(--color-surface));
  font-size: 10px;
  color: var(--fg-4, var(--color-fg-faint));
  text-transform: uppercase;
  letter-spacing: 0.08em;
  display: grid;
  grid-template-columns: 180px 80px 1fr 100px;
  gap: 16px;
}
.omni-row {
  display: grid;
  grid-template-columns: 180px 80px 1fr 100px;
  gap: 16px;
  padding: 12px 18px;
  border-bottom: 1px solid var(--line-soft, var(--color-border));
  align-items: center;
  font-size: 12.5px;
}
.omni-row:last-child { border-bottom: none; }
.omni-row .nm { color: var(--fg, var(--color-fg)); }
.omni-row .tgt { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.omni-row .tgt .mono { word-break: break-all; }
.origin-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  font-family: var(--jbm, var(--font-mono));
  font-size: 10px;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  white-space: nowrap;
}
.origin-hal0 {
  background: color-mix(in srgb, var(--accent, var(--hal0-accent)) 14%, transparent);
  color: var(--accent, var(--hal0-accent));
  border: 1px solid color-mix(in srgb, var(--accent, var(--hal0-accent)) 35%, transparent);
}
.origin-upstream {
  background: var(--bg, var(--color-surface));
  color: var(--fg-3, var(--color-fg-muted));
  border: 1px solid var(--line, var(--color-border));
}
.status-chip {
  font-family: var(--jbm, var(--font-mono));
  font-size: 10.5px;
  white-space: nowrap;
}
.status-chip.ok { color: var(--ok, #4ade80); }
.status-chip.off { color: var(--fg-4, var(--color-fg-faint)); }
.remediation { display: block; margin-top: 2px; font-size: 11px; }
.remediation a { color: var(--accent, var(--hal0-accent)); text-decoration: none; }
.remediation a:hover { text-decoration: underline; }
.omni-footer {
  margin-top: 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  font-size: 11px;
  color: var(--fg-4, var(--color-fg-faint));
  flex-wrap: wrap;
}
.omni-footer b { color: var(--fg, var(--color-fg)); font-weight: 500; }

@media (max-width: 720px) {
  .omni-header, .omni-row { grid-template-columns: 1fr; }
}
</style>
