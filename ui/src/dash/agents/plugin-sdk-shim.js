// hal0 plugin SDK shim (v0.3, PR-7).
//
// Mirrors the upstream Hermes `web/src/plugins/registry.ts` surface
// (`exposePluginSDK` at line 101) so an unmodified upstream plugin
// bundle (kanban today, future plugins for free) can load against the
// hal0 dashboard with zero authoring change.
//
// Both names are exposed so the same bundle works against either host:
//   - window.__HERMES_PLUGIN_SDK__   ← compat alias
//   - window.__HAL0_PLUGIN_SDK__     ← canonical
//   - window.__HERMES_PLUGINS__      ← compat alias  (register/registerSlot)
//   - window.__HAL0_PLUGINS__        ← canonical
//
// The shim also installs a lightweight in-process registry of plugin
// components keyed by name + a slot registry keyed by slot name, both
// observable via the listeners that PluginTabHost subscribes to.
//
// What the shim does NOT do:
//   * Bundle React. The shim re-exports React + ReactDOM from the
//     same globals install/dash chrome uses — `window.React`.
//     Plugins read `SDK.React` instead of importing react, so the
//     dashboard's single React copy stays canonical.
//   * Add upstream Nous DS atoms. v0.3 ships with `components: {}`
//     plus a Proxy that returns a `null` component for unknown keys
//     so a plugin that asks for `SDK.components.Badge` doesn't
//     TypeError — it just renders blank. PR-8 owns the real Nous-shim
//     atoms; v0.3 ships with the safety net.

const _React = window.React;
const _useState = _React?.useState;
const _useEffect = _React?.useEffect;
const _useCallback = _React?.useCallback;
const _useMemo = _React?.useMemo;
const _useRef = _React?.useRef;
const _useContext = _React?.useContext;
const _createContext = _React?.createContext;

// ── registry ───────────────────────────────────────────────────────────

const _registered = new Map();
const _loadErrors = new Map();
const _listeners = new Set();

function _notify() {
  for (const fn of _listeners) {
    try { fn(); } catch { /* ignore */ }
  }
}

function _registerPlugin(name, component) {
  _loadErrors.delete(name);
  _registered.set(name, component);
  _notify();
}

function _getPluginComponent(name) {
  return _registered.get(name);
}

function _getPluginLoadError(name) {
  return _loadErrors.get(name);
}

function _setPluginLoadError(name, message) {
  _loadErrors.set(name, message);
  _notify();
}

