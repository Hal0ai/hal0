<script setup>
/**
 * AgentOverviewTab.vue — identity card + action cluster + shim status.
 *
 * Empty state: full-width install picker (pi-coder / Hermes-Agent).
 * Installed: identity card → action cluster → shim status → journald
 * link (service-shape only, hidden for CLI per ADR-0004 §3).
 */
import { computed, ref } from 'vue'
import { useAgentStore } from '../../stores/agent.js'
import { useToastsStore } from '../../stores/toasts.js'
import Card from '../Card.vue'

const agent = useAgentStore()
const toasts = useToastsStore()

const installBusy = ref(null)   // name being installed
const switchBusy = ref(false)
const uninstallBusy = ref(false)

const a = computed(() => agent.currentAgent)
const shape = computed(() => agent.shape)
const status = computed(() => agent.status)

// Status dot: amber when broken, green when installed, faint when none.
// Service-shape brokenness usually means the systemd unit failed start;
// CLI-shape brokenness means the shim install left files mid-state.
const statusDot = computed(() => {
  if (status.value === 'installed') return 'live'
  if (status.value === 'broken') return 'error'
  return 'idle'
})

const journaldUnit = computed(() => {
  if (shape.value !== 'service') return null
  return `hal0-agent-${a.value.name}.service`
})

async function onInstall(name) {
  installBusy.value = name
  try {
    await agent.install(name)
    toasts.info(`${name} installed`)
  } catch (e) {
    // 409 with code 'agent.hermes_not_hal0_aware' is the actionable
    // case — surface verbatim so the operator knows the upstream needs
    // hal0-awareness before this works.
    toasts.error(e?.message || `Install ${name} failed`)
  } finally {
    installBusy.value = null
  }
}

async function onSwitch(toName) {
  switchBusy.value = true
  try {
    await agent.switchAgent(toName)
    toasts.info(`Switched to ${toName}`)
  } catch (e) {
    toasts.error(e?.message || 'Switch failed')
  } finally {
    switchBusy.value = false
  }
}

async function onUninstall() {
  if (!a.value) return
  if (!confirm(`Uninstall ${a.value.name}?`)) return
  uninstallBusy.value = true
  try {
    await agent.uninstall(a.value.name)
    toasts.info(`${a.value.name} uninstalled`)
  } catch (e) {
    toasts.error(e?.message || 'Uninstall failed')
  } finally {
    uninstallBusy.value = false
  }
}

function copyConfigPath() {
  if (!a.value?.config_path) return
  try {
    navigator.clipboard.writeText(a.value.config_path)
    toasts.info('Path copied')
  } catch {
    toasts.error('Clipboard unavailable')
  }
}
</script>

