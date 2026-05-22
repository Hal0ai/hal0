<script setup>
/**
 * Hardware.vue
 *
 * Design intent: show what compute you have and how slots are using it.
 * Single-page layout: detected hardware at the top, current slot
 * allocation below, re-probe button prominent.
 *
 * Memory math: on AMD UMA (Strix Halo / Ryzen AI), GTT and system RAM
 * share the same physical DIMMs — summing ram_total_mb + gtt_total_mb
 * double-counts (the 169 GB-on-a-128 GB-machine bug from handoff
 * 2026-05-15). The probe now exposes ``unified_memory_mb`` and the
 * ``is_uma`` flag so this view (and Dashboard) can render a single
 * physical pool with breakdown segments instead of two independent bars.
 * Non-UMA (discrete GPU) machines keep separate RAM + dedicated-VRAM
 * bars since there's no overlap there.
 */
import { ref, computed, onMounted } from 'vue'
import { useSystemStore } from '../stores/system.js'
import { useToastsStore } from '../stores/toasts.js'
import { api } from '../composables/useApi.js'
import PageHeader from '../components/PageHeader.vue'
import Card from '../components/Card.vue'
import LoadingSkeleton from '../components/LoadingSkeleton.vue'

const system = useSystemStore()
const toasts = useToastsStore()

const hardware = ref(null)
const stats    = ref(null)   // /api/stats/hardware live counters merge
const loading  = ref(true)
const probing  = ref(false)
const error    = ref(null)

async function loadHardware() {
  loading.value = true
  error.value = null
  try {
    // Pull both the static probe and the live stats merge in parallel.
    // /api/stats/hardware already merges the probe + live process counters
    // (gtt_used_mb, vram_used_mb, gpu_util, etc.) — we use that as the
    // primary source and only fall back to /api/hardware when it 404s.
    const [hw, live] = await Promise.all([
      api('/api/hardware').catch(() => null),
      api('/api/stats/hardware').catch(() => null),
    ])
    hardware.value = hw
    stats.value = live
  } catch (e) {
    error.value = e.message
    hardware.value = null
    stats.value = null
  } finally {
    loading.value = false
  }
}

async function reProbe() {
  probing.value = true
  try {
    // Brief says: re-probe button POSTs /api/install/probe (the installer
    // wizard's probe endpoint, which writes /etc/hal0/hardware.json
    // atomically). /api/hardware/probe also exists but the install one
    // is the canonical write path.
    await api('/api/install/probe', { method: 'POST' })
    toasts.success('Hardware probe re-run — refreshing data…')
    await loadHardware()
    await system.fetchStatus()
  } catch (e) {
    toasts.error(e.message)
  } finally {
    probing.value = false
  }
}

// ── derived shape ────────────────────────────────────────────────────
// Merge stats over hardware: live counters win when present.
const hw = computed(() => ({ ...(hardware.value || {}), ...(stats.value || {}) }))

const isUma = computed(() => !!hw.value.is_uma)
const platform = computed(() => hw.value.platform || 'unknown')
const platformLabel = computed(() => hw.value.platform_label || '')
// "unified" is only meaningful on Strix Halo (and any future UMA host the
// probe explicitly classifies). On generic KVM, WSL, or discrete-GPU bare
// metal, "system" memory is the accurate label.
const isUnifiedMemory = computed(() => hw.value.memory_kind === 'unified' || isUma.value)

// Proxmox host memory authority — when /etc/hal0/proxmox.json is configured
// and the cluster/resources poll succeeded, the host's view trumps the
// LXC's cgroup view (which is just our slice). Mirrors Dashboard.vue so
// the two pages can never disagree about totals.
const hostOk = computed(() => {
  const h = hw.value.host
  return !!(h && h.configured && h.ok && h.host_mem_total_mb)
})

