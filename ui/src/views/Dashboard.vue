<script setup>
/**
 * Dashboard.vue — Control Room (hal0).
 *
 * Layout (top to bottom):
 *   - Stat rail (API · slots · memory · model storage)
 *   - Unified memory bar (Strix Halo stacked breakdown)
 *   - Mini tiles (RAM · disk · throughput · NPU)
 *   - Active slots
 *   - Chat panel (test inference against any model)
 *   - Recent logs
 *
 * Stats arrive from /api/stats/hardware (proxied from configured upstreams)
 * via useStats; per-slot metrics from /api/slots/metrics via useSlotMetrics.
 */
import { computed, ref, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useSystemStore } from '../stores/system.js'
import { useStats, useSlotMetrics } from '../composables/useStats.js'
import { useSlotStats } from '../composables/useSlotStats.js'
import { api } from '../composables/useApi.js'
import PageHeader from '../components/PageHeader.vue'
import Card from '../components/Card.vue'
import LoadingSkeleton from '../components/LoadingSkeleton.vue'
import EmptyState from '../components/EmptyState.vue'
import SlotCard from '../components/SlotCard.vue'
import NPUBackendCard from '../components/capabilities/NPUBackendCard.vue'

const router = useRouter()
const system = useSystemStore()
const { stats } = useStats(2500)
const { metrics, history, aggHistory } = useSlotMetrics(2500)

// Slots managed entirely by the capability cards (Embed/Voice/Img on
// the Slots page). Hidden from the dashboard slot grid so operators
// don't see them twice.
const CAPABILITY_OWNED_SLOTS = new Set([
  'embed', 'embed-rerank', 'stt', 'tts', 'img',
])

// Hard ordering: primary first, nano second, then user-defined slots
// alphabetically. NPU rides at the end as its own card.
const SLOT_ORDER = ['primary', 'nano']
function slotSortKey(name) {
  const i = SLOT_ORDER.indexOf(name)
  return i >= 0 ? [0, i, name] : [1, 0, name]
}
const visibleSlots = computed(() => {
  const rows = system.slots.filter((s) => !CAPABILITY_OWNED_SLOTS.has(s.name))
  rows.sort((a, b) => {
    const ka = slotSortKey(a.name)
    const kb = slotSortKey(b.name)
    if (ka[0] !== kb[0]) return ka[0] - kb[0]
    if (ka[1] !== kb[1]) return ka[1] - kb[1]
    return ka[2].localeCompare(kb[2])
  })
  return rows
})

const hw = computed(() => stats.value || {})

// ── Slot summary ─────────────────────────────────────────────────────
const { running: slotsRunning, total: slotsTotal } = useSlotStats()
const slotSummary = computed(() => ({ running: slotsRunning.value, total: slotsTotal.value }))

// ── Memory tiles — prefer GTT (unified) when present, else VRAM ──────
const memUsedGb = computed(() => {
  if (hw.value.gtt_used_mb) return (hw.value.gtt_used_mb / 1024).toFixed(1)
  if (hw.value.vram_used_mb) return (hw.value.vram_used_mb / 1024).toFixed(1)
  return null
})
const memTotalGb = computed(() => {
  if (hw.value.gtt_total_mb) return (hw.value.gtt_total_mb / 1024).toFixed(0)
  if (hw.value.vram_total_mb) return (hw.value.vram_total_mb / 1024).toFixed(0)
  return null
})
const memLabel = computed(() => (hw.value.gtt_total_mb ? 'GTT' : 'VRAM'))

// ── Unified memory bar (Strix Halo: one physical pool, multiple consumers) ──
// On AMD UMA, GTT *is* system RAM — they share the same DIMMs. Summing
// ram_total + gtt_total would double-count, which is what produced the
// "169 GB pool on a 128 GB machine" bug. The probe exposes the true
// unified_memory_mb (via dmidecode when /proc/meminfo reports an LXC
// cgroup quota); we trust that. When a Proxmox API token is configured
// (see /etc/hal0/proxmox.json) the host's view is more authoritative
// because it sees the actual physical DIMM total *and* the other
// tenants competing for it — prefer it when present.
const hostOk = computed(() => {
  const h = hw.value.host
  return !!(h && h.configured && h.ok && h.host_mem_total_mb)
})
const unifiedTotalGb = computed(() => {
  if (hostOk.value) return hw.value.host.host_mem_total_mb / 1024
  const probed = hw.value.unified_memory_mb
  if (probed) return probed / 1024
  // Non-UMA / unknown: RAM + dedicated VRAM is a fair total (no overlap).
  const ramG = (hw.value.ram_total_mb || 0) / 1024
  const vramG = (hw.value.vram_total_mb || 0) / 1024
  return ramG + vramG
})

