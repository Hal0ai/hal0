<script setup>
/**
 * FirstRun.vue — Production wizard (8 steps, linear).
 *
 * Replaces the legacy 5-step picker (FirstRunLegacy.vue, deleted) and the
 * three competing IA prototypes (components/firstrun-proto/, deleted).
 * The chosen flow is Variant A from the prototype round:
 *
 *   1. Password (optional; auto-skip when /api/auth/status.password_set)
 *   2. Detected hardware + model storage directories
 *   3. Primary chat model (curated picker, hardware-aware)
 *   4. Capabilities (embed / voice.stt / voice.tts / img) with smart defaults
 *   5. HF token (conditional — only when at least one selected model is gated)
 *   6. License acceptance (aggregated across every selected model)
 *   7. Install — parallel pulls + capability registration with retry-per-row
 *   8. Done — links to dashboard, OpenWebUI chat, settings
 *
 * Visual treatment lifted from FirstRunLegacy: aurora gradient, glow
 * header, mono eyebrow chip, hal0-amber accent, step-indicator row,
 * primary/ghost button styles, done-coda chrome. The new screens (2, 4,
 * 5) reuse the same wizard-body card structure so the visual rhythm
 * stays the same as the user clicks Next.
 *
 * State lives in the `useFirstRun` singleton (see
 * components/firstrun/useFirstRun.js). The view here only sequences
 * steps + calls the composable's actions.
 */
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useToastsStore } from '../stores/toasts.js'
import { useSystemStore } from '../stores/system.js'
import { resetFirstRunGuard } from '../router.js'
import { useFirstRun } from '../components/firstrun/useFirstRun.js'
import CapabilityToggle from '../components/capabilities/CapabilityToggle.vue'
import Wordmark from '../components/Wordmark.vue'

const router = useRouter()
const toasts = useToastsStore()
const system = useSystemStore()

const s = useFirstRun()

// Step labels for the indicator row.
const STEPS = [
  { n: 1, label: 'Password' },
  { n: 2, label: 'Hardware' },
  { n: 3, label: 'Chat' },
  { n: 4, label: 'Capabilities' },
  { n: 5, label: 'HF token' },
  { n: 6, label: 'License' },
  { n: 7, label: 'Install' },
  { n: 8, label: 'Done' },
]

// Start on step 2 when a password is already set (re-entering the wizard
// after an upgrade or a manual /etc/hal0/auth.toml). Mirrors the legacy
// auto-skip behaviour from the password step.
const step = ref(s.passwordAlreadySet.value ? 2 : 1)
watch(s.passwordAlreadySet, (set) => { if (set && step.value === 1) step.value = 2 })

// Chat URL for the "Open chat" button on the done coda.
const chatUrl = ref(null)

// Step 4 — rerank is a sub-disclosure inside the embed row, locked off-by-
// default per the IA-grilling session. The disclosure is closed initially.
const showRerank = ref(false)

// Step 2 — disk-dir entry. Editing the first dir maps back to form.modelDirs[0].
function addModelDir() {
  s.form.modelDirs.push('')
}
function removeModelDir(i) {
  if (s.form.modelDirs.length <= 1) return
  s.form.modelDirs.splice(i, 1)
}

// Step transitions — Next skips conditional HF-token step (5) when no
// gated models are selected. Back mirrors the skip so the user can
// retreat to Capabilities (4) without snapping to HF token.
async function goNext() {
  if (step.value === 1) {
    if (s.form.password) {
      try {
        await s.submitPassword()
        toasts.info('Password set — you can change it later in Settings.')
      } catch (e) {
        toasts.error(e?.message || 'Could not set password.')
        return
      }
    }
    step.value = 2
    return
  }
  if (step.value === 2) {
    try {
      await s.persistModelDirs()
    } catch (e) {
      // Don't block — surfacing the error is enough.
      toasts.warning(`Could not persist storage dirs: ${e?.message || e}. Continuing with defaults.`)
    }
    step.value = 3
    return
  }
  if (step.value === 3) {
    if (!s.primaryModel.value) {
      toasts.warning('Please select a chat model first')
      return
    }
    step.value = 4
    return
  }
  if (step.value === 4) {
    // Skip HF token step if no gated models are in the selection.
    step.value = s.needsHfToken.value ? 5 : 6
    return
  }
  if (step.value === 5) {
    s.persistHfToken()
    step.value = 6
    return
  }
  if (step.value === 6) {
    if (!s.form.licenseAccepted) {
      toasts.warning('Please accept the licenses first')
      return
    }
    if (!s.fits.value) {
      toasts.warning('Not enough disk space — free up storage or remove capabilities')
      return
    }
    step.value = 7
    s.startAllPulls()
    return
  }
}

function goBack() {
  if (step.value === 6) { step.value = s.needsHfToken.value ? 5 : 4; return }
  if (step.value === 1) return
  step.value = Math.max(1, step.value - 1)
}

