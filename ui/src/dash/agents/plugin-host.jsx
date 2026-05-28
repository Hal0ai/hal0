// hal0 plugin tab host (v0.3, PR-7).
//
// Fetches the upstream Hermes plugin manifest from hal0-api's reverse
// proxy (`/api/dashboard/plugins`), renders one inner tab per plugin
// that declares an SRI `integrity` value, and mounts each plugin's JS
// bundle inside a shadow DOM root so the upstream's CSS (Tailwind v4,
// scoped tokens) stays contained.
//
// Three contracts to read first:
//   - Master plan §2 locks shadow DOM per `<PluginTabHost>`.
//   - DA-sec-ops MUST-FIX #4: refuse to mount any plugin missing SRI.
//   - DA-sec-ops MUST-FIX #2: never smuggle Authorization in the
//     dynamically-injected <script>. The proxy strips it server-side,
//     but the host never sets it client-side either; `integrity` is
//     declarative and the browser computes the digest itself.
//
// Shadow DOM token bridge:
//   On mount we copy every `--hal0-*` and known Tailwind design-token
//   custom property from the document root onto the shadow root's host
//   so the plugin's CSS (`var(--hal0-accent)`, etc.) resolves correctly.
//   A MutationObserver re-syncs on theme switches.
//
// Future-compat:
//   `agentId` prop reserved for v0.4 multi-agent; today defaults to
//   "hermes" — matches the X-hal0-Agent the proxy injects.

const { useState, useEffect, useRef, useCallback } = React;

// ── token bridge ──────────────────────────────────────────────────────

// Prefixes / explicit names of CSS custom properties that should be
// mirrored from the document root onto every plugin shadow root so
// `var(--hal0-accent)` resolves the same inside and outside.
const _TOKEN_PREFIXES = ["--hal0-", "--theme-", "--color-"];
const _EXPLICIT_TOKENS = [
  // hal0-specific spacing / typography tokens used across the
  // dashboard prototype (see ui/src/dashboard.css). Listed explicitly
  // because they don't share a single prefix.
  "--bg",
  "--bg-2",
  "--fg",
  "--fg-2",
  "--fg-3",
  "--fg-4",
  "--fg-5",
  "--line",
  "--line-soft",
  "--accent",
  "--accent-soft",
  "--accent-line",
  "--jbm",
  "--mono",
  "--rad",
];

function _collectTokens(root) {
  const style = getComputedStyle(root);
  const out = {};
  for (const name of _EXPLICIT_TOKENS) {
    const value = style.getPropertyValue(name);
    if (value) out[name] = value;
  }
  // Iterating CSSStyleDeclaration is opaque for custom properties; we
  // fall back to walking the root's inline + cascaded vars via
  // `style.cssText`. When that's empty, the explicit-name list above
  // covers the dashboard's published tokens.
  if (typeof root.style?.cssText === "string") {
    for (const decl of root.style.cssText.split(";")) {
      const [rawName, ...rest] = decl.split(":");
      const name = (rawName || "").trim();
      if (!name.startsWith("--")) continue;
      if (!_TOKEN_PREFIXES.some(p => name.startsWith(p))) continue;
      out[name] = rest.join(":").trim();
    }
  }
  return out;
}

function _applyTokens(shadowRoot, tokens) {
  // Apply by emitting a single :host {} rule. Avoids touching the
  // host element's inline style (which the dashboard owns).
  let styleEl = shadowRoot.querySelector("style[data-hal0-token-bridge]");
  if (!styleEl) {
    styleEl = document.createElement("style");
    styleEl.setAttribute("data-hal0-token-bridge", "1");
    shadowRoot.prepend(styleEl);
  }
  const declarations = Object.entries(tokens)
    .map(([k, v]) => `${k}: ${v};`)
    .join("\n  ");
  styleEl.textContent = `:host {\n  ${declarations}\n}`;
}

// ── ErrorBoundary ─────────────────────────────────────────────────────

