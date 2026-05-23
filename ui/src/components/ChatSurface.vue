<script setup>
/**
 * ChatSurface.vue — Dashboard chat panel (PR-18, plan §11).
 *
 * The user-facing surface for the OmniRouter tool-calling loop wired in
 * PR-16. Three new pieces on top of the legacy Test chat panel:
 *
 *   1. Persona dropdown — picks the active chat slot among enabled
 *      ``type=llm`` slots. Default = ``primary``; bundles can add
 *      ``agent`` (NPU) or ``coder`` slots (ADR-0010 / installer
 *      manifests). The dropdown is the source of truth for which model
 *      gets posted in ``body.model`` (matches the OmniRouter caller-slot
 *      resolution rule in :func:`v1._maybe_run_omni_loop`).
 *
 *   2. Mic button (push-to-talk) — records via MediaRecorder, POSTs to
 *      ``/v1/audio/transcriptions``, inserts the transcript into the
 *      chat input. Disabled with a toast when the ``transcription``
 *      capability is not enabled (no `stt` / `stt-npu` slot serving).
 *
 *   3. Image button — opens a modal with prompt + optional input image
 *      (for edit). Submit → ``/v1/images/generations`` (no input image)
 *      or ``/v1/images/generations`` with ``image`` field (edit). The
 *      result PNG renders inline in the chat thread. Disabled when no
 *      ``image`` slot is enabled.
 *
 * OmniRouter opt-in: the "Use tools" toggle is ON by default when the
 * selected persona's model advertises the ``tool-calling`` label. The
 * send path attaches ``"omni": true`` to the chat-completions body —
 * the body field shape PR-16 settled on (see
 * :func:`v1.chat_completions`). When the toggle is OFF or the persona
 * lacks tool-calling, the body is unmodified vanilla chat.
 *
 * Tool-call indicator: when OmniRouter runs and the non-streaming
 * response includes ``_hal0.tool_calls`` (the PR-16 trace surface),
 * each tool call renders inline as "Calling tool: <name>..." then its
 * result. Generated images render as <img>; transcription appears as
 * text; embed/rerank results collapse into <details> JSON.
 *
 * Anti-scope:
 *   - History persistence is NOT added (plan §11 anti-scope; v0.3).
 *   - No file uploads beyond image-for-edit.
 *   - No new OmniRouter tools — PR-16 locked the 8-tool set.
 */
import { computed, nextTick, onMounted, onBeforeUnmount, ref } from 'vue'
import Card from './Card.vue'
import { api } from '../composables/useApi.js'
import { useSystemStore } from '../stores/system.js'
import { useCapabilities } from '../composables/useCapabilities.js'
import { useToastsStore } from '../stores/toasts.js'

const system = useSystemStore()
const toasts = useToastsStore()
const { selections: capSelections } = useCapabilities()

// ── Persona dropdown ─────────────────────────────────────────────────
// The chat-type slots known to the dashboard. ``primary`` is always
// rendered; ``agent`` + ``coder`` show up when their slot definitions
// land (NPU bundle / Pro+ bundle respectively). Disabled slots are
// dimmed in the dropdown so the user sees what bundles unlock more
// personas without picking a broken one.
const PERSONA_SLOT_TYPE = 'llm'
const personas = computed(() => {
  const out = []
  for (const s of system.slots) {
    if (s.type !== PERSONA_SLOT_TYPE) continue
    if (!s.model_default) continue  // no model configured → can't dispatch
    out.push({
      name: s.name,
      model: s.model_default,
      labels: Array.isArray(s.labels) ? s.labels : [],
      enabled: s.enabled !== false,
      lemonade_state: s.lemonade_state || 'idle',
    })
  }
  // Hard ordering: primary first, then agent, coder, then alpha.
  const order = ['primary', 'agent', 'coder']
  out.sort((a, b) => {
    const ai = order.indexOf(a.name)
    const bi = order.indexOf(b.name)
    if (ai !== -1 || bi !== -1) {
      if (ai === -1) return 1
      if (bi === -1) return -1
      return ai - bi
    }
    return a.name.localeCompare(b.name)
  })
  return out
})

