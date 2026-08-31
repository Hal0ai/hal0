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
#   preflight_python           — `${PY:-python3}` resolvable + version 3.12–3.14
#                                (matches pyproject.toml's requires-python
#                                floor). resolve_main_python (below) can
#                                resolve/auto-install a compatible interpreter
#                                when the default is below the floor.
#   preflight_container_runtime — a usable container runtime (podman
#                                preferred, docker accepted). Soft by
#                                default (returns 0 with a warning, since
#                                the API/dashboard come up without a runtime
#                                and `hal0 doctor` should report the full
#                                picture rather than abort). Set
#                                HAL0_CONTAINER_REQUIRED=1 to flip it hard:
#                                the function then auto-installs podman via
#                                the detected package manager (lib/distro.sh)
#                                and hard-fails with the exact one-liner if
#                                that's not possible. install.sh sets the
#                                flag so a fresh box never finishes with every
#                                slot dead ("no container runtime found").
#                                `preflight_docker` is a back-compat alias.
#   preflight_git              — git on PATH (needed for Hermes agent
#                                provisioning's `pip install git+...`). Soft
#                                by default (warns + returns 1). Set
#                                HAL0_GIT_REQUIRED=1 to auto-install it via
#                                the detected package manager, hard-failing
#                                with the exact one-liner if that's not
#                                possible; install.sh sets the flag right
#                                before the Hermes provisioning step (#1726).
#   preflight_gpu              — GPU/NPU device nodes visible + group wiring
#                                sane. Soft by default (always returns 0;
#                                CPU-only is a valid install) but prints the
#                                exact Proxmox LXC dev0/gid fix when devices
#                                are missing or mis-mapped inside a container.
#                                Set HAL0_GPU_GATE=1 to flip it into the
#                                install-time GATE: same detection + messages,
#                                but the return code classifies the platform so
#                                install.sh can smart-block a broken passthrough
#                                (HAL0_GPU_RC_BROKEN_GID / HAL0_GPU_RC_NO_DEVICE)
#                                instead of installing "successfully" and then
#                                silently running CPU-only.
#   preflight_node             — node on PATH + version >= NODE_MIN_MAJOR
#                                (default 20). Soft, always returns 0 — a
#                                Node-less box is a valid install; the
#                                dashboard UI build just isn't available
#                                until Node is installed (install.sh
#                                auto-provisions it).
#   preflight_podman_network_backend — advisory: when podman>=6 is present,
#                                warn if netavark/aardvark-dns are still v1
#                                (podman 6 requires v2). Never fails.
#   preflight_disk MIN_GB DIR  — at least MIN_GB free in DIR (default 20 / /var/lib)
#   preflight_ports P1 [P2…]   — none of the named TCP ports are LISTENing
#                                (soft — informational only — when
#                                HAL0_DOCTOR_PORTS_SOFT=1)
#   preflight_bootstrap_prereqs — Linux host + curl/tar/sha256sum on PATH,
#                                mirroring bootstrap.sh's own preflight() so
#                                the direct `sudo bash install.sh` path
#                                enforces the same floor as the curl|bash
#                                one-liner (#1098)
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
#   HAL0_DOCTOR_PORTS_SOFT — when "1", preflight_ports treats "already in
#                        use" as informational (warn, doesn't flip the
#                        aggregate rc) instead of a hard failure. `hal0
#                        doctor` sets this: post-install, 8080/3001 being
#                        bound almost always means hal0's OWN hal0-api /
#                        hal0-openwebui units are up and healthy, not a
#                        collision. install.sh's pre-install gate never
#                        sets it — but even there, a port held by hal0's
#                        OWN hal0-api/hal0-openwebui unit (the documented
#                        `sudo bash install.sh` re-install-over-a-live-box
#                        path, #F24) is auto-detected via
#                        _preflight_port_is_own_service and treated as OK;
#                        a FOREIGN process holding the port still
#                        hard-fails unconditionally.
#   HAL0_CONTAINER_REQUIRED — when "1", preflight_container_runtime
#                        auto-installs podman (via the detected package
#                        manager) and hard-fails (returns non-zero) when it
#                        can't. Default empty → soft mode for `hal0 doctor`.
#                        Legacy HAL0_DOCKER_REQUIRED is still honoured.
#   HAL0_GIT_REQUIRED  — when "1", preflight_git auto-installs git (via the
#                        detected package manager) and returns non-zero when
#                        it can't. Default empty → soft mode for `hal0
#                        doctor` (warn + return 1). install.sh sets the flag
#                        immediately before Hermes provisioning (#1726).
#   HAL0_GPU_GATE      — when "1", preflight_gpu returns a classification code
#                        (see HAL0_GPU_RC_*) instead of always 0, so install.sh
#                        can smart-block a broken LXC passthrough. Default 0 →
#                        soft/advisory mode for `hal0 doctor`.

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
    if [[ "${ver}" =~ ^3\.(12|13|14)$ ]]; then
        info "python: ${py} (${ver})"
        return 0
    fi
    warn "python: ${py} (${ver}) — hal0 requires >=3.12 (pyproject.toml requires-python; tested on 3.12-3.14)"
    return 1
}

# ── Main hal0 venv interpreter selection ────────────────────────────────────
# pyproject.toml pins `requires-python = ">=3.12"`. Stock Debian 12 (python3
# = 3.11) and Ubuntu 22.04 (python3 = 3.10) both fail preflight_python's
# floor check above; historically install.sh treated that as a non-fatal
# warning ("pip may still work") and only hard-failed minutes later at
# `pip install`, with a recovery hint (HAL0_PYTHON=python3.12) that's a dead
# end on those distros' base repos. resolve_main_python finds — or, when
# asked, installs — a floor-compatible interpreter up front, mirroring
# resolve_hindsight_python's pattern for the (separate) Hindsight venv.
MAIN_PY_MIN_MINOR=12

# Best-effort: install python3.MAIN_PY_MIN_MINOR via the detected package
# manager. Echoes the resolved interpreter name on success; returns 1
# otherwise (nothing installed / no recognised manager / distro doesn't
# package a pinned minor). Only fires when HAL0_PY_AUTOINSTALL=1 (install.sh
# sets it) so `hal0 doctor` and read-only preflight never mutate the system.
_main_py_autoinstall() {
    [[ "${HAL0_PY_AUTOINSTALL:-0}" == "1" ]] || return 1
    pkg_mgr >/dev/null 2>&1 || return 1
    local fam cand; fam="$(distro_family)"
    cand="python3.${MAIN_PY_MIN_MINOR}"
    info "no Python >=3.${MAIN_PY_MIN_MINOR} found — attempting to install ${cand} (${fam})"
    case "${fam}" in
        debian)
            DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 update -qq >/dev/null 2>&1 || true
            DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y -q "${cand}" "${cand}-venv" >/dev/null 2>&1 || return 1 ;;
        fedora) "$(pkg_mgr)" install -y "${cand}" >/dev/null 2>&1 || return 1 ;;
        # Arch/openSUSE/Alpine ship a single rolling python; a pinned older
        # minor isn't reliably in the base repos (mirrors
        # _hindsight_py_autoinstall's same limitation).
        *) return 1 ;;
    esac
    if command -v "${cand}" >/dev/null 2>&1; then
        info "installed ${cand} for the main hal0 venv"
        printf '%s\n' "${cand}"
        return 0
    fi
    return 1
}

# Resolve the interpreter the MAIN hal0 venv should be built with. Selection
# order:
#   1. the default ${HAL0_PY:-${HAL0_PYTHON:-python3}} when already >=3.12
#   2. python3.14 → python3.12 already on PATH
#   3. auto-install python3.12 (only when HAL0_PY_AUTOINSTALL=1)
# Echoes the resolved interpreter name and returns 0, or returns 1 if none
# could be resolved. Unlike resolve_hindsight_python there is no
# metadata-gate bypass to fall back on here — `pip install "${REPO_ROOT}"`
# hard-requires >=3.12 — so callers must die on a 1 return.
resolve_main_python() {
    local def="${HAL0_PY:-${HAL0_PYTHON:-python3}}"
    if command -v "${def}" >/dev/null 2>&1; then
        local m; m="$(_py_minor "${def}" 2>/dev/null)" || m=""
        if [[ -n "${m}" ]] && (( m >= MAIN_PY_MIN_MINOR )); then
            printf '%s\n' "${def}"
            return 0
        fi
    fi
    local v cand
    for v in 14 13 12; do
        cand="python3.${v}"
        if command -v "${cand}" >/dev/null 2>&1; then
            printf '%s\n' "${cand}"
            return 0
        fi
    done
    _main_py_autoinstall
}

# ── Hindsight interpreter selection ─────────────────────────────────────────
# The Hindsight memory engine runs in its OWN venv (${VAR_DIR}/memory/hindsight)
# and pulls litellm, which publishes `requires-python >=3.10,<3.14`. The main
# hal0 venv is happy on 3.14, but litellm's *metadata* gate makes
# `pip install hindsight-api` fail with a wall of "Could not find a version"
# resolver noise on a 3.14 host before the install can fall back. We resolve a
# compatible interpreter (3.11-3.13) up front — auto-installing one when asked —
# so the Hindsight venv builds clean; only when none exists do we fall back to
# the default interpreter + --ignore-requires-python (litellm actually runs fine
# on 3.14 — the classifier was dropped over a since-resolved fastuuid wheel gap,
# BerriAI/litellm#26343).
#
# Range kept as constants so bumping the supported band is a one-line change.
HINDSIGHT_PY_MIN_MINOR=11
HINDSIGHT_PY_MAX_MINOR=13

# Echo the 3.x minor (e.g. "14") of an interpreter; nothing + non-zero on error.
_py_minor() { "${1}" -c 'import sys; print(sys.version_info[1])' 2>/dev/null; }

# Is $1 an interpreter whose (3.x) minor is inside the Hindsight-supported band?
_py_hindsight_ok() {
    local m; m="$(_py_minor "${1}")" || return 1
    [[ -n "${m}" ]] || return 1
    (( m >= HINDSIGHT_PY_MIN_MINOR && m <= HINDSIGHT_PY_MAX_MINOR ))
}

# Best-effort: install a Hindsight-compatible Python (3.13→3.11) via the
# detected package manager and set HINDSIGHT_PY to it. Returns 0 on success,
# 1 otherwise (nothing installed / not resolvable). Only fires when
# HAL0_HINDSIGHT_AUTOINSTALL=1 (install.sh sets it) so `hal0 doctor` and
# read-only preflight never mutate the system. Never fatal — the caller falls
# back to --ignore-requires-python.
_hindsight_py_autoinstall() {
    [[ "${HAL0_HINDSIGHT_AUTOINSTALL:-0}" == "1" ]] || return 1
    pkg_mgr >/dev/null 2>&1 || return 1
    local fam v cand; fam="$(distro_family)"
    info "no Python 3.${HINDSIGHT_PY_MIN_MINOR}-3.${HINDSIGHT_PY_MAX_MINOR} found for the Hindsight venv — attempting to install one (${fam})"
    for v in 13 12 11; do
        cand="python3.${v}"
        case "${fam}" in
            debian)
                DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 update -qq >/dev/null 2>&1 || true
                DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y -q "${cand}" "${cand}-venv" >/dev/null 2>&1 || continue ;;
            fedora) "$(pkg_mgr)" install -y "${cand}" >/dev/null 2>&1 || continue ;;
            # Arch/openSUSE/Alpine ship a single rolling python; a pinned older
            # minor isn't reliably in the base repos. Skip — the metadata-gate
            # bypass covers these.
            *) return 1 ;;
        esac
        if command -v "${cand}" >/dev/null 2>&1 && _py_hindsight_ok "${cand}"; then
            info "installed ${cand} for the Hindsight memory engine"
            HINDSIGHT_PY="${cand}"
            return 0
        fi
    done
    return 1
}

