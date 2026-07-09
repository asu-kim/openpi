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


def write_test1_csv(validities: list, avg_latencies: list, wc_latencies: list,
                    output_dir: Path) -> Path:
    """
    Write the aggregated Test 1 summary CSV.
    Format: Validity_sec, Average_ms, WorstCase_ms  (one row per validity period).
    WorstCase_ms is the absolute maximum worst-case latency observed across all runs.
    This is the single source of truth: graphs are always rendered from this file.
    """
    csv_path = output_dir / TEST1_CSV_NAME
    with open(csv_path, "w") as f:
        f.write("Validity_sec,Average_ms,WorstCase_ms\n")
        for val, avg, wc in zip(validities, avg_latencies, wc_latencies):
            f.write(f"{val:.1f},{avg:.4f},{wc:.4f}\n")
    print(f"📊 CSV saved to: {csv_path}")
    return csv_path


def read_test1_csv(csv_path: Path) -> tuple[list, list, list]:
    """
    Read a Test 1 summary CSV and return (validities, avg_latencies, wc_latencies).
    """
    validities, avg_latencies, wc_latencies = [], [], []
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
                wc_latencies.append(float(parts[2]))
    return validities, avg_latencies, wc_latencies


def _plot_single_mode(validities: list, avg_latencies: list, wc_latencies: list,
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
        ax2.plot(validities, wc_latencies,
                 marker='s', markersize=8, linewidth=2.5,
                 linestyle='--', color=color_wc,
                 label='Worst-Case Monitor Latency')
        for i, val in enumerate(validities):
            ax2.annotate(f"{wc_latencies[i]:.2f} ms", (val, wc_latencies[i]),
                         textcoords="offset points", xytext=(0, -18),
                         ha='center', fontweight='bold', color=color_wc)

        ax2.set_ylabel('Worst-Case Monitor Latency (ms)', fontsize=12,
                       color=color_wc, labelpad=10)
        ax2.tick_params(axis='y', labelcolor=color_wc)
        ax2.set_ylim(0, max(max(wc_latencies, default=0) * 1.4, 20))

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
    avg_latencies, wc_latencies, summary_rows = [], [], []
    for val in validities:
        runs = results[val]
        avg = sum(runs) / len(runs) if runs else 0.0
        wc_runs = worst_case_results.get(val, [])
        wc = max(wc_runs) if wc_runs else 0.0   # absolute worst-case across all runs
        avg_latencies.append(avg)
        wc_latencies.append(wc)
        summary_rows.append({"validity_sec": val, "avg": avg, "wc": wc})

    # Console table
    print("\n" + "=" * 62)
    print("VALIDITY vs. MONITOR LATENCY TEST RESULTS")
    print("=" * 62)
    header = f"{'Validity (s)':>12} | {'Average (ms)':>14} | {'Worst-Case (ms)':>16}"
    print(header)
    print("-" * len(header))
    for row in summary_rows:
        print(f"{row['validity_sec']:>12.1f} | {row['avg']:>14.2f} | {row['wc']:>16.2f}")
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
            f.write(f"{row['validity_sec']:>12.1f} | {row['avg']:>14.2f} | {row['wc']:>16.2f}\n")
    print(f"📄 Text report saved to: {txt_path}")

    # Step 2 — write CSV (source of truth)
    csv_path = write_test1_csv(validities, avg_latencies, wc_latencies, output_dir)

    if not MATPLOTLIB_AVAILABLE:
        print("\n⚠️  Warning: matplotlib is not available — graphs skipped.")
        return csv_path

    # Step 3 — render graph by reading back from CSV
    v, avg_lats, wc_lats = read_test1_csv(csv_path)
    _plot_single_mode(v, avg_lats, wc_lats, output_dir, test_name, auth_mode)

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
                color=color_local,  label='Local Auth — Worst-Case Latency')
        ax.plot(common_v, r_wc, marker='s', markersize=8, linewidth=2.5,
                color=color_remote, label='Remote Auth — Worst-Case Latency', linestyle='--')

        for i, val in enumerate(common_v):
            ax.annotate(f"{l_wc[i]:.2f}", (val, l_wc[i]),
                        textcoords="offset points", xytext=(-18, 8),
                        ha='center', fontsize=9, fontweight='bold', color=color_local)
            ax.annotate(f"{r_wc[i]:.2f}", (val, r_wc[i]),
                        textcoords="offset points", xytext=(18, 8),
                        ha='center', fontsize=9, fontweight='bold', color=color_remote)

        ax.set_xlabel('Relative Validity Period (seconds)', fontsize=12, labelpad=10)
        ax.set_ylabel('Worst-Case Monitor Latency (ms)', fontsize=12, labelpad=10)
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


