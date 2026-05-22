<script setup>
/**
 * AgentApprovalBell.vue — canonical pending-approval surface (ADR-0004 §5).
 *
 * Lives in the TopBar, always visible regardless of route. Click opens
 * the modal inbox (lazy-mounted via the parent's `v-if`). Pulses on SSE
 * enqueued events so an operator notices a new request even while their
 * focus is on another tab.
 *
 * Bootstrap: this is the first component the dashboard renders that
 * needs the agent store, so it triggers ensureBootstrapped — opens the
 * SSE socket once for the whole session, used by both the bell and the
 * /agent inbox tab.
 */
import { onMounted, ref, watch } from 'vue'
import { useAgentStore } from '../stores/agent.js'
import AgentApprovalInbox from './AgentApprovalInbox.vue'

const agent = useAgentStore()
const open = ref(false)
const pulse = ref(false)

onMounted(() => {
  agent.ensureBootstrapped()
})

// Pulse animation trigger — flips on whenever the store reports a new
// approval, off after 1.2s so the next pulse re-fires the keyframe.
watch(
  () => agent.newApprovalPulse,
  () => {
    pulse.value = false
    requestAnimationFrame(() => {
      pulse.value = true
      setTimeout(() => { pulse.value = false }, 1200)
    })
  },
)

function onClick(e) {
  e.stopPropagation()
  open.value = true
}
</script>

<template>
  <button
    class="bell"
    :class="{ 'has-pending': agent.pendingCount > 0, pulsing: pulse }"
    type="button"
    @click="onClick"
    :aria-label="`Pending approvals (${agent.pendingCount})`"
    :aria-haspopup="'dialog'"
    :title="agent.pendingCount > 0 ? `${agent.pendingCount} pending approval${agent.pendingCount === 1 ? '' : 's'}` : 'No pending approvals'"
  >
    <svg
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
      stroke-width="2"
      aria-hidden="true"
    >
      <path
        stroke-linecap="round"
        stroke-linejoin="round"
        d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
      />
    </svg>
    <span v-if="agent.pendingCount > 0" class="badge mono">{{ agent.pendingCount }}</span>
  </button>

  <!-- Modal — lazy-mounted; v-if so its EventSource (n/a, store owns it)
       and DOM tree don't exist until the user clicks the bell. -->
  <AgentApprovalInbox v-if="open" :open="open" @close="open = false" />
</template>

<style scoped>
.bell {
  position: relative;
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius);
  background: transparent;
  border: 1px solid transparent;
  color: var(--color-fg-muted);
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.1s, color 0.1s, border-color 0.1s;
}
.bell:hover { background: var(--color-surface-2); color: var(--color-fg); }
.bell:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}
.bell.has-pending { color: var(--hal0-accent); }

.badge {
  position: absolute;
  top: -4px;
  right: -4px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 999px;
  background: var(--hal0-accent);
  color: #000;
  font-size: 10px;
  font-weight: 600;
  font-feature-settings: 'zero' 1, 'tnum' 1;
  display: grid;
  place-items: center;
  line-height: 1;
  border: 1px solid var(--color-surface);
}

@keyframes bell-pulse {
  0%   { transform: scale(1);   box-shadow: 0 0 0 0 color-mix(in srgb, var(--hal0-accent) 50%, transparent); }
  50%  { transform: scale(1.18); box-shadow: 0 0 0 6px color-mix(in srgb, var(--hal0-accent) 0%, transparent); }
  100% { transform: scale(1);   box-shadow: 0 0 0 0 color-mix(in srgb, var(--hal0-accent) 0%, transparent); }
}
.bell.pulsing { animation: bell-pulse 1.2s ease-out; }
@media (prefers-reduced-motion: reduce) {
  .bell.pulsing { animation: none; }
}
</style>
