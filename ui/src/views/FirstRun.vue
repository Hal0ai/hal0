<script setup>
/**
 * FirstRun.vue — First-run wizard backed by the model-pull endpoints.
 *
 * 5 steps (plus a "done" coda) post-ADR-0001 Child B:
 *   1. Set up password (optional, ADR-0001 Child B). Renders only when
 *      /api/auth/status.password_set === false; auto-skips otherwise.
 *      Skip button advances without setting a password (open posture).
 *   2. Pick a model (curated cards + custom HF form).
 *   3. License confirm (checkbox required).
 *   4. Download + assign (POST /api/install/pick-default; SSE-tail the
 *      pull stream for live progress; on completion show "Open chat").
 *   5. Done coda.
 *
 * The router-level guard (router.js) only redirects to /firstrun when
 * /api/install/state.first_run === true; this view does not re-check —
 * the user might have multiple tabs open and we don't want to bounce
 * them around on a slow API.
 */
import { ref, computed, onUnmounted, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useToastsStore } from '../stores/toasts.js'
import { useSystemStore } from '../stores/system.js'
import { api } from '../composables/useApi.js'
import { resetFirstRunGuard } from '../router.js'

const router = useRouter()
const toasts = useToastsStore()
const system = useSystemStore()

// Step numbers map: 1 = password (optional), 2 = picker, 3 = license,
// 4 = install, 5 = done. Step 1 self-skips when a password is already
// set so re-entering the wizard after first install doesn't nag for a
// password rotation.
const step = ref(1)              // 1 | 2 | 3 | 4 | 5 (done)
const curated = ref([])          // /api/install/curated-models result
const customAllowed = ref(false)
const loadingCatalogue = ref(true)
const catalogueError = ref(null)

// Password step state. ``passwordSetServerSide`` reflects the latest
// /api/auth/status.password_set value; when true on mount we skip step 1.
const passwordSetServerSide = ref(false)
const passwordInput = ref('')
const passwordSubmitting = ref(false)
const passwordError = ref('')
const PASSWORD_MIN_LEN = 8  // mirrors hal0.api.routes.auth._MIN_PASSWORD_LEN

// Picker state
const selectedModel = ref(null)  // curated entry OR { id: 'custom', ... }
const customRepo = ref('')
const customFile = ref('')
const customName = ref('')
const customErr  = ref('')
const showCustom = ref(false)

// License step
const licenseAccepted = ref(false)

// Download step
const pullJob       = ref(null)    // last snapshot from SSE/status
const sse           = ref(null)    // EventSource handle (so onUnmounted closes)
const downloadStart = ref(0)       // monotonic for ETA
const chatUrl       = ref(null)

// ── Catalogue + auth-status fetch on mount ───────────────────────────────────
onMounted(async () => {
  // Probe auth status first so the password step can auto-skip when a
  // password is already set (re-running the wizard, or upgrading from
  // an install where the operator set one through Settings). Failure
  // here is tolerable — we default to "ask" rather than silently
  // skipping a security step.
  try {
    const s = await api('/api/auth/status')
    passwordSetServerSide.value = !!s?.password_set
    if (passwordSetServerSide.value) {
      step.value = 2
    }
  } catch (e) {
    // Status probe failed — best to leave the password step visible so
    // the operator can still try to set one. The /password endpoint
    // will surface a real error if it's broken.
    console.warn('auth/status probe failed; showing password step anyway', e)
  }

  try {
    const r = await api('/api/install/curated-models')
    curated.value = r?.models ?? []
    customAllowed.value = !!r?.custom_allowed
  } catch (e) {
    catalogueError.value = e.message
    toasts.error(`Could not load curated catalogue: ${e.message}`)
  } finally {
    loadingCatalogue.value = false
  }
})

onUnmounted(() => {
  if (sse.value) {
    sse.value.close()
    sse.value = null
  }
})

// ── Helpers ─────────────────────────────────────────────────────────────────
function fmtSize(bytes) {
  if (!bytes) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let n = bytes
  let i = 0
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(n >= 10 ? 0 : 1)} ${u[i]}`
}

function fmtSizeGb(gb) {
  return `${gb.toFixed(1)} GB`
}

function fmtEta(seconds) {
  if (!isFinite(seconds) || seconds < 0) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${(seconds / 3600).toFixed(1)}h`
}

