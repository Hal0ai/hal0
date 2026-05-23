<script setup>
/**
 * FirstRun.vue — v2 bundle picker (slice #172).
 *
 * Replaces the v1 8-step linear wizard with the v0.3 design's
 * three-state machine (pick → confirm → progress).
 *
 * Source: /tmp/hal0-design/hal0-v2/project/dash/firstrun.jsx +
 * v0.3 css at /tmp/hal0-design-v3/dashboard.css (.fr-*, .tier-*, .dl-*).
 *
 * State + endpoint wiring lives in
 *   components/firstrun/useFirstRun.js
 * The view only sequences sub-components + wires the skip dialog +
 * conditional banner triggers.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useFirstRun } from '../components/firstrun/useFirstRun.js'
import { useTweaksStore } from '../stores/tweaks.js'
import { useBannerStore } from '../stores/banner.js'
import { resetFirstRunGuard } from '../router.js'
import Wordmark from '../components/Wordmark.vue'
import BannerStack from '../components/primitives/BannerStack.vue'
import BundleGrid from '../components/firstrun/BundleGrid.vue'
import BundleTable from '../components/firstrun/BundleTable.vue'
import InstallProgressRow from '../components/firstrun/InstallProgressRow.vue'
import SkipBundleDialog from '../components/firstrun/SkipBundleDialog.vue'

const router = useRouter()
const s      = useFirstRun()
const tweaks = useTweaksStore()
const banner = useBannerStore()

// Dialog state (local — does not belong in the composable since it
// only matters when the picker is visible).
const skipDialogOpen = ref(false)

// Layout: useTweaksStore.firstrunLayout ∈ { 'tiers' | 'wizard' }.
// 'tiers' = BundleGrid (default); 'wizard' = BundleTable (capability matrix).
const useMatrixLayout = computed(() => tweaks.firstrunLayout === 'wizard')

// ─── Banner triggers ─────────────────────────────────────────────
// The catalog entries live in stores/banner.js — we only show/dismiss
// them based on derived state. Don't ADD entries here.
watch(
  () => [s.isReEntered.value, s.ramTooLow.value, s.needsHfToken.value, s.view.value],
  () => {
    banner.toggle('fr-reentered', s.isReEntered.value && s.view.value === 'pick')
    banner.toggle('fr-ram-low',   s.ramTooLow.value   && s.view.value === 'pick')
    banner.toggle('hf-gated',     s.needsHfToken.value && s.view.value !== 'progress')
  },
  { immediate: true },
)

// ─── Skip flow ───────────────────────────────────────────────────
function openSkipDialog() { skipDialogOpen.value = true }
function cancelSkip()     { skipDialogOpen.value = false }
async function confirmSkip() {
  skipDialogOpen.value = false
  // Skip = mark install complete and route to /. Per the design spec,
  // the dashboard shows configure-buttons on the seeded slots.
  await s.markComplete()
  resetFirstRunGuard()
  router.push('/')
}

// ─── Progress → completion ───────────────────────────────────────
// When every row settles to terminal (done|failed) we don't auto-route
// — the user clicks "Open dashboard". We DO mark install complete + flip
// the post-install hero state once they navigate away.
async function openDashboard() {
  await s.markComplete()
  resetFirstRunGuard()
  // Light flag the dashboard can pick up to render a one-shot
  // "✓ Installed" hero. Cleared by /views/Dashboard.vue on first read.
  try { window.sessionStorage.setItem('hal0:firstrun:just-installed', '1') } catch { /* private mode */ }
  router.push('/')
}

// Auto-dismiss firstrun banners on unmount so other views don't see
// stale picker-scoped chrome.
onMounted(() => { /* state machine already mounted by useFirstRun() */ })
onUnmounted(() => {
  banner.dismiss('fr-reentered')
  banner.dismiss('fr-ram-low')
  banner.dismiss('hf-gated')
  s.dispose()
})

