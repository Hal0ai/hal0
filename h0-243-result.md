Removed the unused `EDITOR_PORT` import from `ui/src/dash/image/useEditorAccess.ts`; `EDITOR_PORT` remains owned by `editor-reach.ts` and its active consumers.

Commit: `c4c2d3c1e` (`fix(ui): remove unused editor port import`), signed off.

Verification: `git diff --check` passed. UI lint, typecheck, unit tests, and build were attempted but could not run because this checkout has no `ui/node_modules` (`eslint`, `tsc`, `vitest`, and `vite` unavailable).