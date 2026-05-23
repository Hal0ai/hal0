<script setup>
/**
 * RestartBanner — sticky top-of-main banner driving the self-update flow.
 *
 * Closes Team I gap #1. Wires the existing component shape to
 * /api/updates/{check, apply, status/{job_id}, rollback, channel} with
 * SSE-style polling on the apply/rollback job lifecycle. Falls back
 * gracefully when the backend's updater routes are still
 * `system.not_implemented` (renders the envelope's message inline so
 * the user knows why nothing happened).
 *
 * Wire shapes (per PLAN.md §9, Team C/D ports):
 *
 *   GET  /api/updates/check
 *     {update_available, current_version, latest_version, notes_url?,
 *      previous_available?, channel}
 *
 *   POST /api/updates/apply
 *     {job_id}
 *
 *   GET  /api/updates/status/{job_id}
 *     {state: queued|running|applied|failed, progress?: 0..100,
 *      breadcrumbs?: string[], error?: {code, message, details}}
 *
 *   POST /api/updates/rollback
 *     {job_id}                          # rollback also runs as a job
 *
 *   PUT  /api/updates/channel
 *     body: {channel: 'stable'|'nightly'}
 *     200:  {channel}
 *
 * Banner visibility, width, position, and dismiss behaviour are
 * unchanged from the prior stub — only the action surface is new.
 */
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { api, Hal0Error } from '../composables/useApi.js'
import { useSystemStore } from '../stores/system.js'

const system = useSystemStore()

// Local banner state — kept here rather than on the system store so an
// unrelated /api/status poll doesn't wipe an in-flight job. Only the
// initial check seeds visibility.
const check = ref(null)                  // last /api/updates/check response
const channel = ref('stable')            // mirrors backend channel
const jobId = ref(null)                  // current apply/rollback job
const jobKind = ref(null)                // 'apply' | 'rollback'
const jobState = ref(null)               // queued | running | applied | failed
const jobProgress = ref(0)
const jobBreadcrumbs = ref([])
const jobError = ref(null)               // {code, message, details} | null
const dismissed = ref(false)
let pollTimer = null
let checkInflight = false

const visible = computed(() => !dismissed.value && (
  !!check.value?.update_available || jobState.value === 'applied' || jobState.value === 'failed'
))

const inFlight = computed(() => jobState.value === 'queued' || jobState.value === 'running')

const showRollback = computed(() => {
  if (inFlight.value) return false
  // Show rollback when (a) we just applied and the backend confirms a
  // previous version is around, or (b) check still reports
  // previous_available on a normal load.
  if (jobState.value === 'applied' && check.value?.previous_available) return true
  return !!check.value?.previous_available && !!check.value?.update_available
})

async function doCheck() {
  if (checkInflight) return
  checkInflight = true
  try {
    const res = await api('/api/updates/check')
    check.value = res
    if (res?.channel) channel.value = res.channel
  } catch (e) {
    // If the updater endpoint is still 501 we silently swallow on the
    // boot poll — surfaces in inline error UI only when the user
    // explicitly clicks Apply / Rollback. Other failures we keep quiet
    // for the same reason: don't toast on a background check.
    if (!(e instanceof Hal0Error) || e.status !== 501) {
      check.value = null
    } else {
      // Surface the typed envelope so the banner can render *something*
      // meaningful once it's visible — but a 501 stub means
      // update_available is false, so the banner stays hidden.
      check.value = null
    }
  } finally {
    checkInflight = false
  }
}

async function doApply() {
  jobError.value = null
  jobKind.value = 'apply'
  jobState.value = 'queued'
  jobProgress.value = 0
  jobBreadcrumbs.value = []
  try {
    const res = await api('/api/updates/apply', { method: 'POST' })
    jobId.value = res?.job_id ?? null
    if (!jobId.value) throw new Hal0Error('apply returned no job_id', { code: 'system.invalid_response' })
    pollStatus()
  } catch (e) {
    handleJobError(e)
  }
}

