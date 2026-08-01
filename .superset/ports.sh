#!/usr/bin/env bash
# Per-workspace port + identity derivation for Superset workspaces.
#
#   source .superset/ports.sh
#
# Superset gives every task its own git worktree, and several of them are alive
# at once. Anything that binds a port has to be unique per worktree or the
# second workspace silently attaches to the first one's server and you test a
# branch you never checked out. That exact failure is documented for the
# Playwright suite in ui/tests/e2e/port.ts (#1399); this file applies the same
# rule to the dev servers.
#
# Properties, in priority order:
#   1. STABLE per workspace  — same path always yields the same ports, so a
#      restart reuses caches and bookmarks keep working.
#   2. DISTINCT across workspaces — the actual bug being avoided.
#   3. Clear of the defaults (8080 / 5173 / 3001) and clear of the e2e window
#      (5300-5999, owned by ui/tests/e2e/port.ts) so nothing can cross-attach.
#
# Explicit env always wins: exporting HAL0_PORT / UI_PORT / HAL0_OPENWEBUI_PORT
# before sourcing pins that value (CI, debugging, attaching to a hand-started
# server).

# Derived-port windows. Deliberately disjoint from each other, from the service
# defaults, and from ui/tests/e2e/port.ts's 5300-5999.
HAL0_API_PORT_RANGE_START=18000
HAL0_API_PORT_RANGE_END=18499
HAL0_UI_PORT_RANGE_START=6100
HAL0_UI_PORT_RANGE_END=6599
HAL0_OWU_PORT_RANGE_START=3300
HAL0_OWU_PORT_RANGE_END=3799

# Absolute path of the workspace. Superset exports SUPERSET_WORKSPACE_PATH;
# fall back to this file's parent so the scripts also work when run by hand.
hal0_workspace_path() {
    if [[ -n "${SUPERSET_WORKSPACE_PATH:-}" ]]; then
        (cd "${SUPERSET_WORKSPACE_PATH}" && pwd)
    else
        (cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
    fi
}

# Superset is a macOS app, so nothing here may assume GNU coreutils: macOS has
# `shasum -a 256`, not `sha256sum`.
hal0_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum | cut -d' ' -f1
    else
        shasum -a 256 | cut -d' ' -f1
    fi
}

# Resolve a path to its physical location without `readlink -f`, which BSD
# readlink didn't carry for years. `cd` follows symlinks and `pwd -P` reports
# where it landed, which is exactly the question the teardown guard asks.
hal0_realpath() {
    local p="$1" d b
    if [[ -d "${p}" ]]; then
        (cd "${p}" 2>/dev/null && pwd -P)
    else
        d="$(dirname "${p}")"
        b="$(basename "${p}")"
        (cd "${d}" 2>/dev/null && printf '%s/%s\n' "$(pwd -P)" "${b}")
    fi
}

# sha256 so sibling worktree paths (which share a long prefix) don't cluster
# into adjacent ports.
hal0_derive_port() {
    local key="$1" lo="$2" hi="$3" span hex
    span=$(( hi - lo + 1 ))
    hex="$(printf '%s' "${key}" | hal0_sha256 | cut -c1-8)"
    printf '%d' "$(( lo + (16#${hex} % span) ))"
}

HAL0_WORKSPACE_PATH="$(hal0_workspace_path)"
HAL0_WORKSPACE_SLUG="$(printf '%s' "${HAL0_WORKSPACE_PATH}" | hal0_sha256 | cut -c1-8)"
HAL0_WORKSPACE_NAME="${SUPERSET_WORKSPACE_NAME:-$(basename "${HAL0_WORKSPACE_PATH}")}"

export HAL0_WORKSPACE_PATH HAL0_WORKSPACE_SLUG HAL0_WORKSPACE_NAME

export HAL0_PORT="${HAL0_PORT:-$(hal0_derive_port "${HAL0_WORKSPACE_PATH}|api" \
    "${HAL0_API_PORT_RANGE_START}" "${HAL0_API_PORT_RANGE_END}")}"
export UI_PORT="${UI_PORT:-$(hal0_derive_port "${HAL0_WORKSPACE_PATH}|ui" \
    "${HAL0_UI_PORT_RANGE_START}" "${HAL0_UI_PORT_RANGE_END}")}"
export HAL0_OPENWEBUI_PORT="${HAL0_OPENWEBUI_PORT:-$(hal0_derive_port "${HAL0_WORKSPACE_PATH}|owu" \
    "${HAL0_OWU_PORT_RANGE_START}" "${HAL0_OWU_PORT_RANGE_END}")}"

# One container per workspace — the shared "hal0-openwebui-dev" name meant the
# second workspace's `docker stop` killed the first workspace's OpenWebUI.
export HAL0_OWU_CONTAINER="${HAL0_OWU_CONTAINER:-hal0-owu-${HAL0_WORKSPACE_SLUG}}"

# Data root stays inside the worktree (gitignored), so teardown is a directory
# removal and nothing leaks into system paths.
export HAL0_HOME="${HAL0_HOME:-${HAL0_WORKSPACE_PATH}/hal0-home}"

# vite.config.ts reads VITE_API_TARGET from process.env — .env files do NOT
# reach a Vite config file, so this has to be exported in the shell that runs
# `npm run dev`, not written to ui/.env.
export VITE_API_TARGET="${VITE_API_TARGET:-http://127.0.0.1:${HAL0_PORT}}"
