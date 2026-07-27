# OpenPI Action & Security Monitor (`openpi_monitor.py`)

`openpi_monitor.py` is the **Continuous Kinematic Gatekeeper and Security Mediator** for the OpenPI Vision-Language-Action ($\pi_0$-FAST) control loop. As the core runtime engine of our secure architecture (see [`secure_architecture_design.md`](secure_architecture_design.md)), it intercepts decoded continuous action chunks, performs Joint Motion Analysis (`intra_score` and `inter_score`) across the 14 ALOHA joints, negotiates demand-driven cryptographic session keys via IoTAuth, and routes commands over a dual-channel TCP gateway while tracking high-precision latency.

---

## 1. Key Features & Architectural Role

1. **Continuous Kinematic Motion Analysis**:
   - While offline analysis (`interpret_fast_tokens.py`) relies on frequency-space token proxies, `openpi_monitor.py` evaluates the **decoded continuous float action array (`actions[:, :14]`)** representing the 6 arm joints + 1 gripper per arm of the ALOHA bimanual robot.
   - Evaluates the first $H$ actions (`execution_horizon = 10` by default) using two normalized kinematic metrics:
     - **Intra-chunk Peak-to-Peak (`intra_score`)**: The maximum joint displacement over the predicted horizon relative to each joint's physical range ($[ \text{max}_{t=0..H-1} a_{t,d} - \text{min}_{t=0..H-1} a_{t,d} ] / \text{range}_d$).
     - **Inter-chunk Displacement (`inter_score`)**: The maximum jump between the robot's current observed state (or previous valid action) and the first action of the incoming chunk ($[ |a_{0,d} - \text{ref}_d| ] / \text{range}_d$).
   - The overall kinematic magnitude is `combined_score = max(intra_score, inter_score)`. If `combined_score >= motion_threshold` (default `0.01`), the chunk is classified as **`SIGA` (Significant Action)**; otherwise, it is classified as **`INSIGA` (Insignificant Action)**.

2. **IoTAuth Session Key Caching & Renewal**:
   - For `SIGA` chunks, the monitor verifies whether a valid IoTAuth session key ($K$) is cached. It checks both:
     - **Absolute Validity ($V_{abs}$)**: Whether current wall-clock time exceeds key expiration from issuance ($t_{current} \geq t_{grant} + V_{abs}$).
     - **Relative Validity ($V_{rel}$)**: Whether elapsed time since the key's first communication use exceeds relative lifespan ($t_{current} \geq t_{first\_use} + V_{rel}$).
   - If missing or expired, the monitor opens a secure socket to the IoTAuth Auth Server using its entity configuration (`client.config`), negotiates a fresh symmetric session key, and caches it.
   - **Idle Efficiency**: If the robot is idle (`INSIGA`) and a cached key expires, the monitor does **not** proactively renew it. It waits until the next rising edge to `ACTIVE`/`SIGA`, eliminating unnecessary network overhead during pauses.

3. **Dual-Channel Secure Gateway Transport**:
   - Interacts with `secure_actuator_gateway.py` via two dedicated transports:
     - **Secure Channel (`SecureActuatorClient`, TCP Port 21100)**: Used for all `SIGA` commands. Actions are serialized, encrypted, and authenticated using the active IoTAuth session key.
     - **Plaintext Channel (`InsigaActuatorClient`, TCP Port 21102)**: Used for `INSIGA` micro-adjustments beneath threshold. Bypasses encryption over trusted local IPC to minimize latency when the robot is nearly stationary.
   - **Global Sequence Synchronization**: Both channels share one monotonically increasing sequence counter (`action_record_id`). The actuator verifies sequence ordering on every tick and immediately fails closed (emergency stop) if any record ID is missing, duplicated, skipped, or out-of-order.

4. **Fail-Closed Compatibility Fallback**:
   - If an action requires authentication (`SIGA`) but negotiation fails or the session key is rejected, `process_actions()` blocks the incoming command and returns a static zero-motion chunk based on `last_valid_action` (or the initial reference pose). This ensures the physical robot maintains a safe hold state rather than executing erratic or unauthenticated trajectories.

