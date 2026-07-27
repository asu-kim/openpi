# LAMPS 2026 VLA access-control testing

[`run_tests.sh`](run_tests.sh) is the primary entry point for the LAMPS 2026 testing workflow.
It prepares the authentication environment, runs the selected parameter sweep, launches the ALOHA simulation, archives the resulting logs, analyzes every run, and generates the final tables and plots.

Run every command in this guide from the OpenPI repository root.

## Prerequisites

The complete runner expects:

- Docker with Docker Compose support.
- The IoTAuth repository checked out next to OpenPI at `../iotauth`.
- A built IoTAuth Auth101 server JAR for local runs.
- The ALOHA simulation configuration at [`examples/aloha_sim/compose.yml`](../../examples/aloha_sim/compose.yml).
- Valid entity configuration, certificate, and key files under `sst_config_creds/`.
- A Python environment containing the analysis and plotting dependencies.

`run_tests.sh` activates the repository's `.venv` when it exists and invokes Python helpers with `python3`.
It does not require `uv run`.

[`plot_results.py`](plot_results.py) uses the active interpreter when `matplotlib` is available.
If the import fails, it searches the known IoTAuth virtual environments and relaunches itself with a compatible interpreter.

For remote mode, prepare the client configurations before running the test.
For local mode, the runner generates and installs the required client and server configurations automatically.
Because the secure actuator is enabled by default, remote credentials must include a registered `server.config` beside each client configuration.

## Start with `run_tests.sh`

Choose one test regime and one authentication mode:

```bash
./scripts/lamps_2026/run_tests.sh --test1 --local --runs 5
./scripts/lamps_2026/run_tests.sh --test2 --remote --runs 5
./scripts/lamps_2026/run_tests.sh --test3 --remote --runs 5
```

The secure actuator and complete `monitor_actuator_ms` latency path are enabled by default.

Use `--no-secure-actuator` only when a monitor-only experiment is required:

```bash
./scripts/lamps_2026/run_tests.sh \
    --test1 \
    --remote \
    --runs 5 \
    --no-secure-actuator
```

Display the current runner options with:

```bash
./scripts/lamps_2026/run_tests.sh --help
```

### Test regimes

| Regime | Parameters varied | Parameters held fixed | Primary output |
| --- | --- | --- | --- |
| `--test1` | Session-key validity periods read from the IoTAuth graph | Motion threshold `0` | Validity-versus-latency tables and plots |
| `--test2` | Motion thresholds configured in `TEST2_THRESHOLDS` | Session-key validity period `1` second | Threshold-versus-latency and bypass-rate tables and plots |
| `--test3` | A fixed 5 by 5 grid of validity periods and motion thresholds | Runs per grid cell | Latency tables and heatmaps |

Each regime must be combined with either `--local` or `--remote`.

Local mode regenerates the IoTAuth credentials, replaces `sst_config_creds/local_auth/testing/validity/`, and starts a local Auth101 server.
Remote mode preserves local credentials and expects the remote Auth101 service and matching files under `sst_config_creds/remote_auth/` to be ready.

### Important runner options

| Option | Effect |
| --- | --- |
| `--runs <n>` | Sets the number of simulation runs for each parameter condition |
| `--password <value>` | Sets the Auth101 password used during local credential generation |
| `--auth-delay-ms <ms>` | Adds Auth101 round-trip delay emulation in remote mode |
| `--secure-actuator` | Explicitly enables the default isolated-actuator behavior for compatibility with existing commands |
| `--no-secure-actuator` | Disables the isolated actuator and measures `monitor_latency` only |
| `--bypass-mode insiga\|siga` | Selects whether INSIGA or SIGA is reported as the bypass-rate series |
| `--show-siga-rate` | Adds the SIGA rate to applicable plots |
| `--show-insiga-rate` | Adds the INSIGA bypass rate to applicable plots |
| `--equidistant-x` | Uses evenly spaced threshold categories |
| `--log-x` | Uses logarithmic threshold spacing |
| `--log-y` | Uses logarithmic latency spacing |
| `--aspect-1-1` | Produces square plots |
| `--no-title` | Omits plot titles |

`still` and `active` remain accepted as legacy aliases for the canonical `insiga` and `siga` bypass modes.

By default, the reported latency metric is `monitor_actuator_ms`.
With `--no-secure-actuator`, the reported latency metric is `monitor_latency`.

## What the runner uses

The runner combines shell orchestration, the Docker-based ALOHA runtime, action monitoring, actuator isolation, report analysis, and plotting.