const personaName = ref('')  // active slot name
const activePersona = computed(
  () => personas.value.find((p) => p.name === personaName.value) || null,
)
// When personas update (system slots load) and we have no selection,
// default to the first enabled persona — prefer ``primary``.
function ensurePersonaSelected() {
  if (personaName.value && personas.value.some((p) => p.name === personaName.value)) return
  const first = personas.value.find((p) => p.enabled) || personas.value[0]
  if (first) personaName.value = first.name
}

// ── OmniRouter toggle ────────────────────────────────────────────────
// Default ON when the persona supports tool-calling; the toggle then
// becomes a kill-switch for cases where the user wants vanilla chat
// (e.g. asking the model to literally describe a tool rather than
// invoking it). When the persona doesn't carry ``tool-calling`` the
// toggle is hidden — there's nothing to opt in to.
const omniEnabled = ref(true)
const personaToolCalling = computed(
  () => !!activePersona.value && activePersona.value.labels.includes('tool-calling'),
)

// ── Capability gating for mic + image buttons ───────────────────────
// Reads the live ``selections`` payload from /api/capabilities (PR-11
// capability orchestrator). A capability is "ready" when its selection
// is enabled AND its slot is loaded — disabled selections still mean
// "user opted out", and idle slots mean "model is configured but not
// running yet", which we surface as a disabled button with a tooltip
// pointing to the Slots page.
const micReady = computed(() => {
  const sel = capSelections.value?.voice?.stt
  return !!(sel && sel.enabled && (sel.status === 'serving' || sel.status === 'ready'))
})
const imageReady = computed(() => {
  const sel = capSelections.value?.img?.gen || capSelections.value?.img?.img
  if (!sel) {
    // Fallback for the legacy single-slot image setup before the
    // capability split — surface the bare ``img`` slot's state.
    const slot = system.slots.find((s) => s.name === 'img')
    return !!(slot && slot.enabled !== false && (slot.status === 'serving' || slot.status === 'ready' || slot.lemonade_state === 'loaded'))
  }
  return !!(sel.enabled && (sel.status === 'serving' || sel.status === 'ready'))
})

// ── Conversation thread ──────────────────────────────────────────────
// Plain in-memory list. Each entry:
//   { id, role: 'user'|'assistant'|'tool', content, toolName?, image?, error? }
// History persistence is anti-scope (plan §11). On a fresh page load
// the thread is empty.
const thread = ref([])
const threadEl = ref(null)
let _nextId = 1
function pushMessage(msg) {
  thread.value.push({ id: _nextId++, ...msg })
  nextTick(() => {
    if (threadEl.value) threadEl.value.scrollTop = threadEl.value.scrollHeight
  })
}

const chatInput = ref('')
const chatBusy = ref(false)

// ── Send ────────────────────────────────────────────────────────────
async function send() {
  const text = chatInput.value.trim()
  if (!text) return
  if (!activePersona.value) {
    toasts.error('No chat persona selected — pick a slot from the dropdown')
    return
  }
  pushMessage({ role: 'user', content: text })
  chatInput.value = ''
  chatBusy.value = true

  const body = {
    model: activePersona.value.model,
    messages: thread.value
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .filter((m) => typeof m.content === 'string' && m.content)
      .map((m) => ({ role: m.role, content: m.content })),
    max_tokens: 512,
  }
  if (omniEnabled.value && personaToolCalling.value) {
    body.omni = true
  }

  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!resp.ok) {
      const txt = await resp.text()
      let msg = `HTTP ${resp.status}`
      try {
        const j = JSON.parse(txt)
        msg = j?.error?.message || msg
      } catch { /* ignore */ }
      pushMessage({ role: 'assistant', error: msg })
      return
    }
    const j = await resp.json()
    // Render any tool calls the OmniRouter ran (PR-16 trace surface).
    const tcalls = j?._hal0?.tool_calls
    if (Array.isArray(tcalls)) {
      for (const tc of tcalls) {
        pushMessage({
          role: 'tool',
          toolName: tc.name,
          content: typeof tc.result === 'string'
            ? tc.result
            : JSON.stringify(tc.result || tc.arguments || {}, null, 2),
          image: tc.image_url || (tc.name === 'generate_image' || tc.name === 'edit_image'
            ? tc?.result?.data?.[0]?.url
            : null),
        })
      }
    }
    const choice = j?.choices?.[0]?.message
    const answer = choice?.content || ''
    if (answer) pushMessage({ role: 'assistant', content: answer })
    else if (!Array.isArray(tcalls) || tcalls.length === 0) {
      pushMessage({ role: 'assistant', error: 'Empty response from model' })
    }
  } catch (e) {
    pushMessage({ role: 'assistant', error: e?.message || String(e) })
  } finally {
    chatBusy.value = false
  }
}