# Resolve the interpreter the Hindsight venv should be built with into the
# globals HINDSIGHT_PY and HINDSIGHT_PY_FALLBACK (1 = the chosen interpreter is
# OUTSIDE litellm's supported band, so the caller must pass
# --ignore-requires-python). Selection order:
#   1. HAL0_HINDSIGHT_PYTHON override (honoured verbatim)
#   2. the default ${PY} when it's already in-band (the common clean case)
#   3. python3.13 → python3.11 already on PATH
#   4. auto-install one (only when HAL0_HINDSIGHT_AUTOINSTALL=1)
#   5. give up → default ${PY} with the metadata gate bypassed
# Read-only unless step 4 fires; always returns 0 (it never blocks the install).
resolve_hindsight_python() {
    local def="${HAL0_PY:-${HAL0_PYTHON:-python3}}"
    HINDSIGHT_PY=""
    HINDSIGHT_PY_FALLBACK=0

    if [[ -n "${HAL0_HINDSIGHT_PYTHON:-}" ]]; then
        HINDSIGHT_PY="${HAL0_HINDSIGHT_PYTHON}"
        if _py_hindsight_ok "${HINDSIGHT_PY}"; then
            info "hindsight python: ${HINDSIGHT_PY} (HAL0_HINDSIGHT_PYTHON)"
        else
            HINDSIGHT_PY_FALLBACK=1
            warn "hindsight python: ${HINDSIGHT_PY} is outside 3.${HINDSIGHT_PY_MIN_MINOR}-3.${HINDSIGHT_PY_MAX_MINOR}; will bypass litellm's requires-python gate"
        fi
        return 0
    fi

    if _py_hindsight_ok "${def}"; then
        HINDSIGHT_PY="${def}"
        info "hindsight python: ${def} ($("${def}" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null))"
        return 0
    fi

    local v cand
    for v in 13 12 11; do
        cand="python3.${v}"
        if command -v "${cand}" >/dev/null 2>&1 && _py_hindsight_ok "${cand}"; then
            HINDSIGHT_PY="${cand}"
            info "hindsight python: ${cand} (default ${def} is out of litellm's 3.10-3.13 range)"
            return 0
        fi
    done

    if _hindsight_py_autoinstall; then
        return 0
    fi

    HINDSIGHT_PY="${def}"
    HINDSIGHT_PY_FALLBACK=1
    warn "hindsight python: no Python 3.${HINDSIGHT_PY_MIN_MINOR}-3.${HINDSIGHT_PY_MAX_MINOR} available — will build the memory-engine venv on ${def} and bypass litellm's requires-python gate"
    warn "  for a clean install: $(pkg_install_cmd python3.12 python3.12-venv 2>/dev/null || echo 'install python3.12') and re-run, or set HAL0_HINDSIGHT_PYTHON=python3.12"
    return 0
}

# Read-only wrapper for preflight_all / `hal0 doctor`: report the Hindsight
# interpreter decision without mutating the system (auto-install suppressed).
# Always returns 0 — the metadata-gate bypass means a 3.14 host is not a
# failure, just a heads-up.
preflight_hindsight_python() {
    if [[ "${HAL0_SKIP_HINDSIGHT:-0}" == "1" ]]; then
        info "hindsight memory engine: skipped (HAL0_SKIP_HINDSIGHT=1)"
        return 0
    fi
    HAL0_HINDSIGHT_AUTOINSTALL=0 resolve_hindsight_python
    return 0
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

    # Missing venv/ensurepip. Debian/Ubuntu ship the venv stdlib as the
    # separate python3-venv package (the classic clean-Ubuntu trap), so a
    # `python3` that's present still can't `-m venv`. Two modes, mirroring
    # preflight_container_runtime:
    #
    #   HAL0_VENV_REQUIRED=1 (set by install.sh) — auto-install the venv
    #     stdlib via the detected package manager, hard-fail if that doesn't
    #     resolve it. The install ALWAYS builds a venv, so dying with a hint
    #     and making the operator hand-install + re-run is pure friction.
    #
    #   Unset (default, e.g. `hal0 doctor`) — soft: warn + return 1 so a
    #     read-only report finishes without mutating the system.
    if [[ "${HAL0_VENV_REQUIRED:-0}" != "1" ]]; then
        err "'${py} -m venv' is unavailable (missing ensurepip/venv)"
        warn "  install the venv stdlib, e.g.: $(python_venv_hint)"
        return 1
    fi

    local pm
    if pm="$(pkg_mgr)"; then
        info "installing the python venv stdlib (required to build hal0's venv)"
        local ok=1
        case "$(distro_family)" in
            debian)
                DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 update -qq || true
                DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y -q python3-venv python3-pip || ok=0
                ;;
            fedora) "${pm}" install -y python3 python3-pip || ok=0 ;;
            suse) zypper install -y python3 python3-pip || ok=0 ;;
            arch) pacman -S --noconfirm python python-pip || ok=0 ;;
            alpine) apk add python3 py3-pip || ok=0 ;;
            *) ok=0 ;;
        esac
        if [[ "${ok}" -eq 1 ]] && "${py}" -c 'import ensurepip, venv' >/dev/null 2>&1; then
            info "python venv: available"
            return 0
        fi
        err "python venv stdlib install did not resolve '${py} -m venv' — see output above"
        warn "  install manually: $(python_venv_hint)"
        return 1
    fi

    err "'${py} -m venv' is unavailable and no package manager was detected"
    warn "  install the venv stdlib manually: $(python_venv_hint)"
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

# Resolve a usable container runtime. Mirrors providers/container.py's
# _container_runtime(), which prefers /usr/bin/podman over /usr/bin/docker —
# podman is daemonless + rootless-capable and is what the hal0 slot stack
# standardises on. `<rt> info` is the functional probe (docker: daemon up;
# podman: storage/conf usable). Echoes the runtime name; non-zero on failure.
_resolve_container_runtime() {
    if command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
        echo podman
        return 0
    fi
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        echo docker
        return 0
    fi
    return 1
}

# `<rt> info`/`podman pull` (the probes above) can succeed in an unprivileged
# Proxmox/LXC container WITHOUT `features: nesting=1` — podman reports itself
# usable, but every actual `<rt> run` fails on cgroup/mount-namespace setup it
# can't do without nesting. That's the false-OK this closes: pre-flight
# passes, install reports success, then every hal0-slot@ inference slot,
# hal0-openwebui, and ComfyUI die at runtime with cgroup/mount errors the
# operator never saw at install time. Bounded with
# `timeout` so an offline host fails fast instead of hanging the install;
# HAL0_CONTAINER_SMOKE_IMAGE overrides the pulled image for air-gapped/
# mirrored registries.
# Captures the smoke test's combined output for _container_runtime_gate to
# diagnose on failure (module-global by design — bash has no return-by-ref;
# reset on every call so a stale message never survives into a later check).
_HAL0_CONTAINER_SMOKE_OUTPUT=""
_container_run_smoke_test() {
    local rt="$1" image="${HAL0_CONTAINER_SMOKE_IMAGE:-quay.io/podman/hello}"
    # Run the image with its OWN entrypoint — do NOT override the command with
    # `true`. The default smoke image (quay.io/podman/hello) is distroless and
    # ships only its greeter binary: `run <image> true` fails with exec 127
    # ("executable file `true` not found") even when the runtime is perfectly
    # healthy, which false-failed the REQUIRED container gate on every host and
    # aborted the install. The hello image self-exits 0; that is the smoke.
    _HAL0_CONTAINER_SMOKE_OUTPUT="$(timeout 30 "${rt}" run --rm "${image}" 2>&1)"
}

# AppArmor profile-load failure signature (#1563): on a privileged Ubuntu
# 24.04 LXC whose Proxmox host has `lxc.apparmor.profile: unconfined` set,
# podman cannot load its default AppArmor profile and `<rt> run` dies with
# `apparmor_parser: ... Access denied` / "install profile containers-default
# apparmor: exit status 243". Matched from the smoke failure text itself
# (never OS/LXC sniffing) so it's never confused with the keyring-quota or
# generic LXC-nesting failures below.
_is_apparmor_profile_load_failure() {
    local blob="$1"
    printf '%s\n' "${blob}" | grep -qi 'apparmor' || return 1
    printf '%s\n' "${blob}" \
        | grep -qiE 'install profile containers-default|apparmor_parser.*access denied|exit status 243'
}

# Shared AppArmor remediation — invokes the SAME convergent fix
# install.sh's post-install re-check runs
# (src/hal0/agents/containers_apparmor.py: detect → write
# `[containers] apparmor_profile = "unconfined"` → retry, idempotent). Run as
# a bare script (`"${py}" "<path>/containers_apparmor.py"`), NOT `-m
# hal0.agents.containers_apparmor` — this point in the installer is BEFORE the
# venv exists / hal0 is pip-installed, so importing the `hal0.agents` package
# (heavy transitive deps) would fail; the bare-script form only needs stdlib
# (the module makes its one third-party import, structlog, optional for
# exactly this reason).
#
# The script's own internal retry smoke honours HAL0_CONTAINER_SMOKE_IMAGE
# (same var `_container_run_smoke_test` above honours) so an air-gapped/
# mirrored-registry install probes with an image it can actually reach
# instead of a hardcoded quay.io one — otherwise the helper would misclassify
# an unreachable-image pull failure as "unrelated" and skip the fix even on a
# genuinely apparmor-broken host. Its exit code is still informational only
# (belt-and-suspenders against any other detection drift between the two
# layers); the real verdict is OUR own `_container_run_smoke_test` re-run
# below, against the exact same configured image.
_apparmor_unconfined_remediate() {
    local rt="$1"
    # install.sh always sets REPO_ROOT before this runs. The public
    # `HAL0_CONTAINER_REQUIRED=1 bash installer/lib/preflight.sh` standalone
    # mode does not — fall back to this file's own location
    # (installer/lib/preflight.sh → repo root is two dirs up) so that path
    # can remediate too instead of always reporting the helper missing.
    local repo_root="${REPO_ROOT:-}"
    if [[ -z "${repo_root}" ]]; then
        repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    fi
    local script="${repo_root}/src/hal0/agents/containers_apparmor.py"
    local py="${PY:-python3}"
    if [[ ! -f "${script}" ]] || ! command -v "${py}" >/dev/null 2>&1; then
        warn "  can't locate ${script} (or ${py}) to run the apparmor remediation"
        return 1
    fi
    # HAL0_CONTAINER_SMOKE_IMAGE, if set in the environment, is inherited by
    # this subprocess automatically — the script reads the same var name.
    local out
    out="$("${py}" "${script}" 2>&1)" || true
    printf '%s\n' "${out}" | sed 's/^/  /'
    _container_run_smoke_test "${rt}"
}