function skipPassword() {
  s.form.password = ''
  step.value = 2
}

// Step 7 → 8 happens implicitly when every pull is terminal. The
// useFirstRun composable flips `pull.done` once all items settled; we
// advance the step on that signal (and refresh system store + chat URL).
watch(
  () => s.pull.done,
  async (done) => {
    if (!done) return
    if (step.value !== 7) return
    // Refresh dashboard state so the user lands on a hydrated screen if
    // they pick "Go to dashboard" rather than chat.
    try {
      const urls = await import('../composables/useApi.js').then(({ api }) => api('/api/config/urls'))
      chatUrl.value = urls?.openwebui ?? null
    } catch { /* tolerable */ }
    try { await system.fetchStatus() } catch { /* ignore */ }
    step.value = 8
  },
)

async function openChat() {
  await s.markComplete()
  resetFirstRunGuard()
  const target = chatUrl.value || `${window.location.protocol}//${window.location.hostname}:3001`
  window.open(target, '_blank', 'noopener')
}
async function goToDashboard() {
  await s.markComplete()
  resetFirstRunGuard()
  router.push('/')
}
async function goToSettings() {
  await s.markComplete()
  resetFirstRunGuard()
  router.push('/settings')
}

onMounted(() => { /* state already loaded by the composable singleton */ })
onUnmounted(() => { s.dispose() })