const unifiedSegments = computed(() => {
  const totalG = unifiedTotalGb.value || 0
  if (totalG === 0) return []
  // Used breakdown:
  //   gtt   = GPU's GTT allocations (live model weights / KV cache on UMA)
  //   npu   = NPU model resident bytes (drawn from the same pool as GTT,
  //           so shown only when an FLM slot is loaded — segment hidden
  //           at 0 to avoid visually double-counting with GTT).
  //   vram  = dedicated VRAM in use (discrete GPUs only). On Strix Halo /
  //           any UMA box the "VRAM" sysfs counter just reports the small
  //           BIOS-reserved framebuffer slice carved out of the same DIMMs
  //           the unified pool already covers — we drop it entirely so the
  //           bar only shows knobs the user can actually manage.
  //   sys   = system RAM used by everything else (MemTotal - MemAvailable).
  //           Inside an LXC, /proc/meminfo does NOT account for GPU-pinned
  //           pages (those live in the host kernel's GTT bookkeeping), so
  //           ram_used_gb and gtt_used_mb are independent — no subtraction.
  //   host  = everything else on the Proxmox host competing for the same
  //           DIMMs — other LXCs/VMs + ZFS ARC + the host kernel. Only
  //           rendered when /etc/hal0/proxmox.json is configured and the
  //           cluster/resources poll succeeded; otherwise the bar honestly
  //           shows "we can't see beyond this LXC" by leaving that mass
  //           unattributed inside Free.
  const isUma = !!hw.value.is_uma
  const gtt = (hw.value.gtt_used_mb || 0) / 1024
  const vram = isUma ? 0 : (hw.value.vram_used_mb || 0) / 1024
  const npu = (hw.value.npu_status?.model_mb || 0) / 1024
  const sys = hw.value.ram_used_gb
    ?? (((hw.value.ram_total_mb || 0) - (hw.value.ram_available_mb || 0)) / 1024)
  let host = 0
  if (hostOk.value) {
    // host_mem_used already includes our LXC's sys RAM + this kernel's
    // share of GTT (GTT is host-kernel-owned on UMA). Subtract our share
    // so we don't visually double-count.
    const hostUsedG = hw.value.host.host_mem_used_mb / 1024
    host = Math.max(0, hostUsedG - sys - gtt)
  }
  const used = gtt + sys + npu + vram + host
  const free = Math.max(0, totalG - used)
  const seg = (label, gb, cls) => ({
    label,
    gb,
    pct: Math.max(0, (gb / totalG) * 100),
    cls,
  })
  const out = [
    seg('GTT · inference', gtt, 'seg-gtt'),
    seg('System RAM', sys, 'seg-sys'),
  ]
  // Hide the NPU segment until an FLM slot is loaded — NPU memory comes
  // out of GTT, so an always-on NPU bucket would visually double-count.
  if (npu > 0.01) out.push(seg('NPU · FLM', npu, 'seg-npu'))
  // Proxmox host segment (other tenants + host kernel / ZFS ARC) only when
  // we have an authoritative cluster snapshot.
  if (host > 0.01) out.push(seg('Proxmox host', host, 'seg-host'))
  // VRAM segment only on discrete-GPU (non-UMA) machines.
  if (vram > 0.01) out.push(seg('VRAM', vram, 'seg-vram'))
  out.push(seg('Free', free, 'seg-free'))
  return out
})

