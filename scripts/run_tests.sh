#!/usr/bin/env bash

set -euo pipefail

# Ensure standard docker and brew binary paths are available
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin"

# Get absolute paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENPI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IOTAUTH_DIR="$(cd "$OPENPI_DIR/../iotauth" && pwd)"

# Automatically activate virtual environment if present
if [ -f "$OPENPI_DIR/.venv/bin/activate" ]; then
    echo "🐍 Activating Python virtual environment at $OPENPI_DIR/.venv..."
    source "$OPENPI_DIR/.venv/bin/activate"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# Usage:
#   ./scripts/run_context_validity_tests.sh --test1 --local [--password <pw>] [--runs <n>]
#   ./scripts/run_context_validity_tests.sh --test1 --remote [--password <pw>] [--runs <n>]
# ─────────────────────────────────────────────────────────────────────────────
TEST_NAME=""
AUTH_MODE=""
AUTH_PASSWORD="1234"
RUNS="5"
BYPASS_MODE="still"

if [ "$#" -eq 0 ]; then
    echo "❌ Error: No arguments provided."
    echo "Usage: $0 --test1|--test2 --local|--remote [--password <pw>] [--runs <n>] [--bypass-mode still|active]"
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
            echo "❌ Error: --test3 is not implemented yet."
            exit 1
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
                echo "❌ Error: --bypass-mode requires a value (still or active)."
                exit 1
            fi
            ;;
        *)
            echo "❌ Error: Unknown argument '$1'"
            echo "Usage: $0 --test1|--test2 --local|--remote [--password <pw>] [--runs <n>] [--bypass-mode still|active]"
            exit 1
            ;;
    esac
done

# Validate required flags
if [ -z "$TEST_NAME" ]; then
    echo "❌ Error: A test flag is required (e.g. --test1 or --test2)."
    echo "Usage: $0 --test1|--test2 --local|--remote [--password <pw>] [--runs <n>] [--bypass-mode still|active]"
    exit 1
fi

if [ -z "$AUTH_MODE" ]; then
    echo "❌ Error: An auth mode flag is required (--local or --remote)."
    echo "Usage: $0 --test1|--test2 --local|--remote [--password <pw>] [--runs <n>] [--bypass-mode still|active]"
    exit 1
fi

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

# Timestamped run output directory (reports + graphs + copied source data all land here)
if [ "$TEST_NAME" = "test2" ]; then
    OUTPUT_DIR="$OPENPI_DIR/test_reports/$TEST_NAME/$AUTH_MODE/$BYPASS_MODE/$RUN_TIMESTAMP"
else
    OUTPUT_DIR="$OPENPI_DIR/test_reports/$TEST_NAME/$AUTH_MODE/$RUN_TIMESTAMP"
fi

# Create the full test_reports hierarchy (including future test placeholders)
mkdir -p "$OUTPUT_DIR"
mkdir -p "$OPENPI_DIR/test_reports/test2/local/still"
mkdir -p "$OPENPI_DIR/test_reports/test2/local/active"
mkdir -p "$OPENPI_DIR/test_reports/test2/remote/still"
mkdir -p "$OPENPI_DIR/test_reports/test2/remote/active"
mkdir -p "$OPENPI_DIR/test_reports/test3/local"
mkdir -p "$OPENPI_DIR/test_reports/test3/remote"

