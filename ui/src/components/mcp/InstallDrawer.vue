<script setup>
/**
 * mcp/InstallDrawer.vue — wraps primitives/Drawer to host two tabs:
 *   1. Catalog  — search + categories + verified card grid (12 items
 *                 in the v0.3 mock).
 *   2. From URL — paste an oci://, git+https://, npm:, uvx:, or
 *                 manifest URL; once non-empty render a resolved-
 *                 manifest preview card with Install/Cancel.
 *
 * Mirrors the React `InstallDrawer` + `InstallFromUrl` in
 *   /tmp/hal0-design-v3/dash/mcp-modals.jsx (lines 7–149).
 *
 * `install` event fires with the resolved catalog item (or the URL
 * stub `{name: 'mcp-things'}`); the parent McpView wires it through
 * useMcpStore.install().
 */
import { computed, ref } from 'vue'
import Drawer from '../primitives/Drawer.vue'
import { useToastStore } from '../../stores/toast.js'

const props = defineProps({
  open:    { type: Boolean, default: false },
  catalog: { type: Array, required: true },
  categories: { type: Array, required: true },
})

const emit = defineEmits(['close', 'install'])

const toasts = useToastStore()

const tab = ref('catalog')
const query = ref('')
const cat = ref('all')

const url = ref('')
const pasted = ref(false)

const examples = [
  { label: 'OCI image',   v: 'oci://ghcr.io/example/mcp-something:latest' },
  { label: 'npx package', v: 'npm:@some-org/mcp-things' },
  { label: 'uvx package', v: 'uvx:mcp-things' },
  { label: 'git repo',    v: 'git+https://github.com/example/mcp-things' },
  { label: 'manifest',    v: 'https://example.com/mcp.json' },
]

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return (props.catalog || []).filter((it) => {
    if (cat.value !== 'all' && it.category !== cat.value) return false
    if (q && !`${it.name} ${it.description} ${it.author}`.toLowerCase().includes(q)) return false
    return true
  })
})

function close() {
  emit('close')
}
function installItem(item) {
  emit('install', item)
}
function setExample(ex) {
  url.value = ex.v
  pasted.value = true
}
function clearUrl() {
  url.value = ''
  pasted.value = false
}
function openReadme(item) {
  toasts.push(`Opening ${item.name} README`, 'info')
}
</script>

