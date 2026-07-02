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

AUTH_PASSWORD="${1:-1234}"
RUNS="${2:-5}"
VALIDITY_PERIODS=(1 3 5 7)

echo "====================================================================="
echo "  Automated Context-Based Validity vs. Monitor Latency Testing"
echo "====================================================================="
echo "OpenPI Root : $OPENPI_DIR"
echo "IoTAuth Root: $IOTAUTH_DIR"
echo "Password    : $AUTH_PASSWORD"
echo "Iterations  : $RUNS runs per validity period (${VALIDITY_PERIODS[*]}s)"
echo "====================================================================="

# Step 1: Clean and generate IoTAuth credentials & DB
echo ""
echo "▶️  [Step 1/6] Cleaning existing IoTAuth databases and credentials..."
cd "$IOTAUTH_DIR/examples"
./cleanAll.sh

echo ""
echo "▶️  [Step 2/6] Generating credentials for context_based_validity.graph..."
./generateAll.sh -g configs/context_based_validity.graph -p "$AUTH_PASSWORD" -lc

# Step 2: Copy certificates and keys to OpenPI
echo ""
echo "▶️  [Step 3/6] Copying certificates and client keys directly to validity folders..."
for val in "${VALIDITY_PERIODS[@]}"; do
    dest_dir="$OPENPI_DIR/sst_config_creds/local_auth/testing/validity/val${val}"
    mkdir -p "$dest_dir"
    cp "$IOTAUTH_DIR/entity/auth_certs/Auth101EntityCert.pem" "$dest_dir/"
    cp "$IOTAUTH_DIR/entity/credentials/keys/net1/Net1.Client_val_${val}Key.pem" "$dest_dir/"
done

echo "✅ Certificates and keys successfully copied."

# Step 3: Start Java Auth101 Server
echo ""
echo "▶️  [Step 4/6] Starting Auth101 Java server in background..."
# Kill any existing process on port 21900
if lsof -tiTCP:21900 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "⚠️ Port 21900 is in use. Killing existing process..."
    kill -9 $(lsof -tiTCP:21900 -sTCP:LISTEN) 2>/dev/null || true
    sleep 1
fi

AUTH_LOG="$OPENPI_DIR/latency_reports/auth101.log"
mkdir -p "$OPENPI_DIR/latency_reports"

cd "$IOTAUTH_DIR/auth/auth-server"
nohup java -jar target/auth-server-jar-with-dependencies.jar -p ../properties/exampleAuth101.properties --password="$AUTH_PASSWORD" > "$AUTH_LOG" 2>&1 &
AUTH_PID=$!
echo "Auth101 PID: $AUTH_PID. Waiting for port 21900 to open..."

# Ensure we kill Auth101 on script exit or interrupt
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

# Poll port 21900 for readiness
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

# Step 4: Run simulation iterations across validity periods
echo ""
echo "▶️  [Step 5/6] Executing Docker simulation loops across validity periods..."
cd "$OPENPI_DIR"

# Clean any previous temporary report files
rm -f "$OPENPI_DIR/latency_reports"/val_*s_run_*.txt

for VAL in "${VALIDITY_PERIODS[@]}"; do
    echo ""
    echo "---------------------------------------------------------------------"
    echo "  Testing Validity Period: ${VAL} Seconds"
    echo "---------------------------------------------------------------------"
    
    for (( RUN=1; RUN<=RUNS; RUN++ )); do
        echo ""
        echo "🔄 [Validity ${VAL}s | Run ${RUN}/${RUNS}] Launching simulation..."
        
        # Prepare environment variables for Docker compose
        export MONITOR_CONFIG="/app/sst_config_creds/local_auth/testing/validity/val${VAL}/client_val_${VAL}.config"
        export OPENPI_MOTION_THRESHOLD="0"
        export ALOHA_MAX_EPISODE_STEPS="${ALOHA_MAX_EPISODE_STEPS:-50}"
        export ALOHA_NUM_EPISODES="${ALOHA_NUM_EPISODES:-1}"
        
        # Run Docker simulation with --build flag
        docker compose -f examples/aloha_sim/compose.yml up --build
        
        # Find latest generated jsonl log file
        LOG_DIR="$OPENPI_DIR/data/aloha_sim/token_logs"
        LATEST_LOG="$(ls -t "$LOG_DIR"/*.jsonl 2>/dev/null | head -n 1 || true)"
        
        if [ -z "$LATEST_LOG" ] || [ ! -f "$LATEST_LOG" ]; then
            echo "❌ Error: No .jsonl token log file generated in $LOG_DIR."
            exit 1
        fi
        
        REPORT_FILE="$OPENPI_DIR/latency_reports/val_${VAL}s_run_${RUN}.txt"
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

# Step 5: Aggregation and Graph Plotting
echo ""
echo "▶️  [Step 6/6] Aggregating results and generating matplotlib graph..."
python3 scripts/test_validity_latency.py --aggregate-reports-dir latency_reports --runs "$RUNS" --validities "${VALIDITY_PERIODS[@]}"

echo ""
echo "====================================================================="
echo "🎉 All tests complete! Summary table and graph generated in:"
echo "   $OPENPI_DIR/latency_test_results/"
echo "====================================================================="
