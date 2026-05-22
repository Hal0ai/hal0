<script setup>
/**
 * Agent.vue — host page for Phase 8's /agent route.
 *
 * Spec: combo A+B layout with horizontal tabs at the top (the sidebar
 * already exists in the dashboard chrome, so a second one would be
 * redundant). Tab state lives in the URL (?tab=…) so the view is
 * shareable and reload-stable.
 *
 * Per ADR-0004 §3 the Chat tab is CLI-only; when the bundled agent is
 * service-shape (Hermes) the tab disappears and the dashboard surface
 * is the "Open Hermes UI" link-out on Overview instead.
 */
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAgentStore } from '../stores/agent.js'
import PageHeader from '../components/PageHeader.vue'
import AgentOverviewTab from '../components/agent/AgentOverviewTab.vue'
import AgentInboxTab from '../components/agent/AgentInboxTab.vue'
import AgentActivityTab from '../components/agent/AgentActivityTab.vue'
import AgentChatTab from '../components/agent/AgentChatTab.vue'

const route = useRoute()
const router = useRouter()
const agent = useAgentStore()

// Tab vocabulary. Chat is hidden when the agent is not CLI-shape so
// users don't see a tab that immediately tells them it's irrelevant.
const ALL_TABS = ['overview', 'inbox', 'activity', 'chat']

const tabsAvailable = computed(() => {
  return ALL_TABS.filter((t) => {
    if (t === 'chat') return agent.shape === 'cli'
    return true
  })
})

const activeTab = computed(() => {
  const t = String(route.query.tab || 'overview')
  return tabsAvailable.value.includes(t) ? t : 'overview'
})

function selectTab(tab) {
  if (tab === activeTab.value) return
  router.replace({ query: { ...route.query, tab } })
}

// Title surface — uses the agent's display name when one is bundled.
const pageTitle = computed(() => {
  const a = agent.currentAgent
  if (!a) return 'Agent'
  return a.name
})

const pageSub = computed(() => {
  const a = agent.currentAgent
  if (!a) return 'Install a bundled agent that uses hal0 as its local AI + MCP provider.'
  if (agent.shape === 'cli') return 'Bundled CLI agent — invoked from your terminal.'
  if (agent.shape === 'service') return 'Bundled service agent — its own web surface, wired to hal0.'
  return 'Bundled agent.'
})

// Bootstrap on mount: the bell may not have been rendered yet (deep
// link to /agent from a fresh session), so we trigger the store here
// too. ensureBootstrapped is idempotent — second caller is a no-op.
onMounted(() => {
  agent.ensureBootstrapped()
  // Refresh activity if we landed directly on that tab.
  if (activeTab.value === 'activity') agent.fetchActivity({ limit: 50 })
})

// Lazy-fetch activity when the user clicks into the tab. Saves the
// journalctl shell-out cost on every /agent visit.
watch(activeTab, (t) => {
  if (t === 'activity') agent.fetchActivity({ limit: 50 })
})
</script>

<template>
  <div class="agent-page">
    <PageHeader
      eyebrow="Bundled agent"
      :title="pageTitle"
      :subtitle="pageSub"
    />

    <!-- Hide tabs entirely in the empty-state ─────────────────── -->
    <nav
      v-if="agent.currentAgent"
      class="tabs"
      role="tablist"
      aria-label="Agent sections"
    >
      <button
        v-for="t in tabsAvailable"
        :key="t"
        type="button"
        role="tab"
        :aria-selected="activeTab === t"
        :class="['tab', { active: activeTab === t }]"
        @click="selectTab(t)"
      >
        <span class="tab-label">{{ t }}</span>
        <span v-if="t === 'inbox' && agent.pendingCount > 0" class="tab-badge mono">{{ agent.pendingCount }}</span>
      </button>
    </nav>

    <div class="page-body">
      <AgentOverviewTab v-if="activeTab === 'overview' || !agent.currentAgent" />
      <AgentInboxTab v-else-if="activeTab === 'inbox'" />
      <AgentActivityTab v-else-if="activeTab === 'activity'" />
      <AgentChatTab v-else-if="activeTab === 'chat'" />
    </div>
  </div>
</template>

<style scoped>
.agent-page { display: flex; flex-direction: column; min-height: 100%; }

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
  text-transform: capitalize;
  transition: color 0.12s;
}
.tab:hover { color: var(--color-fg); }
.tab.active {
  color: var(--hal0-accent);
}
.tab.active::after {
  content: '';
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: -1px;
  height: 2px;
  background: var(--hal0-accent);
  border-radius: 2px 2px 0 0;
  box-shadow: 0 0 12px -2px var(--hal0-accent);
}
.tab:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}

.tab-badge {
  min-width: 16px;
  height: 16px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--hal0-accent);
  color: #000;
  font-size: 10px;
  font-weight: 600;
  font-feature-settings: 'zero' 1, 'tnum' 1;
  display: grid;
  place-items: center;
  line-height: 1;
}

.page-body {
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
