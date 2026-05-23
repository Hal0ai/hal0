<script setup>
/**
 * NoBundledAgentCard.vue — pi-coder vs Hermes radio + Install button.
 *
 * Mirrors the React `NoBundledAgentCard` in
 *   /tmp/hal0-design/hal0-v2/project/dash/flow-modals.jsx (lines 283–328).
 *
 * Lives inside the Overview tab when no agent is installed. Default
 * pick is Hermes (service shape) — `pi-coder` is also offered but is
 * gated to v0.3 per the v0.2 narrowing memory.
 */
import { ref } from 'vue'
import { useAgentStore } from '../../stores/agent.js'
import { useToastStore } from '../../stores/toast.js'

const agent = useAgentStore()
const toasts = useToastStore()

const pick = ref('hermes')
const installing = ref(false)

const OPTIONS = [
  {
    id: 'pi-coder',
    name: 'pi-coder',
    shape: 'CLI shape · invoked per task',
    tools: 4,
    src: '@earendil-works/pi-coding-agent',
  },
  {
    id: 'hermes',
    name: 'Hermes-Agent',
    shape: 'Service shape · resident systemd unit',
    tools: 12,
    src: 'hal0-agent-hermes.service',
  },
]

async function install() {
  installing.value = true
  toasts.push(
    `Installing ${pick.value === 'pi-coder' ? 'pi-coder CLI' : 'Hermes-Agent service'} — ETA ~1 min`,
    'info',
  )
  try {
    await agent.install(pick.value)
    toasts.push(`${pick.value} installed`, 'ok')
  } catch (e) {
    toasts.push(e?.message || 'Install failed', 'err')
  } finally {
    installing.value = false
  }
}
</script>

<template>
  <div class="no-agent" data-testid="no-bundled-agent">
    <div class="head">
      <div class="icon">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="9" />
          <path d="M8 14s1.5 2 4 2 4-2 4-2" />
          <line x1="9" y1="9" x2="9.01" y2="9" />
          <line x1="15" y1="9" x2="15.01" y2="9" />
        </svg>
      </div>
      <div>
        <div class="title mono">No bundled agent installed</div>
        <div class="sub mono">Pick an agent shape · install once · agents persist across reboots</div>
      </div>
    </div>

    <div class="opts">
      <label
        v-for="o in OPTIONS"
        :key="o.id"
        class="opt"
        :class="{ active: pick === o.id }"
        :data-testid="`no-agent-opt-${o.id}`"
      >
        <input
          type="radio"
          name="no-agent-pick"
          :value="o.id"
          :checked="pick === o.id"
          @change="pick = o.id"
        />
        <span class="opt-name mono">{{ o.name }}</span>
        <div class="opt-shape mono">{{ o.shape }}</div>
        <div class="opt-meta">
          <span class="chip">{{ o.tools }} tools</span>
          <span class="chip dim">{{ o.src }}</span>
        </div>
      </label>
    </div>

    <div class="actions">
      <button
        class="btn-primary"
        type="button"
        :disabled="installing"
        data-testid="no-agent-install"
        @click="install"
      >
        {{ installing ? 'Installing…' : `Install ${pick === 'pi-coder' ? 'pi-coder' : 'Hermes'}` }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.no-agent {
  background: var(--color-surface);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  padding: 24px;
  display: flex; flex-direction: column;
  gap: 14px;
}
.head { display: flex; align-items: center; gap: 14px; }
.icon {
  width: 44px; height: 44px; border-radius: 8px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  display: inline-flex; align-items: center; justify-content: center;
  color: var(--color-fg-faint);
  flex-shrink: 0;
}
.title { font-size: 16px; font-weight: 500; letter-spacing: -0.02em; color: var(--color-fg); }
.sub { font-size: 11.5px; color: var(--color-fg-muted); margin-top: 2px; }

.opts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 720px) { .opts { grid-template-columns: 1fr; } }
.opt {
  padding: 16px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface-2);
  cursor: pointer;
  display: block;
}
.opt.active { border-color: var(--hal0-accent); }
.opt input[type="radio"] { accent-color: var(--hal0-accent); margin-right: 8px; }
.opt-name { font-size: 14px; font-weight: 500; color: var(--color-fg); }
.opt-shape { font-size: 11.5px; color: var(--color-fg-muted); margin-top: 6px; }
.opt-meta { display: flex; gap: 6px; margin-top: 8px; }
.chip {
  font-family: var(--font-mono);
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--color-surface-3);
  color: var(--color-fg);
  border: 1px solid var(--color-border);
}
.chip.dim { color: var(--color-fg-faint); }
.mono { font-family: var(--font-mono); }

.actions { display: flex; justify-content: flex-end; }
.btn-primary {
  padding: 8px 16px;
  border-radius: var(--radius);
  background: var(--hal0-accent);
  color: #000;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
  border: none;
  cursor: pointer;
}
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