<template>
  <Drawer
    :open="open"
    :on-close="close"
    :width="720"
    eyebrow="MCP · install"
    title="Install an MCP server"
  >
    <div class="mcp-install-tabs">
      <button
        type="button"
        :class="['mcp-install-tab', { on: tab === 'catalog' }]"
        data-testid="mcp-install-tab-catalog"
        @click="tab = 'catalog'"
      >Catalog</button>
      <button
        type="button"
        :class="['mcp-install-tab', { on: tab === 'url' }]"
        data-testid="mcp-install-tab-url"
        @click="tab = 'url'"
      >From URL / manifest</button>
    </div>

    <template v-if="tab === 'catalog'">
      <div class="mcp-install-search">
        <input
          v-model="query"
          class="mcp-install-input mono"
          data-testid="mcp-install-search"
          placeholder="Search servers, authors, descriptions…"
          autofocus
        />
      </div>
      <div class="mcp-install-cats">
        <button
          v-for="c in categories"
          :key="c.id"
          type="button"
          :class="['mcp-install-cat', { on: cat === c.id }]"
          :data-testid="`mcp-install-cat-${c.id}`"
          @click="cat = c.id"
        >{{ c.label }}</button>
      </div>
      <div class="mcp-install-list">
        <div
          v-for="item in filtered"
          :key="item.id"
          class="mcp-install-item"
          :data-testid="`mcp-install-item-${item.id}`"
        >
          <div class="mcp-install-item-h">
            <span class="mcp-install-name mono">{{ item.name }}</span>
            <span v-if="item.verified" class="mcp-install-verified mono" title="Officially maintained">✓ verified</span>
            <span class="mcp-install-author mono">by {{ item.author }}</span>
            <span class="spacer" />
            <span class="mcp-install-stars mono">{{ item.stars.toLocaleString() }} ★</span>
          </div>
          <div class="mcp-install-desc">{{ item.description }}</div>
          <div class="mcp-install-foot">
            <span class="mcp-install-cat-pill mono">{{ item.category }}</span>
            <span class="mcp-install-tools mono">{{ item.tools }} tools</span>
            <span class="spacer" />
            <button type="button" class="mcp-install-readme" @click="openReadme(item)">README</button>
            <button
              type="button"
              class="mcp-install-install"
              :data-testid="`mcp-install-${item.id}`"
              @click="installItem(item)"
            >Install</button>
          </div>
        </div>
        <div v-if="filtered.length === 0" class="mcp-install-empty mono">
          No catalog entries match. Try a different search, or paste a manifest URL.
        </div>
      </div>
    </template>

    <template v-else>
      <div class="mcp-install-url">
        <div class="mcp-install-url-h mono">URL · manifest · package spec</div>
        <input
          v-model="url"
          class="mcp-install-input mono"
          data-testid="mcp-install-url-input"
          placeholder="oci://, git+https://, npm:, uvx:, or a manifest URL"
          @input="pasted = true"
        />
        <div class="mcp-install-url-examples mono">
          <span class="dim">Examples:</span>
          <button
            v-for="(ex, i) in examples"
            :key="i"
            type="button"
            class="mcp-install-url-ex"
            :data-testid="`mcp-install-ex-${i}`"
            @click="setExample(ex)"
          >
            <span class="dim">{{ ex.label }}</span>
            <span>{{ ex.v }}</span>
          </button>
        </div>

        <div v-if="pasted && url" class="mcp-install-url-preview" data-testid="mcp-install-url-preview">
          <div class="mcp-install-url-eye mono">resolved manifest</div>
          <div class="mcp-install-url-card">
            <div class="mcp-install-url-name-row">
              <span class="mcp-install-url-name mono">mcp-things</span>
              <span class="mcp-install-url-meta mono">v0.1.0 · stdio</span>
            </div>
            <div class="mcp-install-url-desc">Manifest fetched. 5 tools, 0 resources, 0 prompts. No env vars required.</div>
            <div class="mcp-install-url-note mono">
              hal0 will spawn this server under its supervisor and add it to the list below.
            </div>
          </div>
          <div class="mcp-install-url-actions">
            <button type="button" class="mcp-install-readme" @click="clearUrl">Cancel</button>
            <button
              type="button"
              class="mcp-install-install"
              data-testid="mcp-install-url-go"
              @click="installItem({ id: 'mcp-things', name: 'mcp-things' })"
            >Install</button>
          </div>
        </div>
      </div>
    </template>

    <template #foot>
      <span class="mcp-install-foot-note">{{ catalog.length }} servers in the catalog · curated by hal0 · community-contributed</span>
      <button type="button" class="mcp-install-readme" @click="close">Done</button>
    </template>
  </Drawer>
</template>

<style scoped>
.spacer { flex: 1; }

.mcp-install-tabs {
  display: flex;
  border-bottom: 1px solid var(--line);
  margin: -4px 0 14px;
}
.mcp-install-tab {
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  padding: 8px 14px 10px;
  font-family: var(--hal0-font-mono);
  font-size: 12px;
  color: var(--fg-3);
  cursor: pointer;
  font-weight: 500;
}
.mcp-install-tab.on { color: var(--accent); border-bottom-color: var(--accent); }
.mcp-install-tab:hover { color: var(--fg); }

.mcp-install-search { margin-bottom: 12px; }
.mcp-install-input {
  width: 100%;
  padding: 8px 12px;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  color: var(--fg);
  font-size: 12px;
  font-family: var(--hal0-font-mono);
  box-sizing: border-box;
}
.mcp-install-input:focus { outline: none; border-color: var(--accent-line); }