echo "====================================================================="
echo "  Automated Context-Based Validity vs. Monitor Latency Testing"
echo "====================================================================="
echo "OpenPI Root : $OPENPI_DIR"
echo "IoTAuth Root: $IOTAUTH_DIR"
echo "Test        : $TEST_NAME"
echo "Auth Mode   : $AUTH_MODE"
echo "Password    : $AUTH_PASSWORD"
echo "Iterations  : $RUNS runs per validity period (${VALIDITY_PERIODS[*]}s)"
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
        sed -i "s|\"privateKey\":.*|\"privateKey\": \"./Net1.Client_val_${val}Key.pem\"|" \
            "$dest_dir/client_val_${val}.config"
        sed -i "s|\"publicKey\":.*|\"publicKey\": \"./Auth101EntityCert.pem\"|" \
            "$dest_dir/client_val_${val}.config"

        # Copy freshly generated cert and key from IoTAuth
        cp "$IOTAUTH_DIR/entity/auth_certs/Auth101EntityCert.pem" "$dest_dir/"
        cp "$IOTAUTH_DIR/entity/credentials/keys/net1/Net1.Client_val_${val}Key.pem" "$dest_dir/"
        echo "  ✅ val${val}/  — config (from IoTAuth) + cert + key written"
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
# Step 5: Run simulation iterations across validity periods
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "▶️  [Step 5/6] Executing Docker simulation loops across validity periods..."
cd "$OPENPI_DIR"

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
            CONFIG_PATH="/app/sst_config_creds/${AUTH_DIR}/testing/validity/val${VAL}/client_val_${VAL}.config"
            if [ ! -f "$OPENPI_DIR/sst_config_creds/${AUTH_DIR}/testing/validity/val${VAL}/client_val_${VAL}.config" ]; then
                if [ -f "$OPENPI_DIR/sst_config_creds/${AUTH_DIR}/validity/val${VAL}/client_val_${VAL}.config" ]; then
                    CONFIG_PATH="/app/sst_config_creds/${AUTH_DIR}/validity/val${VAL}/client_val_${VAL}.config"
                fi
            fi
            export MONITOR_CONFIG="$CONFIG_PATH"
            export OPENPI_MOTION_THRESHOLD="0"
            export ALOHA_MAX_EPISODE_STEPS="${ALOHA_MAX_EPISODE_STEPS:-300}"
            export ALOHA_NUM_EPISODES="${ALOHA_NUM_EPISODES:-1}"
            export TEST_VALIDITY_PERIOD="${VAL}"
            export TEST_RUN_ITERATION="${RUN}"
            export TEST_TOTAL_RUNS="${RUNS}"

            # Run Docker simulation with --build flag and auto-exit when client finishes
            docker compose -f examples/aloha_sim/compose.yml up --build --abort-on-container-exit
            docker compose -f examples/aloha_sim/compose.yml down >/dev/null 2>&1 || true

            # Find latest generated jsonl log file
            LOG_DIR="$OPENPI_DIR/data/aloha_sim/token_logs"
            LATEST_LOG="$(ls -t "$LOG_DIR"/*.jsonl 2>/dev/null | head -n 1 || true)"

            if [ -z "$LATEST_LOG" ] || [ ! -f "$LATEST_LOG" ]; then
                echo "❌ Error: No .jsonl token log file generated in $LOG_DIR."
                exit 1
            fi

            # Copy the source .jsonl log into the run output directory for archival
            echo "📂 Copying source log $(basename "$LATEST_LOG") into run output folder..."
            cp "$LATEST_LOG" "$OUTPUT_DIR/"

            REPORT_FILE="$OUTPUT_DIR/val_${VAL}s_run_${RUN}.txt"
            echo "📊 Analyzing latency for run ${RUN} from log: $(basename "$LATEST_LOG")..."

            python3 scripts/analyze_latency.py "$LATEST_LOG" -o "$REPORT_FILE"

            # Extract and display the average monitor latency from the report
            if grep -i "Average Monitor Latency" "$REPORT_FILE" >/dev/null 2>&1; then
                LAT_VAL="$(grep -i "Average Monitor Latency" "$REPORT_FILE" | awk '{print $(NF-1)}')"
                echo "✅ Run ${RUN} completed -> Average Monitor Latency: ${LAT_VAL} ms"
            else
                echo "⚠️ Warning: Could not find 'Average Monitor Latency' in report."
            fi

            # Small delay between runs to let sockets clear
            sleep 2
        done
    done