class _PluginErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error(
      `[hal0-plugin-host] plugin "${this.props.pluginName}" crashed`,
      error,
      info,
    );
  }
  render() {
    if (this.state.error) {
      return (
        <div
          className="card"
          style={{
            padding: 18,
            borderColor: "var(--err, #b00020)",
            background: "var(--bg-2)",
          }}
        >
          <div
            className="mono"
            style={{ fontSize: 13, color: "var(--fg)", marginBottom: 6 }}
          >
            Plugin "{this.props.pluginName}" crashed.
          </div>
          <div
            className="mono"
            style={{ fontSize: 11, color: "var(--fg-3)", marginBottom: 8 }}
          >
            {String(this.state.error?.message || this.state.error)}
          </div>
          <button
            className="btn ghost sm"
            onClick={() => this.setState({ error: null })}
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

// ── one plugin mount: shadow DOM + injected script ────────────────────

function _PluginMount({ manifestEntry, agentId }) {
  const hostRef = useRef(null);
  const shadowRef = useRef(null);
  const mountRef = useRef(null);
  const reactRootRef = useRef(null);
  const scriptRef = useRef(null);
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [errorMessage, setErrorMessage] = useState(null);

  const pluginName = manifestEntry.name;
  const integrity = manifestEntry.integrity;
  const entry = manifestEntry.entry || "dist/index.js";
  const css = manifestEntry.css || null;

  // Set up shadow root + token bridge once per mount.
  useEffect(() => {
    if (!hostRef.current) return;
    if (shadowRef.current) return; // already wired
    const shadow = hostRef.current.attachShadow({ mode: "open" });
    shadowRef.current = shadow;
    // Re-emit dashboard tokens onto the shadow root.
    _applyTokens(shadow, _collectTokens(document.documentElement));
    const observer = new MutationObserver(() => {
      _applyTokens(shadow, _collectTokens(document.documentElement));
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["style", "class", "data-theme"],
    });
    // Create the plugin mount node inside the shadow root.
    const mount = document.createElement("div");
    mount.setAttribute("data-hal0-plugin-mount", pluginName);
    shadow.appendChild(mount);
    mountRef.current = mount;
    return () => observer.disconnect();
  }, [pluginName]);

  // SRI gate — we refuse to mount any plugin missing integrity.
  // DA-sec-ops MUST-FIX #4 locks this.
  useEffect(() => {
    if (!integrity) {
      setStatus("error");
      setErrorMessage(
        "plugin manifest is missing required 'integrity' (SRI) field; refusing to mount.",
      );
      return;
    }
    if (!shadowRef.current || scriptRef.current) return;

    let cancelled = false;

    // 1) Optional CSS — inject as a <link> inside the shadow root.
    if (css) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = `/dashboard-plugins/${encodeURIComponent(pluginName)}/${css}`;
      shadowRef.current.appendChild(link);
    }

    // 2) The bundle. Browsers verify SRI for <script> when the integrity
    //    attribute is set + the request is cross-origin OR same-origin
    //    with `crossorigin="anonymous"`. We set both so the browser
    //    performs the digest check itself (defence in depth on top of
    //    the proxy-side SRI gate).
    const script = document.createElement("script");
    script.src = `/dashboard-plugins/${encodeURIComponent(pluginName)}/${entry}`;
    script.async = true;
    script.crossOrigin = "anonymous";
    script.integrity = integrity;
    script.onload = () => {
      if (cancelled) return;
      // Wait one tick — the plugin's IIFE calls
      // `window.__HAL0_PLUGINS__.register(name, Comp)` on load. By the
      // time onload fires, register has already been called.
      const sdk = window.__HAL0_PLUGINS__;
      const Component = sdk?.getPluginComponent?.(pluginName);
      if (!Component) {
        setStatus("error");
        setErrorMessage(
          `plugin bundle loaded but did not call __HAL0_PLUGINS__.register("${pluginName}", Component)`,
        );
        return;
      }
      // Render inside the shadow root mount node. We use a fresh
      // ReactDOM root so the plugin owns its tree.
      const ReactDOM = window.ReactDOM;
      if (!ReactDOM?.createRoot) {
        setStatus("error");
        setErrorMessage("window.ReactDOM not present; cannot mount plugin tree");
        return;
      }
      const root = ReactDOM.createRoot(mountRef.current);
      reactRootRef.current = root;
      root.render(<Component agentId={agentId} />);
      setStatus("ready");
    };
    script.onerror = () => {
      if (cancelled) return;
      setStatus("error");
      setErrorMessage(
        `failed to load plugin bundle (SRI mismatch or network error). ` +
        `Check the proxy log + manifest integrity for "${pluginName}".`,
      );
    };
    document.head.appendChild(script);
    scriptRef.current = script;

    return () => {
      cancelled = true;
      try { reactRootRef.current?.unmount(); } catch { /* ignore */ }
      reactRootRef.current = null;
      if (scriptRef.current) {
        scriptRef.current.remove();
        scriptRef.current = null;
      }
    };
  }, [pluginName, entry, css, integrity, agentId]);

  return (
    <div>
      {status === "error" && (
        <div
          className="card"
          style={{
            padding: 14,
            borderColor: "var(--err, #b00020)",
            background: "var(--bg-2)",
            marginBottom: 10,
          }}
        >
          <div
            className="mono"
            style={{ fontSize: 12, color: "var(--fg)", marginBottom: 4 }}
          >
            Plugin "{pluginName}" failed to mount.
          </div>
          <div className="mono" style={{ fontSize: 11, color: "var(--fg-3)" }}>
            {errorMessage}
          </div>
        </div>
      )}
      {status === "loading" && (
        <div
          className="mono"
          style={{ fontSize: 11, color: "var(--fg-3)", padding: 10 }}
        >
          Loading plugin "{pluginName}"…
        </div>
      )}
      <div
        ref={hostRef}
        data-hal0-plugin-host={pluginName}
        style={{ width: "100%" }}
      />
    </div>
  );
}

// ── manifest fetch + tab nav ──────────────────────────────────────────

function PluginTabHost({ agentId = "hermes" }) {
  const [manifest, setManifest] = useState(null); // null = loading
  const [activeTab, setActiveTab] = useState(null);
  const [fetchError, setFetchError] = useState(null);

  const refetch = useCallback(async () => {
    try {
      const resp = await fetch("/api/dashboard/plugins", {
        headers: { "X-hal0-Agent": agentId },
        cache: "no-store",
      });
      if (!resp.ok) {
        setManifest([]);
        setFetchError(`/api/dashboard/plugins → ${resp.status}`);
        return;
      }
      const body = await resp.json();
      const list = Array.isArray(body) ? body : [];
      setManifest(list);
      setFetchError(null);
      if (!activeTab && list.length > 0) {
        setActiveTab(list[0].name);
      }
    } catch (err) {
      setManifest([]);
      setFetchError(String(err?.message || err));
    }
  }, [agentId, activeTab]);

  useEffect(() => {
    refetch();
  }, [refetch]);

  if (manifest === null) {
    return (
      <div className="mono" style={{ fontSize: 12, color: "var(--fg-3)", padding: 18 }}>
        Loading plugins…
      </div>
    );
  }

  if (manifest.length === 0) {
    return (
      <div
        className="card"
        style={{
          padding: 24,
          textAlign: "center",
          borderStyle: "dashed",
          background: "var(--bg-2)",
        }}
      >
        <div className="mono" style={{ fontSize: 13, color: "var(--fg-3)", marginBottom: 6 }}>
          No plugins available.
        </div>
        <div className="mono" style={{ fontSize: 11, color: "var(--fg-5)" }}>
          {fetchError
            ? `Hermes offline — plugins unavailable (${fetchError}).`
            : "The upstream Hermes runtime exposes plugins via the dashboard manifest. Install one to see it here."}
        </div>
      </div>
    );
  }

  // Plugins missing required `integrity` are listed but rendered as a
  // warning card instead of mounted.
  const tabs = manifest.map(m => ({
    id: m.name,
    label: m.label || m.name,
    hasIntegrity: !!m.integrity,
  }));

  const activeEntry = manifest.find(m => m.name === activeTab) || manifest[0];

  return (
    <div>
      <div
        style={{
          display: "flex",
          gap: 0,
          borderBottom: "1px solid var(--line)",
          marginBottom: 14,
        }}
      >
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            style={{
              padding: "8px 14px",
              background: "transparent",
              border: "none",
              borderBottom:
                (activeTab || tabs[0].id) === t.id
                  ? "2px solid var(--accent)"
                  : "2px solid transparent",
              color:
                (activeTab || tabs[0].id) === t.id
                  ? "var(--accent)"
                  : "var(--fg-3)",
              fontFamily: "var(--jbm)",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            {t.label}
            {!t.hasIntegrity && (
              <span
                className="chip warn"
                style={{ marginLeft: 6, fontSize: 9 }}
                title="missing manifest integrity — host refuses to mount"
              >
                no SRI
              </span>
            )}
          </button>
        ))}
      </div>

      <_PluginErrorBoundary pluginName={activeEntry.name}>
        <_PluginMount manifestEntry={activeEntry} agentId={agentId} />
      </_PluginErrorBoundary>
    </div>
  );
}

Object.assign(window, { PluginTabHost });