<template>
  <div class="overview">
    <!-- Empty state: nothing installed ─────────────────────────── -->
    <Card v-if="!a" class="empty-card">
      <h2 class="empty-title">Install an agent</h2>
      <p class="empty-desc">
        Bundle a third-party agent that uses hal0 as its local AI provider and
        consumes hal0's MCP servers. v0.2 supports two picks — choose one.
        Switch later from this page.
      </p>
      <div class="picker">
        <div class="pick">
          <div class="pick-head">
            <span class="pick-name">pi-coder</span>
            <span class="pick-shape pill-cli">CLI</span>
          </div>
          <p class="pick-desc">
            Terminal coding agent. Minimal-by-design (read/write/edit/bash).
            Installs the hal0 MCP adapter + leaves pi-memory-md in place.
          </p>
          <button
            class="btn-primary"
            type="button"
            :disabled="installBusy === 'pi-coder'"
            @click="onInstall('pi-coder')"
          >{{ installBusy === 'pi-coder' ? 'Installing…' : 'Install pi-coder' }}</button>
        </div>
        <div class="pick">
          <div class="pick-head">
            <span class="pick-name">Hermes-Agent</span>
            <span class="pick-shape pill-svc">service</span>
          </div>
          <p class="pick-desc">
            Long-running service agent with its own web surface. hal0
            link-outs to it OWUI-style; integration lives upstream in
            Hermes itself.
          </p>
          <button
            class="btn-primary"
            type="button"
            :disabled="installBusy === 'hermes'"
            @click="onInstall('hermes')"
          >{{ installBusy === 'hermes' ? 'Installing…' : 'Install Hermes-Agent' }}</button>
        </div>
      </div>
    </Card>

    <!-- Installed: identity card + actions ─────────────────────── -->
    <template v-else>
      <Card class="id-card">
        <div class="id-head">
          <div class="id-l">
            <span class="status-dot" :class="`dot-${statusDot}`" :title="status" aria-hidden="true" />
            <h2 class="id-name">{{ a.name }}</h2>
            <span v-if="shape" class="shape-pill" :class="shape === 'cli' ? 'pill-cli' : 'pill-svc'">
              {{ shape === 'cli' ? 'CLI' : 'service' }}
            </span>
            <span class="track-latest" title="Tracks upstream main (ADR-0004 §3)">track-latest</span>
          </div>
          <div class="id-r">
            <span class="status-text">{{ status }}</span>
          </div>
        </div>
        <div class="id-grid">
          <div class="id-row">
            <span class="id-l-lbl">Installed</span>
            <span class="id-val">{{ a.installed_at || '—' }}</span>
          </div>
          <div class="id-row">
            <span class="id-l-lbl">Data dir</span>
            <span class="id-val mono">{{ a.data_dir || '—' }}</span>
          </div>
          <div class="id-row">
            <span class="id-l-lbl">Config</span>
            <span class="id-val mono">
              {{ a.config_path || '—' }}
              <button v-if="a.config_path" class="copy-btn" type="button" @click="copyConfigPath" title="Copy path">⧉</button>
            </span>
          </div>
        </div>

        <div class="actions">
          <button
            v-if="a.name !== 'pi-coder'"
            class="btn-secondary"
            type="button"
            :disabled="switchBusy"
            @click="onSwitch('pi-coder')"
          >{{ switchBusy ? 'Switching…' : 'Switch to pi-coder' }}</button>
          <button
            v-if="a.name !== 'hermes'"
            class="btn-secondary"
            type="button"
            :disabled="switchBusy"
            @click="onSwitch('hermes')"
          >{{ switchBusy ? 'Switching…' : 'Switch to Hermes-Agent' }}</button>
          <button
            class="btn-danger"
            type="button"
            :disabled="uninstallBusy"
            @click="onUninstall"
          >{{ uninstallBusy ? 'Uninstalling…' : 'Uninstall' }}</button>
        </div>
      </Card>

      <!-- Shim status / MCP wiring (ADR-0004 §6 — pi-coder ships the shim,
           Hermes grows native awareness upstream).  -->
      <Card class="shim-card">
        <h3 class="shim-title">MCP wiring</h3>
        <ul class="shim-list">
          <li>
            <span class="shim-l">Admin MCP</span>
            <span class="shim-v mono">/mcp/admin</span>
            <span class="shim-tag shim-ok">wired</span>
          </li>
          <li>
            <span class="shim-l">Memory MCP</span>
            <span class="shim-v mono">/mcp/memory</span>
            <span class="shim-tag shim-ok">wired</span>
          </li>
          <li v-if="a.name === 'pi-coder'">
            <span class="shim-l">pi-mcp-adapter</span>
            <span class="shim-v mono">proxy-tool routing</span>
            <span class="shim-tag shim-ok">installed</span>
          </li>
          <li v-if="a.name === 'pi-coder'">
            <span class="shim-l">pi-memory-md</span>
            <span class="shim-v mono">project markdown</span>
            <span class="shim-tag shim-ok">in place</span>
          </li>
        </ul>
      </Card>

      <!-- journald link (service-shape only) ─────────────────── -->
      <Card v-if="journaldUnit" class="journald-card">
        <div class="journald-row">
          <div>
            <p class="journald-title">systemd unit</p>
            <p class="journald-unit mono">{{ journaldUnit }}</p>
          </div>
          <router-link
            :to="{ path: '/logs', query: { unit: journaldUnit } }"
            class="btn-secondary"
          >View journald →</router-link>
        </div>
      </Card>
    </template>
  </div>
</template>

<style scoped>
.overview { display: flex; flex-direction: column; gap: 14px; }

