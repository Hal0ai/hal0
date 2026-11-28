#!/usr/bin/env bash

# Copyright (c) 2026 hal0 contributors
# Author: thinmintdev
# License: Apache-2.0 | https://github.com/Hal0ai/hal0/raw/main/LICENSE
# Source: https://hal0.dev/

source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

var_hal0_channel="${var_hal0_channel:-stable}"
if [[ ! "$var_hal0_channel" =~ ^(stable|preview|nightly)$ ]]; then
  msg_warn "Unknown hal0 release channel '${var_hal0_channel}', using stable"
  var_hal0_channel="stable"
fi
var_hal0_memory="${var_hal0_memory:-yes}"
var_hal0_openwebui="${var_hal0_openwebui:-yes}"
var_hal0_agents="${var_hal0_agents:-yes}"
var_hal0_image_models="${var_hal0_image_models:-no}"

confirm_external_installer "https://hal0.dev/" "https://hal0.dev/install.sh"

export HAL0_CHANNEL="$var_hal0_channel"
export HAL0_NONINTERACTIVE=1
if [[ "$var_hal0_memory" == "no" ]]; then
  export HAL0_SKIP_HINDSIGHT=1
fi
if [[ "$var_hal0_openwebui" == "no" ]]; then
  export HAL0_SKIP_OPENWEBUI=1
fi
if [[ "$var_hal0_agents" == "no" ]]; then
  export HAL0_SKIP_HERMES=1 HAL0_SKIP_PI=1
fi
if [[ "$var_hal0_image_models" == "yes" ]]; then
  export HAL0_IMAGE_MODELS=recommended
fi
if ! compgen -G "/dev/dri/renderD*" >/dev/null; then
  msg_warn "No GPU render node in this container, installing hal0 for CPU inference"
  export HAL0_ALLOW_CPU_ONLY=1
fi

msg_info "Installing hal0 (Patience)"
curl_with_retry "https://hal0.dev/install.sh" /tmp/hal0-bootstrap.sh
# The bootstrap verifies the release it downloads against its cosign signature
$STD bash /tmp/hal0-bootstrap.sh
rm -f /tmp/hal0-bootstrap.sh
msg_ok "Installed hal0"

motd_ssh
customize
cleanup_lxc