// ── Step transitions ────────────────────────────────────────────────────────
function selectCurated(m) {
  selectedModel.value = { kind: 'curated', ...m }
  showCustom.value = false
  customErr.value = ''
}

function selectCustom() {
  selectedModel.value = null
  showCustom.value = true
  customErr.value = ''
}

// ── Step 1 — Password (optional, ADR-0001 Child B) ─────────────────────────
async function submitPassword() {
  passwordError.value = ''
  if (passwordInput.value.length < PASSWORD_MIN_LEN) {
    passwordError.value = `Password must be at least ${PASSWORD_MIN_LEN} characters.`
    return
  }
  passwordSubmitting.value = true
  try {
    // First-run claim: /api/auth/password is public when no password is
    // set, so no Authorization header is needed. The middleware enforces
    // the writer-scope requirement once a password exists, which is what
    // makes the second call (rotation) require an authenticated session.
    await api('/api/auth/password', {
      method: 'POST',
      body: JSON.stringify({ password: passwordInput.value }),
    })
    passwordSetServerSide.value = true
    toasts.info('Password set — you can change it later in Settings.')
    // Clear the input rather than keep a copy in the Vue ref.
    passwordInput.value = ''
    step.value = 2
  } catch (e) {
    passwordError.value = e?.message || 'Could not set password.'
    toasts.error(passwordError.value)
  } finally {
    passwordSubmitting.value = false
  }
}

function skipPassword() {
  // Skipping leaves the install in the "open" posture — explicit choice
  // by the operator. The wizard reminds them they can set one later
  // from Settings → Authentication.
  passwordInput.value = ''
  passwordError.value = ''
  step.value = 2
}

// ── Step 2 / 3 transitions (picker → license) ──────────────────────────────
function goStep3() {
  if (showCustom.value) {
    if (!customRepo.value.trim()) {
      customErr.value = 'Repo is required (e.g. org/Model-GGUF)'
      return
    }
    if (!customFile.value.trim()) {
      customErr.value = 'File is required (e.g. model-q4_k_m.gguf)'
      return
    }
    if (!customName.value.trim()) {
      customErr.value = 'Display name is required'
      return
    }
    const id = (customName.value.trim().toLowerCase().replace(/[^a-z0-9.-]+/g, '-')) || 'custom-model'
    selectedModel.value = {
      kind: 'custom',
      id,
      display_name: customName.value.trim(),
      hf_repo: customRepo.value.trim(),
      hf_file: customFile.value.trim(),
      license: 'See repo page',
      license_url: `https://huggingface.co/${customRepo.value.trim()}`,
    }
  } else if (!selectedModel.value) {
    toasts.warning('Please select a model first')
    return
  }
  step.value = 3
}

function backToPicker() {
  step.value = 2
  licenseAccepted.value = false
}

// ── Step 4 — start the pull ─────────────────────────────────────────────────
async function startDownload() {
  if (!licenseAccepted.value) {
    toasts.warning('Please accept the license first')
    return
  }
  step.value = 4
  downloadStart.value = performance.now()
  pullJob.value = null

  try {
    const modelId = selectedModel.value.id
    if (selectedModel.value.kind === 'custom') {
      // Register first so the registry knows the HF coordinates, then
      // pull (the curated pick-default endpoint is curated-only).
      await api('/api/models', {
        method: 'POST',
        body: JSON.stringify({
          id: modelId,
          name: selectedModel.value.display_name,
          path: `/var/lib/hal0/models/${modelId}/${selectedModel.value.hf_file}`,
          hf_repo: selectedModel.value.hf_repo,
          hf_filename: selectedModel.value.hf_file,
          license: 'unknown',
          capabilities: ['chat'],
        }),
      })
      await api(`/api/models/${modelId}/pull`, { method: 'POST' })
      // Best-effort: assign to the primary slot. Tolerates a missing
      // slot TOML on a totally fresh install — the user can wire the
      // slot up later from the Slots view.
      await api('/api/slots/primary/config', {
        method: 'PUT',
        body: JSON.stringify({ model: { default: modelId } }),
      }).catch(() => null)
    } else {
      await api('/api/install/pick-default', {
        method: 'POST',
        body: JSON.stringify({ model_id: modelId, slot: 'primary' }),
      })
    }
    subscribeToProgress(modelId)
  } catch (e) {
    toasts.error(`Download failed: ${e.message}`)
    step.value = 3
  }
}

