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
    import matplotlib.patheffects as path_effects
    MATPLOTLIB_AVAILABLE = True
    plt.rcParams.update({
        'font.size': 12,
        'axes.labelsize': 14,
        'axes.titlesize': 16,
        'xtick.labelsize': 13,
        'ytick.labelsize': 13,
        'legend.fontsize': 12,
    })
    ADJUST_TEXT_AVAILABLE = False
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    ADJUST_TEXT_AVAILABLE = False
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


def discover_compare_csv(reports_dir: Path, test_name: str,
                         auth_mode: str, bypass_mode: str = "still") -> Path | None:
    """Find the newest completed run for the opposite authentication mode."""
    parts = reports_dir.parts
    test_reports_root = None
    for index, part in enumerate(parts):
        if part == "test_reports":
            test_reports_root = Path(*parts[:index + 1])
            break

    if test_reports_root is None or auth_mode not in {"local", "remote"}:
        return None

    other_mode = "remote" if auth_mode == "local" else "local"
    if test_name == "test2":
        comparison_root = test_reports_root / "test2" / other_mode / bypass_mode
        csv_name = TEST2_CSV_NAME
    else:
        comparison_root = test_reports_root / "test1" / other_mode
        csv_name = TEST1_CSV_NAME

    if not comparison_root.is_dir():
        return None

    candidates = [path for path in comparison_root.glob(f"*/{csv_name}") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime).resolve()


def find_existing_summary_csv(reports_dir: Path, output_dir: Path,
                              csv_name: str) -> Path | None:
    """Return an existing summary CSV without rewriting or copying it."""
    candidates = [output_dir / csv_name]
    reports_csv = reports_dir / csv_name
    if reports_csv != candidates[0]:
        candidates.append(reports_csv)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def annotate_point(ax, text, xy, xytext=(0, 12), color='black', fontsize=11, ha='center'):
    ann = ax.annotate(
        text, xy=xy,
        textcoords="offset points", xytext=xytext,
        ha=ha, fontweight='bold', color=color, fontsize=fontsize
    )
    if MATPLOTLIB_AVAILABLE:
        ann.set_path_effects([path_effects.withStroke(linewidth=3, foreground='white')])
    return ann


def optimize_annotations(texts, ax=None):
    pass


LOG_X_ZERO_FLOOR = 0.001
LATENCY_Y_HEADROOM_FACTOR = 1.05
COMPARISON_LABEL_OVERLAP_FRACTION = 0.04


def get_comparison_label_offsets(first_value: float, second_value: float,
                                 all_values: list) -> tuple[int, int]:
    """Vertically stagger centered labels only when two series nearly overlap."""
    if not all_values:
        return 12, 12

    value_span = max(all_values) - min(all_values)
    overlap_threshold = value_span * COMPARISON_LABEL_OVERLAP_FRACTION
    if value_span > 0 and abs(first_value - second_value) > overlap_threshold:
        return 12, 12

    if first_value >= second_value:
        return 30, 12
    return 12, 30


def get_x_coordinates(values: list, equidistant_x: bool = False,
                      log_x: bool = False) -> list:
    """Return plot coordinates without changing the source values or labels."""
    if equidistant_x:
        return list(range(len(values)))
    if log_x:
        return [LOG_X_ZERO_FLOOR if float(value) == 0 else value for value in values]
    return values


def set_threshold_x_ticks(ax, x_coords: list, thresholds: list,
                          log_x: bool = False):
    """Render compact, readable threshold labels without changing coordinates."""
    labels = [f"{float(value):.10g}" for value in thresholds]
    ax.set_xticks(x_coords)
    ax.set_xticklabels(
        labels,
        fontsize=13,
        rotation=20 if log_x else 0,
        ha="right" if log_x else "center",
    )


def apply_log_scales(ax, x_values: list, y_values: list,
                     log_x: bool = False, log_y: bool = False):
    """Apply base-10 log scales, retaining zero/negative data with symlog."""
    for axis_name, enabled, values in (
        ("x", log_x, x_values),
        ("y", log_y, y_values),
    ):
        if not enabled:
            continue

        finite_values = [float(value) for value in values]
        scale_setter = ax.set_xscale if axis_name == "x" else ax.set_yscale
        if finite_values and all(value > 0 for value in finite_values):
            scale_setter("log", base=10)
            continue

        positive_values = [value for value in finite_values if value > 0]
        linthresh = min(positive_values) / 10 if positive_values else 1.0
        scale_setter("symlog", base=10, linthresh=linthresh)


def finalize_plot_layout(fig, aspect_1_1: bool = False):
    """Lay out the figure while reserving room for labels on square plots."""
    if aspect_1_1:
        fig.tight_layout(rect=(0, 0.08, 1, 1))
    else:
        fig.tight_layout()