const gpuName    = computed(() => hw.value.gpu_name || '')
const gpuVendor  = computed(() => hw.value.gpu_vendor || '')
const gpuUtilPct = computed(() => {
  // /api/stats/hardware exposes gpu_util as a fraction (0..1) per haloai
  // contract; the static probe sometimes reports gpu_usage_pct as an
  // integer percent. Normalise both to a 0..100 display value.
  const frac = hw.value.gpu_util
  if (typeof frac === 'number') return frac > 1 ? frac : frac * 100
  const pct = hw.value.gpu_usage_pct
  return typeof pct === 'number' ? pct : null
})

const gttTotalGb = computed(() => (hw.value.gtt_total_mb || 0) / 1024)
const gttUsedGb  = computed(() => (hw.value.gtt_used_mb || 0) / 1024)
const vramTotalGb = computed(() => (hw.value.vram_total_mb || 0) / 1024)
const vramUsedGb  = computed(() => (hw.value.vram_used_mb || 0) / 1024)

// Unified memory pool (Strix Halo): mirror the math from Dashboard.vue so
// the two views can never disagree about totals. Falls back to ram+vram
// only on non-UMA where the two are independent.
const unifiedTotalGb = computed(() => {
  if (hostOk.value) return hw.value.host.host_mem_total_mb / 1024
  const probed = hw.value.unified_memory_mb
  if (probed) return probed / 1024
  const ramG = (hw.value.ram_total_mb || 0) / 1024
  const vramG = (hw.value.vram_total_mb || 0) / 1024
  return ramG + vramG
})

const unifiedSegments = computed(() => {
  const totalG = unifiedTotalGb.value || 0
  if (totalG === 0) return []
  const gtt = gttUsedGb.value
  // On UMA the "VRAM" sysfs counter is the BIOS-reserved framebuffer
  // slice carved out of the same DIMMs the unified pool covers — drop
  // it so the bar only shows knobs the user can actually manage.
  const vram = isUma.value ? 0 : vramUsedGb.value
  const npu = (hw.value.npu_status?.model_mb || 0) / 1024
  // ram_used_gb is the OS-visible used total. Inside an LXC, /proc/meminfo
  // does NOT include GPU-pinned pages (those live in the host kernel's GTT
  // bookkeeping), so ram_used and gtt_used are independent buckets — no
  // subtraction needed.
  const sys = hw.value.ram_used_gb
    ?? ((hw.value.ram_used_mb || 0) / 1024)
    ?? Math.max(0, ((hw.value.ram_total_mb || 0) - (hw.value.ram_available_mb || 0)) / 1024)
  // Proxmox-host pressure: everything else on the physical host —
  // other LXCs/VMs + ZFS ARC + host kernel. Canary confirmed 2026-05-21
  // that GTT lives in host kernel memory (not the LXC cgroup), so it
  // shows up inside host_mem_used and must be subtracted out.
  let host = 0
  if (hostOk.value) {
    const hostUsedG = hw.value.host.host_mem_used_mb / 1024
    host = Math.max(0, hostUsedG - sys - gtt)
  }
  const used = gtt + sys + npu + vram + host
  const free = Math.max(0, totalG - used)
  const seg = (label, gb, cls) => ({
    label, gb, pct: Math.max(0, (gb / totalG) * 100), cls,
  })
  const out = [
    seg('GTT · inference', gtt, 'seg-gtt'),
    seg('System RAM', sys, 'seg-sys'),
  ]
  // NPU memory comes out of GTT — only show the segment when an FLM slot
  // is loaded, otherwise it would visually double-count.
  if (npu > 0.01) out.push(seg('NPU · FLM', npu, 'seg-npu'))
  // Proxmox host segment (other tenants + host kernel / ZFS ARC) only
  // when /etc/hal0/proxmox.json is configured and reachable.
  if (host > 0.01) out.push(seg('Proxmox host', host, 'seg-host'))
  // VRAM segment only on discrete-GPU (non-UMA) machines.
  if (vram > 0.01) out.push(seg('Dedicated VRAM', vram, 'seg-vram'))
  out.push(seg('Free', free, 'seg-free'))
  return out
})