.mcp-install-cats {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 14px;
}
.mcp-install-cat {
  background: transparent;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 3px 10px;
  font-family: var(--hal0-font-mono);
  font-size: 11px;
  color: var(--fg-3);
  cursor: pointer;
}
.mcp-install-cat.on { color: var(--accent); border-color: var(--accent-line); background: var(--accent-soft); }
.mcp-install-cat:hover { color: var(--fg); }

.mcp-install-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.mcp-install-item {
  border: 1px solid var(--line);
  border-radius: var(--rad);
  padding: 12px 14px;
  background: var(--bg-1);
  transition: border-color 0.12s ease;
}
.mcp-install-item:hover { border-color: var(--line-strong); }
.mcp-install-item-h {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.mcp-install-name {
  font-size: 13.5px;
  color: var(--fg);
  font-weight: 500;
}
.mcp-install-verified {
  font-size: 9px;
  color: var(--ok);
  border: 1px solid var(--ok-line);
  background: var(--ok-soft);
  padding: 1px 5px;
  border-radius: 2px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.mcp-install-author { font-size: 11px; color: var(--fg-4); }
.mcp-install-stars  { font-size: 11px; color: var(--fg-3); }
.mcp-install-desc   { font-size: 12.5px; color: var(--fg-2); line-height: 1.5; margin-bottom: 10px; }
.mcp-install-foot {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mcp-install-cat-pill {
  font-size: 10px;
  color: var(--fg-4);
  text-transform: lowercase;
  padding: 1px 6px;
  border: 1px solid var(--line);
  border-radius: 2px;
}
.mcp-install-tools { font-size: 11px; color: var(--fg-3); }
.mcp-install-readme {
  background: transparent;
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  color: var(--fg-3);
  font-family: var(--hal0-font-mono);
  font-size: 11px;
  padding: 4px 10px;
  cursor: pointer;
}
.mcp-install-readme:hover { color: var(--fg); border-color: var(--line-strong); }
.mcp-install-install {
  background: var(--accent);
  border: 1px solid var(--accent);
  border-radius: var(--rad-sm);
  color: #0a0a0a;
  font-family: var(--hal0-font-mono);
  font-size: 11px;
  padding: 4px 10px;
  cursor: pointer;
  font-weight: 500;
}
.mcp-install-install:hover { filter: brightness(1.06); }

.mcp-install-empty {
  padding: 32px;
  text-align: center;
  color: var(--fg-4);
  font-size: 12px;
  border: 1px dashed var(--line);
  border-radius: var(--rad);
}

.mcp-install-url-h {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fg-4);
  margin-bottom: 8px;
}
.mcp-install-url-examples {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 12px;
}
.mcp-install-url-examples .dim {
  color: var(--fg-5);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.mcp-install-url-ex {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 5px 9px;
  border: 1px solid var(--line-soft);
  border-radius: var(--rad-sm);
  background: transparent;
  color: var(--fg-2);
  font-family: var(--hal0-font-mono);
  font-size: 11.5px;
  cursor: pointer;
  text-align: left;
}
.mcp-install-url-ex:hover { border-color: var(--accent-line); color: var(--accent); }
.mcp-install-url-ex .dim {
  color: var(--fg-4);
  width: 80px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.mcp-install-url-preview {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--line-soft);
}
.mcp-install-url-eye {
  font-size: 11px;
  color: var(--fg-4);
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.mcp-install-url-card {
  padding: 12px 14px;
  border: 1px solid var(--accent-line);
  background: var(--accent-soft);
  border-radius: var(--rad);
}
.mcp-install-url-name-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 4px;
}
.mcp-install-url-name { font-size: 14px; color: var(--fg); font-weight: 500; }
.mcp-install-url-meta { font-size: 10px; color: var(--fg-4); }
.mcp-install-url-desc { font-size: 12px; color: var(--fg-3); margin-bottom: 10px; }
.mcp-install-url-note {
  font-size: 10.5px;
  color: var(--fg-4);
  padding: 8px;
  background: var(--bg);
  border-radius: var(--rad-sm);
  border: 1px solid var(--line);
}
.mcp-install-url-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  justify-content: flex-end;
}
.mcp-install-foot-note {
  color: var(--fg-4);
}
</style>