5. **Precision Latency Tracking**:
   - Starts timer `monitor_start_ms` precisely when decoded motion evaluation begins and passes `observation_timestamp_ms` across the socket boundary. The actuator logs when the action reaches the driver handoff boundary, yielding exact end-to-end `monitor_actuator_ms` measurements across both secure and plaintext paths.

---

## 2. Prerequisites & Environment Setup

### 1. Python Virtual Environment
You can run this monitor inside the repository's `.venv` or any lightweight Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install numpy cryptography typing_extensions
```

### 2. Docker & Simulation Requirements
- **Offline Analysis / Log Replay**: Docker is not required when testing against static token logs (`--log-file pi0_fast_tokens*.jsonl`).
- **Live Online Execution**: Docker must be running so the VLA model active inference loop produces live observations and action arrays:
  ```bash
  docker compose -f examples/aloha_sim/compose.yml up --build
  ```

---

## 3. Command-Line Arguments & Environment Variables

### Command-Line Flags
| Flag | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `--config-file` | **Yes** | — | Path to the IoTAuth entity configuration file (e.g., `/app/sst_config_creds/client.config`). |
| `--log-file` | No | — | Path to a specific `pi0_fast_tokens*.jsonl` file for offline testing. If omitted, watches `data/aloha_sim/token_logs/` for new logs. |
| `--motion-threshold` | No | `0.01` | Normalized intra/inter kinematic threshold in `[0, 1]`, expressed as a fraction of ALOHA joint range (`0.0` produces 100% SIGA). |
| `--execution-horizon`| No | `10` | Number of leading actions ($H$) evaluated for kinematic classification and actuator execution. |

### Supported Environment Variables
| Environment Variable | Description |
| :--- | :--- |
| `OPENPI_MOTION_THRESHOLD` | Overrides the normalized motion threshold (`0.01`). For example, `0.05` means 5% of a joint's modeled range. |
| `FORCE_IOTAUTH_REQUEST` | When set to `1`, forces a fresh session key request for every `SIGA` record, bypassing cached keys entirely. |
| `ALWAYS_IOTAUTH_REQUEST` | When set to `1`, treats every chunk as requiring IoTAuth authentication regardless of motion label (`STILL`/`LOW`/`ACTIVE`). |
| `OPENPI_FAST_TOKEN_LOG` | Custom path/directory override for locating generated JSONL token log files. |
| `SECURE_ACTUATOR_ENABLED` | Set to `1` (`true`/`yes`/`on`) to enable the isolated dual-path actuator sockets (`SecureActuatorClient` and `InsigaActuatorClient`). |
| `SECURE_ACTUATOR_HOST` / `PORT` | Encrypted `SIGA` endpoint target (defaults to configured host and TCP `port 21100`). |
| `SECURE_ACTUATOR_TIMEOUT` | Socket connection and delivery timeout in seconds (default `5.0`). |
| `INSIGA_ACTUATOR_HOST` / `PORT` | Unencrypted `INSIGA` endpoint target (default `127.0.0.1` and TCP `port 21102`). |
| `ACTUATOR_LATENCY_LOG` | Optional actuator-side JSONL path for writing `monitor_actuator_ms` records. Automatically configured by `run_tests.sh`. |
| `IOTAUTH_CONTEXT_PEOPLE` / `LOCATION` / `TIME` | Overrides context metadata sent with session-key requests (`Number of People`, `Location`, `Time of Day`). Automated tests use fixed values (`1`, `Meeting Room`, `14:00`); standalone mode defaults to `Classroom` and current wall-clock time. |

---

## 4. Actuator Gateway & Container Launch

To run the full dual-channel secure actuator gateway locally, first start the gateway service using its registered `Servers-group` entity configuration:

```bash
python scripts/lamps_2026/secure_actuator_gateway.py \
  --config-file /app/sst_config_creds/server.config
