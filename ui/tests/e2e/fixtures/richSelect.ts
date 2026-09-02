/**
 * richSelect — shared interaction helpers for `RichSelect` (Task 10,
 * `ui/src/dash/rich-select.jsx`), the button+listbox popover that replaced
 * the slot drawer's native `<select>`s for Runtime (`slot-hw-runtime`),
 * Profile (`slot-profile`) and Model (`slot-model-swap`) — Task 11c.
 *
 * A RichSelect trigger is a `<button role="combobox">`, never a `<select>`,
 * so `.selectOption()` / `.toHaveValue()` don't apply to it. Every spec that
 * drives one goes through these three helpers instead of re-deriving the
 * open/click/close mechanics inline:
 *
 *   - `openRichSelect(trigger)`  — click the trigger open (no-op if already
 *     open) and return its listbox `Locator`.
 *   - `richSelectOption(trigger, id)` — the `<li role="option">` for a given
 *     `RichSelect` option id (opens the listbox first).
 *   - `pickRichOption(trigger, id)` — open + click that option, the
 *     RichSelect equivalent of `.selectOption(id)`.
 *
 * All three take the TRIGGER LOCATOR, not a testid string, so a spec can
 * still reach it via `page.getByTestId(...)` (asserting the plumbing this
 * file's own contract pins) or `page.getByLabel(...)` (asserting the
 * `aria-label` passthrough task-11's fix report restored) — whichever the
 * spec already needed for its own assertions. The listbox id is read off
 * the trigger's own `aria-controls`, so this never hardcodes the
 * `{testid}-listbox` derivation `rich-select.jsx` happens to use today.
 *
 * A closed trigger already renders its selected option's `row` + `right`
 * content (`rich-select.jsx`'s `selected` branch) — so verifying the CURRENT
 * value of a RichSelect rarely needs to open it at all; prefer
 * `expect(trigger).toContainText(...)` for that over opening + reading
 * `aria-selected`.
 */
import type { Locator } from '@playwright/test'

async function listboxFor(trigger: Locator): Promise<Locator> {
  const id = await trigger.getAttribute('aria-controls')
  if (!id) {
    throw new Error(
      'richSelect helper: trigger has no aria-controls — is this actually a RichSelect button?',
    )
  }
  return trigger.page().locator(`[id="${id}"]`)
}

/** Open a RichSelect's listbox (no-op if already open); returns the listbox. */
export async function openRichSelect(trigger: Locator): Promise<Locator> {
  if ((await trigger.getAttribute('aria-expanded')) !== 'true') {
    await trigger.click()
  }
  return listboxFor(trigger)
}

/**
 * Close a RichSelect's listbox (no-op if already closed), via Escape.
 *
 * Safe inside a drawer: `rich-select.jsx` consumes Escape on an OPEN
 * listbox (`stopPropagation()` in its trigger keydown), so the drawer's
 * `document`-level Escape handler (`primitives.jsx`'s `Drawer`) never sees
 * the press — only the dropdown closes. Escape on an already-closed
 * RichSelect still bubbles to the drawer, which is why this helper checks
 * `aria-expanded` before pressing.
 */
export async function closeRichSelect(trigger: Locator): Promise<void> {
  if ((await trigger.getAttribute('aria-expanded')) === 'true') {
    await trigger.page().keyboard.press('Escape')
  }
}

/** The `<li role="option">` for one RichSelect option id — opens the listbox first. */
export async function richSelectOption(trigger: Locator, optionId: string): Promise<Locator> {
  const listbox = await openRichSelect(trigger)
  return listbox.locator(`[data-option-id="${optionId}"]`)
}

/** Open + click an option by id — the RichSelect equivalent of `.selectOption(id)`. */
export async function pickRichOption(trigger: Locator, optionId: string): Promise<void> {
  const option = await richSelectOption(trigger, optionId)
  await option.click()
}