# Run the real `<rt> run` smoke test and print an accurate remedy on failure.
# Only called in REQUIRED mode (install.sh) — `hal0 doctor` stays fast and
# side-effect-free (no image pull) in the default soft mode.
#
# The failure branch used to assume every `<rt> run` failure inside an LXC was
# a missing `nesting`/`keyctl` feature flag. That's wrong when the container
# HAS nesting+keyctl but crun still can't create a session keyring because the
# kernel keyring byte-quota (kernel.keys.maxbytes) is exhausted — observed
# live as `crun: create keyring '…': Disk quota exceeded` (exit 126) with
# nesting=1,keyctl=1 already set. The nesting/keyctl message is actively
# misleading there (operator "fixes" a config that was never broken). Detect
# that signature first and give the real remedy; fall through to the generic
# LXC hint for every other failure shape so nothing else gets mis-masked.
#
# #1563: an AppArmor profile-load failure gets a THIRD branch, ahead of the
# generic LXC fallback (which otherwise mis-masked it with a misleading
# nesting/keyctl hint even when those flags were already set) — remediate
# and re-probe BEFORE giving up, since the fix for this exact signature
# already exists (containers_apparmor.py) but used to only run long after
# this gate had already hard-died.
_container_runtime_gate() {
    local rt="$1"
    if _container_run_smoke_test "${rt}"; then
        return 0
    fi
    if _is_apparmor_profile_load_failure "${_HAL0_CONTAINER_SMOKE_OUTPUT}"; then
        warn "  ${rt} run: ${_HAL0_CONTAINER_SMOKE_OUTPUT}"
        warn "  AppArmor profile-load failure detected — common on a privileged Ubuntu 24.04 LXC whose Proxmox"
        warn "  host has 'lxc.apparmor.profile: unconfined' set. Attempting the automated unconfined-profile fix..."
        if _apparmor_unconfined_remediate "${rt}"; then
            info "  apparmor remediation applied — ${rt} run now succeeds, continuing install"
            return 0
        fi
        err "${rt} info/version succeeded but '${rt} run' failed — the runtime can't actually launch a container"
        # NOTE: this branch only fires on an LXC that ALREADY has
        # 'lxc.apparmor.profile: unconfined' on the Proxmox host (that's the
        # detection signature above) — telling the operator to set it again
        # would be circular and fix nothing. The remaining knobs are inside
        # THIS container/guest, not the host.
        warn "  automated AppArmor remediation (wrote apparmor_profile = \"unconfined\" to"
        warn "  /etc/containers/containers.conf and retried) did NOT resolve it — inspect that file for a syntax"
        warn "  error or conflicting override, confirm 'apparmor_parser --version' works inside THIS container"
        warn "  (a missing/broken apparmor_parser here can't load ANY profile, confined or not), and check"
        warn "  'podman info --debug' for the runtime's own apparmor_profile setting"
        return 1
    fi
    err "${rt} info/version succeeded but '${rt} run' failed — the runtime can't actually launch a container"
    if printf '%s\n' "${_HAL0_CONTAINER_SMOKE_OUTPUT}" \
        | grep -qiE 'create keyring.*(disk quota exceeded|quota exceeded)|keyring.*edquot'; then
        warn "  ${rt} run: ${_HAL0_CONTAINER_SMOKE_OUTPUT}"
        warn "  kernel keyring quota exhausted (crun could not create a session keyring) — this is NOT a missing"
        warn "  nesting/keyctl config. Check: cat /proc/key-users (look for a uid near its byte quota)"
        warn "  remedy: reboot this container to clear leaked session keyrings, or as root: keyctl clear @s /"
        warn "  find + kill the leaked keyring holders, or raise the quota: sysctl kernel.keys.maxbytes (and"
        warn "  kernel.keys.maxkeys) higher on the PROXMOX HOST, then re-run install.sh"
    elif grep -qa 'container=lxc' /proc/1/environ 2>/dev/null; then
        warn "  inside an unprivileged Proxmox/LXC container this needs 'features: nesting=1' (and often keyctl=1)"
        warn "  set it in /etc/pve/lxc/<CTID>.conf on the PROXMOX HOST, then: pct stop <CTID> && pct start <CTID>"
    else
        warn "  inspect the error above (cgroup/mount-namespace setup, subuid/newuidmap, or a daemon issue)"
    fi
    return 1
}

preflight_container_runtime() {
    local required="${HAL0_CONTAINER_REQUIRED:-${HAL0_DOCKER_REQUIRED:-0}}"

    # Fast path: a runtime is already installed and working per `<rt> info`.
    local rt
    if rt="$(_resolve_container_runtime)"; then
        if [[ "${rt}" == podman ]]; then
            info "podman: $(podman version --format '{{.Version}}' 2>/dev/null || echo unknown)"
        else
            info "docker: $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
        fi
        if [[ "${required}" == "1" ]]; then
            _container_runtime_gate "${rt}" || return 1
        fi
        return 0
    fi

    # No usable runtime. Two modes:
    #
    #   HAL0_CONTAINER_REQUIRED=1 (set by install.sh; legacy HAL0_DOCKER_REQUIRED
    #     honoured) — install podman, hard-fail with remediation if that's not
    #     possible. Every inference slot runs in a container; letting the install
    #     finish "successfully" only to have every slot sit in `error` with
    #     "no container runtime found" is worse than refusing to proceed.
    #
    #   Unset (default, e.g. `hal0 doctor`) — soft: warn and return 0 so the
    #     rest of the report runs.
    if [[ "${HAL0_CONTAINER_REQUIRED:-${HAL0_DOCKER_REQUIRED:-0}}" != "1" ]]; then
        warn "no container runtime (podman/docker) — slot launches will fail until one is installed"
        return 0
    fi

    # ── required mode ───────────────────────────────────────────────────
    # A binary present but failing its probe is a real config problem (podman
    # storage/subuid, or docker daemon down). Reinstalling won't help — surface
    # it rather than churn the package manager.
    if command -v podman >/dev/null 2>&1; then
        err "podman present but 'podman info' failed — inspect 'podman info' output"
        warn "  (in an unprivileged container this often needs newuidmap/subuid setup)"
        return 1
    fi
    if command -v docker >/dev/null 2>&1; then
        err "docker binary present but 'docker info' failed — is the daemon running?"
        warn "  start it with: systemctl enable --now docker"
        return 1
    fi

    # Auto-install podman via the detected package manager (lib/distro.sh).
    # podman is daemonless, so unlike docker there is nothing to enable.
    local pm
    if pm="$(pkg_mgr)"; then
        info "installing podman (required for hal0 inference slots)"
        local ok=1
        case "${pm}" in
            apt-get) DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y -q podman || ok=0 ;;
            dnf) dnf install -y podman || ok=0 ;;
            yum) yum install -y podman || ok=0 ;;
            zypper) zypper install -y podman || ok=0 ;;
            pacman) pacman -S --noconfirm podman || ok=0 ;;
            apk) apk add podman || ok=0 ;;
            *) ok=0 ;;
        esac
        if [[ "${ok}" -eq 1 ]] && rt="$(_resolve_container_runtime)" && [[ "${rt}" == podman ]]; then
            info "podman: $(podman version --format '{{.Version}}' 2>/dev/null || echo unknown)"
            _container_runtime_gate "${rt}" || return 1
            return 0
        fi
        err "podman install/initialisation failed — see output above; check 'podman info'"
        return 1
    fi

    # No recognised package manager → hard-fail with the best hint available.
    err "no container runtime — hal0 requires podman (or docker) for inference slots"
    local podman_install
    if podman_install="$(pkg_install_cmd podman)"; then
        warn "  install with: ${podman_install}"
    else
        warn "  install podman from https://podman.io/docs/installation then re-run install.sh"
    fi
    return 1
}

# Back-compat alias: older docs / `hal0 doctor` builds call preflight_docker.
preflight_docker() { preflight_container_runtime "$@"; }

# git — Hermes agent provisioning runs `pip install
# git+https://github.com/NousResearch/hermes-agent.git@<rev>`, which pip
# implements by shelling out to `git clone`. A stock minimal distro image
# (fresh Ubuntu 24.04 LXC/cloud template, no git preinstalled) doesn't ship
# it, so that pip install dies with "ERROR: Cannot find command 'git'" deep
# inside the Hermes toolchain step (#1726) unless something checks for it
# first. Two modes, mirroring preflight_container_runtime/preflight_venv:
#
#   HAL0_GIT_REQUIRED=1 (install.sh sets this immediately before the Hermes
#     provisioning step) — auto-install git via the detected package
#     manager; return non-zero with the exact remediation one-liner if that
#     doesn't resolve it. The caller decides what to do with a failure here
#     (install.sh treats it the same as any other Hermes provisioning
#     failure — non-fatal to the overall install, per #1584's established
#     graceful-degradation pattern — but the message is specific to git
#     instead of being buried in pip's clone-failure noise).
#
#   Unset (default, e.g. `hal0 doctor` / preflight_all) — soft: warn +
#     return 1 so a read-only report finishes without mutating the system.
#
# Codex review (#1727): `command -v git` only proves a `git` file resolves
# on PATH, not that it runs — a non-executable file (permissions drift, a
# half-finished package install) still resolves via `command -v` and would
# have reported success right up until pip's own `git clone` failed with
# the original error. `git --version` is the actual functional probe: it
# both resolves the binary AND proves the OS will exec it.
_git_usable() {
    git --version >/dev/null 2>&1
}

preflight_git() {
    if _git_usable; then
        info "git: $(git --version 2>/dev/null | head -n1 || echo present)"
        return 0
    fi

    if [[ "${HAL0_GIT_REQUIRED:-0}" != "1" ]]; then
        warn "git not found or not runnable — Hermes agent provisioning (pip install git+...) will fail until it's installed"
        return 1
    fi

    local pm
    if pm="$(pkg_mgr)"; then
        info "installing git (required for Hermes agent provisioning)"
        local ok=1
        case "${pm}" in
            apt-get) DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y -q git || ok=0 ;;
            dnf) dnf install -y git || ok=0 ;;
            yum) yum install -y git || ok=0 ;;
            zypper) zypper install -y git || ok=0 ;;
            pacman) pacman -S --noconfirm git || ok=0 ;;
            apk) apk add git || ok=0 ;;
            *) ok=0 ;;
        esac
        if [[ "${ok}" -eq 1 ]] && _git_usable; then
            info "git: $(git --version 2>/dev/null | head -n1 || echo present)"
            return 0
        fi
        err "git install failed — see output above"
        return 1
    fi

    err "git not found and no recognised package manager"
    local git_install
    if git_install="$(pkg_install_cmd git)"; then
        warn "  install with: ${git_install}"
    else
        warn "  install git via your distro's package manager then re-run install.sh"
    fi
    return 1
}

# Extract the leading major-version integer from a version string, e.g.
# "podman version 6.0.0" / "netavark 2.1.0" / "6.0.0" → "6". Echoes nothing
# (non-zero) when no MAJOR.MINOR token is present.
_semver_major() {
    # Grab the first NN.NN(.NN) token anywhere in the input, then its major.
    local ver
    ver="$(grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' <<<"$1" | head -n1)"
    [[ -n "${ver}" ]] || return 1
    echo "${ver%%.*}"
}

