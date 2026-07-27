#!/usr/bin/env bash

set -euo pipefail

# Ensure standard docker and brew binary paths are available
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin"

# Get absolute paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENPI_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
IOTAUTH_DIR="$(cd "$OPENPI_DIR/../iotauth" && pwd)"

# Automatically activate virtual environment if present
if [ -f "$OPENPI_DIR/.venv/bin/activate" ]; then
    echo "🐍 Activating Python virtual environment at $OPENPI_DIR/.venv..."
    source "$OPENPI_DIR/.venv/bin/activate"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# Usage:
#   ./scripts/lamps_2026/run_tests.sh --test1|--test2|--test3 --local|--remote [options]
# ─────────────────────────────────────────────────────────────────────────────
TEST_NAME=""
AUTH_MODE=""
AUTH_PASSWORD="1234"
RUNS="5"
BYPASS_MODE="insiga"
AUTH_DELAY_MS="0"
SECURE_ACTUATOR_MODE=1
PLOT_ARGS=()
TEST2_THRESHOLDS=(0.0000 0.09 0.2 0.25 0.95)
TEST3_VALIDITY_PERIODS=(1 2 3 4 5)
TEST3_THRESHOLDS=(0.0000 0.09 0.2 0.25 0.95)

usage() {
    cat <<EOF
Usage: $0 --test1|--test2|--test3 --local|--remote [options]

Core options:
  --password <pw>           Auth password (default: 1234)
  --runs <n>                Runs per condition (default: 5)
  --auth-delay-ms <ms>      Remote Auth101 round-trip delay emulation
  --secure-actuator         Enable secure actuator (default)
  --no-secure-actuator      Disable secure actuator and measure monitor-only latency
  --bypass-mode <mode>      Result grouping: insiga or siga (default: insiga)

Plot options:
  --show-siga-rate          Overlay SIGA rate (legacy: --show-active-rate)
  --show-insiga-rate        Overlay INSIGA rate (legacy: --show-still-rate)
  --equidistant-x | --log-x | --log-y
  --aspect-1-1 | --no-title
EOF
}

if [ "$#" -eq 0 ]; then
    echo "❌ Error: No arguments provided."
    usage
    exit 1
fi

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --test1)
            TEST_NAME="test1"
            shift
            ;;
        --test2)
            TEST_NAME="test2"
            shift
            ;;
        --test3)
            TEST_NAME="test3"
            shift
            ;;
        --local)
            AUTH_MODE="local"
            shift
            ;;
        --remote)
            AUTH_MODE="remote"
            shift
            ;;
        --password)
            if [[ -n "${2:-}" ]]; then
                AUTH_PASSWORD="$2"
                shift 2
            else
                echo "❌ Error: --password requires a value."
                exit 1
            fi
            ;;
        --runs)
            if [[ -n "${2:-}" ]]; then
                RUNS="$2"
                shift 2
            else
                echo "❌ Error: --runs requires a value."
                exit 1
            fi
            ;;
        --bypass-mode)
            if [[ -n "${2:-}" ]]; then
                BYPASS_MODE="$2"
                shift 2
            else
                echo "❌ Error: --bypass-mode requires a value (insiga or siga)."
                exit 1
            fi
            ;;
        --auth-delay-ms)
            if [[ -n "${2:-}" ]]; then
                AUTH_DELAY_MS="$2"
                shift 2
            else
                echo "❌ Error: --auth-delay-ms requires a non-negative millisecond value."
                exit 1
            fi
            ;;
        --secure-actuator)
            SECURE_ACTUATOR_MODE=1
            shift
            ;;
        --no-secure-actuator)
            SECURE_ACTUATOR_MODE=0
            shift
            ;;
        --show-siga-rate|--show-active-rate)
            PLOT_ARGS+=(--show-siga-rate)
            shift
            ;;
        --show-insiga-rate|--show-still-rate)
            PLOT_ARGS+=(--show-insiga-rate)
            shift
            ;;
        --equidistant-x|--log-x|--log-y|--aspect-1-1|--square|--aspect-ratio-1-1|--no-title)
            PLOT_ARGS+=("$1")
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "❌ Error: Unknown argument '$1'"
            usage
            exit 1
            ;;
    esac
done

# Validate required flags
if [ -z "$TEST_NAME" ]; then
    echo "❌ Error: A test flag is required (e.g. --test1, --test2, or --test3)."
    usage
    exit 1
fi

if [ -z "$AUTH_MODE" ]; then
    echo "❌ Error: An auth mode flag is required (--local or --remote)."
    usage
    exit 1
fi

case "$BYPASS_MODE" in
    insiga|siga)
        ;;
    still)
        BYPASS_MODE="insiga"
        ;;
    active)
        BYPASS_MODE="siga"
        ;;
    *)
        echo "❌ Error: --bypass-mode must be insiga or siga (received '$BYPASS_MODE')."
        exit 1
        ;;
esac

if ! [[ "$RUNS" =~ ^[1-9][0-9]*$ ]]; then
    echo "❌ Error: --runs must be a positive integer (received '$RUNS')."
    exit 1
fi