| Component | How `run_tests.sh` reaches it | Responsibility | Dedicated guide |
| --- | --- | --- | --- |
| [`examples/aloha_sim/compose.yml`](../../examples/aloha_sim/compose.yml) | Invoked directly with `docker compose` | Builds and launches the simulation and the default secure-actuator service | [ALOHA simulation README](../../examples/aloha_sim/README.md) |
| [`run_with_auth_delay.sh`](run_with_auth_delay.sh) | Used as the simulation image command | Applies optional Auth101 network-delay emulation and starts the ALOHA client | This README |
| [`examples/aloha_sim/main.py`](../../examples/aloha_sim/main.py) | Started inside the simulation container | Creates the policy client and installs the monitor wrapper when `MONITOR_CONFIG` is set | [ALOHA simulation README](../../examples/aloha_sim/README.md) |
| [`openpi_monitor.py`](openpi_monitor.py) | Imported by the ALOHA runtime | Classifies action chunks, manages IoTAuth authorization, and routes SIGA or INSIGA actions | [OpenPI monitor guide](../../openpi_monitor_README.md) |
| [`secure_actuator_client.py`](secure_actuator_client.py) | Created by the monitor unless the actuator is disabled | Sends authorized SIGA chunks over the IoTAuth secure channel | [Secure architecture design](../../secure_architecture_design.md) |
| [`insiga_actuator_client.py`](insiga_actuator_client.py) | Created by the monitor unless the actuator is disabled | Sends INSIGA chunks over the plaintext local channel | [Secure architecture design](../../secure_architecture_design.md) |
| [`secure_actuator_gateway.py`](secure_actuator_gateway.py) | Started by the Compose secure-actuator profile | Merges both action channels, enforces record ordering, and records driver-handoff latency | [Secure architecture design](../../secure_architecture_design.md) |
| [`secure_remote_env.py`](secure_remote_env.py) | Imported by the ALOHA runtime unless the actuator is disabled | Connects the simulation loop to the environment owned by the actuator process | [Secure architecture design](../../secure_architecture_design.md) |
| [`analyze_latency.py`](analyze_latency.py) | Invoked directly after every simulation | Converts token and actuator JSONL logs into per-run text reports | This README |
| [`plot_results.py`](plot_results.py) | Invoked directly after all simulations finish | Aggregates reports and generates CSV, text, PNG, and PDF results | This README |

### End-to-end execution flow

1. `run_tests.sh` validates the test regime, authentication mode, run count, and plotting options.
2. It reads the available validity periods from `../iotauth/examples/configs/context_based_validity.graph`.
3. In local mode, it regenerates credentials and starts Auth101.
4. For each parameter condition, it exports the monitor configuration, motion threshold, test metadata, and actuator configuration.
5. It launches [`examples/aloha_sim/compose.yml`](../../examples/aloha_sim/compose.yml).
6. The simulation runtime imports [`openpi_monitor.py`](openpi_monitor.py) when `MONITOR_CONFIG` is present.
7. By default, the monitor sends SIGA and INSIGA records to [`secure_actuator_gateway.py`](secure_actuator_gateway.py), and the ALOHA loop accesses the isolated environment through [`secure_remote_env.py`](secure_remote_env.py).
8. After the simulation exits, the runner identifies and archives the newly created token log.
9. [`analyze_latency.py`](analyze_latency.py) produces a text report for that run and incorporates the actuator log unless `--no-secure-actuator` was selected.
10. After every condition finishes, [`plot_results.py`](plot_results.py) aggregates the reports and generates the final tables and figures.

## Output structure

Every invocation creates a timestamped directory under `test_reports/`:

```text
test_reports/
├── test1/
│   ├── local/<timestamp>/
│   └── remote/<timestamp>/
├── test2/
│   ├── local/<bypass-mode>/<timestamp>/
│   └── remote/<bypass-mode>/<timestamp>/
└── test3/
    ├── local/<timestamp>/
    └── remote/<timestamp>/
```

A completed directory contains:

- `test_metadata.txt` with the conditions required for compatible comparisons.
- Archived token JSONL logs.
- `*_monitor_actuator.jsonl` logs unless `--no-secure-actuator` was selected.
- One latency report for each simulation.
- Aggregate CSV and text summaries.
- PNG and PDF plots.

When a compatible completed run exists for the other authentication mode, the plotter automatically generates local-versus-remote comparisons.
Compatibility is checked using the run count, latency mode, classification boundary, episode settings, IoTAuth context, and applicable parameter arrays.

## Scripts used during the automated run

### `run_with_auth_delay.sh`

[`run_with_auth_delay.sh`](run_with_auth_delay.sh) is the command configured by the ALOHA simulation image.
It reads `AUTH_NETWORK_DELAY_MS`, resolves the Auth101 destination from `MONITOR_CONFIG`, applies network shaping only to that traffic, and then starts the simulation client.

The runner exposes this behavior through `--auth-delay-ms`.
Delay emulation is accepted only in remote mode.

### `openpi_monitor.py`

[`openpi_monitor.py`](openpi_monitor.py) is the action and authorization gate used inside the ALOHA runtime.
It classifies each executable action chunk as SIGA or INSIGA, requests or reuses an IoTAuth session key when required, updates the token log with monitor metrics, and optionally forwards the chunk to the isolated actuator.