def save_plot(fig, output_path: Path, aspect_1_1: bool = False):
    """Save plots with an exact square canvas when 1:1 output is requested."""
    if aspect_1_1:
        fig.savefig(output_path)
    else:
        fig.savefig(output_path, bbox_inches='tight', pad_inches=0.25)


def print_plot_options(test_name: str, output_dir: Path, options: dict,
                       show_active: bool = False, show_still: bool = False):
    x_scale = "equidistant" if options["equidistant_x"] else (
        "logarithmic" if options["log_x"] else "linear"
    )
    y_scale = "logarithmic" if options["log_y"] else "linear"
    print(f"📐 {test_name.upper()} graph configuration for this run:")
    print(f"   Canvas : {'1:1 square' if options['aspect_1_1'] else 'standard 11:6'}")
    print(f"   X-axis : {x_scale}")
    print(f"   Y-axis : {y_scale}")
    print(f"   Title  : {'hidden' if options['no_title'] else 'shown'}")
    if test_name == "test2":
        overlays = []
        if show_active:
            overlays.append("active rate")
        if show_still:
            overlays.append("still rate")
        print(f"   Rates  : {', '.join(overlays) if overlays else 'hidden'}")
    print(f"   Output : {output_dir}")


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
                      output_dir: Path, test_name: str, auth_mode: str,
                      equidistant_x: bool = False,
                      aspect_1_1: bool = False, no_title: bool = False,
                      log_x: bool = False, log_y: bool = False):
    """Render the single-mode graph (avg latency + worst-case on twin axes) from CSV data."""
    try:
        color_avg = '#1f77b4'
        color_wc  = '#d62728'

        figsize = (8, 8) if aspect_1_1 else (11, 6)
        fig, ax1 = plt.subplots(figsize=figsize, dpi=300)
        if aspect_1_1:
            ax1.set_box_aspect(1)

        x_coords = get_x_coordinates(validities, equidistant_x, log_x)
        x_labels = [f"{int(v)}s" for v in validities]

        ax1.plot(x_coords, avg_latencies,
                 marker='o', markersize=8, linewidth=2.5, color=color_avg)
        apply_log_scales(ax1, x_coords, avg_latencies, log_x=log_x, log_y=log_y)
        texts1 = []
        for i, val in enumerate(x_coords):
            texts1.append(annotate_point(ax1, f"{avg_latencies[i]:.2f} ms", (val, avg_latencies[i]),
                                         xytext=(0, 12), color=color_avg, fontsize=11))
        optimize_annotations(texts1, ax=ax1)

        ax1.set_xlabel('Relative Validity Period (seconds)', fontsize=14, labelpad=10)
        ax1.set_ylabel('Avg Monitor Latency (ms)', fontsize=14, color=color_avg, labelpad=10)
        ax1.tick_params(axis='y', labelcolor=color_avg)
        if not log_y:
            ax1.set_ylim(0, max(max(avg_latencies, default=0) * LATENCY_Y_HEADROOM_FACTOR, 20))
        ax1.set_xticks(x_coords)
        ax1.set_xticklabels(x_labels, fontsize=13)
        if equidistant_x:
            ax1.set_xlim(-0.4, len(x_coords) - 0.6)
        ax1.grid(True, linestyle='--', alpha=0.4)

        ax2 = ax1.twinx()
        ax2.plot(x_coords, wc_latencies,
                 marker='s', markersize=8, linewidth=2.5,
                 linestyle='--', color=color_wc)
        apply_log_scales(ax2, x_coords, wc_latencies, log_x=log_x, log_y=log_y)
        texts2 = []
        for i, val in enumerate(x_coords):
            texts2.append(annotate_point(ax2, f"{wc_latencies[i]:.2f} ms", (val, wc_latencies[i]),
                                         xytext=(0, 12), color=color_wc, fontsize=11))
        optimize_annotations(texts2, ax=ax2)

        ax2.set_ylabel('Worst-Case Monitor Latency (ms)', fontsize=14,
                       color=color_wc, labelpad=10)
        ax2.tick_params(axis='y', labelcolor=color_wc)
        if not log_y:
            ax2.set_ylim(0, max(max(wc_latencies, default=0) * LATENCY_Y_HEADROOM_FACTOR, 20))

        test_label = test_name.upper()
        mode_label = auth_mode.capitalize() + " Auth"
        plot_title = f"{test_label}: Monitor Latency vs. Session Key Relative Validity — {mode_label}"
        file_stem  = f"{test_name}_{auth_mode}_validity_vs_monitor_latency"

        if not no_title:
            fig.suptitle(plot_title, fontsize=16, fontweight='bold', y=1.01)
        finalize_plot_layout(fig, aspect_1_1)

        save_plot(fig, output_dir / f"{file_stem}.png", aspect_1_1)
        save_plot(fig, output_dir / f"{file_stem}.pdf", aspect_1_1)
        plt.close(fig)
        print(f"📈 Graph (PNG) saved to: {output_dir / file_stem}.png")
        print(f"📈 Graph (PDF) saved to: {output_dir / file_stem}.pdf")
    except Exception as e:
        print(f"❌ Error generating single-mode graph: {e}")