// ── Voice (push-to-talk) ────────────────────────────────────────────
const recording = ref(false)
const transcribing = ref(false)
let _mediaRecorder = null
let _audioChunks = []
let _stream = null

async function micPress() {
  if (!micReady.value) {
    toasts.error('No voice slot enabled — set up STT on the Slots page')
    return
  }
  if (recording.value || transcribing.value) return
  try {
    _stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    _audioChunks = []
    _mediaRecorder = new MediaRecorder(_stream)
    _mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) _audioChunks.push(e.data)
    }
    _mediaRecorder.onstop = onMicStop
    _mediaRecorder.start()
    recording.value = true
  } catch (e) {
    toasts.error(`Mic error: ${e?.message || e}`)
    _cleanupStream()
  }
}
function micRelease() {
  if (!recording.value || !_mediaRecorder) return
  try {
    _mediaRecorder.stop()
  } catch { /* idempotent */ }
  recording.value = false
}
async function onMicStop() {
  transcribing.value = true
  try {
    const blob = new Blob(_audioChunks, { type: 'audio/webm' })
    const fd = new FormData()
    fd.append('file', blob, 'recording.webm')
    // The model field is required by the OpenAI contract — leave it
    // empty and let the dispatcher's default route pick the configured
    // transcription slot's model. The selection from /api/capabilities
    // is the source of truth.
    const sttModel = capSelections.value?.voice?.stt?.model || 'whisper-1'
    fd.append('model', sttModel)
    const resp = await fetch('/v1/audio/transcriptions', { method: 'POST', body: fd })
    if (!resp.ok) {
      const txt = await resp.text()
      let msg = `HTTP ${resp.status}`
      try {
        const j = JSON.parse(txt)
        msg = j?.error?.message || msg
      } catch { /* ignore */ }
      toasts.error(`Transcription failed: ${msg}`)
      return
    }
    const j = await resp.json()
    const text = (j?.text || '').trim()
    if (text) {
      chatInput.value = chatInput.value ? `${chatInput.value} ${text}` : text
    } else {
      toasts.error('Transcription returned empty')
    }
  } catch (e) {
    toasts.error(`Transcription error: ${e?.message || e}`)
  } finally {
    transcribing.value = false
    _cleanupStream()
  }
}
function _cleanupStream() {
  if (_stream) {
    for (const t of _stream.getTracks()) {
      try { t.stop() } catch { /* ignore */ }
    }
    _stream = null
  }
  _mediaRecorder = null
  _audioChunks = []
}
onBeforeUnmount(_cleanupStream)

// ── Image generation modal ──────────────────────────────────────────
const imgModalOpen = ref(false)
const imgPrompt = ref('')
const imgInputUrl = ref('')  // data: URL when an input image is attached
const imgBusy = ref(false)
const imgFileInput = ref(null)