def load_test2_reports(reports_dir: Path, thresholds: list, runs: int, bypass_mode: str = "still") -> tuple[dict, dict, dict, dict]:
    results = {t: [] for t in thresholds}
    wc_results = {t: [] for t in thresholds}
    active_results = {t: [] for t in thresholds}
    still_results = {t: [] for t in thresholds}

    for t in thresholds:
        for r in range(1, runs + 1):
            lat = 0.0
            wc_lat = 0.0
            act_val = 0.0
            st_val = 0.0
            found_file = None
            for cand in sorted(reports_dir.glob(f"thresh_*_run_{r}.txt")):
                m = re.match(r"thresh_([\d\.]+)_run_\d+\.txt", cand.name, re.IGNORECASE)
                if m and abs(float(m.group(1)) - t) < 1e-6:
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

                act_val = float(active_match.group(1)) if active_match else 0.0
                st_val  = float(still_match.group(1)) if still_match else 0.0

                print(f"✅ Loaded {found_file.name} -> avg: {lat:.2f} ms, active: {act_val:.2f}%, still: {st_val:.2f}%")
            else:
                print(f"⚠️  Warning: Report not found for threshold {t} run {r} in {reports_dir}")

            results[t].append(lat)
            wc_results[t].append(wc_lat)
            active_results[t].append(act_val)
            still_results[t].append(st_val)

    return results, wc_results, active_results, still_results


TEST2_CSV_NAME = "threshold_vs_latency.csv"


def write_test2_csv(thresholds: list, avg_latencies: list, wc_latencies: list,
                    active_rates: list, still_rates: list, output_dir: Path) -> Path:
    """
    Write the aggregated Test 2 summary CSV.
    Format: Threshold,Average_ms,WorstCase_ms,Active_Rate_Pct,Still_Rate_Pct
    This is the single source of truth: graphs are always rendered from this file.
    """
    csv_path = output_dir / TEST2_CSV_NAME
    with open(csv_path, "w") as f:
        f.write("Threshold,Average_ms,WorstCase_ms,Active_Rate_Pct,Still_Rate_Pct\n")
        for t, avg, wc, act, st in zip(thresholds, avg_latencies, wc_latencies, active_rates, still_rates):
            f.write(f"{t:.4f},{avg:.4f},{wc:.4f},{act:.2f},{st:.2f}\n")
    print(f"📊 CSV saved to: {csv_path}")
    return csv_path


def read_test2_csv(csv_path: Path) -> tuple[list, list, list, list, list]:
    """Read a Test 2 summary CSV and return (thresholds, avg_latencies, wc_latencies, active_rates, still_rates)."""
    thresholds, avg_latencies, wc_latencies = [], [], []
    active_rates, still_rates = [], []
    with open(csv_path) as f:
        f.readline()  # skip header
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) >= 3:
                thresholds.append(float(parts[0]))
                avg_latencies.append(float(parts[1]))
                wc_latencies.append(float(parts[2]))
                act = float(parts[3]) if len(parts) >= 4 else 0.0
                st  = float(parts[4]) if len(parts) >= 5 else 0.0
                active_rates.append(act)
                still_rates.append(st)
    return thresholds, avg_latencies, wc_latencies, active_rates, still_rates


