// hal0 dashboard — Bridge clients card (Agents ▸ MCP tab, beside the MCP server list).
//
// A bridge client is a coding agent on another machine (Claude Code and the like) that reaches
// hal0 over MCP with its own key (ADR-0043): it delegates work to Hermes through hal0-agents,
// and can read and write hal0's memory through hal0-memory. The operator mints, rotates, re-scopes
// and revokes those keys here; `hal0 bridge` does the same from a shell.
//
//   list      one row per client: name, note, scope chips (named like the server rows above them),
//             when the key was last used. A row opens to show when it was made, how many runs it
//             has live, and Scopes · Rotate key · Revoke.
//   add       name (the API's rule), note, scopes. `agents` is always on; `memory` is on by
//             default; `admin` is off and carries a warning. The result is a second view of the
//             same modal: the key, once, then one `claude mcp add` line per server, each with its
//             own copy button. That view closes only on Done, so the key is not lost to a stray
//             click, and it is dropped from memory when it closes.
//   rotate    the same result view.
//   disabled  a 404 `bridge.disabled` on add / rotate / scopes means `[bridge].enabled` is off.
//             The card says so in one line and stops offering Add. (Listing and revoking still
//             work while the bridge is off.)
//
// The key is only ever rendered into the result view. It is not logged, toasted or stored.
//
// Window-globals shim: reads React, Modal, ConfirmDialog, EnginePane, Icon and the bridge hooks /
// pure helpers from `window` (primitives.jsx, connections.jsx, agents-hook-bridge.ts), registers
// BridgeClientsCard on it. No ES imports.

const { useState, useEffect } = React;

const SCOPES = ["agents", "memory", "admin"];
const SCOPE_NOTE = {
  agents: "Run tasks on Hermes and read their results.",
  memory: "Read and write hal0's memory.",
  admin: "Reaches hal0's admin MCP tools. Treat this key like an operator credential.",
};
const DISABLED_LINE = "Agent bridge disabled in [bridge] config";
const SEEN_TIP = "Updated at most every 60 s";

function toast(msg, kind) {
  if (window.__hal0Toast) window.__hal0Toast(msg, kind);
}

function errText(err) {
  return (err && err.message) || "request failed";
}

function useNow(ms) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

// ── scopes ──────────────────────────────────────────────────────────

function ScopeChips({ scopes }) {
  const server = window.__hal0BridgeScopeServer;
  return (
    <>
      {SCOPES.filter((s) => scopes.includes(s)).map((s) => (
        <span className={"chip " + (s === "admin" ? "warn" : "info")} key={s} data-scope={s}>{server[s]}</span>
      ))}
    </>
  );
}

// `agents` is the point of a bridge client, so it is always on and cannot be unticked.
function ScopePicker({ value, onChange }) {
  const server = window.__hal0BridgeScopeServer;
  const toggle = (s) => onChange(SCOPES.filter((x) => (x === s ? !value.includes(s) : value.includes(x))));
  return (
    <div className="brg-scopes" role="group" aria-label="Scopes">
      {SCOPES.map((s) => (
        <label className={"brg-scope" + (s === "admin" ? " admin" : "")} key={s}>
          <input
            type="checkbox"
            data-scope={s}
            checked={s === "agents" || value.includes(s)}
            disabled={s === "agents"}
            onChange={() => toggle(s)}
          />
          <span className="brg-scope-name">{server[s]}</span>
          <span className="brg-scope-note">{SCOPE_NOTE[s]}</span>
        </label>
      ))}
    </div>
  );
}

// ── copy ────────────────────────────────────────────────────────────

function CopyBtn({ text, label, what }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className={"cbtn sm" + (done ? " ok-flash" : "")}
      onClick={() => {
        window.__hal0CopyText(text).then((ok) => {
          if (!ok) {
            toast("Copy failed — clipboard unavailable", "err");
            return;
          }
          setDone(true);
          toast(what + " copied", "ok");
          setTimeout(() => setDone(false), 1300);
        });
      }}
    >{done ? "Copied" : label}</button>
  );
}

// ── add ─────────────────────────────────────────────────────────────