```

Then run `openpi_monitor.py` with `SECURE_ACTUATOR_ENABLED=1`. The gateway uses `accept_secure()`, so any newly granted session key is automatically fetched by key ID (`auth101.log`).

With generated local credentials (`run_tests.sh --local`), the entire Docker container stack can be started cleanly via compose profiles:

```bash
export MONITOR_CONFIG=/app/sst_config_creds/local_auth/testing/validity/val1/client_val_1.config
export ACTUATOR_CONFIG=/app/sst_config_creds/local_auth/testing/validity/val1/server.config
export SECURE_ACTUATOR_ENABLED=1
docker compose -f examples/aloha_sim/compose.yml --profile secure-actuator up --build
```

---

## 5. Usage Examples

### Live Monitoring (Recommended Online Mode)
Watches the log directory (`data/aloha_sim/token_logs/`) for new inference files generated by the active VLA simulation and mediates them in real time:

```bash
# Standard run with default threshold (0.01)
python scripts/lamps_2026/openpi_monitor.py --config-file /app/sst_config_creds/client.config

# Custom sensitivity threshold (0.05) using CLI flag
python scripts/lamps_2026/openpi_monitor.py --config-file /app/sst_config_creds/client.config --motion-threshold 0.05

# Custom sensitivity threshold and forced IoTAuth renewal on every SIGA using env vars
OPENPI_MOTION_THRESHOLD=0.05 FORCE_IOTAUTH_REQUEST=1 python scripts/lamps_2026/openpi_monitor.py --config-file /app/sst_config_creds/client.config
```

### Offline Replay & Verification
Process an existing static `.jsonl` token log instantly without launching Docker:

```bash
LATEST_LOG=$(ls -t data/aloha_sim/token_logs/*.jsonl | head -1)
python scripts/lamps_2026/openpi_monitor.py --config-file /app/sst_config_creds/client.config --log-file "$LATEST_LOG" --motion-threshold 0.02
```

---

## 6. Output Log Schema & Analysis

When operating with `SECURE_ACTUATOR_ENABLED=1`, each chunk produces two correlated log entries:

### 1. Monitor Log Record (`TOKEN_LOG.jsonl`)
Contains kinematic classification, threshold comparisons, and cryptographic handshake breakdown:
```json
{
  "record_index": 0,
  "time_unix": 1751410800.123,
  "time_iso": "2026-07-01T10:40:00.123000-07:00",
  "monitor_latency": 14.82,
  "motion_label": "siga",
  "motion_intra_ptp": 0.21,
  "motion_inter_delta": 0.08,
  "motion_combined_score": 0.21,
  "auth_request_ms": 6.17,
  "secure_connect_ms": 2.31,
  "secure_serialize_ms": 0.08,
  "secure_send_ms": 0.44,
  "action_horizon": 10,
  "action_dim": 14,
  "decoded_actions": [ ... ]
}
```

### 2. Actuator Delivery Record (`ACTUATOR_LATENCY_LOG.jsonl`)
Contains exact end-to-end timing between the start of motion classification (`monitor_start_ms`) and driver execution handoff (`driver_handoff_ms`):
```json
{
  "record_id": 0,
  "observation_id": 0,
  "motion_label": "siga",
  "monitor_start_ms": 1784300000000,
  "driver_handoff_ms": 1784300000043,
  "monitor_actuator_ms": 43
}
```

### Automated Analysis (`analyze_latency.py`)
Correlate and summarize both logs into unified performance reports:

```bash
python scripts/lamps_2026/analyze_latency.py TOKEN_LOG.jsonl \
  --actuator-log ACTUATOR_LATENCY_LOG.jsonl
```

The resulting report details every inference index alongside its SIGA/INSIGA classification, intra/inter scores, and `monitor_actuator_ms`. Its summary aggregates SIGA/INSIGA rates, mean latency, and absolute worst-case latency across the entire test run.