// ─── Detected-hardware copy ──────────────────────────────────────
const detectLine = computed(() => {
  const parts = []
  if (s.ramGb.value > 0) parts.push(`${s.ramGb.value} GB RAM`)
  if (s.gpuLabel.value && s.gpuLabel.value !== '—') parts.push(s.gpuLabel.value)
  if (s.npuPresent.value) parts.push('NPU')
  return parts.length ? `Detected: ${parts.join(' · ')}` : ''
})

// Toggle the NPU opt-in on the confirm card. v-model on a child
// produces a deep-tree warning when the parent stores it; bind via
// the composable's reactive ref directly.
function toggleNpu(e) { s.withNpu.value = e.target.checked }
</script>

<template>
  <div class="fr">
    <!-- Banner stack (scope=firstrun + global) -->
    <div class="fr-banners">
      <BannerStack scope="firstrun" />
    </div>

    <div class="fr-inner">
      <!-- ─── STATE: PICK ──────────────────────────────────────── -->
      <template v-if="s.view.value === 'pick'">
        <div class="fr-head">
          <div class="fr-eyebrow">
            <span class="blip" />FirstRun · install
          </div>
          <Wordmark size="text-5xl" class="fr-wordmark" aria-hidden="true" />
          <h1 class="fr-title">Welcome to <span class="accent">hal0</span></h1>
          <p class="fr-lede">
            Pick a starting configuration. You can customise any slot later —
            or skip and configure manually.
          </p>
          <div v-if="detectLine" class="fr-detect" data-testid="fr-detect">
            <span class="seg" v-if="s.ramGb.value">
              <span class="k">RAM</span><b>{{ s.ramGb.value }} GB</b>
            </span>
            <span class="seg" v-if="s.gpuLabel.value && s.gpuLabel.value !== '—'">
              <span class="k">GPU</span><b>{{ s.gpuLabel.value }}</b>
            </span>
            <span class="seg" v-if="s.npuPresent.value">
              <span class="k">NPU</span><b>detected</b><span class="ok">●</span>
            </span>
            <span class="seg" v-if="s.diskFreeGb.value">
              <span class="k">disk</span><b>{{ s.diskFreeGb.value }} GB</b> free
            </span>
          </div>
        </div>

        <div v-if="s.loading.value" class="fr-loading mono">Probing hardware…</div>

        <template v-else>
          <!-- Layout variants — flipped via useTweaksStore.firstrunLayout -->
          <BundleGrid
            v-if="!useMatrixLayout"
            :bundles="s.bundles.value"
            @pick="s.pickBundle"
          />
          <BundleTable
            v-else
            :bundles="s.bundles.value"
            :ram-gb="s.ramGb.value"
            @pick="s.pickBundle"
          />

          <!-- Pre-built kits (only when host ≥100 GB RAM) -->
          <template v-if="s.ramGb.value >= 100">
            <h3 class="fr-section-label">Pre-built kits</h3>
            <div class="kit" data-testid="lmx-kit">
              <div class="kit-main">
                <div class="kit-eyebrow">AMD-curated · vendor-blessed</div>
                <div class="kit-name">LMX-Omni-52B-Halo</div>
                <div class="kit-spec">
                  ≥ 100 GB unified RAM Strix Halo · NPU trio · 4 slots ready out of the box
                </div>
                <div class="kit-models">
                  <span class="chip">Qwen3.6-35B</span>
                  <span class="chip">Whisper-Large</span>
                  <span class="chip">kokoro</span>
                  <span class="chip">Flux-2-Klein-9B</span>
                </div>
              </div>
              <div class="kit-side">
                <div class="sz mono">~75<span class="u">GB</span></div>
                <button type="button" class="btn lg" @click="s.pickBundle('max')">Install LMX kit</button>
              </div>
            </div>
          </template>

          <div class="fr-skip-row">
            <button
              type="button"
              class="fr-skip"
              data-testid="fr-skip"
              @click="openSkipDialog"
            >Skip — configure manually</button>
          </div>
        </template>
      </template>

      <!-- ─── STATE: CONFIRM ──────────────────────────────────── -->
      <template v-else-if="s.view.value === 'confirm' && s.currentBundle.value">
        <span
          class="fr-confirm-back mono"
          role="button"
          tabindex="0"
          data-testid="fr-confirm-back"
          @click="s.backToPicker"
          @keydown.enter="s.backToPicker"
        >← back to picker</span>

        <div class="fr-confirm-h">
          <h2>hal0-{{ s.currentBundle.value.name }}</h2>
          <span class="sub">
            {{ s.currentBundle.value.ram }} GB+ unified ·
            ~{{ s.currentBundle.value.sizeGB }} GB download ·
            est {{ Math.max(2, Math.round(s.currentBundle.value.sizeGB / 3)) }} min
          </span>
        </div>
        <p class="fr-confirm-sub">{{ s.currentBundle.value.desc }} You can change any slot after install.</p>

        <!-- Per-slot install list -->
        <div class="fr-confirm-card" data-testid="fr-install-list">
          <div class="fr-confirm-card-h mono">
            <span>What gets installed</span>
            <b>{{ s.currentDetails.value?.models.length || 0 }} slots</b>
            <span style="color: var(--fg-4)">· ~{{ s.aggregateSizeGb.value.toFixed(1) }} GB total</span>
            <span class="right">capabilities.toml</span>
          </div>
          <div
            v-for="row in s.currentDetails.value?.models || []"
            :key="row.slot"
            class="fr-confirm-row"
            :data-slot="row.slot"
          >
            <span class="nm">{{ row.slot }}</span>
            <span class="ml">{{ row.model }}</span>
            <span class="sz">{{ row.size }}</span>
            <span class="tag">
              <span
                v-for="(t, i) in row.tag.split(' ')"
                :key="i"
                class="chip"
                :class="{
                  'chip-amber-outlined': t === 'default',
                  'chip-cpu': t === 'cpu',
                }"
              >{{ t }}</span>
            </span>
          </div>
        </div>

        <!-- Optional NPU trio toggle (only when NPU detected + tier has NPU rows) -->
        <div
          v-if="s.npuPresent.value && (s.currentDetails.value?.npu?.length || 0) > 0"
          class="fr-confirm-card"
          data-testid="fr-npu-card"
        >
          <div class="fr-confirm-card-h mono" style="justify-content: space-between">
            <div style="display: flex; align-items: center; gap: 14px">
              <span class="npu-chip">NPU</span>
              <span>FLM trio</span>
              <span style="color: var(--fg-4)">· optional</span>
            </div>
            <label class="npu-toggle mono">
              <input
                type="checkbox"
                :checked="s.withNpu.value"
                data-testid="fr-npu-toggle"
                @change="toggleNpu"
              />
              <span>Enable on install</span>
            </label>
          </div>
          <div
            v-for="row in s.currentDetails.value?.npu || []"
            :key="row.slot"
            class="fr-confirm-row"
            :class="{ 'fr-confirm-row-faint': !s.withNpu.value }"
            :data-npu-slot="row.slot"
          >
            <span class="nm npu">{{ row.slot }}</span>
            <span class="ml">{{ row.model }}</span>
            <span class="sz">{{ row.size }}</span>
            <span class="tag">
              <span v-for="(t, i) in row.tag.split(' ')" :key="i" class="chip">{{ t }}</span>
            </span>
          </div>
          <div class="fr-confirm-foot">
            <span>~2 GB NPU memory · ~14s swap penalty on chat-model change · stt-npu + embed-npu are passengers</span>
          </div>
        </div>

        <!-- Notes — license + HF_TOKEN warning -->
        <div class="card notes-card">
          <div class="notes-h mono">Notes</div>
          <ul class="notes-ul">
            <li>By installing, you accept each model's upstream license. hal0 does not redistribute weights — files come straight from Hugging Face.</li>
            <li v-if="s.needsHfToken.value" class="notes-warn">
              One or more models are gated on Hugging Face. Set <span class="mono">HF_TOKEN</span> in
              <strong>Settings → Storage</strong> before install, or this bundle will fail to pull.
            </li>
            <li v-else>HF_TOKEN is not required for this bundle. Configure later in Settings to enable gated repos.</li>
            <li v-if="!s.fitsDisk.value" class="notes-err">
              Not enough disk: needs ~{{ s.aggregateSizeGb.value.toFixed(1) }} GB,
              only {{ s.diskFreeGb.value }} GB free on first model dir.
            </li>
          </ul>
        </div>

        <div class="fr-actions">
          <button type="button" class="btn ghost lg" @click="s.backToPicker">Cancel</button>
          <button
            type="button"
            class="btn lg"
            data-testid="fr-install-btn"
            :disabled="!s.fitsDisk.value"
            @click="s.startInstall"
          >Install hal0-{{ s.currentBundle.value.name }}</button>
        </div>
      </template>

      <!-- ─── STATE: PROGRESS ─────────────────────────────────── -->
      <template v-else-if="s.view.value === 'progress'">
        <div class="fr-prog-h">
          <h2>
            <template v-if="s.pull.done && !s.pull.error">✓ Installed</template>
            <template v-else>Installing hal0-{{ s.currentBundle.value?.name }}…</template>
          </h2>
          <span class="meta">
            ~{{ s.aggregateSizeGb.value.toFixed(1) }} GB total · downloads continue in background
          </span>
        </div>

        <div class="fr-prog-list" data-testid="fr-prog-list">
          <InstallProgressRow
            v-for="item in s.pull.items"
            :key="item.key"
            :item="item"
            @retry="s.retryItem"
            @skip="s.skipItem"
          />
        </div>

        <div v-if="s.pull.error" class="fr-prog-err mono">{{ s.pull.error }}</div>

        <div class="fr-actions" style="justify-content: space-between">
          <button
            type="button"
            class="btn ghost lg"
            :disabled="s.pull.done"
            data-testid="fr-pause-all"
            @click="s.pauseAll"
          >Pause all</button>
          <button
            type="button"
            class="btn lg"
            data-testid="fr-open-dashboard"
            @click="openDashboard"
          >Open dashboard</button>
        </div>
      </template>
    </div>

    <!-- Skip confirm dialog (always present, opens on demand) -->
    <SkipBundleDialog
      :open="skipDialogOpen"
      :on-cancel="cancelSkip"
      :on-confirm="confirmSkip"
    />
  </div>
