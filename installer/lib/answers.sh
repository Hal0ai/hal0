#!/usr/bin/env bash
# installer/lib/answers.sh
#
# Purpose: The one place answers enter the install. install.sh calls
#          hal0_collect_answers once, between the detection half of the
#          pre-flight step and everything that changes the host.
# Expects: Sourced by install.sh. Nothing here prompts or changes the host.
# Provides: hal0_apply_answers (stub until the answers reader lands),
#          hal0_collect_answers.
# Env:     HAL0_ANSWERS_FILE  path of an answers document (read here only).
#          HAL0_FACTS_FILE    where the facts file goes; exported by
#                             hal0_collect_answers with its default filled in.
# Sets:    HAL0_ANSWERS_APPLIED=1 when a document was applied, else 0. The
#          legacy prompts in install.sh skip themselves when it is 1.

# Apply the answers document at PATH; ORIGIN is `file` (a wizard origin joins
# it later). A stub: prints nothing and returns 0 until the reader exists.
hal0_apply_answers() {
    return 0
}

hal0_collect_answers() {
    HAL0_ANSWERS_APPLIED=0
    export HAL0_FACTS_FILE="${HAL0_FACTS_FILE:-/run/hal0-install/facts.json}"
    local path="${HAL0_ANSWERS_FILE:-}"
    [[ -n "${path}" ]] || return 0
    hal0_apply_answers "${path}" file || return $?
    # shellcheck disable=SC2034 # read by install.sh, which sources this file
    HAL0_ANSWERS_APPLIED=1
    return 0
}
