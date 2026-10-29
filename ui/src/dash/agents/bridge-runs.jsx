// hal0 dashboard — delegated runs (Hermes card ▸ Delegated tab).
//
// The runs that bridge clients (coding agents on other machines) handed to hal0's Hermes through
// the hal0-agents MCP server (ADR-0043). One row per run: short id, status chip, when it started,
// who sent it, how long it has taken, what it was asked. Filters narrow by client and by status
// group. "Events" opens the dock's events tab (the whole stream: it does not filter to the run,
// and the dock ignores the event's detail for now).
//
// Window-globals shim: reads React and the bridge hooks / pure helpers from `window`
// (agents-hook-bridge.ts), registers BridgeRunsList on it. No ES imports.

const { useState, useMemo } = React;

// Runs fetched at once. The list is for glancing at recent work, not an archive.
const RUNS_LIMIT = 100;

const GROUPS = ["live", "waiting", "ok", "bad"];

function stop(e) { e.stopPropagation(); }

function RunRow({ run, now }) {
  const fmt = window.__hal0BridgeFmt;
  const group = fmt.bridgeRunGroup(run.status);
  const open = () => {
    // Dock.jsx opens its events tab on this event and ignores the detail; it says which run asked
    // so a later dock filter has it.
    window.dispatchEvent(
      new CustomEvent("hal0:open-events", { detail: { source: "bridge", run_id: run.run_id, client: run.client } }),
    );
  };
  return (
    <div className="brg-run" data-testid={"bridge-run-" + run.run_id}>
      <div className="brg-run-top">
        <code className="brg-run-id" title={run.run_id}>{fmt.bridgeShortRunId(run.run_id)}</code>
        <span
          className={"chip " + fmt.BRIDGE_GROUP_CHIP[group]}
          data-testid="bridge-run-status"
          data-group={group}
          data-status={run.status}
        >{fmt.bridgeStatusLabel(run.status)}</span>
        <span className="grow" />
        <span className="brg-run-when" title={fmt.fmtStamp(run.created_at)}>{fmt.fmtAgo(run.created_at, now)}</span>
      </div>
      <div className="brg-run-meta">
        <span className="brg-run-client" title="bridge client">{run.client}</span>
        <span className="dim">·</span>
        <span title="agent">{run.agent}</span>
        <span className="dim">·</span>
        <span title="duration">{fmt.fmtSpan(fmt.bridgeRunSeconds(run, now))}</span>
      </div>
      {run.input_preview ? <div className="brg-run-in" title={run.input_preview}>{run.input_preview}</div> : null}
      <button type="button" className="fc-act brg-run-ev" onClick={(e) => { stop(e); open(); }}>Events</button>
    </div>
  );
}

function BridgeRunsList() {
  const fmt = window.__hal0BridgeFmt;
  const disabled = window.__hal0BridgeDisabled;
  const runsQ = window.__hal0UseBridgeRuns({ limit: RUNS_LIMIT });
  const now = fmt.useNow(1000);
  const [client, setClient] = useState("");
  const [group, setGroup] = useState("");

  const runs = (runsQ.data && runsQ.data.items) || [];
  const clients = useMemo(() => [...new Set(runs.map((r) => r.client))].sort(), [runs]);
  const shown = runs.filter(
    (r) => (!client || r.client === client) && (!group || fmt.bridgeRunGroup(r.status) === group),
  );

  const err = runsQ.error;
  let body;
  if (err && !runsQ.data) {
    body =
      disabled(err) ? (
        <div className="fc-note">Agent bridge disabled in [bridge] config</div>
      ) : err.status === 503 || err.code === "bridge.unavailable" ? (
        <div className="fc-note">Bridge service unavailable</div>
      ) : (
        <div className="fc-note">Couldn't read delegated runs: {err.message || "request failed"}</div>
      );
  } else if (runsQ.isPending) {
    body = <div className="fc-note">Loading…</div>;
  } else if (runs.length === 0) {
    body = <div className="fc-note">No delegated runs yet</div>;
  } else if (shown.length === 0) {
    body = <div className="fc-note">No runs match these filters</div>;
  } else {
    body = shown.map((r) => <RunRow key={r.run_id} run={r} now={now} />);
  }

  return (
    <div className="fcb-pane-in brg-runs" data-testid="bridge-runs" onClick={stop}>
      <div className="brg-filters">
        <select
          className="fc-select"
          aria-label="Filter by client"
          value={client}
          onChange={(e) => setClient(e.target.value)}
        >
          <option value="">All clients</option>
          {clients.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select
          className="fc-select"
          aria-label="Filter by status"
          value={group}
          onChange={(e) => setGroup(e.target.value)}
        >
          <option value="">Any status</option>
          {GROUPS.map((g) => <option key={g} value={g}>{fmt.BRIDGE_GROUP_LABEL[g]}</option>)}
        </select>
      </div>
      {body}
    </div>
  );
}

Object.assign(window, { BridgeRunsList });