function subscribeToProgress(modelId) {
  if (sse.value) { sse.value.close() }
  const es = new EventSource(`/api/models/${modelId}/pull/stream`)
  sse.value = es
  es.onmessage = (evt) => {
    try {
      const snapshot = JSON.parse(evt.data)
      pullJob.value = snapshot
      if (snapshot.state === 'completed') {
        es.close()
        sse.value = null
        onPullComplete()
      } else if (snapshot.state === 'failed' || snapshot.state === 'cancelled') {
        es.close()
        sse.value = null
        toasts.error(`Download ${snapshot.state}: ${snapshot.error || 'unknown error'}`)
        step.value = 3
      }
    } catch (err) {
      console.error('SSE parse failed', err)
    }
  }
  es.onerror = () => {
    // Browser auto-reconnects. If it stays errored the status poll
    // keeps the UI honest as a fallback.
    pollAsFallback(modelId)
  }
}

async function pollAsFallback(modelId) {
  try {
    const s = await api(`/api/models/${modelId}/pull/status`)
    pullJob.value = s
    if (s.state === 'completed') { onPullComplete(); return }
    if (s.state === 'failed' || s.state === 'cancelled') {
      toasts.error(`Download ${s.state}: ${s.error || 'unknown error'}`)
      step.value = 3
      return
    }
    setTimeout(() => pollAsFallback(modelId), 500)
  } catch {
    setTimeout(() => pollAsFallback(modelId), 1000)
  }
}

async function onPullComplete() {
  try {
    const urls = await api('/api/config/urls')
    chatUrl.value = urls?.openwebui ?? null
  } catch { /* tolerable; fallback below */ }
  await system.fetchStatus()
  step.value = 5
}

async function openChat() {
  try {
    await api('/api/install/complete', { method: 'POST' })
  } catch (e) {
    // Sentinel write failure isn't fatal — the user already has a model.
    console.warn('install/complete failed', e)
  }
  resetFirstRunGuard()
  const target = chatUrl.value || `${window.location.protocol}//${window.location.hostname}:3001`
  window.open(target, '_blank', 'noopener')
}

async function goToDashboard() {
  try { await api('/api/install/complete', { method: 'POST' }) } catch { /* ignore */ }
  resetFirstRunGuard()
  router.push('/')
}

// ── Computed display state ─────────────────────────────────────────────────
const downloadPct = computed(() => {
  if (!pullJob.value) return 0
  const t = pullJob.value.bytes_total
  if (!t) return 0
  return Math.min(100, Math.round((pullJob.value.bytes_downloaded / t) * 100))
})

const downloadEta = computed(() => {
  if (!pullJob.value || !pullJob.value.bytes_downloaded) return '—'
  const elapsedS = (performance.now() - downloadStart.value) / 1000
  const rate = pullJob.value.bytes_downloaded / elapsedS
  if (!rate) return '—'
  const remaining = pullJob.value.bytes_total - pullJob.value.bytes_downloaded
  return fmtEta(remaining / rate)
})

const downloadSpeed = computed(() => {
  if (!pullJob.value || !pullJob.value.bytes_downloaded) return '—'
  const elapsedS = (performance.now() - downloadStart.value) / 1000
  if (elapsedS <= 0) return '—'
  return `${fmtSize(pullJob.value.bytes_downloaded / elapsedS)}/s`
})

const stepLabels = ['Password', 'Pick model', 'License', 'Install', 'Done']
</script>

