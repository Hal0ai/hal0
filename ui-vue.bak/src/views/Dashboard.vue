<script setup>
/**
 * Dashboard.vue — slice #169 (v2 dashboard / view).
 *
 * Chat-first home page. Three stacked regions inside the route:
 *
 *   1. Hero strip      ~60px   3 variants: returning | post-install
 *                              | skip-path-empty
 *   2. SnapshotStrip   ~90px   per-LLM-slot horizontal row
 *   3. Chat surface    rest    ChatActive | ChatEmpty + Composer
 *
 * The chrome (TopBar / Sidebar / Footer / BottomTabs) is owned by
 * slice #168 (App.vue); this view only paints the route body.
 *
 * The composer talks /v1/chat/completions with stream:true. SSE
 * parsing accumulates `delta.content`, `delta.reasoning_content`,
 * and `delta.tool_calls` chunks; tool calls render inline as native
 * <details> blocks in ChatActive.
 *
 * State, banners, and composer state-driving are wired through the
 * shared stores (system / lemonade / banner / tweaks). Skip-path
 * variant hides the composer entirely per the design.
 */
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useSystemStore } from '../stores/system.js'
import { useLemonadeStore } from '../stores/lemonade.js'
import { useBannerStore } from '../stores/banner.js'
import { useTweaksStore } from '../stores/tweaks.js'
import BannerStack from '../components/primitives/BannerStack.vue'
import SnapshotStrip from '../components/dashboard/SnapshotStrip.vue'
import PersonaPicker from '../components/dashboard/PersonaPicker.vue'
import Composer from '../components/dashboard/Composer.vue'
import ChatActive from '../components/dashboard/ChatActive.vue'
import ChatEmpty from '../components/dashboard/ChatEmpty.vue'

const router = useRouter()
const system = useSystemStore()
const lemonade = useLemonadeStore()
const banner = useBannerStore()
const tweaks = useTweaksStore()

// ── Hero strip ───────────────────────────────────────────────────
const HERO_DISMISS_KEY = 'hal0:hero:dismissed'
const heroDismissed = ref(false)

function readDismissed() {
  try { return sessionStorage.getItem(HERO_DISMISS_KEY) === '1' } catch { return false }
}
function dismissHero() {
  heroDismissed.value = true
  try { sessionStorage.setItem(HERO_DISMISS_KEY, '1') } catch { /* ignore */ }
}

// Variant derivation: tweak override > derived state.
// Derived rules:
//   - 0 slots configured → skip-path-empty
//   - first run within this session (no localStorage flag) → post-install
//   - else → returning
const POST_INSTALL_FLAG = 'hal0:post-install:seen'
const heroVariant = computed(() => {
  const tweak = tweaks.heroVariant
  if (tweak && tweak !== 'returning') {
    // explicit override (post-install / skip-path-empty)
    return tweak
  }
  if ((system.slots?.length ?? 0) === 0) return 'skip-path-empty'
  try {
    if (sessionStorage.getItem(POST_INSTALL_FLAG) === '1') return 'post-install'
  } catch { /* ignore */ }
  return 'returning'
})

const defaultModelName = computed(() => {
  const def = (system.slots || []).find((s) => s.is_default && (s.type || 'llm') === 'llm')
  return def?.model || 'qwen3'
})

const hostName = computed(() => system.hostname || 'hal0')
const version = computed(() => system.status?.version || lemonade.version || 'v0.2')

// ── SnapshotStrip ────────────────────────────────────────────────
// Falls out of useSystemStore.slots — SnapshotStrip reads it directly.

