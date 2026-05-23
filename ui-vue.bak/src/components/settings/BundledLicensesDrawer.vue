<script setup>
/**
 * components/settings/BundledLicensesDrawer.vue — right-side drawer
 * listing every bundled-runtime license the About section advertises.
 *
 * The drawer is fully self-contained: data lives in BUNDLED, which is a
 * lightweight roll-up of the runtimes hal0 ships with (Lemonade,
 * llama.cpp, whisper.cpp, sd.cpp, FLM, Kokoro, Cognee). When the
 * `/api/about/licenses` endpoint lands we swap the constant for a fetch
 * — the drawer's render code stays unchanged.
 */
import Drawer from '../primitives/Drawer.vue'

defineProps({
  open: { type: Boolean, default: false },
})

const emit = defineEmits(['close'])

const BUNDLED = Object.freeze([
  { name: 'AMD Lemonade',  license: 'Apache-2.0', url: 'https://github.com/lemonade-sdk/lemonade' },
  { name: 'llama.cpp',     license: 'MIT',        url: 'https://github.com/ggerganov/llama.cpp' },
  { name: 'whisper.cpp',   license: 'MIT',        url: 'https://github.com/ggerganov/whisper.cpp' },
  { name: 'stable-diffusion.cpp', license: 'MIT', url: 'https://github.com/leejet/stable-diffusion.cpp' },
  { name: 'FLM (FastFlowLM)', license: 'Apache-2.0', url: 'https://github.com/FastFlowLM/FastFlowLM' },
  { name: 'Kokoro TTS',    license: 'Apache-2.0', url: 'https://github.com/hexgrad/Kokoro-82M' },
  { name: 'Cognee',        license: 'Apache-2.0', url: 'https://github.com/topoteretes/cognee' },
  { name: 'Vue 3',         license: 'MIT',        url: 'https://github.com/vuejs/core' },
  { name: 'FastAPI',       license: 'MIT',        url: 'https://github.com/tiangolo/fastapi' },
])

function onClose() { emit('close') }
</script>

<template>
  <Drawer
    :open="open"
    :on-close="onClose"
    title="Bundled licenses"
    eyebrow="About"
    :width="520"
  >
    <div class="body" data-testid="bundled-licenses-drawer">
      <p class="hint">
        hal0 ships these third-party runtimes verbatim. Each entry links to its
        upstream source. Source archives are also published with every hal0
        release — see <code class="mono">SOURCES.tar.zst</code> on the release page.
      </p>
      <ul class="lst">
        <li v-for="r in BUNDLED" :key="r.name">
          <a class="nm" :href="r.url" target="_blank" rel="noopener noreferrer">
            {{ r.name }}
          </a>
          <span class="lic mono">{{ r.license }}</span>
        </li>
      </ul>
    </div>
    <template #foot>
      <span>hal0 itself is Apache-2.0.</span>
      <button type="button" class="btn ghost sm" @click="onClose">Close</button>
    </template>
  </Drawer>
</template>

<style scoped>
.body { display: flex; flex-direction: column; gap: 14px; }
.hint { font-size: 12px; color: var(--fg-3, var(--color-fg-muted)); margin: 0; line-height: 1.55; }
.hint code { background: var(--bg, var(--color-surface)); padding: 1px 4px; border-radius: 3px; }
.mono { font-family: var(--jbm, var(--font-mono)); }
.lst { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.lst li {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  padding: 8px 10px;
  background: var(--bg, var(--color-surface));
  border: 1px solid var(--line-soft, var(--color-border));
  border-radius: var(--rad-sm, 4px);
}
.nm { color: var(--accent, var(--hal0-accent)); text-decoration: none; font-size: 12.5px; }
.nm:hover { text-decoration: underline; }
.lic { font-size: 11px; color: var(--fg-4, var(--color-fg-faint)); }
.btn {
  background: transparent;
  border: 1px solid var(--line, var(--color-border));
  color: var(--fg-2, var(--color-fg-muted));
  border-radius: var(--rad-sm, 4px);
  padding: 5px 11px;
  font-family: var(--jbm, var(--font-mono));
  font-size: 11px;
  cursor: pointer;
}
.btn.ghost { background: transparent; }
.btn.sm { padding: 5px 11px; font-size: 11px; }
</style>