<template>
  <div class="wizard-page">
    <div class="wizard-card">
      <!-- Header -->
      <div class="wizard-head">
        <div class="wizard-glow" aria-hidden="true"></div>
        <span class="wizard-eyebrow">
          <span class="wizard-eyebrow-dot" aria-hidden="true"></span>
          First run · v1 pre-alpha
        </span>
        <div class="wizard-logo wordmark" aria-hidden="true">h0</div>
        <h1 class="wizard-title">Welcome to hal0</h1>
        <p class="wizard-sub">Local AI for your home. Let's get your first model running.</p>
      </div>

      <!-- Step indicator -->
      <div class="steps" aria-label="Setup progress">
        <div
          v-for="(label, i) in stepLabels"
          :key="i"
          class="step"
          :class="{ 'step-done': step > i + 1, 'step-active': step === i + 1 }"
        >
          <span class="step-num" :aria-label="`Step ${i + 1}: ${label}`">{{ step > i + 1 ? '✓' : i + 1 }}</span>
          <span class="step-label">{{ label }}</span>
        </div>
      </div>

      <!-- ── Step 1 — Set up password (optional, ADR-0001 Child B) ── -->
      <div v-if="step === 1" class="wizard-body">
        <p class="step-desc">
          Set a password to protect the dashboard and the OpenAI-compatible API.
          You can skip this and run hal0 open on your trusted LAN, then add a
          password later from <strong>Settings → Authentication</strong>.
        </p>

        <label class="field-label" for="firstrun-password">Password</label>
        <input
          id="firstrun-password"
          v-model="passwordInput"
          class="field-input"
          type="password"
          autocomplete="new-password"
          :placeholder="`at least ${PASSWORD_MIN_LEN} characters`"
          @keydown.enter.prevent="submitPassword"
        />
        <p v-if="passwordError" class="field-err">{{ passwordError }}</p>
        <p class="field-hint">
          Recommended: at least 12 characters with a mix of letters and digits.
          We hash the password with bcrypt (cost 12) before storing it.
        </p>

        <div class="wizard-footer wizard-footer-2">
          <button
            class="btn-ghost"
            type="button"
            :disabled="passwordSubmitting"
            @click="skipPassword"
          >
            Skip — leave open
          </button>
          <button
            class="btn-primary"
            type="button"
            :disabled="passwordSubmitting || passwordInput.length < PASSWORD_MIN_LEN"
            @click="submitPassword"
          >
            {{ passwordSubmitting ? 'Setting…' : 'Set password' }}
          </button>
        </div>
      </div>

      <!-- ── Step 2 — Pick model ───────────────────────────────── -->
      <div v-if="step === 2" class="wizard-body">
        <p class="step-desc">Pick a starting model. You can add more from the Models page later.</p>

        <div v-if="loadingCatalogue" class="loading-state">Loading models…</div>
        <div v-else-if="catalogueError" class="error-state">
          Could not load curated catalogue: {{ catalogueError }}
        </div>

        <div v-else class="model-list" role="radiogroup" aria-label="Model selection">
          <label
            v-for="m in curated"
            :key="m.id"
            class="model-option"
            :class="{ selected: selectedModel?.id === m.id && !showCustom }"
          >
            <input
              type="radio"
              class="sr-only"
              name="model-pick"
              :value="m.id"
              :checked="selectedModel?.id === m.id && !showCustom"
              @change="selectCurated(m)"
            />
            <div class="model-option-inner">
              <div class="model-option-header">
                <span class="model-option-name">{{ m.display_name }}</span>
                <span class="model-size-chip">{{ fmtSizeGb(m.size_gb) }}</span>
              </div>
              <p class="model-option-desc">{{ m.description }}</p>
              <div class="model-option-meta">
                <span class="meta-chip">{{ m.license }}</span>
                <span class="meta-chip">{{ fmtSizeGb(m.vram_gb_min) }} VRAM</span>
                <span v-for="t in m.tags" :key="t" class="meta-chip meta-chip-tag">{{ t }}</span>
              </div>
            </div>
          </label>
        </div>

        <!-- Custom HF model affordance -->
        <div v-if="customAllowed && !loadingCatalogue" class="custom-disclosure">
          <button
            type="button"
            class="custom-toggle"
            :class="{ open: showCustom }"
            @click="showCustom ? (showCustom = false) : selectCustom()"
          >
            <span class="custom-chevron">{{ showCustom ? '▾' : '▸' }}</span>
            Custom Hugging Face model
          </button>
          <div v-if="showCustom" class="custom-form">
            <label class="field-label" for="custom-name">Display name <span class="req">*</span></label>
            <input id="custom-name" v-model="customName" class="field-input" placeholder="my-model" autocomplete="off" />
            <label class="field-label" for="custom-repo">Repo <span class="req">*</span></label>
            <input id="custom-repo" v-model="customRepo" class="field-input" placeholder="org/Model-GGUF" autocomplete="off" />
            <label class="field-label" for="custom-file">File (.gguf) <span class="req">*</span></label>
            <input id="custom-file" v-model="customFile" class="field-input" placeholder="model-q4_k_m.gguf" autocomplete="off" />
            <p v-if="customErr" class="field-err">{{ customErr }}</p>
            <p class="field-hint">URL: <code>https://huggingface.co/{{ customRepo || 'org/repo' }}/resolve/main/{{ customFile || 'file.gguf' }}</code></p>
          </div>
        </div>

        <div class="wizard-footer">
          <button
            class="btn-primary btn-wide"
            type="button"
            :disabled="!selectedModel && !showCustom"
            @click="goStep3"
          >
            Next: Review license →
          </button>
        </div>
      </div>

      <!-- ── Step 3 — License confirm ─────────────────────────── -->
      <div v-if="step === 3 && selectedModel" class="wizard-body">
        <p class="step-desc">
          You're about to download <strong>{{ selectedModel.display_name }}</strong> under the
          <strong>{{ selectedModel.license }}</strong> license. Confirm the terms below.
        </p>

        <div class="license-card">
          <p class="license-text">
            By clicking "Accept &amp; download", you acknowledge that you are downloading this model
            under its stated license. hal0 does not modify or distribute model weights — the file
            comes straight from Hugging Face. You are responsible for compliance with the model's
            license terms in your jurisdiction.
          </p>
          <a
            v-if="selectedModel.license_url"
            class="license-link"
            :href="selectedModel.license_url"
            target="_blank"
            rel="noopener noreferrer"
          >
            View full license ↗
          </a>
        </div>

        <label class="accept-label">
          <input type="checkbox" v-model="licenseAccepted" />
          I understand and accept the {{ selectedModel.license }} license
        </label>

        <div class="wizard-footer wizard-footer-2">
          <button class="btn-ghost" type="button" @click="backToPicker">← Back</button>
          <button class="btn-primary" type="button" :disabled="!licenseAccepted" @click="startDownload">
            Accept &amp; download
          </button>
        </div>
      </div>

      <!-- ── Step 4 — Download + assign ───────────────────────── -->
      <div v-if="step === 4" class="wizard-body">
        <p class="step-desc">
          Downloading <strong>{{ selectedModel?.display_name }}</strong> and assigning to the
          <code class="inline-code">primary</code> slot.
        </p>

        <div class="progress-wrap" role="progressbar" :aria-valuenow="downloadPct" aria-valuemin="0" aria-valuemax="100">
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: downloadPct + '%' }" />
          </div>
          <span class="progress-pct">{{ downloadPct }}%</span>
        </div>

        <div class="progress-meta">
          <span>{{ pullJob ? fmtSize(pullJob.bytes_downloaded) : '0 B' }} / {{ pullJob ? fmtSize(pullJob.bytes_total) : '—' }}</span>
          <span>{{ downloadSpeed }}</span>
          <span>ETA {{ downloadEta }}</span>
        </div>

        <p class="step-hint">This typically takes a few minutes depending on your connection. The slot starts automatically when the download completes.</p>
      </div>

      <!-- ── Step 5 — Done ────────────────────────────────────── -->
      <div v-if="step === 5" class="wizard-body wizard-done">
        <div class="done-icon" aria-hidden="true">✓</div>
        <h2 class="done-title">You're all set!</h2>
        <p class="done-desc">
          <strong>{{ selectedModel?.display_name }}</strong> is in the primary slot and ready to serve requests.
        </p>

        <div class="done-actions">
          <button class="btn-primary btn-wide" type="button" @click="openChat">
            Open chat →
          </button>
          <button class="btn-ghost" type="button" @click="goToDashboard">
            Go to dashboard
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wizard-page {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: 32px 16px;
  background: var(--hal0-bg);
  overflow: hidden;
}
.wizard-page::before {
  content: '';
  position: absolute;
  inset: -20% -10% auto -10%;
  height: 60%;
  pointer-events: none;
  background: radial-gradient(ellipse at center, var(--hal0-accent-glow), transparent 70%);
  z-index: 0;
}