// Prefer upstream-reported NPU readiness (haloai proxies it through
// /api/stats/hardware as npu_status.ok). Falls back to the local probe's
// npu_present so the single-LXC deployment — which has no upstreams to
// proxy from — still lights up when amdxdna + /dev/accel are present.
const npuOk = computed(() => hw.value.npu_status?.ok ?? !!hw.value.npu_present)

// ── Throughput ────────────────────────────────────────────────────────
const totalTps = computed(() =>
  Object.values(metrics.value).reduce((a, m) => a + (m?.tokens_per_sec ?? m?.tps ?? 0), 0),
)
const tputSparkPath = computed(() => {
  const series = aggHistory.value.tps
  if (!series.length) return ''
  const max = Math.max(1, ...series)
  const n = series.length
  const pts = series.map((v, i) => {
    const x = (i / Math.max(1, n - 1)) * 320
    const y = 44 - (v / max) * 40 - 2
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  })
  return pts.join(' ')
})
const tputAreaPath = computed(() => {
  const line = tputSparkPath.value
  if (!line) return ''
  return `${line} L320,44 L0,44 Z`
})

// ── API health ───────────────────────────────────────────────────────
const apiOk = computed(() => !system.error && system.status !== null)
const recentLogs = computed(() => system.status?.recent_logs ?? [])

// ── Chat panel ───────────────────────────────────────────────────────
const chatModels = ref([])
const chatModel = ref('')
const chatPrompt = ref('')
const chatOutput = ref('')
const chatReasoning = ref('')
const chatBusy = ref(false)
const chatError = ref(null)
const chatOutputEl = ref(null)

async function loadChatModels() {
  try {
    // The Test chat panel POSTs to /v1/chat/completions, so we must show
    // only chat-capable upstreams in the dropdown. /v1/models is a flat
    // aggregation across every slot (embed, rerank, stt, tts included)
    // with no capability tag — relying on its order put the embed model
    // at the top of the list as default. We instead derive the set of
    // chat-capable slots by joining /api/slots (slot → loaded model id)
    // with /api/capabilities (model id → capabilities) and filter
    // /v1/models' rows by owned_by ∈ chat-capable slots.
    const [models, slots, cap] = await Promise.all([
      api('/v1/models'),
      api('/api/slots').catch(() => []),
      api('/api/capabilities').catch(() => ({ catalogs: {} })),
    ])
    const chatModelIds = new Set(
      (cap?.catalogs?.chat?.chat ?? []).map((m) => m.id),
    )
    const chatSlots = new Set(
      (slots || []).filter((s) => chatModelIds.has(s.model_id)).map((s) => s.name),
    )
    chatModels.value = (models?.data || [])
      .filter((m) => chatSlots.has(m.owned_by))
      .map((m) => m.id)
    if (!chatModel.value && chatModels.value.length) {
      // Prefer a small fast model for the first interaction.
      const preferred = chatModels.value.find((m) =>
        ['gemma3:1b', 'qwen3:0.6b', 'llama3.2:1b', 'lfm2:1.2b'].includes(m),
      )
      chatModel.value = preferred || chatModels.value[0]
    }
  } catch (e) {
    chatError.value = e?.message ?? String(e)
  }
}

async function runChat() {
  if (!chatPrompt.value.trim() || !chatModel.value) return
  chatBusy.value = true
  chatError.value = null
  chatOutput.value = ''
  chatReasoning.value = ''
  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        model: chatModel.value,
        messages: [{ role: 'user', content: chatPrompt.value }],
        stream: true,
        max_tokens: 512,
      }),
    })
    if (!resp.ok) {
      const txt = await resp.text()
      try {
        const j = JSON.parse(txt)
        throw new Error(j?.error?.message || `HTTP ${resp.status}`)
      } catch {
        throw new Error(`HTTP ${resp.status}: ${txt.slice(0, 200)}`)
      }
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const event = buffer.slice(0, idx).trim()
        buffer = buffer.slice(idx + 2)
        if (!event.startsWith('data:')) continue
        const data = event.slice(5).trim()
        if (data === '[DONE]') continue
        try {
          const j = JSON.parse(data)
          const d = j?.choices?.[0]?.delta
          // Reasoning models (Qwen3-Reason, gpt-oss, deepseek-r1, …) emit
          // `reasoning_content` for their <think> tokens and `content` for
          // the user-visible answer. Dropping reasoning made the panel look
          // frozen — small reasoning models can burn the whole token budget
          // on thinking before any `content` arrives.
          if (d?.reasoning_content) chatReasoning.value += d.reasoning_content
          if (d?.content) chatOutput.value += d.content
          if (d?.reasoning_content || d?.content) {
            await nextTick()
            if (chatOutputEl.value) {
              chatOutputEl.value.scrollTop = chatOutputEl.value.scrollHeight
            }
          }
        } catch {
          /* ignore partial JSON */
        }
      }
    }
  } catch (e) {
    chatError.value = e?.message ?? String(e)
  } finally {
    chatBusy.value = false
  }
}

