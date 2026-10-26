// approval-args.jsx — the argument block on a gated MCP call's approval card,
// shared by the approvals modal (chrome.jsx) and the dashboard Approvals card
// (optin-cards.jsx) so the two can never drift.
//
// The agent controls the order of `args`, so a long body field placed first
// used to push the argument that picks the resource (a path arg such as
// `model_id`, `name` or `slot`) past a 120-character preview (#3052). The
// server names those arguments in the entry's `target_args`; they render
// first and in full, and the complete argument JSON is one click away.
import React from 'react'

function show(value) {
  try {
    const s = JSON.stringify(value)
    return s === undefined ? String(value) : s
  } catch {
    return String(value)
  }
}

/** Split an approval's args into target rows (in `targetArgs` order), the
 *  remaining rows (in the agent's order) and the full pretty-printed JSON. */
export function approvalArgParts(args, targetArgs) {
  const a = args && typeof args === 'object' ? args : {}
  const has = (k) => Object.prototype.hasOwnProperty.call(a, k)
  const targetKeys = [...new Set(Array.isArray(targetArgs) ? targetArgs : [])].filter(has)
  const targets = targetKeys.map((k) => [k, show(a[k])])
  const rest = Object.keys(a)
    .filter((k) => !targetKeys.includes(k))
    .map((k) => [k, show(a[k])])
  let full
  try {
    full = JSON.stringify(a, null, 2)
  } catch {
    full = '[unserialisable arguments]'
  }
  return { targets, rest, full }
}

export function ApprovalArgs({ args, targetArgs }) {
  const { targets, rest, full } = approvalArgParts(args, targetArgs)
  if (targets.length === 0 && rest.length === 0) {
    return <div className="apx mono"><span className="apx-k">no arguments</span></div>
  }
  return (
    <div className="apx mono">
      {targets.map(([k, v]) => (
        <div key={`t:${k}`} className="apx-row target">
          <span className="apx-k">{k}</span>
          <span className="apx-v">{v}</span>
        </div>
      ))}
      {rest.map(([k, v]) => (
        <div key={`r:${k}`} className="apx-row">
          <span className="apx-k">{k}</span>
          <span className="apx-v" title={v}>{v}</span>
        </div>
      ))}
      <details className="apx-full">
        <summary>full arguments</summary>
        <pre>{full}</pre>
      </details>
    </div>
  )
}