function openImgModal() {
  if (!imageReady.value) {
    toasts.error('No image slot enabled — set up image gen on the Slots page')
    return
  }
  imgModalOpen.value = true
  imgPrompt.value = ''
  imgInputUrl.value = ''
}
function closeImgModal() {
  imgModalOpen.value = false
  imgPrompt.value = ''
  imgInputUrl.value = ''
}
function onImgFile(e) {
  const file = e?.target?.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => { imgInputUrl.value = String(reader.result || '') }
  reader.readAsDataURL(file)
}
async function submitImg() {
  if (!imgPrompt.value.trim()) return
  imgBusy.value = true
  try {
    const isEdit = !!imgInputUrl.value
    const body = {
      prompt: imgPrompt.value,
      model: capSelections.value?.img?.gen || capSelections.value?.img?.img?.model || 'sdxl-turbo',
      n: 1,
      response_format: 'url',
    }
    if (isEdit) body.image = imgInputUrl.value
    // /v1/images/generations handles both gen + edit (edit branch
    // detected by presence of ``image`` field; the provider's
    // translator picks the img2img workflow). See PR-16 §generate_image
    // / edit_image tool dispatch.
    const resp = await fetch('/v1/images/generations', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!resp.ok) {
      const txt = await resp.text()
      let msg = `HTTP ${resp.status}`
      try {
        const j = JSON.parse(txt)
        msg = j?.error?.message || msg
      } catch { /* ignore */ }
      pushMessage({
        role: 'tool',
        toolName: isEdit ? 'edit_image' : 'generate_image',
        error: msg,
      })
      closeImgModal()
      return
    }
    const j = await resp.json()
    const url = j?.data?.[0]?.url
    const b64 = j?.data?.[0]?.b64_json
    pushMessage({
      role: 'tool',
      toolName: isEdit ? 'edit_image' : 'generate_image',
      content: imgPrompt.value,
      image: url || (b64 ? `data:image/png;base64,${b64}` : null),
    })
    closeImgModal()
  } catch (e) {
    pushMessage({
      role: 'tool',
      toolName: 'generate_image',
      error: e?.message || String(e),
    })
    closeImgModal()
  } finally {
    imgBusy.value = false
  }
}

// ── Lifecycle ───────────────────────────────────────────────────────
onMounted(() => {
  ensurePersonaSelected()
  // The system store re-polls slots, so personas can land late; ensure
  // we still pick one. A watcher would be cleaner but Vue's reactivity
  // already covers the computed; this single-pass re-check after first
  // mount handles the dashboard-cold-start case.
  setTimeout(ensurePersonaSelected, 50)
  setTimeout(ensurePersonaSelected, 500)
})
</script>