loadChatModels()
</script>

<template>
  <div class="dashboard-page">
    <div class="dashboard-hero" aria-hidden="true"></div>
    <PageHeader eyebrow="Control Room" title="Dashboard" subtitle="Live status of your hal0 box.">
      <template #actions>
        <button class="btn-secondary" type="button" @click="system.fetchStatus()">
          <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          Refresh
        </button>
      </template>
    </PageHeader>

    <div class="page-body">
      <!-- ── Stat rail ───────────────────────────────────────────── -->
      <div class="stat-grid">
        <Card class="stat-card">
          <div class="stat-label">API status</div>
          <div class="stat-value" :class="apiOk ? 'text-success' : 'text-danger'">
            {{ apiOk ? 'Online' : 'Unreachable' }}
          </div>
          <div class="stat-sub">
            {{ system.loading ? 'Checking…' : (system.status?.version ? `v${system.status.version}` : 'unknown') }}
          </div>
        </Card>

        <Card class="stat-card" :highlight="slotSummary.running > 0">
          <div class="stat-label">Slots running</div>
          <div class="stat-value">
            <template v-if="system.loading && system.slots.length === 0">
              <LoadingSkeleton :lines="1" height="28px" />
            </template>
            <template v-else>
              {{ slotSummary.running }}<span class="stat-denom">/{{ slotSummary.total }}</span>
            </template>
          </div>
          <div class="stat-sub">
            <button class="stat-link" type="button" @click="router.push('/slots')">Manage slots →</button>
          </div>
        </Card>

        <Card class="stat-card">
          <div class="stat-label">{{ memLabel }} memory</div>
          <div class="stat-value">
            <template v-if="memUsedGb && memTotalGb">
              {{ memUsedGb }}<span class="stat-denom">/{{ memTotalGb }} GB</span>
            </template>
            <template v-else>
              <span class="text-muted">—</span>
            </template>
          </div>
          <div class="stat-sub">
            <button class="stat-link" type="button" @click="router.push('/hardware')">Hardware details →</button>
          </div>
        </Card>

        <Card class="stat-card stat-tput">
          <div class="stat-label">Throughput</div>
          <div class="stat-value">
            {{ totalTps.toFixed(0) }}<span class="stat-denom">tok/s</span>
          </div>
          <svg class="stat-spark" viewBox="0 0 320 36" preserveAspectRatio="none" aria-hidden="true">
            <path :d="tputAreaPath" fill="currentColor" opacity="0.12" />
            <path :d="tputSparkPath" fill="none" stroke="currentColor" stroke-width="1.4" />
          </svg>
        </Card>
      </div>

      <!-- ── Unified memory bar (Strix Halo) ─────────────────────── -->
      <Card v-if="unifiedSegments.length" class="um-card">
        <div class="um-head">
          <div class="um-title">
            {{ hostOk ? 'Physical host memory' : 'Unified memory' }} ·
            {{ unifiedTotalGb.toFixed(0) }} GB pool
          </div>
          <div class="um-sub">
            {{ unifiedSegments.filter(s => s.cls !== 'seg-free').reduce((a, s) => a + s.gb, 0).toFixed(1) }} GB used
            <span class="dim"> · NPU {{ npuOk ? 'ready' : 'offline' }}</span>
            <span v-if="hostOk" class="dim">
              · {{ hw.host.tenants_running }} tenant{{ hw.host.tenants_running === 1 ? '' : 's' }}
            </span>
            <!-- Token-rot / network-failure indicator. Memory bar quietly
                 falls back to the LXC-only view in this state; the pill
                 is the user-visible signal that the host total is stale.
                 Click target is the Settings panel where the operator
                 can re-test or rotate the API token. -->
            <router-link
              v-if="hw.host?.configured && !hw.host?.ok"
              to="/settings"
              class="pve-warn"
              :title="hw.host?.error || 'Cluster poll failed — check token / network'"
            >
              · Proxmox: unreachable
            </router-link>
          </div>
        </div>
        <div class="um-bar" role="img" aria-label="Unified memory breakdown">
          <div
            v-for="seg in unifiedSegments"
            :key="seg.label"
            class="useg"
            :class="seg.cls"
            :style="{ width: seg.pct + '%' }"
            :title="`${seg.label}: ${seg.gb.toFixed(1)} GB`"
          ></div>
        </div>
        <div class="um-legend">
          <span v-for="seg in unifiedSegments" :key="seg.label" class="lg">
            <span class="sw" :class="seg.cls"></span>
            <span class="lbl">{{ seg.label }}</span>
            <span class="dim">{{ seg.gb.toFixed(1) }}<small>GB</small></span>
          </span>
        </div>
      </Card>

      <!-- ── Active slots ─────────────────────────────────────────── -->
      <section aria-labelledby="slots-heading">
        <p class="section-eyebrow"><span class="section-eyebrow-dot" aria-hidden="true"></span> Slots</p>
        <h2 id="slots-heading" class="section-title">Slots</h2>
        <template v-if="system.loading && visibleSlots.length === 0">
          <div class="slots-grid">
            <Card v-for="i in 3" :key="i"><LoadingSkeleton :lines="3" /></Card>
          </div>
        </template>
        <template v-else-if="visibleSlots.length === 0">
          <div class="slots-grid" role="list" aria-label="Inference slots">
            <NPUBackendCard />
          </div>
        </template>
        <template v-else>
          <div class="slots-grid" role="list" aria-label="Inference slots">
            <SlotCard
              v-for="slot in visibleSlots"
              :key="slot.name"
              :slot="slot"
              :metrics="metrics[slot.name]"
              :spark-data="history[slot.name] || { tps: [], pps: [] }"
              @action="() => router.push('/slots')"
              @logs="() => router.push('/slots')"
              @edit="() => router.push('/slots')"
              @delete="() => router.push('/slots')"
              @swapped="() => { /* slot store re-polls on its own */ }"
            />
            <!-- NPU backend lives in the slots grid: it can serve a
                 chat-shaped model the same way a llama.cpp slot does,
                 so colocating it here is honest. -->
            <NPUBackendCard />
          </div>
        </template>
      </section>

      <!-- ── Chat panel ───────────────────────────────────────────── -->
      <section aria-labelledby="chat-heading">
        <p class="section-eyebrow"><span class="section-eyebrow-dot" aria-hidden="true"></span> /v1/chat</p>
        <h2 id="chat-heading" class="section-title">Test chat</h2>
        <Card class="chat-card">
          <div class="chat-row">
            <select v-model="chatModel" class="chat-model">
              <option v-if="!chatModels.length" value="">No models</option>
              <option v-for="m in chatModels" :key="m" :value="m">{{ m }}</option>
            </select>
            <input
              v-model="chatPrompt"
              class="chat-input"
              placeholder="Ask the model anything…"
              @keydown.enter="runChat"
              :disabled="chatBusy"
            />
            <button
              class="btn-primary chat-send"
              type="button"
              :disabled="chatBusy || !chatPrompt.trim() || !chatModel"
              @click="runChat"
            >
              {{ chatBusy ? 'Streaming…' : 'Send' }}
            </button>
          </div>
          <div ref="chatOutputEl" class="chat-output" :class="{ empty: !chatOutput && !chatReasoning && !chatError }">
            <span v-if="chatError" class="text-danger">{{ chatError }}</span>
            <template v-else-if="chatOutput || chatReasoning">
              <span v-if="chatReasoning" class="chat-reasoning">{{ chatReasoning }}</span>
              <span v-if="chatReasoning && chatOutput" class="chat-reasoning-sep">───</span>
              <span v-if="chatOutput">{{ chatOutput }}</span>
            </template>
            <span v-else class="text-muted mono-text">Response appears here · streaming SSE from /v1/chat/completions</span>
          </div>
        </Card>
      </section>

      <!-- ── Recent logs ──────────────────────────────────────────── -->
      <section aria-labelledby="logs-heading">
        <p class="section-eyebrow"><span class="section-eyebrow-dot" aria-hidden="true"></span> Journal</p>
        <h2 id="logs-heading" class="section-title">
          Recent log events
          <button class="stat-link section-link" type="button" @click="router.push('/logs')">View all →</button>
        </h2>
        <Card :padded="false">
          <div v-if="recentLogs.length === 0" class="logs-empty">
            <span class="text-muted mono-text">No recent log events.</span>
          </div>
          <div v-else class="logs-list">
            <div v-for="(line, i) in recentLogs.slice(-10)" :key="i" class="log-line">{{ line }}</div>
          </div>
        </Card>
      </section>
    </div>
  </div>