function AddClientModal({ open, create, onClose, onMinted, onDisabled }) {
  const fmt = window.__hal0BridgeFmt;
  const Modal = window.Modal;
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [scopes, setScopes] = useState(["agents", "memory"]);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return;
    setName("");
    setNote("");
    setScopes(["agents", "memory"]);
    setError(null);
  }, [open]);

  const nameError = name ? fmt.bridgeNameError(name) : null;
  const canSubmit = !!name && !nameError && !create.isPending;

  const submit = async () => {
    if (!canSubmit) return;
    setError(null);
    try {
      const minted = await create.mutateAsync({ name, note: note.trim(), scopes });
      onMinted(minted);
    } catch (err) {
      if (window.__hal0BridgeDisabled(err)) onDisabled();
      else setError(errText(err));
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="bridge client"
      title="Add bridge client"
      width={560}
      foot={
        <>
          <span style={{ color: "var(--fg-4)" }}>hal0 stores only a hash of the key.</span>
          <span style={{ display: "inline-flex", gap: 8 }}>
            <button type="button" className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button type="button" className="btn sm" data-testid="bridge-add-submit" disabled={!canSubmit} onClick={submit}>
              {create.isPending ? "Adding…" : "Add client"}
            </button>
          </span>
        </>
      }
    >
      <div className="brg-form">
        <div className="brg-field">
          <label htmlFor="brg-name" className="brg-label">Name</label>
          <input
            id="brg-name"
            className={"input mono" + (nameError ? " input-err" : "")}
            data-testid="bridge-add-name"
            value={name}
            placeholder="laptop"
            autoFocus
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          />
          {nameError ? <div className="brg-err" role="alert">{nameError}</div> : null}
        </div>
        <div className="brg-field">
          <label htmlFor="brg-note" className="brg-label">Note</label>
          <input
            id="brg-note"
            className="input"
            data-testid="bridge-add-note"
            value={note}
            placeholder="Whose machine, and what it is for"
            autoComplete="off"
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          />
        </div>
        <div className="brg-field">
          <span className="brg-label">Scopes</span>
          <ScopePicker value={scopes} onChange={setScopes} />
        </div>
        {error ? <div className="brg-err" role="alert">{error}</div> : null}
      </div>
    </Modal>
  );
}

// ── the key, once ───────────────────────────────────────────────────