.wizard-card {
  position: relative;
  z-index: 1;
  background: var(--hal0-bg-elevated);
  border: 1px solid var(--hal0-border);
  border-radius: var(--radius-xl);
  width: min(580px, 100%);
  overflow: hidden;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.45);
}

.wizard-head {
  position: relative;
  text-align: center;
  padding: 36px 32px 24px;
  border-bottom: 1px solid var(--hal0-border);
}
.wizard-glow {
  position: absolute;
  inset: auto 0 -32px 0;
  height: 64px;
  pointer-events: none;
  background: radial-gradient(ellipse at center, var(--hal0-accent-glow), transparent 70%);
}
.wizard-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 18px;
  padding: 4px 11px;
  border-radius: 999px;
  border: 1px solid var(--hal0-border);
  background: var(--hal0-bg);
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--hal0-fg-muted);
}
.wizard-eyebrow-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--hal0-accent);
  box-shadow: 0 0 8px var(--hal0-accent);
}
.wizard-logo {
  width: 56px; height: 56px; border-radius: 14px;
  background: var(--hal0-accent);
  color: #000;
  font-family: var(--hal0-font-display); font-size: 22px; font-weight: 700;
  display: grid; place-items: center; margin: 0 auto 18px;
  letter-spacing: -0.04em;
  box-shadow: 0 0 32px color-mix(in srgb, var(--hal0-accent) 30%, transparent);
}
.wizard-title { font-size: 28px; font-weight: 600; color: var(--hal0-fg); margin: 0 0 8px; letter-spacing: -0.02em; }
.wizard-sub   { font-size: 14px; color: var(--hal0-fg-muted); margin: 0; line-height: 1.5; }

