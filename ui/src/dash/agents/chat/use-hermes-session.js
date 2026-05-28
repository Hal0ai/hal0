// hal0 v0.3 PR-10 — HermesChat session store + WS connection manager.
//
// Two WebSocket channels per PR-9's chat_proxy:
//   /api/agents/hermes/events  — server→browser JSON-RPC event mirror
//   /api/agents/hermes/submit  — bidi JSON-RPC client→hermes
//
// First-connect handshake (PR-9 contract):
//   GET /api/agents/{id}/session/handshake  → mints the hal0_session
//   HMAC cookie. We hit this before opening either WS; failure short-
//   circuits the open so we don't get a quiet 4403 close on the WS.
//
// State management (master plan §2):
//   This file lives in the window-globals .js layer so it can't `import`
//   zustand directly. We hand-roll a tiny external store + expose a hook
//   via React.useSyncExternalStore. The store carries the runtime UI
//   slice (transcript, sessionId, ws status, approvals queue). Persona /
//   MCP / model badge live behind TanStack Query hooks bridged onto
//   window by the .ts shim files (matches PR-6/PR-8 pattern).
//
// Event routing covers every R1 taxonomy entry (MASTER-PLAN.md §4 PR-10
// table + notes/r1-upstream-core.md table). See _routeEvent below.
//
// Reconnect strategy (browser-side, PR-9 says proxy is stateless):
//   jittered backoff 250ms → 4s; keep trying. On reconnect, if we have a
//   sessionId we call session.resume; otherwise session.create w/
//   first_run=true so the persona welcome (PR-3 prompt addendum) fires.
//
// Window-globals shim wiring at file bottom.