# Locate a podman network-backend helper (netavark / aardvark-dns) and echo
# its major version. Asks podman first (authoritative — it uses the binary it
# will actually exec), then falls back to the binary on PATH / common libexec
# dirs. Non-zero when the tool can't be found or versioned.
_network_helper_major() {
    local tool="$1" path ver
    # podman info exposes the resolved netavark path (>=4.7); aardvark sits
    # beside it. Prefer this so we version the binary podman actually runs.
    if [[ "${tool}" == netavark ]]; then
        path="$(podman info --format '{{.Host.NetworkBackendInfo.Path}}' 2>/dev/null)"
    fi
    if [[ -z "${path}" || ! -x "${path}" ]]; then
        path="$(command -v "${tool}" 2>/dev/null)"
    fi
    if [[ -z "${path}" || ! -x "${path}" ]]; then
        local d
        for d in /usr/lib/podman /usr/libexec/podman /usr/local/lib/podman; do
            if [[ -x "${d}/${tool}" ]]; then path="${d}/${tool}"; break; fi
        done
    fi
    [[ -n "${path}" && -x "${path}" ]] || return 1
    ver="$("${path}" --version 2>/dev/null)" || return 1
    _semver_major "${ver}"
}

# Podman 6 modernized networking and REQUIRES netavark + aardvark-dns v2.
# hal0's bridge slots (GPU llama / FLM / Kokoro / Qwen3-TTS use the default
# bridge + loopback publish; ComfyUI/llama_server use --network=host) depend
# on the network backend, so a podman-6 host still carrying netavark/aardvark
# v1 fails to route/resolve on bridge slots. We install from distro repos
# (never pin podman ourselves), so a coherent stack is the packager's job —
# this check is purely advisory: it warns on a mismatch and never fails, matching
# preflight_podman_forward. It no-ops on podman <6 and on docker-only hosts.
preflight_podman_network_backend() {
    command -v podman >/dev/null 2>&1 || return 0
    local pv major
    pv="$(podman version --format '{{.Version}}' 2>/dev/null)" || return 0
    major="$(_semver_major "${pv}")" || return 0
    (( major >= 6 )) || return 0   # v4/v5 pair with netavark v1 — nothing to assert.

    local nv av mismatch=0
    if nv="$(_network_helper_major netavark)"; then
        if (( nv < 2 )); then
            warn "podman ${pv} needs netavark v2 but found v${nv}.x — bridge slots may fail to route"
            mismatch=1
        fi
    else
        warn "podman ${pv}: could not determine netavark version (expected v2 for this podman)"
    fi
    if av="$(_network_helper_major aardvark-dns)"; then
        if (( av < 2 )); then
            warn "podman ${pv} needs aardvark-dns v2 but found v${av}.x — bridge-slot DNS may fail"
            mismatch=1
        fi
    fi
    if (( mismatch == 1 )); then
        warn "  fix: upgrade netavark + aardvark-dns from your distro repos to match podman 6"
    elif [[ -n "${nv}" ]]; then
        info "podman network backend: netavark v${nv}${av:+, aardvark-dns v${av}} (podman ${pv})"
    fi
    return 0
}

# Docker + podman coexistence: Docker sets the iptables FORWARD policy to DROP
# and manages that chain. netavark (podman's firewall) adds no explicit
# NEW-connection ACCEPT for published ports (it assumes FORWARD defaults to
# ACCEPT), so once Docker is installed alongside podman, packets to a
# podman-published port (e.g. OpenWebUI's 0.0.0.0:3001) are reachable from the
# host itself but DROPped for every other host — a reverse proxy or LAN client
# can't connect. The hal0-podman-forward.service unit closes the gap by adding
# an ACCEPT for the podman0 bridge into DOCKER-USER; install.sh enables it when
# docker is present. This check is purely advisory — it never fails (returns 0)
# and only runs its checks when BOTH runtimes are present (the conflict source).
# It also needs root + iptables; under an unprivileged `hal0 doctor` the probes
# quietly no-op.
preflight_podman_forward() {
    command -v podman >/dev/null 2>&1 || return 0
    command -v docker >/dev/null 2>&1 || return 0
    command -v iptables >/dev/null 2>&1 || return 0
    # No podman bridge yet (no containers created) — nothing to reconcile.
    ip link show podman0 >/dev/null 2>&1 || return 0
    # Already reconciled? (unit ran, or FORWARD isn't DROP) — all good.
    if iptables -C DOCKER-USER -o podman0 -j ACCEPT 2>/dev/null; then
        info "podman/Docker FORWARD: podman0 accepted in DOCKER-USER"
        return 0
    fi
    if iptables -S FORWARD 2>/dev/null | grep -q -- '-P FORWARD DROP'; then
        warn "podman/Docker coexistence: FORWARD is DROP and DOCKER-USER lacks a podman0 ACCEPT"
        warn "  → podman-published ports (e.g. OpenWebUI :3001) are unreachable off-host"
        warn "  fix: systemctl enable --now hal0-podman-forward   (or re-run install.sh)"
    fi
    return 0
}

