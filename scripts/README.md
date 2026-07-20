# OpenPI Automated Testing & Visualization Pipeline

This directory contains the automated test runner, log analyzer, and visualization engine used to evaluate the end-to-end security and latency performance of the $\pi_0$-FAST Vision-Language-Action (VLA) model when integrated with the **IoTAuth** secure action monitoring system (`openpi_monitor.py`).

---

## Table of Contents
1. [Pipeline Architecture & Data Flow](#1-pipeline-architecture--data-flow)
2. [Automated Test Runner (`run_tests.sh`)](#2-automated-test-runner-run_testssh)
   - [Prerequisites & Directory Setup](#prerequisites--directory-setup)
   - [Supported Test Regimes (Test 1, Test 2, Test 3)](#supported-test-regimes)
   - [Command-Line Flags & Usage](#command-line-flags--usage)
   - [Execution Workflow](#execution-workflow)
3. [Latency Log Analyzer (`analyze_latency.py`)](#3-latency-log-analyzer-analyze_latencypy)
4. [Aggregation & Plotting Engine (`plot_results.py`)](#4-aggregation--plotting-engine-plot_resultspy)
   - [Test 1 & Test 2 Line Plots](#test-1--test-2-line-plots)
   - [Test 3 2D Heatmaps & Shared Temperature Scaling](#test-3-2d-heatmaps--shared-temperature-scaling)
   - [Command-Line Options](#command-line-options)
5. [Supporting Training & Policy Scripts](#5-supporting-training--policy-scripts)

---

## 1. Pipeline Architecture & Data Flow

The testing pipeline orchestrates multi-run Docker simulations, captures fine-grained token timestamps, computes per-run latency metrics, and aggregates cross-condition results into publication-quality line graphs and 2D heatmaps.

### End-to-End Execution Flow
1. **Test Runner (`scripts/run_tests.sh`)** configures the environment, selects client configurations (`sst_config_creds/`), and launches the simulation inside Docker (`examples/aloha_sim/compose.yml`).
2. **Action Monitor (`openpi_monitor.py`)** runs inside the container during inference, analyzing continuous ALOHA action chunks (`14D` bimanual trajectories).
   - Calculates intra-chunk peak-to-peak motion and inter-chunk state displacement against `OPENPI_MOTION_THRESHOLD`.
   - Classifies action chunks as **`SIGA`** (Significant Action / Active) or **`INSIGA`** (Insignificant Action / Still).
   - Routes `SIGA` chunks through an encrypted IoTAuth secure channel (local or remote authentication server) and `INSIGA` chunks over a local plaintext socket.
   - Logs exact timestamps (`monitor_actuator_ms`) for every action chunk to a `.jsonl` log file inside `data/aloha_sim/token_logs/`.
3. **Log Analyzer (`scripts/analyze_latency.py`)** is automatically invoked by `run_tests.sh` after each iteration. It parses the raw `.jsonl` log, computes average and worst-case monitor latency, measures classification ratios (`SIGA` vs `INSIGA`), and writes a per-run `.txt` report file.
4. **Plotter & Aggregator (`scripts/plot_results.py`)** runs once all test iterations finish. It parses all `.txt` report files in the output directory, builds summary CSVs, and generates comparative line charts (`.png`/`.pdf`) and 2D temperature heatmaps across local and remote authentication regimes.

### Standardized Output Directory Structure
All generated reports, logs, CSVs, and graphs are automatically organized under `test_reports/`:

```text
test_reports/
├── test1/
│   ├── local/
│   │   └── 2026-07-20-10-30-00/
│   │       ├── test_metadata.txt
│   │       ├── val_1s_thresh_0.01_run_1.jsonl  # Archived raw token log
│   │       ├── val_1s_thresh_0.01_run_1.txt    # Parsed latency summary report
│   │       ├── ...
│   │       ├── validity_vs_latency.csv         # Aggregated summary CSV
│   │       └── test1_validity_vs_latency.png   # Single-mode line plot
│   └── remote/
│       └── 2026-07-20-11-00-00/
│           ├── validity_vs_latency.csv
│           └── test1_comparative_avg_latency.png # Auto-overlaid Local vs Remote plot
├── test2/
│   └── remote/
│       └── active/                             # Separated by bypass-mode
│           └── 2026-07-20-11-30-00/
│               ├── threshold_vs_latency.csv
│               └── test2_comparative_avg_latency.png
└── test3/
    ├── local/
    │   └── 2026-07-20-12-00-00/
    │       ├── validity_threshold_latency.csv
    │       └── test3_local_validity_threshold_latency_heatmap.png
    └── remote/
        └── 2026-07-20-12-30-00/
            ├── validity_threshold_latency.csv
            └── test3_remote_validity_threshold_latency_heatmap.png # Shared scale
```

---

## 2. Automated Test Runner (`run_tests.sh`)

`scripts/run_tests.sh` is the central orchestration script. It automates parameter sweeps across validity periods and motion thresholds, manages client cryptographic certificates, handles container lifecycles, and triggers automated analysis.

### Prerequisites & Directory Setup
- **Docker & Docker Compose**: Must be installed and running. The runner builds and runs `examples/aloha_sim/compose.yml`.
- **IoTAuth Authentication Server (`../iotauth/`)**:
  - **Local Mode (`--local`)**: `run_tests.sh` automatically runs `generateAll.sh` to create certificates and launches the local `Auth101` JAR in the background. You only need the `iotauth/` repository cloned alongside this repo (`../iotauth/`) and the server JAR built (`make` inside `../iotauth/auth/auth-server/`).
  - **Remote Mode (`--remote`)**: `run_tests.sh` skips local certificate generation and server startup. You must manually start the remote `Auth101` server and generate/place credentials before running tests. For instructions on configuring the remote Auth server and generating credentials, see:
    - **[IoTAuth Credential & Example Scripts](https://github.com/iotauth/iotauth/blob/main/examples/README.md)**
- **Client Configurations & Credentials (`sst_config_creds/`)**: Before running tests (especially in remote mode), ensure that entity certificates, private keys, and server config files (`client_val_<val>.config`) are placed in the host directory:
  ```text
  sst_config_creds/
  ├── local_auth/   # Automatically populated by run_tests.sh when using --local
  │   └── testing/validity/val<1..120>/client_val_<val>.config
  └── remote_auth/  # Must be populated manually when using --remote
      └── testing/validity/val<1..120>/client_val_<val>.config
  ```
  The script mounts these configuration files into `/app/sst_config_creds/` inside the simulation container via the `MONITOR_CONFIG` environment variable.

### Supported Test Regimes

#### 1. Test 1 (`--test1`): Validity Period Sweep
Evaluates how the IoTAuth session certificate **validity period** impacts average and worst-case monitor latency under a fixed motion threshold (`0.01`).
- **Swept Validity Periods**: `1, 2, 5, 10, 30, 60, 120` seconds.
- **Goal**: Measure re-authentication and session key handshake overhead when session keys expire rapidly (e.g., `1s`) versus when they remain valid for long windows (`120s`).

#### 2. Test 2 (`--test2`): Motion Threshold Sweep
Evaluates how the **motion threshold** (`OPENPI_MOTION_THRESHOLD`) impacts latency and classification rates (`SIGA` vs `INSIGA`) under a fixed certificate validity period (`1s`).
- **Swept Motion Thresholds**: `0.0000, 0.0080, 0.0200, 0.1500, 0.6000`.
- **Bypass Modes (`--bypass-mode still|active`)**:
  - `still` (Default): Evaluates baseline operation where insignificant (`INSIGA`) action chunks bypass encryption.
  - `active`: Evaluates performance behavior across active/still classification boundaries.
- **Goal**: Quantify the trade-off between sensitivity to minor joint movements, the percentage of actions triggering cryptographic authentication, and end-to-end system latency.

#### 3. Test 3 (`--test3`): 2D Parameter Grid Sweep
Performs a comprehensive **$5 \times 5$ Cartesian grid evaluation** across both validity periods and motion thresholds to capture joint parameter interactions.
- **Validity Periods**: `1, 2, 3, 4, 5` seconds.
- **Motion Thresholds**: `0.0000, 0.0080, 0.0200, 0.1500, 0.6000`.
- **Total Simulations**: $25 \text{ grid cells} \times N \text{ runs per cell}$ (e.g., $125$ total simulations for `--runs 5`).
- **Goal**: Generate multi-dimensional latency heatmaps showing exactly how strict thresholds and aggressive re-authentication schedules interact across both average and worst-case scenarios.

### Command-Line Flags & Usage

```bash
# General Syntax
./scripts/run_tests.sh --test1|--test2|--test3 --local|--remote [OPTIONS] [PLOT_FLAGS]
```

| Flag | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `--test1`, `--test2`, `--test3` | Yes | — | Selects the target test regime to execute. |
| `--local`, `--remote` | Yes | — | Selects the authentication mode (`local_auth` vs `remote_auth`). |
| `--runs <n>` | No | `1` | Number of simulation iterations to run per parameter condition. |
| `--password <pw>` | No | — | `sudo` password for container network/permission adjustments if required. |
| `--bypass-mode <mode>` | No | `still` | Bypass mode for Test 1 and Test 2 (`still` or `active`). |
| `--equidistant-x` | No | `False` | Forwarded to plotter: plots X-axis categories with uniform spacing. |
| `--log-x` | No | `False` | Forwarded to plotter: plots Test 2 X-axis on base-10 logarithmic scale. |
| `--log-y` | No | `False` | Forwarded to plotter: plots Y-axis (latency) on logarithmic scale. |
| `--show-active-rate` | No | `False` | Forwarded to plotter: overlays `SIGA` classification rate percentage curve. |
| `--show-still-rate` | No | `False` | Forwarded to plotter: overlays `INSIGA` classification rate percentage curve. |
| `--aspect-1-1` / `--square` | No | `False` | Forwarded to plotter: forces square `1:1` aspect ratio on generated figures. |
| `--no-title` | No | `False` | Forwarded to plotter: omits main chart title for clean publication insertion. |

#### Example Executions
```bash
# Run 5 iterations of Test 1 under remote authentication
./scripts/run_tests.sh --test1 --remote --runs 5

# Run 5 iterations of Test 2 under local authentication with logarithmic X-axis plotting
./scripts/run_tests.sh --test2 --local --runs 5 --log-x

# Run a full 5x5 grid sweep for Test 3 under remote authentication without titles
./scripts/run_tests.sh --test3 --remote --runs 5 --no-title
```

### Execution Workflow
1. **Argument & Configuration Validation**: Parses CLI flags, verifies required configuration files for the requested validity periods exist on the host machine (`sst_config_creds/`), and initializes the timestamped output folder.
2. **Metadata Archival**: Writes `test_metadata.txt` recording the test regime, auth mode, run counts, threshold values, secure actuator settings, and start timestamp.
3. **Simulation Execution Loop (Steps 1–5)**:
   - For each parameter combination (validity period and/or threshold) and run iteration ($1 \dots N$):
     - Exports target environment variables (`MONITOR_CONFIG`, `OPENPI_MOTION_THRESHOLD`, `TEST_VALIDITY_PERIOD`, `TEST_RUN_ITERATION`, `TEST_TOTAL_RUNS`).
     - Launches `docker compose -f examples/aloha_sim/compose.yml up --build --abort-on-container-exit`.
     - Detects newly generated `.jsonl` token logs in `data/aloha_sim/token_logs/` upon container exit and copies them into the run output directory.
     - Invokes `python3 scripts/analyze_latency.py` on the fresh log file to generate `val_<val>s_thresh_<thresh>_run_<run>.txt`.
4. **Automated Aggregation & Plotting (Step 6)**:
   - Automatically checks if an opposite authentication mode run (`local` vs `remote`) with identical parameters and metadata (`metadata_is_compatible`) already exists.
   - If a compatible run exists, automatically passes `--compare-csv "$OTHER_CSV"` to `plot_results.py` so that comparative overlays and shared-scale heatmaps are generated immediately.

---

## 3. Latency Log Analyzer (`analyze_latency.py`)

`scripts/analyze_latency.py` is a standalone analysis tool invoked after each simulation run to parse raw JSONL token logs.

### Usage
```bash
python3 scripts/analyze_latency.py <path_to_token_log.jsonl> [-o <output_report.txt>]
```

### Metrics Processed
The analyzer scans every action chunk entry inside the `.jsonl` file and extracts:
- **`monitor_actuator_ms`**: Measured elapsed time in milliseconds from when the action chunk enters the monitor to when the verified action reaches the actuator boundary.
- **Classification Counts (`SIGA` vs `INSIGA`)**: Counts how many chunks required secure authentication (`SIGA`) versus how many bypassed encryption as idle/insignificant motion (`INSIGA`).
- **Average & Worst-Case Latency**: Computes the arithmetic mean and peak (`max`) monitor latency across the entire episode.
- **Active / Still Rates**: Calculates the exact percentage of total chunks classified as `SIGA` ($\text{Active Rate} = \frac{N_{\text{SIGA}}}{N_{\text{total}}} \times 100\%$) and `INSIGA` ($\text{Still Rate} = \frac{N_{\text{INSIGA}}}{N_{\text{total}}} \times 100\%$).

### Sample Output Report (`.txt`)
```text
================================================================================
TOKEN LOG LATENCY ANALYSIS REPORT
================================================================================
Log File Name           : val_1s_thresh_0.0080_run_1.jsonl
Total Records Analyzed  : 300
Active Records (SIGA)   : 245 (81.67%)
Still Records (INSIGA)  : 55 (18.33%)
--------------------------------------------------------------------------------
Average Monitor Latency : 36.6260 ms
Worst-Case Latency      : 119.4000 ms
================================================================================
```

---

## 4. Aggregation & Plotting Engine (`plot_results.py`)

`scripts/plot_results.py` is a powerful Python plotting engine built on `matplotlib`. It can be run automatically via `run_tests.sh` or executed standalone against any existing test report directory.

### Standalone Invocation Syntax
```bash
python3 scripts/plot_results.py --reports-dir path/to/test_reports/<test>/<mode>/<timestamp> [OPTIONS]
```

### Test 1 & Test 2 Line Plots
When processing Test 1 or Test 2 directories, `plot_results.py`:
1. **Aggregates Multi-Run Data**: Averages metrics across all $N$ run iterations per condition (`val_*_run_*.txt`), calculating both arithmetic mean and peak worst-case latency across runs.
2. **Summary CSV Generation**: Outputs `validity_vs_latency.csv` (Test 1) or `threshold_vs_latency.csv` (Test 2) containing exact tabulated values for external analysis.
3. **Automatic Mode Overlay (`--compare-csv`)**:
   - If `--compare-csv <other_csv>` is passed (or auto-discovered via `discover_compare_csv`), `plot_results.py` generates comparative overlay line plots (`test1_comparative_avg_latency.png` / `.pdf` and `test1_comparative_wc_latency.png` / `.pdf`).
   - Clearly contrasts **Local Auth (Decentralized)** versus **Remote Auth (Centralized)** curves on the same axes.
4. **Collision-Aware Legend Positioning**: Automatically arranges and stacks legend boxes using `position_collision_aware_legends()`, ensuring Centralized (Remote) entries consistently stack above Decentralized (Local) entries without obscuring data curves.
5. **Secondary Y-Axis Classification Rates**:
   - Passing `--show-active-rate` or `--show-still-rate` adds a right-hand Y-axis (`0%` to `100%`) showing the exact percentage of `SIGA` or `INSIGA` chunks at each threshold or validity period.

#### X-Axis Scaling Transforms (Test 2)
Because Test 2 sweeps motion thresholds across several orders of magnitude (`0.0000` to `0.6000`), `plot_results.py` provides versatile coordinate transformations:
- **Linear (`default`)**: True proportional numerical spacing along the X-axis.
- **Logarithmic (`--log-x`)**: Base-10 logarithmic scaling. Zero values (`0.0000`) are mapped to `0.0001` (`LOG_X_ZERO_FLOOR`) while preserving their original `"0"` text label.
- **Equidistant (`--equidistant-x`)**: Categorical spacing where each threshold label is spaced uniformly across the X-axis regardless of numerical gaps.
- **Hybrid (`--hybrid-x`)**: Blended transformation mapping early dense thresholds linearly and larger thresholds logarithmically to balance visual clarity across both regimes.

### Test 3 2D Heatmaps & Shared Temperature Scaling
When invoked on a Test 3 directory (`--test-name test3`), `plot_results.py` constructs a $5 \times 5$ grid matrix over Validity Periods ($\text{columns} = 1, 2, 3, 4, 5\text{s}$) and Motion Thresholds ($\text{rows} = 0, 0.008, 0.02, 0.15, 0.6$).

#### Heatmap Generation & Cell Labels
- Renders two distinct heatmaps: **Average Monitor Latency** (`test3_<mode>_validity_threshold_latency_heatmap.png`) and **Worst-Case Monitor Latency** (`test3_<mode>_validity_threshold_wc_latency_heatmap.png`).
- Uses the `RdYlGn_r` (Red-Yellow-Green reversed) colormap, where low latencies appear cool/green and high latencies appear bright red.
- Dynamically computes cell text luminance (`luminance = 0.2126*R + 0.7152*G + 0.0722*B`) to automatically switch numeric label font color between bold black and white for optimal readability.
- Outputs a comprehensive tabular text summary (`validity_threshold_latency_report.txt`) and `validity_threshold_latency.csv`.

#### Shared Temperature Scaling (`vmin` / `vmax`)
By default, standalone heatmaps normalize their colorbar across the minimum (`vmin`) and maximum (`vmax`) latency values inside that single matrix. However, when comparing Local Auth vs Remote Auth heatmaps, independent normalization distorts color perception (e.g., `80 ms` might appear deep red in a Local run but yellow in a Remote run where the peak reaches `210 ms`).

When `plot_results.py` is provided a comparison CSV (`--compare-csv <path>` or via auto-discovery):
1. Reads both `primary_csv` (e.g., Local) and `compare_csv` (e.g., Remote).
2. Computes the **global minimum (`vmin`)** and **global maximum (`vmax`)** latency across **both datasets simultaneously**:
   $$\text{vmin}_{\text{global}} = \min(\text{values}_{\text{local}} \cup \text{values}_{\text{remote}}), \quad \text{vmax}_{\text{global}} = \max(\text{values}_{\text{local}} \cup \text{values}_{\text{remote}})$$
3. Passes $\text{vmin}_{\text{global}}$ and $\text{vmax}_{\text{global}}$ to `ax.imshow()`.
4. Automatically renders and updates the heatmaps in **both** the primary run folder (`test_reports/test3/<mode>/<timestamp>/`) and the comparison run folder with the synchronized temperature scale, guaranteeing exact side-by-side comparability.

### Command-Line Options (`plot_results.py`)

| Argument | Description |
| :--- | :--- |
| `--reports-dir <path>` | Required. Directory containing per-run `.txt` report files or existing summary CSVs. |
| `--output-dir <path>` | Target directory for generated CSVs and plots (defaults to `--reports-dir`). |
| `--test-name <name>` | Force test identification (`test1`, `test2`, `test3`). Auto-inferred if omitted. |
| `--compare-csv <path>` | Explicit path to opposite mode summary CSV (`validity_vs_latency.csv`, etc.) for overlays or shared scale. |
| `--no-auto-compare` | Disables automatic discovery of opposite authentication mode runs (`discover_compare_csv`). |
| `--bypass-mode <mode>` | Bypass mode label for plot subtitle (`still` vs `active`). Defaults to `still`. |
| `--equidistant-x` | Spacings categories evenly along the X-axis (`Test 1` and `Test 2`). |
| `--log-x` | Uses base-10 logarithmic spacing on the X-axis (`Test 2` threshold sweeps). |
| `--log-y` | Uses base-10 logarithmic spacing on the Y-axis (`Test 1` and `Test 2`). |
| `--hybrid-x` | Uses blended hybrid linear-logarithmic spacing on the X-axis (`Test 2`). |
| `--show-active-rate` | Overlays secondary right-hand Y-axis showing percentage of `SIGA` chunks. |
| `--show-still-rate` | Overlays secondary right-hand Y-axis showing percentage of `INSIGA` chunks. |
| `--aspect-1-1` / `--square` | Forces exact `1:1` square figure aspect ratio across generated plots. |
| `--no-title` | Omits main chart figure titles (`fig.suptitle`) for clean LaTeX document inclusion. |

---

## 5. Supporting Training & Policy Scripts

In addition to the evaluation pipeline, `scripts/` houses core scripts for training, profiling, and serving Vision-Language-Action policies:

### `train.py` & `train_pytorch.py`
- Entry points for fine-tuning and pre-training OpenPI models ($\pi_0$, $\pi_0$-FAST, $\pi_{0.5}$).
- `train.py` leverages JAX/Flax with distributed data parallelism and LeRobot/DROID dataset loaders.
- `train_pytorch.py` provides native PyTorch training support with Fully Sharded Data Parallelism (`FSDP`), LoRA fine-tuning, and multi-GPU synchronization.

### `serve_policy.py`
- High-performance inference policy server.
- Exposes a network socket/WebSocket interface allowing remote robot platforms or simulation instances (`aloha_sim`, `ur5`, `libero`) to stream visual observations and proprioceptive state and receive real-time decoded continuous action chunks or FAST tokens.

### `compute_norm_stats.py`
- Utility script to compute and normalize joint trajectories, action bounds, and proprioceptive statistics over custom robotic training datasets before fine-tuning.
- Outputs normalization quantile dictionaries consumed by the policy wrapper during action decoding.

### `test_validity_latency.py` & `profile_entropy_quantiles.py`
- `test_validity_latency.py`: Standalone test harness for measuring cryptographic certificate verification delays across varying X.509/IoTAuth validity window configurations without running full Docker VLA simulations.
- `profile_entropy_quantiles.py`: Profiling script used during the research and design phase of `openpi_monitor.py` to analyze information-theoretic entropy distributions across ALOHA action trajectories and establish baseline classification boundaries.