async function doRollback() {
  jobError.value = null
  jobKind.value = 'rollback'
  jobState.value = 'queued'
  jobProgress.value = 0
  jobBreadcrumbs.value = []
  try {
    const res = await api('/api/updates/rollback', { method: 'POST' })
    jobId.value = res?.job_id ?? null
    if (!jobId.value) throw new Hal0Error('rollback returned no job_id', { code: 'system.invalid_response' })
    pollStatus()
  } catch (e) {
    handleJobError(e)
  }
}

async function pollStatus() {
  if (!jobId.value) return
  if (pollTimer) clearTimeout(pollTimer)
  try {
    const res = await api(`/api/updates/status/${encodeURIComponent(jobId.value)}`)
    if (typeof res?.state === 'string') jobState.value = res.state
    if (typeof res?.progress === 'number') jobProgress.value = res.progress
    if (Array.isArray(res?.breadcrumbs)) jobBreadcrumbs.value = res.breadcrumbs
    if (res?.error) jobError.value = res.error
    if (inFlight.value) {
      pollTimer = setTimeout(pollStatus, 1000)
    } else {
      // Terminal state — refresh check so the rollback affordance
      // accurately reflects the post-apply world.
      doCheck()
    }
  } catch (e) {
    handleJobError(e)
  }
}

function handleJobError(e) {
  jobState.value = 'failed'
  if (e instanceof Hal0Error) {
    jobError.value = { code: e.code, message: e.message, details: e.details }
  } else {
    jobError.value = { code: 'system.unknown', message: String(e?.message ?? e), details: {} }
  }
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
}

async function setChannel(next) {
  if (!next || next === channel.value || inFlight.value) return
  const prev = channel.value
  channel.value = next  // optimistic
  try {
    await api('/api/updates/channel', { method: 'PUT', body: JSON.stringify({ channel: next }) })
    // A channel switch invalidates the prior check — re-fetch so the
    // banner reflects whatever's latest on the new channel.
    await doCheck()
  } catch (e) {
    channel.value = prev
    handleJobError(e)
  }
}

function dismiss() {
  dismissed.value = true
}

onMounted(() => {
  // Poll once per session on boot — no tight loop. The check is cheap
  // server-side (HEAD on releases manifest) but we don't want to
  // hammer hal0.dev from every tab.
  doCheck()
})

onUnmounted(() => {
  if (pollTimer) clearTimeout(pollTimer)
})

// Surface system store's updateAvailable hint without coupling — some
// other path may flip it, in which case we should re-check.
const systemUpdateHint = computed(() => !!system.status?.update_available)
import { watch } from 'vue'
watch(systemUpdateHint, (v) => { if (v && !check.value) doCheck() })
</script>

<template>
  <Transition name="slide-up">
    <div v-if="visible" class="restart-banner" role="alert" aria-live="assertive">
      <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
      </svg>

      <span class="banner-msg">
        <template v-if="jobState === 'applied'">
          Update applied — restart <code>hal0-api</code> to activate.
        </template>
        <template v-else-if="jobState === 'failed' && jobError">
          <strong>{{ jobKind }} failed</strong>
          (<span class="mono">{{ jobError.code }}</span>): {{ jobError.message }}
        </template>
        <template v-else-if="inFlight">
          {{ jobKind === 'rollback' ? 'Rolling back' : 'Applying update' }}
          <span v-if="jobState === 'queued'">— queued…</span>
          <span v-else-if="jobBreadcrumbs.length > 0"> — {{ jobBreadcrumbs[jobBreadcrumbs.length - 1] }}</span>
        </template>
        <template v-else>
          Update available
          <strong v-if="check?.latest_version">
            v{{ check.current_version }} → v{{ check.latest_version }}
          </strong>
          — slots will keep running.
          <a v-if="check?.notes_url" :href="check.notes_url" target="_blank" rel="noopener noreferrer" class="banner-link">
            release notes ↗
          </a>
        </template>
      </span>

      <!-- Progress bar — shown while a job is in-flight. -->
      <span v-if="inFlight" class="banner-progress" role="progressbar" :aria-valuenow="jobProgress" aria-valuemin="0" aria-valuemax="100">
        <span class="banner-progress-fill" :style="{ width: jobProgress + '%' }" />
      </span>

      <span class="banner-actions">
        <!-- Channel switcher — only shown when no job is in flight. -->
        <span v-if="!inFlight && jobState !== 'applied'" class="banner-channel" role="group" aria-label="Update channel">
          <button
            type="button"
            class="banner-channel-btn"
            :class="{ active: channel === 'stable' }"
            @click="setChannel('stable')"
            :aria-pressed="channel === 'stable'"
          >stable</button>
          <button
            type="button"
            class="banner-channel-btn"
            :class="{ active: channel === 'nightly' }"
            @click="setChannel('nightly')"
            :aria-pressed="channel === 'nightly'"
          >nightly</button>
        </span>

        <button
          v-if="!inFlight && jobState !== 'applied' && check?.update_available"
          type="button"
          class="banner-btn"
          :disabled="inFlight"
          @click="doApply"
          data-action="apply-update"
        >Apply update</button>

        <button
          v-if="!inFlight && showRollback"
          type="button"
          class="banner-btn banner-btn-ghost"
          :disabled="inFlight"
          @click="doRollback"
          data-action="rollback"
        >Rollback</button>

        <button
          v-if="jobState === 'applied' || jobState === 'failed'"
          type="button"
          class="banner-btn banner-btn-ghost"
          @click="dismiss"
          aria-label="Dismiss banner"
        >Dismiss</button>
      </span>
    </div>
  </Transition>
