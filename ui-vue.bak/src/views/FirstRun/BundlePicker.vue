<script setup>
/**
 * BundlePicker.vue — First-run bundle picker (ADR-0010 / plan §8 / PR-17).
 *
 * Shown on first dashboard load when capabilities.toml lands empty and
 * the bundle-chosen marker is absent. Five locked tiers:
 *
 *   hal0-Lite (16 GB+) · hal0-Default (32 GB+) · hal0-Pro (64 GB+) ·
 *   hal0-Max (100 GB+) · LMX-Omni-52B-Halo (AMD-curated kit)
 *
 * Tiers whose min_ram_gb exceeds the host's detected RAM are greyed
 * with an explanatory tooltip — the operator can still see they exist
 * (no silent omission) but the card isn't clickable.
 *
 * Picking a tier opens a confirmation modal that surfaces:
 *   - bundle contents (every model with its slot + size)
 *   - estimated total download size
 *   - NPU opt-in toggle (Pro/Max only — manifest's npu_trio_shown)
 *
 * Confirm → POST /api/bundles/{name} { npu_opt_in } → redirect to /
 * Skip → GET /api/bundles/skip → redirect to /
 *
 * Wired via ui/src/router.js + a beforeEach guard that hits
 * /api/bundles to decide between /bundles and the regular dashboard
 * on a fresh load.
 */
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../composables/useApi.js'
import { useToastsStore } from '../../stores/toasts.js'
import { resetBundlePickerGuard } from '../../router.js'
import Wordmark from '../../components/Wordmark.vue'

const router = useRouter()
const toasts = useToastsStore()

const loading = ref(true)
const error = ref('')
const tiers = ref([])
const eligible = ref([])
const hostRamGb = ref(0)

// Modal state
const showModal = ref(false)
const selectedTier = ref(null)
const npuOptIn = ref(false)
const submitting = ref(false)

const hal0Tiers = computed(() => tiers.value.filter((t) => t.vendor !== 'amd'))
const vendorKits = computed(() => tiers.value.filter((t) => t.vendor === 'amd'))

function isEligible(tier) {
  return eligible.value.includes(tier.name)
}

function ramTooltip(tier) {
  if (isEligible(tier)) return ''
  if (hostRamGb.value > 0) {
    return `Needs ${tier.min_ram_gb} GB; this host has ~${hostRamGb.value} GB.`
  }
  return `Needs ${tier.min_ram_gb} GB; host RAM could not be probed.`
}

function fmtGb(n) {
  if (!n && n !== 0) return '—'
  return `${Number(n).toFixed(1)} GB`
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const body = await api('/api/bundles')
    tiers.value = body.tiers || []
    eligible.value = body.eligible || []
    hostRamGb.value = body.host_ram_gb || 0
    // If the marker is already present (browser back, double-load),
    // skip straight to the dashboard. The router guard would catch
    // this on the next navigation, but the picker view rendering at
    // all would be a confusing flash.
    if (body.picker_pending === false) {
      router.replace('/')
    }
  } catch (e) {
    error.value = e?.message || 'Could not load bundle catalogue.'
  } finally {
    loading.value = false
  }
}

function openTier(tier) {
  if (!isEligible(tier)) return
  selectedTier.value = tier
  npuOptIn.value = !!tier.npu_trio_optin
  showModal.value = true
}

function closeModal() {
  if (submitting.value) return
  showModal.value = false
  selectedTier.value = null
}