// Total used pct (for the headline tile colour).
const unifiedUsedPct = computed(() => {
  const total = unifiedTotalGb.value
  if (!total) return 0
  // When we have an authoritative host view, just use it — host_mem_used
  // already accounts for our LXC + GTT + every other tenant + kernel.
  if (hostOk.value) {
    return Math.min(100, (hw.value.host.host_mem_used_mb / 1024 / total) * 100)
  }
  const ramUsedG = hw.value.ram_used_gb ?? 0
  const npu = (hw.value.npu_status?.model_mb || 0) / 1024
  const vram = isUma.value ? 0 : vramUsedGb.value
  return Math.min(100, ((gttUsedGb.value + ramUsedG + npu + vram) / total) * 100)
})

// Non-UMA bars (only render when !is_uma)
const ramTotalGb = computed(() => (hw.value.ram_total_mb || 0) / 1024)
const ramUsedGb  = computed(() => hw.value.ram_used_gb ?? ((hw.value.ram_used_mb || 0) / 1024))
const ramPct     = computed(() => ramTotalGb.value > 0 ? (ramUsedGb.value / ramTotalGb.value * 100) : 0)

const diskTotalGb = computed(() => (hw.value.disk_total_mb || hw.value.disk_total_gb * 1024 || 0) / 1024)
const diskFreeGb  = computed(() => (hw.value.disk_free_mb || 0) / 1024)
const diskPct     = computed(() => diskTotalGb.value > 0 ? Math.max(0, 100 - (diskFreeGb.value / diskTotalGb.value * 100)) : 0)

function pctColor(pct) {
  if (pct > 85) return 'var(--color-danger)'
  if (pct > 60) return 'var(--color-warning)'
  return 'var(--color-success)'
}

const runningSlots = computed(() =>
  system.slots.filter((s) => ['running', 'ready', 'serving', 'idle'].includes(s.status))
)

onMounted(loadHardware)
</script>