/* Steps */
.steps {
  display: flex;
  align-items: center;
  gap: 0;
  padding: 16px 32px;
  border-bottom: 1px solid var(--color-border);
}
.step {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  flex: 1;
  position: relative;
}
.step + .step::before {
  content: '';
  position: absolute; left: -50%; top: 10px;
  width: 100%; height: 1px;
  background: var(--color-border);
}
.step-done + .step::before { background: var(--hal0-accent); }
.step-num {
  width: 22px; height: 22px; border-radius: 50%;
  background: var(--color-surface-2); border: 1px solid var(--color-border);
  color: var(--color-fg-faint); font-size: 11px;
  display: grid; place-items: center; font-family: var(--font-mono);
  position: relative; z-index: 1;
}
.step-active .step-num { border-color: var(--hal0-accent); color: var(--hal0-accent); background: var(--color-accent-bg); box-shadow: 0 0 12px color-mix(in srgb, var(--hal0-accent) 35%, transparent); }
.step-done .step-num   { background: var(--hal0-accent); color: #000; border-color: var(--hal0-accent); }
.step-label { font-size: 10.5px; color: var(--color-fg-faint); font-family: var(--font-mono); text-transform: uppercase; letter-spacing: 0.06em; }
.step-active .step-label { color: var(--hal0-accent); }

/* Body */
.wizard-body { padding: 24px 32px; display: flex; flex-direction: column; gap: 16px; }

.step-desc { font-size: 13.5px; color: var(--color-fg-muted); margin: 0; line-height: 1.6; }
.step-hint { font-size: 12px; color: var(--color-fg-faint); margin: 0; }
.loading-state, .error-state { padding: 24px; text-align: center; color: var(--color-fg-faint); font-size: 13px; }
.error-state { color: var(--color-danger); }

/* Model list */
.model-list { display: flex; flex-direction: column; gap: 8px; }
.model-option {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
  background: var(--color-surface);
}
.model-option:hover { border-color: var(--color-border-hi); }
.model-option.selected {
  border-color: color-mix(in srgb, var(--hal0-accent) 45%, var(--color-border));
  box-shadow: inset 3px 0 0 var(--hal0-accent);
}
.model-option-inner { padding: 12px 14px; }
.model-option-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 4px; }
.model-option-name { font-family: var(--font-mono); font-weight: 600; color: var(--color-fg); font-size: 13.5px; }
.model-size-chip { font-family: var(--font-mono); font-size: 10.5px; padding: 2px 7px; border-radius: 999px; background: color-mix(in srgb, var(--hal0-accent) 14%, transparent); border: 1px solid color-mix(in srgb, var(--hal0-accent) 35%, transparent); color: var(--hal0-accent); text-transform: uppercase; letter-spacing: 0.04em; font-feature-settings: 'zero' 1, 'ss02' 1; }
.model-option-desc { font-size: 12px; color: var(--color-fg-muted); margin: 0 0 8px; }
.model-option-meta { display: flex; gap: 6px; flex-wrap: wrap; }
.meta-chip { font-family: var(--font-mono); font-size: 10.5px; padding: 2px 6px; border-radius: 4px; background: var(--color-surface-3); color: var(--color-fg-faint); border: 1px solid var(--color-border); }
.meta-chip-tag { opacity: 0.75; }

