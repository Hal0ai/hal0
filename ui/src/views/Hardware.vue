<script setup>
/**
 * Hardware.vue — v2 read-only hardware inventory (slice #174).
 *
 * Mirrors the React `HardwareView` in
 *   /tmp/hal0-design/hal0-v2/project/dash/extras.jsx (lines 7–80).
 *
 * Six vertical-stack panels: Host, CPU, GPU (full), NPU (full, purple),
 * Memory (full), Storage (full). Top-right [Refresh] re-hits
 * /api/hardware. lemond-offline → dim entire view + stale banner.
 */
import { ref, computed, onMounted } from 'vue'
import { useSystemStore } from '../stores/system.js'
import { useLemonadeStore } from '../stores/lemonade.js'
import { useToastStore } from '../stores/toast.js'
import { useBannerStore } from '../stores/banner.js'
import { api } from '../composables/useApi.js'
import PageHeader from '../components/PageHeader.vue'
import BannerStack from '../components/primitives/BannerStack.vue'
import HwCard from '../components/hardware/HwCard.vue'
import HwRow from '../components/hardware/HwRow.vue'
import MemoryBar from '../components/hardware/MemoryBar.vue'

const system = useSystemStore()
const lemonade = useLemonadeStore()
const toasts = useToastStore()
const banners = useBannerStore()

const hardware = ref(null)
const stats = ref(null)
const loading = ref(true)
const refreshing = ref(false)
const error = ref(null)