<template>
  <div class="hardware-page">
    <PageHeader eyebrow="Probe" title="Hardware" subtitle="Compute resources and slot allocation">
      <template #actions>
        <button class="btn-secondary" type="button" @click="reProbe" :disabled="probing">
          <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" aria-hidden="true" :class="{ 'spin': probing }">
            <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
          {{ probing ? 'Probing…' : 'Re-probe' }}
        </button>
      </template>
    </PageHeader>

    <div class="page-body">
      <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

      <!-- ── Stat tiles ────────────────────────────────────────── -->
      <section v-if="!loading" aria-labelledby="tiles-heading">
        <h2 id="tiles-heading" class="sr-only">Hardware overview</h2>
        <div class="tiles">
          <div class="tile">
            <div class="tile-label">
              {{ hostOk ? 'Physical host memory' : (isUnifiedMemory ? 'Unified memory' : 'System RAM') }}
            </div>
            <div class="tile-value mono">
              {{ unifiedTotalGb.toFixed(0) }}<span class="tile-unit">GB</span>
            </div>
            <div v-if="hostOk" class="tile-sub">
              {{ unifiedUsedPct.toFixed(0) }}% in use ·
              {{ hw.host.tenants_running }} tenant{{ hw.host.tenants_running === 1 ? '' : 's' }}
            </div>
            <div v-else-if="isUnifiedMemory" class="tile-sub">{{ unifiedUsedPct.toFixed(0) }}% in use · UMA pool</div>
            <div v-else class="tile-sub">{{ ramPct.toFixed(0) }}% in use</div>
          </div>

          <div class="tile" v-if="platformLabel">
            <div class="tile-label">Platform</div>
            <div class="tile-value tile-small">{{ platformLabel }}</div>
            <div class="tile-sub mono">{{ platform }}</div>
          </div>

          <div class="tile" v-if="gpuName">
            <div class="tile-label">GPU</div>
            <div class="tile-value tile-small">{{ gpuName }}</div>
            <div class="tile-sub mono">
              {{ gpuVendor || 'unknown' }}<template v-if="gpuUtilPct != null"> · {{ gpuUtilPct.toFixed(0) }}% util</template>
            </div>
          </div>

          <div class="tile" v-if="isUma">
            <div class="tile-label">GTT</div>
            <div class="tile-value mono">
              {{ gttUsedGb.toFixed(1) }}<span class="tile-unit">/{{ gttTotalGb.toFixed(0) }} GB</span>
            </div>
            <div class="tile-sub">Carved from unified pool</div>
          </div>
          <div class="tile" v-else-if="vramTotalGb > 0">
            <div class="tile-label">VRAM</div>
            <div class="tile-value mono">
              {{ vramUsedGb.toFixed(1) }}<span class="tile-unit">/{{ vramTotalGb.toFixed(0) }} GB</span>
            </div>
            <div class="tile-sub">Dedicated</div>
          </div>

          <div class="tile" v-if="hw.npu_present || hw.npu_ok != null">
            <div class="tile-label">NPU</div>
            <div class="tile-value tile-small">
              <span :class="hw.npu_present || hw.npu_ok ? 'text-success' : 'text-muted'">
                {{ hw.npu_present || hw.npu_ok ? 'Available' : 'Not detected' }}
              </span>
            </div>
            <div class="tile-sub mono">{{ hw.npu_name || '—' }}</div>
          </div>
        </div>
      </section>

      <!-- ── Unified memory breakdown (UMA hosts) ────────────── -->
      <section v-if="!loading && isUma && unifiedTotalGb > 0" aria-labelledby="mem-heading">
        <h2 id="mem-heading" class="section-title">Memory breakdown</h2>
        <Card>
          <div class="bar-row">
            <div class="bar">
              <div
                v-for="seg in unifiedSegments"
                :key="seg.label"
                class="bar-seg"
                :class="seg.cls"
                :style="{ width: seg.pct + '%' }"
                :title="`${seg.label}: ${seg.gb.toFixed(2)} GB`"
              />
            </div>
            <span class="mono bar-total">{{ unifiedTotalGb.toFixed(0) }} GB pool</span>
          </div>
          <ul class="legend">
            <li v-for="seg in unifiedSegments" :key="seg.label">
              <span class="legend-swatch" :class="seg.cls" />
              <span class="legend-label">{{ seg.label }}</span>
              <span class="legend-val mono">{{ seg.gb.toFixed(2) }} GB</span>
              <span class="legend-pct mono">{{ seg.pct.toFixed(0) }}%</span>
            </li>
          </ul>
        </Card>
      </section>

      <!-- ── Non-UMA: separate RAM + Disk bars ────────────────── -->
      <section v-if="!loading && !isUma" aria-labelledby="ram-heading">
        <h2 id="ram-heading" class="section-title">System memory</h2>
        <Card>
          <div class="hw-grid">
            <div class="hw-row" v-if="ramTotalGb > 0">
              <span class="hw-key">RAM</span>
              <div class="hw-bar-wrap">
                <div class="hw-bar">
                  <div class="hw-bar-fill" :style="{ width: ramPct + '%', background: pctColor(ramPct) }" />
                </div>
                <span class="hw-val mono">{{ ramUsedGb.toFixed(1) }} / {{ ramTotalGb.toFixed(0) }} GB</span>
              </div>
            </div>
            <div class="hw-row" v-if="diskTotalGb > 0">
              <span class="hw-key">Disk</span>
              <div class="hw-bar-wrap">
                <div class="hw-bar">
                  <div class="hw-bar-fill" :style="{ width: diskPct + '%', background: pctColor(diskPct) }" />
                </div>
                <span class="hw-val mono">{{ (diskTotalGb - diskFreeGb).toFixed(0) }} / {{ diskTotalGb.toFixed(0) }} GB</span>
              </div>
            </div>
          </div>
        </Card>
      </section>

      <!-- ── CPU + extras ────────────────────────────────────── -->
      <section v-if="!loading" aria-labelledby="cpu-heading">
        <h2 id="cpu-heading" class="section-title">CPU</h2>
        <Card>
          <div class="hw-grid">
            <div class="hw-row" v-if="hw.cpu_name || hw.cpu_model">
              <span class="hw-key">Model</span>
              <span class="hw-val">{{ hw.cpu_name || hw.cpu_model }}</span>
            </div>
            <div class="hw-row" v-if="hw.cpu_cores">
              <span class="hw-key">Cores</span>
              <span class="hw-val mono">{{ hw.cpu_cores }} physical / {{ hw.cpu_threads || hw.cpu_cores }} threads</span>
            </div>
            <div class="hw-row" v-if="hw.cpu_pct != null">
              <span class="hw-key">Util</span>
              <span class="hw-val mono">{{ hw.cpu_pct?.toFixed(0) }}%</span>
            </div>
          </div>
        </Card>
      </section>

      <Card v-if="loading"><LoadingSkeleton :lines="4" /></Card>

      <!-- ── Slot allocation ─────────────────────────────────── -->
      <section v-if="!loading" aria-labelledby="slots-heading">
        <h2 id="slots-heading" class="section-title">Slot allocation</h2>
        <Card :padded="false">
          <div v-if="runningSlots.length === 0" class="allocation-empty">
            No slots are currently running.
          </div>
          <table v-else class="alloc-table">
            <thead>
              <tr>
                <th>Slot</th>
                <th>Backend</th>
                <th>Model</th>
                <th>Port</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="slot in system.slots" :key="slot.name">
                <td class="mono">{{ slot.name }}</td>
                <td class="mono small">{{ slot.backend ?? '—' }}</td>
                <td class="mono small">{{ slot.model ?? slot.model_id ?? '—' }}</td>
                <td class="mono small">{{ slot.port ? ':' + slot.port : '—' }}</td>
                <td>
                  <span class="status-chip" :class="['running','ready','serving','idle'].includes(slot.status) ? 'chip-ok' : 'chip-off'">
                    {{ slot.status ?? 'offline' }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </Card>
      </section>
    </div>
  </div>
</template>

<style scoped>
.hardware-page { display: flex; flex-direction: column; min-height: 100%; }
.page-body     { padding: 20px 24px; display: flex; flex-direction: column; gap: 20px; }

.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }

.error-banner { padding: 10px 16px; border-radius: var(--radius-lg); background: color-mix(in oklch, var(--color-danger) 10%, var(--color-surface)); border: 1px solid color-mix(in oklch, var(--color-danger) 30%, transparent); color: var(--color-danger); font-size: 13px; }

.section-title { font-size: 16px; font-weight: 600; color: var(--color-fg); letter-spacing: -0.01em; margin: 0 0 10px; }

/* Tiles */
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}
.tile {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.tile-label {
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--hal0-accent);
  font-weight: 500;
}
.tile-value { font-family: var(--font-mono); font-size: 24px; font-weight: 600; color: var(--color-fg); letter-spacing: -0.02em; font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1; }
.tile-value.tile-small { font-size: 13px; font-weight: 500; letter-spacing: 0; }
.tile-unit { font-size: 12px; font-weight: 400; color: var(--color-fg-faint); margin-left: 4px; }
.tile-sub { font-size: 11.5px; color: var(--color-fg-faint); }
.tile-sub.mono { font-family: var(--font-mono); font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1; }

/* Stacked memory bar */
.bar-row { display: flex; align-items: center; gap: 12px; }
.bar {
  flex: 1;
  height: 14px;
  background: var(--color-surface-3);
  border-radius: 7px;
  overflow: hidden;
  display: flex;
}
.bar-seg { height: 100%; transition: width 0.3s ease; }
.bar-seg.seg-gtt  { background: var(--hal0-accent); }
.bar-seg.seg-sys  { background: color-mix(in oklch, var(--color-fg-muted) 40%, var(--color-surface)); }
.bar-seg.seg-npu  { background: var(--color-warning); }
.bar-seg.seg-host { background: color-mix(in oklch, var(--color-fg-muted) 55%, var(--color-surface-2)); }
.bar-seg.seg-vram { background: var(--color-danger); }
.bar-seg.seg-free { background: transparent; }
.bar-total { font-size: 11.5px; color: var(--color-fg-faint); flex-shrink: 0; }