def _add_rate_twinx(ax, x_coords, active_rates, still_rates, show_active: bool, show_still: bool):
    if not show_active and not show_still:
        return
    ax2 = ax.twinx()
    lines = []
    labels = []
    if show_active and active_rates:
        l1, = ax2.plot(x_coords, active_rates, marker='^', markersize=7, linewidth=2,
                       linestyle=':', color='#ff7f0e', label='% Active Rate')
        lines.append(l1)
        labels.append('% Active Rate')
        for i, x in enumerate(x_coords):
            ax2.annotate(f"{active_rates[i]:.1f}%", (x, active_rates[i]),
                         textcoords="offset points", xytext=(0, -14),
                         ha='center', fontsize=8.5, color='#ff7f0e', fontweight='bold')
    if show_still and still_rates:
        l2, = ax2.plot(x_coords, still_rates, marker='v', markersize=7, linewidth=2,
                       linestyle=':', color='#2ca02c', label='% Still Rate (Bypass)')
        lines.append(l2)
        labels.append('% Still Rate (Bypass)')
        for i, x in enumerate(x_coords):
            ax2.annotate(f"{still_rates[i]:.1f}%", (x, still_rates[i]),
                         textcoords="offset points", xytext=(0, 14),
                         ha='center', fontsize=8.5, color='#2ca02c', fontweight='bold')

    ax2.set_ylabel('Percentage Rate (%)', fontsize=12, color='#555555', labelpad=10)
    ax2.set_ylim(-5, 115)
    l_lines, l_labels = ax.get_legend_handles_labels()
    ax.legend(l_lines + lines, l_labels + labels, frameon=True, facecolor='white', framealpha=0.9, fontsize=10, loc='upper right')


