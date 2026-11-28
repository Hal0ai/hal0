# Dashboard plugin tab shows an error overlay

Symptom: a dashboard tab for a plugin shows an error overlay such as "Could not load this plugin's
script", or says the script did not register itself.

Which plugin file the dashboard expects, and what happens when it is missing, is decided by the
Hermes release the box runs. hal0 does not document it, and the answer differs between Hermes
releases, so do not diagnose it from memory or by opening files in the release.

1. Read `dashboard-plugin-js-loading.md` for what you may do (read the log, report what you saw).
2. **Ask the person** whether the plugin is expected to have a dashboard tab in the pinned Hermes
   release, and to check Hermes's documentation for it.

Never edit a plugin manifest or any other file in a Hermes release checkout, and never remove a tab
by editing one. A change there is lost on the next release, and the release belongs to hal0.