</template>

<style scoped>
.fr {
  min-height: calc(100vh - var(--topbar-h, 0px) - var(--footer-h, 0px));
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 56px 32px 80px;
  background: var(--bg);
}
.fr-banners { width: 100%; max-width: 1240px; }
.fr-inner   { width: 100%; max-width: 1240px; }
.fr-loading { padding: 48px; text-align: center; color: var(--fg-3); font-size: 13px; }

/* ─── PICK state ─────────────────────────────────────────────── */
.fr-head { text-align: center; margin-bottom: 48px; }
.fr-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
  border: 1px solid var(--accent-line);
  background: var(--accent-soft);
  border-radius: 999px;
  font-family: var(--jbm);
  font-size: 10px;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 22px;
}
.fr-eyebrow .blip {
  width: 5px; height: 5px; background: var(--accent); border-radius: 50%;
  animation: fr-pulse 1.5s ease-in-out infinite;
}
@keyframes fr-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.4; }
}
.fr-wordmark { display: flex; justify-content: center; margin: 0 auto 18px; }
.fr-title {
  font-family: var(--jbm);
  font-weight: 500;
  font-size: clamp(36px, 4vw, 52px);
  letter-spacing: -0.03em;
  line-height: 1.05;
  margin: 0 0 16px;
}
.fr-title .accent { color: var(--accent); }
.fr-lede {
  font-size: 16px;
  color: var(--fg-2);
  max-width: 620px;
  margin: 0 auto 22px;
  line-height: 1.55;
  text-wrap: pretty;
}