if ! [[ "$AUTH_DELAY_MS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "❌ Error: --auth-delay-ms must be a non-negative number (received '$AUTH_DELAY_MS')."
    exit 1
fi

if [ "$AUTH_MODE" != "remote" ] && ! [[ "$AUTH_DELAY_MS" =~ ^0+([.]0+)?$ ]]; then
    echo "❌ Error: --auth-delay-ms is only supported with --remote."
    exit 1
fi
export AUTH_NETWORK_DELAY_MS="$AUTH_DELAY_MS"
export ALOHA_MAX_EPISODE_STEPS="${ALOHA_MAX_EPISODE_STEPS:-300}"
export ALOHA_NUM_EPISODES="${ALOHA_NUM_EPISODES:-1}"
export SECURE_ACTION_MAX_AGE_MS="${SECURE_ACTION_MAX_AGE_MS:-300000}"
export SECURE_ACTUATOR_CONTROL_TIMEOUT="${SECURE_ACTUATOR_CONTROL_TIMEOUT:-300.0}"
export ACTUATOR_RECORD_WAIT_TIMEOUT="${ACTUATOR_RECORD_WAIT_TIMEOUT:-10.0}"
# Use a deterministic context that satisfies context_based_validity.graph.
# Ordinary monitor runs still default to the wall clock when these are unset.
export IOTAUTH_CONTEXT_PEOPLE="${IOTAUTH_CONTEXT_PEOPLE:-1}"
export IOTAUTH_CONTEXT_LOCATION="${IOTAUTH_CONTEXT_LOCATION:-Meeting Room}"
export IOTAUTH_CONTEXT_TIME="${IOTAUTH_CONTEXT_TIME:-14:00}"

# ─────────────────────────────────────────────────────────────────────────────
# Output directory setup
# Structure: test_reports/<test_name>/<auth_mode>/<timestamp>/
# ─────────────────────────────────────────────────────────────────────────────
RUN_TIMESTAMP="$(date +%Y-%m-%d-%H-%M-%S)"
# Derive validity periods directly from the commPolicies in the IoTAuth graph.
# Any policy with a RelativeValidity of "N*sec" contributes one period.
# This means run_tests.sh never needs updating when the graph changes.
export GRAPH_FILE="$IOTAUTH_DIR/examples/configs/context_based_validity.graph"
if [ ! -f "$GRAPH_FILE" ]; then
    echo "❌ Error: IoTAuth graph not found at $GRAPH_FILE"
    exit 1
fi
read -ra VALIDITY_PERIODS <<< "$(python3 - <<'PYEOF'
import json, re, sys
try:
    import os
    path = os.environ["GRAPH_FILE"]
    with open(path) as f:
        g = json.load(f)
    periods = set()
    for p in g.get("commPolicies", []):
        m = re.match(r"(\d+)\*sec", p.get("RelativeValidity", ""))
        if m:
            periods.add(int(m.group(1)))
    print(" ".join(str(v) for v in sorted(periods)))
except Exception as e:
    print(f"ERROR parsing graph: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
)"
if [ ${#VALIDITY_PERIODS[@]} -eq 0 ]; then
    echo "❌ Error: No validity periods found in $GRAPH_FILE"
    exit 1
fi
echo "📋 Validity periods from graph: ${VALIDITY_PERIODS[*]}s"

# Test 3 intentionally uses a fixed 5x5 Cartesian grid. Its validity periods
# must still be present in the graph because each one needs a matching config.
if [ "$TEST_NAME" = "test3" ]; then
    for required_val in "${TEST3_VALIDITY_PERIODS[@]}"; do
        if [[ " ${VALIDITY_PERIODS[*]} " != *" ${required_val} "* ]]; then
            echo "❌ Error: Test 3 requires validity period ${required_val}s, but it is not present in $GRAPH_FILE."
            exit 1
        fi
    done
fi

# Timestamped run output directory (reports + graphs + copied source data all land here)
if [ "$TEST_NAME" = "test2" ]; then
    OUTPUT_DIR="$OPENPI_DIR/test_reports/$TEST_NAME/$AUTH_MODE/$BYPASS_MODE/$RUN_TIMESTAMP"
else
    OUTPUT_DIR="$OPENPI_DIR/test_reports/$TEST_NAME/$AUTH_MODE/$RUN_TIMESTAMP"
fi

# Create the full test_reports hierarchy (including future test placeholders)
mkdir -p "$OUTPUT_DIR"
mkdir -p "$OPENPI_DIR/test_reports/test2/local/insiga"
mkdir -p "$OPENPI_DIR/test_reports/test2/local/siga"
mkdir -p "$OPENPI_DIR/test_reports/test2/remote/insiga"
mkdir -p "$OPENPI_DIR/test_reports/test2/remote/siga"
mkdir -p "$OPENPI_DIR/test_reports/test3/local"
mkdir -p "$OPENPI_DIR/test_reports/test3/remote"

{
    echo "Test: $TEST_NAME"
    echo "Auth mode: $AUTH_MODE"
    echo "Runs per condition: $RUNS"
    echo "Auth RTT delay requested: ${AUTH_DELAY_MS} ms"
    echo "Secure actuator: ${SECURE_ACTUATOR_MODE}"
    echo "Latency metric: $([ "$SECURE_ACTUATOR_MODE" -eq 1 ] && echo monitor_actuator_ms || echo monitor_latency)"
    echo "Classification boundary: score >= threshold"
    echo "Episode steps: ${ALOHA_MAX_EPISODE_STEPS}"
    echo "Episodes: ${ALOHA_NUM_EPISODES}"
    echo "IoTAuth context: people=${IOTAUTH_CONTEXT_PEOPLE}, location=${IOTAUTH_CONTEXT_LOCATION}, time=${IOTAUTH_CONTEXT_TIME}"
    if [ "$TEST_NAME" = "test2" ]; then
        echo "Thresholds: ${TEST2_THRESHOLDS[*]}"
    elif [ "$TEST_NAME" = "test3" ]; then
        echo "Thresholds: ${TEST3_THRESHOLDS[*]}"
        echo "Validity periods: ${TEST3_VALIDITY_PERIODS[*]}"
    fi
    echo "Started: $(date '+%Y-%m-%d %H:%M:%S %Z')"
} > "$OUTPUT_DIR/test_metadata.txt"

echo "====================================================================="
echo "  Automated Context-Based Validity vs. Secure Architecture Latency Testing"
echo "====================================================================="
echo "OpenPI Root : $OPENPI_DIR"
echo "IoTAuth Root: $IOTAUTH_DIR"
echo "Test        : $TEST_NAME"
echo "Auth Mode   : $AUTH_MODE"
echo "Password    : $AUTH_PASSWORD"
echo "Auth Delay  : ${AUTH_DELAY_MS} ms added per Auth101 round trip"
echo "Secure Path : $([ "$SECURE_ACTUATOR_MODE" -eq 1 ] && echo enabled || echo disabled)"
if [ "$TEST_NAME" = "test3" ]; then
    echo "Validities  : ${TEST3_VALIDITY_PERIODS[*]} seconds"
    echo "Thresholds  : ${TEST3_THRESHOLDS[*]}"
    echo "Iterations  : $RUNS runs per grid cell (25 cells, $((25 * RUNS)) total runs)"
else
    echo "Iterations  : $RUNS runs per test condition"
fi
echo "Output Dir  : $OUTPUT_DIR"
echo "====================================================================="

# ─────────────────────────────────────────────────────────────────────────────
# Step 1-4: Local Auth setup (Skipped if --remote)
# ─────────────────────────────────────────────────────────────────────────────
if [ "$AUTH_MODE" = "local" ]; then
    echo ""
    echo "▶️  [Step 1/6] Cleaning existing IoTAuth databases and credentials..."
    cd "$IOTAUTH_DIR/examples"
    ./cleanAll.sh

    echo ""
    echo "▶️  [Step 2/6] Generating credentials for context_based_validity.graph..."
    ./generateAll.sh -g configs/context_based_validity.graph -p "$AUTH_PASSWORD" -lc

    echo ""
    echo "▶️  [Step 3/6] Rebuilding local_auth/testing/validity/ from IoTAuth-generated configs..."
    VALIDITY_BASE="$OPENPI_DIR/sst_config_creds/local_auth/testing/validity"
    IOTAUTH_CONFIGS="$IOTAUTH_DIR/entity/node/example_entities/configs/net1"

    # Wipe the entire validity directory so stale val* folders from old runs are gone
    rm -rf "$VALIDITY_BASE"
    mkdir -p "$VALIDITY_BASE"

    for val in "${VALIDITY_PERIODS[@]}"; do
        dest_dir="$VALIDITY_BASE/val${val}"
        mkdir -p "$dest_dir"

        src_config="$IOTAUTH_CONFIGS/client_val_${val}.config"
        if [ ! -f "$src_config" ]; then
            echo "  ❌ Config not found: $src_config"
            echo "     Did generateAll.sh run successfully?"
            exit 1
        fi

        # Copy IoTAuth-generated config then patch key paths to point to same folder (./)
        cp "$src_config" "$dest_dir/client_val_${val}.config"
        sed -i.bak "s|\"privateKey\":.*|\"privateKey\": \"./Net1.Client_val_${val}Key.pem\"|" \
            "$dest_dir/client_val_${val}.config"
        sed -i.bak "s|\"publicKey\":.*|\"publicKey\": \"./Auth101EntityCert.pem\"|" \
            "$dest_dir/client_val_${val}.config"
        rm -f "$dest_dir/client_val_${val}.config.bak"

        # Copy freshly generated cert and key from IoTAuth
        cp "$IOTAUTH_DIR/entity/auth_certs/Auth101EntityCert.pem" "$dest_dir/"
        cp "$IOTAUTH_DIR/entity/credentials/keys/net1/Net1.Client_val_${val}Key.pem" "$dest_dir/"

        # The secure actuator is a separately registered Servers-group entity. It
        # retrieves the monitor's session key by keyId during accept_secure().
        server_config="$IOTAUTH_CONFIGS/server.config"
        server_key="$IOTAUTH_DIR/entity/credentials/keys/net1/Net1.ServerKey.pem"
        if [ ! -f "$server_config" ] || [ ! -f "$server_key" ]; then
            echo "  ❌ Secure actuator credentials were not generated"
            echo "     Expected: $server_config and $server_key"
            exit 1
        fi
        cp "$server_config" "$dest_dir/server.config"
        sed -i.bak 's|"privateKey":.*|"privateKey": "./Net1.ServerKey.pem"|' \
            "$dest_dir/server.config"
        sed -i.bak 's|"publicKey":.*|"publicKey": "./Auth101EntityCert.pem"|' \
            "$dest_dir/server.config"
        rm -f "$dest_dir/server.config.bak"
        cp "$server_key" "$dest_dir/"
        echo "  ✅ val${val}/  — monitor + secure actuator configs, certs, and keys written"
    done

    echo "✅ Certificates and keys successfully written (${#VALIDITY_PERIODS[@]} periods)."

    echo ""
    echo "▶️  [Step 4/6] Starting Auth101 Java server in background..."
    if lsof -tiTCP:21900 -sTCP:LISTEN >/dev/null 2>&1; then
        echo "⚠️ Port 21900 is in use. Killing existing process..."
        kill -9 $(lsof -tiTCP:21900 -sTCP:LISTEN) 2>/dev/null || true
        sleep 1
    fi

    AUTH_LOG="$OUTPUT_DIR/auth101.log"

    cd "$IOTAUTH_DIR/auth/auth-server"
    nohup java -jar target/auth-server-jar-with-dependencies.jar -p ../properties/exampleAuth101.properties --password="$AUTH_PASSWORD" > "$AUTH_LOG" 2>&1 &
    AUTH_PID=$!
    echo "Auth101 PID: $AUTH_PID. Waiting for port 21900 to open..."

    cleanup() {
        echo ""
        echo "⏹️  Shutting down Auth101 server (PID: $AUTH_PID)..."
        kill -9 "$AUTH_PID" 2>/dev/null || true
        if lsof -tiTCP:21900 -sTCP:LISTEN >/dev/null 2>&1; then
            kill -9 $(lsof -tiTCP:21900 -sTCP:LISTEN) 2>/dev/null || true
        fi
        echo "✅ Cleaned up services."
    }
    trap cleanup EXIT INT TERM

    elapsed=0
    timeout=30
    while ! lsof -tiTCP:21900 -sTCP:LISTEN >/dev/null 2>&1; do
        if [ $elapsed -ge $timeout ]; then
            echo "❌ Error: Timed out waiting for Auth101 server to bind to port 21900."
            cat "$AUTH_LOG"
            exit 1
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    echo "✅ Auth101 server is ready and listening on port 21900."

else
    echo ""
    echo "🌐 [REMOTE AUTH MODE] Skipping local certificate regeneration and local Auth101 server startup."
    echo "✅ Assuming Auth101 is running remotely and config files/certificates are already in place."
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Run simulation iterations across test conditions
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "▶️  [Step 5/6] Executing Docker simulation loops across test conditions..."
cd "$OPENPI_DIR"

COMPOSE_ARGS=(-f examples/aloha_sim/compose.yml)
LOG_DIR="$OPENPI_DIR/data/aloha_sim/token_logs"
if [ "$SECURE_ACTUATOR_MODE" -eq 1 ]; then
    COMPOSE_ARGS+=(--profile secure-actuator)
fi

configure_secure_actuator() {
    if [ "$SECURE_ACTUATOR_MODE" -eq 0 ]; then
        unset SECURE_ACTUATOR_ENABLED ACTUATOR_CONFIG
        return
    fi

    local host_actuator_config
    host_actuator_config="$(dirname "$HOST_CONFIG_PATH")/server.config"
    if [ ! -f "$host_actuator_config" ]; then
        echo "❌ Error: Secure actuator config not found: $host_actuator_config"
        echo "   Generate or install the registered Servers-group entity credentials first."
        exit 1
    fi
    export SECURE_ACTUATOR_ENABLED=1
    export ACTUATOR_CONFIG="$(dirname "$CONFIG_PATH")/server.config"
}

configure_actuator_latency_log() {
    local run_stem="$1"
    if [ "$SECURE_ACTUATOR_MODE" -eq 0 ]; then
        unset ACTUATOR_LATENCY_LOG
        ACTUATOR_LATENCY_LOG_HOST=""
        return
    fi

    ACTUATOR_LATENCY_LOG_HOST="$OUTPUT_DIR/${run_stem}_monitor_actuator.jsonl"
    rm -f "$ACTUATOR_LATENCY_LOG_HOST"
    local output_relative="${OUTPUT_DIR#"$OPENPI_DIR"/}"
    export ACTUATOR_LATENCY_LOG="/app/${output_relative}/${run_stem}_monitor_actuator.jsonl"
}

analyze_run_latency() {
    local source_log="$1"
    local report_file="$2"
    local analyze_args=("$source_log" -o "$report_file")
    if [ "$SECURE_ACTUATOR_MODE" -eq 1 ]; then
        if [ ! -s "$ACTUATOR_LATENCY_LOG_HOST" ]; then
            echo "❌ Error: Secure actuator produced no latency records at $ACTUATOR_LATENCY_LOG_HOST"
            exit 1
        fi
        analyze_args+=(--actuator-log "$ACTUATOR_LATENCY_LOG_HOST")
    fi
    python3 scripts/lamps_2026/analyze_latency.py "${analyze_args[@]}"
}

print_run_latency() {
    local report_file="$1"
    local run_label="$2"
    local metric_label="Average Monitor Latency"
    if [ "$SECURE_ACTUATOR_MODE" -eq 1 ]; then
        metric_label="Average Monitor-Actuator Latency"
    fi
    if grep -i "$metric_label" "$report_file" >/dev/null 2>&1; then
        local latency_value
        latency_value="$(grep -i "$metric_label" "$report_file" | awk '{print $(NF-1)}')"
        echo "✅ ${run_label} completed -> ${metric_label}: ${latency_value} ms"
    else
        echo "❌ Error: Could not find '${metric_label}' in $report_file."
        exit 1
    fi
}

run_simulation() {
    local log_marker
    log_marker="$(mktemp "$OUTPUT_DIR/.token-log-start.XXXXXX")"
    if ! docker compose "${COMPOSE_ARGS[@]}" up --build --abort-on-container-exit; then
        rm -f "$log_marker"
        docker compose "${COMPOSE_ARGS[@]}" down >/dev/null 2>&1 || true
        return 1
    fi
    docker compose "${COMPOSE_ARGS[@]}" down >/dev/null 2>&1 || true
    LATEST_LOG="$(python3 - "$LOG_DIR" "$log_marker" <<'PYEOF'
import pathlib
import sys

log_dir = pathlib.Path(sys.argv[1])
marker_mtime = pathlib.Path(sys.argv[2]).stat().st_mtime_ns
candidates = [
    path for path in log_dir.glob("*.jsonl")
    if path.stat().st_mtime_ns > marker_mtime
]
if candidates:
    print(max(candidates, key=lambda path: path.stat().st_mtime_ns))
PYEOF
)"
    rm -f "$log_marker"
    if [ -z "$LATEST_LOG" ] || [ ! -f "$LATEST_LOG" ]; then
        echo "❌ Error: This simulation did not generate a new token log in $LOG_DIR."
        return 1
    fi
}

metadata_is_compatible() {
    local candidate_metadata="$1"
    local key current_value candidate_value
    local keys=(
        "Runs per condition"
        "Secure actuator"
        "Latency metric"
        "Classification boundary"
        "Episode steps"
        "Episodes"
        "IoTAuth context"
    )
    if [ "$TEST_NAME" = "test2" ] || [ "$TEST_NAME" = "test3" ]; then
        keys+=("Thresholds")
    fi
    if [ "$TEST_NAME" = "test3" ]; then
        keys+=("Validity periods")
    fi
    for key in "${keys[@]}"; do
        current_value="$(sed -n "s/^${key}: //p" "$OUTPUT_DIR/test_metadata.txt" | head -n 1)"
        candidate_value="$(sed -n "s/^${key}: //p" "$candidate_metadata" | head -n 1)"
        if [ -z "$current_value" ] || [ "$current_value" != "$candidate_value" ]; then
            return 1
        fi
    done
    return 0
}

if [ "$TEST_NAME" = "test1" ]; then
    for VAL in "${VALIDITY_PERIODS[@]}"; do
        echo ""
        echo "---------------------------------------------------------------------"
        echo "  Testing Validity Period: ${VAL} Seconds"
        echo "---------------------------------------------------------------------"

        for (( RUN=1; RUN<=RUNS; RUN++ )); do
            echo ""
            echo "🔄 [Validity ${VAL}s | Run ${RUN}/${RUNS}] Launching simulation..."

            # Determine config directory name based on auth mode
            if [ "$AUTH_MODE" = "remote" ]; then
                AUTH_DIR="remote_auth"
            else
                AUTH_DIR="local_auth"
            fi

            # Prepare environment variables for Docker compose
            HOST_CONFIG_PATH="$OPENPI_DIR/sst_config_creds/${AUTH_DIR}/testing/validity/val${VAL}/client_val_${VAL}.config"
            CONFIG_PATH="/app/sst_config_creds/${AUTH_DIR}/testing/validity/val${VAL}/client_val_${VAL}.config"
            if [ ! -f "$HOST_CONFIG_PATH" ]; then
                if [ -f "$OPENPI_DIR/sst_config_creds/${AUTH_DIR}/validity/val${VAL}/client_val_${VAL}.config" ]; then
                    HOST_CONFIG_PATH="$OPENPI_DIR/sst_config_creds/${AUTH_DIR}/validity/val${VAL}/client_val_${VAL}.config"
                    CONFIG_PATH="/app/sst_config_creds/${AUTH_DIR}/validity/val${VAL}/client_val_${VAL}.config"
                fi
            fi
            if [ ! -f "$HOST_CONFIG_PATH" ]; then
                echo "❌ Error: Client config file for validity period ${VAL}s not found on host machine at:"
                echo "   $HOST_CONFIG_PATH"
                echo "   Please ensure ${AUTH_DIR} config files are placed in sst_config_creds/${AUTH_DIR}/."
                exit 1
            fi
            export MONITOR_CONFIG="$CONFIG_PATH"
            export OPENPI_MOTION_THRESHOLD="0"
            export ALOHA_MAX_EPISODE_STEPS="${ALOHA_MAX_EPISODE_STEPS:-300}"
            export ALOHA_NUM_EPISODES="${ALOHA_NUM_EPISODES:-1}"
            export TEST_VALIDITY_PERIOD="${VAL}"
            export TEST_RUN_ITERATION="${RUN}"
            export TEST_TOTAL_RUNS="${RUNS}"
            configure_secure_actuator
            configure_actuator_latency_log "val_${VAL}s_run_${RUN}"

            # Run Docker simulation with --build flag and auto-exit when client finishes
            run_simulation

            # Copy the source .jsonl log into the run output directory for archival
            echo "📂 Copying source log $(basename "$LATEST_LOG") into run output folder..."
            cp "$LATEST_LOG" "$OUTPUT_DIR/"

            REPORT_FILE="$OUTPUT_DIR/val_${VAL}s_run_${RUN}.txt"
            echo "📊 Analyzing latency for run ${RUN} from log: $(basename "$LATEST_LOG")..."

            analyze_run_latency "$LATEST_LOG" "$REPORT_FILE"
            print_run_latency "$REPORT_FILE" "Run ${RUN}"

            # Small delay between runs to let sockets clear
            sleep 2
        done
    done
elif [ "$TEST_NAME" = "test2" ]; then
    VAL="1"  # Fixed validity period of 1s as requested by user
    for THRESH in "${TEST2_THRESHOLDS[@]}"; do
        echo ""
        echo "---------------------------------------------------------------------"
        echo "  Testing Motion Threshold: ${THRESH} (Fixed Validity: ${VAL}s)"
        echo "---------------------------------------------------------------------"

        for (( RUN=1; RUN<=RUNS; RUN++ )); do
            echo ""
            echo "🔄 [Threshold ${THRESH} | Run ${RUN}/${RUNS}] Launching simulation..."

            if [ "$AUTH_MODE" = "remote" ]; then
                AUTH_DIR="remote_auth"
            else
                AUTH_DIR="local_auth"
            fi

            HOST_CONFIG_PATH="$OPENPI_DIR/sst_config_creds/${AUTH_DIR}/testing/validity/val${VAL}/client_val_${VAL}.config"
            CONFIG_PATH="/app/sst_config_creds/${AUTH_DIR}/testing/validity/val${VAL}/client_val_${VAL}.config"
            if [ ! -f "$HOST_CONFIG_PATH" ]; then
                if [ -f "$OPENPI_DIR/sst_config_creds/${AUTH_DIR}/validity/val${VAL}/client_val_${VAL}.config" ]; then
                    HOST_CONFIG_PATH="$OPENPI_DIR/sst_config_creds/${AUTH_DIR}/validity/val${VAL}/client_val_${VAL}.config"
                    CONFIG_PATH="/app/sst_config_creds/${AUTH_DIR}/validity/val${VAL}/client_val_${VAL}.config"
                fi
            fi
            if [ ! -f "$HOST_CONFIG_PATH" ]; then
                echo "❌ Error: Client config file for validity period ${VAL}s not found on host machine at:"
                echo "   $HOST_CONFIG_PATH"
                echo "   Please ensure ${AUTH_DIR} config files are placed in sst_config_creds/${AUTH_DIR}/."
                exit 1
            fi
            export MONITOR_CONFIG="$CONFIG_PATH"
            export OPENPI_MOTION_THRESHOLD="$THRESH"
            export ALOHA_MAX_EPISODE_STEPS="${ALOHA_MAX_EPISODE_STEPS:-300}"
            export ALOHA_NUM_EPISODES="${ALOHA_NUM_EPISODES:-1}"
            export TEST_VALIDITY_PERIOD="${VAL}"
            export TEST_RUN_ITERATION="${RUN}"
            export TEST_TOTAL_RUNS="${RUNS}"
            configure_secure_actuator
            configure_actuator_latency_log "thresh_${THRESH}_run_${RUN}"

            run_simulation

            echo "📂 Copying source log $(basename "$LATEST_LOG") into run output folder..."
            cp "$LATEST_LOG" "$OUTPUT_DIR/"

            REPORT_FILE="$OUTPUT_DIR/thresh_${THRESH}_run_${RUN}.txt"
            echo "📊 Analyzing latency and bypass rate for run ${RUN} from log: $(basename "$LATEST_LOG")..."

            analyze_run_latency "$LATEST_LOG" "$REPORT_FILE"
            print_run_latency "$REPORT_FILE" "Run ${RUN}"

            sleep 2
        done
    done
elif [ "$TEST_NAME" = "test3" ]; then
    TOTAL_CELLS=$((${#TEST3_THRESHOLDS[@]} * ${#TEST3_VALIDITY_PERIODS[@]}))
    TOTAL_SIMULATIONS=$((TOTAL_CELLS * RUNS))
    CELL_INDEX=0

    for THRESH in "${TEST3_THRESHOLDS[@]}"; do
        for VAL in "${TEST3_VALIDITY_PERIODS[@]}"; do
            CELL_INDEX=$((CELL_INDEX + 1))
            echo ""
            echo "---------------------------------------------------------------------"
            echo "  Cell ${CELL_INDEX}/${TOTAL_CELLS}: Threshold ${THRESH} | Validity ${VAL}s"
            echo "---------------------------------------------------------------------"

            for (( RUN=1; RUN<=RUNS; RUN++ )); do
                OVERALL_RUN=$((((CELL_INDEX - 1) * RUNS) + RUN))
                echo ""
                echo "🔄 [Threshold ${THRESH} | Validity ${VAL}s | Run ${RUN}/${RUNS} | Overall ${OVERALL_RUN}/${TOTAL_SIMULATIONS}] Launching simulation..."

                if [ "$AUTH_MODE" = "remote" ]; then
                    AUTH_DIR="remote_auth"
                else
                    AUTH_DIR="local_auth"
                fi

                HOST_CONFIG_PATH="$OPENPI_DIR/sst_config_creds/${AUTH_DIR}/testing/validity/val${VAL}/client_val_${VAL}.config"
                CONFIG_PATH="/app/sst_config_creds/${AUTH_DIR}/testing/validity/val${VAL}/client_val_${VAL}.config"
                if [ ! -f "$HOST_CONFIG_PATH" ]; then
                    if [ -f "$OPENPI_DIR/sst_config_creds/${AUTH_DIR}/validity/val${VAL}/client_val_${VAL}.config" ]; then
                        HOST_CONFIG_PATH="$OPENPI_DIR/sst_config_creds/${AUTH_DIR}/validity/val${VAL}/client_val_${VAL}.config"
                        CONFIG_PATH="/app/sst_config_creds/${AUTH_DIR}/validity/val${VAL}/client_val_${VAL}.config"
                    fi
                fi
                if [ ! -f "$HOST_CONFIG_PATH" ]; then
                    echo "❌ Error: Client config file for validity period ${VAL}s not found on host machine at:"
                    echo "   $HOST_CONFIG_PATH"
                    echo "   Please ensure ${AUTH_DIR} config files are placed in sst_config_creds/${AUTH_DIR}/."
                    exit 1
                fi

                export MONITOR_CONFIG="$CONFIG_PATH"
                export OPENPI_MOTION_THRESHOLD="$THRESH"
                export ALOHA_MAX_EPISODE_STEPS="${ALOHA_MAX_EPISODE_STEPS:-300}"
                export ALOHA_NUM_EPISODES="${ALOHA_NUM_EPISODES:-1}"
                export TEST_VALIDITY_PERIOD="$VAL"
                export TEST_RUN_ITERATION="$RUN"
                export TEST_TOTAL_RUNS="$RUNS"
                configure_secure_actuator
                configure_actuator_latency_log "val_${VAL}s_thresh_${THRESH}_run_${RUN}"

                run_simulation

                ARCHIVED_LOG="$OUTPUT_DIR/val_${VAL}s_thresh_${THRESH}_run_${RUN}.jsonl"
                echo "📂 Archiving source log as $(basename "$ARCHIVED_LOG")..."
                cp "$LATEST_LOG" "$ARCHIVED_LOG"

                REPORT_FILE="$OUTPUT_DIR/val_${VAL}s_thresh_${THRESH}_run_${RUN}.txt"
                echo "📊 Analyzing latency for threshold ${THRESH}, validity ${VAL}s, run ${RUN}..."
                analyze_run_latency "$LATEST_LOG" "$REPORT_FILE"
                print_run_latency "$REPORT_FILE" "Run ${RUN}"

                sleep 2
            done
        done
    done
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 6: Aggregation and Graph Plotting
# Both input reports and output graph/CSV land in the same OUTPUT_DIR
# For Test 1: if both local and remote result dirs are populated, generate two
# separate comparative plots (avg latency and worst-case, each local vs remote).
# Otherwise fall back to the legacy single-mode combined graph.
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "▶️  [Step 6/6] Aggregating results and generating matplotlib graph..."

if [ "$TEST_NAME" = "test1" ]; then
    TEST1_BASE="$OPENPI_DIR/test_reports/test1"

    # Determine the "other" auth mode directory
    if [ "$AUTH_MODE" = "local" ]; then
        OTHER_MODE="remote"
    else
        OTHER_MODE="local"
    fi

    OTHER_BASE="$TEST1_BASE/$OTHER_MODE"

    # Find the latest timestamped run dir for the other auth mode (if any).
    # A run dir is considered "complete" only when validity_vs_latency.csv exists —
    # that file is written at the end of a successful plot_results.py run, so its
    # presence guarantees the run fully finished (not just partially executed).
    OTHER_CSV=""
    if [ -d "$OTHER_BASE" ]; then
        for dir in $(ls -dt "$OTHER_BASE"/*/  2>/dev/null); do
            dir="${dir%/}"   # strip trailing slash
            if [ -f "$dir/validity_vs_latency.csv" ] \
                && [ -f "$dir/test_metadata.txt" ] \
                && metadata_is_compatible "$dir/test_metadata.txt"; then
                OTHER_CSV="$dir/validity_vs_latency.csv"
                break
            fi
        done
    fi

    if [ -n "$OTHER_CSV" ]; then
        echo "🔀 Both local and remote Test 1 data found."
        echo "   Primary ($AUTH_MODE): $OUTPUT_DIR"
        echo "   Compare ($OTHER_MODE): $OTHER_CSV"
        echo "   → Generating separate comparative plots (avg latency & worst-case)..."
        python3 scripts/lamps_2026/plot_results.py \
            --reports-dir "$OUTPUT_DIR" \
            --compare-csv "$OTHER_CSV" \
            --bypass-mode "$BYPASS_MODE" \
            "${PLOT_ARGS[@]}"
    else
        echo "ℹ️  Only $AUTH_MODE Test 1 data found. Using single-mode (combined) graph."
        python3 scripts/lamps_2026/plot_results.py --reports-dir "$OUTPUT_DIR" --bypass-mode "$BYPASS_MODE" --no-auto-compare "${PLOT_ARGS[@]}"
    fi
elif [ "$TEST_NAME" = "test2" ]; then
    TEST2_BASE="$OPENPI_DIR/test_reports/test2"

    if [ "$AUTH_MODE" = "local" ]; then
        OTHER_MODE="remote"
    else
        OTHER_MODE="local"
    fi

    OTHER_BASE="$TEST2_BASE/$OTHER_MODE/$BYPASS_MODE"
    OTHER_CSV=""
    if [ -d "$OTHER_BASE" ]; then
        for dir in $(ls -dt "$OTHER_BASE"/*/  2>/dev/null); do
            dir="${dir%/}"
            if [ -f "$dir/threshold_vs_latency.csv" ] \
                && [ -f "$dir/test_metadata.txt" ] \
                && metadata_is_compatible "$dir/test_metadata.txt"; then
                OTHER_CSV="$dir/threshold_vs_latency.csv"
                break
            fi
        done
    fi

    if [ -n "$OTHER_CSV" ]; then
        echo "🔀 Both local and remote Test 2 data found."
        echo "   Primary ($AUTH_MODE): $OUTPUT_DIR"
        echo "   Compare ($OTHER_MODE): $OTHER_CSV"
        echo "   → Generating separate comparative plots (avg latency & worst-case)..."
        python3 scripts/lamps_2026/plot_results.py \
            --reports-dir "$OUTPUT_DIR" \
            --compare-csv "$OTHER_CSV" \
            --bypass-mode "$BYPASS_MODE" \
            "${PLOT_ARGS[@]}"
    else
        echo "ℹ️  Only $AUTH_MODE Test 2 data found. Generating single-mode graphs."
        python3 scripts/lamps_2026/plot_results.py --reports-dir "$OUTPUT_DIR" --bypass-mode "$BYPASS_MODE" --no-auto-compare "${PLOT_ARGS[@]}"
    fi
elif [ "$TEST_NAME" = "test3" ]; then
    TEST3_BASE="$OPENPI_DIR/test_reports/test3"

    if [ "$AUTH_MODE" = "local" ]; then
        OTHER_MODE="remote"
    else
        OTHER_MODE="local"
    fi

    OTHER_BASE="$TEST3_BASE/$OTHER_MODE"
    OTHER_CSV=""
    if [ -d "$OTHER_BASE" ]; then
        for dir in $(ls -dt "$OTHER_BASE"/*/ 2>/dev/null); do
            dir="${dir%/}"
            if [ -f "$dir/validity_threshold_latency.csv" ] \
                && [ -f "$dir/test_metadata.txt" ] \
                && metadata_is_compatible "$dir/test_metadata.txt"; then
                OTHER_CSV="$dir/validity_threshold_latency.csv"
                break
            fi
        done
    fi

    if [ -n "$OTHER_CSV" ]; then
        echo "🌡️  Both local and remote Test 3 data found."
        echo "   Primary ($AUTH_MODE): $OUTPUT_DIR"
        echo "   Compare ($OTHER_MODE): $OTHER_CSV"
        echo "   → Generating monitor-latency heatmaps with shared temperature scale..."
        python3 scripts/lamps_2026/plot_results.py \
            --reports-dir "$OUTPUT_DIR" \
            --test-name test3 \
            --compare-csv "$OTHER_CSV" \
            "${PLOT_ARGS[@]}"
    else
        echo "🌡️  Aggregating the 5x5 Test 3 grid and generating the monitor-latency heatmap..."
        python3 scripts/lamps_2026/plot_results.py \
            --reports-dir "$OUTPUT_DIR" \
            --test-name test3 \
            "${PLOT_ARGS[@]}"
    fi
else
    python3 scripts/lamps_2026/plot_results.py --reports-dir "$OUTPUT_DIR" --bypass-mode "$BYPASS_MODE" "${PLOT_ARGS[@]}"
fi

echo ""
echo "====================================================================="
echo "🎉 All tests complete! Summary table and graph generated in:"
echo "   $OUTPUT_DIR/"
echo "====================================================================="
