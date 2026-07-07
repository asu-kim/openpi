#!/usr/bin/env python3
"""
plot_results.py — Standalone graph generator for openpi latency test reports
=============================================================================
Reads pre-generated per-run report files from a timestamped test_reports/ directory:
  - Test 1 (val_Xs_run_Y.txt): Validity vs. Average & Worst-Case Monitor Latency
  - Test 2 (thresh_X_run_Y.txt): Motion Threshold vs. Bypass Rate (%) & Latency

Test 1 pipeline (single source of truth):
  val_*s_run_*.txt  →  validity_vs_latency.csv  →  PNG/PDF graphs
  The CSV is always written first; graphs are always rendered from it.

Can be run independently on any existing test_reports/ run folder:

    # Single-mode (generates combined avg+worst-case graph)
    python scripts/plot_results.py \
        --reports-dir test_reports/test1/local/2026-07-06-10-38-00

    # Comparative (generates separate avg and worst-case graphs, local vs remote)
    python scripts/plot_results.py \
        --reports-dir test_reports/test1/local/2026-07-06-10-38-00 \
        --compare-csv test_reports/test1/remote/2026-07-06-11-00-00/validity_vs_latency.csv

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


def infer_test_context(reports_dir: Path) -> tuple[str, str, str]:
    """
    Infer test_name, auth_mode, and bypass_mode from the reports_dir path.
    Expected structure:
      Test 1: ...test_reports/<test_name>/<auth_mode>/<timestamp>/
      Test 2: ...test_reports/<test_name>/<auth_mode>/<bypass_mode>/<timestamp>/
    Returns (test_name, auth_mode, bypass_mode) or (None, None, None) if structure doesn't match.
    """
    parts = reports_dir.parts
    for i, part in enumerate(parts):
        if part == "test_reports" and i + 2 < len(parts):
            test_name = parts[i + 1]   # e.g. 'test1' or 'test2'
            auth_mode = parts[i + 2]   # e.g. 'local' or 'remote'
            bypass_mode = None
            if i + 3 < len(parts) and parts[i + 3] in ["still", "active"]:
                bypass_mode = parts[i + 3]
            return test_name, auth_mode, bypass_mode
    return None, None, None


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


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 CSV helpers — single source of truth for graph generation
# ─────────────────────────────────────────────────────────────────────────────

TEST1_CSV_NAME = "validity_vs_latency.csv"


def write_test1_csv(validities: list, avg_latencies: list, avg_wc_latencies: list,
                    output_dir: Path) -> Path:
    """
    Write the aggregated Test 1 summary CSV.
    Format: Validity_sec, Average_ms, AvgWorstCase_ms  (one row per validity period).
    This is the single source of truth: graphs are always rendered from this file.
    """
    csv_path = output_dir / TEST1_CSV_NAME
    with open(csv_path, "w") as f:
        f.write("Validity_sec,Average_ms,AvgWorstCase_ms\n")
        for val, avg, wc in zip(validities, avg_latencies, avg_wc_latencies):
            f.write(f"{val:.1f},{avg:.4f},{wc:.4f}\n")
    print(f"📊 CSV saved to: {csv_path}")
    return csv_path


def read_test1_csv(csv_path: Path) -> tuple[list, list, list]:
    """
    Read a Test 1 summary CSV and return (validities, avg_latencies, avg_wc_latencies).
    """
    validities, avg_latencies, avg_wc_latencies = [], [], []
    with open(csv_path) as f:
        f.readline()  # skip header
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) >= 3:
                validities.append(float(parts[0]))
                avg_latencies.append(float(parts[1]))
                avg_wc_latencies.append(float(parts[2]))
    return validities, avg_latencies, avg_wc_latencies


def _plot_single_mode(validities: list, avg_latencies: list, avg_wc_latencies: list,
                      output_dir: Path, test_name: str, auth_mode: str):
    """Render the single-mode graph (avg latency + worst-case on twin axes) from CSV data."""
    try:
        color_avg = '#1f77b4'
        color_wc  = '#d62728'

        fig, ax1 = plt.subplots(figsize=(11, 6), dpi=300)

        ax1.plot(validities, avg_latencies,
                 marker='o', markersize=8, linewidth=2.5, color=color_avg,
                 label='Avg Monitor Latency')
        for i, val in enumerate(validities):
            ax1.annotate(f"{avg_latencies[i]:.2f} ms", (val, avg_latencies[i]),
                         textcoords="offset points", xytext=(0, 12),
                         ha='center', fontweight='bold', color=color_avg)

        ax1.set_xlabel('Relative Validity Period (seconds)', fontsize=12, labelpad=10)
        ax1.set_ylabel('Avg Monitor Latency (ms)', fontsize=12, color=color_avg, labelpad=10)
        ax1.tick_params(axis='y', labelcolor=color_avg)
        ax1.set_ylim(0, max(max(avg_latencies, default=0) * 1.4, 20))
        ax1.set_xticks(validities)
        ax1.set_xticklabels([f"{int(v)}s" for v in validities], fontsize=11)
        ax1.grid(True, linestyle='--', alpha=0.4)

        ax2 = ax1.twinx()
        ax2.plot(validities, avg_wc_latencies,
                 marker='s', markersize=8, linewidth=2.5,
                 linestyle='--', color=color_wc,
                 label='Avg Worst-Case Monitor Latency')
        for i, val in enumerate(validities):
            ax2.annotate(f"{avg_wc_latencies[i]:.2f} ms", (val, avg_wc_latencies[i]),
                         textcoords="offset points", xytext=(0, -18),
                         ha='center', fontweight='bold', color=color_wc)

        ax2.set_ylabel('Avg Worst-Case Monitor Latency (ms)', fontsize=12,
                       color=color_wc, labelpad=10)
        ax2.tick_params(axis='y', labelcolor=color_wc)
        ax2.set_ylim(0, max(max(avg_wc_latencies, default=0) * 1.4, 20))

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

        fig.savefig(output_dir / f"{file_stem}.png", bbox_inches='tight')
        fig.savefig(output_dir / f"{file_stem}.pdf", bbox_inches='tight')
        plt.close(fig)
        print(f"📈 Graph (PNG) saved to: {output_dir / file_stem}.png")
        print(f"📈 Graph (PDF) saved to: {output_dir / file_stem}.pdf")
    except Exception as e:
        print(f"❌ Error generating single-mode graph: {e}")


def generate_plots_and_reports(validities: list, results: dict, worst_case_results: dict,
                                output_dir: Path, test_name: str = "test1",
                                auth_mode: str = "local") -> Path:
    """
    Test 1 pipeline:
      1. Compute per-validity averages from raw run dicts.
      2. Write validity_vs_latency.csv  (single source of truth).
      3. Read the CSV back and render the single-mode graph from it.
    Returns the path to the written CSV.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1 — compute simple averages (one number per validity period)
    avg_latencies, avg_wc_latencies, summary_rows = [], [], []
    for val in validities:
        runs = results[val]
        avg = sum(runs) / len(runs) if runs else 0.0
        wc_runs = worst_case_results.get(val, [])
        avg_wc = sum(wc_runs) / len(wc_runs) if wc_runs else 0.0
        avg_latencies.append(avg)
        avg_wc_latencies.append(avg_wc)
        summary_rows.append({"validity_sec": val, "avg": avg, "avg_wc": avg_wc})

    # Console table
    print("\n" + "=" * 62)
    print("VALIDITY vs. MONITOR LATENCY TEST RESULTS")
    print("=" * 62)
    header = f"{'Validity (s)':>12} | {'Average (ms)':>14} | {'Avg Worst-Case (ms)':>20}"
    print(header)
    print("-" * len(header))
    for row in summary_rows:
        print(f"{row['validity_sec']:>12.1f} | {row['avg']:>14.2f} | {row['avg_wc']:>20.2f}")
    print("=" * 62)

    # Text report
    txt_path = output_dir / "validity_vs_latency_report.txt"
    with open(txt_path, "w") as f:
        f.write("VALIDITY PERIOD vs. MONITOR LATENCY TEST REPORT\n")
        f.write("===============================================\n")
        f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(header + "\n")
        f.write("-" * len(header) + "\n")
        for row in summary_rows:
            f.write(f"{row['validity_sec']:>12.1f} | {row['avg']:>14.2f} | {row['avg_wc']:>20.2f}\n")
    print(f"📄 Text report saved to: {txt_path}")

    # Step 2 — write CSV (source of truth)
    csv_path = write_test1_csv(validities, avg_latencies, avg_wc_latencies, output_dir)

    if not MATPLOTLIB_AVAILABLE:
        print("\n⚠️  Warning: matplotlib is not available — graphs skipped.")
        return csv_path

    # Step 3 — render graph by reading back from CSV
    v, avg_lats, avg_wc_lats = read_test1_csv(csv_path)
    _plot_single_mode(v, avg_lats, avg_wc_lats, output_dir, test_name, auth_mode)

    return csv_path


