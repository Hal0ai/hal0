<script setup>
/**
 * FlmDebGuideModal.vue — Linux-only manual `.deb` install for FLM.
 *
 * Mirrors the React `FlmDebGuideModal` in
 *   /tmp/hal0-design/hal0-v2/project/dash/flow-modals.jsx (lines 129–169).
 *
 * Triggers from BackendsView on install/reinstall of `flm:npu`. FLM
 * ships a .deb directly from AMD — no auto-installer on Linux.
 */
import { computed } from 'vue'
import Modal from '../primitives/Modal.vue'
import { useToastStore } from '../../stores/toast.js'
import { useSystemStore } from '../../stores/system.js'

const props = defineProps({
  open: { type: Boolean, default: false },
  backend: { type: Object, default: null },
  onClose: { type: Function, default: () => {} },
})

const toasts = useToastStore()
const system = useSystemStore()

const ver = computed(() => (props.backend?.version || 'v0.9.42').replace(/^v/, ''))
const host = computed(() => system.status?.hostname || 'this host')

const cmd = computed(() => `# 1. Download the FLM Linux .deb from AMD
wget https://amd.com/flm/flm_${ver.value}_amd64.deb

# 2. Install (requires sudo)
sudo dpkg -i flm_${ver.value}_amd64.deb

# 3. Add your user to the xdna group
sudo usermod -aG xdna $USER

# 4. Reboot or re-login, then restart lemond
sudo systemctl restart hal0-lemonade`)

async function copyCommands() {
  try {
    await navigator.clipboard.writeText(cmd.value)
    toasts.push('Commands copied to clipboard', 'ok')
  } catch {
    toasts.push('Clipboard unavailable — copy manually', 'err')
  }
}
</script>

<template>
  <Modal
    :open="open"
    :on-close="onClose"
    eyebrow="FLM · manual install"
    title="Install FLM (.deb) — Linux"
    :width="680"
  >
    <!-- Hidden test marker — Modal uses Teleport so the prop-level
         data-testid never reaches the DOM; this anchor sticks inside
         the slot body so spec selectors resolve reliably. -->
    <span data-testid="flm-deb-modal" hidden></span>
    <p class="copy">
      FLM ships as a .deb directly from AMD. Run these from a shell on
      <span class="mono fg">{{ host }}</span>. After the reboot, the NPU
      trio slots (<span class="mono">agent</span>,
      <span class="mono">stt-npu</span>,
      <span class="mono">embed-npu</span>) will become configurable.
    </p>
    <pre class="cmd" data-testid="flm-deb-cmd">{{ cmd }}</pre>
    <div class="info-pill mono">
      After reboot, hal0 detects FLM automatically. You'll see a toast:
      <span class="fg">"FLM v{{ ver }} detected — NPU slots available"</span>.
    </div>

    <template #foot>
      <span>FLM's auto-installer is Windows-only. Linux requires this manual flow.</span>
      <span class="foot-actions">
        <button
          class="btn-ghost sm"
          type="button"
          data-testid="flm-deb-copy"
          @click="copyCommands"
        >Copy commands</button>
        <button
          class="btn-primary sm"
          type="button"
          @click="onClose"
        >Close</button>
      </span>
    </template>
  </Modal>
</template>

<style scoped>
.copy { font-size: 13px; color: var(--color-fg-muted); line-height: 1.6; margin: 0 0 14px; }
.mono { font-family: var(--font-mono); }
.fg { color: var(--color-fg); }

.cmd {
  margin: 0;
  padding: 14px;
  background: var(--hal0-bg-sunken);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.65;
  color: var(--color-fg-muted);
  overflow-x: auto;
  white-space: pre;
}
.info-pill {
  margin-top: 14px;
  padding: 10px 12px;
  border-radius: var(--radius);
  background: color-mix(in oklch, var(--hal0-accent) 10%, var(--color-surface));
  border: 1px solid color-mix(in oklch, var(--hal0-accent) 30%, transparent);
  color: var(--hal0-accent);
  font-size: 12px;
}

.foot-actions { display: inline-flex; gap: 8px; }
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
</style>