def generate_plots_and_reports(validities: list, results: dict, worst_case_results: dict,
                                output_dir: Path, test_name: str = "test1",
                                auth_mode: str = "local",
                                equidistant_x: bool = False,
                                aspect_1_1: bool = False,
                                no_title: bool = False,
                                log_x: bool = False,
                                log_y: bool = False) -> Path:
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
    _plot_single_mode(
        v, avg_lats, wc_lats, output_dir, test_name, auth_mode,
        equidistant_x=equidistant_x, aspect_1_1=aspect_1_1,
        no_title=no_title, log_x=log_x, log_y=log_y
    )

    return csv_path


def generate_comparative_plots(local_csv: Path, remote_csv: Path,
                               output_dir: Path, test_name: str = "test1",
                               equidistant_x: bool = False,
                               aspect_1_1: bool = False,
                               no_title: bool = False,
                               log_x: bool = False,
                               log_y: bool = False):
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

    x_coords = get_x_coordinates(common_v, equidistant_x, log_x)
    x_labels   = [f"{int(v)}s" for v in common_v]
    test_label = test_name.upper()
    figsize = (8, 8) if aspect_1_1 else (11, 6)

    # ── Plot 1: Average Latency (Local vs Remote) ────────────────────────────
    try:
        fig, ax = plt.subplots(figsize=figsize, dpi=300)
        if aspect_1_1:
            ax.set_box_aspect(1)

        color_local  = '#1f77b4'   # blue
        color_remote = '#d62728'   # red

        ax.plot(x_coords, l_avg, marker='o', markersize=8, linewidth=2.5,
                color=color_local, label='Local Auth')
        ax.plot(x_coords, r_avg, marker='s', markersize=8, linewidth=2.5,
                color=color_remote, label='Remote Auth', linestyle='--')
        apply_log_scales(ax, x_coords, l_avg + r_avg, log_x=log_x, log_y=log_y)

        texts = []
        for i, val in enumerate(x_coords):
            texts.append(annotate_point(ax, f"{l_avg[i]:.2f}", (val, l_avg[i]),
                                        xytext=(0, 12), color=color_local, fontsize=11))
            texts.append(annotate_point(ax, f"{r_avg[i]:.2f}", (val, r_avg[i]),
                                        xytext=(0, 12), color=color_remote, fontsize=11))
        optimize_annotations(texts, ax=ax)

        ax.set_xlabel('Relative Validity Period (seconds)', fontsize=14, labelpad=10)
        ax.set_ylabel('Average Monitor Latency (ms)', fontsize=14, labelpad=10)
        ax.set_xticks(x_coords)
        ax.set_xticklabels(x_labels, fontsize=13)
        if equidistant_x:
            ax.set_xlim(-0.4, len(x_coords) - 0.6)
        if not log_y:
            ax.set_ylim(0, max(max(l_avg + r_avg, default=0) * LATENCY_Y_HEADROOM_FACTOR, 20))
        ax.grid(True, linestyle='--', alpha=0.4)
        ax.legend(frameon=True, facecolor='white', framealpha=0.9, fontsize=12, loc='upper right')

        plot_title = f"{test_label}: Average Monitor Latency vs. Validity Period — Local vs. Remote Auth"
        if not no_title:
            fig.suptitle(plot_title, fontsize=16, fontweight='bold', y=1.01)
        finalize_plot_layout(fig, aspect_1_1)

        stem = f"{test_name}_avg_latency_local_vs_remote"
        save_plot(fig, output_dir / f"{stem}.png", aspect_1_1)
        save_plot(fig, output_dir / f"{stem}.pdf", aspect_1_1)
        plt.close(fig)
        print(f"📈 Comparative avg-latency graph saved to: {output_dir}/{stem}.png / .pdf")
    except Exception as e:
        print(f"❌ Error generating comparative avg-latency graph: {e}")

    # ── Plot 2: Worst-Case Latency (Local vs Remote) ─────────────────────────
    try:
        fig, ax = plt.subplots(figsize=figsize, dpi=300)
        if aspect_1_1:
            ax.set_box_aspect(1)

        color_local  = '#2ca02c'   # green
        color_remote = '#9467bd'   # purple

        ax.plot(x_coords, l_wc, marker='o', markersize=8, linewidth=2.5,
                color=color_local, label='Local Auth')
        ax.plot(x_coords, r_wc, marker='s', markersize=8, linewidth=2.5,
                color=color_remote, label='Remote Auth', linestyle='--')
        apply_log_scales(ax, x_coords, l_wc + r_wc, log_x=log_x, log_y=log_y)

        texts = []
        for i, val in enumerate(x_coords):
            texts.append(annotate_point(ax, f"{l_wc[i]:.2f}", (val, l_wc[i]),
                                        xytext=(0, 12), color=color_local, fontsize=11))
            texts.append(annotate_point(ax, f"{r_wc[i]:.2f}", (val, r_wc[i]),
                                        xytext=(0, 12), color=color_remote, fontsize=11))
        optimize_annotations(texts, ax=ax)

        ax.set_xlabel('Relative Validity Period (seconds)', fontsize=14, labelpad=10)
        ax.set_ylabel('Worst-Case Monitor Latency (ms)', fontsize=14, labelpad=10)
        ax.set_xticks(x_coords)
        ax.set_xticklabels(x_labels, fontsize=13)
        if equidistant_x:
            ax.set_xlim(-0.4, len(x_coords) - 0.6)
        if not log_y:
            ax.set_ylim(0, max(max(l_wc + r_wc, default=0) * LATENCY_Y_HEADROOM_FACTOR, 20))
        ax.grid(True, linestyle='--', alpha=0.4)
        ax.legend(frameon=True, facecolor='white', framealpha=0.9, fontsize=12, loc='upper right')

        plot_title = f"{test_label}: Worst-Case Monitor Latency vs. Validity Period — Local vs. Remote Auth"
        if not no_title:
            fig.suptitle(plot_title, fontsize=16, fontweight='bold', y=1.01)
        finalize_plot_layout(fig, aspect_1_1)

        stem = f"{test_name}_worstcase_latency_local_vs_remote"
        save_plot(fig, output_dir / f"{stem}.png", aspect_1_1)
        save_plot(fig, output_dir / f"{stem}.pdf", aspect_1_1)
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
    texts = []
    if show_active and active_rates:
        l1, = ax2.plot(x_coords, active_rates, marker='^', markersize=7, linewidth=2,
                       linestyle=':', color='#ff7f0e', label='% Active Rate')
        lines.append(l1)
        labels.append('% Active Rate')
        for i, x in enumerate(x_coords):
            texts.append(annotate_point(ax2, f"{active_rates[i]:.1f}%", (x, active_rates[i]),
                                        xytext=(0, 12), color='#ff7f0e', fontsize=10.5))
    if show_still and still_rates:
        l2, = ax2.plot(x_coords, still_rates, marker='v', markersize=7, linewidth=2,
                       linestyle=':', color='#2ca02c', label='% Still Rate (Bypass)')
        lines.append(l2)
        labels.append('% Still Rate (Bypass)')
        for i, x in enumerate(x_coords):
            texts.append(annotate_point(ax2, f"{still_rates[i]:.1f}%", (x, still_rates[i]),
                                        xytext=(0, 12), color='#2ca02c', fontsize=10.5))
    optimize_annotations(texts, ax=ax2)

    ax2.set_ylabel('Percentage Rate (%)', fontsize=14, color='#555555', labelpad=10)
    ax2.set_ylim(-5, 115)
    l_lines, l_labels = ax.get_legend_handles_labels()
    ax.legend(l_lines + lines, l_labels + labels, frameon=True, facecolor='white', framealpha=0.9, fontsize=12, loc='upper right')