function _onPluginRegistered(fn) {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

// ── slot registry (upstream `slots.ts` shape, 5-slot starter map) ──────

const _slotRegistry = new Map();
const _slotListeners = new Set();

function _notifySlots() {
  for (const fn of _slotListeners) {
    try { fn(); } catch { /* ignore */ }
  }
}

function _registerSlot(plugin, slot, component) {
  const entries = _slotRegistry.get(slot) || [];
  entries.push({ plugin, component });
  _slotRegistry.set(slot, entries);
  _notifySlots();
}

function _getSlotEntries(slot) {
  return _slotRegistry.get(slot) || [];
}

function _onSlotsChanged(fn) {
  _slotListeners.add(fn);
  return () => _slotListeners.delete(fn);
}

// ── fetch helper — auto-add X-hal0-Agent ─────────────────────────────

function _resolveAgentId() {
  // Match hal0-api's resolution order: HAL0_AGENT_ID cookie > document.body data attr > default.
  const fromCookie =
    typeof document !== "undefined"
      ? (document.cookie || "")
          .split(";")
          .map(s => s.trim())
          .find(s => s.startsWith("HAL0_AGENT_ID="))
      : null;
  if (fromCookie) return decodeURIComponent(fromCookie.split("=", 2)[1]);
  if (typeof document !== "undefined" && document.body?.dataset?.hal0Agent) {
    return document.body.dataset.hal0Agent;
  }
  return "hermes";
}

async function _hal0Fetch(path, init = {}) {
  const headers = new Headers(init.headers || {});
  headers.set("X-hal0-Agent", _resolveAgentId());
  // Plugins MUST NOT smuggle an Authorization header; the proxy strips
  // it on the server side anyway, but enforce client-side so an
  // upstream bundle that calls `Authorization: Bearer ${secret}` from
  // window can't accidentally leak the value to the dashboard's own
  // network panel either.
  headers.delete("Authorization");
  return fetch(path, { ...init, headers });
}

async function _fetchJSON(path, init = {}) {
  const resp = await _hal0Fetch(path, init);
  if (!resp.ok) {
    let detail = "";
    try { detail = await resp.text(); } catch { /* ignore */ }
    const err = new Error(`fetch ${path} → ${resp.status}: ${detail}`);
    err.status = resp.status;
    throw err;
  }
  const ct = resp.headers.get("content-type") || "";
  if (ct.includes("application/json")) return resp.json();
  return resp.text();
}

// ── utilities ──────────────────────────────────────────────────────────

function _cn(...parts) {
  return parts
    .flat(Infinity)
    .filter(p => typeof p === "string" && p.length)
    .join(" ");
}

function _timeAgo(value) {
  const now = Date.now();
  const then = typeof value === "number" ? value : Date.parse(value);
  if (!Number.isFinite(then)) return "";
  const seconds = Math.floor((now - then) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function _isoTimeAgo(value) {
  // Upstream `isoTimeAgo` returns the iso string + the relative form.
  const then = typeof value === "number" ? new Date(value) : new Date(value);
  return `${then.toISOString()} (${_timeAgo(then.getTime())})`;
}

// ── unknown-key safety Proxy for SDK.components ───────────────────────

const _NullComponent = () => null;

function _makeComponentsProxy(base = {}) {
  return new Proxy(base, {
    get(target, prop) {
      if (prop in target) return target[prop];
      // Surface once-per-key warning so author can fix; render null
      // so plugin keeps loading.
      if (typeof prop === "string") {
        console.warn(
          `[hal0-plugin-sdk] components.${prop} not provided by host; ` +
          "rendering null. PR-8 will add Nous-shim atoms; until then, " +
          "use the SDK.components.HalCard / HalButton primitives or " +
          "BYO inside the plugin's shadow root."
        );
      }
      return _NullComponent;
    },
  });
}

// ── publish ──────────────────────────────────────────────────────────

function exposePluginSDK() {
  if (!_React) {
    console.warn(
      "[hal0-plugin-sdk] window.React not present at install time; " +
      "plugins will see SDK.React === undefined. main.tsx pins the " +
      "globals install order — file a hal0 bug if you hit this."
    );
  }

  const sdk = {
    version: "1.0.0",
    React: _React,
    hooks: {
      useState: _useState,
      useEffect: _useEffect,
      useCallback: _useCallback,
      useMemo: _useMemo,
      useRef: _useRef,
      useContext: _useContext,
      createContext: _createContext,
    },
    api: {
      hal0Fetch: _hal0Fetch,
    },
    fetchJSON: _fetchJSON,
    components: _makeComponentsProxy({
      // v0.3 ships zero components — Proxy keeps unknown keys safe.
      // PR-8 fills in Nous-shim atoms.
    }),
    utils: { cn: _cn, timeAgo: _timeAgo, isoTimeAgo: _isoTimeAgo },
    useI18n: () => (k) => k, // no-op stub, v0.3 ships English-only
    PluginSlot: _PluginSlot,
  };

  const registry = {
    register: _registerPlugin,
    registerSlot: _registerSlot,
    // Read-side helpers used by PluginTabHost — exposed under the
    // same namespace so a tab can hot-pick the registered component.
    getPluginComponent: _getPluginComponent,
    getPluginLoadError: _getPluginLoadError,
    setPluginLoadError: _setPluginLoadError,
    onPluginRegistered: _onPluginRegistered,
    getSlotEntries: _getSlotEntries,
    onSlotsChanged: _onSlotsChanged,
  };

  window.__HAL0_PLUGIN_SDK__ = sdk;
  window.__HERMES_PLUGIN_SDK__ = sdk;
  window.__HAL0_PLUGINS__ = registry;
  window.__HERMES_PLUGINS__ = registry;
}

// ── PluginSlot React component — used by upstream bundles ─────────────

function _PluginSlot({ name }) {
  if (!_React) return null;
  const [, setVersion] = _useState(0);
  _useEffect(() => {
    const unsubscribe = _onSlotsChanged(() => setVersion(v => v + 1));
    return unsubscribe;
  }, []);
  const entries = _getSlotEntries(name);
  if (!entries.length) return null;
  return _React.createElement(
    _React.Fragment,
    null,
    ...entries.map((entry, idx) =>
      _React.createElement(entry.component, { key: `${entry.plugin}:${idx}` })
    )
  );
}

// Install immediately. Browsers evaluate this file as a side-effect
// import from main.tsx; window globals must be in place before the
// dashboard's dash/main.jsx renders.
exposePluginSDK();

Object.assign(window, { PluginSDKShim: { exposePluginSDK } });
