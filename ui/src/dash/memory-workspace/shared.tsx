// hal0 v3 dashboard — Memory workspace shared UI primitives (Card, EngineError).
import React from 'react'

export const Card: React.FC<{ title?: string; children: React.ReactNode; testId?: string }> = ({
  title,
  children,
  testId,
}) => (
  <div className="card" data-testid={testId}>
    {title ? <div className="card-title">{title}</div> : null}
    {children}
  </div>
)

export const EngineError: React.FC<{ error: unknown; what: string; onRetry?: () => void }> = ({
  error,
  what,
  onRetry,
}) => (
  <div className="empty mono mem-error" data-testid="memws-error">
    <div>Memory engine unreachable — {(error as Error)?.message || `could not load ${what}`}</div>
    {onRetry ? (
      <button className="btn ghost sm" style={{ marginTop: 8 }} onClick={onRetry}>
        Retry
      </button>
    ) : null}
  </div>
)
