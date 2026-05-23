<script setup>
/**
 * primitives/_sandbox/PrimitivesSandbox.vue — test-only host.
 *
 * Renders one toggle per primitive so the Playwright spec can mount
 * each in isolation and exercise behaviours (Esc, backdrop, focus
 * trap, drawer transform, banner stack scope filtering, menu
 * outside-click close, toast queue auto-removal).
 *
 * NOT linked from any chrome — only reachable via the explicit
 * `/_primitives_test` route. Lives next to the primitives it tests.
 */
import { ref } from 'vue'
import Modal from '../Modal.vue'
import Drawer from '../Drawer.vue'
import ConfirmDialog from '../ConfirmDialog.vue'
import Banner from '../Banner.vue'
import BannerStack from '../BannerStack.vue'
import Menu from '../Menu.vue'
import ToastStack from '../ToastStack.vue'
import { useBannerStore } from '../../../stores/banner.js'
import { useToastStore } from '../../../stores/toast.js'

const banners = useBannerStore()
const toasts  = useToastStore()

// ── Modal ──────────────────────────────────────────────────────────
const modalOpen = ref(false)
function openModal()  { modalOpen.value = true }
function closeModal() { modalOpen.value = false }

// ── Drawer ─────────────────────────────────────────────────────────
const drawerOpen = ref(false)
function openDrawer()  { drawerOpen.value = true }
function closeDrawer() { drawerOpen.value = false }

// ── ConfirmDialog ──────────────────────────────────────────────────
const cdRecoverableOpen = ref(false)
const cdDestructiveOpen = ref(false)
const cdTypeOpen        = ref(false)
const cdLog             = ref([])
function logCd(s) { cdLog.value.push(s) }

// ── Banner / BannerStack ───────────────────────────────────────────
const bannerScope = ref('slots')
function toggleBanner(id) { banners.toggle(id) }
function showAllSlots() {
  for (const b of banners.CATALOG) if (b.scope === 'slots') banners.show(b.id)
}
function clearAll() {
  for (const id of Object.keys(banners.active)) banners.dismiss(id)
}

// ── Menu ───────────────────────────────────────────────────────────
const menuOpen   = ref(false)
const menuAnchor = ref(null)
const menuActionLog = ref([])
const menuItems = [
  { label: 'Wired action', kbd: '⌘E', onClick: () => menuActionLog.value.push('wired-fired') },
  { divider: true },
  { label: 'Stubbed action' }, // no onClick → triggers toast
  { label: 'Destructive', danger: true, onClick: () => menuActionLog.value.push('destructive-fired') },
]
function openMenu() {
  menuOpen.value = true
}
function closeMenu() { menuOpen.value = false }

// ── Toast ──────────────────────────────────────────────────────────
function pushToast(kind = 'info', ttl = 4000) {
  toasts.push(`Sandbox ${kind} #${toasts.queue.length + 1}`, kind, ttl)
}
function pushShortToast() { toasts.push('quick', 'info', 200) }
</script>