</template>

<style scoped>
.dashboard-page { position: relative; display: flex; flex-direction: column; min-height: 100%; }
.page-body { padding: 20px 24px; display: flex; flex-direction: column; gap: 20px; position: relative; z-index: 1; }

/* Hero glow — radial amber wash behind the page title, mirroring
   the HeroSection on hal0-web. Pointer-events none so it never
   blocks the refresh button. */
.dashboard-hero {
  position: absolute;
  inset: 0 0 auto 0;
  height: 220px;
  pointer-events: none;
  background: radial-gradient(ellipse at top, var(--hal0-accent-glow), transparent 70%);
  z-index: 0;
}

/* ── Stat rail ──────────────────────────────────────────────────── */
.stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
@media (max-width: 900px) { .stat-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 480px) { .stat-grid { grid-template-columns: 1fr; } }

.stat-card { padding: 16px; }
.stat-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--hal0-accent); font-family: var(--font-mono); margin-bottom: 8px; font-weight: 500; }
.stat-value { font-family: var(--font-mono); font-size: 26px; font-weight: 600; color: var(--color-fg); line-height: 1.1; margin-bottom: 6px; letter-spacing: -0.02em; font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1; }
.stat-denom { font-size: 14px; color: var(--color-fg-muted); font-weight: 400; margin-left: 2px; }
.stat-sub { font-size: 11.5px; color: var(--color-fg-faint); font-family: var(--font-mono); }
.stat-link { background: transparent; border: none; color: var(--hal0-accent); font-family: var(--font-mono); font-size: 11px; cursor: pointer; padding: 0; }
.stat-link:hover { text-decoration: underline; }
.stat-tput { display: flex; flex-direction: column; }
.stat-tput .stat-value { margin-bottom: 4px; }
.stat-tput .stat-denom { margin-left: 4px; font-size: 13px; }
.stat-spark { width: 100%; height: 30px; color: var(--hal0-accent); margin-top: auto; }

