#!/usr/bin/env bash
# Register `dmdmediashowcase.local` with macOS Bonjour (`mDNSResponder`) on the Mac host
# when running `dmd-media-showcase` inside Docker Desktop / Rancher Desktop / OrbStack
# (whose Linux VM NAT isolates container UDP :5353 multicast from the physical Wi-Fi/Ethernet LAN).
#
# Usage:
#   ./scripts/register_macos_mdns.sh              # foreground (Ctrl+C to stop)
#   ./scripts/register_macos_mdns.sh --daemon     # background daemon (~/.dmd_showcase_mdns.pid)
#   ./scripts/register_macos_mdns.sh --stop       # stop background daemon

set -euo pipefail

HOSTNAME_BASE="${MDNS_HOSTNAME:-dmdmediashowcase}"
HOSTNAME_BASE="${HOSTNAME_BASE%.local}"
PORT="${PORT:-8890}"
PID_FILE="${HOME}/.dmd_showcase_mdns.pid"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This helper is only needed on macOS Docker hosts (on Linux use 'network_mode: host')." >&2
    exit 1
fi

stop_existing() {
    if [[ -f "${PID_FILE}" ]]; then
        old_pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
        if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" 2>/dev/null; then
            kill "${old_pid}" 2>/dev/null || true
            echo "Stopped macOS Bonjour proxy (PID ${old_pid})."
        fi
        rm -f "${PID_FILE}"
    fi
}

if [[ "${1:-}" == "--stop" ]]; then
    stop_existing
    exit 0
fi

LAN_IP="${HOST_LAN_IP:-}"
if [[ -z "${LAN_IP}" ]]; then
    DEFAULT_IF="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}')"
    if [[ -n "${DEFAULT_IF}" ]]; then
        LAN_IP="$(ipconfig getifaddr "${DEFAULT_IF}" 2>/dev/null || true)"
    fi
fi
if [[ -z "${LAN_IP}" ]]; then
    LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
fi
if [[ -z "${LAN_IP}" ]]; then
    echo "Could not auto-detect Mac LAN IPv4. Set HOST_LAN_IP=192.168.x.y and retry." >&2
    exit 1
fi

stop_existing

if [[ "${1:-}" == "--daemon" ]]; then
    nohup /usr/bin/dns-sd -P "DMD Media Showcase" "_dmd-showcase._tcp" "local" "${PORT}" "${HOSTNAME_BASE}.local" "${LAN_IP}" </dev/null >/dev/null 2>&1 &
    echo $! > "${PID_FILE}"
    echo "Registered http://${HOSTNAME_BASE}.local:${PORT} -> ${LAN_IP}:${PORT} via macOS Bonjour (PID $(cat "${PID_FILE}"))."
else
    echo "Advertising http://${HOSTNAME_BASE}.local:${PORT} -> ${LAN_IP}:${PORT} via macOS Bonjour (Ctrl+C to stop)..."
    exec /usr/bin/dns-sd -P "DMD Media Showcase" "_dmd-showcase._tcp" "local" "${PORT}" "${HOSTNAME_BASE}.local" "${LAN_IP}"
fi
