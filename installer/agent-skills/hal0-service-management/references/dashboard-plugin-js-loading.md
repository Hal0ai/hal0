# Dashboard plugin tab: "Could not load this plugin's script"

How the Hermes dashboard finds and loads a plugin's script is Hermes's own code and changes between
releases. hal0 does not document it, so this file does not describe it. Do not rely on file names,
manifest fields or endpoint paths you remember from another Hermes version.

What you can do, read-only:

1. Note which tab fails and the exact text the page shows.
2. Read the dashboard's log for errors from that moment:
   `hal0 doctor logs --unit hal0-agent@hermes -n 200`. The dashboard runs inside
   `hal0-agent@hermes`; it is not a separate service.
3. Tell the person what you saw. **Ask the person** to check Hermes's documentation for the pinned
   release, or to report it upstream.

What you must not do: edit a plugin's `manifest.json` or any other file inside a Hermes release
checkout. The release is built by hal0, is replaced whole on the next update, and is not yours to
change. Do not start `hermes dashboard` yourself either.

See also `dashboard-plugin-entry-mismatch.md`.