// ── Persona picker ───────────────────────────────────────────────
const currentPersona = ref('')
function onSwap(e) {
  // Persona swap to an NPU slot pauses voice + embed ~14s. Show the
  // composer's `swap` state briefly so the user sees that latency
  // before the next chat reply arrives.
  const isNpu = (e.slot?.device || '').toLowerCase() === 'npu'
  if (isNpu) {
    swapTarget.value = e.to
    transientState.value = 'swap'
    // Banner — npu-swap from the catalog (scope=slots; we'd see it on
    // /slots, and dashboard renders the slots+dashboard scope group).
    try { banner.show('npu-swap', { body: `Swapping NPU chat to ${e.to}. Voice + embed pause ~14s.` }) } catch { /* ignore */ }
    setTimeout(() => {
      transientState.value = null
      try { banner.dismiss('npu-swap') } catch { /* ignore */ }
    }, 3500)
  }
  // Persona-swap marker into the conversation, for the visual record.
  if (chatVariant.value === 'active' && messages.value.length > 0) {
    messages.value.push({
      id: `swap-${Date.now()}`,
      role: 'system',
      swap: { from: e.from || 'previous', to: e.to },
    })
  }
}

// ── Composer state ───────────────────────────────────────────────
const transientState = ref(null) // 'sending' | 'streaming' | 'swap'
const swapTarget = ref('')

const composerState = computed(() => {
  // 1) Tweak override (dev preview)
  const tweak = tweaks.composerState
  if (tweak && tweak !== 'idle') return tweak
  // 2) Lemonade offline beats everything live
  if (lemonade.health === 'down') return 'offline'
  // 3) Transient run-state from in-flight chat
  if (transientState.value) return transientState.value
  return 'idle'
})

function restartLemond() {
  // Best-effort — backend route may 501 on the dev stub. Either way
  // we just kick the daemon and let /v1/health polling reflect the
  // result. No client-side optimism on success.
  fetch('/api/lemonade/restart', { method: 'POST' }).catch(() => { /* noop */ })
}

// ── Chat surface ─────────────────────────────────────────────────
// Variant: tweak override > derived (empty when 0 messages).
const messages = ref([])
const chatVariant = computed(() => {
  const tweak = tweaks.chatVariant
  if (tweak === 'active' || tweak === 'empty') return tweak
  return messages.value.length === 0 ? 'empty' : 'active'
})

function seedFromPrompt(p) {
  // ChatEmpty chip pick — drop it straight into the composer.
  if (composerRef.value) {
    composerRef.value.text = p
    composerRef.value.focus()
  }
}

const composerRef = ref(null)
const abortCtrl = ref(null)

async function onSubmit({ text }) {
  if (!text) return
  const userId = `u-${Date.now()}`
  messages.value.push({ id: userId, role: 'user', content: text })

  const aId = `a-${Date.now()}`
  const persona = currentPersona.value || defaultModelName.value
  const assistant = {
    id: aId,
    role: 'assistant',
    persona,
    content: '',
    reasoning: '',
    tool_calls: [],
  }
  messages.value.push(assistant)

  transientState.value = 'sending'
  abortCtrl.value = new AbortController()

  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        model: persona,
        messages: messages.value
          .filter((m) => m.role === 'user' || m.role === 'assistant')
          .map((m) => ({ role: m.role, content: m.content || '' })),
        stream: true,
        max_tokens: 512,
      }),
      signal: abortCtrl.value.signal,
    })
    if (!resp.ok) {
      const txt = await resp.text()
      assistant.content = `Error: HTTP ${resp.status} — ${txt.slice(0, 160)}`
      transientState.value = null
      return
    }

    transientState.value = 'streaming'
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
          if (!d) continue
          if (d.reasoning_content) assistant.reasoning += d.reasoning_content
          if (d.content) assistant.content += d.content
          if (Array.isArray(d.tool_calls)) {
            for (const raw of d.tool_calls) {
              const id = raw.id || `tc-${aId}-${assistant.tool_calls.length}`
              let tc = assistant.tool_calls.find((t) => t.id === id)
              if (!tc) {
                tc = { id, name: raw.function?.name || raw.name || 'tool', args: '', result: null }
                assistant.tool_calls.push(tc)
              }
              if (raw.function?.arguments) tc.args += raw.function.arguments
              else if (raw.arguments) tc.args += raw.arguments
            }
          }
        } catch { /* ignore partial json */ }
      }
    }
  } catch (e) {
    if (e?.name !== 'AbortError') {
      assistant.content = (assistant.content || '') + `\n\n[stream error: ${e?.message || e}]`
    }
  } finally {
    transientState.value = null
    abortCtrl.value = null
  }
}