<template>
  <Card class="chat-surface" data-testid="chat-surface">
    <!-- ── Header row: persona dropdown + omni toggle ─────────── -->
    <div class="chat-head">
      <label class="chat-persona-label">
        <span class="dim">Persona</span>
        <select
          v-model="personaName"
          class="chat-persona"
          data-testid="chat-persona"
          :disabled="chatBusy"
        >
          <option v-if="!personas.length" value="">No chat slots</option>
          <option
            v-for="p in personas"
            :key="p.name"
            :value="p.name"
            :disabled="!p.enabled"
          >
            {{ p.name }} · {{ p.model }}{{ p.enabled ? '' : ' (disabled)' }}
          </option>
        </select>
      </label>
      <label
        v-if="personaToolCalling"
        class="chat-omni-toggle"
        :title="`OmniRouter (tool-calling) is ${omniEnabled ? 'enabled' : 'disabled'} for this persona`"
      >
        <input
          v-model="omniEnabled"
          type="checkbox"
          data-testid="chat-omni-toggle"
        />
        <span>Use tools</span>
      </label>
    </div>

    <!-- ── Thread ─────────────────────────────────────────────── -->
    <div
      ref="threadEl"
      class="chat-thread"
      :class="{ empty: !thread.length }"
      data-testid="chat-thread"
    >
      <template v-if="!thread.length">
        <span class="text-muted mono-text">
          Ask anything · {{ activePersona ? activePersona.model : 'no persona' }}
          <template v-if="personaToolCalling && omniEnabled">
            · tools enabled
          </template>
        </span>
      </template>
      <template v-else>
        <div
          v-for="m in thread"
          :key="m.id"
          class="chat-msg"
          :class="`role-${m.role}`"
        >
          <span class="msg-role mono-text">
            <template v-if="m.role === 'tool'">tool · {{ m.toolName }}</template>
            <template v-else>{{ m.role }}</template>
          </span>
          <div class="msg-body">
            <span v-if="m.error" class="text-danger">{{ m.error }}</span>
            <template v-else>
              <img
                v-if="m.image"
                :src="m.image"
                class="msg-image"
                :alt="m.toolName === 'edit_image' ? 'Edited image' : 'Generated image'"
                data-testid="chat-tool-image"
              />
              <details
                v-if="m.role === 'tool' && !m.image && m.content && /^[\[{]/.test(m.content)"
                class="msg-tool-json"
              >
                <summary>JSON result</summary>
                <pre>{{ m.content }}</pre>
              </details>
              <span v-else-if="m.content" class="msg-text">{{ m.content }}</span>
            </template>
          </div>
        </div>
      </template>
    </div>

    <!-- ── Input row: mic + text + image + send ───────────────── -->
    <div class="chat-input-row">
      <button
        class="chat-iconbtn"
        type="button"
        :class="{ recording, busy: transcribing }"
        :disabled="!micReady || chatBusy || transcribing"
        :title="micReady ? 'Hold to record voice' : 'No voice slot enabled'"
        data-testid="chat-mic-btn"
        @mousedown="micPress"
        @mouseup="micRelease"
        @mouseleave="micRelease"
        @touchstart.prevent="micPress"
        @touchend.prevent="micRelease"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
          <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
          <line x1="12" y1="19" x2="12" y2="23"/>
          <line x1="8" y1="23" x2="16" y2="23"/>
        </svg>
      </button>

      <input
        v-model="chatInput"
        class="chat-input"
        :placeholder="recording ? 'Recording…' : 'Ask the model anything…'"
        data-testid="chat-input"
        :disabled="chatBusy || recording"
        @keydown.enter="send"
      />

      <button
        class="chat-iconbtn"
        type="button"
        :disabled="!imageReady || chatBusy"
        :title="imageReady ? 'Generate or edit an image' : 'No image slot enabled'"
        data-testid="chat-image-btn"
        @click="openImgModal"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <rect x="3" y="3" width="18" height="18" rx="2"/>
          <circle cx="8.5" cy="8.5" r="1.5"/>
          <polyline points="21 15 16 10 5 21"/>
        </svg>
      </button>

      <button
        class="btn-primary chat-send"
        type="button"
        :disabled="chatBusy || !chatInput.trim() || !activePersona"
        data-testid="chat-send"
        @click="send"
      >
        {{ chatBusy ? 'Sending…' : 'Send' }}
      </button>
    </div>

    <!-- ── Image modal ────────────────────────────────────────── -->
    <div
      v-if="imgModalOpen"
      class="chat-img-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="chat-img-modal-title"
      data-testid="chat-image-modal"
      @click.self="closeImgModal"
    >
      <div class="chat-img-modal-body">
        <h3 id="chat-img-modal-title" class="chat-img-modal-title">
          {{ imgInputUrl ? 'Edit image' : 'Generate image' }}
        </h3>
        <textarea
          v-model="imgPrompt"
          class="chat-img-prompt"
          placeholder="Describe the image…"
          rows="3"
          data-testid="chat-image-prompt"
          :disabled="imgBusy"
        ></textarea>
        <label class="chat-img-file-row">
          <span class="dim">Input image (optional, for edit)</span>
          <input
            ref="imgFileInput"
            type="file"
            accept="image/*"
            data-testid="chat-image-file"
            :disabled="imgBusy"
            @change="onImgFile"
          />
        </label>
        <img v-if="imgInputUrl" :src="imgInputUrl" class="chat-img-preview" alt="Input preview" />
        <div class="chat-img-modal-actions">
          <button
            class="btn-secondary"
            type="button"
            :disabled="imgBusy"
            @click="closeImgModal"
          >
            Cancel
          </button>
          <button
            class="btn-primary"
            type="button"
            data-testid="chat-image-submit"
            :disabled="imgBusy || !imgPrompt.trim()"
            @click="submitImg"
          >
            {{ imgBusy ? 'Generating…' : (imgInputUrl ? 'Edit' : 'Generate') }}
          </button>
        </div>
      </div>
    </div>
  </Card>
</template>

<style scoped>
.chat-surface { padding: 14px 16px; display: flex; flex-direction: column; gap: 12px; }

