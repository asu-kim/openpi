#!/usr/bin/env python3
"""
Automated Test Script: Validity Period vs. Monitor Latency
==========================================================
Runs automated tests across different session key relative validity periods
(e.g., 1, 3, 5, 7 seconds), performing multiple iterations (default: 5 runs)
for each period. It extracts monitor latency metrics from the logs, averages
them per validity data point, generates a summary CSV/report, and plots a
graph using matplotlib.
"""

import os
import sys
import time
import json
import glob
import shlex
import argparse
import subprocess
import re
from pathlib import Path

# Try importing matplotlib/numpy; if unavailable, attempt automatic re-launch with a venv that has them.
try:
    import matplotlib.pyplot as plt
    import numpy as np
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    # Check known venvs for matplotlib
    known_venvs = [
        Path(__file__).parent.parent.parent / "iotauth/entity/yolo_entity/.venv/bin/python",
        Path(__file__).parent.parent.parent / "iotauth/entity/python/.venv/bin/python",
        Path("/Users/krutyanjayshinde/Desktop/OPT_project/iotauth/entity/yolo_entity/.venv/bin/python"),
    ]
    for venv_py in known_venvs:
        if venv_py.exists() and str(venv_py.resolve()) != str(Path(sys.executable).resolve()):
            try:
                res = subprocess.run([str(venv_py), "-c", "import matplotlib, numpy"], capture_output=True)
                if res.returncode == 0:
                    print(f"🔄 Relaunching script with Python environment: {venv_py}")
                    os.execv(str(venv_py), [str(venv_py)] + sys.argv)
            except Exception:
                pass


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
    print(f"\n" + "-"*70)
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
            print(f"❌ Error: No .jsonl log file found to test against. Please run a simulation first or pass --log-file.")
            return None
            
        print(f"Running ActionMonitor offline analysis over log: {log_path}")
        monitor_script = openpi_dir / "openpi_monitor.py"
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
            
    # Now analyze the latency using scripts/analyze_latency.py
    log_path = get_latest_log_file(openpi_dir / "data/aloha_sim/token_logs", args.log_file)
    if not log_path or not log_path.exists():
        print("❌ Error: Could not locate token log file after test run.")
        return None
        
    analyze_script = openpi_dir / "scripts/analyze_latency.py"
    report_file = openpi_dir / f"latency_reports/temp_report_val_{validity_sec}s_run_{run_idx}.txt"
    report_file.parent.mkdir(exist_ok=True)
    
    res = subprocess.run(
        [sys.executable, str(analyze_script), str(log_path), "-o", str(report_file)],
        capture_output=True, text=True, cwd=openpi_dir
    )
    
    # Extract monitor latency from stdout or report file
    monitor_lat = None
    output_text = res.stdout + (report_file.read_text() if report_file.exists() else "")
    
    match = re.search(r"Average Monitor Latency:\s*([\d\.]+)\s*ms", output_text, re.IGNORECASE)
    if match:
        monitor_lat = float(match.group(1))
    else:
        # Try finding any individual monitor latency if average is missing
        matches = re.findall(r'"monitor_latency":\s*([\d\.]+)', log_path.read_text())
        if matches:
            vals = [float(x) for x in matches]
            monitor_lat = sum(vals) / len(vals)
            
    if monitor_lat is not None:
        print(f"✅ Run {run_idx} complete -> Average Monitor Latency: {monitor_lat:.2f} ms")
    else:
        print(f"⚠️ Warning: Could not extract monitor latency from {log_path}. Defaulting to 0.0 ms.")
        monitor_lat = 0.0
        
    return monitor_lat


