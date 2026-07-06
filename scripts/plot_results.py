#!/usr/bin/env python3
"""
plot_results.py — Standalone graph generator for openpi latency test reports
=============================================================================
Reads pre-generated per-run report files from a timestamped test_reports/ directory:
  - Test 1 (val_Xs_run_Y.txt): Validity vs. Average & Worst-Case Monitor Latency
  - Test 2 (thresh_X_run_Y.txt): Motion Threshold vs. Bypass Rate (%) & Latency

Can be run independently on any existing test_reports/ run folder to regenerate
or tweak graphs without re-running the full simulation:

    python scripts/plot_results.py \
        --reports-dir test_reports/test1/local/2026-07-06-10-38-00 \
        --output-dir  test_reports/test1/local/2026-07-06-10-38-00 \
        --test-name test1 --auth-mode local

    python scripts/plot_results.py \
        --reports-dir test_reports/test2/local/2026-07-06-11-00-00 \
        --bypass-mode still
"""

import os
import sys
import time
import re
import argparse
import subprocess
import tempfile
from pathlib import Path

# Ensure MPLCONFIGDIR is set to a writable temporary directory to avoid permission errors on restricted/shared workstations
if "MPLCONFIGDIR" not in os.environ:
    uid = getattr(os, "getuid", lambda: "default")()
    cache_dir = Path(tempfile.gettempdir()) / f"matplotlib_cache_{uid}"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(cache_dir)
    except Exception:
        pass

# Try importing matplotlib; if unavailable, attempt re-launch with a known venv.
try:
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    known_venvs = [
        Path(__file__).parent.parent.parent / "iotauth/entity/yolo_entity/.venv/bin/python",
        Path(__file__).parent.parent.parent / "iotauth/entity/python/.venv/bin/python",
        Path("/Users/krutyanjayshinde/Desktop/OPT_project/iotauth/entity/yolo_entity/.venv/bin/python"),
    ]
    for venv_py in known_venvs:
        if venv_py.exists() and str(venv_py.resolve()) != str(Path(sys.executable).resolve()):
            try:
                res = subprocess.run([str(venv_py), "-c", "import matplotlib"], capture_output=True)
                if res.returncode == 0:
                    print(f"🔄 Relaunching with Python environment: {venv_py}")
                    os.execv(str(venv_py), [str(venv_py)] + sys.argv)
            except Exception:
                pass


def infer_test_context(reports_dir: Path) -> tuple[str, str]:
    """
    Infer test_name and auth_mode from the reports_dir path.
    Expected structure: ...test_reports/<test_name>/<auth_mode>/<timestamp>/
    Returns (test_name, auth_mode) or (None, None) if structure doesn't match.
    """
    parts = reports_dir.parts
    for i, part in enumerate(parts):
        if part == "test_reports" and i + 2 < len(parts):
            test_name = parts[i + 1]   # e.g. 'test1' or 'test2'
            auth_mode = parts[i + 2]   # e.g. 'local' or 'remote'
            return test_name, auth_mode
    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Validity Period vs. Monitor Latency
# ─────────────────────────────────────────────────────────────────────────────

def discover_reports(reports_dir: Path) -> tuple[list, int]:
    """
    Auto-discover validity periods and run count by globbing for val_*s_run_*.txt
    files in reports_dir. Returns (sorted_validities, max_run_count).
    """
    pattern = re.compile(r"val_(\d+(?:\.\d+)?)s_run_(\d+)\.txt", re.IGNORECASE)
    validity_set = set()
    runs_per_validity = {}

    for f in reports_dir.glob("val_*s_run_*.txt"):
        m = pattern.match(f.name)
        if m:
            val = float(m.group(1))
            run = int(m.group(2))
            validity_set.add(val)
            runs_per_validity[val] = max(runs_per_validity.get(val, 0), run)

    if not validity_set:
        print(f"❌ Error: No val_*s_run_*.txt files found in {reports_dir}")
        sys.exit(1)

    validities = sorted(validity_set)
    runs = max(runs_per_validity.values())
    print(f"🔍 Auto-discovered Test 1: validities={[int(v) for v in validities]}s, runs={runs}")
    return validities, runs