# GPU / NPU device visibility.
#
# Two modes, selected by HAL0_GPU_GATE:
#   unset / 0 (default — `hal0 doctor`, preflight_all): SOFT. Always returns 0;
#     prints device visibility + the Proxmox LXC dev0/gid remedy as advisories
#     so the full report never aborts.
#   1 (install.sh Stage-1 gate, #1104): GATED. Identical detection + messages,
#     but the return code classifies the platform so install.sh can smart-block
#     the single most common broken-install shape — a Proxmox LXC with the GPU
#     forwarded but the render-node gid mis-mapped, which otherwise installs
#     "successfully" and then silently runs every slot CPU-only:
#       0                         → GPU present + wired, or a genuine bare-metal
#                                    CPU box: proceed.
#       HAL0_GPU_RC_BROKEN_GID(3) → render device visible but its gid maps to
#                                    NO group, or to a group OTHER than the
#                                    render group, inside this LXC (dev0
#                                    miswire): caller HARD STOPS with the
#                                    printed remedy.
#       HAL0_GPU_RC_NO_DEVICE(4)  → no GPU devices inside an LXC: caller allows
#                                    an EXPLICIT CPU-only opt-in.
# The gid check requires the NAME match the render group specifically, not
# merely resolve to *some* group — a bare "maps to a group" check false-passed
# a gid/name collision (renderD128 gid landing on an unrelated system group
# like 'clock' instead of 'render') because getent still resolved a name.
# Test seams (only consulted here, never set in production): HAL0_GPU_DRI_GLOB,
# HAL0_GPU_CONTAINER_OVERRIDE, HAL0_GPU_RENDER_GID_OVERRIDE,
# HAL0_GPU_RENDER_GROUP_OVERRIDE, HAL0_GPU_USER_OVERRIDE, HAL0_GPU_KFD_PATH,
# HAL0_GPU_AMD_OVERRIDE.
# See docs/getting-started/proxmox.mdx for the full walkthrough.
HAL0_GPU_RC_BROKEN_GID=3
HAL0_GPU_RC_NO_DEVICE=4
#       HAL0_GPU_RC_NO_KFD(5)     → an AMD GPU render node is visible but
#                                    /dev/kfd is NOT: the ROCm compute node is
#                                    missing, so llama.cpp silently falls back
#                                    to the runner image's Vulkan backend,
#                                    which emits INVALID TOKENS for every
#                                    model while every health surface reads
#                                    green (#1888). Caller allows an explicit
#                                    CPU-only opt-in, same as NO_DEVICE.
HAL0_GPU_RC_NO_KFD=5
#       HAL0_GPU_RC_KFD_GID(6)    → /dev/kfd IS forwarded, but its owning gid
#                                    differs from the render node's, so the
#                                    hal0 user cannot open it even though the
#                                    rootful slot containers can (#1953). A
#                                    plain LXC dev passthrough produces exactly
#                                    this: renderD128 lands root:render while
#                                    the compute node lands root:root. Fixable
#                                    IN PLACE — never tell the operator to
#                                    re-forward a device that is already there.
HAL0_GPU_RC_KFD_GID=6
preflight_gpu() {
    local gate="${HAL0_GPU_GATE:-0}"

    local in_container=""
    if [[ -f /run/systemd/container ]] || grep -qa 'container=lxc' /proc/1/environ 2>/dev/null; then
        in_container="lxc"
    fi
    # Test seam: force the container classification (unit tests can't fake
    # /proc/1/environ). Never set in production.
    [[ -n "${HAL0_GPU_CONTAINER_OVERRIDE:-}" ]] && in_container="${HAL0_GPU_CONTAINER_OVERRIDE}"

    local dri_glob="${HAL0_GPU_DRI_GLOB:-/dev/dri/renderD*}"
    local kfd_path="${HAL0_GPU_KFD_PATH:-/dev/kfd}"
    local have_render="" have_kfd="" have_accel=""
    compgen -G "${dri_glob}" >/dev/null 2>&1 && have_render=1
    [[ -e "${kfd_path}" ]] && have_kfd=1
    [[ -e /dev/accel/accel0 ]] && have_accel=1

    if [[ -n "${have_render}" ]]; then
        info "gpu: $(compgen -G "${dri_glob}" 2>/dev/null | tr '\n' ' ')present"
    fi
    [[ -n "${have_kfd}"   ]] && info "gpu: ${kfd_path} present (ROCm compute)"
    [[ -n "${have_accel}" ]] && info "npu: /dev/accel/accel0 present (AMD XDNA)"

    if [[ -z "${have_render}" ]]; then
        if [[ "${in_container}" == "lxc" ]]; then
            warn "gpu: no /dev/dri/renderD* inside this container — GPU slots will run CPU-only"
            warn "  Forward the devices from the Proxmox HOST (/etc/pve/lxc/<CTID>.conf, PVE 8.2+):"
            warn "    dev0: /dev/dri/renderD128,gid=<render gid INSIDE this container>"
            warn "    dev1: /dev/kfd                    # ROCm compute — REQUIRED for GPU LLM slots (#1888)"
            warn "    dev2: /dev/accel/accel0,gid=<render gid>   # XDNA NPU (Strix Halo only)"
            warn "  then: pct stop <CTID> && pct start <CTID>. Full guide: https://hal0.dev/docs/getting-started/proxmox/"
            # Gated install: no devices in an LXC is the opt-in CPU-only case.
            [[ "${gate}" == "1" ]] && return "${HAL0_GPU_RC_NO_DEVICE}"
        else
            warn "gpu: no /dev/dri/renderD* — no GPU driver bound (CPU-only install?)"
            warn "  AMD: check 'lspci -nnk' shows 'Kernel driver in use: amdgpu'; very new"
            warn "  silicon (Strix Halo) needs kernel >= 6.14 + current firmware."
            # Bare-metal CPU box → proceed silently even when gated.
        fi
        return 0
    fi

    # Node group wiring: the render node's gid must map to the ACTUAL render
    # group (not merely *some* named group) that hal0/containers are granted
    # access through. In a Proxmox LXC a dev0 entry with the HOST's gid can
    # either leave the node owned by an unmapped gid inside the container, or
    # — the false-pass this closes — collide with an unrelated system group
    # (e.g. gid 993 landing on 'clock' instead of 'render' because the host's
    # and container's gid spaces disagree). getent still resolves a name in
    # that case, so checking only "maps to *a* group" reports success while
    # GPU access is actually denied. HAL0_GPU_RENDER_GROUP_OVERRIDE lets tests
    # target a group name guaranteed to exist without a real GPU/render host.
    local node gid grpname want_group
    node="$(compgen -G "${dri_glob}" 2>/dev/null | head -1)"
    want_group="${HAL0_GPU_RENDER_GROUP_OVERRIDE:-render}"
    if [[ -n "${node}" ]]; then
        gid="${HAL0_GPU_RENDER_GID_OVERRIDE:-$(stat -c '%g' "${node}" 2>/dev/null)}"
        grpname="$(getent group "${gid}" 2>/dev/null | cut -d: -f1)"
        if [[ -z "${grpname}" ]]; then
            warn "gpu: ${node} is owned by gid ${gid}, which maps to NO group in this container"
            if [[ "${in_container}" == "lxc" ]]; then
                local want_gid
                want_gid="$(getent group "${want_group}" 2>/dev/null | cut -d: -f3)"
                warn "  Fix on the Proxmox host: dev0: ${node},gid=${want_gid:-<${want_group} gid>}"
                warn "  (gid= must be the group id INSIDE the container, not the host's)"
                # Gated install: devices present but a mis-mapped gid → silent
                # CPU-only fallback. This is the #1 broken-install shape.
                [[ "${gate}" == "1" ]] && return "${HAL0_GPU_RC_BROKEN_GID}"
            fi
        elif [[ "${grpname}" != "${want_group}" ]]; then
            # M3: the gid resolves to a REAL group, just not the render one —
            # a gid/name collision. hal0/containers are only ever granted
            # device access via '${want_group}', so this gid grants none
            # despite looking like a pass.
            warn "gpu: ${node} is owned by gid ${gid}, which maps to group '${grpname}' — NOT '${want_group}'"
            warn "  hal0/containers are only granted GPU access via the '${want_group}' group; this gid grants none"
            if [[ "${in_container}" == "lxc" ]]; then
                local want_gid
                want_gid="$(getent group "${want_group}" 2>/dev/null | cut -d: -f3)"
                warn "  Fix on the Proxmox host: dev0: ${node},gid=${want_gid:-<${want_group} gid>}"
                warn "  (gid= must be the '${want_group}' group's id INSIDE the container, not the host's)"
                # Gated install: same broken-install shape as the no-group
                # case above — a wrong group is just as silently CPU-only.
                [[ "${gate}" == "1" ]] && return "${HAL0_GPU_RC_BROKEN_GID}"
            fi
        else
            info "gpu: ${node} → group ${grpname} (gid ${gid})"
            # A correct group name is necessary but not sufficient — the hal0
            # service user must actually be a member, or device access still
            # fails with Permission denied at runtime. Skipped when the user
            # doesn't exist yet: install.sh runs this gate BEFORE creating the
            # hal0 system user and adding it to '${want_group}' (see "System
            # user" step), so its absence here is expected on a fresh install,
            # not a fault. An EXISTING hal0 missing from the group is a real,
            # warnable gap (advisory only — usermod during install is
            # idempotent and self-heals it moments later).
            local gpu_user="${HAL0_GPU_USER_OVERRIDE:-hal0}"
            if id "${gpu_user}" >/dev/null 2>&1; then
                if id -nG "${gpu_user}" 2>/dev/null | tr ' ' '\n' | grep -qx "${want_group}"; then
                    info "gpu: ${gpu_user} is a member of ${want_group}"
                else
                    warn "gpu: ${gpu_user} exists but is NOT a member of '${want_group}' — GPU device access will be denied"
                    warn "  fix: usermod -aG ${want_group} ${gpu_user}   (then restart hal0 services)"
                fi
            fi
        fi
    fi

    # ── AMD GPU lane availability (#1888 / #1948) ────────────────────────
    # No /dev/kfd means no ROCm lane. Whether that is fatal depends entirely
    # on whether the OTHER AMD GPU lane is real on this install.
    #
    # Under #1923 it never was: the pinned runner was one HIP+Vulkan build
    # whose Vulkan backend emitted invalid tokens for every model, at full
    # nominal speed, while HTTP 200, container health, `hal0 doctor` and the
    # SSE done frame all read green — so a kfd-less AMD box had no lane that
    # produced language, and the gate refused the install outright.
    #
    # #1948 fixed the image. A runner whose Vulkan backend is validated serves
    # this box correctly on the render node alone — proven on the very box the
    # defect was found on — so refusing here would now be refusing a working
    # configuration. The gate therefore asks the same question the slot-load
    # guard and the three device-derivation ladders ask, via the shell mirror
    # above, and only refuses when the answer is no.
    #
    # AMD-only: the defect is characterised on the AMD/HIP build, and a
    # non-AMD GPU has no /dev/kfd to forward in the first place.
    local is_amd="${HAL0_GPU_AMD_OVERRIDE:-}"
    if [[ -z "${is_amd}" ]]; then
        [[ -d /sys/module/amdgpu ]] && is_amd=1
    fi
    if [[ -n "${is_amd}" && "${is_amd}" != "0" && -z "${have_kfd}" ]]; then
        if [[ -n "${have_render}" ]] && _hal0_vulkan_lane_serves_default_image; then
            # Vulkan is this box's GPU lane. Not a warning about a broken
            # install — a description of a supported one.
            info "gpu: no /dev/kfd (no ROCm compute node) — LLM slots will use the Vulkan lane"
            info "  This install's runner image is validated for that lane (#1948), and every"
            info "  slot must still pass the output-sanity readiness probe before serving (#1922)."
            if [[ "${in_container}" == "lxc" ]]; then
                info "  To ALSO enable the ROCm lane, forward it from the Proxmox HOST"
                info "  (/etc/pve/lxc/<CTID>.conf):  dev1: /dev/kfd   then pct stop/start."
            fi
        else
            warn "gpu: AMD GPU visible but /dev/kfd is MISSING — no ROCm compute node"
            if [[ -z "${have_render}" ]]; then
                warn "  and no render node either, so there is no Vulkan lane to fall back to."
            else
                warn "  and this install's runner image is not validated for the Vulkan lane,"
                warn "  whose backend emits INVALID TOKENS for every model on the ade07ba"
                warn "  lineage while all health checks read green (#1888)."
            fi
            warn "  Every GPU LLM slot would therefore refuse to start."
            if [[ "${in_container}" == "lxc" ]]; then
                warn "  Forward it from the Proxmox HOST (/etc/pve/lxc/<CTID>.conf):"
                warn "    dev1: /dev/kfd"
                warn "  then: pct stop <CTID> && pct start <CTID>"
            else
                warn "  Load the amdkfd driver (it ships with amdgpu; check 'dmesg | grep -i kfd')."
            fi
            [[ "${gate}" == "1" ]] && return "${HAL0_GPU_RC_NO_KFD}"
        fi
    fi

    # ── ROCm compute-node group wiring (#1953) ───────────────────────────
    # The node above exists — but a plain LXC `dev` passthrough hands it over
    # as root:root 0660 while /dev/dri/renderD128 lands root:render 0660. The
    # rootful slot containers open it regardless, so ROCm genuinely WORKS; the
    # hal0 service user does not, so every hal0-user GPU probe reports the box
    # unusable and #1923's guard then refuses every AMD GPU slot.
    #
    # The target gid comes from the RENDER NODE, never from `getent group
    # render`: the kernel gates on the integer and the name for that integer is
    # not portable (a halo143-class box has renderD128 owned by a gid whose
    # /etc/group name is 'clock', while 'render' resolves to a different,
    # useless gid). Same authority resolve_gpu_group_ids() already follows for
    # --group-add, so the two devices cannot disagree.
    if [[ -n "${have_kfd}" && -n "${node}" ]]; then
        local kfd_path kfd_gid render_gid
        kfd_path="${HAL0_GPU_KFD_PATH:-/dev/kfd}"
        # Both gids come from the real nodes. Deliberately NOT
        # HAL0_GPU_RENDER_GID_OVERRIDE: that override fakes the group-NAME
        # mapping check above, and reusing it here would compare a claimed gid
        # against a real one. HAL0_GPU_KFD_GID_OVERRIDE is this check's own
        # seam, so divergence can be forced without needing two real groups.
        kfd_gid="${HAL0_GPU_KFD_GID_OVERRIDE:-$(stat -c '%g' "${kfd_path}" 2>/dev/null || true)}"
        render_gid="$(stat -c '%g' "${node}" 2>/dev/null || true)"
        # Gate on the INACCESSIBLE shape, not on gid inequality. A differing gid
        # is not itself a fault: a valid box can put /dev/kfd on `video` and the
        # render nodes on `render`, and install.sh adds hal0 to BOTH; world bits
        # or an ACL can grant access independently too. Rewriting those to the
        # render group would remove access from video-only users and, on an
        # unprivileged LXC, abort a fresh install over a working config.
        #
        # The shape that actually breaks is the plain-passthrough default:
        # root-owned with no group the service user can ever be in (gid 0) and
        # no world access. Only that is repaired.
        local kfd_mode kfd_world_rw=0
        kfd_mode="$(stat -c '%a' "${kfd_path}" 2>/dev/null || echo 000)"
        case "${kfd_mode}" in *[67]) kfd_world_rw=1 ;; esac
        if [[ -n "${kfd_gid}" && -n "${render_gid}" \
            && "${kfd_gid}" != "${render_gid}" \
            && "${kfd_gid}" == "0" \
            && "${kfd_world_rw}" == "0" ]]; then
            warn "gpu: ${kfd_path} is root-owned (gid ${kfd_gid}, mode ${kfd_mode}) while ${node} uses gid ${render_gid}"
            warn "  The compute node is forwarded and the rootful slot containers can use it,"
            warn "  but the hal0 service user cannot — so GPU slots get refused on a working box (#1953)."
            warn "  Fix IN PLACE (no re-forward, no reboot):"
            warn "    chgrp ${render_gid} ${kfd_path} && chmod 0660 ${kfd_path}"
            warn "  Unprivileged LXC (chgrp returns EPERM): set it on the Proxmox host instead:"
            warn "    dev1: ${kfd_path},gid=${render_gid}   (the gid INSIDE the container)"
            [[ "${gate}" == "1" ]] && return "${HAL0_GPU_RC_KFD_GID}"
        fi
    fi
    return 0
}