.fr-detect {
  display: inline-flex;
  flex-wrap: wrap;
  font-family: var(--jbm);
  font-size: 12px;
  color: var(--fg-3);
  border: 1px solid var(--line);
  border-radius: var(--rad);
  background: var(--bg-1);
  overflow: hidden;
}
.fr-detect .seg {
  padding: 7px 14px;
  border-right: 1px solid var(--line-soft);
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.fr-detect .seg:last-child { border-right: none; }
.fr-detect .seg .k { color: var(--fg-5); }
.fr-detect .seg b  { color: var(--fg); font-weight: 500; }
.fr-detect .ok     { color: var(--ok); }

.fr-section-label {
  font-family: var(--jbm);
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--fg-4);
  margin: 28px 0 16px;
  display: flex;
  align-items: center;
  gap: 16px;
}
.fr-section-label::after { content: ''; flex: 1; height: 1px; background: var(--line-soft); }

/* LMX kit */
.kit {
  background: linear-gradient(135deg, var(--bg-1) 0%, color-mix(in oklab, var(--accent-soft) 30%, var(--bg-1)) 100%);
  border: 1px solid var(--accent-line);
  border-radius: var(--rad-lg);
  padding: 24px 28px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 24px;
  align-items: center;
  margin-bottom: 40px;
  position: relative;
  overflow: hidden;
}
.kit::before {
  content: ''; position: absolute; inset: 0;
  background: radial-gradient(circle at 80% 50%, var(--accent-soft), transparent 60%);
  pointer-events: none;
}
.kit-main { position: relative; }
.kit-eyebrow { font-family: var(--jbm); font-size: 10px; color: var(--accent); text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 8px; }
.kit-name { font-family: var(--jbm); font-size: 22px; font-weight: 500; letter-spacing: -0.02em; margin-bottom: 8px; }
.kit-spec { font-family: var(--jbm); font-size: 12px; color: var(--fg-3); margin-bottom: 12px; }
.kit-models { display: flex; flex-wrap: wrap; gap: 6px; }
.kit-side { position: relative; text-align: right; display: flex; flex-direction: column; gap: 8px; align-items: flex-end; }
.kit-side .sz { font-family: var(--jbm); font-size: 24px; font-weight: 500; color: var(--fg); letter-spacing: -0.02em; }
.kit-side .sz .u { color: var(--accent); font-size: 14px; margin-left: 2px; }