/* ── Unified memory bar ─────────────────────────────────────────── */
.um-card { padding: 16px 18px; }
.um-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }
.um-title { font-family: var(--font-mono); font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 500; color: var(--hal0-accent); }
.um-sub { font-size: 11.5px; color: var(--color-fg-muted); font-family: var(--font-mono); font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1; }
.um-sub .pve-warn { color: var(--color-danger); text-decoration: none; margin-left: 2px; }
.um-sub .pve-warn:hover { text-decoration: underline; }
.um-bar { display: flex; height: 24px; border-radius: 4px; overflow: hidden; background: var(--color-surface-2); border: 1px solid var(--color-border); }
.useg { height: 100%; transition: width 0.4s ease; }
.seg-gtt   { background: var(--hal0-accent); }
.seg-sys   { background: var(--color-success); opacity: 0.85; }
.seg-npu   { background: var(--color-warning); opacity: 0.9; }
.seg-vram  { background: color-mix(in oklch, var(--hal0-accent) 50%, var(--color-warning)); }
/* Muted slate so other-tenant pressure reads as "outside our control"
   without competing with the amber GTT and green Sys colours. */
.seg-host  { background: color-mix(in oklch, var(--color-fg-muted) 55%, var(--color-surface-2)); }
.seg-free  { background: var(--color-surface-2); }
.um-legend { display: flex; flex-wrap: wrap; gap: 14px 18px; margin-top: 10px; font-size: 11.5px; color: var(--color-fg-muted); font-family: var(--font-mono); font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1; }
.lg { display: inline-flex; align-items: center; gap: 6px; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; }
.lbl { color: var(--color-fg-muted); }
.lg .dim { color: var(--color-fg-faint); }
.lg small { color: var(--color-fg-faint); margin-left: 1px; }

