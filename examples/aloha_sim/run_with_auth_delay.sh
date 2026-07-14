#!/usr/bin/env bash

set -euo pipefail

DELAY_MS="${AUTH_NETWORK_DELAY_MS:-0}"
MONITOR_CONFIG_PATH="${MONITOR_CONFIG:-}"
SHAPED_INTERFACE=""
QDISC_APPLIED=0
CLIENT_PID=""

run_client() {
    source /.venv/bin/activate
    python examples/aloha_sim/main.py
}

cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM

    if [ -n "$CLIENT_PID" ] && kill -0 "$CLIENT_PID" 2>/dev/null; then
        kill "$CLIENT_PID" 2>/dev/null || true
        wait "$CLIENT_PID" 2>/dev/null || true
    fi

    if [ "$QDISC_APPLIED" -eq 1 ] && [ -n "$SHAPED_INTERFACE" ]; then
        echo " Removing Auth101 network-delay rule from $SHAPED_INTERFACE..."
        tc qdisc del dev "$SHAPED_INTERFACE" root 2>/dev/null || true
    fi

    exit "$exit_code"
}

if [[ "$DELAY_MS" =~ ^0+([.]0+)?$ ]]; then
    run_client
    exit $?
fi

if ! [[ "$DELAY_MS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "❌ Invalid AUTH_NETWORK_DELAY_MS value: '$DELAY_MS'"
    exit 1
fi

if [ -z "$MONITOR_CONFIG_PATH" ] || [ ! -f "$MONITOR_CONFIG_PATH" ]; then
    echo "❌ Cannot apply Auth101 delay: MONITOR_CONFIG is missing or unreadable: $MONITOR_CONFIG_PATH"
    exit 1
fi

IFS=$'\t' read -r AUTH_HOST AUTH_PORT < <(python3 - "$MONITOR_CONFIG_PATH" <<'PYEOF'
import json
import sys

with open(sys.argv[1]) as config_file:
    config = json.load(config_file)

auth_info = config.get("authInfo", {})
host = str(auth_info.get("host", "")).strip()
port = auth_info.get("port", "")
if not host or not port:
    raise SystemExit("authInfo.host or authInfo.port is missing")
print(f"{host}\t{port}")
PYEOF
)

if [ -z "$AUTH_HOST" ] || ! [[ "$AUTH_PORT" =~ ^[0-9]+$ ]]; then
    echo "❌ Could not read a valid Auth101 endpoint from $MONITOR_CONFIG_PATH"
    exit 1
fi

AUTH_IP="$(getent ahostsv4 "$AUTH_HOST" | awk 'NR == 1 {print $1}')"
if [ -z "$AUTH_IP" ]; then
    echo "❌ Could not resolve Auth101 host '$AUTH_HOST' to an IPv4 address."
    exit 1
fi

SHAPED_INTERFACE="$(ip -4 route get "$AUTH_IP" | awk '{for (i=1; i<=NF; i++) if ($i == "dev") {print $(i+1); exit}}')"
if [ -z "$SHAPED_INTERFACE" ]; then
    echo "❌ Could not determine the network interface used to reach Auth101 at $AUTH_IP."
    exit 1
fi
if [ "$SHAPED_INTERFACE" = "lo" ] || [[ "$AUTH_IP" = 127.* ]]; then
    echo "❌ Auth101 host '$AUTH_HOST' resolves through loopback ($AUTH_IP)."
    echo "   --auth-delay-ms requires a remote Auth101 address reachable over LAN."
    exit 1
fi

ROOT_QDISC="$(tc qdisc show dev "$SHAPED_INTERFACE" | awk '$0 ~ / root / {print $2; exit}')"
case "$ROOT_QDISC" in
    ""|noqueue|mq|fq|fq_codel|pfifo_fast)
        ;;
    *)
        echo "❌ Refusing to replace custom root qdisc '$ROOT_QDISC' on $SHAPED_INTERFACE."
        echo "   Remove the custom traffic-control setup or run without --auth-delay-ms."
        exit 1
        ;;
esac

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Install a three-band priority scheduler and route only IPv4 TCP packets for
# the configured Auth101 endpoint through the delayed third band. Other host
# traffic continues through the non-delayed default bands.
tc qdisc replace dev "$SHAPED_INTERFACE" root handle 1: prio bands 3
QDISC_APPLIED=1
tc qdisc replace dev "$SHAPED_INTERFACE" parent 1:3 handle 30: netem delay "${DELAY_MS}ms"
tc filter replace dev "$SHAPED_INTERFACE" protocol ip parent 1: prio 3 u32 \
    match ip dst "${AUTH_IP}/32" \
    match ip dport "$AUTH_PORT" 0xffff \
    flowid 1:3

echo "🌐 Auth101 RTT emulation enabled:"
echo "   Endpoint : ${AUTH_HOST} (${AUTH_IP}):${AUTH_PORT}"
echo "   Interface: ${SHAPED_INTERFACE}"
echo "   Delay    : ${DELAY_MS} ms on outbound Auth101 packets (~${DELAY_MS} ms added RTT)"
tc qdisc show dev "$SHAPED_INTERFACE"
tc filter show dev "$SHAPED_INTERFACE" parent 1:

run_client &
CLIENT_PID=$!
set +e
wait "$CLIENT_PID"
CLIENT_STATUS=$?
set -e
CLIENT_PID=""
exit "$CLIENT_STATUS"