See the [OpenPI action and security monitor guide](../../openpi_monitor_README.md) for its CLI, environment variables, motion classification, session-key lifecycle, fallback behavior, and log fields.

### Secure actuator components

[`secure_actuator_client.py`](secure_actuator_client.py) sends SIGA chunks through the secure IoTAuth channel.

[`insiga_actuator_client.py`](insiga_actuator_client.py) sends INSIGA chunks through the plaintext local channel.

[`secure_actuator_gateway.py`](secure_actuator_gateway.py) receives both streams, verifies their shared record sequence, owns the isolated ALOHA environment, and records the first driver handoff for each action record.

[`secure_remote_env.py`](secure_remote_env.py) lets the main simulation loop reset, observe, and advance the environment owned by the gateway.

These components are enabled by default.
Pass `--no-secure-actuator` to run the legacy monitor-only path without them.
Their protocol and trust boundaries are described in the [secure architecture design](../../secure_architecture_design.md).

### `analyze_latency.py`

[`analyze_latency.py`](analyze_latency.py) runs automatically after every simulation.
It validates and summarizes the token log, then adds monitor-to-actuator statistics when the runner supplies an actuator log.

Analyze an existing token log manually with:

```bash
python3 scripts/lamps_2026/analyze_latency.py \
    data/aloha_sim/token_logs/pi0_fast_tokens_<timestamp>.jsonl \
    --output latency_reports/run.txt
```

Include a matching actuator log with:

```bash
python3 scripts/lamps_2026/analyze_latency.py \
    path/to/token_log.jsonl \
    --actuator-log path/to/monitor_actuator.jsonl \
    --output latency_reports/run.txt
```

The analyzer validates actuator record identifiers, required timing fields, and latency calculations before incorporating those records.

### `plot_results.py`

[`plot_results.py`](plot_results.py) runs automatically after every parameter sweep finishes.
It parses the per-run reports, writes aggregate summaries, and produces the plots for the selected test regime.

Regenerate a completed run without repeating its simulations:

```bash
python3 scripts/lamps_2026/plot_results.py \
    --reports-dir test_reports/test1/local/<timestamp>
```

Compare against a specific run from the other authentication mode with:

```bash
python3 scripts/lamps_2026/plot_results.py \
    --reports-dir test_reports/test1/local/<timestamp> \
    --compare-csv test_reports/test1/remote/<timestamp>/validity_vs_latency.csv
```

The plotter can infer the test name and authentication mode from the standard output structure.
Use `--test-name` and `--auth-mode` when processing reports stored elsewhere.
Run `python3 scripts/lamps_2026/plot_results.py --help` for all scale and presentation options.

## Additional research utilities

The following scripts support calibration and investigation but are not invoked by `run_tests.sh`.

### `profile_entropy_quantiles.py`

[`profile_entropy_quantiles.py`](profile_entropy_quantiles.py) derives five empirical motion-threshold operating points from existing token logs.

```bash
python3 scripts/lamps_2026/profile_entropy_quantiles.py \
    data/aloha_sim/token_logs \
    --output calibrated_thresholds.json
```

The script prints a Bash-compatible `THRESHOLDS=(...)` array and records the source files, record counts, thresholds, and expected bypass rates in JSON.
Review these operating points before changing the threshold arrays in [`run_tests.sh`](run_tests.sh).

### `test_validity_latency.py`

[`test_validity_latency.py`](test_validity_latency.py) is a lower-level driver for replaying a log or running a custom command across several validity periods.
Prefer [`run_tests.sh`](run_tests.sh) for the complete Docker-based Test 1 workflow.

```bash
python3 scripts/lamps_2026/test_validity_latency.py \
    --validities 1 3 5 7 \
    --runs 5 \
    --log-file path/to/token_log.jsonl \
    --config-file path/to/client.config \
    --output-dir latency_test_results
```

### `interpret_fast_tokens.py`

[`interpret_fast_tokens.py`](interpret_fast_tokens.py) produces human-readable summaries of FAST token logs and decoded ALOHA trajectories.

See the [FAST token and trajectory interpreter guide](../../interpreter_output_README.md) for its inputs, output fields, and interpretation guidance.

## Focused tests

Files ending in `_test.py` exercise the monitor-to-actuator pipeline and latency analyzer.

Run the focused test suite with:

```bash
uv run pytest scripts/lamps_2026
```

## Related documentation

- [OpenPI monitor guide](../../openpi_monitor_README.md)
- [FAST token and trajectory interpreter guide](../../interpreter_output_README.md)
- [Secure architecture design](../../secure_architecture_design.md)
- [ALOHA simulation README](../../examples/aloha_sim/README.md)
- [General scripts README](../README.md)
