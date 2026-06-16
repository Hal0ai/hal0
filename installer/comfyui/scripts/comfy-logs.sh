#!/usr/bin/env bash
docker logs --tail "${1:-60}" -f comfyui