.fr-skip-row { display: flex; justify-content: center; gap: 24px; align-items: center; margin-top: 16px; }
.fr-skip {
  background: transparent;
  border: none;
  font-family: var(--jbm);
  font-size: 12px;
  color: var(--fg-3);
  cursor: pointer;
  padding: 6px 12px;
  border-bottom: 1px dashed var(--line);
}
.fr-skip:hover { color: var(--accent); border-color: var(--accent-line); }

/* ─── CONFIRM state ──────────────────────────────────────────── */
.fr-confirm-back {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--jbm);
  font-size: 12px;
  color: var(--fg-3);
  cursor: pointer;
  margin-bottom: 20px;
  outline: none;
}
.fr-confirm-back:hover, .fr-confirm-back:focus-visible { color: var(--accent); }

.fr-confirm-h { display: flex; align-items: baseline; gap: 14px; margin-bottom: 6px; }
.fr-confirm-h h2 { font-family: var(--jbm); font-size: 32px; font-weight: 500; margin: 0; letter-spacing: -0.025em; }
.fr-confirm-h .sub { font-family: var(--jbm); font-size: 13px; color: var(--fg-3); }
.fr-confirm-sub { font-family: var(--jbm); font-size: 12px; color: var(--fg-3); margin-bottom: 28px; }

