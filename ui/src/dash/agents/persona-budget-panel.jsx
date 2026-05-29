// hal0 v0.3 Phase 0 — PersonaBudgetPanel.
//
// Per-persona spending caps editor. Mounts inside personas-tab.jsx
// when the operator opens a persona detail. Reads budget + running
// spend via `window.__hal0UsePersonaBudget`, mutates via
// `window.__hal0PutPersonaBudget` (TanStack bridge, same window-globals
// pattern as PersonasTab — see persona-budget-hook-bridge.ts).
//
// Empty-state copy is the CTA: "no budget set — set caps to enable
// cloud providers". That's the v0.3 line connecting this primitive to
// the V1 OpenRouter provider; once V1 ships, the empty-state message
// rewrites to "OpenRouter inactive — set a daily cap to enable".

const { useEffect: useEffectPBP, useState: useStatePBP } = React

function _fmtUsd(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return "—";
  return "$" + n.toFixed(4).replace(/0+$/, "").replace(/\.$/, ".00");
}

function _toForm(budget) {
  // Convert API budget shape → editable form strings. ``null`` / missing
  // caps render as empty strings so the operator sees the empty box
  // (not a literal "null"); we only PUT back the fields the user typed.
  const b = budget || { hard_cap: true };
  return {
    daily_usd: b.daily_usd != null ? String(b.daily_usd) : "",
    monthly_usd: b.monthly_usd != null ? String(b.monthly_usd) : "",
    lifetime_usd: b.lifetime_usd != null ? String(b.lifetime_usd) : "",
    per_call_max_usd: b.per_call_max_usd != null ? String(b.per_call_max_usd) : "",
    hard_cap: b.hard_cap !== false,
  };
}

function _formToPayload(form) {
  const out = { hard_cap: !!form.hard_cap };
  const numeric = ["daily_usd", "monthly_usd", "lifetime_usd", "per_call_max_usd"];
  for (const k of numeric) {
    const raw = (form[k] ?? "").trim();
    if (raw === "") continue;
    const n = Number(raw);
    if (!Number.isFinite(n) || n < 0) continue;
    out[k] = n;
  }
  return out;
}

