// hal0 dashboard — the "Remember me" tick-box shared by both sign-in surfaces
// (LoginView at the front door, AuthChallengeDrawer inside the app).
//
// Ticked, the login asks for a 30-day session instead of the 8h default; the
// server decides and signs the lifetime (see rememberPreference.js for what
// the browser does and does not keep). The wording names the browser and the
// duration because that is the decision being made: this is for a machine the
// operator trusts, not a borrowed one.

export function RememberMeField({ checked, onChange, disabled, testId }) {
  return (
    <label
      className="mono"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 11.5,
        color: 'var(--fg-3, #aaa)',
        cursor: disabled ? 'default' : 'pointer',
        userSelect: 'none',
      }}
    >
      <input
        type="checkbox"
        data-testid={testId}
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        style={{ accentColor: 'var(--accent, #6ea8fe)', margin: 0 }}
      />
      Remember me on this browser for 30 days
    </label>
  )
}
