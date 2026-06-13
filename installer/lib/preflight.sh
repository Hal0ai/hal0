#!/usr/bin/env bash
# installer/lib/preflight.sh — re-runnable pre-flight checks.
#
# Sourceable: install.sh dot-sources this to do its inline preflight.
# Executable: `bash installer/lib/preflight.sh` runs preflight_all and
#             exits with the aggregate status. `hal0 doctor` shells
#             this in executable mode.
#
# Public API (all functions return 0 on success, non-zero on failure;
# none of them exit the calling shell):
#   preflight_systemd          — systemctl on PATH
#   preflight_python           — `${PY:-python3}` resolvable + version 3.11–3.14
#   preflight_docker           — `docker info` reachable. Soft by default
#                                (returns 0 with a warning, since the API
#                                can run without Docker until a slot is
#                                launched and `hal0 doctor` should report
#                                the full picture rather than abort). Set
#                                HAL0_DOCKER_REQUIRED=1 to flip it hard:
#                                on apt-based hosts the function then
#                                auto-installs docker.io + enables the
#                                docker.service; on rpm/pacman/etc. it
#                                emits the exact one-liner to run and
#                                returns non-zero. install.sh sets the
#                                flag so a fresh box doesn't end up with
#                                hal0-openwebui.service restart-looping
#                                with status=203/EXEC.
#   preflight_disk MIN_GB DIR  — at least MIN_GB free in DIR (default 20 / /var/lib)
#   preflight_ports P1 [P2…]   — none of the named TCP ports are LISTENing
#   preflight_all              — run all of the above; aggregate non-zero
#
# Globals honoured
#   HAL0_PY            — python interpreter (default python3)
#   HAL0_DISK_MIN_GB   — preflight_disk threshold (default 20)
#   HAL0_DISK_TARGET   — preflight_disk target directory (default /var/lib;
#                        falls back to /tmp if /var/lib is absent — useful
#                        when running `hal0 doctor` on a fresh container)
#   HAL0_DOCTOR_PORTS  — space-separated port list for preflight_ports
#                        (default "8080 3001")
#   HAL0_DOCKER_REQUIRED — when "1", preflight_docker auto-installs
#                        docker.io on apt hosts and hard-fails (returns
#                        non-zero) elsewhere. Default empty → soft mode
#                        for `hal0 doctor`.

# shellcheck shell=bash

set -o pipefail

# Source ui.sh for `info`/`warn`/`err`/`die` if they aren't already
# defined.  When install.sh sources us, it has already sourced ui.sh and
# `info` is in scope; the guard prevents a second source call.
if ! declare -F info >/dev/null 2>&1; then
    # shellcheck source=ui.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ui.sh"
fi

# Distro / package-manager detection for the install hints below. Guarded
# (the helper no-ops on a second source), so this is safe whether install.sh
# already sourced it or `hal0 doctor` runs preflight.sh standalone.
if ! declare -F pkg_install_cmd >/dev/null 2>&1; then
    # shellcheck source=distro.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/distro.sh"
fi

# ── individual checks ───────────────────────────────────────────────────────

preflight_systemd() {
    if command -v systemctl >/dev/null 2>&1; then
        info "systemd: $(systemctl --version 2>/dev/null | head -n1 || echo present)"
        return 0
    fi
    err "systemd not found — hal0 v1 requires systemctl on PATH"
    return 1
}

preflight_python() {
    local py="${HAL0_PY:-${HAL0_PYTHON:-python3}}"
    if ! command -v "${py}" >/dev/null 2>&1; then
        err "python interpreter '${py}' not found"
        warn "  install with: $(python_venv_hint)  (or set HAL0_PYTHON=...)"
        return 1
    fi
    local ver
    if ! ver="$("${py}" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))' 2>/dev/null)"; then
        err "could not read Python version from ${py}"
        return 1
    fi
    if [[ "${ver}" =~ ^3\.(11|12|13|14)$ ]]; then
        info "python: ${py} (${ver})"
        return 0
    fi
    warn "python: ${py} (${ver}) — hal0 is tested on 3.11-3.14"
    return 1
}

# CPU architecture — hal0 ships x86_64-only binaries (FastFlowLM .deb,
# toolbox container images). On ARM the install gets deep into apt
# before failing cryptically, so refuse up front.
preflight_arch() {
    local m
    m="$(uname -m 2>/dev/null || echo unknown)"
    if [[ "${m}" == "x86_64" || "${m}" == "amd64" ]]; then
        info "arch: ${m}"
        return 0
    fi
    err "unsupported architecture '${m}' — hal0 requires x86_64 (FLM/toolbox images are amd64-only)"
    return 1
}

# `python3 -m venv` needs the `ensurepip` + `venv` stdlib modules, which
# Debian/Ubuntu split into the `python3-venv` package. Without it the
# venv step fails with an opaque "ensurepip is not available".
preflight_venv() {
    local py="${HAL0_PY:-${HAL0_PYTHON:-python3}}"
    if "${py}" -c 'import ensurepip, venv' >/dev/null 2>&1; then
        info "python venv: available"
        return 0
    fi
    err "'${py} -m venv' is unavailable (missing ensurepip/venv)"
    warn "  install the venv stdlib, e.g.: $(python_venv_hint)"
    return 1
}