elif [ "$TEST_NAME" = "test2" ]; then
    THRESHOLDS=(0.0000 0.4517 0.5413 0.6666 0.8524)
    VAL="1"  # Fixed validity period of 1s as requested by user
    for THRESH in "${THRESHOLDS[@]}"; do
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

            CONFIG_PATH="/app/sst_config_creds/${AUTH_DIR}/testing/validity/val${VAL}/client_val_${VAL}.config"
            if [ ! -f "$OPENPI_DIR/sst_config_creds/${AUTH_DIR}/testing/validity/val${VAL}/client_val_${VAL}.config" ]; then
                if [ -f "$OPENPI_DIR/sst_config_creds/${AUTH_DIR}/validity/val${VAL}/client_val_${VAL}.config" ]; then
                    CONFIG_PATH="/app/sst_config_creds/${AUTH_DIR}/validity/val${VAL}/client_val_${VAL}.config"
                fi
            fi
            export MONITOR_CONFIG="$CONFIG_PATH"
            export OPENPI_MOTION_THRESHOLD="$THRESH"
            export ALOHA_MAX_EPISODE_STEPS="${ALOHA_MAX_EPISODE_STEPS:-300}"
            export ALOHA_NUM_EPISODES="${ALOHA_NUM_EPISODES:-1}"
            export TEST_VALIDITY_PERIOD="${VAL}"
            export TEST_RUN_ITERATION="${RUN}"
            export TEST_TOTAL_RUNS="${RUNS}"

            docker compose -f examples/aloha_sim/compose.yml up --build --abort-on-container-exit
            docker compose -f examples/aloha_sim/compose.yml down >/dev/null 2>&1 || true

            LOG_DIR="$OPENPI_DIR/data/aloha_sim/token_logs"
            LATEST_LOG="$(ls -t "$LOG_DIR"/*.jsonl 2>/dev/null | head -n 1 || true)"

            if [ -z "$LATEST_LOG" ] || [ ! -f "$LATEST_LOG" ]; then
                echo "❌ Error: No .jsonl token log file generated in $LOG_DIR."
                exit 1
            fi

            echo "📂 Copying source log $(basename "$LATEST_LOG") into run output folder..."
            cp "$LATEST_LOG" "$OUTPUT_DIR/"

            REPORT_FILE="$OUTPUT_DIR/thresh_${THRESH}_run_${RUN}.txt"
            echo "📊 Analyzing latency and bypass rate for run ${RUN} from log: $(basename "$LATEST_LOG")..."

            python3 scripts/analyze_latency.py "$LATEST_LOG" -o "$REPORT_FILE"

            if grep -i "Average Monitor Latency" "$REPORT_FILE" >/dev/null 2>&1; then
                LAT_VAL="$(grep -i "Average Monitor Latency" "$REPORT_FILE" | awk '{print $(NF-1)}')"
                echo "✅ Run ${RUN} completed -> Average Monitor Latency: ${LAT_VAL} ms"
            else
                echo "⚠️ Warning: Could not find 'Average Monitor Latency' in report."
            fi

            sleep 2
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
            if [ -f "$dir/validity_vs_latency.csv" ]; then
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
        python3 scripts/plot_results.py \
            --reports-dir "$OUTPUT_DIR" \
            --compare-csv "$OTHER_CSV" \
            --bypass-mode "$BYPASS_MODE"
    else
        echo "ℹ️  Only $AUTH_MODE Test 1 data found. Using single-mode (combined) graph."
        python3 scripts/plot_results.py --reports-dir "$OUTPUT_DIR" --bypass-mode "$BYPASS_MODE"
    fi
elif [ "$TEST_NAME" = "test2" ]; then
    TEST2_BASE="$OPENPI_DIR/test_reports/test2"

    if [ "$AUTH_MODE" = "local" ]; then
        OTHER_MODE="remote"
    else
        OTHER_MODE="local"
    fi

    OTHER_BASE="$TEST2_BASE/$OTHER_MODE"
    OTHER_CSV=""
    if [ -d "$OTHER_BASE" ]; then
        for dir in $(ls -dt "$OTHER_BASE"/*/  2>/dev/null); do
            dir="${dir%/}"
            if [ -f "$dir/threshold_vs_latency.csv" ]; then
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
        python3 scripts/plot_results.py \
            --reports-dir "$OUTPUT_DIR" \
            --compare-csv "$OTHER_CSV" \
            --bypass-mode "$BYPASS_MODE" \
            "${@:3}"
    else
        echo "ℹ️  Only $AUTH_MODE Test 2 data found. Generating single-mode graphs."
        python3 scripts/plot_results.py --reports-dir "$OUTPUT_DIR" --bypass-mode "$BYPASS_MODE" "${@:3}"
    fi
else
    python3 scripts/plot_results.py --reports-dir "$OUTPUT_DIR" --bypass-mode "$BYPASS_MODE"
fi

echo ""
echo "====================================================================="
echo "🎉 All tests complete! Summary table and graph generated in:"
echo "   $OUTPUT_DIR/"
echo "====================================================================="