/* ── Empty state ──────────────────────────────────────────────── */
.empty-card { padding: 24px; }
.empty-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0 0 6px;
  letter-spacing: -0.01em;
}
.empty-desc {
  font-size: 13px;
  color: var(--color-fg-muted);
  margin: 0 0 18px;
  line-height: 1.55;
  max-width: 60ch;
}
.picker { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 720px) { .picker { grid-template-columns: 1fr; } }
.pick {
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface-2);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pick-head { display: flex; align-items: baseline; gap: 8px; }
.pick-name {
  font-family: var(--font-mono);
  font-weight: 600;
  font-size: 14px;
  color: var(--color-fg);
}
.pick-shape {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 1px 7px;
  border-radius: 999px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.pill-cli {
  background: color-mix(in srgb, var(--hal0-accent) 14%, transparent);
  color: var(--hal0-accent);
  border: 1px solid color-mix(in srgb, var(--hal0-accent) 35%, transparent);
}
.pill-svc {
  background: color-mix(in srgb, var(--color-success) 14%, transparent);
  color: var(--color-success);
  border: 1px solid color-mix(in srgb, var(--color-success) 35%, transparent);
}
.pick-desc {
  font-size: 12px;
  color: var(--color-fg-muted);
  margin: 0;
  line-height: 1.5;
}

/* ── Identity card ────────────────────────────────────────────── */
.id-card { padding: 18px 20px; }
.id-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
.id-l { display: flex; align-items: center; gap: 10px; min-width: 0; }
.id-name {
  font-family: var(--font-mono);
  font-size: 18px;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0;
  letter-spacing: -0.01em;
}
.shape-pill {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 1px 7px;
  border-radius: 999px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.track-latest {
  font-family: var(--font-mono);
  font-size: 9.5px;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--color-surface-3);
  color: var(--color-fg-faint);
  border: 1px solid var(--color-border);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-live { background: var(--color-success); box-shadow: 0 0 8px var(--color-success); }
.dot-error { background: var(--color-danger); box-shadow: 0 0 6px var(--color-danger); }
.dot-idle { background: var(--color-fg-faint); }
.status-text {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-fg-muted);
}

.id-grid { display: flex; flex-direction: column; gap: 6px; padding: 12px 14px; background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: var(--radius); }
.id-row { display: flex; gap: 14px; font-family: var(--font-mono); font-size: 12px; }
.id-l-lbl { width: 100px; color: var(--color-fg-faint); flex-shrink: 0; }
.id-val { color: var(--color-fg); overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.id-val.mono { font-feature-settings: 'zero' 1, 'ss02' 1; word-break: break-all; }
.copy-btn {
  background: transparent;
  border: none;
  color: var(--color-fg-faint);
  cursor: pointer;
  margin-left: 6px;
  padding: 0 4px;
  font-size: 11px;
}
.copy-btn:hover { color: var(--hal0-accent); }

.actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }

/* ── Shim card ────────────────────────────────────────────────── */
.shim-card { padding: 14px 16px; }
.shim-title {
  font-size: 11px;
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--hal0-accent);
  font-weight: 500;
  margin: 0 0 10px;
}
.shim-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 6px; }
.shim-list li {
  display: grid;
  grid-template-columns: 140px 1fr auto;
  gap: 12px;
  align-items: center;
  font-family: var(--font-mono);
  font-size: 12px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--color-border);
}
.shim-list li:last-child { border-bottom: none; }
.shim-l { color: var(--color-fg-muted); }
.shim-v { color: var(--color-fg); font-feature-settings: 'zero' 1, 'ss02' 1; }
.shim-tag {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.shim-ok {
  background: color-mix(in srgb, var(--color-success) 14%, transparent);
  color: var(--color-success);
  border: 1px solid color-mix(in srgb, var(--color-success) 30%, transparent);
}

/* ── journald link ───────────────────────────────────────────── */
.journald-card { padding: 14px 16px; }
.journald-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.journald-title {
  font-size: 10.5px;
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-fg-faint);
  margin: 0 0 4px;
}
.journald-unit {
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--color-fg);
  margin: 0;
}

/* ── Buttons ──────────────────────────────────────────────────── */
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
  align-self: flex-start;
  transition: background 0.12s;
}
.btn-primary:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-secondary {
  padding: 7px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  cursor: pointer;
  text-decoration: none;
  transition: border-color 0.12s, color 0.12s;
}
.btn-secondary:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }

.btn-danger {
  padding: 7px 14px;
  border-radius: var(--radius);
  border: 1px solid color-mix(in srgb, var(--color-danger) 35%, var(--color-border));
  background: transparent;
  color: var(--color-danger);
  font-family: var(--font-mono);
  font-size: 12px;
  cursor: pointer;
  transition: background 0.12s;
}
.btn-danger:hover:not(:disabled) {
  background: color-mix(in srgb, var(--color-danger) 12%, transparent);
}
</style>