# -- Vulkan-lane image gate (shell mirror of providers/_gpu, #1948) ---------
# Answers the SAME question `default_image_serves_vulkan_lane()` answers in
# Python: can this install's default runner image serve the Vulkan LLM lane?
#
# Why a mirror and not a call: preflight_gpu runs in Stage 1, BEFORE hal0 is
# pip-installed, so `python3 -c "from hal0.config.schema import ..."` is not
# available yet -- but the source tree it is about to install IS on disk next
# to this script. So the two literals are read straight out of schema.py.
#
# CONSERVATIVE BY CONSTRUCTION, and blind in three specific ways. It reads the
# two literals out of schema.py, so it sees neither the HAL0_TOOLBOX_IMAGE_*
# env overrides nor the release manifest's digest pin that
# resolve_runner_image() consults ahead of them, and it has no notion of the
# runner IDENTITY that render_node_present() now evaluates (#1981) -- it only
# asks whether a render node exists at all. Every one of those blindnesses
# errs the same way: toward answering "no" or toward accepting a box the
# load-time guard will re-examine anyway with better information. A false "no"
# costs an operator a CPU-only install they could have avoided; a false "yes"
# would ship a box that serves invalid tokens (#1888), and the real gate still
# stands behind this one. That asymmetry is why the mirror is allowed to be
# this crude rather than growing a uid model in bash -- the least testable
# layer in the system is the wrong place to re-derive logic that already has a
# tested implementation twenty seconds later in the install.
#
# It recognises only the single-member shape of VULKAN_CAPABLE_IMAGE_REFS (a
# default equal to VULKAN_FIXED_IMAGE) and answers "no" to everything else,
# including anything it cannot parse. tests/installer/test_preflight_gpu_gate.py
# pins agreement with the Python predicate, and trips if the capable set ever
# grows past one member without this mirror being taught about it.
#
# Test seams: HAL0_GPU_VULKAN_LANE_OVERRIDE (1/0, decides outright),
# HAL0_SCHEMA_PY_OVERRIDE (path to the schema.py to read).
_hal0_vulkan_lane_serves_default_image() {
    local override="${HAL0_GPU_VULKAN_LANE_OVERRIDE:-}"
    if [[ -n "${override}" ]]; then
        [[ "${override}" != "0" ]]
        return
    fi

    local schema="${HAL0_SCHEMA_PY_OVERRIDE:-}"
    if [[ -z "${schema}" ]]; then
        local here
        here="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || return 1
        schema="${here}/../../src/hal0/config/schema.py"
    fi
    [[ -r "${schema}" ]] || return 1

    local default_ref fixed_ref
    default_ref="$(sed -n 's/^DEFAULT_ROCMFPX_IMAGE = "\(.*\)"$/\1/p' "${schema}" | head -n1)"
    fixed_ref="$(sed -n 's/^VULKAN_FIXED_IMAGE = "\(.*\)"$/\1/p' "${schema}" | head -n1)"
    [[ -n "${default_ref}" && -n "${fixed_ref}" && "${default_ref}" == "${fixed_ref}" ]]
}

# ── Bootstrap-prereq parity (#1098) ────────────────────────────────────────
# bootstrap.sh (the curl|bash one-liner) hard-requires a Linux host plus
# curl/tar/sha256sum in its own preflight() before it ever fetches the
# release tarball. install.sh leans on all three later in its own run
# (preflight_network's curl probe, the rsync-fallback tar copy, the FLM
# .deb sha256 check) but a direct `sudo bash install.sh` — no bootstrap in
# front — never checked for them up front: a minimal host missing one
# would sail past "Pre-flight checks" and die deep in the run with a bare
# "command not found" instead of an actionable message. This mirrors
# bootstrap.sh's preflight() (same checks, same die-style message) so both
# entry points enforce the same floor. python3 is deliberately NOT
# re-checked here — preflight_python (below) already does a stricter,
# version-aware check with a better error message; duplicating a bare
# `command -v python3` here would just produce a redundant, less useful
# failure.
preflight_bootstrap_prereqs() {
    local rc=0
    if [[ "$(uname -s)" != "Linux" ]]; then
        err "hal0 only supports Linux right now (got $(uname -s))"
        rc=1
    fi
    local dep
    for dep in curl tar sha256sum; do
        if ! command -v "${dep}" >/dev/null 2>&1; then
            err "missing dependency: ${dep} — install it and re-run"
            rc=1
        fi
    done
    [[ "${rc}" -eq 0 ]] && info "bootstrap prereqs: curl, tar, sha256sum present (Linux)"
    return "${rc}"
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

# The PID of the process LISTENing on ``port``, or empty when it can't be
# determined (no `ss`, unreadable, or nothing listening). Requires `ss -p`,
# which needs root (or the socket's owning uid) to resolve — install.sh
# always runs as root, so this is reliable there; `hal0 doctor` may run
# unprivileged, in which case this silently yields nothing and callers fall
# back to the generic "already in use" message.
_preflight_port_owner_pid() {
    local port="$1"
    command -v ss >/dev/null 2>&1 || return 1
    ss -ltnp "sport = :${port}" 2>/dev/null \
        | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2
}

# True when the process holding ``port`` is the MainPID of one of hal0's own
# systemd units (hal0-api.service, hal0-openwebui.service) — i.e. re-running
# the installer over a live, healthy hal0 rather than colliding with an
# unrelated process (#F24). Never hard-fails the caller: on any ambiguity
# (no systemctl, no PID, no match) this returns non-zero so preflight_ports
# keeps its existing hard-fail-on-foreign-holder behaviour.
_preflight_port_is_own_service() {
    local port="$1" pid unit mainpid
    command -v systemctl >/dev/null 2>&1 || return 1
    pid="$(_preflight_port_owner_pid "${port}")"
    [[ -n "${pid}" ]] || return 1
    for unit in hal0-api.service hal0-openwebui.service; do
        mainpid="$(systemctl show -p MainPID --value "${unit}" 2>/dev/null)"
        if [[ -n "${mainpid}" && "${mainpid}" != "0" && "${mainpid}" == "${pid}" ]]; then
            return 0
        fi
    done
    # cgroup fallback — containerised / port-forwarded services. OpenWebUI on
    # :3001 runs as a podman container under hal0-openwebui.service; the LISTEN
    # socket is held by conmon (a child), NOT the unit MainPID, so the check
    # above misses it and a healthy re-install used to hard-fail on :3001.
    # systemd places the container's processes under the owning unit's cgroup
    # slice, so resolve the holder's cgroup to a hal0 unit instead.
    if [[ -r "/proc/${pid}/cgroup" ]]; then
        case "$(cat "/proc/${pid}/cgroup" 2>/dev/null)" in
            *hal0-api.service*|*hal0-openwebui.service*|*hal0-slot@*|*hal0-agent@*)
                return 0 ;;
        esac
    fi
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
            if [[ "${HAL0_DOCTOR_PORTS_SOFT:-0}" == "1" ]]; then
                warn "port ${port}: already in use (expected if hal0's own services are running; find the owner with 'ss -ltnp \"sport = :${port}\"')"
            elif _preflight_port_is_own_service "${port}"; then
                info "port ${port}: in use by hal0's own service — OK for a re-install"
            else
                err "port ${port}: already in use (find with 'ss -ltnp \"sport = :${port}\"')"
                rc=1
            fi
        else
            info "port ${port}: free"
        fi
    done
    return "${rc}"
}

# Node.js / npm — needed for the dashboard UI build (Vite 6 / Tailwind v4;
# ui/package.json has no `engines` floor to surface a mismatch). Soft:
# always returns 0 (a Node-less box is a valid install — the dashboard build
# just isn't available until Node is installed). This is the read-only
# `hal0 doctor` view; install.sh's own "Node.js toolchain" step actually
# provisions Node when it's missing/too old.
NODE_MIN_MAJOR=20
preflight_node() {
    if ! command -v node >/dev/null 2>&1; then
        warn "node: not found — dashboard UI build needs Node ${NODE_MIN_MAJOR}+ LTS"
        return 0
    fi
    local ver major
    ver="$(node -v 2>/dev/null || true)"
    major=0
    [[ "${ver}" =~ ^v([0-9]+) ]] && major="${BASH_REMATCH[1]}"
    if (( major >= NODE_MIN_MAJOR )); then
        info "node: ${ver}"
    else
        warn "node: ${ver} — below the ${NODE_MIN_MAJOR}+ LTS floor (dashboard build may fail)"
    fi
    return 0
}

# ── Node.js provisioning ────────────────────────────────────────────────────
# Node/npm is a HARD dependency for the dashboard Vite build (see the
# "Dashboard UI" step in install.sh) — the pi-coder + opencode bundled
# agents used to need it too (installer/agents/*.sh both shelled out to
# npm) but those speculative drivers were deleted; hal0 v0.3 only ever
# ships hermes, which doesn't need Node. install.sh used to only WARN on a
# missing/old npm; this resolves — or, when asked, auto-installs — a
# Node >= NODE_MIN_MAJOR via the detected package manager, mirroring
# resolve_main_python's pattern. Best-effort and never fatal: a Node-less
# box still installs, just without the dashboard build until Node is added
# later.

# Echo the Node major version (e.g. "20") of the `node` on PATH; nothing +
# non-zero if node isn't found or its version can't be parsed.
_node_major() {
    command -v node >/dev/null 2>&1 || return 1
    local ver; ver="$(node -v 2>/dev/null || true)"
    [[ "${ver}" =~ ^v([0-9]+) ]] || return 1
    printf '%s\n' "${BASH_REMATCH[1]}"
}

# Best-effort: install a Node >= NODE_MIN_MAJOR via the detected package
# manager. Debian/Ubuntu's own repos ship an ancient Node (10-18 depending
# on release), so this adds the NodeSource setup script for a current LTS
# instead of trusting the base repo; other ecosystems' current/rolling
# nodejs packages are close enough to install directly. Returns 0 on
# success. Only fires when HAL0_NODE_AUTOINSTALL=1 (install.sh sets it) so
# `hal0 doctor` and read-only preflight never mutate the system.
_node_autoinstall() {
    [[ "${HAL0_NODE_AUTOINSTALL:-0}" == "1" ]] || return 1
    local fam; fam="$(distro_family 2>/dev/null)" || return 1
    info "node >=${NODE_MIN_MAJOR} not found — attempting to install Node ${NODE_MIN_MAJOR} LTS (${fam})"
    case "${fam}" in
        debian)
            if curl -fsSL "https://deb.nodesource.com/setup_${NODE_MIN_MAJOR}.x" -o /tmp/hal0-nodesource-setup.sh 2>/dev/null \
                && DEBIAN_FRONTEND=noninteractive bash /tmp/hal0-nodesource-setup.sh >/dev/null 2>&1; then
                DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y -q nodejs >/dev/null 2>&1
            fi
            rm -f /tmp/hal0-nodesource-setup.sh
            ;;
        fedora) dnf install -y nodejs >/dev/null 2>&1 || dnf module install -y "nodejs:${NODE_MIN_MAJOR}" >/dev/null 2>&1 ;;
        arch) pacman -S --noconfirm nodejs npm >/dev/null 2>&1 ;;
        suse) zypper install -y "nodejs${NODE_MIN_MAJOR}" >/dev/null 2>&1 || zypper install -y nodejs npm >/dev/null 2>&1 ;;
        alpine) apk add nodejs npm >/dev/null 2>&1 ;;
        *) return 1 ;;
    esac
    command -v node >/dev/null 2>&1
}