function onStop() {
  abortCtrl.value?.abort()
  transientState.value = null
}

// ── Lifecycle ────────────────────────────────────────────────────
onMounted(() => {
  heroDismissed.value = readDismissed()
  // Make sure the system store has slot data — chrome's footer kicks
  // the lemonade poller, but the slot list comes from /api/status.
  if (!system.status) system.fetchStatus()
  // E2E hook — let Playwright seed a synthetic assistant message
  // (tool-call collapsibility spec depends on having a tool block in
  // the DOM without having to stream a stub SSE response). Gated on
  // dev so prod doesn't expose it. Cypress-style global; Playwright
  // calls it via page.evaluate.
  if (import.meta.env.DEV) {
    if (typeof window !== 'undefined') {
      window.__hal0DashTest = {
        pushMessage(m) { messages.value.push(m) },
        clearMessages() { messages.value = [] },
        getMessages() { return JSON.parse(JSON.stringify(messages.value)) },
      }
    }
  }
})

// Mock SSE / live: nothing to do here; useApi handles its own routes.

// Cleanup banners on unmount (best-effort).
watch(() => router.currentRoute.value.path, () => {
  try { banner.dismiss('npu-swap') } catch { /* ignore */ }
})
</script>

<template>
  <div class="dash-route">
    <!-- Dashboard-scoped banners (includes global) -->
    <BannerStack scope="dashboard" />

    <!-- ── Hero strip ─────────────────────────────────────────── -->
    <div
      v-if="!heroDismissed"
      class="hero-strip"
      :class="`hero-${heroVariant}`"
      data-testid="dash-hero"
      :data-variant="heroVariant"
    >
      <div v-if="heroVariant === 'returning'" class="greet">
        Welcome back. <b>{{ hostName }}</b> · <span class="dim">{{ version }}</span>
      </div>

      <div v-else-if="heroVariant === 'post-install'" class="greet">
        Welcome to hal0. <b>hal0-Pro</b> is loaded.
        Try a message — <b>primary</b> ({{ defaultModelName }}) is your default chat persona.
      </div>

      <div v-else class="greet">
        No models loaded yet.
      </div>

      <div class="spacer" />

      <button
        v-if="heroVariant === 'post-install'"
        type="button"
        class="hero-action"
        data-testid="hero-tour"
      >Take the tour</button>

      <template v-else-if="heroVariant === 'skip-path-empty'">
        <button
          type="button"
          class="hero-action"
          data-testid="hero-pick-bundle"
          @click="router.push('/models')"
        >Pick a bundle</button>
        <button
          type="button"
          class="hero-action ghost"
          data-testid="hero-configure-slots"
          @click="router.push('/slots')"
        >Configure slots</button>
      </template>

      <button
        type="button"
        class="close"
        data-testid="hero-dismiss"
        aria-label="Dismiss hero"
        @click="dismissHero"
      >✕</button>
    </div>

    <!-- ── Snapshot strip ─────────────────────────────────────── -->
    <SnapshotStrip />

    <!-- ── Chat surface ───────────────────────────────────────── -->
    <section
      v-if="heroVariant !== 'skip-path-empty'"
      class="chat"
      data-testid="dash-chat"
      :data-variant="chatVariant"
    >
      <ChatActive v-if="chatVariant === 'active'" :messages="messages" />
      <ChatEmpty v-else @pick="seedFromPrompt" />

      <Composer
        ref="composerRef"
        :state="composerState"
        :swap-target="swapTarget"
        persona-placement="above"
        @submit="onSubmit"
        @stop="onStop"
        @restart="restartLemond"
      >
        <template #persona>
          <PersonaPicker
            v-model="currentPersona"
            :no-tools="composerState === 'no-tools'"
            :disabled="composerState === 'offline'"
            @swap="onSwap"
          />
        </template>
      </Composer>
    </section>

    <!-- ── Skip-path-empty (composer hidden by design) ────────── -->
    <section v-else class="dash-empty" data-testid="dash-empty">
      <h3>Pick a bundle to get hal0 chatting.</h3>
      <p>You haven't loaded any models yet. Bundles install a curated
        stack (chat + embed + voice) wired into the right slots.</p>
      <div class="dash-empty-actions">
        <button class="hero-action" @click="router.push('/models')">Pick a bundle</button>
        <button class="hero-action ghost" @click="router.push('/slots')">Configure slots</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.dash-route {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px 20px 24px;
  max-width: 1440px;
  margin: 0 auto;
  width: 100%;
}