async function confirmTier() {
  if (!selectedTier.value) return
  submitting.value = true
  try {
    const body = selectedTier.value.npu_trio_shown
      ? { npu_opt_in: !!npuOptIn.value }
      : {}
    await api(`/api/bundles/${encodeURIComponent(selectedTier.value.name)}`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    toasts.info(`Installing ${selectedTier.value.display_label || selectedTier.value.name} — pulling models in the background.`)
    resetBundlePickerGuard()
    router.replace('/')
  } catch (e) {
    toasts.error(e?.message || 'Could not apply bundle.')
    submitting.value = false
  }
}

async function skipPicker() {
  submitting.value = true
  try {
    await api('/api/bundles/skip')
    toasts.info('Skipped — configure capabilities manually from the dashboard.')
    resetBundlePickerGuard()
    router.replace('/')
  } catch (e) {
    toasts.error(e?.message || 'Could not record skip.')
    submitting.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="picker-page">
    <div class="picker-card">
      <div class="picker-head">
        <div class="picker-glow" aria-hidden="true"></div>
        <span class="picker-eyebrow">
          <span class="picker-eyebrow-dot" aria-hidden="true"></span>
          First run · bundle picker
        </span>
        <Wordmark size="text-5xl" class="picker-mark" aria-hidden="true" />
        <h1 class="picker-title">Welcome to hal0</h1>
        <p class="picker-sub">
          Pick a starting configuration. You can customise any slot afterwards.
          Or skip to configure manually.
        </p>
      </div>

      <div v-if="loading" class="picker-body picker-loading">
        Loading bundle catalogue…
      </div>

      <div v-else-if="error" class="picker-body picker-error">
        <p>{{ error }}</p>
        <button class="btn-ghost" type="button" @click="refresh">Retry</button>
      </div>

      <div v-else class="picker-body">
        <section class="tier-section" aria-label="Hardware-anchored tiers">
          <header class="tier-section-head">
            <h2 class="tier-section-title">Hardware-anchored tiers</h2>
            <p v-if="hostRamGb > 0" class="tier-section-sub">
              Detected host RAM: {{ hostRamGb }} GB
            </p>
          </header>
          <div class="tier-grid" role="list">
            <button
              v-for="tier in hal0Tiers"
              :key="tier.name"
              type="button"
              role="listitem"
              class="tier-card"
              :class="{ 'tier-card-disabled': !isEligible(tier) }"
              :disabled="!isEligible(tier)"
              :title="ramTooltip(tier)"
              :aria-label="`${tier.display_label || tier.name} — minimum ${tier.min_ram_gb} GB`"
              :data-tier-name="tier.name"
              :data-tier-eligible="isEligible(tier) ? 'true' : 'false'"
              @click="openTier(tier)"
            >
              <div class="tier-card-head">
                <span class="tier-card-name">{{ tier.display_label || tier.name }}</span>
                <span class="tier-card-ram">{{ tier.min_ram_gb }} GB+</span>
              </div>
              <p class="tier-card-sub">{{ tier.display_subtitle }}</p>
              <div class="tier-card-meta">
                <span class="tier-card-chip">{{ fmtGb(tier.total_size_gb) }} total</span>
                <span v-if="tier.npu_trio_shown" class="tier-card-chip tier-card-chip-npu">NPU trio</span>
                <span v-if="!isEligible(tier)" class="tier-card-chip tier-card-chip-warn">needs more RAM</span>
              </div>
            </button>
          </div>
        </section>

        <section v-if="vendorKits.length > 0" class="tier-section" aria-label="Pre-built kits">
          <header class="tier-section-head">
            <h2 class="tier-section-title">Pre-built kits</h2>
            <p class="tier-section-sub">Vendor-curated bundles for specific hardware.</p>
          </header>
          <div class="tier-grid tier-grid-kits" role="list">
            <button
              v-for="tier in vendorKits"
              :key="tier.name"
              type="button"
              role="listitem"
              class="tier-card tier-card-kit"
              :class="{ 'tier-card-disabled': !isEligible(tier) }"
              :disabled="!isEligible(tier)"
              :title="ramTooltip(tier)"
              :aria-label="`${tier.display_label || tier.name} — minimum ${tier.min_ram_gb} GB`"
              :data-tier-name="tier.name"
              :data-tier-eligible="isEligible(tier) ? 'true' : 'false'"
              @click="openTier(tier)"
            >
              <div class="tier-card-head">
                <span class="tier-card-name">{{ tier.display_label || tier.name }}</span>
                <span class="tier-card-chip tier-card-chip-vendor">AMD</span>
              </div>
              <p class="tier-card-sub">{{ tier.display_subtitle }}</p>
              <div class="tier-card-meta">
                <span class="tier-card-chip">{{ fmtGb(tier.total_size_gb) }} total</span>
                <span v-if="!isEligible(tier)" class="tier-card-chip tier-card-chip-warn">needs more RAM</span>
              </div>
            </button>
          </div>
        </section>

        <div class="picker-footer">
          <button
            class="btn-ghost"
            type="button"
            :disabled="submitting"
            data-skip-bundle
            @click="skipPicker"
          >Skip — configure manually</button>
        </div>
      </div>
    </div>

    <!-- ── Confirmation modal ────────────────────────────────────── -->
    <transition name="picker-fade">
      <div
        v-if="showModal && selectedTier"
        class="picker-modal-backdrop"
        role="dialog"
        aria-modal="true"
        :aria-label="`Confirm ${selectedTier.name}`"
        @click.self="closeModal"
      >
        <div class="picker-modal">
          <h3 class="picker-modal-title">{{ selectedTier.display_label || selectedTier.name }}</h3>
          <p class="picker-modal-sub">{{ selectedTier.display_subtitle }}</p>

          <ul class="picker-modal-list">
            <li v-if="selectedTier.primary" class="picker-modal-row">
              <span class="picker-modal-slot">chat.primary</span>
              <span class="picker-modal-model">{{ selectedTier.primary.model_name }}</span>
              <span class="picker-modal-size">{{ fmtGb(selectedTier.primary.size_gb) }}</span>
            </li>
            <li v-if="selectedTier.coder" class="picker-modal-row">
              <span class="picker-modal-slot">chat.coder</span>
              <span class="picker-modal-model">{{ selectedTier.coder.model_name }}<span v-if="selectedTier.coder.lru" class="picker-modal-lru"> · LRU</span></span>
              <span class="picker-modal-size">{{ fmtGb(selectedTier.coder.size_gb) }}</span>
            </li>
            <li v-for="entry in selectedTier.aux" :key="entry.slot + entry.model_name" class="picker-modal-row">
              <span class="picker-modal-slot">{{ entry.slot }}</span>
              <span class="picker-modal-model">{{ entry.model_name }}<span v-if="entry.lru" class="picker-modal-lru"> · LRU</span></span>
              <span class="picker-modal-size">{{ fmtGb(entry.size_gb) }}</span>
            </li>
          </ul>

          <div class="picker-modal-total">
            <span class="picker-modal-total-label">Total download</span>
            <span class="picker-modal-total-value">{{ fmtGb(selectedTier.total_size_gb) }}</span>
          </div>

          <label v-if="selectedTier.npu_trio_shown" class="picker-modal-npu">
            <input
              type="checkbox"
              v-model="npuOptIn"
              data-npu-opt-in
            />
            <span>
              Also configure the NPU trio (FLM agent + STT-NPU + embed-NPU).
              The slots will be defined; you can enable them later from the Slots page.
            </span>
          </label>

          <div class="picker-modal-actions">
            <button
              class="btn-ghost"
              type="button"
              :disabled="submitting"
              @click="closeModal"
            >Cancel</button>
            <button
              class="btn-primary"
              type="button"
              :disabled="submitting"
              data-confirm-bundle
              @click="confirmTier"
            >{{ submitting ? 'Applying…' : 'Confirm + install' }}</button>
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.picker-page {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: 32px 16px;
  background: var(--hal0-bg);
  overflow: hidden;
}
.picker-page::before {
  content: '';
  position: absolute;
  inset: -20% -10% auto -10%;
  height: 60%;
  pointer-events: none;
  background: radial-gradient(ellipse at center, var(--hal0-accent-glow), transparent 70%);
  z-index: 0;
}

.picker-card {
  position: relative;
  z-index: 1;
  background: var(--hal0-bg-elevated);
  border: 1px solid var(--hal0-border);
  border-radius: var(--radius-xl);
  width: min(820px, 100%);
  overflow: hidden;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.45);
}

.picker-head {
  position: relative;
  text-align: center;
  padding: 36px 32px 24px;
  border-bottom: 1px solid var(--hal0-border);
}
.picker-glow {
  position: absolute;
  inset: auto 0 -32px 0;
  height: 64px;
  pointer-events: none;
  background: radial-gradient(ellipse at center, var(--hal0-accent-glow), transparent 70%);
}
.picker-eyebrow {
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
.picker-eyebrow-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--hal0-accent);
  box-shadow: 0 0 8px var(--hal0-accent);
}
.picker-mark {
  display: flex; justify-content: center; margin: 0 auto 18px;
  filter: drop-shadow(0 0 24px color-mix(in srgb, var(--hal0-accent) 30%, transparent));
}
.picker-title { font-size: 28px; font-weight: 600; color: var(--hal0-fg); margin: 0 0 8px; letter-spacing: -0.02em; }
.picker-sub   { font-size: 14px; color: var(--hal0-fg-muted); margin: 0; line-height: 1.5; }

.picker-body { padding: 24px 32px; display: flex; flex-direction: column; gap: 24px; }
.picker-loading, .picker-error { text-align: center; padding: 32px; color: var(--color-fg-muted); font-size: 13px; }
.picker-error { color: var(--color-danger); display: flex; flex-direction: column; align-items: center; gap: 12px; }

.tier-section { display: flex; flex-direction: column; gap: 12px; }
.tier-section-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.tier-section-title { font-size: 14px; font-weight: 600; color: var(--color-fg); margin: 0; letter-spacing: 0.02em; }
.tier-section-sub { font-size: 11.5px; color: var(--color-fg-faint); margin: 0; font-family: var(--font-mono); }

.tier-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.tier-grid-kits { grid-template-columns: 1fr; }
@media (min-width: 720px) {
  .tier-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .tier-grid-kits { grid-template-columns: 1fr; }
}

.tier-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 14px;
  text-align: left;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  cursor: pointer;
  font-family: inherit;
  color: inherit;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.05s;
}
.tier-card:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--hal0-accent) 45%, var(--color-border));
  box-shadow: inset 3px 0 0 var(--hal0-accent);
}
.tier-card:active:not(:disabled) { transform: translateY(1px); }
.tier-card-disabled { opacity: 0.45; cursor: not-allowed; }