# Resolve a Node interpreter meeting NODE_MIN_MAJOR. Returns 0 when a usable
# Node is on PATH afterwards (already present, or just auto-installed); 1
# when none is found and auto-install is disabled/unavailable/failed.
# Read-only unless HAL0_NODE_AUTOINSTALL=1.
resolve_node() {
    local m
    if m="$(_node_major)" && (( m >= NODE_MIN_MAJOR )); then
        return 0
    fi
    _node_autoinstall || return 1
    m="$(_node_major)" || return 1
    (( m >= NODE_MIN_MAJOR ))
}

# ── privileged-seam verification (POST-install, #1465) ──────────────────────
#
# Every privileged op hal0 performs post-P3-perms goes through a narrow
# `sudo -n /usr/lib/hal0/bin/hal0-*` wrapper: slot lifecycle (hal0-systemctl)
# and self-update (hal0-update) are load-bearing; agentenv/benchctl/
# podman-ro/podman-rw are optional. install.sh used to install each
# best-effort — a `visudo -cf` failure produced only a mid-log warn and the
# run still printed its success box — and NOTHING verified the result
# afterwards, so a box where that warn fired reported all-green from every
# surface while every slot start failed undiagnosably.
#
# DELIBERATELY NOT IN preflight_all: preflight runs BEFORE the seams are
# installed, where asserting them would fail every fresh install. This is a
# post-install assertion — install.sh calls it once the wrappers + grants are
# down, and `hal0 doctor all` carries the same predicate in Python
# (src/hal0/system/seam_check.py). Keep the two inventories in lock-step.
HAL0_REQUIRED_SEAMS=("hal0-systemctl" "hal0-update")
HAL0_OPTIONAL_SEAMS=("hal0-agentenv" "hal0-benchctl" "hal0-podman-ro" "hal0-podman-rw")

# Probe verbs that are provably side-effect-free (`help` prints usage,
# `check` only proves the interpreter loads). Seams absent from this map are
# presence-checked only, never invoked.
_hal0_seam_probe() {
    case "$1" in
        hal0-systemctl) printf 'help\n' ;;
        hal0-update)    printf 'check\n' ;;
        # #1889: podman-ro is now the only source of truth for a running
        # slot's image_status/actual_image, so a silently-missing grant stops
        # being cosmetic. NOT `help` — the pre-#1889 wrapper implements that
        # too, so a failed wrapper refresh would probe green while every new
        # verb was rejected. `check-slot-token` is release-specific and
        # side-effect-free (validates, prints the name it would build, never
        # calls podman). Keep in lock-step with src/hal0/system/seam_check.py.
        hal0-podman-ro) printf 'check-slot-token hal0probe\n' ;;
        # runner-images v3: the podman WRITE seam. `check-image-ref` is its
        # side-effect-free validator probe (never touches podman), same
        # shape as podman-ro's `check-slot-token` — presence alone would let
        # a stale wrapper missing image-pull/image-rm probe green. Keep in
        # lock-step with src/hal0/system/seam_check.py.
        hal0-podman-rw) printf 'check-image-ref hal0probe\n' ;;
        *)              return 1 ;;
    esac
}

# ── grant probe (#2084) ─────────────────────────────────────────────────────
#
# install.sh calls preflight_seams IMMEDIATELY after writing the wrappers and
# the sudoers drop-ins, on a box whose `hal0` user was itself created minutes
# earlier. A single failed probe in that window is therefore not evidence that
# the grant is broken — rc.10/ct151 warned on hal0-podman-ro in the same log
# second as "wrote /etc/sudoers.d/hal0-podman-ro", and the identical
# invocation succeeded on the untouched box minutes later. An operator who
# believes that warning re-runs install.sh for nothing; a validation agent
# files a regression that does not exist.
#
# So: retry, and — the part that actually matters — REPORT WHAT HAPPENED
# instead of asserting a cause we never observed. The old message threw the
# probe's stderr away and then named a diagnosis ("the grant does not apply,
# or the wrapper is stale") it had no evidence for. src/hal0/system/seam_check.py
# has kept the rc + stderr tail since #1465; this is the shell copy catching
# up, and the two are meant to stay in lock-step.
#
# Every probe verb in _hal0_seam_probe is side-effect-free by construction
# (`help` prints usage; `check`/`check-slot-token`/`check-image-ref` validate
# and print), which is exactly what makes re-running one safe.
HAL0_SEAM_PROBE_ATTEMPTS="${HAL0_SEAM_PROBE_ATTEMPTS:-3}"
HAL0_SEAM_PROBE_DELAY="${HAL0_SEAM_PROBE_DELAY:-1}"
# Bounds ONE attempt. Load-bearing now that there are up to three of them: an
# unbounded sudo that hangs would wedge the installer for good. Mirrors the
# timeout=20 seam_check.py already passes to subprocess.run.
HAL0_SEAM_PROBE_TIMEOUT="${HAL0_SEAM_PROBE_TIMEOUT:-20}"

# Run the grant probe ONCE, as the hal0 user. Prints whatever the probe wrote
# to STDERR on our stdout (its stdout is discarded) and returns the probe's
# rc. Split out so the retry/report logic below is exercisable in tests
# without root, without sudo and without a provisioned box.
_hal0_seam_probe_run() {
    local bin="$1"; shift
    # The grant is written for the hal0 user, so the only honest test runs AS
    # that user. -n keeps both hops non-interactive: a missing grant fails
    # immediately instead of prompting.
    local -a cmd=(sudo -n -u hal0 sudo -n "${bin}" "$@")
    if command -v timeout >/dev/null 2>&1; then
        cmd=(timeout "${HAL0_SEAM_PROBE_TIMEOUT}" "${cmd[@]}")
    fi
    # Order matters: 2>&1 first duplicates the capture pipe onto fd 2, THEN
    # fd 1 is discarded — so we keep stderr and drop stdout.
    "${cmd[@]}" 2>&1 >/dev/null
}

# Prove ONE seam's grant end-to-end, retrying a transient failure.
# Args: <name> <bin> <grant> <report-fn> <probe word>...
# Returns 0 when the grant works, 1 once the last attempt has failed.
_hal0_seam_grant() {
    local name="$1" bin="$2" grant="$3" report="$4"; shift 4
    local attempt=0 probe_rc=0 probe_err='' last=''

    while :; do
        attempt=$(( attempt + 1 ))
        probe_rc=0
        probe_err="$(_hal0_seam_probe_run "${bin}" "$@")" || probe_rc=$?
        (( probe_rc == 0 )) && break
        (( attempt >= HAL0_SEAM_PROBE_ATTEMPTS )) && break
        sleep "${HAL0_SEAM_PROBE_DELAY}"
    done

    if (( probe_rc == 0 )); then
        if (( attempt > 1 )); then
            info "seam ${name}: grant verified on attempt ${attempt}/${HAL0_SEAM_PROBE_ATTEMPTS} — the first probe ran before the grant was live, not a fault"
        fi
        return 0
    fi

    # Last non-empty stderr line, capped — the evidence the old message
    # discarded. `sudo: a password is required` and `hal0-podman-ro: bad slot
    # token` are entirely different bugs and used to print identically.
    last="$(printf '%s\n' "${probe_err}" | grep -v '^[[:space:]]*$' | tail -n 1 || true)"
    last="${last:0:160}"
    # Quote the command we ACTUALLY ran, full path and both sudo hops
    # included. The old text printed the bare wrapper name, so an operator who
    # pasted it hit "command not found" (the wrapper is not on sudo's
    # secure_path) and mis-diagnosed a second time.
    #
    # Facts first, then a HEDGED reading — the whole point of #2084 is not to
    # name a cause we did not observe. Three different bugs land here (grant
    # broken, wrapper stale, transient that outlasted the retry window) and
    # this line cannot tell them apart. Keep the wording in lock-step with
    # src/hal0/system/seam_check.py.
    "${report}" "seam ${name}: 'sudo -n -u hal0 sudo -n ${bin} $*' exited ${probe_rc} after ${attempt} attempt(s)${last:+ (${last})} — usually the ${grant} grant not applying or a stale wrapper, though a transient outlasting the retry window reports identically"
    return 1
}

# Verify ONE seam: wrapper present + root-owned + 0755, sudoers drop-in
# present + root-owned + 0440, and — the fact that actually matters — the
# grant works when exercised AS the hal0 user. Returns non-zero on any
# failure, printing an actionable line per problem.
_preflight_seam() {
    local name="$1" required="$2" bin_dir="${3:-/usr/lib/hal0/bin}" sudoers_dir="${4:-/etc/sudoers.d}"
    local bin="${bin_dir}/${name}" grant="${sudoers_dir}/${name}" rc=0 mode owner
    local report; report=$([[ "${required}" == "required" ]] && echo err || echo warn)

    if [[ ! -f "${bin}" ]]; then
        "${report}" "seam ${name}: wrapper ${bin} is missing — re-run 'sudo bash install.sh'"
        return 1
    fi
    mode="$(stat -c '%a' "${bin}" 2>/dev/null || echo '?')"
    owner="$(stat -c '%U:%G' "${bin}" 2>/dev/null || echo '?')"
    if [[ "${mode}" != "755" || "${owner}" != "root:root" ]]; then
        "${report}" "seam ${name}: ${bin} is ${owner} ${mode}, expected root:root 755"
        rc=1
    fi

    if [[ ! -f "${grant}" ]]; then
        "${report}" "seam ${name}: sudoers grant ${grant} is missing — re-run 'sudo bash install.sh'"
        return 1
    fi
    mode="$(stat -c '%a' "${grant}" 2>/dev/null || echo '?')"
    owner="$(stat -c '%U' "${grant}" 2>/dev/null || echo '?')"
    if [[ "${mode}" != "440" || "${owner}" != "root" ]]; then
        # sudo ignores (and on some builds refuses) a drop-in with the wrong
        # mode, so this is a total, silent failure rather than a nit.
        "${report}" "seam ${name}: ${grant} is ${owner} ${mode}, expected root 440 — sudo ignores it"
        rc=1
    fi

    local probe
    if probe="$(_hal0_seam_probe "${name}")" && (( rc == 0 )); then
        # Probes are hardcoded above, never caller input, so splitting the
        # line into argv words is safe — and #1889 needs a probe that takes an
        # argument (a verb-only probe cannot tell a stale wrapper from a
        # current one).
        local -a probe_argv=()
        read -r -a probe_argv <<< "${probe}"
        _hal0_seam_grant "${name}" "${bin}" "${grant}" "${report}" "${probe_argv[@]}" || rc=1
    fi
    return "${rc}"
}

# Verify every seam. Required seams failing is a hard failure; optional ones
# only warn. Skipped entirely when not root (a grant written for `hal0` cannot
# be exercised from an unrelated account, and reporting that as broken would
# be a false alarm).
preflight_seams() {
    local bin_dir="${1:-/usr/lib/hal0/bin}" sudoers_dir="${2:-/etc/sudoers.d}"
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        warn "privileged seams: not root — skipping grant verification (re-run 'sudo hal0 doctor')"
        return 0
    fi
    if ! id -u hal0 >/dev/null 2>&1; then
        warn "privileged seams: no hal0 service user on this box — skipping"
        return 0
    fi
    local rc=0 name
    for name in "${HAL0_REQUIRED_SEAMS[@]}"; do
        _preflight_seam "${name}" required "${bin_dir}" "${sudoers_dir}" || rc=1
    done
    for name in "${HAL0_OPTIONAL_SEAMS[@]}"; do
        _preflight_seam "${name}" optional "${bin_dir}" "${sudoers_dir}" || true
    done
    if (( rc == 0 )); then
        info "privileged seams: ${HAL0_REQUIRED_SEAMS[*]} installed and their sudo grants verified"
    else
        err "privileged seams: a required sudo grant is missing or does not work — slot lifecycle and/or self-update WILL fail on this box"
    fi
    return "${rc}"
}

