// rich-select.jsx — Task 10: shared multi-row listbox for drawer selects
// (Runtime / Profile / Model, wired in by Task 11 without touching their
// data-testids). Replaces a native <select> with a real `role="listbox"`
// popover so each option can carry a title row, right-aligned state chips,
// and a full-width description line (operator-approved two-line mockups,
// PR-4 canvas).
//
// Every stateful rule — open/close, arrow/Home/End navigation skipping
// disabled options, commit-on-Enter/click, aria-activedescendant id
// derivation — lives in rich-select-core.ts and is vitest-pinned there. This
// file is a thin rendering shell: it owns markup, the click-outside
// listener, and active-option scroll-into-view; it makes no navigation or
// selection decisions of its own.
import React from 'react'

import {
  activeDescendantId,
  closeList,
  commit,
  handleKey,
  initialState,
  optionDomId,
  selectedOption,
  toggle,
} from './rich-select-core'

const { useEffect, useId, useRef, useState } = React

/**
 * @param {{
 *   value: string|null,
 *   options: Array<{id: string, disabled?: boolean, row?: unknown, desc?: unknown, right?: unknown}>,
 *   onChange: (id: string) => void,
 *   disabled?: boolean,
 *   onOpenChange?: (open: boolean) => void,
 *   className?: string,
 *   'aria-label'?: string,
 *   'data-testid'?: string,
 * }} props
 */
export function RichSelect({
  value,
  options,
  onChange,
  disabled = false,
  onOpenChange,
  className,
  'aria-label': ariaLabel,
  'data-testid': testId,
}) {
  const generatedId = useId();
  const baseId = testId || generatedId;
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const [state, setState] = useState(() => initialState(value ?? null));

  // Optional open/close notification (Task 11d) — the Model select's caller
  // fires its one-shot batch feasibility probe here, on the transition into
  // `open`, rather than on every render while open. No consumer is required
  // to pass this; the effect is a no-op without it.
  useEffect(() => {
    if (onOpenChange) onOpenChange(state.open);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.open]);

  // A `value` change from outside while closed (e.g. a sibling control
  // driving this select, or the initial load resolving) re-seeds activeId
  // so the next open starts on the right row. While open, an in-flight
  // keyboard/mouse navigation owns activeId and is left alone.
  useEffect(() => {
    setState((s) => (s.open ? s : closeList(value ?? null)));
  }, [value]);

  useEffect(() => {
    if (!state.open) return undefined;
    const onDocMouseDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setState(closeList(value ?? null));
      }
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [state.open, value]);

  useEffect(() => {
    if (!state.open || !listRef.current || state.activeId == null) return;
    const escaped = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(state.activeId) : state.activeId;
    const el = listRef.current.querySelector(`[data-option-id="${escaped}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }, [state.open, state.activeId]);

  const fireChange = (id) => {
    if (onChange) onChange(id);
  };

  const onTriggerClick = () => {
    if (disabled) return;
    setState((s) => toggle(s, options, value ?? null));
  };

  const onKeyDown = (e) => {
    if (disabled) return;
    const wasOpen = state.open;
    const outcome = handleKey(e.key, state, options, value ?? null);
    if (outcome.kind === 'none') return;
    // Tab must keep moving focus per normal browser behaviour — only close.
    if (e.key !== 'Tab') e.preventDefault();
    // Escape on an OPEN listbox is consumed here: it closes only the
    // dropdown, and must not also reach the drawer's document-level
    // Escape→close listener (primitives.jsx). Escape while closed still
    // bubbles so the drawer keeps its normal dismiss behaviour.
    if (e.key === 'Escape' && wasOpen) e.stopPropagation();
    setState(outcome.state);
    if (outcome.kind === 'commit') fireChange(outcome.id);
  };

  const onOptionClick = (optionId) => {
    const result = commit(state, options, optionId);
    setState(result.nextState);
    if (result.committedId != null) fireChange(result.committedId);
  };

  const selected = selectedOption(options, value ?? null);

  return (
    <div className="rsel" ref={rootRef}>
      <button
        type="button"
        className={className ? `rsel-trigger ${className}` : 'rsel-trigger'}
        data-testid={testId}
        disabled={!!disabled}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={state.open}
        // #2204: only name the listbox once it actually exists — pointing
        // aria-controls at a closed/unrendered id misdirects assistive tech.
        aria-controls={state.open ? `${baseId}-listbox` : undefined}
        aria-activedescendant={activeDescendantId(baseId, state)}
        aria-label={ariaLabel}
        onClick={onTriggerClick}
        onKeyDown={onKeyDown}
      >
        <span className="rsel-trigger-row">
          {selected ? (
            <span className="rsel-trigger-row-main">{selected.row}</span>
          ) : (
            <span className="rsel-trigger-placeholder">— select —</span>
          )}
          {selected && selected.right}
        </span>
        <span className="rsel-trigger-caret" aria-hidden="true">▾</span>
      </button>
      {state.open && (
        <ul className="rsel-listbox" role="listbox" id={`${baseId}-listbox`} ref={listRef}>
          {options.length === 0 && <li className="rsel-empty">No options</li>}
          {options.map((opt) => (
            <li
              key={opt.id}
              id={optionDomId(baseId, opt.id)}
              data-option-id={opt.id}
              role="option"
              aria-selected={opt.id === value}
              aria-disabled={!!opt.disabled}
              data-active={opt.id === state.activeId}
              className="rsel-option"
              onMouseEnter={() => {
                if (!opt.disabled) setState((s) => ({ ...s, activeId: opt.id }));
              }}
              onClick={() => onOptionClick(opt.id)}
            >
              <div className="rsel-option-row">
                <span className="rsel-option-row-main">{opt.row}</span>
                {opt.right && <span className="rsel-option-right">{opt.right}</span>}
              </div>
              {opt.desc && <div className="rsel-option-desc">{opt.desc}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