def generate_plots_and_reports(validities: list, results: dict, worst_case_results: dict, output_dir: Path,
                               test_name: str = "test1", auth_mode: str = "local"):
    output_dir.mkdir(exist_ok=True)

    # Prepare summary statistics
    summary_rows = []
    avg_latencies = []
    std_latencies = []
    min_latencies = []
    max_latencies = []
    avg_worst_latencies = []  # mean of per-run worst-case values

    for val in validities:
        runs = results[val]
        avg = sum(runs) / len(runs) if runs else 0.0
        min_val = min(runs) if runs else 0.0
        max_val = max(runs) if runs else 0.0
        variance = sum((x - avg) ** 2 for x in runs) / len(runs) if len(runs) > 1 else 0.0
        std_val = variance ** 0.5

        wc_runs = worst_case_results.get(val, [])
        avg_wc = sum(wc_runs) / len(wc_runs) if wc_runs else 0.0

        avg_latencies.append(avg)
        std_latencies.append(std_val)
        min_latencies.append(min_val)
        max_latencies.append(max_val)
        avg_worst_latencies.append(avg_wc)

        summary_rows.append({
            "validity_sec": val,
            "runs": runs,
            "avg": avg,
            "std": std_val,
            "min": min_val,
            "max": max_val,
            "avg_wc": avg_wc,
        })

    # Print Console Report Table
    print("\n" + "="*80)
    print("VALIDITY vs. MONITOR LATENCY TEST RESULTS (Auth on Same Device)")
    print("="*80)
    num_runs = len(list(results.values())[0])
    header = (f"{'Validity (s)':>12} | "
              + " | ".join([f"Run {i+1} (ms)".rjust(10) for i in range(num_runs)])
              + f" | {'Average (ms)':>12} | {'Std Dev':>10} | {'AvgWorstCase':>12}")
    print(header)
    print("-" * len(header))
    for row in summary_rows:
        runs_str = " | ".join([f"{r:>10.2f}" for r in row["runs"]])
        print(f"{row['validity_sec']:>12.1f} | {runs_str} | {row['avg']:>12.2f} | {row['std']:>10.2f} | {row['avg_wc']:>12.2f}")
    print("="*80)

    # Write CSV
    csv_path = output_dir / "validity_vs_latency_same_device.csv"
    with open(csv_path, "w") as f:
        run_headers = ",".join([f"Run_{i+1}_ms" for i in range(num_runs)])
        f.write(f"Validity_sec,{run_headers},Average_ms,StdDev_ms,Min_ms,Max_ms,AvgWorstCase_ms\n")
        for row in summary_rows:
            runs_csv = ",".join([f"{r:.4f}" for r in row["runs"]])
            f.write(f"{row['validity_sec']},{runs_csv},{row['avg']:.4f},{row['std']:.4f},{row['min']:.4f},{row['max']:.4f},{row['avg_wc']:.4f}\n")
    print(f"📊 CSV summary report saved to: {csv_path}")

    # Write Text Report
    txt_path = output_dir / "validity_vs_latency_report.txt"
    with open(txt_path, "w") as f:
        f.write("VALIDITY PERIOD vs. MONITOR LATENCY TEST REPORT\n")
        f.write("===============================================\n")
        f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(header + "\n")
        f.write("-" * len(header) + "\n")
        for row in summary_rows:
            runs_str = " | ".join([f"{r:>10.2f}" for r in row["runs"]])
            f.write(f"{row['validity_sec']:>12.1f} | {runs_str} | {row['avg']:>12.2f} | {row['std']:>10.2f} | {row['avg_wc']:>12.2f}\n")
    print(f"📄 Text summary report saved to: {txt_path}")

    # Plot Graph using matplotlib
    if not MATPLOTLIB_AVAILABLE:
        print("\n⚠️ Warning: matplotlib is not installed in the current Python environment.")
        print("To generate graphs, install matplotlib (e.g., `pip install matplotlib`) and run:")
        print(f"  python scripts/plot_validity_results.py {csv_path}")
        return

    try:
        fig, ax1 = plt.subplots(figsize=(11, 6), dpi=300)

        # ── Left Y-axis: Average Monitor Latency ──────────────────────────────
        color_avg = '#1f77b4'   # blue
        color_ind = '#2ca02c'   # green
        color_wc  = '#d62728'   # red

        ax1.errorbar(validities, avg_latencies, yerr=std_latencies,
                     marker='o', markersize=8, linewidth=2.5,
                     capsize=6, capthick=2, color=color_avg,
                     label='Avg Monitor Latency (±1 Std Dev)', ecolor='#ff7f0e')

        for i, val in enumerate(validities):
            ax1.scatter([val] * len(results[val]), results[val],
                        color=color_ind, alpha=0.6, s=30,
                        label='Individual Runs' if i == 0 else "")
            ax1.annotate(f"{avg_latencies[i]:.2f} ms",
                         (val, avg_latencies[i]),
                         textcoords="offset points", xytext=(0, 12),
                         ha='center', fontweight='bold', color=color_avg)

        ax1.set_xlabel('Relative Validity Period (seconds)', fontsize=12, labelpad=10)
        ax1.set_ylabel('Avg Monitor Latency (ms)', fontsize=12, color=color_avg, labelpad=10)
        ax1.tick_params(axis='y', labelcolor=color_avg)
        ax1.set_xticks(validities)
        ax1.set_xticklabels([f"{v}s" for v in validities], fontsize=11)
        ax1.grid(True, linestyle='--', alpha=0.4)

        # ── Right Y-axis: Avg of Worst-Case Monitor Latency ───────────────────
        ax2 = ax1.twinx()
        ax2.plot(validities, avg_worst_latencies,
                 marker='s', markersize=8, linewidth=2.5,
                 linestyle='--', color=color_wc,
                 label='Avg Worst-Case Monitor Latency')

        for i, val in enumerate(validities):
            ax2.annotate(f"{avg_worst_latencies[i]:.2f} ms",
                         (val, avg_worst_latencies[i]),
                         textcoords="offset points", xytext=(0, -18),
                         ha='center', fontweight='bold', color=color_wc)

        ax2.set_ylabel('Avg Worst-Case Monitor Latency (ms)', fontsize=12,
                       color=color_wc, labelpad=10)
        ax2.tick_params(axis='y', labelcolor=color_wc)

        # ── Combined legend ───────────────────────────────────────────────────
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2,
                   frameon=True, facecolor='white', framealpha=0.9, fontsize=10,
                   loc='upper left')

        # Build title and filename from test/auth context
        test_label = test_name.upper()  # e.g. "TEST1"
        mode_label = auth_mode.capitalize() + " Auth"  # e.g. "Local Auth" / "Remote Auth"
        plot_title = f"{test_label}: Monitor Latency vs. Session Key Relative Validity — {mode_label}"
        file_stem = f"{test_name}_{auth_mode}_validity_vs_monitor_latency"  # e.g. test1_local_validity_vs_monitor_latency

        fig.suptitle(plot_title, fontsize=14, fontweight='bold', y=1.01)
        fig.tight_layout()

        png_path = output_dir / f"{file_stem}.png"
        pdf_path = output_dir / f"{file_stem}.pdf"

        fig.savefig(png_path, bbox_inches='tight')
        fig.savefig(pdf_path, bbox_inches='tight')
        plt.close(fig)

        print(f"📈 Graph (PNG) successfully created at: {png_path}")
        print(f"📈 Graph (PDF) successfully created at: {pdf_path}")
    except Exception as e:
        print(f"❌ Error while generating graph: {e}")