def _plot_test2_single_mode(thresholds: list, avg_latencies: list, wc_latencies: list,
                            active_rates: list, still_rates: list,
                            output_dir: Path, test_name: str, auth_mode: str,
                            show_active: bool = False, show_still: bool = False,
                            equidistant_x: bool = False,
                            aspect_1_1: bool = False,
                            no_title: bool = False,
                            log_x: bool = False,
                            log_y: bool = False):
    """Render two separate single-mode graphs for Test 2: Average Latency & Worst-Case Latency."""
    if not MATPLOTLIB_AVAILABLE:
        print("\n⚠️  Warning: matplotlib is not available.")
        return

    test_label = test_name.upper()
    mode_label = auth_mode.capitalize() + " Auth"
    figsize = (8, 8) if aspect_1_1 else (11, 6)

    x_coords = get_x_coordinates(thresholds, equidistant_x, log_x)
    # ── Graph 1: Average Monitor Latency vs. Threshold ───────────────────────
    try:
        color_lat = '#1f77b4'
        fig, ax = plt.subplots(figsize=figsize, dpi=300)
        if aspect_1_1:
            ax.set_box_aspect(1)
        ax.plot(x_coords, avg_latencies, marker='o', markersize=8, linewidth=2.5,
                color=color_lat)
        apply_log_scales(ax, x_coords, avg_latencies, log_x=log_x, log_y=log_y)
        texts = []
        for i, x in enumerate(x_coords):
            texts.append(annotate_point(ax, f"{avg_latencies[i]:.2f} ms", (x, avg_latencies[i]),
                                        xytext=(0, 12), color=color_lat, fontsize=11))
        optimize_annotations(texts, ax=ax)

        ax.set_xlabel('Motion Threshold Value (τ)', fontsize=14, labelpad=10)
        ax.set_ylabel('Average Monitor Latency (ms)', fontsize=14, color=color_lat, labelpad=10)
        set_threshold_x_ticks(ax, x_coords, thresholds, log_x)
        if equidistant_x:
            ax.set_xlim(-0.4, len(x_coords) - 0.6)
        if not log_y:
            ax.set_ylim(0, max(max(avg_latencies, default=0) * LATENCY_Y_HEADROOM_FACTOR, 20))
        ax.grid(True, linestyle='--', alpha=0.4)
        _add_rate_twinx(ax, x_coords, active_rates, still_rates, show_active, show_still)

        plot_title = f"{test_label}: Average Monitor Latency vs. Motion Threshold — {mode_label}"
        if not no_title:
            fig.suptitle(plot_title, fontsize=16, fontweight='bold', y=1.01)
        finalize_plot_layout(fig, aspect_1_1)

        file_stem = f"{test_name}_{auth_mode}_threshold_vs_avg_latency"
        save_plot(fig, output_dir / f"{file_stem}.png", aspect_1_1)
        save_plot(fig, output_dir / f"{file_stem}.pdf", aspect_1_1)
        plt.close(fig)
        print(f"📈 Graph (PNG) saved to: {output_dir / file_stem}.png")
        print(f"📈 Graph (PDF) saved to: {output_dir / file_stem}.pdf")
    except Exception as e:
        print(f"❌ Error generating Test 2 avg-latency graph: {e}")

    # ── Graph 2: Worst-Case Monitor Latency vs. Threshold ────────────────────
    try:
        color_wc = '#d62728'
        fig, ax = plt.subplots(figsize=figsize, dpi=300)
        if aspect_1_1:
            ax.set_box_aspect(1)
        ax.plot(x_coords, wc_latencies, marker='s', markersize=8, linewidth=2.5,
                linestyle='--', color=color_wc)
        apply_log_scales(ax, x_coords, wc_latencies, log_x=log_x, log_y=log_y)
        texts = []
        for i, x in enumerate(x_coords):
            texts.append(annotate_point(ax, f"{wc_latencies[i]:.2f} ms", (x, wc_latencies[i]),
                                        xytext=(0, 12), color=color_wc, fontsize=11))
        optimize_annotations(texts, ax=ax)

        ax.set_xlabel('Motion Threshold Value (τ)', fontsize=14, labelpad=10)
        ax.set_ylabel('Worst-Case Monitor Latency (ms)', fontsize=14, color=color_wc, labelpad=10)
        set_threshold_x_ticks(ax, x_coords, thresholds, log_x)
        if equidistant_x:
            ax.set_xlim(-0.4, len(x_coords) - 0.6)
        if not log_y:
            ax.set_ylim(0, max(max(wc_latencies, default=0) * LATENCY_Y_HEADROOM_FACTOR, 20))
        ax.grid(True, linestyle='--', alpha=0.4)
        _add_rate_twinx(ax, x_coords, active_rates, still_rates, show_active, show_still)

        plot_title = f"{test_label}: Worst-Case Monitor Latency vs. Motion Threshold — {mode_label}"
        if not no_title:
            fig.suptitle(plot_title, fontsize=16, fontweight='bold', y=1.01)
        finalize_plot_layout(fig, aspect_1_1)

        file_stem = f"{test_name}_{auth_mode}_threshold_vs_worstcase_latency"
        save_plot(fig, output_dir / f"{file_stem}.png", aspect_1_1)
        save_plot(fig, output_dir / f"{file_stem}.pdf", aspect_1_1)
        plt.close(fig)
        print(f"📈 Graph (PNG) saved to: {output_dir / file_stem}.png")
        print(f"📈 Graph (PDF) saved to: {output_dir / file_stem}.pdf")
    except Exception as e:
        print(f"❌ Error generating Test 2 worst-case graph: {e}")


