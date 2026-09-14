#!/usr/bin/env bash
# installer/lib/pull-retry.sh
#
# Purpose: Backoff + non-retryable classification for container-image pulls,
#          so a flaky registry connection gets a few honest retries while an
#          auth/404/out-of-space failure fails fast instead of spinning.
# Expects: A sourced installer environment — `warn`/`err`/`info` from
#          lib/ui.sh (used for progress messages; falls back to plain
#          `echo` if ui.sh was not sourced, so this file also works
#          standalone under a bats/pytest harness).
# Provides: hal0_pull_backoff_delay(attempt), hal0_pull_is_retryable(text),
#           hal0_pull_with_retry(runtime, image, [max_attempts])
# Modder notes:
#   The backoff table and non-retryable pattern are the two knobs a modder
#   is likely to want to change; both are overridable via env
#   (HAL0_PULL_RETRY_DELAYS, HAL0_PULL_MAX_ATTEMPTS) rather than requiring an
#   edit here. The post-pull `inspect` verification catches the case where
#   the pull subprocess exits 0 but the image never lands in local storage
#   (seen with some rootless podman + overlay combinations under disk
#   pressure) — do not remove it as a "redundant" check.

# shellcheck shell=bash

# Fall back to plain stderr writers when lib/ui.sh was not sourced (e.g. a
# standalone test harness) so this file has no hard dependency on it.
if ! command -v warn >/dev/null 2>&1; then
    warn() { printf 'WARN  %s\n' "$*" >&2; }
fi
if ! command -v err >/dev/null 2>&1; then
    err() { printf 'ERROR %s\n' "$*" >&2; }
fi
if ! command -v info >/dev/null 2>&1; then
    info() { printf 'INFO  %s\n' "$*"; }
fi

# Non-retryable failure signatures: auth/permission, 404/manifest-unknown,
# and out-of-space/daemon-unreachable are never fixed by trying again.
# Everything else (DNS blip, connection reset, registry 5xx, timeout) is
# treated as transient and gets the backoff table below.
_HAL0_PULL_NONRETRYABLE_RE='unauthorized|authentication required|denied|forbidden|not[[:space:]-]?found|manifest unknown|\b404\b|no space left on device|cannot connect to the .*daemon'

# hal0_pull_is_retryable TEXT — 0 (retry it) unless TEXT matches a known
# non-retryable signature, in which case 1 (give up now).
hal0_pull_is_retryable() {
    local text="$1"
    if printf '%s' "$text" | grep -qiE "${_HAL0_PULL_NONRETRYABLE_RE}"; then
        return 1
    fi
    return 0
}

# hal0_pull_backoff_delay ATTEMPT — seconds to sleep before retry #ATTEMPT
# (1-indexed: the wait before the 2nd overall attempt is delay(1)). Reads
# HAL0_PULL_RETRY_DELAYS (space-separated seconds) or falls back to
# "5 15 30 60"; past the end of the table the last value doubles per extra
# step, same shape as the ODS reference implementation.
hal0_pull_backoff_delay() {
    local attempt="$1"
    local default_delays=(5 15 30 60)
    local -a delays
    # shellcheck disable=SC2206  # word-splitting HAL0_PULL_RETRY_DELAYS is intentional
    delays=(${HAL0_PULL_RETRY_DELAYS:-${default_delays[*]}})
    [[ ${#delays[@]} -eq 0 ]] && delays=("${default_delays[@]}")

    local idx=$((attempt - 1))
    local delay="${delays[$idx]:-}"
    if [[ -z "$delay" ]]; then
        local last_idx=$((${#delays[@]} - 1))
        delay="${delays[$last_idx]}"
        [[ "$delay" =~ ^[0-9]+$ ]] || delay="${default_delays[-1]}"
        local extra=$((idx - last_idx)) step
        for ((step = 0; step < extra; step++)); do
            delay=$((delay * 2))
        done
    fi
    [[ "$delay" =~ ^[0-9]+$ ]] || delay=30
    printf '%s\n' "$delay"
}

# hal0_pull_with_retry RUNTIME IMAGE [MAX_ATTEMPTS]
#
# Runs `RUNTIME pull IMAGE`, retrying transient failures with backoff up to
# MAX_ATTEMPTS (default HAL0_PULL_MAX_ATTEMPTS or 4). A non-retryable
# failure (see hal0_pull_is_retryable) returns immediately. On an apparent
# success, verifies the image actually landed in local storage with
# `RUNTIME image exists` (falling back to `RUNTIME inspect` for older
# podman/docker without the `image exists` subcommand) before declaring
# victory — a pull that exits 0 without the image present is treated as a
# retryable failure.
hal0_pull_with_retry() {
    local runtime="$1" image="$2"
    local max_attempts="${3:-${HAL0_PULL_MAX_ATTEMPTS:-4}}"
    [[ "$max_attempts" =~ ^[0-9]+$ && "$max_attempts" -ge 1 ]] || max_attempts=4

    local attempt log
    for ((attempt = 1; attempt <= max_attempts; attempt++)); do
        if ((attempt > 1)); then
            local backoff
            backoff="$(hal0_pull_backoff_delay $((attempt - 1)))"
            warn "retry ${attempt}/${max_attempts} pulling ${image} (waiting ${backoff}s)"
            sleep "$backoff"
        fi

        log="$(mktemp -t hal0-pull.XXXXXX)"
        if "${runtime}" pull "${image}" >"${log}" 2>&1; then
            if "${runtime}" image exists "${image}" 2>/dev/null \
                || "${runtime}" inspect "${image}" >/dev/null 2>&1; then
                rm -f "$log"
                return 0
            fi
            warn "${runtime} pull ${image} exited 0 but the image is not in local storage — retrying"
            rm -f "$log"
            continue
        fi

        if ! hal0_pull_is_retryable "$(cat "$log")"; then
            err "non-retryable failure pulling ${image}: $(tail -n1 "$log")"
            rm -f "$log"
            return 1
        fi
        warn "attempt ${attempt}/${max_attempts} failed pulling ${image}: $(tail -n1 "$log")"
        rm -f "$log"
    done

    err "failed to pull ${image} after ${max_attempts} attempts"
    return 1
}