<template>
  <div class="sandbox">
    <h1>Primitives sandbox</h1>
    <p class="sub">Test-only host for slice #167. Not linked from chrome.</p>

    <!-- ─── Modal ─── -->
    <section data-section="modal">
      <h2>Modal</h2>
      <button type="button" class="btn sm" data-testid="open-modal" @click="openModal">Open modal</button>
      <Modal :open="modalOpen" :on-close="closeModal" title="Sandbox modal" eyebrow="Test · modal">
        <p data-testid="modal-body">Modal body content.</p>
        <input type="text" placeholder="focus-1" data-testid="modal-input-1" />
        <button type="button" data-testid="modal-btn-inner">Inner button</button>
      </Modal>
    </section>

    <!-- ─── Drawer ─── -->
    <section data-section="drawer">
      <h2>Drawer</h2>
      <button type="button" class="btn sm" data-testid="open-drawer" @click="openDrawer">Open drawer</button>
      <Drawer :open="drawerOpen" :on-close="closeDrawer" title="Sandbox drawer" eyebrow="Test · drawer">
        <p data-testid="drawer-body">Drawer body content.</p>
        <button type="button" data-testid="drawer-btn-inner">Inner button</button>
      </Drawer>
    </section>

    <!-- ─── ConfirmDialog ─── -->
    <section data-section="confirm">
      <h2>ConfirmDialog</h2>
      <button type="button" class="btn sm" data-testid="open-cd-recoverable" @click="cdRecoverableOpen = true">Open recoverable</button>
      <button type="button" class="btn sm" data-testid="open-cd-destructive" @click="cdDestructiveOpen = true">Open destructive</button>
      <button type="button" class="btn sm" data-testid="open-cd-type" @click="cdTypeOpen = true">Open type-to-confirm</button>
      <pre data-testid="cd-log">{{ cdLog.join('\n') }}</pre>

      <ConfirmDialog
        :open="cdRecoverableOpen"
        title="Recoverable action"
        message="You can undo this."
        :on-cancel="() => { logCd('rec-cancel'); cdRecoverableOpen = false }"
        :on-confirm="() => { logCd('rec-confirm'); cdRecoverableOpen = false }"
      />
      <ConfirmDialog
        :open="cdDestructiveOpen"
        title="Destructive action"
        message="This action is permanent."
        destructive
        :on-cancel="() => { logCd('dest-cancel'); cdDestructiveOpen = false }"
        :on-confirm="() => { logCd('dest-confirm'); cdDestructiveOpen = false }"
      />
      <ConfirmDialog
        :open="cdTypeOpen"
        title="Type to confirm"
        message="Type the keyword to enable confirm."
        destructive
        type-to-confirm="DELETE"
        :on-cancel="() => { logCd('type-cancel'); cdTypeOpen = false }"
        :on-confirm="() => { logCd('type-confirm'); cdTypeOpen = false }"
      />
    </section>

    <!-- ─── Banner / BannerStack ─── -->
    <section data-section="banner">
      <h2>Banner / BannerStack</h2>
      <div class="row">
        <button type="button" class="btn sm" data-testid="ban-toggle-nuclear" @click="toggleBanner('nuclear-evict')">Toggle nuclear-evict</button>
        <button type="button" class="btn sm" data-testid="ban-toggle-lemond" @click="toggleBanner('lemond-offline')">Toggle lemond-offline</button>
        <button type="button" class="btn sm" data-testid="ban-toggle-skip" @click="toggleBanner('skip-path')">Toggle skip-path</button>
        <button type="button" class="btn sm" data-testid="ban-show-slots" @click="showAllSlots">Show all slots</button>
        <button type="button" class="btn sm" data-testid="ban-clear" @click="clearAll">Clear</button>
      </div>
      <p>Scope: <code>{{ bannerScope }}</code></p>
      <BannerStack :scope="bannerScope" />

      <h3>Standalone Banner</h3>
      <Banner
        kind="warn"
        eyebrow="Standalone"
        heading="Sandbox standalone banner"
        body="Renders without the stack/store, for visual diff."
        :actions="[{ label: 'Primary', primary: true }, { label: 'Secondary' }]"
        :on-dismiss="() => {}"
        data-testid="standalone-banner"
      />
    </section>

    <!-- ─── Menu ─── -->
    <section data-section="menu">
      <h2>Menu</h2>
      <button
        ref="menuAnchor"
        type="button"
        class="btn sm"
        data-testid="open-menu"
        @click="openMenu"
      >Open menu ▾</button>
      <Menu :open="menuOpen" :anchor="menuAnchor" :items="menuItems" :on-close="closeMenu" />
      <pre data-testid="menu-log">{{ menuActionLog.join('\n') }}</pre>
    </section>

    <!-- ─── Toast / ToastStack ─── -->
    <section data-section="toast">
      <h2>Toast / ToastStack</h2>
      <button type="button" class="btn sm" data-testid="push-toast" @click="pushToast('info')">Push toast</button>
      <button type="button" class="btn sm" data-testid="push-short-toast" @click="pushShortToast">Push short toast (200ms)</button>
      <button type="button" class="btn sm" data-testid="push-toast-error" @click="pushToast('error')">Push error toast</button>
      <span data-testid="toast-count">queue: {{ toasts.queue.length }}</span>
      <ToastStack />
    </section>
  </div>
</template>

<style scoped>
.sandbox {
  padding: 24px;
  font-family: var(--font-sans, system-ui);
  color: var(--fg);
  background: var(--bg);
  min-height: 100vh;
}
.sandbox h1 { font-size: 22px; margin: 0 0 4px; }
.sandbox h2 { font-size: 14px; margin: 32px 0 8px; color: var(--fg-2); }
.sandbox h3 { font-size: 12px; margin: 16px 0 6px; color: var(--fg-3); }
.sub { color: var(--fg-4); font-size: 12px; margin: 0 0 16px; }
.row { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
section { padding: 12px 0; border-bottom: 1px solid var(--line-soft); }
section button { margin-right: 6px; margin-bottom: 6px; }
pre {
  font-family: var(--jbm);
  font-size: 11px;
  color: var(--fg-3);
  background: var(--bg-1);
  border: 1px solid var(--line-soft);
  border-radius: var(--rad-sm);
  padding: 6px 8px;
  margin: 8px 0 0;
  min-height: 18px;
}
</style>