def main():
    parser = argparse.ArgumentParser(description="Automated tests for Session Key Validity vs. Monitor Latency.")
    parser.add_argument("--validities", nargs="+", type=float, default=[1.0, 3.0, 5.0, 7.0],
                        help="List of relative validity periods in seconds (default: 1 3 5 7).")
    parser.add_argument("--runs", type=int, default=5,
                        help="Number of test runs per validity period (default: 5).")
    parser.add_argument("--log-file", default=None,
                        help="Path to an existing .jsonl token log file for offline replay testing.")
    parser.add_argument("--config-file", default="/app/sst_config_creds/client.config",
                        help="Path to IoTAuth entity client config file.")
    parser.add_argument("--cmd", default=None,
                        help="Custom command to run for each iteration (e.g. 'docker compose -f examples/aloha_sim/compose.yml up --build').")
    parser.add_argument("--motion-threshold", type=float, default=None,
                        help="Motion threshold to pass to openpi_monitor.py when running in offline mode.")
    parser.add_argument("--output-dir", default="latency_test_results",
                        help="Directory to store CSV reports, text logs, and graphs.")
    parser.add_argument("--aggregate-reports-dir", default=None,
                        help="Directory containing pre-generated report files (val_<sec>s_run_<idx>.txt) to aggregate directly without running simulation.")
    parser.add_argument("--test-name", default="test1",
                        help="Test identifier used in plot title and output filenames (e.g. test1).")
    parser.add_argument("--auth-mode", default="local", choices=["local", "remote"],
                        help="Auth mode used in this run, reflected in plot title and filenames (local or remote).")
    args = parser.parse_args()
    
    openpi_dir = Path(__file__).parent.parent.resolve()
    
    if args.aggregate_reports_dir:
        reports_dir = openpi_dir / args.aggregate_reports_dir if not Path(args.aggregate_reports_dir).is_absolute() else Path(args.aggregate_reports_dir)
        print("="*80)
        print("AGGREGATING EXISTING LATENCY REPORTS")
        print("="*80)
        print(f"Reports Directory: {reports_dir}")
        print(f"Validities:        {args.validities} seconds")
        print(f"Runs per validity: {args.runs}")
        print("="*80)
        
        results = {val: [] for val in args.validities}
        worst_case_results = {val: [] for val in args.validities}
        for val in args.validities:
            for r in range(1, args.runs + 1):
                # Try integer representation first (e.g. val_1s_run_1.txt), then float (val_1.0s_run_1.txt)
                file_candidates = [
                    reports_dir / f"val_{int(val)}s_run_{r}.txt",
                    reports_dir / f"val_{val}s_run_{r}.txt"
                ]
                lat = 0.0
                wc_lat = 0.0
                found_file = None
                for cand in file_candidates:
                    if cand.exists():
                        found_file = cand
                        break

                if found_file:
                    content = found_file.read_text()
                    avg_match = re.search(r"Average Monitor Latency:\s*([\d\.]+)\s*ms", content, re.IGNORECASE)
                    wc_match  = re.search(r"Worst-Case Monitor Lat\.:\s*([\d\.]+)\s*ms", content, re.IGNORECASE)
                    if avg_match:
                        lat = float(avg_match.group(1))
                        print(f"✅ Loaded {found_file.name} -> avg: {lat:.2f} ms", end="")
                    else:
                        print(f"⚠️ Warning: Could not find 'Average Monitor Latency' in {found_file.name}")
                    if wc_match:
                        wc_lat = float(wc_match.group(1))
                        print(f", worst-case: {wc_lat:.2f} ms")
                    else:
                        wc_lat = lat  # fall back to avg if worst-case line is missing
                        print(f" (worst-case not found, using avg)")
                else:
                    print(f"⚠️ Warning: Report file not found for validity {val}s run {r} in {reports_dir}")
                results[val].append(lat)
                worst_case_results[val].append(wc_lat)

        generate_plots_and_reports(args.validities, results, worst_case_results, openpi_dir / args.output_dir,
                                    test_name=args.test_name, auth_mode=args.auth_mode)
        print("\n✅ Aggregation and plotting completed successfully!\n")
        return

    # Verify config file exists or adjust path for host
    config_path = Path(args.config_file)
    if not config_path.exists():
        host_fallback = openpi_dir / "sst_config_creds/client.config"
        if host_fallback.exists():
            args.config_file = str(host_fallback)
            
    print("="*80)
    print("AUTOMATED TEST: VALIDITY PERIOD vs. MONITOR LATENCY")
    print("="*80)
    print(f"Validities to test:  {args.validities} seconds")
    print(f"Runs per validity:   {args.runs}")
    print(f"Config file path:    {args.config_file}")
    print(f"Execution mode:      {'Custom Command' if args.cmd else 'Offline Log Analysis'}")
    print("="*80)
    
    results = {val: [] for val in args.validities}
    
    for val in args.validities:
        for r in range(1, args.runs + 1):
            lat = run_single_iteration(val, r, args, openpi_dir)
            if lat is not None:
                results[val].append(lat)
            else:
                results[val].append(0.0)
            time.sleep(0.5)  # Brief pause between iterations
            
    # In live-run mode, worst-case data is not separately tracked per run;
    # pass an empty dict so generate_plots_and_reports() shows zeros on right axis.
    generate_plots_and_reports(args.validities, results, {}, openpi_dir / args.output_dir,
                                test_name=args.test_name, auth_mode=args.auth_mode)
    print("\n✅ All automated testing completed successfully!\n")


if __name__ == "__main__":
    main()
