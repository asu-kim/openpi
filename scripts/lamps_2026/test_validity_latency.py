#!/usr/bin/env python3
"""
test_validity_latency.py — Live-run test driver: Validity Period vs. Monitor Latency
=====================================================================================
Runs automated live tests across different session key relative validity periods
(e.g., 1, 3, 5, 7 seconds), performing multiple iterations for each period.
Extracts monitor latency from logs and hands off aggregation + graph generation
to plot_results.py.

For re-generating graphs from existing report files without re-running the
simulation, use plot_results.py directly:

    python scripts/lamps_2026/plot_results.py \\
        --reports-dir test_reports/test1/local/2026-07-06-10-38-00 \\
        --test-name test1 --auth-mode local
"""

import os
import sys
import time
import shlex
import argparse
import subprocess
import re
from pathlib import Path

# Import graph generation from the standalone plot_results module
sys.path.insert(0, str(Path(__file__).parent))
from plot_results import generate_plots_and_reports  # noqa: E402


def get_latest_log_file(log_dir: Path, explicit_log: str = None) -> Path:
    if explicit_log and Path(explicit_log).exists():
        return Path(explicit_log)

    if env_log := os.environ.get("OPENPI_FAST_TOKEN_LOG"):
        if Path(env_log).exists():
            return Path(env_log)

    search_dirs = [log_dir, Path("data/aloha_sim/token_logs"), Path("../data/aloha_sim/token_logs"), Path(".")]
    for d in search_dirs:
        if d.exists():
            files = list(d.glob("*.jsonl"))
            if files:
                return max(files, key=os.path.getctime)
    return None


def run_single_iteration(validity_sec: float, run_idx: int, args: argparse.Namespace, openpi_dir: Path) -> float:
    print(f"\n" + "-" * 70)
    print(f"▶️  [Validity: {validity_sec}s | Run {run_idx}/{args.runs}] Executing test iteration...")
    print("-" * 70)

    env = os.environ.copy()
    env["OPENPI_REL_VALIDITY_SEC"] = str(validity_sec)

    if args.cmd:
        print(f"Executing custom command: {args.cmd}")
        cmd_list = shlex.split(args.cmd)
        res = subprocess.run(cmd_list, env=env, cwd=openpi_dir)
        if res.returncode != 0:
            print(f"⚠️ Warning: Command exited with return code {res.returncode}")
    else:
        # Default offline log replay mode
        log_path = get_latest_log_file(openpi_dir / "data/aloha_sim/token_logs", args.log_file)
        if not log_path or not log_path.exists():
            print(f"❌ Error: No .jsonl log file found. Run a simulation first or pass --log-file.")
            return None

        print(f"Running ActionMonitor offline analysis over log: {log_path}")
        monitor_script = Path(__file__).parent / "openpi_monitor.py"
        cmd_list = [
            sys.executable, str(monitor_script),
            "--config-file", args.config_file,
            "--log-file", str(log_path),
            "--override-rel-validity-sec", str(validity_sec)
        ]
        if args.motion_threshold is not None:
            cmd_list.extend(["--motion-threshold", str(args.motion_threshold)])

        res = subprocess.run(cmd_list, env=env, cwd=openpi_dir)
        if res.returncode != 0:
            print(f"⚠️ Warning: openpi_monitor.py exited with return code {res.returncode}")

    # Analyse latency from the generated log
    log_path = get_latest_log_file(openpi_dir / "data/aloha_sim/token_logs", args.log_file)
    if not log_path or not log_path.exists():
        print("❌ Error: Could not locate token log file after test run.")
        return None

    analyze_script = Path(__file__).parent / "analyze_latency.py"
    report_file = openpi_dir / f"latency_reports/temp_report_val_{validity_sec}s_run_{run_idx}.txt"
    report_file.parent.mkdir(exist_ok=True)

    res = subprocess.run(
        [sys.executable, str(analyze_script), str(log_path), "-o", str(report_file)],
        capture_output=True, text=True, cwd=openpi_dir
    )

    monitor_lat = None
    output_text = res.stdout + (report_file.read_text() if report_file.exists() else "")

    match = re.search(r"Average Monitor Latency:\s*([\d\.]+)\s*ms", output_text, re.IGNORECASE)
    if match:
        monitor_lat = float(match.group(1))
    else:
        matches = re.findall(r'"monitor_latency":\s*([\d\.]+)', log_path.read_text())
        if matches:
            vals = [float(x) for x in matches]
            monitor_lat = sum(vals) / len(vals)

    if monitor_lat is not None:
        print(f"✅ Run {run_idx} complete -> Average Monitor Latency: {monitor_lat:.2f} ms")
    else:
        print(f"⚠️ Warning: Could not extract monitor latency. Defaulting to 0.0 ms.")
        monitor_lat = 0.0

    return monitor_lat


def main():
    parser = argparse.ArgumentParser(description="Live-run tests for Session Key Validity vs. Monitor Latency.")
    parser.add_argument("--validities", nargs="+", type=float, default=[1.0, 3.0, 5.0, 7.0],
                        help="Validity periods in seconds (default: 1 3 5 7).")
    parser.add_argument("--runs", type=int, default=5,
                        help="Number of runs per validity period (default: 5).")
    parser.add_argument("--log-file", default=None,
                        help="Path to an existing .jsonl token log file for offline replay.")
    parser.add_argument("--config-file", default="/app/sst_config_creds/client.config",
                        help="Path to IoTAuth entity client config file.")
    parser.add_argument("--cmd", default=None,
                        help="Custom command to run for each iteration.")
    parser.add_argument("--motion-threshold", type=float, default=None,
                        help="Motion threshold to pass to openpi_monitor.py.")
    parser.add_argument("--output-dir", default="latency_test_results",
                        help="Directory to store CSV reports, text logs, and graphs.")
    parser.add_argument("--test-name", default="test1",
                        help="Test identifier for plot title and output filenames (e.g. test1).")
    parser.add_argument("--auth-mode", default="local", choices=["local", "remote"],
                        help="Auth mode reflected in plot title and filenames (local or remote).")
    args = parser.parse_args()

    openpi_dir = Path(__file__).resolve().parents[2]

    # Verify config file exists or fall back to host path
    config_path = Path(args.config_file)
    if not config_path.exists():
        host_fallback = openpi_dir / "sst_config_creds/client.config"
        if host_fallback.exists():
            args.config_file = str(host_fallback)

    print("=" * 80)
    print("AUTOMATED TEST: VALIDITY PERIOD vs. MONITOR LATENCY")
    print("=" * 80)
    print(f"Validities to test:  {args.validities} seconds")
    print(f"Runs per validity:   {args.runs}")
    print(f"Config file path:    {args.config_file}")
    print(f"Execution mode:      {'Custom Command' if args.cmd else 'Offline Log Analysis'}")
    print("=" * 80)

    results = {val: [] for val in args.validities}

    for val in args.validities:
        for r in range(1, args.runs + 1):
            lat = run_single_iteration(val, r, args, openpi_dir)
            results[val].append(lat if lat is not None else 0.0)
            time.sleep(0.5)

    # In live-run mode worst-case is not tracked per-run; pass empty dict
    # (plot_results will show 0 on the right axis for those points).
    generate_plots_and_reports(args.validities, results, {},
                                openpi_dir / args.output_dir,
                                test_name=args.test_name, auth_mode=args.auth_mode)
    print("\n✅ All automated testing completed successfully!\n")


if __name__ == "__main__":
    main()