def generate_test2_comparative_plots(local_csv: Path, remote_csv: Path,
                                     output_dir: Path, test_name: str = "test2",
                                     show_active: bool = False, show_still: bool = False,
                                     equidistant_x: bool = False,
                                     aspect_1_1: bool = False,
                                     no_title: bool = False,
                                     log_x: bool = False,
                                     log_y: bool = False,
                                     primary_auth_mode: str = "local"):
    """Generate two separate comparative plots for Test 2 from threshold_vs_latency.csv files."""
    if not MATPLOTLIB_AVAILABLE:
        print("\n⚠️  Warning: matplotlib is not available — comparative plots skipped.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    local_t, local_avg, local_wc, l_act_all, l_st_all = read_test2_csv(local_csv)
    remote_t, remote_avg, remote_wc, r_act_all, r_st_all = read_test2_csv(remote_csv)

    local_rows = sorted(zip(local_t, local_avg, local_wc, l_act_all, l_st_all))
    remote_rows = sorted(zip(remote_t, remote_avg, remote_wc, r_act_all, r_st_all))
    if not local_rows or not remote_rows:
        print("⚠️  Local or remote comparison CSV contains no data rows.")
        return

    if len(local_rows) == len(remote_rows):
        local_t, l_avg, l_wc, l_act, l_st = map(list, zip(*local_rows))
        remote_t, r_avg, r_wc, r_act, r_st = map(list, zip(*remote_rows))
        use_remote_grid = primary_auth_mode == "remote"
        common_t = remote_t if use_remote_grid else local_t
        plot_act = r_act if use_remote_grid else l_act
        plot_st = r_st if use_remote_grid else l_st

        local_grid = [round(value, 4) for value in local_t]
        remote_grid = [round(value, 4) for value in remote_t]
        if local_grid != remote_grid:
            print("⚠️  Local and remote threshold values differ; aligning rows by "
                  f"position and using the {primary_auth_mode} CSV threshold grid.")
    else:
        local_map_avg = {round(t, 4): avg for t, avg in zip(local_t, local_avg)}
        local_map_wc = {round(t, 4): wc for t, wc in zip(local_t, local_wc)}
        local_map_act = {round(t, 4): act for t, act in zip(local_t, l_act_all)}
        local_map_st = {round(t, 4): st for t, st in zip(local_t, l_st_all)}
        remote_map_avg = {round(t, 4): avg for t, avg in zip(remote_t, remote_avg)}
        remote_map_wc = {round(t, 4): wc for t, wc in zip(remote_t, remote_wc)}
        remote_map_act = {round(t, 4): act for t, act in zip(remote_t, r_act_all)}
        remote_map_st = {round(t, 4): st for t, st in zip(remote_t, r_st_all)}

        common_t = sorted(set(local_map_avg) & set(remote_map_avg))
        if not common_t:
            print("⚠️  No common thresholds between local and remote CSVs.")
            return

        l_avg = [local_map_avg[t] for t in common_t]
        l_wc = [local_map_wc[t] for t in common_t]
        r_avg = [remote_map_avg[t] for t in common_t]
        r_wc = [remote_map_wc[t] for t in common_t]
        if primary_auth_mode == "remote":
            plot_act = [remote_map_act[t] for t in common_t]
            plot_st = [remote_map_st[t] for t in common_t]
        else:
            plot_act = [local_map_act[t] for t in common_t]
            plot_st = [local_map_st[t] for t in common_t]

    x_coords = get_x_coordinates(common_t, equidistant_x, log_x)
    test_label = test_name.upper()
    figsize    = (8, 8) if aspect_1_1 else (11, 6)

    # ── Plot 1: Average Latency (Local vs Remote) ────────────────────────────
    try:
        fig, ax = plt.subplots(figsize=figsize, dpi=300)
        if aspect_1_1:
            ax.set_box_aspect(1)
        ax.plot(x_coords, l_avg, marker='o', markersize=8, linewidth=2.5,
                color='#1f77b4', label='Local Auth')
        ax.plot(x_coords, r_avg, marker='s', markersize=8, linewidth=2.5,
                color='#d62728', label='Remote Auth', linestyle='--')
        apply_log_scales(ax, x_coords, l_avg + r_avg, log_x=log_x, log_y=log_y)

        texts = []
        for i, x in enumerate(x_coords):
            local_offset, remote_offset = get_comparison_label_offsets(
                l_avg[i], r_avg[i], l_avg + r_avg
            )
            texts.append(annotate_point(ax, f"{l_avg[i]:.2f}", (x, l_avg[i]),
                                        xytext=(0, local_offset), color='#1f77b4', fontsize=11))
            texts.append(annotate_point(ax, f"{r_avg[i]:.2f}", (x, r_avg[i]),
                                        xytext=(0, remote_offset), color='#d62728', fontsize=11))
        optimize_annotations(texts, ax=ax)

        ax.set_xlabel('Motion Threshold Value (τ)', fontsize=14, labelpad=10)
        ax.set_ylabel('Average Monitor Latency (ms)', fontsize=14, labelpad=10)
        set_threshold_x_ticks(ax, x_coords, common_t, log_x)
        if equidistant_x:
            ax.set_xlim(-0.4, len(x_coords) - 0.6)
        if not log_y:
            ax.set_ylim(0, max(max(l_avg + r_avg, default=0) * LATENCY_Y_HEADROOM_FACTOR, 20))
        ax.grid(True, linestyle='--', alpha=0.4)
        ax.legend(frameon=True, facecolor='white', framealpha=0.9, fontsize=12, loc='upper right')

        _add_rate_twinx(ax, x_coords, plot_act, plot_st, show_active, show_still)

        plot_title = f"{test_label}: Average Monitor Latency vs. Threshold — Local vs. Remote Auth"
        if not no_title:
            fig.suptitle(plot_title, fontsize=16, fontweight='bold', y=1.01)
        finalize_plot_layout(fig, aspect_1_1)

        stem = f"{test_name}_avg_latency_local_vs_remote"
        save_plot(fig, output_dir / f"{stem}.png", aspect_1_1)
        save_plot(fig, output_dir / f"{stem}.pdf", aspect_1_1)
        plt.close(fig)
        print(f"📈 Comparative avg-latency graph saved to: {output_dir}/{stem}.png / .pdf")
    except Exception as e:
        print(f"❌ Error generating Test 2 comparative avg-latency graph: {e}")

    # ── Plot 2: Worst-Case Latency (Local vs Remote) ─────────────────────────
    try:
        fig, ax = plt.subplots(figsize=figsize, dpi=300)
        if aspect_1_1:
            ax.set_box_aspect(1)
        ax.plot(x_coords, l_wc, marker='o', markersize=8, linewidth=2.5,
                color='#2ca02c', label='Local Auth')
        ax.plot(x_coords, r_wc, marker='s', markersize=8, linewidth=2.5,
                color='#9467bd', label='Remote Auth', linestyle='--')
        apply_log_scales(ax, x_coords, l_wc + r_wc, log_x=log_x, log_y=log_y)

        texts = []
        for i, x in enumerate(x_coords):
            local_offset, remote_offset = get_comparison_label_offsets(
                l_wc[i], r_wc[i], l_wc + r_wc
            )
            texts.append(annotate_point(ax, f"{l_wc[i]:.2f}", (x, l_wc[i]),
                                        xytext=(0, local_offset), color='#2ca02c', fontsize=11))
            texts.append(annotate_point(ax, f"{r_wc[i]:.2f}", (x, r_wc[i]),
                                        xytext=(0, remote_offset), color='#9467bd', fontsize=11))
        optimize_annotations(texts, ax=ax)

        ax.set_xlabel('Motion Threshold Value (τ)', fontsize=14, labelpad=10)
        ax.set_ylabel('Worst-Case Monitor Latency (ms)', fontsize=14, labelpad=10)
        set_threshold_x_ticks(ax, x_coords, common_t, log_x)
        if equidistant_x:
            ax.set_xlim(-0.4, len(x_coords) - 0.6)
        if not log_y:
            ax.set_ylim(0, max(max(l_wc + r_wc, default=0) * LATENCY_Y_HEADROOM_FACTOR, 20))
        ax.grid(True, linestyle='--', alpha=0.4)
        ax.legend(frameon=True, facecolor='white', framealpha=0.9, fontsize=12, loc='upper right')

        _add_rate_twinx(ax, x_coords, plot_act, plot_st, show_active, show_still)

        plot_title = f"{test_label}: Worst-Case Monitor Latency vs. Threshold — Local vs. Remote Auth"
        if not no_title:
            fig.suptitle(plot_title, fontsize=16, fontweight='bold', y=1.01)
        finalize_plot_layout(fig, aspect_1_1)

        stem = f"{test_name}_worstcase_latency_local_vs_remote"
        save_plot(fig, output_dir / f"{stem}.png", aspect_1_1)
        save_plot(fig, output_dir / f"{stem}.pdf", aspect_1_1)
        plt.close(fig)
        print(f"📈 Comparative worst-case graph saved to: {output_dir}/{stem}.png / .pdf")
    except Exception as e:
        print(f"❌ Error generating Test 2 comparative worst-case graph: {e}")


def generate_test2_plots_and_reports(thresholds: list, results: dict, worst_case_results: dict,
                                     active_results: dict, still_results: dict,
                                     output_dir: Path, test_name: str = "test2",
                                     auth_mode: str = "local",
                                     show_active: bool = False, show_still: bool = False,
                                     equidistant_x: bool = False,
                                     aspect_1_1: bool = False,
                                     no_title: bool = False,
                                     log_x: bool = False,
                                     log_y: bool = False) -> Path:
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
                            equidistant_x=equidistant_x, aspect_1_1=aspect_1_1,
                            no_title=no_title, log_x=log_x, log_y=log_y)

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
        help="Path to the other auth mode's summary CSV. "
             "When provided, two separate comparative plots are generated (avg latency "
             "and worst-case latency, each with local and remote on the same graph). "
             "When omitted, the newest completed opposite-auth run is auto-discovered "
             "from the test_reports hierarchy."
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
        "--equidistant-x", action="store_true", dest="equidistant_x",
        help="Plot Test 2 x-axis points at equidistant categorical intervals rather than continuous numerical positions on the number line."
    )
    parser.add_argument(
        "--log-x", action="store_true", dest="log_x",
        help="Use base-10 logarithmic spacing on the Test 2 threshold x-axis. "
             "Test 1 always uses a linear validity-period x-axis. Zero Test 2 "
             "values are plotted at 0.001 while retaining their original labels."
    )
    parser.add_argument(
        "--log-y", action="store_true", dest="log_y",
        help="Use base-10 logarithmic spacing on the latency y-axis. Uses symmetric-log around zero when needed."
    )
    parser.add_argument(
        "--aspect-1-1", "--square", "--aspect-ratio-1-1", action="store_true", dest="aspect_1_1",
        help="Render graphs with a 1:1 aspect ratio (square figure and axes box)."
    )
    parser.add_argument(
        "--no-title", action="store_true", dest="no_title",
        help="Omit graph titles (suptitle) from rendered plots."
    )
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir).resolve()
    output_dir  = Path(args.output_dir).resolve() if args.output_dir else reports_dir

    if not reports_dir.exists():
        print(f"❌ Error: reports directory does not exist: {reports_dir}")
        sys.exit(1)
    output_dir.mkdir(parents=True, exist_ok=True)

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
    is_test2 = (
        test_name.lower() == "test2"
        or bool(list(reports_dir.glob("thresh_*_run_*.txt")))
        or (reports_dir / TEST2_CSV_NAME).is_file()
        or (output_dir / TEST2_CSV_NAME).is_file()
    )
    if is_test2:
        test_name = "test2"

    if is_test2 and args.equidistant_x and args.log_x:
        parser.error("--equidistant-x and --log-x cannot be used together")
    if not is_test2 and args.log_x:
        print("ℹ️  Ignoring --log-x for Test 1; its validity-period x-axis is always linear.")

    plot_options = {
        "equidistant_x": args.equidistant_x,
        "aspect_1_1": args.aspect_1_1,
        "no_title": args.no_title,
        "log_x": args.log_x if is_test2 else False,
        "log_y": args.log_y,
    }
    print_plot_options(
        test_name, output_dir, plot_options,
        show_active=args.show_active_rate, show_still=args.show_still_rate
    )

    compare_csv = Path(args.compare_csv).resolve() if args.compare_csv else discover_compare_csv(
        reports_dir, test_name, auth_mode, args.bypass_mode
    )
    if compare_csv and not args.compare_csv:
        print(f"🔍 Auto-discovered comparison CSV: {compare_csv}")
    elif not compare_csv and not args.compare_csv:
        other_mode = "remote" if auth_mode == "local" else "local"
        print(f"ℹ️  No completed {other_mode} comparison run was found; "
              "only single-mode graphs will be generated.")

    if is_test2:
        primary_csv = find_existing_summary_csv(reports_dir, output_dir, TEST2_CSV_NAME)
        if primary_csv:
            print(f"♻️  Existing Test 2 CSV found: {primary_csv}")
            print("   Skipping report aggregation and CSV generation.")
            thresholds, avg_latencies, wc_latencies, active_rates, still_rates = read_test2_csv(primary_csv)
            _plot_test2_single_mode(
                thresholds, avg_latencies, wc_latencies, active_rates, still_rates,
                output_dir, test_name, auth_mode,
                show_active=args.show_active_rate, show_still=args.show_still_rate,
                **plot_options
            )
        else:
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

            results, wc_results, active_results, still_results = load_test2_reports(
                reports_dir, thresholds, runs, bypass_mode=args.bypass_mode
            )
            primary_csv = generate_test2_plots_and_reports(
                thresholds, results, wc_results, active_results, still_results,
                output_dir, test_name=test_name, auth_mode=auth_mode,
                show_active=args.show_active_rate, show_still=args.show_still_rate,
                **plot_options
            )

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
                    primary_auth_mode=auth_mode, **plot_options
                )
    else:
        # ── Test 1: validity vs. monitor latency ──────────────────────────────
        cmp_auth_mode = None
        if compare_csv and compare_csv.exists():
            _, cmp_inferred_mode, _ = infer_test_context(compare_csv.parent)
            cmp_auth_mode = cmp_inferred_mode or ("remote" if auth_mode == "local" else "local")

        primary_csv = find_existing_summary_csv(reports_dir, output_dir, TEST1_CSV_NAME)
        if primary_csv:
            print(f"♻️  Existing Test 1 CSV found: {primary_csv}")
            print("   Skipping report aggregation and CSV generation.")
            validities, avg_latencies, wc_latencies = read_test1_csv(primary_csv)
            if MATPLOTLIB_AVAILABLE:
                _plot_single_mode(
                    validities, avg_latencies, wc_latencies,
                    output_dir, test_name, auth_mode, **plot_options
                )
            else:
                print("\n⚠️  Warning: matplotlib is not available — graphs skipped.")
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
            if compare_csv:
                print(f"Compare CSV : {compare_csv} ({cmp_auth_mode or 'unknown mode'})")
            print("=" * 80)

            results, worst_case_results = load_reports(reports_dir, validities, runs)
            primary_csv = generate_plots_and_reports(
                validities, results, worst_case_results,
                output_dir, test_name=test_name, auth_mode=auth_mode,
                **plot_options
            )

        # Step 3: if a compare CSV is provided, also generate comparative plots
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
                    **plot_options
                )
    print("\n✅ Done!\n")


if __name__ == "__main__":
    main()