/* ── Sections ───────────────────────────────────────────────────── */
.section-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin: 0 0 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--hal0-accent);
  font-weight: 500;
}
.section-eyebrow-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--hal0-accent);
  box-shadow: 0 0 6px var(--hal0-accent);
}
.section-title { font-size: 16px; font-weight: 600; color: var(--color-fg); letter-spacing: -0.01em; margin: 0 0 12px; display: flex; align-items: center; gap: 12px; }
.section-link { margin-left: auto; font-size: 11px; }

/* ── Slot grid ──────────────────────────────────────────────────── */
/* Wider min-column so the SlotCard's roomier internals (taller spark,
   outlined load-cycle buttons) breathe without wrapping. Matches the
   Slots page's grid so the two views stay visually aligned. */
.slots-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 14px;
}

/* ── Chat panel ─────────────────────────────────────────────────── */
.chat-card { padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
.chat-row { display: flex; gap: 8px; align-items: stretch; }
.chat-model { padding: 6px 10px; font-size: 12px; font-family: var(--font-mono); background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-fg); min-width: 160px; }
.chat-input { flex: 1; padding: 6px 12px; font-size: 13px; background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: var(--radius); color: var(--color-fg); }
.chat-input:focus, .chat-model:focus { outline: 2px solid var(--color-accent); outline-offset: 1px; }
.chat-send { padding: 6px 16px; flex-shrink: 0; }
.chat-output { min-height: 80px; max-height: 260px; overflow-y: auto; padding: 10px 12px; background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: var(--radius); font-size: 13px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.chat-output.empty { display: flex; align-items: center; min-height: 60px; }
.chat-reasoning { color: var(--color-fg-faint); font-style: italic; font-size: 12px; }
.chat-reasoning-sep { display: block; margin: 8px 0; color: var(--color-fg-faint); font-family: var(--font-mono); font-size: 11px; }

/* ── Logs ───────────────────────────────────────────────────────── */
.logs-empty { padding: 20px 16px; display: flex; align-items: center; }
.logs-list { padding: 8px 0; max-height: 200px; overflow-y: auto; background: var(--hal0-bg-sunken); }
.log-line { padding: 3px 16px; font-family: var(--font-mono); font-size: 11.5px; color: var(--hal0-fg-dim); white-space: pre-wrap; word-break: break-all; border-bottom: 1px solid var(--color-border); }
.log-line:last-child { border-bottom: none; }

/* ── Utility ────────────────────────────────────────────────────── */
.dim { color: var(--color-fg-faint); }
.text-success { color: var(--color-success); }
.text-danger  { color: var(--color-danger); }
.text-muted   { color: var(--color-fg-faint); }
.mono-text    { font-family: var(--font-mono); font-size: 12px; }

.btn-primary { padding: 8px 18px; border-radius: var(--radius); background: var(--hal0-accent); color: #000; font-family: var(--font-mono); font-size: 12px; font-weight: 500; border: none; cursor: pointer; flex-shrink: 0; transition: background 0.15s; }
.btn-primary:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-secondary { display: flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: var(--radius); border: 1px solid var(--color-border); background: transparent; color: var(--color-fg-muted); font-family: var(--font-mono); font-size: 12px; cursor: pointer; transition: border-color 0.15s, color 0.15s; }
.btn-secondary:hover { border-color: var(--color-border-hi); color: var(--color-fg); }
</style>