def load_reports(reports_dir: Path, validities: list, runs: int) -> tuple[dict, dict]:
    """
    Read all val_Xs_run_Y.txt files from reports_dir.
    Returns (results, worst_case_results) where each is {validity: [run_values...]}.
    """
    results = {val: [] for val in validities}
    worst_case_results = {val: [] for val in validities}

    for val in validities:
        for r in range(1, runs + 1):
            file_candidates = [
                reports_dir / f"val_{int(val)}s_run_{r}.txt",
                reports_dir / f"val_{val}s_run_{r}.txt",
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
                    print(f"⚠️  Warning: 'Average Monitor Latency' not found in {found_file.name}")
                if wc_match:
                    wc_lat = float(wc_match.group(1))
                    print(f", worst-case: {wc_lat:.2f} ms")
                else:
                    wc_lat = lat
                    print(f" (worst-case not found, using avg)")
            else:
                print(f"⚠️  Warning: Report not found for validity {val}s run {r} in {reports_dir}")

            results[val].append(lat)
            worst_case_results[val].append(wc_lat)

    return results, worst_case_results


def generate_plots_and_reports(validities: list, results: dict, worst_case_results: dict,
                                output_dir: Path, test_name: str = "test1", auth_mode: str = "local"):
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    avg_latencies = []
    std_latencies = []
    min_latencies = []
    max_latencies = []
    avg_worst_latencies = []

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

    # Console table
    print("\n" + "=" * 80)
    print("VALIDITY vs. MONITOR LATENCY TEST RESULTS")
    print("=" * 80)
    num_runs = len(list(results.values())[0])
    header = (f"{'Validity (s)':>12} | "
              + " | ".join([f"Run {i+1} (ms)".rjust(10) for i in range(num_runs)])
              + f" | {'Average (ms)':>12} | {'Std Dev':>10} | {'AvgWorstCase':>12}")
    print(header)
    print("-" * len(header))
    for row in summary_rows:
        runs_str = " | ".join([f"{r:>10.2f}" for r in row["runs"]])
        print(f"{row['validity_sec']:>12.1f} | {runs_str} | {row['avg']:>12.2f} | {row['std']:>10.2f} | {row['avg_wc']:>12.2f}")
    print("=" * 80)

    # CSV
    csv_path = output_dir / "validity_vs_latency_same_device.csv"
    with open(csv_path, "w") as f:
        run_headers = ",".join([f"Run_{i+1}_ms" for i in range(num_runs)])
        f.write(f"Validity_sec,{run_headers},Average_ms,StdDev_ms,Min_ms,Max_ms,AvgWorstCase_ms\n")
        for row in summary_rows:
            runs_csv = ",".join([f"{r:.4f}" for r in row["runs"]])
            f.write(f"{row['validity_sec']},{runs_csv},{row['avg']:.4f},{row['std']:.4f}"
                    f",{row['min']:.4f},{row['max']:.4f},{row['avg_wc']:.4f}\n")
    print(f"📊 CSV saved to: {csv_path}")

    # Text report
    txt_path = output_dir / "validity_vs_latency_report.txt"
    with open(txt_path, "w") as f:
        f.write("VALIDITY PERIOD vs. MONITOR LATENCY TEST REPORT\n")
        f.write("===============================================\n")
        f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(header + "\n")
        f.write("-" * len(header) + "\n")
        for row in summary_rows:
            runs_str = " | ".join([f"{r:>10.2f}" for r in row["runs"]])
            f.write(f"{row['validity_sec']:>12.1f} | {runs_str} | {row['avg']:>12.2f}"
                    f" | {row['std']:>10.2f} | {row['avg_wc']:>12.2f}\n")
    print(f"📄 Text report saved to: {txt_path}")

    # Graph
    if not MATPLOTLIB_AVAILABLE:
        print("\n⚠️  Warning: matplotlib is not available.")
        return

    try:
        fig, ax1 = plt.subplots(figsize=(11, 6), dpi=300)

        color_avg = '#1f77b4'
        color_ind = '#2ca02c'
        color_wc  = '#d62728'

        ax1.errorbar(validities, avg_latencies, yerr=std_latencies,
                     marker='o', markersize=8, linewidth=2.5,
                     capsize=6, capthick=2, color=color_avg,
                     label='Avg Monitor Latency (±1 Std Dev)', ecolor='#ff7f0e')
        for i, val in enumerate(validities):
            ax1.scatter([val] * len(results[val]), results[val],
                        color=color_ind, alpha=0.6, s=30,
                        label='Individual Runs' if i == 0 else "")
            ax1.annotate(f"{avg_latencies[i]:.2f} ms", (val, avg_latencies[i]),
                         textcoords="offset points", xytext=(0, 12),
                         ha='center', fontweight='bold', color=color_avg)

        ax1.set_xlabel('Relative Validity Period (seconds)', fontsize=12, labelpad=10)
        ax1.set_ylabel('Avg Monitor Latency (ms)', fontsize=12, color=color_avg, labelpad=10)
        ax1.tick_params(axis='y', labelcolor=color_avg)
        ax1.set_ylim(0, 100)
        ax1.set_yticks(range(0, 101, 10))
        ax1.set_xticks(validities)
        ax1.set_xticklabels([f"{v}s" for v in validities], fontsize=11)
        ax1.grid(True, linestyle='--', alpha=0.4)

        ax2 = ax1.twinx()
        ax2.plot(validities, avg_worst_latencies,
                 marker='s', markersize=8, linewidth=2.5,
                 linestyle='--', color=color_wc,
                 label='Avg Worst-Case Monitor Latency')
        for i, val in enumerate(validities):
            ax2.annotate(f"{avg_worst_latencies[i]:.2f} ms", (val, avg_worst_latencies[i]),
                         textcoords="offset points", xytext=(0, -18),
                         ha='center', fontweight='bold', color=color_wc)

        ax2.set_ylabel('Avg Worst-Case Monitor Latency (ms)', fontsize=12,
                       color=color_wc, labelpad=10)
        ax2.tick_params(axis='y', labelcolor=color_wc)
        ax2.set_ylim(0, 230)
        ax2.set_yticks(range(0, 231, 10))

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2,
                   frameon=True, facecolor='white', framealpha=0.9, fontsize=10,
                   loc='upper right')

        test_label = test_name.upper()
        mode_label = auth_mode.capitalize() + " Auth"
        plot_title = f"{test_label}: Monitor Latency vs. Session Key Relative Validity — {mode_label}"
        file_stem  = f"{test_name}_{auth_mode}_validity_vs_monitor_latency"

        fig.suptitle(plot_title, fontsize=14, fontweight='bold', y=1.01)
        fig.tight_layout()

        png_path = output_dir / f"{file_stem}.png"
        pdf_path = output_dir / f"{file_stem}.pdf"
        fig.savefig(png_path, bbox_inches='tight')
        fig.savefig(pdf_path, bbox_inches='tight')
        plt.close(fig)

        print(f"📈 Graph (PNG) saved to: {png_path}")
        print(f"📈 Graph (PDF) saved to: {pdf_path}")
    except Exception as e:
        print(f"❌ Error generating graph: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Motion Threshold vs. Bypass Rate & Latency
# ─────────────────────────────────────────────────────────────────────────────

def discover_test2_reports(reports_dir: Path) -> tuple[list, int]:
    pattern = re.compile(r"thresh_(\d+(?:\.\d+)?)_run_(\d+)\.txt", re.IGNORECASE)
    threshold_set = set()
    runs_per_thresh = {}

    for f in reports_dir.glob("thresh_*_run_*.txt"):
        m = pattern.match(f.name)
        if m:
            val = float(m.group(1))
            run = int(m.group(2))
            threshold_set.add(val)
            runs_per_thresh[val] = max(runs_per_thresh.get(val, 0), run)

    if not threshold_set:
        print(f"❌ Error: No thresh_*_run_*.txt files found in {reports_dir}")
        sys.exit(1)

    thresholds = sorted(threshold_set)
    runs = max(runs_per_thresh.values())
    print(f"🔍 Auto-discovered Test 2: thresholds={thresholds}, runs={runs}")
    return thresholds, runs


def load_test2_reports(reports_dir: Path, thresholds: list, runs: int, bypass_mode: str = "still") -> tuple[dict, dict, dict]:
    results = {t: [] for t in thresholds}
    wc_results = {t: [] for t in thresholds}
    bypass_results = {t: [] for t in thresholds}

    for t in thresholds:
        for r in range(1, runs + 1):
            file_candidates = [
                reports_dir / f"thresh_{t}_run_{r}.txt",
                reports_dir / f"thresh_{int(t) if t == int(t) else t}_run_{r}.txt",
            ]
            lat = 0.0
            wc_lat = 0.0
            b_val = 0.0
            found_file = None
            for cand in file_candidates:
                if cand.exists():
                    found_file = cand
                    break

            if found_file:
                content = found_file.read_text()
                avg_match = re.search(r"Average Monitor Latency:\s*([\d\.]+)\s*ms", content, re.IGNORECASE)
                wc_match  = re.search(r"Worst-Case Monitor Lat\.:\s*([\d\.]+)\s*ms", content, re.IGNORECASE)
                active_match = re.search(r"Active Rate:\s*([\d\.]+)\s*%", content, re.IGNORECASE)
                still_match  = re.search(r"Still Rate \(Bypass\):\s*([\d\.]+)\s*%", content, re.IGNORECASE)

                if avg_match:
                    lat = float(avg_match.group(1))
                if wc_match:
                    wc_lat = float(wc_match.group(1))
                else:
                    wc_lat = lat

                if bypass_mode.lower() == "active":
                    b_val = float(active_match.group(1)) if active_match else 0.0
                else:
                    b_val = float(still_match.group(1)) if still_match else 0.0

                print(f"✅ Loaded {found_file.name} -> avg: {lat:.2f} ms, bypass({bypass_mode}): {b_val:.2f}%")
            else:
                print(f"⚠️  Warning: Report not found for threshold {t} run {r} in {reports_dir}")

            results[t].append(lat)
            wc_results[t].append(wc_lat)
            bypass_results[t].append(b_val)

    return results, wc_results, bypass_results


def generate_test2_plots_and_reports(thresholds: list, results: dict, worst_case_results: dict,
                                     bypass_results: dict, output_dir: Path, test_name: str = "test2",
                                     auth_mode: str = "local", bypass_mode: str = "still"):
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    avg_latencies = []
    std_latencies = []
    avg_bypass_rates = []

    for t in thresholds:
        runs = results[t]
        avg = sum(runs) / len(runs) if runs else 0.0
        variance = sum((x - avg) ** 2 for x in runs) / len(runs) if len(runs) > 1 else 0.0
        std_val = variance ** 0.5

        b_runs = bypass_results[t]
        avg_b = sum(b_runs) / len(b_runs) if b_runs else 0.0

        avg_latencies.append(avg)
        std_latencies.append(std_val)
        avg_bypass_rates.append(avg_b)

        summary_rows.append({
            "threshold": t,
            "runs_lat": runs,
            "avg_lat": avg,
            "std_lat": std_val,
            "avg_bypass": avg_b,
        })

    # Console table
    print("\n" + "=" * 80)
    print(f"MOTION THRESHOLD vs. BYPASS RATE ({bypass_mode.upper()}) & MONITOR LATENCY")
    print("=" * 80)
    num_runs = len(list(results.values())[0])
    header = (f"{'Threshold':>10} | "
              + " | ".join([f"Run {i+1} (ms)".rjust(10) for i in range(num_runs)])
              + f" | {'Average (ms)':>12} | {'Std Dev':>10} | {'AvgBypass (%)':>14}")
    print(header)
    print("-" * len(header))
    for row in summary_rows:
        runs_str = " | ".join([f"{r:>10.2f}" for r in row["runs_lat"]])
        print(f"{row['threshold']:>10.2f} | {runs_str} | {row['avg_lat']:>12.2f} | {row['std_lat']:>10.2f} | {row['avg_bypass']:>14.2f}%")
    print("=" * 80)

    # CSV
    csv_path = output_dir / f"threshold_vs_bypass_{bypass_mode}_latency.csv"
    with open(csv_path, "w") as f:
        run_headers = ",".join([f"Run_{i+1}_ms" for i in range(num_runs)])
        f.write(f"Threshold,{run_headers},Average_ms,StdDev_ms,AvgBypassRate_pct\n")
        for row in summary_rows:
            runs_csv = ",".join([f"{r:.4f}" for r in row["runs_lat"]])
            f.write(f"{row['threshold']},{runs_csv},{row['avg_lat']:.4f},{row['std_lat']:.4f},{row['avg_bypass']:.4f}\n")
    print(f"📊 CSV saved to: {csv_path}")

    # Text report
    txt_path = output_dir / f"threshold_vs_bypass_{bypass_mode}_report.txt"
    with open(txt_path, "w") as f:
        f.write(f"MOTION THRESHOLD vs. BYPASS RATE ({bypass_mode.upper()}) & LATENCY REPORT\n")
        f.write("===============================================================\n")
        f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(header + "\n")
        f.write("-" * len(header) + "\n")
        for row in summary_rows:
            runs_str = " | ".join([f"{r:>10.2f}" for r in row["runs_lat"]])
            f.write(f"{row['threshold']:>10.2f} | {runs_str} | {row['avg_lat']:>12.2f}"
                    f" | {row['std_lat']:>10.2f} | {row['avg_bypass']:>14.2f}%\n")
    print(f"📄 Text report saved to: {txt_path}")

    # Graph
    if not MATPLOTLIB_AVAILABLE:
        print("\n⚠️  Warning: matplotlib is not available.")
        return

    try:
        fig, ax1 = plt.subplots(figsize=(11, 6), dpi=300)

        color_lat = '#1f77b4'   # blue  — left axis
        color_ind = '#2ca02c'   # green — individual scatter
        color_byp = '#ff7f0e'   # orange— right axis

        ax1.errorbar(thresholds, avg_latencies, yerr=std_latencies,
                     marker='o', markersize=8, linewidth=2.5,
                     capsize=6, capthick=2, color=color_lat,
                     label='Avg Monitor Latency (±1 Std Dev)', ecolor='#d62728')
        for i, t in enumerate(thresholds):
            ax1.scatter([t] * len(results[t]), results[t],
                        color=color_ind, alpha=0.6, s=30,
                        label='Individual Runs' if i == 0 else "")
            ax1.annotate(f"{avg_latencies[i]:.2f} ms", (t, avg_latencies[i]),
                         textcoords="offset points", xytext=(0, 12),
                         ha='center', fontweight='bold', color=color_lat)

        ax1.set_xlabel('Motion Threshold Value (0.0 to 1.0)', fontsize=12, labelpad=10)
        ax1.set_ylabel('Avg Monitor Latency (ms)', fontsize=12, color=color_lat, labelpad=10)
        ax1.tick_params(axis='y', labelcolor=color_lat)
        ax1.set_ylim(0, 100)
        ax1.set_yticks(range(0, 101, 10))
        ax1.set_xticks(thresholds)
        ax1.set_xticklabels([f"{t}" for t in thresholds], fontsize=11)
        ax1.grid(True, linestyle='--', alpha=0.4)

        ax2 = ax1.twinx()
        bypass_label_str = 'Active Rate (%) [Actions requiring Auth]' if bypass_mode.lower() == 'active' else 'Bypass Rate (%) [Still Actions Bypassed]'
        ax2.plot(thresholds, avg_bypass_rates,
                 marker='s', markersize=8, linewidth=2.5,
                 linestyle='--', color=color_byp,
                 label=bypass_label_str)
        for i, t in enumerate(thresholds):
            ax2.annotate(f"{avg_bypass_rates[i]:.1f}%", (t, avg_bypass_rates[i]),
                         textcoords="offset points", xytext=(0, -18),
                         ha='center', fontweight='bold', color=color_byp)

        ax2.set_ylabel(bypass_label_str, fontsize=12, color=color_byp, labelpad=10)
        ax2.tick_params(axis='y', labelcolor=color_byp)
        ax2.set_ylim(0, 100)
        ax2.set_yticks(range(0, 101, 10))

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2,
                   frameon=True, facecolor='white', framealpha=0.9, fontsize=10,
                   loc='upper right')

        test_label = test_name.upper()
        mode_label = auth_mode.capitalize() + " Auth"
        plot_title = f"{test_label}: Motion Threshold vs. {bypass_mode.capitalize()} Rate & Monitor Latency — {mode_label}"
        file_stem  = f"{test_name}_{auth_mode}_threshold_vs_{bypass_mode}_and_latency"

        fig.suptitle(plot_title, fontsize=14, fontweight='bold', y=1.01)
        fig.tight_layout()

        png_path = output_dir / f"{file_stem}.png"
        pdf_path = output_dir / f"{file_stem}.pdf"
        fig.savefig(png_path, bbox_inches='tight')
        fig.savefig(pdf_path, bbox_inches='tight')
        plt.close(fig)

        print(f"📈 Graph (PNG) saved to: {png_path}")
        print(f"📈 Graph (PDF) saved to: {pdf_path}")
    except Exception as e:
        print(f"❌ Error generating graph: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main Orchestration
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Aggregate latency report files and generate graphs. "
                    "Can be run standalone on any existing test_reports/ run folder."
    )
    parser.add_argument(
        "--reports-dir", required=True,
        help="Directory containing report files (e.g. test_reports/test1/local/2026-07-06-10-38-00)."
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Where to write the CSV, text report, and graph. Defaults to --reports-dir."
    )
    parser.add_argument(
        "--test-name", default=None, choices=["test1", "test2", None],
        help="Test identifier for plot title and output filename (test1 or test2). "
             "Auto-inferred from the reports-dir path if not specified."
    )
    parser.add_argument(
        "--auth-mode", default=None, choices=["local", "remote", None],
        help="Auth mode for plot title and output filename (local or remote). "
             "Auto-inferred from the reports-dir path if not specified."
    )
    parser.add_argument(
        "--bypass-mode", default="still", choices=["still", "active"],
        help="Definition of bypass rate for Test 2 graph: 'still' (percentage of still actions bypassed) or 'active' (percentage of active actions requiring auth)."
    )
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir).resolve()
    output_dir  = Path(args.output_dir).resolve() if args.output_dir else reports_dir

    if not reports_dir.exists():
        print(f"❌ Error: reports directory does not exist: {reports_dir}")
        sys.exit(1)

    inferred_test, inferred_mode = infer_test_context(reports_dir)
    test_name = args.test_name or inferred_test or "test1"
    auth_mode = args.auth_mode or inferred_mode or "local"
    if args.test_name is None and inferred_test:
        print(f"🔍 Auto-inferred test name: {test_name}")
    if args.auth_mode is None and inferred_mode:
        print(f"🔍 Auto-inferred auth mode: {auth_mode}")

    # Check if there are any Test 2 reports (thresh_*_run_*.txt) or explicitly requested test2
    is_test2 = (test_name.lower() == "test2") or list(reports_dir.glob("thresh_*_run_*.txt"))
    if is_test2:
        test_name = "test2"
        thresholds, runs = discover_test2_reports(reports_dir)
        print("=" * 80)
        print("AGGREGATING TEST 2 REPORTS & GENERATING GRAPH (Threshold vs Bypass & Latency)")
        print("=" * 80)
        print(f"Reports dir : {reports_dir}")
        print(f"Output dir  : {output_dir}")
        print(f"Thresholds  : {thresholds}")
        print(f"Runs        : {runs} per threshold condition")
        print(f"Bypass Mode : {args.bypass_mode}")
        print(f"Test / Mode : {test_name} / {auth_mode}")
        print("=" * 80)

        results, wc_results, bypass_results = load_test2_reports(reports_dir, thresholds, runs, bypass_mode=args.bypass_mode)
        generate_test2_plots_and_reports(thresholds, results, wc_results, bypass_results,
                                         output_dir, test_name=test_name, auth_mode=auth_mode, bypass_mode=args.bypass_mode)
    else:
        validities, runs = discover_reports(reports_dir)
        print("=" * 80)
        print("AGGREGATING TEST 1 REPORTS & GENERATING GRAPH")
        print("=" * 80)
        print(f"Reports dir : {reports_dir}")
        print(f"Output dir  : {output_dir}")
        print(f"Validities  : {[int(v) for v in validities]} seconds")
        print(f"Runs        : {runs} per validity period")
        print(f"Test / Mode : {test_name} / {auth_mode}")
        print("=" * 80)

        results, worst_case_results = load_reports(reports_dir, validities, runs)
        generate_plots_and_reports(validities, results, worst_case_results,
                                   output_dir, test_name=test_name, auth_mode=auth_mode)
    print("\n✅ Done!\n")


if __name__ == "__main__":
    main()