/* Custom HF form */
.custom-disclosure { border-top: 1px dashed var(--color-border); padding-top: 12px; }
.custom-toggle {
  background: transparent;
  border: none;
  color: var(--color-fg-muted);
  font-size: 13px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  width: 100%;
  text-align: left;
}
.custom-toggle:hover { color: var(--color-fg); }
.custom-chevron { font-family: var(--font-mono); width: 12px; }
.custom-form { display: flex; flex-direction: column; gap: 6px; padding-top: 8px; }
.field-label { font-size: 12.5px; font-weight: 600; color: var(--color-fg-muted); margin-top: 4px; }
.req { color: var(--color-danger); }
.field-input { padding: 7px 10px; border-radius: var(--radius); border: 1px solid var(--color-border); background: var(--color-surface-2); color: var(--color-fg); font-size: 13px; outline: none; transition: border-color 0.1s; box-sizing: border-box; width: 100%; }
.field-input:focus { border-color: var(--color-border-hi); }
.field-err { font-size: 11.5px; color: var(--color-danger); margin: 0; }
.field-hint { font-size: 11px; color: var(--color-fg-faint); margin: 4px 0 0; word-break: break-all; }
.field-hint code { font-family: var(--font-mono); font-size: 10.5px; background: var(--color-surface-2); padding: 1px 4px; border-radius: 3px; }

/* License */
.license-card { background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: var(--radius-lg); padding: 14px; }
.license-text { font-size: 12.5px; color: var(--color-fg-muted); margin: 0 0 8px; line-height: 1.6; }
.license-link { font-size: 12px; color: var(--hal0-accent); text-decoration: none; font-family: var(--font-mono); }
.license-link:hover { text-decoration: underline; }
.accept-label { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--color-fg-muted); cursor: pointer; }

/* Progress */
.progress-wrap { display: flex; align-items: center; gap: 12px; }
.progress-bar { flex: 1; height: 8px; background: var(--color-surface-3); border-radius: 4px; overflow: hidden; }
.progress-fill { height: 100%; background: var(--hal0-accent); border-radius: 4px; transition: width 0.3s ease; box-shadow: 0 0 12px color-mix(in srgb, var(--hal0-accent) 50%, transparent); }
.progress-pct { font-family: var(--font-mono); font-size: 12px; color: var(--hal0-accent); min-width: 36px; text-align: right; font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1; }
.progress-meta {
  display: flex;
  justify-content: space-between;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-fg-faint);
  gap: 12px;
  font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1;
}

/* Done */
.wizard-done { align-items: center; text-align: center; padding: 32px; }
.done-icon { width: 56px; height: 56px; border-radius: 50%; background: color-mix(in srgb, var(--hal0-accent) 14%, var(--color-surface)); border: 2px solid var(--hal0-accent); color: var(--hal0-accent); font-size: 24px; display: grid; place-items: center; box-shadow: 0 0 32px color-mix(in srgb, var(--hal0-accent) 30%, transparent); }
.done-title { font-size: 24px; font-weight: 600; color: var(--color-fg); margin: 0; letter-spacing: -0.02em; }
.done-desc  { font-size: 13.5px; color: var(--color-fg-muted); margin: 0; line-height: 1.6; }
.done-actions { display: flex; flex-direction: column; gap: 10px; width: 100%; align-items: center; }

/* Footers */
.wizard-footer { padding-top: 4px; }
.wizard-footer-2 { display: flex; justify-content: space-between; align-items: center; }

/* Buttons */
.btn-primary { display: flex; align-items: center; justify-content: center; gap: 6px; padding: 11px 22px; border-radius: var(--radius); background: var(--hal0-accent); color: #000; font-family: var(--font-mono); font-size: 13px; font-weight: 500; border: none; cursor: pointer; transition: background 0.15s; }
.btn-primary:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn-primary:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-wide { width: 100%; }
.btn-ghost { padding: 9px 18px; border-radius: var(--radius); border: 1px solid var(--color-border); background: transparent; color: var(--color-fg-muted); font-family: var(--font-mono); font-size: 12px; cursor: pointer; transition: border-color 0.15s, color 0.15s; }
.btn-ghost:hover { border-color: var(--color-border-hi); color: var(--color-fg); }

.inline-code { font-family: var(--font-mono); font-size: 12px; padding: 1px 5px; border-radius: 3px; background: var(--color-surface-3); color: var(--color-fg-muted); }

.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
</style>