async function loadHardware() {
  loading.value = true
  error.value = null
  try {
    const [hw, live] = await Promise.all([
      api('/api/hardware').catch(() => null),
      api('/api/stats/hardware').catch(() => null),
    ])
    hardware.value = hw
    stats.value = live
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function refresh() {
  refreshing.value = true
  try {
    await loadHardware()
    await system.fetchStatus().catch(() => {})
    toasts.push('Hardware refreshed', 'ok')
  } catch (e) {
    toasts.push(e.message || 'Refresh failed', 'err')
  } finally {
    refreshing.value = false
  }
}

// ── derived shape ────────────────────────────────────────────────────
const hw = computed(() => ({ ...(hardware.value || {}), ...(stats.value || {}) }))
const lemondOffline = computed(() => lemonade.health === 'down')

const hostname = computed(() => system.status?.hostname || hw.value.hostname || 'unknown')
const uptime = computed(() => hw.value.uptime || system.status?.uptime || '—')
const kernel = computed(() => hw.value.kernel || hw.value.kernel_release || '—')
const distro = computed(() => hw.value.distro || hw.value.os_pretty || hw.value.os || '—')
const bootId = computed(() => hw.value.boot_id || '—')

const cpuModel = computed(() => hw.value.cpu_name || hw.value.cpu_model || '—')
const cpuCores = computed(() => {
  const c = hw.value.cpu_cores
  const t = hw.value.cpu_threads
  if (!c) return '—'
  return t ? `${c}c · ${t}t` : `${c}c`
})
const cpuClock = computed(() => hw.value.cpu_clock || hw.value.cpu_freq || '—')
const cpuCache = computed(() => hw.value.cpu_cache || '—')

const gpuName = computed(() => hw.value.gpu_name || '—')
const gpuVendor = computed(() => hw.value.gpu_vendor || 'unknown')
const rocmStatus = computed(() => hw.value.rocm_ok ?? hw.value.rocm_present)
const vulkanStatus = computed(() => hw.value.vulkan_ok ?? hw.value.vulkan_present)
const isUma = computed(() => !!hw.value.is_uma)
const unifiedTotalGb = computed(() => {
  const probed = hw.value.unified_memory_mb
  if (probed) return probed / 1024
  return ((hw.value.ram_total_mb || 0) + (hw.value.vram_total_mb || 0)) / 1024
})

const npuPresent = computed(() => !!(hw.value.npu_present || hw.value.npu_ok))
const npuName = computed(() => hw.value.npu_name || 'AMDXDNA')
const npuColumns = computed(() => hw.value.npu_columns || hw.value.npu?.columns || '—')
const npuCtx = computed(() => hw.value.npu_ctx || hw.value.npu?.ctx || 1)
const flmVersion = computed(() => hw.value.flm_version || hw.value.npu_runtime_version || null)
const npuLoaded = computed(() => {
  // Slots that currently target the NPU device.
  return system.slots
    .filter((s) => s.device === 'npu' && ['ready', 'serving', 'idle'].includes(s.status))
    .map((s) => s.model || s.model_id)
    .filter(Boolean)
    .join(' · ') || '—'
})

const ramTotalGb = computed(() => (hw.value.ram_total_mb || 0) / 1024)
const ramUsedGb = computed(() =>
  hw.value.ram_used_gb ?? ((hw.value.ram_used_mb || 0) / 1024),
)
const ramFreeGb = computed(() => Math.max(0, (unifiedTotalGb.value || ramTotalGb.value) - ramUsedGb.value))

// Stacked-bar segments: per-slot memory pulled from the slot table.
const memorySegments = computed(() => {
  const total = unifiedTotalGb.value || ramTotalGb.value
  if (!total) return []
  const buckets = { primary: 0, agent: 0, embed: 0, tts: 0 }
  for (const s of system.slots) {
    const mem = s.metrics?.mem ?? s.mem_gb ?? 0
    if (s.type === 'llm' && s.device === 'npu') buckets.agent += mem
    else if (s.type === 'llm') buckets.primary += mem
    else if (s.type === 'embedding' || s.type === 'reranking') buckets.embed += mem
    else if (s.type === 'tts' || s.type === 'transcription') buckets.tts += mem
    else buckets.primary += mem
  }
  const used = Object.values(buckets).reduce((a, b) => a + b, 0)
  const free = Math.max(0, total - used)
  return [
    { label: 'primary', gb: buckets.primary, cls: 'seg-primary' },
    { label: 'agent', gb: buckets.agent, cls: 'seg-agent' },
    { label: 'embed', gb: buckets.embed, cls: 'seg-embed' },
    { label: 'tts', gb: buckets.tts, cls: 'seg-tts' },
    { label: 'free', gb: free, cls: 'seg-free' },
  ]
})
const memoryUsedGb = computed(() =>
  memorySegments.value.reduce((acc, s) => s.cls === 'seg-free' ? acc : acc + s.gb, 0),
)
const loadedCount = computed(() =>
  system.slots.filter((s) => ['ready', 'serving', 'loading'].includes(s.status)).length,
)

const modelDir = computed(() => hw.value.model_dir || '/var/lib/hal0/models')
const modelDirSize = computed(() => hw.value.model_dir_size_gb || '—')
const diskFreeGb = computed(() => ((hw.value.disk_free_mb || 0) / 1024).toFixed(0))
const hfCache = computed(() => hw.value.hf_cache_dir || '/root/.cache/huggingface')

onMounted(loadHardware)
</script>

<template>
  <div class="hardware-page" :class="{ 'is-stale': lemondOffline }">
    <PageHeader
      eyebrow="System"
      title="Hardware"
      subtitle="Read-only inventory · sourced from /api/hardware"
    >
      <template #actions>
        <button
          class="btn-secondary"
          type="button"
          :disabled="refreshing"
          data-testid="hw-refresh"
          @click="refresh"
        >
          <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" :class="{ spin: refreshing }" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
          {{ refreshing ? 'Refreshing…' : 'Refresh' }}
        </button>
      </template>
    </PageHeader>

    <BannerStack scope="global" />

    <div class="page-body">
      <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

      <div v-if="lemondOffline" class="stale-banner" role="status">
        lemond is offline — hardware probe data may be stale.
      </div>

      <!-- 2-col responsive grid; full-width cards span both columns. -->
      <div class="hw-grid">
        <HwCard title="Host" eyebrow="machine">
          <HwRow k="hostname"><span>{{ hostname }}</span></HwRow>
          <HwRow k="kernel"><span>{{ kernel }}</span></HwRow>
          <HwRow k="distro"><span>{{ distro }}</span></HwRow>
          <HwRow k="uptime"><span>{{ uptime }}</span></HwRow>
          <HwRow k="boot id" mono><span>{{ bootId }}</span></HwRow>
        </HwCard>

        <HwCard title="CPU" :eyebrow="hw.cpu_arch || 'x86-64'">
          <HwRow k="model"><span>{{ cpuModel }}</span></HwRow>
          <HwRow k="cores"><span class="mono">{{ cpuCores }}</span></HwRow>
          <HwRow k="clock"><span class="mono">{{ cpuClock }}</span></HwRow>
          <HwRow k="cache"><span class="mono">{{ cpuCache }}</span></HwRow>
          <HwRow k="recommended">
            <span class="chip chip-ok">llamacpp:cpu</span>
          </HwRow>
        </HwCard>

        <HwCard title="GPU" eyebrow="iGPU · unified memory" full>
          <HwRow k="device"><span>{{ gpuName }}</span></HwRow>
          <HwRow k="vendor stack">
            <span class="mono">ROCm</span>
            <span :class="['dot-tiny', rocmStatus ? 'dot-ok' : 'dot-off']" />
            <span class="mono" :style="{ color: rocmStatus ? 'var(--color-success)' : 'var(--color-fg-faint)' }">
              {{ rocmStatus ? 'present' : 'absent' }}
            </span>
            <span class="mono dim">·</span>
            <span class="mono">Vulkan</span>
            <span :class="['dot-tiny', vulkanStatus ? 'dot-ok' : 'dot-off']" />
            <span class="mono" :style="{ color: vulkanStatus ? 'var(--color-success)' : 'var(--color-fg-faint)' }">
              {{ vulkanStatus ? 'present' : 'absent' }}
            </span>
          </HwRow>
          <HwRow k="vram model">
            <span v-if="isUma">unified · shares system RAM ({{ unifiedTotalGb.toFixed(0) }} GB)</span>
            <span v-else class="mono">{{ ((hw.vram_total_mb || 0) / 1024).toFixed(0) }} GB dedicated</span>
          </HwRow>
          <HwRow k="recommended">
            <span class="chip chip-ok">llamacpp:rocm</span>
            <span class="chip chip-ok">sdcpp:rocm</span>
          </HwRow>
          <HwRow k="fallback" sub="if ROCm fails to load a model">
            <span class="chip">llamacpp:vulkan</span>
          </HwRow>
        </HwCard>

        <HwCard title="NPU" eyebrow="XDNA2 · coresident trio" full purple>
          <template v-if="npuPresent">
            <HwRow k="device"><span>{{ npuName }}</span></HwRow>
            <HwRow k="topology">
              <span class="mono">{{ npuColumns }} columns · {{ npuCtx }} hardware context</span>
            </HwRow>
            <HwRow k="runtime">
              <template v-if="flmVersion">
                <b class="mono">FLM {{ flmVersion }}</b>
                <span class="dim">·</span>
                <span class="mono">trio mode (--asr 1 --embed 1)</span>
              </template>
              <span v-else class="mono dim">FLM runtime not detected</span>
            </HwRow>
            <HwRow k="currently loaded" mono><span>{{ npuLoaded }}</span></HwRow>
            <HwRow k="recommended">
              <span class="chip chip-npu">flm:npu</span>
            </HwRow>
          </template>
          <template v-else>
            <HwRow k="device"><span class="dim">no NPU detected</span></HwRow>
            <HwRow k="recommended">
              <span class="chip chip-npu" style="opacity: 0.4">flm:npu</span>
            </HwRow>
          </template>
        </HwCard>

        <HwCard title="Memory" eyebrow="unified" full>
          <HwRow k="total"><span><span class="num">{{ (unifiedTotalGb || 0).toFixed(0) }}</span> GB</span></HwRow>
          <HwRow k="used"><span><span class="num">{{ ramUsedGb.toFixed(1) }}</span> GB · {{ loadedCount }} models loaded</span></HwRow>
          <HwRow k="free"><span><span class="num text-success">{{ ramFreeGb.toFixed(1) }}</span> GB</span></HwRow>
          <HwRow k="per-type budget">
            <span class="mono">{{ loadedCount }} loaded model{{ loadedCount === 1 ? '' : 's' }}</span>
          </HwRow>
          <MemoryBar
            :segments="memorySegments"
            :total-gb="unifiedTotalGb"
            :used-gb="memoryUsedGb"
            caption="primary · agent · embed · tts · free"
          />
        </HwCard>

        <HwCard title="Storage" eyebrow="model cache" full>
          <HwRow k="model dir" mono><span>{{ modelDir }}</span></HwRow>
          <HwRow k="size"><span class="mono">{{ modelDirSize }}</span></HwRow>
          <HwRow k="free on /var"><span class="mono">{{ diskFreeGb }} GB</span></HwRow>
          <HwRow k="hf cache" mono><span>{{ hfCache }}</span></HwRow>
        </HwCard>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hardware-page { display: flex; flex-direction: column; min-height: 100%; }
.hardware-page.is-stale { opacity: 0.65; pointer-events: auto; }
.page-body { padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; }

.hw-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
@media (max-width: 720px) {
  .hw-grid { grid-template-columns: 1fr; }
}

.error-banner {
  padding: 10px 16px;
  border-radius: var(--radius-lg);
  background: color-mix(in oklch, var(--color-danger) 10%, var(--color-surface));
  border: 1px solid color-mix(in oklch, var(--color-danger) 30%, transparent);
  color: var(--color-danger);
  font-size: 13px;
}
.stale-banner {
  padding: 8px 12px;
  border-radius: var(--radius);
  background: color-mix(in oklch, var(--color-warning) 10%, var(--color-surface));
  border: 1px solid color-mix(in oklch, var(--color-warning) 30%, transparent);
  color: var(--color-warning);
  font-family: var(--font-mono);
  font-size: 11.5px;
}

.chip {
  font-family: var(--font-mono);
  font-size: 10.5px;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  background: var(--color-surface-2);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.chip-ok {
  color: var(--color-success);
  border-color: color-mix(in srgb, var(--color-success) 30%, transparent);
  background: color-mix(in srgb, var(--color-success) 8%, transparent);
}
.chip-npu {
  color: rgb(200, 150, 255);
  border-color: rgba(200, 150, 255, 0.30);
  background: rgba(200, 150, 255, 0.06);
}

.dot-tiny {
  width: 6px; height: 6px; border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}
.dot-ok { background: var(--color-success); box-shadow: 0 0 6px var(--color-success); }
.dot-off { background: var(--color-fg-faint); }

.mono { font-family: var(--font-mono); }
.dim { color: var(--color-fg-faint); }
.num { font-family: var(--font-mono); font-feature-settings: 'zero' 1, 'tnum' 1; }
.text-success { color: var(--color-success); }

.btn-secondary {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 12px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.btn-secondary:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }
.spin { animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