# The install writes to several system trees; if any is read-only (overlay
# LXC, SELinux-strict, /usr mounted ro) the install explodes halfway. Probe
# writability of each parent up front. Pass dirs as args; defaults cover the
# system-mode layout. Runs after the sudo re-exec, so we expect to be root.
# shellcheck disable=SC2120  # called with args from install.sh, argless (defaults) from preflight_all
preflight_writable() {
    local rc=0 d parent
    local dirs=("$@")
    if [[ ${#dirs[@]} -eq 0 ]]; then
        dirs=(/opt /usr/lib /etc/hal0 /etc/systemd/system /var/lib /usr/local/bin)
    fi
    for d in "${dirs[@]}"; do
        parent="${d}"
        while [[ -n "${parent}" && ! -e "${parent}" ]]; do parent="$(dirname "${parent}")"; done
        if [[ -w "${parent}" ]]; then
            continue
        fi
        err "not writable: ${parent} (needed to create ${d})"
        rc=1
    done
    [[ "${rc}" -eq 0 ]] && info "writable paths: ok"
    return "${rc}"
}

# Single up-front connectivity probe so a network/proxy problem surfaces
# once with an actionable message instead of as N separate download
# failures later. curl honours http(s)_proxy/no_proxy automatically. Soft:
# warns (returns 0) so offline-from-local-tarball installs aren't blocked.
preflight_network() {
    local url="${HAL0_NET_PROBE_URL:-https://github.com}"
    if curl -fsS -m 8 -I "${url}" >/dev/null 2>&1; then
        info "network: reachable (${url})"
    else
        warn "network: could not reach ${url} — check connectivity/proxy (http_proxy/https_proxy)"
        warn "  downloads (release, FLM, models, container images) will fail if this host is offline"
    fi
    return 0
}

preflight_docker() {
    # Fast path: docker is on PATH and the daemon answers. Same as before.
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        info "docker: $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
        return 0
    fi

    # Docker is missing or the daemon is unreachable. Two modes:
    #
    #   HAL0_DOCKER_REQUIRED=1 (set by install.sh) — try to fix it on apt
    #     hosts, hard-fail with remediation elsewhere. hal0-openwebui
    #     and the slot containers genuinely need docker; letting the
    #     installer finish "successfully" only to have systemd restart-
    #     loop the unit with status=203/EXEC is worse than refusing to
    #     proceed (real fallout: LXC 105 burned ~191 restarts before a
    #     human noticed).
    #
    #   Unset (default, e.g. `hal0 doctor`) — keep the legacy soft
    #     behaviour: warn and return 0 so the rest of the report runs.
    if [[ "${HAL0_DOCKER_REQUIRED:-0}" != "1" ]]; then
        warn "docker is not available — slot launches will fail until it is installed"
        return 0
    fi

    # ── required mode ───────────────────────────────────────────────────
    # If the binary is there but the daemon isn't reachable, we don't
    # try to "fix" it by reinstalling — surfacing a clear "daemon down"
    # message is more honest than an apt-get that won't help.
    if command -v docker >/dev/null 2>&1; then
        err "docker binary present but 'docker info' failed — is the daemon running?"
        warn "  start it with: systemctl enable --now docker"
        return 1
    fi

    # Apt-based host → auto-install docker.io. The package name is
    # deliberate: Debian/Ubuntu ship the upstream-maintained docker.io
    # in main, which is what our docs already point operators at. We
    # don't add Docker Inc.'s third-party repo here — it's a heavier
    # change that an operator can opt into manually.
    if command -v apt-get >/dev/null 2>&1; then
        info "installing docker.io (required for OpenWebUI + ComfyUI containers)"
        # -q to keep apt's per-line progress chatter out of the spinner-
        # less preflight phase; -y for non-interactive. We don't pipe to
        # /dev/null — if the install fails the operator needs to see why.
        if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io; then
            err "apt-get install docker.io failed — see output above"
            return 1
        fi
        # Enable + start the daemon so the very next step (which may be
        # `docker pull` for OpenWebUI) actually has something to talk to.
        if command -v systemctl >/dev/null 2>&1; then
            systemctl enable --now docker >/dev/null 2>&1 || \
                warn "could not 'systemctl enable --now docker' — start it manually if the daemon isn't running"
        fi
        # Re-probe. If the daemon still isn't reachable (e.g. inside an
        # unprivileged container) we surface that rather than press on.
        if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
            info "docker: $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
            return 0
        fi
        err "docker installed but daemon still unreachable — check 'systemctl status docker' and 'journalctl -u docker -n 40'"
        return 1
    fi

    # Non-apt host: emit the right one-liner for the detected package
    # manager (via lib/distro.sh), then hard-fail. Operator runs it, reruns
    # install.sh. Alpine uses OpenRC, not systemd, so its enable step differs.
    err "docker not installed — hal0 requires docker for OpenWebUI + slot containers"
    local docker_install
    if docker_install="$(pkg_install_cmd docker)"; then
        if [[ "$(pkg_mgr)" == "apk" ]]; then
            warn "  install with: ${docker_install} && sudo rc-update add docker default && sudo service docker start"
        else
            warn "  install with: ${docker_install} && sudo systemctl enable --now docker"
        fi
    else
        warn "  no recognised package manager — install docker from https://docs.docker.com/engine/install/ then re-run install.sh"
    fi
    return 1
}

preflight_disk() {
    local min_gb="${1:-${HAL0_DISK_MIN_GB:-20}}"
    local target="${2:-${HAL0_DISK_TARGET:-/var/lib}}"
    # The installer calls us with the *eventual* target (e.g.
    # /var/lib/hal0), which doesn't exist yet on a fresh host. df only
    # works on extant paths, so walk up to the deepest existing
    # ancestor before measuring. This still measures the right device
    # because /var/lib/hal0 will land on /var/lib's filesystem.
    local probe="${target}"
    while [[ -n "${probe}" && ! -d "${probe}" ]]; do
        local parent
        parent="$(dirname "${probe}")"
        [[ "${parent}" == "${probe}" ]] && break   # hit / and still missing
        probe="${parent}"
    done
    if [[ ! -d "${probe}" ]]; then
        warn "disk: could not find an existing ancestor of ${target} to probe; skipping"
        return 1
    fi
    target="${probe}"
    local avail_kb
    avail_kb="$(df -Pk "${target}" 2>/dev/null | awk 'NR==2 {print $4}')"
    if [[ -z "${avail_kb}" ]]; then
        warn "disk: could not read free space on ${target} (df failed)"
        return 1
    fi
    local avail_gb=$(( avail_kb / 1024 / 1024 ))
    if (( avail_gb >= min_gb )); then
        info "disk: ${avail_gb} GB free on ${target} (need ${min_gb})"
        return 0
    fi
    err "disk: only ${avail_gb} GB free on ${target}; need at least ${min_gb} GB"
    return 1
}

# Detect a TCP listener on a port without requiring lsof / netstat:
# prefer `ss`, fall back to /proc/net/tcp{,6} for the static-binary case.
_preflight_port_in_use() {
    local port="$1" hex
    if command -v ss >/dev/null 2>&1; then
        ss -ltn "sport = :${port}" 2>/dev/null | awk 'NR>1 {found=1} END {exit !found}'
        return $?
    fi
    if [[ -r /proc/net/tcp ]]; then
        printf -v hex '%04X' "${port}"
        # State 0A == LISTEN
        awk -v hex=":${hex}" '$2 ~ hex"$" && $4 == "0A" {found=1} END {exit !found}' \
            /proc/net/tcp /proc/net/tcp6 2>/dev/null
        return $?
    fi
    # No tool to check — assume not in use; surface a warning so the
    # operator knows the check was skipped.
    warn "port ${port}: cannot probe (no ss, no /proc/net/tcp); skipping"
    return 1
}

preflight_ports() {
    local ports=("$@")
    if (( ${#ports[@]} == 0 )); then
        # Default to the API + OpenWebUI ports the installer binds to.
        # Caller can pass an explicit list to widen this.
        local default_ports="${HAL0_DOCTOR_PORTS:-8080 3001}"
        # shellcheck disable=SC2206  # intentional word-split on the env var
        ports=( ${default_ports} )
    fi
    local rc=0 port
    for port in "${ports[@]}"; do
        if _preflight_port_in_use "${port}"; then
            err "port ${port}: already in use (find with 'ss -ltnp \"sport = :${port}\"')"
            rc=1
        else
            info "port ${port}: free"
        fi
    done
    return "${rc}"
}

# ── aggregate runner ────────────────────────────────────────────────────────

# Run every check; return non-zero if any failed. We deliberately don't
# short-circuit — operators expect `hal0 doctor` to surface the full
# picture, not the first failure.
preflight_all() {
    local rc=0
    preflight_arch    || rc=$?
    preflight_systemd || rc=$?
    preflight_python  || rc=$?
    preflight_venv    || rc=$?
    preflight_writable || rc=$?
    preflight_network || rc=$?
    preflight_docker  || rc=$?
    preflight_disk    || rc=$?
    preflight_ports   || rc=$?
    if (( rc == 0 )); then
        info "all pre-flight checks passed"
    else
        err "one or more pre-flight checks failed (see above)"
    fi
    return "${rc}"
}

# ── executable entry point ──────────────────────────────────────────────────
# Only fires when this file is invoked directly (e.g.
# `bash installer/lib/preflight.sh` or via `hal0 doctor`). When sourced
# from install.sh, BASH_SOURCE[0] != $0 and we no-op so the caller can
# pick which checks to run.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    preflight_all
    exit $?
fi