.tier-card-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.tier-card-name { font-family: var(--font-mono); font-weight: 600; color: var(--color-fg); font-size: 13.5px; }
.tier-card-ram { font-family: var(--font-mono); font-size: 11px; color: var(--hal0-accent); }
.tier-card-sub { font-size: 12px; color: var(--color-fg-muted); margin: 0; line-height: 1.5; min-height: 2.6em; }
.tier-card-meta { display: flex; flex-wrap: wrap; gap: 6px; }
.tier-card-chip {
  font-family: var(--font-mono);
  font-size: 10.5px;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--color-surface-3);
  color: var(--color-fg-faint);
  border: 1px solid var(--color-border);
}
.tier-card-chip-npu { color: var(--hal0-accent); border-color: color-mix(in srgb, var(--hal0-accent) 45%, var(--color-border)); }
.tier-card-chip-warn { color: var(--color-warning, #f5b049); }
.tier-card-chip-vendor { color: var(--color-fg); }
.tier-card-kit { background: var(--color-surface-2); }

.picker-footer { display: flex; justify-content: center; padding-top: 8px; }

.picker-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.picker-modal {
  background: var(--hal0-bg-elevated);
  border: 1px solid var(--hal0-border);
  border-radius: var(--radius-xl);
  width: min(520px, 100%);
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-height: 90vh;
  overflow-y: auto;
}
.picker-modal-title { font-size: 18px; font-weight: 600; color: var(--color-fg); margin: 0; }
.picker-modal-sub  { font-size: 12.5px; color: var(--color-fg-muted); margin: 0; line-height: 1.5; }
.picker-modal-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 4px; }
.picker-modal-row {
  display: grid;
  grid-template-columns: minmax(80px, 1fr) 2fr auto;
  gap: 8px;
  align-items: baseline;
  padding: 4px 8px;
  border-radius: 6px;
  background: var(--color-surface-2);
  font-family: var(--font-mono);
  font-size: 11.5px;
}
.picker-modal-slot { color: var(--hal0-accent); }
.picker-modal-model { color: var(--color-fg); overflow: hidden; text-overflow: ellipsis; }
.picker-modal-lru { color: var(--color-fg-faint); }
.picker-modal-size { color: var(--color-fg-faint); justify-self: end; }
.picker-modal-total {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 6px 0;
  border-top: 1px solid var(--color-border);
  font-family: var(--font-mono);
  font-size: 12.5px;
}
.picker-modal-total-label { color: var(--color-fg-muted); }
.picker-modal-total-value { color: var(--hal0-accent); }
.picker-modal-npu {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  font-size: 12px;
  color: var(--color-fg-muted);
  line-height: 1.5;
  padding: 8px 10px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius);
}
.picker-modal-npu input { margin-top: 2px; }
.picker-modal-actions { display: flex; justify-content: flex-end; gap: 8px; padding-top: 8px; }

.btn-primary {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  padding: 9px 18px; border-radius: var(--radius);
  background: var(--hal0-accent); color: #000;
  font-family: var(--font-mono); font-size: 12.5px; font-weight: 500;
  border: none; cursor: pointer;
  transition: background 0.15s, transform 0.05s;
}
.btn-primary:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn-primary:active:not(:disabled) { transform: translateY(1px); }
.btn-primary:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-ghost {
  padding: 8px 16px; border-radius: var(--radius);
  border: 1px solid var(--color-border); background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono); font-size: 12px; cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.btn-ghost:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }
.btn-ghost:disabled { opacity: 0.45; cursor: not-allowed; }

.picker-fade-enter-active, .picker-fade-leave-active { transition: opacity 0.15s ease; }
.picker-fade-enter-from, .picker-fade-leave-to { opacity: 0; }
</style>