.fr-confirm-card {
  border: 1px solid var(--line);
  border-radius: var(--rad-lg);
  background: var(--bg-1);
  overflow: hidden;
  margin-bottom: 20px;
}
.fr-confirm-card-h {
  padding: 14px 18px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  gap: 14px;
  font-family: var(--jbm);
  font-size: 12px;
  color: var(--fg-3);
  background: var(--bg);
}
.fr-confirm-card-h b { color: var(--fg); font-weight: 500; }
.fr-confirm-card-h .right { margin-left: auto; }

.fr-confirm-row {
  display: grid;
  grid-template-columns: 100px 1fr 120px 120px;
  gap: 14px;
  align-items: center;
  padding: 11px 18px;
  border-bottom: 1px solid var(--line-soft);
  font-family: var(--jbm);
  font-size: 12px;
}
.fr-confirm-row:last-child { border-bottom: none; }
.fr-confirm-row .nm { color: var(--accent); }
.fr-confirm-row .nm.npu { color: var(--dev-npu, #c896ff); }
.fr-confirm-row .ml { color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fr-confirm-row .sz { color: var(--fg-2); text-align: right; }
.fr-confirm-row .tag { color: var(--fg-3); text-align: right; display: flex; gap: 5px; justify-content: flex-end; }
.fr-confirm-row-faint { opacity: 0.55; }

.fr-confirm-foot {
  padding: 14px 18px;
  background: var(--bg);
  border-top: 1px solid var(--line);
  font-family: var(--jbm);
  font-size: 11px;
  color: var(--fg-4);
  display: flex;
  align-items: center;
  gap: 10px;
}

.npu-chip {
  width: 28px; height: 18px;
  border-radius: 3px;
  border: 1px solid rgba(200, 150, 255, 0.40);
  background: rgba(200, 150, 255, 0.08);
  color: var(--dev-npu, #c896ff);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 9px;
  letter-spacing: 0.05em;
  font-weight: 600;
}
.npu-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: var(--fg-2);
}
.npu-toggle input { accent-color: var(--accent); }

.notes-card {
  padding: 16px;
  font-size: 12.5px;
  color: var(--fg-3);
  margin-bottom: 24px;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--rad-lg);
}
.notes-h {
  font-size: 10px;
  color: var(--fg-4);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 8px;
}
.notes-ul { margin: 0; padding-left: 18px; line-height: 1.7; }
.notes-warn { color: var(--warn); }
.notes-err  { color: var(--err); }
.notes-ul .mono { font-family: var(--jbm); color: var(--fg-2); }

.fr-actions {
  display: flex;
  gap: 12px;
  justify-content: flex-end;
  padding-top: 12px;
}

/* ─── PROGRESS state ─────────────────────────────────────────── */
.fr-prog-h {
  display: flex;
  align-items: baseline;
  gap: 14px;
  margin-bottom: 22px;
}
.fr-prog-h h2 {
  font-family: var(--jbm);
  font-size: 28px;
  font-weight: 500;
  margin: 0;
  letter-spacing: -0.025em;
}
.fr-prog-h .meta { font-family: var(--jbm); font-size: 12px; color: var(--fg-3); }

.fr-prog-list {
  border: 1px solid var(--line);
  border-radius: var(--rad-lg);
  background: var(--bg-1);
  overflow: hidden;
  margin-bottom: 22px;
}

.fr-prog-err {
  margin-bottom: 16px;
  padding: 10px 14px;
  border: 1px solid var(--err-line);
  background: var(--err-soft);
  border-radius: var(--rad);
  color: var(--err);
  font-size: 12px;
}

/* ─── Chip shim (until /primitives is reachable from this scope) ─── */
.chip {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--bg-3);
  color: var(--fg-3);
  font-family: var(--jbm);
  font-size: 10px;
  letter-spacing: 0.04em;
  border: 1px solid var(--line);
}
.chip-amber-outlined { color: var(--accent); border-color: var(--accent-line); background: transparent; }
.chip-cpu { color: var(--fg-3); }
</style>