// ── View helpers ──────────────────────────────────────────────────────
function fmtSize(bytes) {
  if (!bytes) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let n = bytes
  let i = 0
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(n >= 10 ? 0 : 1)} ${u[i]}`
}
function fmtGb(n) { return `${(n || 0).toFixed(1)} GB` }
function selectKey(cap) { return cap.backend && cap.model ? `${cap.backend}::${cap.model}` : '' }

// Step 6 — when the per-model license is identical across every selected
// model (common for a chat-only install with no caps), dedupe the list
// so the operator sees a single license card instead of N copies.
const licenseList = computed(() => s.enabledList.value)

const canAdvance = computed(() => {
  if (step.value === 1) return s.form.password.length === 0 || s.form.password.length >= 8
  if (step.value === 2) return s.form.modelDirs.some((d) => String(d).trim().length > 0)
  if (step.value === 3) return !!s.primaryModel.value
  if (step.value === 4) return true
  if (step.value === 5) return true  // skip is allowed; empty token is permitted
  if (step.value === 6) return s.form.licenseAccepted && s.fits.value
  return false
})
</script>

<template>
  <div class="wizard-page">
    <div class="wizard-card">
      <!-- ── Header (lifted from FirstRunLegacy) ────────────────── -->
      <div class="wizard-head">
        <div class="wizard-glow" aria-hidden="true"></div>
        <span class="wizard-eyebrow">
          <span class="wizard-eyebrow-dot" aria-hidden="true"></span>
          First run · v1 pre-alpha
        </span>
        <Wordmark size="text-5xl" class="wizard-mark" aria-hidden="true" />
        <h1 class="wizard-title">Welcome to hal0</h1>
        <p class="wizard-sub">Local AI for your home. Eight quick steps to your first model.</p>
      </div>

      <!-- ── Step indicator ─────────────────────────────────────── -->
      <ol class="steps" aria-label="Setup progress">
        <li
          v-for="st in STEPS"
          :key="st.n"
          class="step"
          :class="{ 'step-done': step > st.n, 'step-active': step === st.n }"
        >
          <span class="step-num" :aria-label="`Step ${st.n}: ${st.label}`">{{ step > st.n ? '✓' : st.n }}</span>
          <span class="step-label">{{ st.label }}</span>
        </li>
      </ol>

      <!-- ── Loading shell ──────────────────────────────────────── -->
      <div v-if="s.loading.value && step !== 7 && step !== 8" class="wizard-body">
        <div class="loading-state">Loading…</div>
      </div>

      <!-- ── 1. Password ────────────────────────────────────────── -->
      <transition name="wizard-fade" mode="out-in">
        <div v-if="!s.loading.value && step === 1" key="s1" class="wizard-body">
          <p class="step-desc">
            Set a password to protect the dashboard and the OpenAI-compatible API.
            You can skip this and run hal0 open on your trusted LAN, then add a
            password later from <strong>Settings → Authentication</strong>.
          </p>
          <label class="field-label" for="firstrun-password">Password</label>
          <input
            id="firstrun-password"
            v-model="s.form.password"
            class="field-input"
            type="password"
            autocomplete="new-password"
            placeholder="at least 8 characters"
            @keydown.enter.prevent="goNext"
          />
          <p class="field-hint">
            Recommended: at least 12 characters with a mix of letters and digits.
            We hash the password with bcrypt (cost 12) before storing it.
          </p>
          <div class="wizard-footer wizard-footer-2">
            <button class="btn-ghost" type="button" @click="skipPassword">Skip — leave open</button>
            <button class="btn-primary" type="button" :disabled="!canAdvance" @click="goNext">
              {{ s.form.password ? 'Set password →' : 'Next →' }}
            </button>
          </div>
        </div>

        <!-- ── 2. Detected hardware + model storage ──────────────── -->
        <div v-else-if="!s.loading.value && step === 2" key="s2" class="wizard-body">
          <p class="step-desc">We probed your hardware. Confirm where downloaded models live.</p>

          <div class="hw-card">
            <div class="hw-row"><span class="hw-l">CPU</span><span class="hw-v">{{ s.hardware.value?.cpu_name || s.hardware.value?.cpu_model || '—' }}</span></div>
            <div class="hw-row"><span class="hw-l">Memory</span><span class="hw-v">{{ Math.round((s.hardware.value?.unified_memory_mb || s.hardware.value?.ram_total_mb || 0) / 1024) }} GB unified</span></div>
            <div class="hw-row"><span class="hw-l">GPU</span><span class="hw-v">{{ s.hardware.value?.gpu_name || '—' }}</span></div>
            <div class="hw-row"><span class="hw-l">NPU</span><span class="hw-v">{{ s.hardware.value?.npu_present ? (s.hardware.value?.npu_name || 'detected') : 'none detected' }}</span></div>
          </div>

          <div class="dirs-block">
            <label class="field-label">Model storage directories</label>
            <div v-for="(dir, i) in s.form.modelDirs" :key="i" class="dir-row">
              <input
                v-model="s.form.modelDirs[i]"
                class="field-input field-input-mono"
                :placeholder="i === 0 ? '/var/lib/hal0/models' : '/mnt/extra-models'"
                spellcheck="false"
                autocomplete="off"
              />
              <button
                v-if="s.form.modelDirs.length > 1"
                type="button"
                class="dir-remove"
                :aria-label="`Remove ${dir}`"
                @click="removeModelDir(i)"
              >×</button>
            </div>
            <button type="button" class="dir-add" @click="addModelDir">+ add directory</button>
            <p v-if="!s.modelsConfigWritable.value" class="field-err">
              Could not persist directories last time — they'll be used in-memory only.
            </p>
          </div>

          <div class="disk-row">
            <span class="disk-l">Free on first directory</span>
            <span class="disk-v">{{ fmtGb(s.diskFreeGb.value) }}</span>
          </div>
          <p class="field-hint">
            Per-dir validation runs server-side; non-absolute paths are rejected.
            You can add more later from <strong>Settings → Storage</strong>.
          </p>

          <div class="wizard-footer wizard-footer-2">
            <button class="btn-ghost" type="button" @click="goBack">← Back</button>
            <button class="btn-primary" type="button" :disabled="!canAdvance" @click="goNext">Next →</button>
          </div>
        </div>

        <!-- ── 3. Primary chat ───────────────────────────────────── -->
        <div v-else-if="!s.loading.value && step === 3" key="s3" class="wizard-body">
          <p class="step-desc">Pick the model that powers chat. You can add more from the Models page later.</p>

          <div v-if="!s.curated.value.length" class="error-state">
            Curated catalogue is empty — the API may be unavailable.
          </div>

          <div v-else class="model-list" role="radiogroup" aria-label="Chat model selection">
            <label
              v-for="m in s.curated.value"
              :key="m.id"
              class="model-option"
              :class="{ selected: s.form.primaryId === m.id, dim: m.vram_gb_min > ((s.hardware.value?.unified_memory_mb || s.hardware.value?.ram_total_mb || 0) / 1024) }"
            >
              <input
                type="radio"
                class="sr-only"
                name="firstrun-primary"
                :value="m.id"
                v-model="s.form.primaryId"
              />
              <div class="model-option-inner">
                <div class="model-option-header">
                  <span class="model-option-name">{{ m.display_name }}</span>
                  <span class="model-size-chip">{{ fmtGb(m.size_gb) }}</span>
                </div>
                <p class="model-option-desc">{{ m.description }}</p>
                <div class="model-option-meta">
                  <span class="meta-chip">{{ m.license }}</span>
                  <span class="meta-chip">{{ fmtGb(m.vram_gb_min) }} VRAM</span>
                  <span
                    class="meta-chip"
                    :class="m.vram_gb_min <= ((s.hardware.value?.unified_memory_mb || s.hardware.value?.ram_total_mb || 0) / 1024) ? 'meta-chip-ok' : 'meta-chip-tight'"
                  >
                    {{ m.vram_gb_min <= ((s.hardware.value?.unified_memory_mb || s.hardware.value?.ram_total_mb || 0) / 1024) ? 'fits' : 'tight' }}
                  </span>
                  <span v-for="t in m.tags" :key="t" class="meta-chip meta-chip-tag">{{ t }}</span>
                </div>
              </div>
            </label>
          </div>

          <div class="wizard-footer wizard-footer-2">
            <button class="btn-ghost" type="button" @click="goBack">← Back</button>
            <button class="btn-primary" type="button" :disabled="!canAdvance" @click="goNext">Next: capabilities →</button>
          </div>
        </div>

        <!-- ── 4. Capabilities ───────────────────────────────────── -->
        <div v-else-if="!s.loading.value && step === 4" key="s4" class="wizard-body">
          <p class="step-desc">
            Pick which capabilities run at startup. Off = configured but not loaded — flip on later in <strong>/slots</strong>.
          </p>

          <!-- embed -->
          <section class="cap">
            <header class="cap-head">
              <div class="cap-l">
                <span class="cap-name">embed</span>
                <span class="cap-sub">text → vector · /v1/embeddings</span>
              </div>
              <CapabilityToggle v-model="s.form.caps.embed.enabled" label="enable embed" />
            </header>
            <select
              class="cap-select"
              :value="selectKey(s.form.caps.embed)"
              @change="s.setCapModel('embed', 'embed', 'embed', $event.target.value)"
            >
              <option value="" disabled>pick model…</option>
              <option
                v-for="o in s.optionsFor('embed', 'embed')"
                :key="`${o.backend}::${o.id}`"
                :value="`${o.backend}::${o.id}`"
              >{{ o.backend }} / {{ o.id }} — {{ fmtGb(o.size_gb) }}</option>
            </select>
            <button class="disclosure" type="button" @click="showRerank = !showRerank">
              <span class="custom-chevron">{{ showRerank ? '▾' : '▸' }}</span>
              Advanced: rerank
            </button>
            <div v-if="showRerank" class="cap-nested">
              <header class="cap-head">
                <div class="cap-l">
                  <span class="cap-name cap-name-sm">rerank</span>
                  <span class="cap-sub">query+doc → score · /v1/rerankings</span>
                </div>
                <CapabilityToggle v-model="s.form.caps.rerank.enabled" label="enable rerank" />
              </header>
              <select
                class="cap-select"
                :value="selectKey(s.form.caps.rerank)"
                @change="s.setCapModel('rerank', 'embed', 'rerank', $event.target.value)"
              >
                <option value="" disabled>pick model…</option>
                <option
                  v-for="o in s.optionsFor('embed', 'rerank')"
                  :key="`${o.backend}::${o.id}`"
                  :value="`${o.backend}::${o.id}`"
                >{{ o.backend }} / {{ o.id }} — {{ fmtGb(o.size_gb) }}</option>
              </select>
            </div>
          </section>

          <!-- voice -->
          <section class="cap">
            <header class="cap-head">
              <div class="cap-l">
                <span class="cap-name">voice</span>
                <span class="cap-sub">speech in + out · stt + tts</span>
              </div>
            </header>
            <div class="cap-sub-row">
              <span class="cap-sub-name">stt · /v1/audio/transcriptions</span>
              <CapabilityToggle v-model="s.form.caps.stt.enabled" label="enable stt" />
            </div>
            <select
              class="cap-select"
              :value="selectKey(s.form.caps.stt)"
              @change="s.setCapModel('stt', 'voice', 'stt', $event.target.value)"
            >
              <option value="" disabled>pick model…</option>
              <option
                v-for="o in s.optionsFor('voice', 'stt')"
                :key="`${o.backend}::${o.id}`"
                :value="`${o.backend}::${o.id}`"
              >{{ o.backend }} / {{ o.id }} — {{ fmtGb(o.size_gb) }}</option>
            </select>
            <div class="cap-sub-row">
              <span class="cap-sub-name">tts · /v1/audio/speech</span>
              <CapabilityToggle v-model="s.form.caps.tts.enabled" label="enable tts" />
            </div>
            <select
              class="cap-select"
              :value="selectKey(s.form.caps.tts)"
              @change="s.setCapModel('tts', 'voice', 'tts', $event.target.value)"
            >
              <option value="" disabled>pick model…</option>
              <option
                v-for="o in s.optionsFor('voice', 'tts')"
                :key="`${o.backend}::${o.id}`"
                :value="`${o.backend}::${o.id}`"
              >{{ o.backend }} / {{ o.id }} — {{ fmtGb(o.size_gb) }}</option>
            </select>
          </section>

          <!-- img -->
          <section class="cap">
            <header class="cap-head">
              <div class="cap-l">
                <span class="cap-name">img</span>
                <span class="cap-sub">text → image · /v1/images/generations</span>
              </div>
              <CapabilityToggle v-model="s.form.caps.img.enabled" label="enable img" />
            </header>
            <select
              class="cap-select"
              :value="selectKey(s.form.caps.img)"
              @change="s.setCapModel('img', 'img', 'img', $event.target.value)"
            >
              <option value="" disabled>pick model…</option>
              <option
                v-for="o in s.optionsFor('img', 'img')"
                :key="`${o.backend}::${o.id}`"
                :value="`${o.backend}::${o.id}`"
              >{{ o.backend }} / {{ o.id }} — {{ fmtGb(o.size_gb) }}</option>
            </select>
            <p v-if="s.form.caps.img.enabled" class="step-warn">Heavy: 7–12 GB pull + significant memory at runtime.</p>
          </section>

          <div class="wizard-footer wizard-footer-2">
            <button class="btn-ghost" type="button" @click="goBack">← Back</button>
            <button class="btn-primary" type="button" @click="goNext">Next →</button>
          </div>
        </div>

        <!-- ── 5. HF token (conditional) ─────────────────────────── -->
        <div v-else-if="!s.loading.value && step === 5" key="s5" class="wizard-body">
          <p class="step-desc">
            One or more selected models are gated on Hugging Face. Provide a read-only
            token, or skip and remove the gated picks on the previous step.
          </p>
          <ul class="gated-list">
            <li v-for="m in s.gatedModels.value" :key="m.id">
              <span class="gated-name">{{ m.display_name }}</span>
              <span class="gated-id">({{ m.id }})</span>
            </li>
          </ul>
          <label class="field-label" for="firstrun-hf">Hugging Face access token</label>
          <input
            id="firstrun-hf"
            v-model="s.form.hfToken"
            class="field-input field-input-mono"
            placeholder="hf_…"
            autocomplete="off"
            spellcheck="false"
          />
          <p class="field-hint">
            Optional — only needed for gated models. The current pull task
            reads <code>HF_TOKEN</code> from the API process env; for now,
            export it on the host before starting hal0. The wizard caches
            what you type here in your browser so a refresh keeps it.
          </p>
          <div class="wizard-footer wizard-footer-2">
            <button class="btn-ghost" type="button" @click="goBack">← Back</button>
            <button class="btn-primary" type="button" @click="goNext">{{ s.form.hfToken ? 'Save &amp; next →' : 'Skip token →' }}</button>
          </div>
        </div>

        <!-- ── 6. License acceptance (aggregated) ────────────────── -->
        <div v-else-if="!s.loading.value && step === 6" key="s6" class="wizard-body">
          <p class="step-desc">
            You're about to download these models. Confirm the licenses below.
            hal0 does not modify or redistribute weights — files come straight
            from Hugging Face. You're responsible for compliance in your jurisdiction.
          </p>

          <ul class="license-list">
            <li v-for="row in licenseList" :key="row.kind + ':' + row.modelId" class="license-row">
              <div class="license-row-l">
                <span class="license-row-name">{{ row.label }}</span>
                <span class="license-row-size">{{ fmtGb(row.sizeGb) }}</span>
              </div>
              <div class="license-row-r">
                <span class="meta-chip">{{ row.license || 'see repo' }}</span>
                <a
                  v-if="row.license_url"
                  class="license-link"
                  :href="row.license_url"
                  target="_blank"
                  rel="noopener noreferrer"
                >view ↗</a>
              </div>
            </li>
          </ul>

          <div class="totals">
            <div><span class="totals-l">Total download</span><span class="totals-v">{{ fmtGb(s.totalDownloadGb.value) }}</span></div>
            <div>
              <span class="totals-l">Free on storage dir</span>
              <span class="totals-v" :class="{ 'totals-bad': !s.fits.value }">{{ fmtGb(s.diskFreeGb.value) }}</span>
            </div>
          </div>
          <p v-if="!s.fits.value" class="field-err">
            Not enough disk space. Free up some, add a larger storage directory in step 2, or remove capabilities.
          </p>

          <label class="accept-label">
            <input type="checkbox" v-model="s.form.licenseAccepted" />
            I accept the licenses for each model shown above.
          </label>

          <div class="wizard-footer wizard-footer-2">
            <button class="btn-ghost" type="button" @click="goBack">← Back</button>
            <button class="btn-primary" type="button" :disabled="!canAdvance" @click="goNext">Accept &amp; install →</button>
          </div>
        </div>

        <!-- ── 7. Install (parallel pulls + retry-per-row) ───────── -->
        <div v-else-if="step === 7" key="s7" class="wizard-body">
          <p class="step-desc">
            Downloading {{ s.pull.items.length }} model{{ s.pull.items.length === 1 ? '' : 's' }} in parallel.
            You can leave this tab open and come back — pulls keep running on the server.
          </p>

          <div v-for="it in s.pull.items" :key="it.key" class="pull-row" :class="{ 'pull-row-done': it.state === 'completed', 'pull-row-err': it.state === 'failed' || it.state === 'cancelled' }">
            <div class="pull-head">
              <span class="pull-name">{{ it.label }}</span>
              <span class="pull-state">
                <template v-if="it.state === 'completed'">✓ done</template>
                <template v-else-if="it.state === 'failed' || it.state === 'cancelled'">! {{ it.state }}</template>
                <template v-else>{{ it.pct }}%</template>
              </span>
            </div>
            <div class="pull-bar"><div class="pull-fill" :style="{ width: it.pct + '%' }" /></div>
            <div class="pull-meta">
              <span>{{ fmtSize(it.bytesDownloaded) }} / {{ it.bytesTotal ? fmtSize(it.bytesTotal) : '—' }}</span>
              <span v-if="it.error" class="pull-err">{{ it.error }}</span>
              <button
                v-if="it.state === 'failed' || it.state === 'cancelled'"
                type="button"
                class="btn-ghost btn-ghost-sm"
                @click="s.retryItem(it.key)"
              >Retry</button>
            </div>
          </div>

          <p v-if="s.pull.error" class="field-err">{{ s.pull.error }}</p>
          <p v-else-if="!s.pull.done" class="step-hint">Slots warm up automatically once each download completes.</p>
        </div>

        <!-- ── 8. Done ───────────────────────────────────────────── -->
        <div v-else-if="step === 8" key="s8" class="wizard-body wizard-done">
          <div class="done-icon" aria-hidden="true">✓</div>
          <h2 class="done-title">You're all set!</h2>
          <p class="done-desc">
            {{ s.enabledList.value.length }} model{{ s.enabledList.value.length === 1 ? '' : 's' }} ready.
            Capabilities are loading in the background.
          </p>

          <div class="done-actions">
            <button class="btn-primary btn-wide" type="button" @click="openChat">Open chat →</button>
            <button class="btn-ghost" type="button" @click="goToDashboard">Go to dashboard</button>
            <button class="btn-ghost" type="button" @click="goToSettings">Open settings</button>
          </div>
        </div>
      </transition>
    </div>
  </div>
</template>

<style scoped>
/* ── Page chrome (lifted from FirstRunLegacy) ──────────────────────────── */
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
  width: min(620px, 100%);
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
.wizard-mark {
  display: flex; justify-content: center; margin: 0 auto 18px;
  filter: drop-shadow(0 0 24px color-mix(in srgb, var(--hal0-accent) 30%, transparent));
}
.wizard-title { font-size: 28px; font-weight: 600; color: var(--hal0-fg); margin: 0 0 8px; letter-spacing: -0.02em; }
.wizard-sub   { font-size: 14px; color: var(--hal0-fg-muted); margin: 0; line-height: 1.5; }

/* ── Steps indicator ───────────────────────────────────────────────────── */
.steps {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 0;
  padding: 16px 24px;
  border-bottom: 1px solid var(--color-border);
  margin: 0;
  list-style: none;
}
.step {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
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
.step-label { font-size: 10px; color: var(--color-fg-faint); font-family: var(--font-mono); text-transform: uppercase; letter-spacing: 0.06em; }
.step-active .step-label { color: var(--hal0-accent); }

/* ── Body + shared field styling ───────────────────────────────────────── */
.wizard-body { padding: 24px 32px; display: flex; flex-direction: column; gap: 16px; }

.step-desc { font-size: 13.5px; color: var(--color-fg-muted); margin: 0; line-height: 1.6; }
.step-hint { font-size: 12px; color: var(--color-fg-faint); margin: 0; }
.step-warn { font-size: 11.5px; color: var(--color-warning, #f5b049); margin: 2px 0 0; }
.loading-state, .error-state { padding: 24px; text-align: center; color: var(--color-fg-faint); font-size: 13px; }
.error-state { color: var(--color-danger); }

.field-label { font-size: 12.5px; font-weight: 600; color: var(--color-fg-muted); margin-top: 4px; }
.field-input { padding: 8px 10px; border-radius: var(--radius); border: 1px solid var(--color-border); background: var(--color-surface-2); color: var(--color-fg); font-size: 13px; outline: none; transition: border-color 0.1s; box-sizing: border-box; width: 100%; }
.field-input:focus { border-color: var(--color-border-hi); }
.field-input-mono { font-family: var(--font-mono); font-size: 12.5px; }
.field-err { font-size: 11.5px; color: var(--color-danger); margin: 0; }
.field-hint { font-size: 11px; color: var(--color-fg-faint); margin: 4px 0 0; line-height: 1.55; }
.field-hint code { font-family: var(--font-mono); font-size: 10.5px; background: var(--color-surface-2); padding: 1px 4px; border-radius: 3px; }

/* Hardware probe card */
.hw-card {
  display: flex; flex-direction: column; gap: 6px;
  padding: 12px 14px;
  border-radius: var(--radius-lg);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  font-family: var(--font-mono);
  font-size: 12px;
}
.hw-row { display: flex; justify-content: space-between; gap: 12px; }
.hw-l { color: var(--color-fg-faint); }
.hw-v { color: var(--color-fg); text-align: right; }

/* Model dir editor */
.dirs-block { display: flex; flex-direction: column; gap: 6px; }
.dir-row { display: flex; gap: 6px; align-items: center; }
.dir-remove {
  background: transparent; border: 1px solid var(--color-border);
  border-radius: var(--radius); color: var(--color-fg-faint);
  cursor: pointer; width: 28px; height: 28px; flex-shrink: 0;
}
.dir-remove:hover { color: var(--color-danger); border-color: var(--color-danger); }
.dir-add {
  align-self: flex-start;
  background: transparent; border: 1px dashed var(--color-border);
  border-radius: var(--radius); padding: 5px 12px;
  color: var(--color-fg-muted); font-family: var(--font-mono); font-size: 11.5px;
  cursor: pointer;
}
.dir-add:hover { color: var(--hal0-accent); border-color: color-mix(in srgb, var(--hal0-accent) 45%, var(--color-border)); }

.disk-row {
  display: flex; justify-content: space-between;
  font-family: var(--font-mono); font-size: 12px;
  color: var(--color-fg-muted);
  padding-top: 4px;
}
.disk-v { color: var(--color-fg); }

/* ── Model list (chat picker, step 3) ──────────────────────────────────── */
.model-list { display: flex; flex-direction: column; gap: 8px; max-height: 50vh; overflow-y: auto; padding-right: 4px; }
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
.model-option.dim .model-option-name { color: var(--color-fg-muted); }
.model-option-inner { padding: 12px 14px; }
.model-option-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 4px; }
.model-option-name { font-family: var(--font-mono); font-weight: 600; color: var(--color-fg); font-size: 13.5px; }
.model-size-chip { font-family: var(--font-mono); font-size: 10.5px; padding: 2px 7px; border-radius: 999px; background: color-mix(in srgb, var(--hal0-accent) 14%, transparent); border: 1px solid color-mix(in srgb, var(--hal0-accent) 35%, transparent); color: var(--hal0-accent); text-transform: uppercase; letter-spacing: 0.04em; font-feature-settings: 'zero' 1, 'ss02' 1; }
.model-option-desc { font-size: 12px; color: var(--color-fg-muted); margin: 0 0 8px; }
.model-option-meta { display: flex; gap: 6px; flex-wrap: wrap; }
.meta-chip { font-family: var(--font-mono); font-size: 10.5px; padding: 2px 6px; border-radius: 4px; background: var(--color-surface-3); color: var(--color-fg-faint); border: 1px solid var(--color-border); }
.meta-chip-tag { opacity: 0.75; }
.meta-chip-ok { color: var(--hal0-accent); border-color: color-mix(in srgb, var(--hal0-accent) 45%, var(--color-border)); }
.meta-chip-tight { color: var(--color-warning, #f5b049); }

/* ── Capability cards (step 4) ─────────────────────────────────────────── */
.cap {
  border: 1px solid var(--color-border); border-radius: var(--radius-lg);
  padding: 12px 14px; display: flex; flex-direction: column; gap: 8px;
  background: var(--color-surface);
}
.cap-head { display: flex; align-items: center; justify-content: space-between; }
.cap-l { display: flex; flex-direction: column; }
.cap-name { font-family: var(--font-mono); font-weight: 600; color: var(--color-fg); font-size: 13px; }
.cap-name-sm { font-size: 12px; }
.cap-sub { font-size: 11px; color: var(--color-fg-faint); font-family: var(--font-mono); }
.cap-select { padding: 7px 10px; border-radius: var(--radius); border: 1px solid var(--color-border); background: var(--color-surface-2); color: var(--color-fg); font-family: var(--font-mono); font-size: 12px; }
.cap-sub-row { display: flex; align-items: center; justify-content: space-between; padding-top: 6px; border-top: 1px dashed var(--color-border); }
.cap-sub-name { font-family: var(--font-mono); font-size: 11.5px; color: var(--color-fg-muted); }
.cap-nested { border-top: 1px dashed var(--color-border); padding-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.disclosure { background: transparent; border: 0; color: var(--color-fg-muted); cursor: pointer; font-size: 11.5px; text-align: left; padding: 2px 0; font-family: var(--font-mono); display: flex; gap: 6px; align-items: center; }
.disclosure:hover { color: var(--color-fg); }
.custom-chevron { font-family: var(--font-mono); width: 12px; }

/* ── HF gated list (step 5) ────────────────────────────────────────────── */
.gated-list {
  list-style: none; padding: 8px 12px; margin: 0;
  background: var(--color-surface-2); border-radius: var(--radius);
  border: 1px solid var(--color-border);
  font-family: var(--font-mono); font-size: 12px;
  display: flex; flex-direction: column; gap: 4px;
}
.gated-list li { display: flex; justify-content: space-between; gap: 8px; }
.gated-name { color: var(--color-fg); }
.gated-id { color: var(--color-fg-faint); }

/* ── License list + totals (step 6) ────────────────────────────────────── */
.license-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 6px; }
.license-row {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  padding: 8px 12px; border: 1px solid var(--color-border);
  border-radius: var(--radius-lg); background: var(--color-surface-2);
}
.license-row-l { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.license-row-name { font-family: var(--font-mono); font-size: 12.5px; color: var(--color-fg); overflow: hidden; text-overflow: ellipsis; }
.license-row-size { font-family: var(--font-mono); font-size: 10.5px; color: var(--color-fg-faint); }
.license-row-r { display: flex; gap: 8px; align-items: center; flex-shrink: 0; }
.license-link { font-size: 11.5px; color: var(--hal0-accent); text-decoration: none; font-family: var(--font-mono); }
.license-link:hover { text-decoration: underline; }

.totals {
  display: flex; flex-direction: column; gap: 4px;
  font-family: var(--font-mono); font-size: 12px;
  padding: 8px 0;
}
.totals > div { display: flex; justify-content: space-between; }
.totals-l { color: var(--color-fg-muted); }
.totals-v { color: var(--color-fg); font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1; }
.totals-bad { color: var(--color-danger); }

.accept-label { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--color-fg-muted); cursor: pointer; padding-top: 4px; }

/* ── Pull rows (step 7) ────────────────────────────────────────────────── */
.pull-row {
  padding: 10px 12px; border: 1px solid var(--color-border); border-radius: var(--radius);
  background: var(--color-surface-2); display: flex; flex-direction: column; gap: 6px;
  transition: border-color 0.2s;
}
.pull-row-done { border-color: color-mix(in srgb, var(--hal0-accent) 45%, var(--color-border)); }
.pull-row-err  { border-color: color-mix(in srgb, var(--color-danger) 45%, var(--color-border)); }
.pull-head { display: flex; justify-content: space-between; align-items: center; font-family: var(--font-mono); font-size: 12px; }
.pull-name { color: var(--color-fg); overflow: hidden; text-overflow: ellipsis; }
.pull-state { color: var(--hal0-accent); font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1; }
.pull-row-err .pull-state { color: var(--color-danger); }
.pull-bar { height: 6px; background: var(--color-surface-3); border-radius: 3px; overflow: hidden; }
.pull-fill { height: 100%; background: var(--hal0-accent); border-radius: 3px; transition: width 0.3s ease; box-shadow: 0 0 10px color-mix(in srgb, var(--hal0-accent) 35%, transparent); }
.pull-meta { display: flex; justify-content: space-between; gap: 12px; font-family: var(--font-mono); font-size: 10.5px; color: var(--color-fg-faint); align-items: center; }
.pull-err { color: var(--color-danger); flex: 1; overflow: hidden; text-overflow: ellipsis; }

/* ── Done coda (step 8) ────────────────────────────────────────────────── */
.wizard-done { align-items: center; text-align: center; padding: 32px; }
.done-icon {
  width: 56px; height: 56px; border-radius: 50%;
  background: color-mix(in srgb, var(--hal0-accent) 14%, var(--color-surface));
  border: 2px solid var(--hal0-accent); color: var(--hal0-accent);
  font-size: 24px; display: grid; place-items: center;
  box-shadow: 0 0 32px color-mix(in srgb, var(--hal0-accent) 30%, transparent);
}
.done-title { font-size: 24px; font-weight: 600; color: var(--color-fg); margin: 0; letter-spacing: -0.02em; }
.done-desc  { font-size: 13.5px; color: var(--color-fg-muted); margin: 0; line-height: 1.6; }
.done-actions { display: flex; flex-direction: column; gap: 10px; width: 100%; align-items: center; }

/* ── Footers + buttons ─────────────────────────────────────────────────── */
.wizard-footer { padding-top: 4px; }
.wizard-footer-2 { display: flex; justify-content: space-between; align-items: center; }

.btn-primary { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 11px 22px; border-radius: var(--radius); background: var(--hal0-accent); color: #000; font-family: var(--font-mono); font-size: 13px; font-weight: 500; border: none; cursor: pointer; transition: background 0.15s, transform 0.05s; }
.btn-primary:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn-primary:active:not(:disabled) { transform: translateY(1px); }
.btn-primary:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-wide { width: 100%; }

.btn-ghost { padding: 9px 18px; border-radius: var(--radius); border: 1px solid var(--color-border); background: transparent; color: var(--color-fg-muted); font-family: var(--font-mono); font-size: 12px; cursor: pointer; transition: border-color 0.15s, color 0.15s; }
.btn-ghost:hover { border-color: var(--color-border-hi); color: var(--color-fg); }
.btn-ghost-sm { padding: 4px 10px; font-size: 11px; }

.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }

/* ── Step transitions ──────────────────────────────────────────────────── */
.wizard-fade-enter-active, .wizard-fade-leave-active { transition: opacity 0.18s ease, transform 0.18s ease; }
.wizard-fade-enter-from { opacity: 0; transform: translateY(4px); }
.wizard-fade-leave-to   { opacity: 0; transform: translateY(-4px); }

@media (prefers-reduced-motion: reduce) {
  .wizard-fade-enter-active, .wizard-fade-leave-active { transition: none; }
}
</style>
