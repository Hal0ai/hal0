<script setup>
/**
 * Agent.vue — v2 host page for /agent (slice #174).
 *
 * Mirrors the React `AgentView` in
 *   /tmp/hal0-design/hal0-v2/project/dash/extras.jsx (lines 435–499).
 *
 * Tabs: Overview / Inbox / Skills / Memory / Personas. ADR-0004 §5
 * approvals surface is preserved unchanged — Inbox reuses
 * AgentInboxTab + AgentApprovalRow + the header bell still drives
 * everything via useAgentStore.
 */
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAgentStore } from '../stores/agent.js'
import { useBannerStore } from '../stores/banner.js'
import { api } from '../composables/useApi.js'
import PageHeader from '../components/PageHeader.vue'
import BannerStack from '../components/primitives/BannerStack.vue'
import AgentOverviewTab from '../components/agent/AgentOverviewTab.vue'
import AgentInboxTab from '../components/agent/AgentInboxTab.vue'
import AgentActivityTab from '../components/agent/AgentActivityTab.vue'
import NoBundledAgentCard from '../components/agent/NoBundledAgentCard.vue'
import PersonaEditModal from '../components/agent/PersonaEditModal.vue'

const route = useRoute()
const router = useRouter()
const agent = useAgentStore()
const banners = useBannerStore()

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'inbox', label: 'Inbox' },
  { id: 'skills', label: 'Skills' },
  { id: 'memory', label: 'Memory' },
  { id: 'personas', label: 'Personas' },
]

const activeTab = computed(() => {
  const t = String(route.query.tab || 'overview')
  return TABS.some((x) => x.id === t) ? t : 'overview'
})
function selectTab(id) {
  if (id === activeTab.value) return
  router.replace({ query: { ...route.query, tab: id } })
}

// ── Persona editor ───────────────────────────────────────────────
const editingPersona = ref(null)
const personas = ref([])

async function loadPersonas() {
  try {
    const r = await api('/api/personas')
    personas.value = Array.isArray(r?.personas) ? r.personas : []
  } catch {
    // Fallback to a small static set so the personas tab isn't empty in
    // dev / when /api/personas isn't live yet.
    personas.value = [
      { id: 'hermes', name: 'hermes', slot: 'primary', model: 'qwen3.6-27b-mtp', tone: 'operator', desc: 'Default — terse, technical, runs skills aggressively.', active: true },
      { id: 'hermes-coder', name: 'hermes-coder', slot: 'coder', model: 'qwen3-coder-30b', tone: 'code-focused', desc: 'Swaps in when the persona dropdown picks coder.' },
      { id: 'hermes-npu', name: 'hermes-npu', slot: 'agent', model: 'gemma3:1b', tone: 'low-latency', desc: 'NPU coresident · for short follow-ups.' },
    ]
  }
}

function openPersonaEditor(p) {
  editingPersona.value = p
}
function openPersonaCreate() {
  editingPersona.value = { isAdd: true }
}

onMounted(() => {
  agent.ensureBootstrapped()
  loadPersonas()
  // Show the catalog's "no-agent" banner whenever there isn't one
  // installed — clears automatically on install via the store's
  // reactivity (Overview tab re-renders).
  if (!agent.currentAgent) banners.show('no-agent')
})
watch(
  () => agent.currentAgent,
  (a) => {
    if (a) banners.dismiss('no-agent')
    else banners.show('no-agent')
  },
)
</script>

