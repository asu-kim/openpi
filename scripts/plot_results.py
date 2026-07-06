#!/usr/bin/env python3
"""
plot_results.py — Standalone graph generator for openpi latency test reports
=============================================================================
Reads pre-generated per-run report files (val_Xs_run_Y.txt) from a timestamped
test_reports/ directory, aggregates the average and worst-case monitor latency
values, writes a CSV + text summary, and produces a dual-Y-axis matplotlib graph.

Can be run independently on any existing test_reports/ run folder to regenerate
or tweak graphs without re-running the full simulation:

    python scripts/plot_results.py \\
        --reports-dir test_reports/test1/local/2026-07-06-10-38-00 \\
        --output-dir  test_reports/test1/local/2026-07-06-10-38-00 \\
        --runs 5 --validities 1 3 5 7 \\
        --test-name test1 --auth-mode local
"""

import os
import sys
import time
import re
import argparse
import subprocess
from pathlib import Path

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
    print(f"🔍 Auto-discovered: validities={[int(v) for v in validities]}s, runs={runs}")
    return validities, runs


def infer_test_context(reports_dir: Path) -> tuple[str, str]:
    """
    Infer test_name and auth_mode from the reports_dir path.
    Expected structure: ...test_reports/<test_name>/<auth_mode>/<timestamp>/
    Returns (test_name, auth_mode) or (None, None) if structure doesn't match.
    """
    parts = reports_dir.parts
    # Walk up the path looking for a 'test_reports' anchor
    for i, part in enumerate(parts):
        if part == "test_reports" and i + 2 < len(parts):
            test_name = parts[i + 1]   # e.g. 'test1'
            auth_mode = parts[i + 2]   # e.g. 'local' or 'remote'
            return test_name, auth_mode
    return None, None


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
                    wc_lat = lat  # fall back to avg if worst-case line is missing
                    print(f" (worst-case not found, using avg)")
            else:
                print(f"⚠️  Warning: Report not found for validity {val}s run {r} in {reports_dir}")

            results[val].append(lat)
            worst_case_results[val].append(wc_lat)

    return results, worst_case_results


def generate_plots_and_reports(validities: list, results: dict, worst_case_results: dict,
                                output_dir: Path, test_name: str = "test1", auth_mode: str = "local"):
    """
    Compute summary statistics, write CSV + text report, and generate a dual-Y-axis
    matplotlib graph saved as PNG and PDF into output_dir.
    """
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

    # ── Console table ─────────────────────────────────────────────────────────
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

    # ── CSV ───────────────────────────────────────────────────────────────────
    csv_path = output_dir / "validity_vs_latency_same_device.csv"
    with open(csv_path, "w") as f:
        run_headers = ",".join([f"Run_{i+1}_ms" for i in range(num_runs)])
        f.write(f"Validity_sec,{run_headers},Average_ms,StdDev_ms,Min_ms,Max_ms,AvgWorstCase_ms\n")
        for row in summary_rows:
            runs_csv = ",".join([f"{r:.4f}" for r in row["runs"]])
            f.write(f"{row['validity_sec']},{runs_csv},{row['avg']:.4f},{row['std']:.4f}"
                    f",{row['min']:.4f},{row['max']:.4f},{row['avg_wc']:.4f}\n")
    print(f"📊 CSV saved to: {csv_path}")

    # ── Text report ───────────────────────────────────────────────────────────
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

    # ── Graph ─────────────────────────────────────────────────────────────────
    if not MATPLOTLIB_AVAILABLE:
        print("\n⚠️  Warning: matplotlib is not available. Install it and re-run:")
        print(f"  python scripts/plot_results.py --reports-dir {output_dir} ...")
        return

    try:
        fig, ax1 = plt.subplots(figsize=(11, 6), dpi=300)

        color_avg = '#1f77b4'   # blue  — left axis
        color_ind = '#2ca02c'   # green — individual scatter
        color_wc  = '#d62728'   # red   — right axis

        # Left Y-axis: average monitor latency with std-dev error bars
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
        ax1.set_xticks(validities)
        ax1.set_xticklabels([f"{v}s" for v in validities], fontsize=11)
        ax1.grid(True, linestyle='--', alpha=0.4)

        # Right Y-axis: average of worst-case monitor latency per run
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

        # Combined legend
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2,
                   frameon=True, facecolor='white', framealpha=0.9, fontsize=10,
                   loc='upper left')

        # Dynamic title and filenames
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


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate latency report files and generate graphs. "
                    "Can be run standalone on any existing test_reports/ run folder."
    )
    parser.add_argument(
        "--reports-dir", required=True,
        help="Directory containing val_Xs_run_Y.txt report files (e.g. test_reports/test1/local/2026-07-06-10-38-00)."
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Where to write the CSV, text report, and graph. Defaults to --reports-dir."
    )
    parser.add_argument(
        "--test-name", default=None,
        help="Test identifier for plot title and output filename (e.g. test1). "
             "Auto-inferred from the reports-dir path if not specified."
    )
    parser.add_argument(
        "--auth-mode", default=None, choices=["local", "remote", None],
        help="Auth mode for plot title and output filename (local or remote). "
             "Auto-inferred from the reports-dir path if not specified."
    )
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir).resolve()
    output_dir  = Path(args.output_dir).resolve() if args.output_dir else reports_dir

    if not reports_dir.exists():
        print(f"❌ Error: reports directory does not exist: {reports_dir}")
        sys.exit(1)

    # Auto-discover validity periods and run count from the report filenames
    validities, runs = discover_reports(reports_dir)

    # Infer test_name and auth_mode from path; allow explicit override via flags
    inferred_test, inferred_mode = infer_test_context(reports_dir)
    test_name = args.test_name or inferred_test or "test1"
    auth_mode = args.auth_mode or inferred_mode or "local"
    if args.test_name is None and inferred_test:
        print(f"🔍 Auto-inferred test name: {test_name}")
    if args.auth_mode is None and inferred_mode:
        print(f"🔍 Auto-inferred auth mode: {auth_mode}")

    print("=" * 80)
    print("AGGREGATING LATENCY REPORTS & GENERATING GRAPH")
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