.legend {
  list-style: none;
  margin: 14px 0 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 4px 16px;
}
.legend li { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.legend-swatch {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  flex-shrink: 0;
}
.legend-swatch.seg-gtt  { background: var(--hal0-accent); }
.legend-swatch.seg-sys  { background: color-mix(in oklch, var(--color-fg-muted) 40%, var(--color-surface)); }
.legend-swatch.seg-npu  { background: var(--color-warning); }
.legend-swatch.seg-host { background: color-mix(in oklch, var(--color-fg-muted) 55%, var(--color-surface-2)); }
.legend-swatch.seg-vram { background: var(--color-danger); }
.legend-swatch.seg-free { background: var(--color-surface-3); border: 1px solid var(--color-border); }
.legend-label { color: var(--color-fg-muted); }
.legend-val   { margin-left: auto; color: var(--color-fg); font-size: 11.5px; }
.legend-pct   { color: var(--color-fg-faint); font-size: 11px; min-width: 36px; text-align: right; }

/* Classic key/value rows for the non-UMA + CPU sections */
.hw-grid { display: flex; flex-direction: column; gap: 12px; }
.hw-row { display: flex; align-items: center; gap: 16px; }
.hw-key { font-family: var(--font-mono); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--color-fg-faint); min-width: 100px; flex-shrink: 0; }
.hw-val { font-size: 13px; color: var(--color-fg-muted); }
.hw-val.mono { font-family: var(--font-mono); font-size: 12px; }
.hw-bar-wrap { display: flex; align-items: center; gap: 10px; flex: 1; }
.hw-bar { flex: 1; height: 5px; background: var(--color-surface-3); border-radius: 3px; overflow: hidden; }
.hw-bar-fill { height: 100%; border-radius: 3px; transition: width 0.5s ease; }
.text-success { color: var(--color-success); }
.text-muted   { color: var(--color-fg-faint); }

/* Slot table */
.allocation-empty { padding: 24px; text-align: center; color: var(--color-fg-faint); font-size: 13px; }
.alloc-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.alloc-table thead { background: var(--hal0-bg-sunken); }
.alloc-table th { padding: 9px 16px; text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--hal0-accent); font-family: var(--font-mono); border-bottom: 1px solid var(--color-border); font-weight: 500; }
.alloc-table td { padding: 10px 16px; border-bottom: 1px solid var(--color-border); color: var(--color-fg-muted); }
.alloc-table tbody tr:last-child td { border-bottom: none; }
.alloc-table .mono { font-family: var(--font-mono); font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1; }
.alloc-table .small { font-size: 11.5px; }

.status-chip { font-family: var(--font-mono); font-size: 10.5px; padding: 2px 8px; border-radius: 4px; text-transform: uppercase; letter-spacing: 0.06em; }
.chip-ok  { background: color-mix(in srgb, var(--hal0-accent) 14%, transparent); color: var(--hal0-accent); }
.chip-off { background: var(--color-surface-2); color: var(--color-fg-faint); }

.btn-secondary { display: flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: var(--radius); border: 1px solid var(--color-border); background: transparent; color: var(--color-fg-muted); font-family: var(--font-mono); font-size: 12px; cursor: pointer; transition: border-color 0.15s, color 0.15s; }
.btn-secondary:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }
.spin { animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