def _plot_test2_single_mode(thresholds: list, avg_latencies: list, wc_latencies: list,
                            active_rates: list, still_rates: list,
                            output_dir: Path, test_name: str, auth_mode: str,
                            show_active: bool = False, show_still: bool = False,
                            equidistant_x: bool = False):
    """Render two separate single-mode graphs for Test 2: Average Latency & Worst-Case Latency."""
    if not MATPLOTLIB_AVAILABLE:
        print("\n⚠️  Warning: matplotlib is not available.")
        return

    test_label = test_name.upper()
    mode_label = auth_mode.capitalize() + " Auth"

    if equidistant_x:
        x_coords = list(range(len(thresholds)))
    else:
        x_coords = thresholds
    x_labels = [f"{t:.4f}" for t in thresholds]

    # ── Graph 1: Average Monitor Latency vs. Threshold ───────────────────────
    try:
        color_lat = '#1f77b4'
        fig, ax = plt.subplots(figsize=(11, 6), dpi=300)
        ax.plot(x_coords, avg_latencies, marker='o', markersize=8, linewidth=2.5,
                color=color_lat, label='Avg Monitor Latency')
        for i, x in enumerate(x_coords):
            ax.annotate(f"{avg_latencies[i]:.2f} ms", (x, avg_latencies[i]),
                         textcoords="offset points", xytext=(0, 12),
                         ha='center', fontweight='bold', color=color_lat)

        ax.set_xlabel('Motion Threshold Value (τ)', fontsize=12, labelpad=10)
        ax.set_ylabel('Average Monitor Latency (ms)', fontsize=12, color=color_lat, labelpad=10)
        ax.set_xticks(x_coords)
        ax.set_xticklabels(x_labels, fontsize=11)
        if equidistant_x:
            ax.set_xlim(-0.4, len(x_coords) - 0.6)
        ax.set_ylim(0, max(max(avg_latencies, default=0) * 1.4, 20))
        ax.grid(True, linestyle='--', alpha=0.4)
        ax.legend(frameon=True, facecolor='white', framealpha=0.9, fontsize=10, loc='upper right')

        _add_rate_twinx(ax, x_coords, active_rates, still_rates, show_active, show_still)

        plot_title = f"{test_label}: Average Monitor Latency vs. Motion Threshold — {mode_label}"
        fig.suptitle(plot_title, fontsize=14, fontweight='bold', y=1.01)
        fig.tight_layout()

        file_stem = f"{test_name}_{auth_mode}_threshold_vs_avg_latency"
        fig.savefig(output_dir / f"{file_stem}.png", bbox_inches='tight')
        fig.savefig(output_dir / f"{file_stem}.pdf", bbox_inches='tight')
        plt.close(fig)
        print(f"📈 Graph (PNG) saved to: {output_dir / file_stem}.png")
        print(f"📈 Graph (PDF) saved to: {output_dir / file_stem}.pdf")
    except Exception as e:
        print(f"❌ Error generating Test 2 avg-latency graph: {e}")

    # ── Graph 2: Worst-Case Monitor Latency vs. Threshold ────────────────────
    try:
        color_wc = '#d62728'
        fig, ax = plt.subplots(figsize=(11, 6), dpi=300)
        ax.plot(x_coords, wc_latencies, marker='s', markersize=8, linewidth=2.5,
                linestyle='--', color=color_wc, label='Worst-Case Monitor Latency')
        for i, x in enumerate(x_coords):
            ax.annotate(f"{wc_latencies[i]:.2f} ms", (x, wc_latencies[i]),
                         textcoords="offset points", xytext=(0, 12),
                         ha='center', fontweight='bold', color=color_wc)

        ax.set_xlabel('Motion Threshold Value (τ)', fontsize=12, labelpad=10)
        ax.set_ylabel('Worst-Case Monitor Latency (ms)', fontsize=12, color=color_wc, labelpad=10)
        ax.set_xticks(x_coords)
        ax.set_xticklabels(x_labels, fontsize=11)
        if equidistant_x:
            ax.set_xlim(-0.4, len(x_coords) - 0.6)
        ax.set_ylim(0, max(max(wc_latencies, default=0) * 1.4, 20))
        ax.grid(True, linestyle='--', alpha=0.4)
        ax.legend(frameon=True, facecolor='white', framealpha=0.9, fontsize=10, loc='upper right')

        _add_rate_twinx(ax, x_coords, active_rates, still_rates, show_active, show_still)

        plot_title = f"{test_label}: Worst-Case Monitor Latency vs. Motion Threshold — {mode_label}"
        fig.suptitle(plot_title, fontsize=14, fontweight='bold', y=1.01)
        fig.tight_layout()

        file_stem = f"{test_name}_{auth_mode}_threshold_vs_worstcase_latency"
        fig.savefig(output_dir / f"{file_stem}.png", bbox_inches='tight')
        fig.savefig(output_dir / f"{file_stem}.pdf", bbox_inches='tight')
        plt.close(fig)
        print(f"📈 Graph (PNG) saved to: {output_dir / file_stem}.png")
        print(f"📈 Graph (PDF) saved to: {output_dir / file_stem}.pdf")
    except Exception as e:
        print(f"❌ Error generating Test 2 worst-case graph: {e}")