def generate_comparative_plots(local_csv: Path, remote_csv: Path,
                               output_dir: Path, test_name: str = "test1"):
    """
    Generate two separate comparison plots for Test 1 by reading directly from
    two pre-existing validity_vs_latency.csv files (local and remote):
      1. Average Latency     — Local vs. Remote
      2. Worst-Case Latency  — Local vs. Remote
    Both CSVs must exist; this function does not re-read any txt files.
    """
    if not MATPLOTLIB_AVAILABLE:
        print("\n⚠️  Warning: matplotlib is not available — comparative plots skipped.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    # Read both CSVs (single source of truth)
    local_v,  local_avg,  local_wc  = read_test1_csv(local_csv)
    remote_v, remote_avg, remote_wc = read_test1_csv(remote_csv)

    # Intersect on common validity periods
    local_map_avg  = dict(zip(local_v,  local_avg))
    local_map_wc   = dict(zip(local_v,  local_wc))
    remote_map_avg = dict(zip(remote_v, remote_avg))
    remote_map_wc  = dict(zip(remote_v, remote_wc))

    common_v = sorted(set(local_v) & set(remote_v))
    if not common_v:
        print("⚠️  No common validity periods between local and remote CSVs. "
              "Cannot generate comparative plots.")
        return

    l_avg = [local_map_avg[v]  for v in common_v]
    l_wc  = [local_map_wc[v]   for v in common_v]
    r_avg = [remote_map_avg[v] for v in common_v]
    r_wc  = [remote_map_wc[v]  for v in common_v]

    x_labels   = [f"{int(v)}s" for v in common_v]
    test_label = test_name.upper()

    # ── Plot 1: Average Latency (Local vs Remote) ────────────────────────────
    try:
        fig, ax = plt.subplots(figsize=(11, 6), dpi=300)

        color_local  = '#1f77b4'   # blue
        color_remote = '#d62728'   # red

        ax.plot(common_v, l_avg, marker='o', markersize=8, linewidth=2.5,
                color=color_local,  label='Local Auth — Avg Latency')
        ax.plot(common_v, r_avg, marker='s', markersize=8, linewidth=2.5,
                color=color_remote, label='Remote Auth — Avg Latency', linestyle='--')

        for i, val in enumerate(common_v):
            ax.annotate(f"{l_avg[i]:.2f}", (val, l_avg[i]),
                        textcoords="offset points", xytext=(-18, 8),
                        ha='center', fontsize=9, fontweight='bold', color=color_local)
            ax.annotate(f"{r_avg[i]:.2f}", (val, r_avg[i]),
                        textcoords="offset points", xytext=(18, 8),
                        ha='center', fontsize=9, fontweight='bold', color=color_remote)

        ax.set_xlabel('Relative Validity Period (seconds)', fontsize=12, labelpad=10)
        ax.set_ylabel('Average Monitor Latency (ms)', fontsize=12, labelpad=10)
        ax.set_xticks(common_v)
        ax.set_xticklabels(x_labels, fontsize=11)
        ax.set_ylim(0, max(max(l_avg + r_avg, default=0) * 1.4, 20))
        ax.grid(True, linestyle='--', alpha=0.4)
        ax.legend(frameon=True, facecolor='white', framealpha=0.9, fontsize=10, loc='upper right')

        plot_title = f"{test_label}: Average Monitor Latency vs. Validity Period — Local vs. Remote Auth"
        fig.suptitle(plot_title, fontsize=14, fontweight='bold', y=1.01)
        fig.tight_layout()

        stem = f"{test_name}_avg_latency_local_vs_remote"
        fig.savefig(output_dir / f"{stem}.png", bbox_inches='tight')
        fig.savefig(output_dir / f"{stem}.pdf", bbox_inches='tight')
        plt.close(fig)
        print(f"📈 Comparative avg-latency graph saved to: {output_dir}/{stem}.png / .pdf")
    except Exception as e:
        print(f"❌ Error generating comparative avg-latency graph: {e}")

    # ── Plot 2: Worst-Case Latency (Local vs Remote) ─────────────────────────
    try:
        fig, ax = plt.subplots(figsize=(11, 6), dpi=300)

        color_local  = '#2ca02c'   # green
        color_remote = '#9467bd'   # purple

        ax.plot(common_v, l_wc, marker='o', markersize=8, linewidth=2.5,
                color=color_local,  label='Local Auth — Avg Worst-Case Latency')
        ax.plot(common_v, r_wc, marker='s', markersize=8, linewidth=2.5,
                color=color_remote, label='Remote Auth — Avg Worst-Case Latency', linestyle='--')

        for i, val in enumerate(common_v):
            ax.annotate(f"{l_wc[i]:.2f}", (val, l_wc[i]),
                        textcoords="offset points", xytext=(-18, 8),
                        ha='center', fontsize=9, fontweight='bold', color=color_local)
            ax.annotate(f"{r_wc[i]:.2f}", (val, r_wc[i]),
                        textcoords="offset points", xytext=(18, 8),
                        ha='center', fontsize=9, fontweight='bold', color=color_remote)

        ax.set_xlabel('Relative Validity Period (seconds)', fontsize=12, labelpad=10)
        ax.set_ylabel('Avg Worst-Case Monitor Latency (ms)', fontsize=12, labelpad=10)
        ax.set_xticks(common_v)
        ax.set_xticklabels(x_labels, fontsize=11)
        ax.set_ylim(0, max(max(l_wc + r_wc, default=0) * 1.4, 20))
        ax.grid(True, linestyle='--', alpha=0.4)
        ax.legend(frameon=True, facecolor='white', framealpha=0.9, fontsize=10, loc='upper right')

        plot_title = f"{test_label}: Worst-Case Monitor Latency vs. Validity Period — Local vs. Remote Auth"
        fig.suptitle(plot_title, fontsize=14, fontweight='bold', y=1.01)
        fig.tight_layout()

        stem = f"{test_name}_worstcase_latency_local_vs_remote"
        fig.savefig(output_dir / f"{stem}.png", bbox_inches='tight')
        fig.savefig(output_dir / f"{stem}.pdf", bbox_inches='tight')
        plt.close(fig)
        print(f"📈 Comparative worst-case graph saved to: {output_dir}/{stem}.png / .pdf")
    except Exception as e:
        print(f"❌ Error generating comparative worst-case graph: {e}")


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

        ax1.set_xlabel('Motion Threshold Value (Peak-to-Peak Joint Variation)', fontsize=12, labelpad=10)
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
    parser.add_argument(
        "--compare-csv", default=None,
        help="(Test 1 only) Path to the other auth mode's validity_vs_latency.csv. "
             "When provided, two separate comparative plots are generated (avg latency "
             "and worst-case latency, each with local and remote on the same graph). "
             "The CSV must have been produced by a completed plot_results.py run."
    )
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir).resolve()
    output_dir  = Path(args.output_dir).resolve() if args.output_dir else reports_dir

    if not reports_dir.exists():
        print(f"❌ Error: reports directory does not exist: {reports_dir}")
        sys.exit(1)

    inferred_test, inferred_mode, inferred_bypass = infer_test_context(reports_dir)
    test_name = args.test_name or inferred_test or "test1"
    auth_mode = args.auth_mode or inferred_mode or "local"
    if args.test_name is None and inferred_test:
        print(f"🔍 Auto-inferred test name: {test_name}")
    if args.auth_mode is None and inferred_mode:
        print(f"🔍 Auto-inferred auth mode: {auth_mode}")
    if args.bypass_mode == "still" and inferred_bypass and inferred_bypass != "still":
        args.bypass_mode = inferred_bypass
        print(f"🔍 Auto-inferred bypass mode: {args.bypass_mode}")

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
        # ── Test 1: validity vs. monitor latency ──────────────────────────────
        compare_dir = Path(args.compare_dir).resolve() if args.compare_dir else None

        # Determine which mode is "primary" and which is the comparison
        primary_mode = auth_mode
        if compare_dir and compare_dir.exists():
            # Infer the compare dir's auth mode from its path
            _, cmp_inferred_mode, _ = infer_test_context(compare_dir)
            cmp_auth_mode = cmp_inferred_mode or ("remote" if primary_mode == "local" else "local")
        else:
            cmp_auth_mode = None
            compare_dir = None

        validities, runs = discover_reports(reports_dir)
        print("=" * 80)
        print("AGGREGATING TEST 1 REPORTS & GENERATING GRAPH")
        print("=" * 80)
        print(f"Reports dir : {reports_dir}")
        print(f"Output dir  : {output_dir}")
        print(f"Validities  : {[int(v) for v in validities]} seconds")
        print(f"Runs        : {runs} per validity period")
        print(f"Test / Mode : {test_name} / {auth_mode}")
        if compare_dir:
            print(f"Compare dir : {compare_dir} ({cmp_auth_mode})")
        print("=" * 80)

        results, worst_case_results = load_reports(reports_dir, validities, runs)

        # Step 1+2: txt → CSV (writes primary dir's validity_vs_latency.csv)
        primary_csv = generate_plots_and_reports(
            validities, results, worst_case_results,
            output_dir, test_name=test_name, auth_mode=auth_mode,
        )

        # Step 3: if a compare CSV is provided, also generate comparative plots
        compare_csv = Path(args.compare_csv).resolve() if args.compare_csv else None
        if compare_csv:
            if not compare_csv.exists():
                print(f"⚠️  --compare-csv path not found: {compare_csv}. "
                      "Skipping comparative plots.")
            else:
                # Infer the compare CSV's auth mode for labeling
                _, cmp_mode, _ = infer_test_context(compare_csv.parent)
                cmp_mode = cmp_mode or ("remote" if auth_mode == "local" else "local")

                # Map local/remote CSV paths correctly regardless of which was primary
                if auth_mode == "local":
                    local_csv, remote_csv = primary_csv, compare_csv
                else:
                    local_csv, remote_csv = compare_csv, primary_csv

                print(f"\n📊 Generating comparative plots from CSVs...")
                print(f"   Local  CSV: {local_csv}")
                print(f"   Remote CSV: {remote_csv}")
                generate_comparative_plots(
                    local_csv, remote_csv, output_dir, test_name=test_name,
                )
    print("\n✅ Done!\n")


if __name__ == "__main__":
    main()