<template>
  <div class="agent-page">
    <PageHeader
      eyebrow="Tools"
      title="Agent"
      subtitle="Bundled agent — chat, skills, memory, personas"
    >
      <template #actions>
        <span class="hint mono">scaffolded · v0.2.1 · full surface in v0.3</span>
      </template>
    </PageHeader>

    <BannerStack scope="agent" />

    <nav class="tabs" role="tablist" aria-label="Agent sections">
      <button
        v-for="t in TABS"
        :key="t.id"
        type="button"
        role="tab"
        :aria-selected="activeTab === t.id"
        :class="['tab', { active: activeTab === t.id }]"
        :data-testid="`agent-tab-${t.id}`"
        @click="selectTab(t.id)"
      >
        <span class="tab-label">{{ t.label }}</span>
        <span v-if="t.id === 'inbox' && agent.pendingCount > 0" class="tab-badge mono">
          {{ agent.pendingCount }}
        </span>
      </button>
    </nav>

    <div class="page-body">
      <!-- Overview ─────────────────────────────────────────────── -->
      <template v-if="activeTab === 'overview'">
        <NoBundledAgentCard v-if="!agent.currentAgent" />
        <AgentOverviewTab v-else />
      </template>

      <!-- Inbox (preserves ADR-0004 §5 wiring) ──────────────────── -->
      <template v-else-if="activeTab === 'inbox'">
        <AgentInboxTab />
      </template>

      <!-- Skills (capability/policy table) ─────────────────────── -->
      <template v-else-if="activeTab === 'skills'">
        <div class="skill-card">
          <div class="skill-head mono">
            <span>skill</span>
            <span>capability</span>
            <span>source</span>
            <span>policy</span>
            <span class="right">calls</span>
          </div>
          <div
            v-for="s in [
              { name: 'read_file', cap: 'fs-read', policy: 'remember', calls: 247, src: 'builtin' },
              { name: 'write_file', cap: 'fs-write', policy: 'always', calls: 38, src: 'builtin' },
              { name: 'edit_file', cap: 'fs-write', policy: 'always', calls: 14, src: 'builtin' },
              { name: 'list_dir', cap: 'fs-read', policy: 'remember', calls: 41, src: 'builtin' },
              { name: 'shell_exec', cap: 'shell-exec', policy: 'always', calls: 9, src: 'builtin' },
              { name: 'model_pull', cap: 'registry-write', policy: 'always', calls: 3, src: 'hal0-router' },
              { name: 'restart_slot', cap: 'slot-control', policy: 'always', calls: 1, src: 'hal0-router' },
              { name: 'generate_image', cap: 'tool-call', policy: 'auto', calls: 18, src: 'omnirouter' },
              { name: 'transcribe_audio', cap: 'tool-call', policy: 'auto', calls: 7, src: 'omnirouter' },
              { name: 'embed_text', cap: 'tool-call', policy: 'auto', calls: 184, src: 'omnirouter' },
            ]"
            :key="s.name"
            class="skill-row mono"
            :data-testid="`skill-row-${s.name}`"
          >
            <span class="name">{{ s.name }}</span>
            <span class="cap">{{ s.cap }}</span>
            <span class="src">{{ s.src }}</span>
            <span>
              <span :class="['chip', `chip-${s.policy}`]">{{ s.policy }}</span>
            </span>
            <span class="right num">{{ s.calls }}</span>
          </div>
        </div>
      </template>

      <!-- Memory ────────────────────────────────────────────────── -->
      <template v-else-if="activeTab === 'memory'">
        <div class="mem-card">
          <div class="mem-head">
            <span class="mono eye">Cognee · shared</span>
            <span class="mono num big">2,847</span>
            <span class="mono dim">records · 184 MB</span>
            <span class="chip chip-ok" style="margin-left:auto">healthy</span>
          </div>
          <div class="mem-grid">
            <div class="mem-tile" v-for="t in [
              { l: 'SQLite', v: '847', sub: 'indexed text' },
              { l: 'LanceDB', v: '2,140', sub: 'vectors · 768d' },
              { l: 'Kuzu', v: '412', sub: 'graph edges' },
            ]" :key="t.l">
              <div class="mono eye">{{ t.l }}</div>
              <div class="mono num big">{{ t.v }}</div>
              <div class="mono dim">{{ t.sub }}</div>
            </div>
          </div>
          <p class="mem-link mono">
            Manage namespaces + reset from
            <router-link to="/settings#memory">Settings → Memory</router-link>.
          </p>
        </div>
      </template>

      <!-- Personas (card grid + + custom) ──────────────────────── -->
      <template v-else-if="activeTab === 'personas'">
        <div class="persona-grid">
          <div
            v-for="p in personas"
            :key="p.id || p.name"
            class="persona-card"
            :class="{ active: p.active }"
            :data-testid="`persona-card-${p.id || p.name}`"
          >
            <div class="persona-head">
              <div class="persona-name mono">{{ p.name }}</div>
              <span v-if="p.active" class="chip chip-amber">active</span>
            </div>
            <div v-if="p.slot" class="persona-meta mono">
              routes to slot <b>{{ p.slot }}</b> · {{ p.model || '—' }}
            </div>
            <p class="persona-desc">{{ p.desc || p.description || '' }}</p>
            <div class="persona-actions">
              <span v-if="p.tone" class="chip">{{ p.tone }}</span>
              <button
                class="btn-ghost sm"
                type="button"
                :data-testid="`persona-edit-${p.id || p.name}`"
                @click="openPersonaEditor(p)"
              >Edit</button>
            </div>
          </div>
          <button
            class="persona-card persona-add"
            type="button"
            data-testid="persona-add"
            @click="openPersonaCreate"
          >
            <span class="add-icon">＋</span>
            <span class="mono">custom</span>
            <span class="add-sub">Pick a chat slot, write a system prompt</span>
          </button>
        </div>
      </template>
    </div>

    <PersonaEditModal
      :open="!!editingPersona"
      :persona="editingPersona"
      :on-close="() => (editingPersona = null)"
      @saved="loadPersonas"
    />
  </div>
</template>

