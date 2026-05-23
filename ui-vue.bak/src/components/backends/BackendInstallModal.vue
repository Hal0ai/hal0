<script setup>
/**
 * BackendInstallModal.vue — Modal shell for /api/backends/<id>/install.
 *
 * Mirrors the React `BackendInstallModal` in
 *   /tmp/hal0-design/hal0-v2/project/dash/flow-modals.jsx (lines 33–71).
 *
 * The FLM rerouting (lines 35–36) happens in the parent BackendsView,
 * which routes FLM installs to `FlmDebGuideModal` directly. This modal
 * is the generic non-FLM path — version + size + ETA + sha-verify +
 * restart preflight + an info pill for the ROCm-detected case.
 */
import { computed } from 'vue'
import Modal from '../primitives/Modal.vue'
import { useBackendsStore } from '../../stores/backends.js'
import { useToastStore } from '../../stores/toast.js'

const props = defineProps({
  open: { type: Boolean, default: false },
  backend: { type: Object, default: null },
  onClose: { type: Function, default: () => {} },
})

const backends = useBackendsStore()
const toasts = useToastStore()

const id = computed(() => props.backend?.id || '')
const ver = computed(() => props.backend?.version || '—')
const hasRocm = computed(() => id.value.includes('rocm'))
const sizeHint = computed(() => props.backend?.size_hint || '~210 MB')
const etaHint = computed(() => props.backend?.eta_hint || '~2 min on a 100 Mbps link')

async function onInstall() {
  if (!id.value) return
  // Optimistic toast — real progress lands when the SSE/poll path ships;
  // for now this surfaces the operation and the store re-fetches on
  // success.
  toasts.push(`Installing ${id.value} — ETA ${etaHint.value}`, 'info')
  try {
    await backends.install(id.value)
    toasts.push(`${id.value} installed`, 'ok')
  } catch (e) {
    toasts.push(e?.message || 'Install failed', 'err')
  } finally {
    props.onClose()
  }
}
</script>

<template>
  <Modal
    :open="open"
    :on-close="onClose"
    eyebrow="Backends · install"
    :title="`Install ${id || 'backend'}?`"
    :width="580"
  >
    <span data-testid="backend-install-modal" hidden></span>
    <p class="copy">
      This downloads the <span class="mono fg">{{ id }}</span> binary and
      places it under <span class="mono fg">/opt/lemonade/bin</span>.
    </p>
    <div class="meta-box">
      <div><span class="lbl">version</span> · <span class="val">{{ ver }}</span></div>
      <div><span class="lbl">size</span> · <span class="val">{{ sizeHint }}</span></div>
      <div><span class="lbl">eta</span> · <span class="val">{{ etaHint }}</span></div>
      <div><span class="lbl">verify</span> · <span class="val text-success">sha-256 pinned ✓</span></div>
      <div><span class="lbl">restart</span> · <span class="val text-warn">lemond restart required after install</span></div>
    </div>
    <div v-if="hasRocm" class="info-pill">
      ROCm detected on this host. The install will use the gfx1151 build.
    </div>

    <template #foot>
      <span>Backend ships pinned with sha-256 verification.</span>
      <span class="foot-actions">
        <button class="btn-ghost sm" type="button" @click="onClose">Cancel</button>
        <button
          class="btn-primary sm"
          type="button"
          data-testid="backend-install-confirm"
          @click="onInstall"
        >Install</button>
      </span>
    </template>
  </Modal>
</template>

<style scoped>
.copy { font-size: 13px; color: var(--color-fg-muted); line-height: 1.6; margin: 0 0 14px; }
.mono { font-family: var(--font-mono); }
.fg { color: var(--color-fg); }
.meta-box {
  padding: 12px;
  background: var(--hal0-bg-sunken);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.7;
}
.meta-box .lbl { color: var(--color-fg-faint); }
.meta-box .val { color: var(--color-fg); }
.text-success { color: var(--color-success); }
.text-warn { color: var(--color-warning); }

.info-pill {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: var(--radius);
  background: color-mix(in oklch, var(--hal0-accent) 10%, var(--color-surface));
  border: 1px solid color-mix(in oklch, var(--hal0-accent) 30%, transparent);
  color: var(--hal0-accent);
  font-size: 12px;
  font-family: var(--font-mono);
}

.foot-actions { display: inline-flex; gap: 8px; }
.btn-primary {
  padding: 6px 14px;
  border-radius: var(--radius);
  background: var(--hal0-accent);
  color: #000;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
  border: none;
  cursor: pointer;
}
.btn-primary.sm { font-size: 11.5px; padding: 5px 12px; }
.btn-ghost {
  padding: 6px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  cursor: pointer;
}
.btn-ghost.sm { font-size: 11.5px; padding: 5px 12px; }
</style>
