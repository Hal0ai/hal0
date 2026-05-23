<script setup>
/**
 * Backends.vue — v2 backends (renamed from /providers in v2 IA).
 *
 * Mirrors the React `BackendsView` in
 *   /tmp/hal0-design/hal0-v2/project/dash/extras.jsx (lines 113–195).
 *
 * Top row: Lemonade self-card (version / pinned / sha-verified /
 * channel) + Logs / Restart / Update buttons. Below: backend table
 * keyed by `id`. State chip per row; install/uninstall actions wire
 * to the modal trio (Install / Uninstall / FLM-deb).
 */
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useBackendsStore } from '../stores/backends.js'
import { useLemonadeStore } from '../stores/lemonade.js'
import { useToastStore } from '../stores/toast.js'
import PageHeader from '../components/PageHeader.vue'
import BannerStack from '../components/primitives/BannerStack.vue'
import BackendRow from '../components/backends/BackendRow.vue'
import BackendInstallModal from '../components/backends/BackendInstallModal.vue'
import BackendUninstallModal from '../components/backends/BackendUninstallModal.vue'
import FlmDebGuideModal from '../components/backends/FlmDebGuideModal.vue'

const router = useRouter()
const backends = useBackendsStore()
const lemonade = useLemonadeStore()
const toasts = useToastStore()

const installB = ref(null)
const uninstallB = ref(null)
const flmOpen = ref(false)

function onInstall(b) {
  if (b.id?.startsWith('flm')) {
    flmOpen.value = true
    return
  }
  installB.value = b
}
function onReinstall(b) {
  // Same modal flow for non-FLM; FLM reuses the guide.
  onInstall(b)
}
function onUninstall(b) {
  uninstallB.value = b
}

function gotoLogs() {
  router.push({ path: '/logs', query: { tab: 'lemonade' } })
}
function restartLemond() {
  toasts.push('Restarting lemond — brief outage', 'warn')
}
function updateLemond() {
  toasts.push('Checking for lemonade update…', 'info')
}

onMounted(async () => {
  await backends.fetch()
  // The lemonade store may not be initialised by chrome yet (some
  // E2E specs go straight to /backends without mounting the dashboard).
  lemonade.init?.()
})
</script>

<template>
  <div class="backends-page">
    <PageHeader
      eyebrow="Runtime"
      title="Backends"
      subtitle="Inference backends bundled with Lemonade"
    />

    <BannerStack scope="global" />

    <div class="page-body">
      <!-- Lemonade self-card ────────────────────────────────────── -->
      <div class="self-card" data-testid="lemonade-self-card">
        <div class="self-l">
          <span class="dot dot-ready" />
          <div>
            <div class="self-title mono">
              lemonade
              <span class="dim">· {{ backends.lemonadeSelf.version || lemonade.version || 'unknown' }}</span>
            </div>
            <div class="self-sub mono">
              {{ backends.lemonadeSelf.pinned ? 'pinned' : 'unpinned' }}
              · sha-256 {{ backends.lemonadeSelf.sha ? 'verified' : 'unverified' }}
              · channel {{ backends.lemonadeSelf.channel || 'stable' }}
            </div>
          </div>
        </div>
        <span class="self-r mono">uptime —</span>
        <button class="btn-ghost sm" type="button" data-testid="lemond-logs" @click="gotoLogs">Logs</button>
        <button class="btn-ghost sm" type="button" @click="restartLemond">Restart</button>
        <button class="btn-primary sm" type="button" @click="updateLemond">Update</button>
      </div>

      <div class="sec">
        <h2>
          Backends
          <span class="ct mono">{{ backends.backends.length }}</span>
        </h2>
        <div class="rule" />
      </div>

      <div class="table">
        <div class="table-head mono">
          <span>backend</span>
          <span>version</span>
          <span>state</span>
          <span>used by</span>
          <span class="right">actions</span>
        </div>
        <BackendRow
          v-for="b in backends.backends"
          :key="b.id"
          :backend="b"
          @install="onInstall"
          @reinstall="onReinstall"
          @uninstall="onUninstall"
        />
        <div v-if="backends.backends.length === 0" class="empty mono">
          No backends discovered. lemond may not be reachable yet.
        </div>
      </div>
    </div>

    <!-- Modals ────────────────────────────────────────────────── -->
    <BackendInstallModal
      :open="!!installB"
      :backend="installB"
      :on-close="() => (installB = null)"
    />
    <BackendUninstallModal
      :open="!!uninstallB"
      :backend="uninstallB"
      :on-close="() => (uninstallB = null)"
    />
    <FlmDebGuideModal
      :open="flmOpen"
      :backend="backends.backends.find((b) => b.id?.startsWith('flm'))"
      :on-close="() => (flmOpen = false)"
    />
  </div>
</template>

<style scoped>
.backends-page { display: flex; flex-direction: column; min-height: 100%; }
.page-body { padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; }

.self-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 16px;
  display: flex;
  align-items: center;
  gap: 14px;
}
.self-l { display: flex; align-items: center; gap: 10px; flex: 1; }
.self-title { font-size: 14px; font-weight: 500; color: var(--color-fg); }
.self-sub { font-size: 11px; color: var(--color-fg-faint); margin-top: 2px; }
.self-r { font-size: 11px; color: var(--color-fg-muted); margin-right: 12px; }
.dim { color: var(--color-fg-muted); }
.mono { font-family: var(--font-mono); }

.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dot-ready { background: var(--color-success); box-shadow: 0 0 8px var(--color-success); }

.sec {
  display: flex; align-items: center; gap: 12px;
  margin-top: 4px;
}
.sec h2 {
  font-size: 14px;
  font-weight: 500;
  color: var(--color-fg);
  letter-spacing: -0.01em;
  margin: 0;
  display: flex; align-items: center; gap: 8px;
}
.ct {
  font-size: 11px;
  color: var(--color-fg-faint);
  font-weight: 400;
}
.rule { flex: 1; height: 1px; background: var(--color-border); }

.table {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.table-head {
  padding: 10px 18px;
  background: var(--hal0-bg-sunken);
  border-bottom: 1px solid var(--color-border);
  display: grid;
  grid-template-columns: 1fr 200px 160px 1fr auto;
  gap: 16px;
  font-size: 10px;
  color: var(--color-fg-faint);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.table-head .right { text-align: right; }
.empty {
  padding: 24px;
  text-align: center;
  color: var(--color-fg-faint);
  font-size: 12px;
}

.btn-ghost {
  padding: 5px 12px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 11.5px;
  cursor: pointer;
}
.btn-ghost:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }
.btn-primary {
  padding: 5px 12px;
  border-radius: var(--radius);
  background: var(--hal0-accent);
  color: #000;
  font-family: var(--font-mono);
  font-size: 11.5px;
  font-weight: 500;
  border: none;
  cursor: pointer;
}
.btn-primary:hover { background: var(--hal0-accent-hover); }
.sm { font-size: 11px; padding: 4px 10px; }
</style>