# ── aggregate runner ────────────────────────────────────────────────────────

# Run every check; return non-zero if any failed. We deliberately don't
# short-circuit — operators expect `hal0 doctor` to surface the full
# picture, not the first failure.
preflight_all() {
    local rc=0
    preflight_bootstrap_prereqs || rc=$?
    preflight_arch    || rc=$?
    preflight_systemd || rc=$?
    preflight_python  || rc=$?
    preflight_venv    || rc=$?
    preflight_hindsight_python || rc=$?
    preflight_writable || rc=$?
    preflight_network || rc=$?
    preflight_container_runtime || rc=$?
    preflight_git     || rc=$?
    preflight_podman_network_backend || rc=$?
    preflight_podman_forward || rc=$?
    preflight_gpu     || rc=$?
    preflight_node    || rc=$?
    preflight_disk    || rc=$?
    preflight_ports   || rc=$?
    if (( rc == 0 )); then
        # A soft check (e.g. no GPU, git missing, node too old) can warn()
        # without ever flipping ``rc`` — that's by design (a warning must not
        # fail a valid CPU-only / no-git install). But an unconditional "all
        # passed" while warnings sit right above it reads as a contradiction
        # (#1796). Reflect the warning count in the summary instead of
        # hiding it.
        if (( ${UI_WARN_COUNT:-0} > 0 )); then
            warn "pre-flight checks passed with ${UI_WARN_COUNT} warning(s) — see above"
        else
            info "all pre-flight checks passed"
        fi
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

# ── cosign persistence for the updater (#2052) ─────────────────────────────
# bootstrap.sh verifies the release with a digest-pinned cosign fetched into
# its throwaway work directory, but nothing used to leave a cosign on the
# installed system — so the FIRST `hal0 update` of every fresh install
# hard-failed its signature gate ("cosign is not installed"). The updater's
# requirement is correct; the dependency belongs to the platform. Persist
# the exact binary bootstrap already verified (sha256 pinned in
# bootstrap.sh, handed over via HAL0_BOOTSTRAP_COSIGN). Never overwrite a
# system cosign; warn honestly — never die — when there is nothing to
# persist (e.g. a tarball-direct install), because the install itself is
# fine and only the first update is affected.
persist_bootstrap_cosign() {
    local dest_dir="${1:-/usr/local/bin}"
    if command -v cosign >/dev/null 2>&1; then
        return 0
    fi
    if [[ -n "${HAL0_BOOTSTRAP_COSIGN:-}" && -x "${HAL0_BOOTSTRAP_COSIGN}" ]]; then
        if install -m 0755 "${HAL0_BOOTSTRAP_COSIGN}" "${dest_dir}/cosign"; then
            info "cosign: persisted bootstrap's pinned build to ${dest_dir}/cosign (hal0 update requires it)"
        else
            warn "could not persist cosign to ${dest_dir}/cosign — the first 'hal0 update' will fail its signature check until cosign is installed (https://docs.sigstore.dev/cosign/installation/)"
        fi
        return 0
    fi
    warn "cosign is not installed and this run has no bootstrap-verified binary to persist — the first 'hal0 update' will fail its signature check until cosign is installed (https://docs.sigstore.dev/cosign/installation/)"
    return 0
}

# ── bootstrap channel persistence (#2083) ──────────────────────────────────
# bootstrap.sh admits and cosign-verifies the release against HAL0_CHANNEL,
# then execs into install.sh — but nothing used to write that channel
# anywhere durable, so the installed updater started life on its `stable`
# default, whose manifest pointer is 404 until GA (#1530). Every preview/
# nightly fresh install then failed the #2066 install-time update-check
# probe with a false "the update path is broken" alarm, and an rc box could
# never see the next rc via `hal0 update` without a manual channel flip.
# Same hand-off shape as persist_bootstrap_cosign above: act only when the
# env var came across the exec, never overwrite an explicit differing value
# (warn instead), never die — the install itself is fine either way.
# Writes telemetry.channel in hal0.toml: the exact key PUT
# /api/updates/channel persists and the updater's _current_channel() reads.
# Locked read-modify-write via system python3 with the same hal0.toml.lock
# fcntl + O_NOFOLLOW discipline as install.sh's [models].store seeding —
# this can run against a live daemon serving config writes, and /etc/hal0
# is service-writable while we run as root.
persist_bootstrap_channel() {
    local toml="$1"
    local channel="${HAL0_CHANNEL:-}"
    [[ -n "${channel}" ]] || return 0
    case "${channel}" in
        stable|preview|nightly) ;;
        *)
            warn "HAL0_CHANNEL='${channel}' is not one of stable|preview|nightly — not persisting an update channel"
            return 0
            ;;
    esac
    if [[ ! -f "${toml}" ]]; then
        warn "no ${toml} to persist update channel '${channel}' into — 'hal0 update' will use the stable default"
        return 0
    fi
    local out
    if out="$(python3 - "${toml}" "${channel}" <<'PYEOF'
import fcntl, os, pathlib, re, stat, sys, tempfile, tomllib
path = pathlib.Path(sys.argv[1])
channel = sys.argv[2]

# Same lock file, same O_NOFOLLOW discipline as hal0.config.locking.file_lock
# (and install.sh's [models].store seeding): /etc/hal0 is service-writable
# while this runs as root, so following a symlink here would be a privilege
# hole.
lock_path = pathlib.Path(f"{path}.lock")
created = False
try:
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o664)
    created = True
except FileExistsError:
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise SystemExit(f"lock path is not a regular file: {lock_path}")
    if created:
        os.fchmod(fd, 0o664)
        try:
            st_src = path.stat()
            if (st_src.st_uid, st_src.st_gid) != (0, 0):
                os.fchown(fd, st_src.st_uid, st_src.st_gid)
        except OSError:
            pass
    fcntl.flock(fd, fcntl.LOCK_EX)

    if path.is_symlink():
        raise SystemExit(f"refusing to write through a symlinked config: {path}")
    tfd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(tfd).st_mode):
            raise SystemExit(f"config is not a regular file: {path}")
        with os.fdopen(tfd, "r", encoding="utf-8") as fh:
            tfd = -1
            text = fh.read()
    finally:
        if tfd >= 0:
            os.close(tfd)

    # Detect the existing value with a REAL parser, not a regex: inline
    # comments (`channel = "x"  # set by ops`), single-quoted strings and
    # every other valid-TOML spelling must be seen — a missed match here
    # would append a duplicate key, and duplicate keys are a hard tomllib
    # parse error that takes the daemon down with the config.
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"existing config does not parse, leaving it alone: {exc}")
    existing = data.get("telemetry", {}).get("channel")
    if existing is not None:
        print("same" if str(existing) == channel else f"differs:{existing}")
        raise SystemExit(0)
    if channel == "stable":
        # No key on disk and the admitted channel IS the schema default:
        # persisting it would be behavior-identical for the updater today
        # but would poison the never-overwrite rule above — a later
        # deliberate `HAL0_CHANNEL=preview` re-bootstrap must still be able
        # to persist cleanly onto a box that only ever ran defaults.
        print("default")
        raise SystemExit(0)
    # Section body = every following line that does not START with '[' —
    # NOT `[^\[]*`, which would stop at the '[' of a list value and splice
    # the table in half (the [models].store patcher bug class). The header
    # match tolerates a trailing inline comment.
    m = re.search(r"^\[telemetry\][ \t]*(?:#.*)?\n(?:(?!\[).*\n?)*", text, flags=re.MULTILINE)
    if m:
        block = m.group(0)
        new_block = block if block.endswith("\n") else block + "\n"
        new_block += f'channel = "{channel}"\n'
        text = text[: m.start()] + new_block + text[m.end() :]
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += f'\n[telemetry]\nchannel = "{channel}"\n'
    # Belt and braces: whatever the splice produced must itself parse and
    # carry the value — any blind spot degrades to warn-and-leave-alone,
    # never to an unloadable config.
    try:
        patched = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"patched config would not parse, leaving it alone: {exc}")
    if patched.get("telemetry", {}).get("channel") != channel:
        raise SystemExit("patched config did not take the channel, leaving it alone")

    st = path.stat()
    fd_tmp, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd_tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except OSError:
            pass
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    print("wrote")
finally:
    os.close(fd)
PYEOF
    )"; then
        case "${out}" in
            wrote) info "update channel: persisted '${channel}' to ${toml} (telemetry.channel)" ;;
            same)  info "update channel: ${toml} already set to '${channel}'" ;;
            default) info "update channel: '${channel}' is the built-in default — nothing to persist" ;;
            differs:*)
                warn "hal0.toml already sets telemetry.channel='${out#differs:}' — leaving it, though this install was admitted from '${channel}'"
                warn "  switch with: hal0 update --channel ${channel}"
                ;;
            *) warn "unexpected result persisting update channel '${channel}' to ${toml}: ${out}" ;;
        esac
    else
        warn "could not persist update channel '${channel}' to ${toml} — 'hal0 update' will use the stable default until 'hal0 update --channel ${channel}'"
    fi
    return 0
}

# ── bootstrap work-dir cleanup (#2065) ─────────────────────────────────────
# bootstrap.sh arms an EXIT trap to delete its /tmp/hal0-install-* work dir
# (release tarball + unpacked tree, ~150 MB), but that trap dies at the
# `exec` into install.sh — so every successful install used to leak the
# whole tree. Bootstrap hands the path over via HAL0_BOOTSTRAP_WORK (left
# unset under HAL0_BOOTSTRAP_KEEP_TMP=1, though the knob is honored here
# again as defense in depth). install.sh calls this as its very last step:
# that is strictly after persist_bootstrap_cosign above has copied
# bootstrap's cosign out of the tree (#2052/#2058 ordering), and a failed
# install never reaches it — the tree stays for debugging. Deleting the
# tree install.sh itself runs from is safe: bash holds an open fd on the
# script, which the kernel keeps readable after the unlink. The name check
# keeps a stray or mangled value from ever aiming rm -rf at anything that
# is not a bootstrap work dir. Never fatal — a leftover tmp dir must not
# fail an otherwise complete install.
cleanup_bootstrap_workdir() {
    local work="${HAL0_BOOTSTRAP_WORK:-}"
    [[ -n "${work}" ]] || return 0
    if [[ "${HAL0_BOOTSTRAP_KEEP_TMP:-0}" == "1" ]]; then
        info "HAL0_BOOTSTRAP_KEEP_TMP=1 — leaving bootstrap work dir ${work}"
        return 0
    fi
    if [[ "${work##*/}" != hal0-install-* ]]; then
        warn "not removing unexpected bootstrap work dir path: ${work}"
        return 0
    fi
    [[ -d "${work}" ]] || return 0
    rm -rf -- "${work}" || return 0
    info "removed bootstrap work dir ${work}"
    return 0
}