;(function () {
  const React = window.React;

  // ── External store (minimal, subscribable) ───────────────────────
  function _initialState() {
    return {
      // Connection state
      connectionState: "idle", // idle | connecting | open | reconnecting | closed
      consecutiveFailures: 0,
      sessionId: null,
      firstRun: true,

      // Sidecar/identity
      model: null,
      provider: null,
      caps: null,

      // Transcript: ordered list of items. Item kinds:
      //   user       { kind:'user',       id, text, ts }
      //   assistant  { kind:'assistant',  id, text, status, streaming, usage }
      //   thinking   { kind:'thinking',   id, parentId, text }
      //   reasoning  { kind:'reasoning',  id, parentId, text }
      //   tool       { kind:'tool',       id, parentId, tool_id, name, context,
      //                                   preview, summary, error, status,
      //                                   startedAt, completedAt }
      //   approval   { kind:'approval',   id, requestId, payload,
      //                                   kind2:'approval'|'clarify'|'sudo'|'secret',
      //                                   resolved }
      //   status     { kind:'status',     id, level, text, ephemeral? }
      //   error      { kind:'error',      id, text }
      transcript: [],

      activeAssistantId: null,
      pendingApprovals: 0,
      lastStatus: null,
      lastSubmitAt: 0,
    };
  }

  let _state = _initialState();
  const _listeners = new Set();

  function getState() { return _state; }
  function setState(partial) {
    const next = typeof partial === "function" ? partial(_state) : partial;
    if (!next) return;
    _state = Object.assign({}, _state, next);
    _listeners.forEach((l) => {
      try { l(); } catch (_e) {}
    });
  }
  function subscribe(listener) {
    _listeners.add(listener);
    return () => _listeners.delete(listener);
  }

  function _reset() { _state = _initialState(); _listeners.forEach((l) => l()); }

  function _now() { return Date.now(); }
  function _nid() { return `${_now()}-${Math.random().toString(36).slice(2, 8)}`; }

  // ── Mutators ─────────────────────────────────────────────────────
  const actions = {
    setConnectionState(s) { setState({ connectionState: s }); },
    setSession(info) {
      const p = info || {};
      setState((st) => ({
        sessionId: p.session_id || p.sessionId || st.sessionId,
        model: p.model || st.model,
        provider: p.provider || st.provider,
        caps: p.caps || p.toolsets || st.caps,
        firstRun: p.first_run === true,
      }));
    },

    appendUser(text) {
      const id = _nid();
      setState((st) => ({
        transcript: st.transcript.concat({
          kind: "user", id, text: String(text || ""), ts: _now(),
        }),
        lastSubmitAt: _now(),
      }));
      return id;
    },

    startAssistant() {
      const id = _nid();
      setState((st) => ({
        transcript: st.transcript.concat({
          kind: "assistant", id, text: "",
          status: "streaming", streaming: true, usage: null,
          startedAt: _now(),
        }),
        activeAssistantId: id,
      }));
      return id;
    },

    appendAssistantDelta(delta) {
      const text = typeof delta === "string" ? delta : (delta && delta.text) || "";
      if (!text) return;
      setState((st) => {
        const aid = st.activeAssistantId;
        if (!aid) {
          // Stream arrived before message.start — open a fresh bubble.
          const id = _nid();
          return {
            transcript: st.transcript.concat({
              kind: "assistant", id, text,
              status: "streaming", streaming: true, usage: null,
              startedAt: _now(),
            }),
            activeAssistantId: id,
          };
        }
        const next = st.transcript.slice();
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].kind === "assistant" && next[i].id === aid) {
            next[i] = Object.assign({}, next[i], { text: next[i].text + text });
            break;
          }
        }
        return { transcript: next };
      });
    },

    completeAssistant(payload) {
      const data = payload || {};
      setState((st) => {
        const aid = st.activeAssistantId;
        if (!aid) return null;
        const next = st.transcript.slice();
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].kind === "assistant" && next[i].id === aid) {
            next[i] = Object.assign({}, next[i], {
              status: data.status || "complete",
              streaming: false,
              usage: data.usage || null,
              warning: data.warning || null,
              completedAt: _now(),
              text: data.text != null ? data.text : next[i].text,
            });
            break;
          }
        }
        return { transcript: next, activeAssistantId: null };
      });
    },

    appendThinking(delta, kind) {
      const text = typeof delta === "string" ? delta : (delta && delta.text) || "";
      if (!text) return;
      const flavor = kind === "reasoning" ? "reasoning" : "thinking";
      setState((st) => {
        const aid = st.activeAssistantId;
        const next = st.transcript.slice();
        for (let i = next.length - 1; i >= 0; i--) {
          const row = next[i];
          if (row.kind === flavor && row.parentId === aid) {
            next[i] = Object.assign({}, row, { text: row.text + text });
            return { transcript: next };
          }
          if (row.kind === "assistant" || row.kind === "user") break;
        }
        next.push({ kind: flavor, id: _nid(), parentId: aid, text, ts: _now() });
        return { transcript: next };
      });
    },

    toolStart(payload) {
      const data = payload || {};
      const id = _nid();
      const toolId = String(data.tool_id || data.name || id);
      setState((st) => {
        const row = {
          kind: "tool", id, tool_id: toolId, parentId: st.activeAssistantId,
          name: data.name || "tool",
          context: data.context || data.args_text || null,
          preview: null, summary: null, error: null,
          status: "running",
          startedAt: _now(),
        };
        return { transcript: st.transcript.concat(row) };
      });
      return id;
    },

    toolProgress(payload) {
      const data = payload || {};
      const toolId = String(data.tool_id || data.name || "");
      if (!toolId) return;
      setState((st) => {
        const next = st.transcript.slice();
        for (let i = next.length - 1; i >= 0; i--) {
          const r = next[i];
          if (r.kind === "tool" && r.tool_id === toolId && r.status === "running") {
            next[i] = Object.assign({}, r, {
              preview: data.preview != null ? data.preview : r.preview,
            });
            return { transcript: next };
          }
        }
        return null;
      });
    },

    toolComplete(payload) {
      const data = payload || {};
      const toolId = String(data.tool_id || data.name || "");
      if (!toolId) return;
      setState((st) => {
        const next = st.transcript.slice();
        for (let i = next.length - 1; i >= 0; i--) {
          const r = next[i];
          if (r.kind === "tool" && r.tool_id === toolId && r.status === "running") {
            const isError = !!data.error || data.status === "error";
            next[i] = Object.assign({}, r, {
              status: isError ? "error" : "done",
              summary: data.summary || data.result_text || null,
              inline_diff: data.inline_diff || null,
              error: data.error || null,
              duration_s: data.duration_s != null ? data.duration_s : null,
              completedAt: _now(),
            });
            return { transcript: next };
          }
        }
        return null;
      });
    },

    toolGenerating(payload) {
      const data = payload || {};
      setState((st) => ({
        transcript: st.transcript.concat({
          kind: "status", id: _nid(),
          level: "info",
          text: `(generating tool call: ${data.name || "?"})`,
          ephemeral: true,
        }),
      }));
    },

    approvalRequest(kind, payload) {
      const data = payload || {};
      const requestId = String(data.request_id || data.requestId || _nid());
      const id = _nid();
      setState((st) => ({
        transcript: st.transcript.concat({
          kind: "approval", id, requestId, kind2: kind, payload: data,
          resolved: false, ts: _now(),
        }),
        pendingApprovals: st.pendingApprovals + 1,
      }));
      return { id, requestId };
    },

    resolveApproval(requestId) {
      setState((st) => {
        const next = st.transcript.slice();
        for (let i = next.length - 1; i >= 0; i--) {
          const r = next[i];
          if (r.kind === "approval" && r.requestId === requestId && !r.resolved) {
            next[i] = Object.assign({}, r, { resolved: true });
            break;
          }
        }
        return {
          transcript: next,
          pendingApprovals: Math.max(0, st.pendingApprovals - 1),
        };
      });
    },

    pushStatus(payload) {
      const data = payload || {};
      setState({
        lastStatus: { level: data.kind || "status", text: data.text || "", ts: _now() },
      });
    },

    pushError(payload) {
      const data = payload || {};
      const text = data.message || String(data.error || data) || "Unknown error";
      setState((st) => ({
        transcript: st.transcript.concat({
          kind: "error", id: _nid(), text, ts: _now(),
        }),
      }));
    },

    bumpFailure() {
      setState((st) => ({
        consecutiveFailures: st.consecutiveFailures + 1,
        connectionState: "reconnecting",
      }));
    },
    resetFailure() {
      setState({ consecutiveFailures: 0, connectionState: "open" });
    },
  };

  // ── React hook (useSyncExternalStore) ────────────────────────────
  // We can't pass an object-returning selector straight to
  // useSyncExternalStore: object identity changes every snapshot and
  // useSyncExternalStore re-renders on identity mismatch. We cache the
  // last computed slice and only return a NEW reference when a shallow
  // equality check fails. This mirrors zustand's shallow selector.
  function _shallowEq(a, b) {
    if (Object.is(a, b)) return true;
    if (typeof a !== "object" || a === null || typeof b !== "object" || b === null) return false;
    const ak = Object.keys(a);
    const bk = Object.keys(b);
    if (ak.length !== bk.length) return false;
    for (let i = 0; i < ak.length; i++) {
      const k = ak[i];
      if (!Object.is(a[k], b[k])) return false;
    }
    return true;
  }

  function useHermesSession(selector) {
    const cache = React.useRef({ snap: undefined, has: false });
    const getSnapshot = React.useCallback(() => {
      const next = selector ? selector(_state) : _state;
      if (cache.current.has && _shallowEq(cache.current.snap, next)) {
        return cache.current.snap;
      }
      cache.current.snap = next;
      cache.current.has = true;
      return next;
    }, [selector]);
    return React.useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  }
  // Match the zustand-shaped API: useStore.getState() / setState() exposed
  // on the hook function itself.
  useHermesSession.getState = getState;
  useHermesSession.setState = setState;
  useHermesSession.subscribe = subscribe;

  // ── Event router ─────────────────────────────────────────────────
  const _warnedEvents = new Set();

  function _routeEvent(envelope) {
    if (!envelope || envelope.method !== "event") return;
    const params = envelope.params || {};
    const type = params.type;
    const payload = params.payload || {};
    const sessionId = params.session_id;
    if (!type) return;

    switch (type) {
      case "gateway.ready":
        actions.setConnectionState("open");
        break;
      case "session.info":
        actions.setSession(Object.assign({}, payload, {
          session_id: sessionId || payload.session_id,
        }));
        break;
      case "message.start":
        actions.startAssistant();
        break;
      case "message.delta":
        actions.appendAssistantDelta(payload);
        break;
      case "message.complete":
        actions.completeAssistant(payload);
        break;
      case "thinking.delta":
        actions.appendThinking(payload, "thinking");
        break;
      case "reasoning.delta":
      case "reasoning.available":
        actions.appendThinking(payload, "reasoning");
        break;
      case "tool.start":
        actions.toolStart(payload);
        break;
      case "tool.progress":
        actions.toolProgress(payload);
        break;
      case "tool.complete":
        actions.toolComplete(payload);
        break;
      case "tool.generating":
        actions.toolGenerating(payload);
        break;
      case "approval.request":
        actions.approvalRequest("approval", payload);
        _fireApprovalToast(payload, "approval");
        break;
      case "clarify.request":
        actions.approvalRequest("clarify", payload);
        _fireApprovalToast(payload, "clarify");
        break;
      case "sudo.request":
        actions.approvalRequest("sudo", payload);
        _fireApprovalToast(payload, "sudo");
        break;
      case "secret.request":
        actions.approvalRequest("secret", payload);
        _fireApprovalToast(payload, "secret");
        break;
      case "status.update":
        actions.pushStatus(payload);
        break;
      case "background_review":
        actions.pushStatus(Object.assign({ kind: "info" }, payload));
        break;
      case "error":
        actions.pushError(payload);
        if (window.__hal0Toast) {
          window.__hal0Toast(payload.message || "Hermes error", "warn");
        }
        break;
      case "skin.changed":
      case "voice.transcript":
      case "voice.status":
      case "voice.silent_limit":
      case "browser.progress":
        // Master plan §4 PR-10 ignored
        break;
      default:
        if (!_warnedEvents.has(type)) {
          _warnedEvents.add(type);
          // eslint-disable-next-line no-console
          console.warn("hal0.hermes_chat.unknown_event", type);
        }
        break;
    }
  }

  function _fireApprovalToast(payload, kind) {
    if (typeof window === "undefined" || !window.__hal0Toast) return;
    const label =
      kind === "clarify" ? "Hermes needs clarification" :
      kind === "sudo"    ? "Hermes needs sudo"           :
      kind === "secret"  ? "Hermes needs a secret"       :
                           "Approval requested";
    window.__hal0Toast(label, "warn");
    try {
      window.dispatchEvent(new CustomEvent("hal0:hermes:approval", {
        detail: { kind, requestId: payload.request_id || payload.requestId },
      }));
    } catch (_e) {}
  }

  // ── WS connection manager ────────────────────────────────────────
  let _eventsWs = null;
  let _submitWs = null;
  let _reconnectTimer = null;
  let _stopped = false;
  let _handshakeDone = false;
  let _agentIdActive = null;

  // Jittered exponential backoff 250ms → 4s capped (PR-9 contract).
  const RECONNECT_BASE_MS = 250;
  const RECONNECT_CAP_MS  = 4000;
  function _nextDelay(failures) {
    const exp = Math.min(RECONNECT_CAP_MS, RECONNECT_BASE_MS * 2 ** Math.min(failures, 5));
    return Math.floor(exp * (1 + Math.random() * 0.5));
  }

  function _wsScheme() {
    return location.protocol === "https:" ? "wss:" : "ws:";
  }

  async function _handshake(agentId) {
    if (_handshakeDone) return true;
    try {
      const r = await fetch(`/api/agents/${encodeURIComponent(agentId)}/session/handshake`, {
        credentials: "include",
      });
      if (!r.ok) return false;
      _handshakeDone = true;
      return true;
    } catch (_e) {
      return false;
    }
  }

  function _openEvents(agentId) {
    if (_eventsWs && _eventsWs.readyState <= 1) return;
    const url = `${_wsScheme()}//${location.host}/api/agents/${encodeURIComponent(agentId)}/events`;
    const ws = new WebSocket(url);
    _eventsWs = ws;
    ws.onopen = () => {
      actions.resetFailure();
    };
    ws.onmessage = (m) => {
      const raw = typeof m.data === "string" ? m.data : "";
      if (!raw) return;
      try {
        _routeEvent(JSON.parse(raw));
      } catch (_e) { /* bad frame — ignored */ }
    };
    ws.onclose = () => {
      if (_stopped) return;
      actions.bumpFailure();
      _scheduleReconnect(agentId);
    };
    ws.onerror = () => { /* onclose follows */ };
  }

  function _openSubmit(agentId) {
    if (_submitWs && _submitWs.readyState <= 1) return;
    const url = `${_wsScheme()}//${location.host}/api/agents/${encodeURIComponent(agentId)}/submit`;
    const ws = new WebSocket(url);
    _submitWs = ws;
    ws.onopen = () => {
      // First-run hook (master plan §1 #9): on open, if we have no
      // sessionId, request a fresh session with first_run=true so the
      // persona's welcome message (PR-3 system-prompt addendum) fires.
      // Sent from the SUBMIT socket so the resume/create envelope can't
      // race ahead of the WS being writable.
      const st = getState();
      if (!st.sessionId) {
        _sendSubmit({
          jsonrpc: "2.0", id: 1, method: "session.create",
          params: { first_run: true },
        });
      } else {
        _sendSubmit({
          jsonrpc: "2.0", id: 1, method: "session.resume",
          params: { session_id: st.sessionId },
        });
      }
    };
    ws.onmessage = (m) => {
      try { _routeEvent(JSON.parse(m.data)); } catch (_e) {}
    };
    ws.onclose = () => {
      if (_stopped) return;
      _scheduleReconnect(agentId);
    };
    ws.onerror = () => {};
  }

  function _scheduleReconnect(agentId) {
    if (_reconnectTimer) return;
    const failures = getState().consecutiveFailures;
    const delay = _nextDelay(failures);
    actions.setConnectionState("reconnecting");
    _reconnectTimer = setTimeout(async () => {
      _reconnectTimer = null;
      if (_stopped) return;
      _handshakeDone = false;
      const ok = await _handshake(agentId);
      if (!ok) { actions.bumpFailure(); _scheduleReconnect(agentId); return; }
      _openEvents(agentId);
      _openSubmit(agentId);
    }, delay);
  }

  function _sendSubmit(envelope) {
    if (!_submitWs || _submitWs.readyState !== 1) return false;
    try {
      _submitWs.send(JSON.stringify(envelope));
      return true;
    } catch (_e) {
      return false;
    }
  }

  async function connect(agentId) {
    _stopped = false;
    _handshakeDone = false;
    _agentIdActive = agentId;
    actions.setConnectionState("connecting");
    const ok = await _handshake(agentId);
    if (!ok) {
      actions.bumpFailure();
      _scheduleReconnect(agentId);
      return;
    }
    _openEvents(agentId);
    _openSubmit(agentId);
  }

  function disconnect() {
    _stopped = true;
    if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null; }
    if (_eventsWs) { try { _eventsWs.close(); } catch (_e) {} _eventsWs = null; }
    if (_submitWs) { try { _submitWs.close(); } catch (_e) {} _submitWs = null; }
    actions.setConnectionState("closed");
  }

  function submitPrompt(text) {
    const st = getState();
    if (!text || !text.trim()) return false;
    actions.appendUser(text);
    return _sendSubmit({
      jsonrpc: "2.0", id: Date.now(), method: "prompt.submit",
      params: { session_id: st.sessionId, text },
    });
  }

  function respondApproval(requestId, decision, extra) {
    actions.resolveApproval(requestId);
    return _sendSubmit({
      jsonrpc: "2.0", id: Date.now(), method: "approval.respond",
      params: Object.assign({ request_id: requestId, decision }, extra || {}),
    });
  }
  function respondClarify(requestId, answer) {
    actions.resolveApproval(requestId);
    return _sendSubmit({
      jsonrpc: "2.0", id: Date.now(), method: "clarify.respond",
      params: { request_id: requestId, answer },
    });
  }
  function respondSudo(requestId, password) {
    actions.resolveApproval(requestId);
    return _sendSubmit({
      jsonrpc: "2.0", id: Date.now(), method: "sudo.respond",
      params: { request_id: requestId, password },
    });
  }
  function respondSecret(requestId, secret) {
    actions.resolveApproval(requestId);
    return _sendSubmit({
      jsonrpc: "2.0", id: Date.now(), method: "secret.respond",
      params: { request_id: requestId, secret },
    });
  }

  // Restart — POSTs to a hal0-api restart endpoint. Follow-up PR owns the
  // server side; 404 surfaces as a toast.
  async function restartAgent(agentId) {
    try {
      const r = await fetch(`/api/agents/${encodeURIComponent(agentId)}/restart`, {
        method: "POST", credentials: "include",
      });
      if (!r.ok) {
        if (window.__hal0Toast) window.__hal0Toast(`Restart not available (${r.status})`, "warn");
        return false;
      }
      if (window.__hal0Toast) window.__hal0Toast("Hermes restarting…", "info");
      return true;
    } catch (_e) {
      if (window.__hal0Toast) window.__hal0Toast("Restart endpoint unreachable", "warn");
      return false;
    }
  }

  async function activatePersona(agentId, personaId) {
    try {
      const r = await fetch(
        `/api/agents/${encodeURIComponent(agentId)}/personas/${encodeURIComponent(personaId)}/activate`,
        { method: "POST", credentials: "include" },
      );
      if (!r.ok) {
        if (window.__hal0Toast) window.__hal0Toast(`Persona activate failed (${r.status})`, "warn");
        return false;
      }
      if (window.__hal0Toast) {
        window.__hal0Toast(`Persona ${personaId} activates on next turn`, "ok");
      }
      return true;
    } catch (_e) {
      if (window.__hal0Toast) window.__hal0Toast("Persona endpoint unreachable", "warn");
      return false;
    }
  }

  // ── Window-globals publish ───────────────────────────────────────
  Object.assign(window, {
    useHermesSession,
    __hal0HermesSession: {
      useHermesSession,
      connect, disconnect,
      submitPrompt,
      respondApproval, respondClarify, respondSudo, respondSecret,
      restartAgent, activatePersona,
      // Test handles
      _routeEvent,
      _nextDelay,
      _reset,
      _getState: getState,
    },
  });
})();
