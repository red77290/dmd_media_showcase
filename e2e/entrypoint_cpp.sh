#!/usr/bin/env bash
set -euo pipefail

TARGET_KIND="${DMD_TARGET_KIND:-arcadematrix_esp32}"
mkdir -p /sd/cache

if [ "${TARGET_KIND}" = "arcadematrix_esp32" ]; then
    cp -a /e2e_sd/am_esp32_sd/. /sd/
    exec /usr/local/bin/arcadematrix_esp32_e2e
else
    echo "[entrypoint_cpp] Unknown DMD_TARGET_KIND=${TARGET_KIND}" >&2
    exit 1
fi
