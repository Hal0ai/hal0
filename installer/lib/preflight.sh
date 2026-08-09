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
            DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 || true
            DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${cand}" "${cand}-venv" >/dev/null 2>&1 || return 1 ;;
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
                DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 || true
                DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${cand}" "${cand}-venv" >/dev/null 2>&1 || continue ;;
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
                DEBIAN_FRONTEND=noninteractive apt-get update -qq || true
                DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-venv python3-pip || ok=0
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
            apt-get) DEBIAN_FRONTEND=noninteractive apt-get install -y -q podman || ok=0 ;;
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
# HAL0_GPU_RENDER_GROUP_OVERRIDE, HAL0_GPU_USER_OVERRIDE.
# See docs/getting-started/proxmox.mdx for the full walkthrough.
HAL0_GPU_RC_BROKEN_GID=3
HAL0_GPU_RC_NO_DEVICE=4
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
    local have_render="" have_kfd="" have_accel=""
    compgen -G "${dri_glob}" >/dev/null 2>&1 && have_render=1
    [[ -e /dev/kfd ]] && have_kfd=1
    [[ -e /dev/accel/accel0 ]] && have_accel=1

    if [[ -n "${have_render}" ]]; then
        info "gpu: $(compgen -G "${dri_glob}" 2>/dev/null | tr '\n' ' ')present"
    fi
    [[ -n "${have_kfd}"   ]] && info "gpu: /dev/kfd present (ROCm compute)"
    [[ -n "${have_accel}" ]] && info "npu: /dev/accel/accel0 present (AMD XDNA)"

    if [[ -z "${have_render}" ]]; then
        if [[ "${in_container}" == "lxc" ]]; then
            warn "gpu: no /dev/dri/renderD* inside this container — GPU slots will run CPU-only"
            warn "  Forward the devices from the Proxmox HOST (/etc/pve/lxc/<CTID>.conf, PVE 8.2+):"
            warn "    dev0: /dev/dri/renderD128,gid=<render gid INSIDE this container>"
            warn "    dev1: /dev/kfd                    # ROCm compute (optional)"
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
    return 0
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
                DEBIAN_FRONTEND=noninteractive apt-get install -y -q nodejs >/dev/null 2>&1
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
# and self-update (hal0-update) are load-bearing; agentenv/benchctl/podman-ro
# are optional. install.sh used to install each best-effort — a `visudo -cf`
# failure produced only a mid-log warn and the run still printed its success
# box — and NOTHING verified the result afterwards, so a box where that warn
# fired reported all-green from every surface while every slot start failed
# undiagnosably.
#
# DELIBERATELY NOT IN preflight_all: preflight runs BEFORE the seams are
# installed, where asserting them would fail every fresh install. This is a
# post-install assertion — install.sh calls it once the wrappers + grants are
# down, and `hal0 doctor all` carries the same predicate in Python
# (src/hal0/system/seam_check.py). Keep the two inventories in lock-step.
HAL0_REQUIRED_SEAMS=("hal0-systemctl" "hal0-update")
HAL0_OPTIONAL_SEAMS=("hal0-agentenv" "hal0-benchctl" "hal0-podman-ro")

# Probe verbs that are provably side-effect-free (`help` prints usage,
# `check` only proves the interpreter loads). Seams absent from this map are
# presence-checked only, never invoked.
_hal0_seam_probe() {
    case "$1" in
        hal0-systemctl) printf 'help\n' ;;
        hal0-update)    printf 'check\n' ;;
        *)              return 1 ;;
    esac
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
        # The grant is written for the hal0 user, so the only honest test runs
        # AS that user. -n keeps it non-interactive: a missing grant fails
        # immediately instead of prompting.
        if ! sudo -n -u hal0 sudo -n "${bin}" "${probe}" >/dev/null 2>&1; then
            "${report}" "seam ${name}: 'sudo -n ${name} ${probe}' failed as the hal0 user — the ${grant} grant does not apply"
            rc=1
        fi
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
    preflight_podman_network_backend || rc=$?
    preflight_podman_forward || rc=$?
    preflight_gpu     || rc=$?
    preflight_node    || rc=$?
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