</template>

<style scoped>
.restart-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 24px;
  background: color-mix(in srgb, var(--hal0-accent) 10%, var(--hal0-bg-elevated));
  border-bottom: 1px solid color-mix(in srgb, var(--hal0-accent) 35%, transparent);
  color: var(--hal0-fg);
  font-size: 13px;
}

.banner-msg { min-width: 0; }
.banner-msg code,
.banner-msg .mono { font-family: var(--font-mono); font-size: 12px; }
.banner-link {
  margin-left: 6px;
  color: var(--hal0-accent);
  text-decoration: none;
  font-size: 12px;
  font-family: var(--font-mono);
}
.banner-link:hover { text-decoration: underline; }

.banner-progress {
  display: inline-block;
  position: relative;
  width: 120px;
  height: 4px;
  background: color-mix(in srgb, var(--hal0-accent) 20%, transparent);
  border-radius: 4px;
  overflow: hidden;
  margin-left: 6px;
}
.banner-progress-fill {
  display: block;
  height: 100%;
  background: var(--hal0-accent);
  transition: width 0.3s ease;
  box-shadow: 0 0 8px color-mix(in srgb, var(--hal0-accent) 60%, transparent);
}

.banner-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.banner-channel {
  display: inline-flex;
  border: 1px solid color-mix(in srgb, var(--hal0-accent) 30%, transparent);
  border-radius: var(--radius);
  overflow: hidden;
}
.banner-channel-btn {
  padding: 2px 8px;
  background: transparent;
  border: none;
  color: var(--hal0-fg-muted);
  font-size: 11px;
  font-family: var(--font-mono);
  cursor: pointer;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.banner-channel-btn.active {
  background: var(--hal0-accent);
  color: #000;
}

.banner-btn {
  padding: 4px 12px;
  border-radius: var(--radius);
  background: var(--hal0-accent);
  color: #000;
  font-family: var(--font-mono);
  font-size: 11.5px;
  font-weight: 500;
  border: none;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.15s;
}
.banner-btn:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.banner-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.banner-btn-ghost {
  background: transparent;
  color: var(--hal0-fg-muted);
  border: 1px solid color-mix(in srgb, var(--hal0-accent) 35%, transparent);
}
.banner-btn-ghost:hover:not(:disabled) {
  background: color-mix(in srgb, var(--hal0-accent) 10%, transparent);
  color: var(--hal0-fg);
}

.slide-up-enter-active { transition: all 0.2s ease; }
.slide-up-leave-active { transition: all 0.15s ease; }
.slide-up-enter-from   { opacity: 0; transform: translateY(-100%); }
.slide-up-leave-to     { opacity: 0; transform: translateY(-100%); }
</style>
