#!/usr/bin/env bash
_CS_DEFAULT_URL="https://raw.githubusercontent.com/Hal0ai/hal0/main/scripts/proxmox-ve"
_cs_boot="${COMMUNITY_SCRIPTS_CORE_DIR:-$(dirname "${BASH_SOURCE[0]}")/../../core}/core/build.func"
source "$_cs_boot" 2>/dev/null || source <(curl -fsSL "${COMMUNITY_SCRIPTS_CORE_URL:-https://raw.githubusercontent.com/community-scripts/core/main}/core/build.func")

# Copyright (c) 2026 hal0 contributors
# Author: thinmintdev
# License: Apache-2.0 | https://github.com/Hal0ai/hal0/raw/main/LICENSE
# Source: https://hal0.dev/

APP="hal0"
var_tags="${var_tags:-ai;llm;inference}"
var_cpu="${var_cpu:-4}"
var_ram="${var_ram:-8192}"
var_disk="${var_disk:-40}"
var_os="${var_os:-ubuntu}"
var_version="${var_version:-26.04}"
var_arm64="${var_arm64:-no}" # the runner images and FastFlowLM are amd64 only
var_unprivileged="${var_unprivileged:-1}"
var_gpu="${var_gpu:-yes}"
var_fuse="${var_fuse:-yes}"
var_keyctl="${var_keyctl:-1}"
var_mknod="${var_mknod:-1}"

export var_hal0_channel="${var_hal0_channel:-}"
export var_hal0_memory="${var_hal0_memory:-}"
export var_hal0_openwebui="${var_hal0_openwebui:-}"
export var_hal0_agents="${var_hal0_agents:-}"
export var_hal0_image_models="${var_hal0_image_models:-}"

header_info "$APP"
variables
color
catch_errors

function update_script() {
  header_info
  check_container_storage
  check_container_resources

  if [[ ! -x /usr/lib/hal0/venv/bin/hal0 ]]; then
    msg_error "No ${APP} Installation Found!"
    exit
  fi

  msg_info "Updating hal0"
  $STD /usr/lib/hal0/venv/bin/hal0 update --yes
  msg_ok "Updated hal0"
  msg_ok "Updated successfully!"
  exit
}

start
build_container
description

msg_ok "Completed Successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e "${INFO}${YW}Access it using the following URL:${CL}"
echo -e "${GATEWAY}${BGN}http://${IP}:8080${CL}"
echo -e "${INFO}${YW}Chat (Open WebUI): http://${IP}:3001${CL}"