.chat-head {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.chat-persona-label { display: inline-flex; align-items: center; gap: 8px; font-size: 12px; }
.chat-persona {
  padding: 5px 10px;
  font-size: 12px;
  font-family: var(--font-mono);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  color: var(--color-fg);
  min-width: 200px;
}
.chat-omni-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--color-fg-muted);
  cursor: pointer;
}
.chat-omni-toggle input { accent-color: var(--hal0-accent); }

/* ── Thread ──────────────────────────────────────────────────────── */
.chat-thread {
  min-height: 120px;
  max-height: 360px;
  overflow-y: auto;
  padding: 10px 12px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  font-size: 13px;
  line-height: 1.5;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.chat-thread.empty { align-items: flex-start; justify-content: flex-start; }
.chat-msg { display: flex; gap: 10px; align-items: flex-start; }
.msg-role {
  flex-shrink: 0;
  width: 80px;
  text-transform: uppercase;
  font-size: 10.5px;
  letter-spacing: 0.06em;
  color: var(--hal0-accent);
  padding-top: 2px;
}
.chat-msg.role-user .msg-role { color: var(--color-success); }
.chat-msg.role-assistant .msg-role { color: var(--color-fg-muted); }
.chat-msg.role-tool .msg-role { color: var(--color-warning); }
.msg-body { flex: 1; min-width: 0; }
.msg-text { white-space: pre-wrap; word-break: break-word; color: var(--color-fg); }
.msg-image { max-width: 100%; max-height: 320px; border-radius: var(--radius); border: 1px solid var(--color-border); display: block; }
.msg-tool-json summary { cursor: pointer; color: var(--color-fg-muted); font-size: 11px; font-family: var(--font-mono); }
.msg-tool-json pre { margin: 6px 0 0; padding: 8px 10px; background: var(--hal0-bg-sunken); border-radius: var(--radius); font-size: 11px; white-space: pre-wrap; word-break: break-word; }

/* ── Input row ───────────────────────────────────────────────────── */
.chat-input-row { display: flex; gap: 8px; align-items: stretch; }
.chat-iconbtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  color: var(--color-fg-muted);
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}
.chat-iconbtn:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }
.chat-iconbtn:disabled { opacity: 0.4; cursor: not-allowed; }
.chat-iconbtn.recording { background: color-mix(in oklch, var(--color-danger) 20%, var(--color-surface-2)); color: var(--color-danger); border-color: var(--color-danger); animation: pulse 1.2s infinite; }
.chat-iconbtn.busy { color: var(--hal0-accent); }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.55; } }
.chat-input {
  flex: 1;
  padding: 6px 12px;
  font-size: 13px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  color: var(--color-fg);
}
.chat-input:focus, .chat-persona:focus { outline: 2px solid var(--color-accent); outline-offset: 1px; }
.chat-send { padding: 6px 16px; flex-shrink: 0; }

/* ── Image modal ─────────────────────────────────────────────────── */
.chat-img-modal {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.chat-img-modal-body {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 20px 22px;
  width: 520px;
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 32px);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.chat-img-modal-title { margin: 0; font-size: 15px; color: var(--color-fg); }
.chat-img-prompt {
  width: 100%;
  padding: 8px 10px;
  font-size: 13px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  color: var(--color-fg);
  resize: vertical;
  font-family: inherit;
}
.chat-img-file-row { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.chat-img-preview { max-width: 100%; max-height: 200px; border-radius: var(--radius); border: 1px solid var(--color-border); }
.chat-img-modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 4px; }

/* ── Utility ─────────────────────────────────────────────────────── */
.dim { color: var(--color-fg-faint); font-family: var(--font-mono); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; }
.text-danger { color: var(--color-danger); }
.text-muted  { color: var(--color-fg-faint); }
.mono-text   { font-family: var(--font-mono); font-size: 12px; }

.btn-primary { padding: 8px 18px; border-radius: var(--radius); background: var(--hal0-accent); color: #000; font-family: var(--font-mono); font-size: 12px; font-weight: 500; border: none; cursor: pointer; flex-shrink: 0; transition: background 0.15s; }
.btn-primary:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-secondary { padding: 6px 14px; border-radius: var(--radius); border: 1px solid var(--color-border); background: transparent; color: var(--color-fg-muted); font-family: var(--font-mono); font-size: 12px; cursor: pointer; }
.btn-secondary:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