function KeyResultModal({ result, onDone }) {
  const Modal = window.Modal;
  if (!result) return null;
  const { kind, minted } = result;
  const lines = (minted.onboarding && minted.onboarding.lines) || [];
  return (
    <Modal
      open
      onClose={onDone}
      dismissable={false}
      eyebrow={kind === "rotate" ? "key rotated" : "client added"}
      title={(kind === "rotate" ? "New key for " : "Key for ") + minted.client.name}
      width={680}
      foot={
        <>
          <span style={{ color: "var(--fg-4)" }}>Closing this drops the key from the page.</span>
          <button type="button" className="btn sm" data-testid="bridge-result-done" onClick={onDone}>Done</button>
        </>
      }
    >
      <div className="brg-form" data-testid="bridge-result">
        <div className="brg-field">
          <span className="brg-label">API key</span>
          <div className="brg-code">
            <code data-testid="bridge-key">{minted.key}</code>
            <CopyBtn text={minted.key} label="Copy key" what="Key" />
          </div>
          <div className="brg-warn">Shown once — hal0 stores only a hash.</div>
        </div>
        {lines.length > 0 ? (
          <div className="brg-field">
            <span className="brg-label">Run on the client's machine</span>
            {lines.map((l) => (
              <div className="brg-line" key={l.server}>
                <div className="brg-line-l">{l.server}</div>
                <div className="brg-code">
                  <pre data-testid={"bridge-add-line-" + l.server}>{l.claude_mcp_add}</pre>
                  <CopyBtn text={l.claude_mcp_add} label={"Copy " + l.server} what={l.server + " command"} />
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}

// ── scopes editor ───────────────────────────────────────────────────

function ScopesModal({ client, scopesMut, onClose, onDisabled }) {
  const Modal = window.Modal;
  const [scopes, setScopes] = useState(client ? client.scopes : []);
  const [error, setError] = useState(null);
  useEffect(() => {
    setScopes(client ? client.scopes : []);
    setError(null);
  }, [client]);
  if (!client) return null;
  const same = scopes.length === client.scopes.length && scopes.every((s) => client.scopes.includes(s));
  const save = async () => {
    setError(null);
    try {
      await scopesMut.mutateAsync({ name: client.name, scopes });
      toast("Scopes updated for " + client.name, "ok");
      onClose();
    } catch (err) {
      if (window.__hal0BridgeDisabled(err)) {
        onClose();
        onDisabled();
      } else setError(errText(err));
    }
  };
  return (
    <Modal
      open
      onClose={onClose}
      eyebrow="bridge client"
      title={"Scopes for " + client.name}
      width={560}
      foot={
        <>
          <span style={{ color: "var(--fg-4)" }}>The key does not change.</span>
          <span style={{ display: "inline-flex", gap: 8 }}>
            <button type="button" className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button
              type="button"
              className="btn sm"
              data-testid="bridge-scopes-save"
              disabled={same || scopesMut.isPending}
              onClick={save}
            >{scopesMut.isPending ? "Saving…" : "Save scopes"}</button>
          </span>
        </>
      }
    >
      <div className="brg-form">
        <ScopePicker value={scopes} onChange={setScopes} />
        <div className="brg-note">
          Takes effect on the client's next request. To get the <code>claude mcp add</code> lines for a server
          it did not have, rotate the key.
        </div>
        {error ? <div className="brg-err" role="alert">{error}</div> : null}
      </div>
    </Modal>
  );
}

// ── one client ──────────────────────────────────────────────────────

// Runs this client has in flight. One small query per row, made only while the row is open, so a
// long client list costs nothing until someone looks.
function ActiveRuns({ name }) {
  const fmt = window.__hal0BridgeFmt;
  const q = window.__hal0UseBridgeRuns({ client: name, limit: 50 });
  if (q.isPending) return <span>active runs <b>…</b></span>;
  if (q.error) return <span title="The bridge service did not answer">active runs <b>—</b></span>;
  const n = ((q.data && q.data.items) || []).filter((r) => {
    const g = fmt.bridgeRunGroup(r.status);
    return g === "live" || g === "waiting";
  }).length;
  return <span data-testid="bridge-active-runs">active runs <b>{n}</b></span>;
}

function ClientRow({ c, now, busy, onScopes, onRotate, onRevoke }) {
  const fmt = window.__hal0BridgeFmt;
  const Icon = window.Icon;
  const [open, setOpen] = useState(false);
  const revoked = c.revoked_at != null;
  const seen = c.last_seen_at == null ? "never seen" : "seen " + fmt.fmtAgo(c.last_seen_at, now);
  const seenTip = SEEN_TIP + (c.last_seen_at != null ? " · " + fmt.fmtStamp(c.last_seen_at) : "");
  return (
    <div className={"mcprow" + (open ? " expanded" : "")} data-testid={"bridge-client-" + c.name} data-revoked={revoked ? "true" : "false"}>
      <div className="mcprow-main" onClick={() => setOpen((o) => !o)}>
        <span className={"mcp-dot" + (revoked ? " down" : "")} />
        <span className="mcp-id">
          <span><span className="nm">{c.name}</span></span>
          <div className="desc">{c.note || "no note"}</div>
        </span>
        <span className="mcp-meta">
          {revoked ? <span className="chip err" data-testid="bridge-revoked">revoked</span> : null}
          <ScopeChips scopes={c.scopes} />
        </span>
        <span className="mcp-toolct" title={seenTip} data-testid="bridge-last-seen">{seen}</span>
        <span className="mcp-caret"><Icon d="M4 6l4 4 4-4" size={14} /></span>
      </div>
      <div className="mcp-drawer">
        <div className="mcp-drawer-in">
          {open ? (
            <div className="brg-detail">
              <div className="brg-facts">
                <span title={fmt.fmtStamp(c.created_at)}>created <b>{fmt.fmtAgo(c.created_at, now)}</b> by {c.created_by}</span>
                {revoked ? <span title={fmt.fmtStamp(c.revoked_at)}>revoked <b>{fmt.fmtAgo(c.revoked_at, now)}</b></span> : null}
                <ActiveRuns name={c.name} />
              </div>
              {revoked ? null : (
                <div className="brg-acts">
                  <button type="button" className="cbtn sm" disabled={busy} onClick={() => onScopes(c)}>Scopes</button>
                  <button type="button" className="cbtn sm" disabled={busy} onClick={() => onRotate(c.name)}>Rotate key</button>
                  <button type="button" className="cbtn sm brg-danger" disabled={busy} onClick={() => onRevoke(c.name)}>Revoke</button>
                </div>
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

// ── the card ────────────────────────────────────────────────────────

function BridgeClientsCard() {
  const EnginePane = window.EnginePane;
  const ConfirmDialog = window.ConfirmDialog;
  const clientsQ = window.__hal0UseBridgeClients();
  const create = window.__hal0UseBridgeClientCreate();
  const rotate = window.__hal0UseBridgeClientRotate();
  const revoke = window.__hal0UseBridgeClientRevoke();
  const scopesMut = window.__hal0UseBridgeClientScopes();
  const now = useNow(30000);

  const [addOpen, setAddOpen] = useState(false);
  const [disabled, setDisabled] = useState(false);
  const [result, setResult] = useState(null);
  const [rotating, setRotating] = useState(null);
  const [revoking, setRevoking] = useState(null);
  const [editing, setEditing] = useState(null);

  const items = (clientsQ.data && clientsQ.data.items) || [];
  const active = items.filter((c) => c.revoked_at == null).length;
  const busy = rotate.isPending || revoke.isPending;

  const minted = (kind, m) => {
    setAddOpen(false);
    setResult({ kind, minted: m });
  };
  // Drop the key from the page and from the mutations that carried it.
  const doneWithKey = () => {
    setResult(null);
    create.reset();
    rotate.reset();
  };
  const markDisabled = () => {
    setAddOpen(false);
    setRotating(null);
    setDisabled(true);
  };

  const confirmRotate = async () => {
    const name = rotating;
    setRotating(null);
    try {
      minted("rotate", await rotate.mutateAsync(name));
    } catch (err) {
      if (window.__hal0BridgeDisabled(err)) markDisabled();
      else toast("Rotate failed: " + errText(err), "err");
    }
  };
  const confirmRevoke = async () => {
    const name = revoking;
    setRevoking(null);
    try {
      await revoke.mutateAsync(name);
      toast("Revoked " + name, "ok");
    } catch (err) {
      toast("Revoke failed: " + errText(err), "err");
    }
  };

  let body;
  if (clientsQ.error && !clientsQ.data) {
    body = <div className="cn-empty mono">Couldn't read bridge clients: {errText(clientsQ.error)}</div>;
  } else if (clientsQ.isPending) {
    body = <div className="cn-empty mono">Loading bridge clients…</div>;
  } else if (items.length === 0) {
    body = (
      <div className="cn-empty mono">
        No bridge clients yet. Add one to let a coding agent on another machine delegate to Hermes.
      </div>
    );
  } else {
    body = items.map((c) => (
      <ClientRow key={c.name} c={c} now={now} busy={busy} onScopes={setEditing} onRotate={setRotating} onRevoke={setRevoking} />
    ));
  }

  return (
    <>
      <EnginePane
        glyph="link"
        title="Bridge clients"
        sub="remote coding agents · hal0-agents"
        pill={{ tone: active ? "ok" : "", text: active + " active" }}
        headRight={
          <button
            type="button"
            className="cbtn primary"
            data-testid="bridge-add"
            disabled={disabled}
            title={disabled ? DISABLED_LINE : undefined}
            onClick={() => setAddOpen(true)}
          >Add client</button>
        }
        strip={
          <span className="ss-summary">
            <b>{active}</b> active · {items.length - active} revoked · each client has its own key
          </span>
        }
        foot={
          <>
            <span className="k">mount</span>
            <span className="v">/mcp/agents/mcp</span>
            <span className="sep">·</span>
            <span className="k">auth</span>
            <span className="v">bearer key per client</span>
            <span className="sep">·</span>
            <span className="k">keys</span>
            <span className="v">hashed · shown once</span>
          </>
        }
      >
        {disabled ? <div className="cn-empty mono brg-off" role="status">{DISABLED_LINE}</div> : null}
        <div className="mcplist" data-testid="bridge-clients">{body}</div>
      </EnginePane>

      <AddClientModal open={addOpen} create={create} onClose={() => setAddOpen(false)} onMinted={(m) => minted("add", m)} onDisabled={markDisabled} />
      <KeyResultModal result={result} onDone={doneWithKey} />
      <ScopesModal client={editing} scopesMut={scopesMut} onClose={() => setEditing(null)} onDisabled={markDisabled} />
      <ConfirmDialog
        open={!!rotating}
        onCancel={() => setRotating(null)}
        onConfirm={confirmRotate}
        title={"Rotate the key for " + rotating + "?"}
        message="The current key stops working immediately. You get a new one to install on that machine."
        confirmLabel="Rotate key"
        footerNote="The old key cannot be brought back."
      />
      <ConfirmDialog
        open={!!revoking}
        onCancel={() => setRevoking(null)}
        onConfirm={confirmRevoke}
        title={"Revoke " + revoking + "?"}
        message="Its key stops working immediately, and the name stays taken so it cannot be reused."
        confirmLabel="Revoke"
        destructive
      />
    </>
  );
}

Object.assign(window, { BridgeClientsCard });