<style scoped>
.agent-page { display: flex; flex-direction: column; min-height: 100%; }
.hint { font-size: 11px; color: var(--color-fg-faint); }
.mono { font-family: var(--font-mono); }
.num { font-feature-settings: 'zero' 1, 'tnum' 1; }

.tabs {
  display: flex;
  gap: 4px;
  padding: 0 24px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
}
.tab {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  background: transparent;
  border: none;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: -0.01em;
  cursor: pointer;
}
.tab:hover { color: var(--color-fg); }
.tab.active { color: var(--hal0-accent); }
.tab.active::after {
  content: '';
  position: absolute;
  left: 8px; right: 8px; bottom: -1px;
  height: 2px;
  background: var(--hal0-accent);
  border-radius: 2px 2px 0 0;
}
.tab-badge {
  min-width: 16px; height: 16px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--hal0-accent);
  color: #000;
  font-size: 10px;
  font-weight: 600;
  display: grid; place-items: center;
}

.page-body { padding: 20px 24px; display: flex; flex-direction: column; gap: 14px; }

.chip {
  font-family: var(--font-mono);
  font-size: 10.5px;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  background: var(--color-surface-2);
  white-space: nowrap;
}
.chip-ok {
  color: var(--color-success);
  border-color: color-mix(in srgb, var(--color-success) 30%, transparent);
  background: color-mix(in srgb, var(--color-success) 8%, transparent);
}
.chip-amber {
  color: var(--hal0-accent);
  border-color: color-mix(in srgb, var(--hal0-accent) 35%, transparent);
  background: color-mix(in srgb, var(--hal0-accent) 12%, transparent);
}
.chip-always { color: var(--color-warning); border-color: color-mix(in srgb, var(--color-warning) 30%, transparent); background: color-mix(in srgb, var(--color-warning) 8%, transparent); }
.chip-remember { color: var(--color-success); border-color: color-mix(in srgb, var(--color-success) 30%, transparent); background: color-mix(in srgb, var(--color-success) 8%, transparent); }
.chip-auto { color: var(--color-fg-muted); }

/* Skills */
.skill-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.skill-head {
  padding: 10px 18px;
  background: var(--hal0-bg-sunken);
  border-bottom: 1px solid var(--color-border);
  display: grid;
  grid-template-columns: 200px 160px 1fr 120px 80px;
  gap: 16px;
  font-size: 10px;
  color: var(--color-fg-faint);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.skill-head .right { text-align: right; }
.skill-row {
  padding: 11px 18px;
  border-bottom: 1px solid var(--color-border);
  display: grid;
  grid-template-columns: 200px 160px 1fr 120px 80px;
  gap: 16px;
  align-items: center;
  font-size: 12px;
}
.skill-row:last-child { border-bottom: none; }
.skill-row .name { color: var(--color-fg); font-weight: 500; }
.skill-row .cap { color: var(--color-fg-muted); }
.skill-row .src { color: var(--color-fg-faint); }
.skill-row .right { text-align: right; color: var(--color-fg); }

/* Memory */
.mem-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 18px;
}
.mem-head { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.eye { font-size: 10px; color: var(--hal0-accent); text-transform: uppercase; letter-spacing: 0.1em; }
.dim { color: var(--color-fg-muted); font-size: 12px; }
.big { font-size: 24px; color: var(--color-fg); letter-spacing: -0.02em; }
.mem-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  overflow: hidden;
}
.mem-tile { padding: 14px; border-right: 1px solid var(--color-border); }
.mem-tile:last-child { border-right: none; }
.mem-tile .big { font-size: 22px; margin-top: 4px; }
.mem-link { margin-top: 12px; font-size: 11.5px; color: var(--color-fg-muted); }
.mem-link a { color: var(--hal0-accent); }

/* Personas */
.persona-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
}
@media (max-width: 720px) { .persona-grid { grid-template-columns: 1fr; } }
.persona-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 18px;
  position: relative;
  display: flex; flex-direction: column;
  gap: 8px;
}
.persona-card.active { border-color: var(--hal0-accent); }
.persona-card.active::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  background: var(--hal0-accent);
}
.persona-head { display: flex; align-items: center; gap: 8px; }
.persona-name { font-size: 14px; font-weight: 500; color: var(--color-fg); }
.persona-meta { font-size: 11px; color: var(--color-fg-muted); }
.persona-meta b { color: var(--hal0-accent); }
.persona-desc { font-size: 12.5px; color: var(--color-fg-muted); line-height: 1.55; margin: 0; }
.persona-actions { display: flex; gap: 6px; margin-top: auto; align-items: center; justify-content: space-between; }

.persona-add {
  border-style: dashed;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  text-align: center;
}
.add-icon { font-size: 26px; color: var(--color-fg-faint); }
.add-sub { font-size: 11px; color: var(--color-fg-faint); margin-top: 2px; }

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
.btn-ghost:hover { border-color: var(--color-border-hi); color: var(--color-fg); }
</style>