def generate_test2_comparative_plots(local_csv: Path, remote_csv: Path,
                                     output_dir: Path, test_name: str = "test2",
                                     show_active: bool = False, show_still: bool = False,
                                     equidistant_x: bool = False):
    """Generate two separate comparative plots for Test 2 from threshold_vs_latency.csv files."""
    if not MATPLOTLIB_AVAILABLE:
        print("\n⚠️  Warning: matplotlib is not available — comparative plots skipped.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    local_t, local_avg, local_wc, l_act_all, l_st_all = read_test2_csv(local_csv)
    remote_t, remote_avg, remote_wc, r_act_all, r_st_all = read_test2_csv(remote_csv)

    local_map_avg = dict(zip(local_t, local_avg))
    local_map_wc  = dict(zip(local_t, local_wc))
    local_map_act = dict(zip(local_t, l_act_all))
    local_map_st  = dict(zip(local_t, l_st_all))
    remote_map_avg = dict(zip(remote_t, remote_avg))
    remote_map_wc  = dict(zip(remote_t, remote_wc))

    common_t = sorted(set(local_t) & set(remote_t))
    if not common_t:
        print("⚠️  No common thresholds between local and remote CSVs.")
        return

    l_avg = [local_map_avg[t]  for t in common_t]
    l_wc  = [local_map_wc[t]   for t in common_t]
    l_act = [local_map_act[t]  for t in common_t]
    l_st  = [local_map_st[t]   for t in common_t]
    r_avg = [remote_map_avg[t] for t in common_t]
    r_wc  = [remote_map_wc[t]  for t in common_t]

    if equidistant_x:
        x_coords = list(range(len(common_t)))
    else:
        x_coords = common_t
    x_labels   = [f"{t:.4f}" for t in common_t]
    test_label = test_name.upper()

    # ── Plot 1: Average Latency (Local vs Remote) ────────────────────────────
    try:
        fig, ax = plt.subplots(figsize=(11, 6), dpi=300)
        ax.plot(x_coords, l_avg, marker='o', markersize=8, linewidth=2.5,
                color='#1f77b4', label='Local Auth — Avg Latency')
        ax.plot(x_coords, r_avg, marker='s', markersize=8, linewidth=2.5,
                color='#d62728', label='Remote Auth — Avg Latency', linestyle='--')

        for i, x in enumerate(x_coords):
            ax.annotate(f"{l_avg[i]:.2f}", (x, l_avg[i]),
                        textcoords="offset points", xytext=(-18, 8),
                        ha='center', fontsize=9, fontweight='bold', color='#1f77b4')
            ax.annotate(f"{r_avg[i]:.2f}", (x, r_avg[i]),
                        textcoords="offset points", xytext=(18, 8),
                        ha='center', fontsize=9, fontweight='bold', color='#d62728')

        ax.set_xlabel('Motion Threshold Value (τ)', fontsize=12, labelpad=10)
        ax.set_ylabel('Average Monitor Latency (ms)', fontsize=12, labelpad=10)
        ax.set_xticks(x_coords)
        ax.set_xticklabels(x_labels, fontsize=11)
        if equidistant_x:
            ax.set_xlim(-0.4, len(x_coords) - 0.6)
        ax.set_ylim(0, max(max(l_avg + r_avg, default=0) * 1.4, 20))
        ax.grid(True, linestyle='--', alpha=0.4)
        ax.legend(frameon=True, facecolor='white', framealpha=0.9, fontsize=10, loc='upper right')

        _add_rate_twinx(ax, x_coords, l_act, l_st, show_active, show_still)

        plot_title = f"{test_label}: Average Monitor Latency vs. Threshold — Local vs. Remote Auth"
        fig.suptitle(plot_title, fontsize=14, fontweight='bold', y=1.01)
        fig.tight_layout()

        stem = f"{test_name}_avg_latency_local_vs_remote"
        fig.savefig(output_dir / f"{stem}.png", bbox_inches='tight')
        fig.savefig(output_dir / f"{stem}.pdf", bbox_inches='tight')
        plt.close(fig)
        print(f"📈 Comparative avg-latency graph saved to: {output_dir}/{stem}.png / .pdf")
    except Exception as e:
        print(f"❌ Error generating Test 2 comparative avg-latency graph: {e}")

    # ── Plot 2: Worst-Case Latency (Local vs Remote) ─────────────────────────
    try:
        fig, ax = plt.subplots(figsize=(11, 6), dpi=300)
        ax.plot(x_coords, l_wc, marker='o', markersize=8, linewidth=2.5,
                color='#2ca02c', label='Local Auth — Worst-Case Latency')
        ax.plot(x_coords, r_wc, marker='s', markersize=8, linewidth=2.5,
                color='#9467bd', label='Remote Auth — Worst-Case Latency', linestyle='--')

        for i, x in enumerate(x_coords):
            ax.annotate(f"{l_wc[i]:.2f}", (x, l_wc[i]),
                        textcoords="offset points", xytext=(-18, 8),
                        ha='center', fontsize=9, fontweight='bold', color='#2ca02c')
            ax.annotate(f"{r_wc[i]:.2f}", (x, r_wc[i]),
                        textcoords="offset points", xytext=(18, 8),
                        ha='center', fontsize=9, fontweight='bold', color='#9467bd')

        ax.set_xlabel('Motion Threshold Value (τ)', fontsize=12, labelpad=10)
        ax.set_ylabel('Worst-Case Monitor Latency (ms)', fontsize=12, labelpad=10)
        ax.set_xticks(x_coords)
        ax.set_xticklabels(x_labels, fontsize=11)
        if equidistant_x:
            ax.set_xlim(-0.4, len(x_coords) - 0.6)
        ax.set_ylim(0, max(max(l_wc + r_wc, default=0) * 1.4, 20))
        ax.grid(True, linestyle='--', alpha=0.4)
        ax.legend(frameon=True, facecolor='white', framealpha=0.9, fontsize=10, loc='upper right')

        _add_rate_twinx(ax, x_coords, l_act, l_st, show_active, show_still)

        plot_title = f"{test_label}: Worst-Case Monitor Latency vs. Threshold — Local vs. Remote Auth"
        fig.suptitle(plot_title, fontsize=14, fontweight='bold', y=1.01)
        fig.tight_layout()

        stem = f"{test_name}_worstcase_latency_local_vs_remote"
        fig.savefig(output_dir / f"{stem}.png", bbox_inches='tight')
        fig.savefig(output_dir / f"{stem}.pdf", bbox_inches='tight')
        plt.close(fig)
        print(f"📈 Comparative worst-case graph saved to: {output_dir}/{stem}.png / .pdf")
    except Exception as e:
        print(f"❌ Error generating Test 2 comparative worst-case graph: {e}")


def generate_test2_plots_and_reports(thresholds: list, results: dict, worst_case_results: dict,
                                     active_results: dict, still_results: dict,
                                     output_dir: Path, test_name: str = "test2",
                                     auth_mode: str = "local",
                                     show_active: bool = False, show_still: bool = False,
                                     equidistant_x: bool = False) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    avg_latencies = []
    wc_latencies = []
    active_rates = []
    still_rates = []

    for t in thresholds:
        runs = results[t]
        avg = sum(runs) / len(runs) if runs else 0.0
        wc_runs = worst_case_results.get(t, [])
        wc = max(wc_runs) if wc_runs else 0.0
        act_runs = active_results.get(t, [])
        act = sum(act_runs) / len(act_runs) if act_runs else 0.0
        st_runs = still_results.get(t, [])
        st = sum(st_runs) / len(st_runs) if st_runs else 0.0

        avg_latencies.append(avg)
        wc_latencies.append(wc)
        active_rates.append(act)
        still_rates.append(st)

        summary_rows.append({
            "threshold": t,
            "avg_lat": avg,
            "wc_lat": wc,
            "active_pct": act,
            "still_pct": st,
        })

    # Console table
    print("\n" + "=" * 80)
    print("MOTION THRESHOLD vs. MONITOR LATENCY & RATES TEST RESULTS")
    print("=" * 80)
    header = f"{'Threshold (τ)':>14} | {'Average (ms)':>14} | {'Worst-Case (ms)':>16} | {'Active (%)':>11} | {'Still (%)':>10}"
    print(header)
    print("-" * len(header))
    for row in summary_rows:
        print(f"{row['threshold']:>14.4f} | {row['avg_lat']:>14.2f} | {row['wc_lat']:>16.2f} | {row['active_pct']:>11.2f} | {row['still_pct']:>10.2f}")
    print("=" * 80)

    # Text report
    txt_path = output_dir / "threshold_vs_latency_report.txt"
    with open(txt_path, "w") as f:
        f.write("MOTION THRESHOLD vs. MONITOR LATENCY & RATES TEST REPORT\n")
        f.write("========================================================\n")
        f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(header + "\n")
        f.write("-" * len(header) + "\n")
        for row in summary_rows:
            f.write(f"{row['threshold']:>14.4f} | {row['avg_lat']:>14.2f} | {row['wc_lat']:>16.2f} | {row['active_pct']:>11.2f} | {row['still_pct']:>10.2f}\n")
    print(f"📄 Text report saved to: {txt_path}")

    # Write CSV (single source of truth)
    csv_path = write_test2_csv(thresholds, avg_latencies, wc_latencies, active_rates, still_rates, output_dir)

    # Render single-mode graphs from CSV
    _plot_test2_single_mode(thresholds, avg_latencies, wc_latencies, active_rates, still_rates,
                            output_dir, test_name, auth_mode, show_active=show_active, show_still=show_still,
                            equidistant_x=equidistant_x)

    return csv_path


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
    parser.add_argument(
        "--show-active-rate", action="store_true",
        help="Include the average %% Active Rate on a secondary y-axis for Test 2 graphs."
    )
    parser.add_argument(
        "--show-still-rate", action="store_true",
        help="Include the average %% Still Rate (Bypass Rate) on a secondary y-axis for Test 2 graphs."
    )
    parser.add_argument(
        "--equidistant-x", "--equidistant", action="store_true", dest="equidistant_x",
        help="Plot Test 2 x-axis points at equidistant categorical intervals rather than continuous numerical positions on the number line."
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

        results, wc_results, active_results, still_results = load_test2_reports(reports_dir, thresholds, runs, bypass_mode=args.bypass_mode)
        primary_csv = generate_test2_plots_and_reports(
            thresholds, results, wc_results, active_results, still_results,
            output_dir, test_name=test_name, auth_mode=auth_mode,
            show_active=args.show_active_rate, show_still=args.show_still_rate,
            equidistant_x=args.equidistant_x
        )

        compare_csv = Path(args.compare_csv).resolve() if args.compare_csv else None
        if compare_csv:
            if not compare_csv.exists():
                print(f"⚠️  --compare-csv path not found: {compare_csv}. Skipping Test 2 comparative plots.")
            else:
                if auth_mode == "local":
                    local_csv, remote_csv = primary_csv, compare_csv
                else:
                    local_csv, remote_csv = compare_csv, primary_csv
                print(f"\n📊 Generating Test 2 comparative plots from CSVs...")
                print(f"   Local  CSV: {local_csv}")
                print(f"   Remote CSV: {remote_csv}")
                generate_test2_comparative_plots(
                    local_csv, remote_csv, output_dir, test_name=test_name,
                    show_active=args.show_active_rate, show_still=args.show_still_rate,
                    equidistant_x=args.equidistant_x
                )
    else:
        # ── Test 1: validity vs. monitor latency ──────────────────────────────
        compare_csv = Path(args.compare_csv).resolve() if args.compare_csv else None
        cmp_auth_mode = None
        if compare_csv and compare_csv.exists():
            _, cmp_inferred_mode, _ = infer_test_context(compare_csv.parent)
            cmp_auth_mode = cmp_inferred_mode or ("remote" if auth_mode == "local" else "local")

        validities, runs = discover_reports(reports_dir)
        print("=" * 80)
        print("AGGREGATING TEST 1 REPORTS & GENERATING GRAPH")
        print("=" * 80)
        print(f"Reports dir : {reports_dir}")
        print(f"Output dir  : {output_dir}")
        print(f"Validities  : {[int(v) for v in validities]} seconds")
        print(f"Runs        : {runs} per validity period")
        print(f"Test / Mode : {test_name} / {auth_mode}")
        if compare_csv:
            print(f"Compare CSV : {compare_csv} ({cmp_auth_mode or 'unknown mode'})")
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