/* ── Hero strip ─────────────────────────────────────────────────── */
.hero-strip {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px;
  border: 1px solid var(--color-border, var(--line, #2a2a2a));
  border-radius: 8px;
  background: var(--color-surface, var(--bg-1, #111));
  position: relative;
  min-height: 60px;
}
.hero-strip.hero-post-install {
  background:
    radial-gradient(circle at 0% 50%, color-mix(in oklab, var(--hal0-accent, #feaf00) 12%, transparent), transparent 60%),
    var(--color-surface, var(--bg-1, #111));
}
.hero-strip .greet {
  font-size: 14px;
  color: var(--color-fg, var(--fg, #e5e5e5));
  line-height: 1.4;
}
.hero-strip .greet b {
  color: var(--hal0-accent, var(--accent, #feaf00));
  font-weight: 500;
  font-family: var(--font-mono, var(--jbm, monospace));
}
.hero-strip .greet .dim {
  color: var(--color-fg-muted, var(--fg-3, #888));
  font-family: var(--font-mono, var(--jbm, monospace));
}
.hero-strip .spacer { flex: 1; }
.hero-action {
  padding: 6px 12px;
  background: var(--hal0-accent, var(--accent, #feaf00));
  color: #0a0a0a;
  border: 1px solid var(--hal0-accent, var(--accent, #feaf00));
  border-radius: 4px;
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
}
.hero-action:hover { filter: brightness(1.06); }
.hero-action.ghost {
  background: transparent;
  color: var(--color-fg, var(--fg, #e5e5e5));
  border-color: var(--color-border, var(--line, #2a2a2a));
}
.hero-action.ghost:hover { border-color: var(--hal0-accent, var(--accent, #feaf00)); color: var(--hal0-accent, var(--accent, #feaf00)); filter: none; }
.hero-strip .close {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-fg-faint, var(--fg-4, #777));
  background: transparent;
  border: none;
  cursor: pointer;
  border-radius: 4px;
  font-size: 12px;
}
.hero-strip .close:hover {
  color: var(--color-fg, var(--fg, #e5e5e5));
  background: var(--color-surface-2, var(--bg-2, #181818));
}

/* ── Chat surface ──────────────────────────────────────────────── */
.chat {
  border: 1px solid var(--color-border, var(--line, #2a2a2a));
  border-radius: 8px;
  background: var(--color-surface, var(--bg-1, #111));
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 480px;
  overflow: hidden;
  position: relative;
}

/* ── Skip-path empty state ─────────────────────────────────────── */
.dash-empty {
  border: 1px solid var(--color-border, var(--line, #2a2a2a));
  border-radius: 8px;
  background:
    radial-gradient(circle at 50% 0%, color-mix(in oklab, var(--hal0-accent, #feaf00) 18%, transparent), transparent 60%),
    var(--color-surface, var(--bg-1, #111));
  padding: 80px 40px 64px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 18px;
}
.dash-empty h3 {
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 18px;
  font-weight: 500;
  margin: 0;
  color: var(--color-fg, var(--fg, #e5e5e5));
}
.dash-empty p {
  margin: 0;
  max-width: 480px;
  color: var(--color-fg-muted, var(--fg-3, #888));
  font-size: 13px;
  line-height: 1.55;
}
.dash-empty-actions {
  display: flex;
  gap: 10px;
  margin-top: 8px;
}
</style>