function PersonaBudgetPanel({ agentId, personaId } = {}) {
  const useBudget = window.__hal0UsePersonaBudget;
  const usePut = window.__hal0PutPersonaBudget;
  const query = useBudget ? useBudget(agentId, personaId) : { data: null, isLoading: false, isError: false };
  const mutation = usePut ? usePut(agentId, personaId) : { mutate: () => {}, isPending: false, error: null };

  const [form, setForm] = useStatePBP(() => _toForm(query.data && query.data.budget));
  const [dirty, setDirty] = useStatePBP(false);
  const [err, setErr] = useStatePBP(null);

  // Re-seed the form from the server snapshot when the persona changes
  // OR the server-side budget changes due to a non-UI mutation (e.g.
  // a /api/agents/.../budget/charge from V1's OpenRouter provider). We
  // intentionally don't re-seed while the form is dirty — that would
  // wipe the operator's pending edits during the 15s poll.
  useEffectPBP(() => {
    if (dirty) return;
    setForm(_toForm(query.data && query.data.budget));
  }, [query.data, personaId, agentId, dirty]);

  const change = (key) => (event) => {
    const value = event && event.target ? (key === "hard_cap" ? event.target.checked : event.target.value) : event;
    setForm((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
    setErr(null);
  };

  const save = async () => {
    setErr(null);
    const payload = _formToPayload(form);
    try {
      await mutation.mutateAsync(payload);
      setDirty(false);
      if (window.__hal0Toast) window.__hal0Toast("Budget saved", "ok");
    } catch (exc) {
      setErr((exc && exc.message) || String(exc));
    }
  };

  const reset = () => {
    setForm(_toForm(query.data && query.data.budget));
    setDirty(false);
    setErr(null);
  };

  const budget = (query.data && query.data.budget) || { hard_cap: true };
  const spend = (query.data && query.data.spend) || { today_usd: 0, mtd_usd: 0, lifetime_usd: 0 };
  const remaining = (query.data && query.data.remaining) || {};
  const isEmpty = !budget.daily_usd && !budget.monthly_usd && !budget.lifetime_usd && !budget.per_call_max_usd;

  return (
    <div
      data-testid="persona-budget-panel"
      className="card"
      style={{ padding: 18, marginTop: 14, display: "grid", gap: 14 }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div className="mono" style={{ fontSize: 13, fontWeight: 500, letterSpacing: "-0.01em" }}>
          Spending cap (persona-scoped)
        </div>
        {isEmpty && (
          <span className="chip" style={{ marginLeft: "auto" }} data-testid="persona-budget-empty">
            no cap set
          </span>
        )}
        {!isEmpty && budget.hard_cap === false && (
          <span className="chip amber" style={{ marginLeft: "auto" }}>
            warn-only
          </span>
        )}
      </div>

      {isEmpty && (
        <p style={{ fontSize: 12.5, color: "var(--fg-2)", margin: 0, lineHeight: 1.55 }}>
          No budget set — set caps to enable cloud providers (OpenRouter, fusion).
          Without a cap a single recursing agent loop can drain a credit pool
          overnight; that's why hal0 won't enable paid surfaces until at least
          a daily limit is configured.
        </p>
      )}

      <div
        className="mono"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 10,
          fontSize: 11,
          color: "var(--fg-3)",
        }}
        data-testid="persona-budget-spend"
      >
        <div>
          <div>spent today</div>
          <div style={{ color: "var(--fg-1)", fontSize: 14, marginTop: 2 }}>{_fmtUsd(spend.today_usd)}</div>
          {remaining.daily_usd != null && (
            <div style={{ marginTop: 2 }}>remaining {_fmtUsd(remaining.daily_usd)}</div>
          )}
        </div>
        <div>
          <div>spent MTD</div>
          <div style={{ color: "var(--fg-1)", fontSize: 14, marginTop: 2 }}>{_fmtUsd(spend.mtd_usd)}</div>
          {remaining.monthly_usd != null && (
            <div style={{ marginTop: 2 }}>remaining {_fmtUsd(remaining.monthly_usd)}</div>
          )}
        </div>
        <div>
          <div>spent lifetime</div>
          <div style={{ color: "var(--fg-1)", fontSize: 14, marginTop: 2 }}>{_fmtUsd(spend.lifetime_usd)}</div>
          {remaining.lifetime_usd != null && (
            <div style={{ marginTop: 2 }}>remaining {_fmtUsd(remaining.lifetime_usd)}</div>
          )}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 10 }}>
        {[
          ["daily_usd", "Daily cap (USD)"],
          ["monthly_usd", "Monthly cap (USD)"],
          ["lifetime_usd", "Lifetime cap (USD)"],
          ["per_call_max_usd", "Per-call max (USD)"],
        ].map(([key, label]) => (
          <label key={key} className="mono" style={{ display: "grid", gap: 4, fontSize: 11, color: "var(--fg-3)" }}>
            {label}
            <input
              data-testid={`persona-budget-${key}`}
              className="input"
              type="number"
              min="0"
              step="0.01"
              value={form[key]}
              onChange={change(key)}
              placeholder="—"
              disabled={!agentId || !personaId}
              style={{ padding: "6px 8px", background: "var(--bg-2)", border: "1px solid var(--line)", borderRadius: 4 }}
            />
          </label>
        ))}
      </div>

      <label className="mono" style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, color: "var(--fg-2)" }}>
        <input
          data-testid="persona-budget-hard-cap"
          type="checkbox"
          checked={!!form.hard_cap}
          onChange={change("hard_cap")}
          disabled={!agentId || !personaId}
        />
        Hard cap (block requests over budget). Uncheck for warn-only.
      </label>

      {err && (
        <div className="mono" style={{ fontSize: 11, color: "var(--err)" }} data-testid="persona-budget-error">
          {err}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn ghost sm" onClick={reset} disabled={!dirty || mutation.isPending}>
          Reset
        </button>
        <button
          className="btn primary sm"
          onClick={save}
          disabled={!dirty || mutation.isPending || !agentId || !personaId}
          data-testid="persona-budget-save"
        >
          {mutation.isPending ? "Saving…" : "Save budget"}
        </button>
      </div>
    </div>
  );
}

Object.assign(window, { PersonaBudgetPanel });
