#!/usr/bin/env python3
"""
plot_results.py — Standalone graph generator for openpi latency test reports
=============================================================================
Reads pre-generated per-run report files from a timestamped test_reports/ directory:
  - Test 1 (val_Xs_run_Y.txt): Validity vs. Average & Worst-Case Monitor Latency
  - Test 2 (thresh_X_run_Y.txt): Motion Threshold vs. Bypass Rate (%) & Latency
  - Test 3 (val_Xs_thresh_Y_run_Z.txt): Validity/Threshold Monitor Latency Heatmap

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

# Central sizing for figures intended to be reduced from 6 inches to one
# approximately 3.3-inch ACM column.  An 18 pt source font renders at about
# 10 pt after that reduction.
STANDARD_FIGURE_SIZE = (6, 6)
SQUARE_FIGURE_SIZE = (8, 8)
HEATMAP_FIGURE_SIZE = (10.5, 8.0)
FIGURE_DPI = 300
HEATMAP_AXIS_LABEL_FONT_SIZE = 32
HEATMAP_TICK_LABEL_FONT_SIZE = 32
HEATMAP_DATA_LABEL_FONT_SIZE = 26
HEATMAP_TITLE_FONT_SIZE = 30
HEATMAP_COLORBAR_LABEL_FONT_SIZE = 32
HEATMAP_COLORBAR_TICK_FONT_SIZE = 30
BASE_FONT_SIZE = 20
AXIS_LABEL_FONT_SIZE = 26
AXIS_TITLE_FONT_SIZE = 24
X_TICK_LABEL_FONT_SIZE = 26
Y_TICK_LABEL_FONT_SIZE = 26
LEGEND_FONT_SIZE = 26
FIGURE_TITLE_FONT_SIZE = 24
DATA_LABEL_FONT_SIZE = 20
X_TICK_LABEL_ROTATION = 50
DATA_LABEL_BOUNDARY_PADDING_POINTS = 4
DATA_LABEL_MIN_GAP_POINTS = 4
LAYOUT_PADDING = 0.25
EXPORT_PADDING_INCHES = 0.03
# ==============================================================================
# Y-AXIS DISPLAY NAME MACRO
# Change this constant at source to customize the base name for latency y-axis labels across all graphs.
# Examples: "Monitor-to-Actuator Latency", "Monitor-Actuator Latency", "Monitor Latency"
# ==============================================================================
LATENCY_DISPLAY_NAME = "Monitor-Actuator Avg. Latency"


def extract_latency_summary(content: str):
    """Prefer the end-to-end secure architecture metric, with legacy monitor fallback."""
    avg_match = re.search(
        r"Average Monitor-Actuator Latency:\s*([\d.]+)\s*ms",
        content,
        re.IGNORECASE,
    )
    wc_match = re.search(
        r"Worst-Case Monitor-Actuator Lat\.:\s*([\d.]+)\s*ms",
        content,
        re.IGNORECASE,
    )
    if avg_match is None:
        avg_match = re.search(r"Average Monitor Latency:\s*([\d.]+)\s*ms", content, re.IGNORECASE)
    if wc_match is None:
        wc_match = re.search(r"Worst-Case Monitor Lat\.:\s*([\d.]+)\s*ms", content, re.IGNORECASE)
    return avg_match, wc_match

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
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as path_effects
    MATPLOTLIB_AVAILABLE = True
    plt.rcParams.update({
        'font.size': BASE_FONT_SIZE,
        'axes.labelsize': AXIS_LABEL_FONT_SIZE,
        'axes.titlesize': AXIS_TITLE_FONT_SIZE,
        'xtick.labelsize': X_TICK_LABEL_FONT_SIZE,
        'ytick.labelsize': Y_TICK_LABEL_FONT_SIZE,
        'legend.fontsize': LEGEND_FONT_SIZE,
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
        # Compare executable paths without resolving symlinks. A virtualenv's
        # Python often resolves to the same base interpreter as system Python,
        # but it still has a different site-packages environment.
        if venv_py.exists() and str(venv_py.absolute()) != os.path.abspath(sys.executable):
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
            if i + 3 < len(parts) and parts[i + 3] in ["insiga", "siga", "still", "active"]:
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


def annotate_point(ax, text, xy, xytext=(0, 12), color='black',
                   fontsize=DATA_LABEL_FONT_SIZE, ha='center'):
    ann = ax.annotate(
        text, xy=xy,
        textcoords="offset points", xytext=xytext,
        ha=ha, fontweight='bold', color=color, fontsize=fontsize
    )
    if MATPLOTLIB_AVAILABLE:
        ann.set_path_effects([path_effects.withStroke(linewidth=3, foreground='white')])
    ann.set_gid("data-label")
    return ann


def optimize_annotations(texts, ax=None):
    pass


def separate_close_data_labels(fig):
    """Raise the higher-value label when rendered text boxes are too close."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    gap_px = DATA_LABEL_MIN_GAP_POINTS * fig.dpi / 72
    pixels_to_points = 72 / fig.dpi
    labels = [
        label
        for ax in fig.axes
        for label in ax.texts
        if label.get_gid() == "data-label"
    ]
    # Place lower plotted points first so a close higher-value label is the one
    # shifted upward. This naturally raises the earlier label on decreasing data.
    labels.sort(
        key=lambda label: label.axes.transData.transform(label.xy)[1]
    )
    placed_boxes = []
    adjusted = False

    for label in labels:
        label_box = label.get_window_extent(renderer)
        total_dy_px = 0

        while True:
            close_boxes = [
                other_box
                for other_box in placed_boxes
                if (
                    label_box.x0 < other_box.x1 + gap_px
                    and label_box.x1 > other_box.x0 - gap_px
                    and label_box.y0 < other_box.y1 + gap_px
                    and label_box.y1 > other_box.y0 - gap_px
                )
            ]
            if not close_boxes:
                break

            dy_px = max(other_box.y1 + gap_px - label_box.y0
                        for other_box in close_boxes)
            if dy_px <= 0:
                break
            label_box = label_box.translated(0, dy_px)
            total_dy_px += dy_px

        if total_dy_px:
            offset_x, offset_y = label.get_position()
            label.set_position((
                offset_x,
                offset_y + total_dy_px * pixels_to_points,
            ))
            adjusted = True
        placed_boxes.append(label_box)

    if adjusted:
        fig.canvas.draw()


def keep_data_labels_inside_axes(fig):
    """Shift data labels inward when their rendered bounds cross an axes box."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    padding_px = DATA_LABEL_BOUNDARY_PADDING_POINTS * fig.dpi / 72
    adjusted = False

    for ax in fig.axes:
        axes_box = ax.get_window_extent(renderer)
        left = axes_box.x0 + padding_px
        right = axes_box.x1 - padding_px
        bottom = axes_box.y0 + padding_px
        top = axes_box.y1 - padding_px

        for label in ax.texts:
            if label.get_gid() != "data-label":
                continue

            label_box = label.get_window_extent(renderer)
            dx_px = 0
            dy_px = 0
            if label_box.x0 < left:
                dx_px = left - label_box.x0
            elif label_box.x1 > right:
                dx_px = right - label_box.x1
            if label_box.y0 < bottom:
                dy_px = bottom - label_box.y0
            elif label_box.y1 > top:
                dy_px = top - label_box.y1

            if dx_px or dy_px:
                offset_x, offset_y = label.get_position()
                pixels_to_points = 72 / fig.dpi
                label.set_position((
                    offset_x + dx_px * pixels_to_points,
                    offset_y + dy_px * pixels_to_points,
                ))
                adjusted = True

    if adjusted:
        fig.canvas.draw()


LEGEND_LOCATION = "upper right"
LEGEND_CLEARANCE_GROWTH = 1.15
LEGEND_CLEARANCE_MAX_PASSES = 8


def _bbox_overlap_area(first, second) -> float:
    """Return the overlap area of two display-coordinate bounding boxes."""
    width = max(0.0, min(first.x1, second.x1) - max(first.x0, second.x0))
    height = max(0.0, min(first.y1, second.y1) - max(first.y0, second.y0))
    return width * height


def _legend_collision_score(fig, legend, owner_ax) -> float:
    """Score overlap with plotted lines, markers, and rendered data labels."""
    renderer = fig.canvas.get_renderer()
    # Treat a small halo around the legend as occupied so it does not merely
    # touch a line or label.
    legend_box = legend.get_window_extent(renderer).expanded(1.06, 1.10)
    score = 0.0

    owner_box = owner_ax.get_position()
    for ax in fig.axes:
        # Include twin axes, but ignore unrelated subplots.
        ax_box = ax.get_position()
        if _bbox_overlap_area(owner_box, ax_box) <= 0:
            continue

        for label in ax.texts:
            if label.get_gid() != "data-label" or not label.get_visible():
                continue
            overlap = _bbox_overlap_area(legend_box, label.get_window_extent(renderer))
            if overlap:
                # Covering a value label is the most expensive placement.
                score += 1_000_000 + overlap

        for line in ax.lines:
            if not line.get_visible() or len(line.get_xdata()) == 0:
                continue
            display_path = line.get_path().transformed(line.get_transform())
            if display_path.intersects_bbox(legend_box, filled=False):
                score += 10_000

            # Explicitly count marker centers inside the legend.  This also
            # catches short/discontinuous paths that intersection tests miss.
            vertices = display_path.vertices
            if len(vertices):
                inside = (
                    (vertices[:, 0] >= legend_box.x0)
                    & (vertices[:, 0] <= legend_box.x1)
                    & (vertices[:, 1] >= legend_box.y0)
                    & (vertices[:, 1] <= legend_box.y1)
                )
                score += 25_000 * int(inside.sum())

    return score


def add_collision_aware_legend(ax, handles=None, labels=None):
    """Add an upper-right legend whose clearance is resolved during layout."""
    kwargs = {
        "frameon": True,
        "facecolor": "white",
        "framealpha": 0.9,
        "fontsize": LEGEND_FONT_SIZE,
    }
    ax._collision_legend_spec = (handles, labels, kwargs)
    if handles is None:
        return ax.legend(loc=LEGEND_LOCATION, **kwargs)
    return ax.legend(handles, labels, loc=LEGEND_LOCATION, **kwargs)


def _expand_shared_y_limits(fig, owner_ax):
    """Add headroom on an axes and any twin axes sharing its plot rectangle."""
    owner_box = owner_ax.get_position()
    for ax in fig.axes:
        if _bbox_overlap_area(owner_box, ax.get_position()) <= 0:
            continue

        lower, upper = ax.get_ylim()
        if upper <= lower:
            continue
        if ax.get_yscale() == "log":
            ax.set_ylim(lower, upper * LEGEND_CLEARANCE_GROWTH)
        else:
            span = upper - lower
            ax.set_ylim(lower, lower + span * LEGEND_CLEARANCE_GROWTH)


def position_collision_aware_legends(fig):
    """Keep legends upper-right and create headroom until content clears them."""
    for ax in fig.axes:
        spec = getattr(ax, "_collision_legend_spec", None)
        if spec is None:
            continue

        handles, labels, kwargs = spec
        existing = ax.get_legend()
        if existing is not None:
            existing.remove()

        if handles is None:
            legend = ax.legend(loc=LEGEND_LOCATION, **kwargs)
        else:
            legend = ax.legend(handles, labels, loc=LEGEND_LOCATION, **kwargs)

        for _ in range(LEGEND_CLEARANCE_MAX_PASSES):
            fig.canvas.draw()
            if _legend_collision_score(fig, legend, ax) == 0:
                break
            _expand_shared_y_limits(fig, ax)
            keep_data_labels_inside_axes(fig)
            separate_close_data_labels(fig)
            keep_data_labels_inside_axes(fig)
    fig.canvas.draw()


LOG_X_ZERO_FLOOR = 0.001
LATENCY_Y_HEADROOM_FACTOR = 1.12
COMPARISON_LABEL_OVERLAP_FRACTION = 0.04


def set_latency_y_limits(ax, values: list):
    """Use the data range instead of forcing an often mostly-empty 20 ms axis."""
    maximum = max((float(value) for value in values), default=0.0)
    ax.set_ylim(0, maximum * LATENCY_Y_HEADROOM_FACTOR if maximum > 0 else 1)


def set_equidistant_x_limits(ax, point_count: int):
    """Keep end points close to the axes while labels remain inside the box."""
    if point_count:
        ax.set_xlim(-EQUIDISTANT_X_MARGIN, point_count - 1 + EQUIDISTANT_X_MARGIN)


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
        fontsize=X_TICK_LABEL_FONT_SIZE,
        rotation=X_TICK_LABEL_ROTATION,
        ha="right" if X_TICK_LABEL_ROTATION > 0 else (
            "left" if X_TICK_LABEL_ROTATION < 0 else "center"
        ),
        rotation_mode="anchor",
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
    """Tightly lay out labels, then place legends away from plotted content."""
    if aspect_1_1:
        fig.tight_layout(rect=(0.02, 0.02, 0.98, 0.98), pad=1.2)
        fig.subplots_adjust(left=0.18, bottom=0.15, right=0.95)
    else:
        fig.tight_layout(pad=LAYOUT_PADDING)
    keep_data_labels_inside_axes(fig)
    separate_close_data_labels(fig)
    keep_data_labels_inside_axes(fig)
    position_collision_aware_legends(fig)


def save_plot(fig, output_path: Path, aspect_1_1: bool = False):
    """Save plots with an exact square canvas when 1:1 output is requested."""
    if aspect_1_1:
        fig.savefig(output_path)
    else:
        fig.savefig(output_path, bbox_inches='tight', pad_inches=EXPORT_PADDING_INCHES)


def print_plot_options(test_name: str, output_dir: Path, options: dict,
                       show_active: bool = False, show_still: bool = False):
    if test_name == "test3":
        print(f"📐 {test_name.upper()} heatmap configuration for this run:")
        print("   X-axis : categorical validity periods")
        print("   Y-axis : categorical motion thresholds")
        print(f"   Color  : average {LATENCY_DISPLAY_NAME.lower()} (ms)")
        print(f"   Title  : {'hidden' if options['no_title'] else 'shown'}")
        print(f"   Output : {output_dir}")
        return

    x_scale = "equidistant" if options["equidistant_x"] else (
        "logarithmic" if options["log_x"] else "linear"
    )
    y_scale = "logarithmic" if options["log_y"] else "linear"
    print(f"📐 {test_name.upper()} graph configuration for this run:")
    print(f"   Canvas : {'1:1 square (6:6)' if options['aspect_1_1'] else 'standard 6:4'}")
    print(f"   X-axis : {x_scale}")
    print(f"   Y-axis : {y_scale}")
    print(f"   Title  : {'hidden' if options['no_title'] else 'shown'}")
    if test_name == "test2":
        overlays = []
        if show_active:
            overlays.append("SIGA rate")
        if show_still:
            overlays.append("INSIGA rate")
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
                avg_match, wc_match = extract_latency_summary(content)
                if avg_match:
                    lat = float(avg_match.group(1))
                    print(f"✅ Loaded {found_file.name} -> avg: {lat:.2f} ms", end="")
                else:
                    print(f"⚠️  Warning: no supported average latency metric found in {found_file.name}")
                if wc_match:
                    wc_lat = float(wc_match.group(1))
                    print(f", worst-case: {wc_lat:.2f} ms")
                else:
                    wc_lat = lat
                    print(" (worst-case not found, using avg)")
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

        figsize = SQUARE_FIGURE_SIZE if aspect_1_1 else STANDARD_FIGURE_SIZE
        fig, ax1 = plt.subplots(figsize=figsize, dpi=FIGURE_DPI)
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
                                         xytext=(0, 12), color=color_avg))
        optimize_annotations(texts1, ax=ax1)

        ax1.set_xlabel('Relative Validity Period (seconds)', fontsize=AXIS_LABEL_FONT_SIZE, labelpad=10)
        ax1.set_ylabel(f'Avg {LATENCY_DISPLAY_NAME} (ms)', fontsize=AXIS_LABEL_FONT_SIZE, color=color_avg, labelpad=10)
        ax1.tick_params(axis='y', labelcolor=color_avg)
        if not log_y:
            set_latency_y_limits(ax1, avg_latencies)
        ax1.set_xticks(x_coords)
        ax1.set_xticklabels(
            x_labels,
            fontsize=X_TICK_LABEL_FONT_SIZE,
            rotation=X_TICK_LABEL_ROTATION,
            ha="right" if X_TICK_LABEL_ROTATION > 0 else (
                "left" if X_TICK_LABEL_ROTATION < 0 else "center"
            ),
            rotation_mode="anchor",
        )
        if equidistant_x:
            set_equidistant_x_limits(ax1, len(x_coords))
        ax1.grid(True, linestyle='--', alpha=0.4)

        ax2 = ax1.twinx()
        ax2.plot(x_coords, wc_latencies,
                 marker='s', markersize=8, linewidth=2.5,
                 linestyle='--', color=color_wc)
        apply_log_scales(ax2, x_coords, wc_latencies, log_x=log_x, log_y=log_y)
        texts2 = []
        for i, val in enumerate(x_coords):
            texts2.append(annotate_point(ax2, f"{wc_latencies[i]:.2f} ms", (val, wc_latencies[i]),
                                         xytext=(0, 12), color=color_wc))
        optimize_annotations(texts2, ax=ax2)

        ax2.set_ylabel(f'Worst-Case {LATENCY_DISPLAY_NAME} (ms)', fontsize=AXIS_LABEL_FONT_SIZE,
                       color=color_wc, labelpad=10)
        ax2.tick_params(axis='y', labelcolor=color_wc)
        if not log_y:
            set_latency_y_limits(ax2, wc_latencies)

        test_label = test_name.upper()
        mode_label = auth_mode.capitalize() + " Auth"
        plot_title = f"{test_label}: {LATENCY_DISPLAY_NAME} vs. Session Key Relative Validity — {mode_label}"
        file_stem  = f"{test_name}_{auth_mode}_validity_vs_monitor_latency"

        if not no_title:
            fig.suptitle(plot_title, fontsize=FIGURE_TITLE_FONT_SIZE, fontweight='bold', y=1.01)
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
    figsize = SQUARE_FIGURE_SIZE if aspect_1_1 else STANDARD_FIGURE_SIZE

    # ── Plot 1: Average Latency (Local vs Remote) ────────────────────────────
    try:
        fig, ax = plt.subplots(figsize=figsize, dpi=FIGURE_DPI)
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
                                        xytext=(0, 12), color=color_local))
            texts.append(annotate_point(ax, f"{r_avg[i]:.2f}", (val, r_avg[i]),
                                        xytext=(0, 12), color=color_remote))
        optimize_annotations(texts, ax=ax)

        ax.set_xlabel('Relative Validity Period (seconds)', fontsize=AXIS_LABEL_FONT_SIZE, labelpad=10)
        ax.set_ylabel(f'Average {LATENCY_DISPLAY_NAME} (ms)', fontsize=AXIS_LABEL_FONT_SIZE, labelpad=10)
        ax.set_xticks(x_coords)
        ax.set_xticklabels(
            x_labels,
            fontsize=X_TICK_LABEL_FONT_SIZE,
            rotation=X_TICK_LABEL_ROTATION,
            ha="right" if X_TICK_LABEL_ROTATION > 0 else (
                "left" if X_TICK_LABEL_ROTATION < 0 else "center"
            ),
            rotation_mode="anchor",
        )
        if equidistant_x:
            set_equidistant_x_limits(ax, len(x_coords))
        if not log_y:
            set_latency_y_limits(ax, l_avg + r_avg)
        ax.grid(True, linestyle='--', alpha=0.4)
        add_collision_aware_legend(ax)

        plot_title = f"{test_label}: Average {LATENCY_DISPLAY_NAME} vs. Validity Period — Local vs. Remote Auth"
        if not no_title:
            fig.suptitle(plot_title, fontsize=FIGURE_TITLE_FONT_SIZE, fontweight='bold', y=1.01)
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
        fig, ax = plt.subplots(figsize=figsize, dpi=FIGURE_DPI)
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
                                        xytext=(0, 12), color=color_local))
            texts.append(annotate_point(ax, f"{r_wc[i]:.2f}", (val, r_wc[i]),
                                        xytext=(0, 12), color=color_remote))
        optimize_annotations(texts, ax=ax)

        ax.set_xlabel('Relative Validity Period (seconds)', fontsize=AXIS_LABEL_FONT_SIZE, labelpad=10)
        ax.set_ylabel(f'Worst-Case {LATENCY_DISPLAY_NAME} (ms)', fontsize=AXIS_LABEL_FONT_SIZE, labelpad=10)
        ax.set_xticks(x_coords)
        ax.set_xticklabels(
            x_labels,
            fontsize=X_TICK_LABEL_FONT_SIZE,
            rotation=X_TICK_LABEL_ROTATION,
            ha="right" if X_TICK_LABEL_ROTATION > 0 else (
                "left" if X_TICK_LABEL_ROTATION < 0 else "center"
            ),
            rotation_mode="anchor",
        )
        if equidistant_x:
            set_equidistant_x_limits(ax, len(x_coords))
        if not log_y:
            set_latency_y_limits(ax, l_wc + r_wc)
        ax.grid(True, linestyle='--', alpha=0.4)
        add_collision_aware_legend(ax)

        plot_title = f"{test_label}: Worst-Case {LATENCY_DISPLAY_NAME} vs. Validity Period — Local vs. Remote Auth"
        if not no_title:
            fig.suptitle(plot_title, fontsize=FIGURE_TITLE_FONT_SIZE, fontweight='bold', y=1.01)
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


def load_test2_reports(reports_dir: Path, thresholds: list, runs: int) -> tuple[dict, dict, dict, dict]:
    results = {t: [] for t in thresholds}
    wc_results = {t: [] for t in thresholds}
    active_results = {t: [] for t in thresholds}
    still_results = {t: [] for t in thresholds}

    for t in thresholds:
        for r in range(1, runs + 1):
            found_file = None
            for cand in sorted(reports_dir.glob(f"thresh_*_run_{r}.txt")):
                m = re.match(r"thresh_([\d\.]+)_run_\d+\.txt", cand.name, re.IGNORECASE)
                if m and abs(float(m.group(1)) - t) < 1e-6:
                    found_file = cand
                    break

            if found_file:
                content = found_file.read_text()
                avg_match, wc_match = extract_latency_summary(content)
                active_match = re.search(r"(?:SIGA|Active) Rate:\s*([\d\.]+)\s*%", content, re.IGNORECASE)
                still_match  = re.search(r"(?:INSIGA|Still) Rate \(Bypass\):\s*([\d\.]+)\s*%", content, re.IGNORECASE)

                missing_metrics = []
                if avg_match is None:
                    missing_metrics.append("average latency")
                if wc_match is None:
                    missing_metrics.append("worst-case latency")
                if active_match is None:
                    missing_metrics.append("SIGA rate")
                if still_match is None:
                    missing_metrics.append("INSIGA rate")
                if missing_metrics:
                    print(
                        f"❌ Error: {found_file} is missing required metrics: "
                        f"{', '.join(missing_metrics)}"
                    )
                    sys.exit(1)

                lat = float(avg_match.group(1))
                wc_lat = float(wc_match.group(1))
                act_val = float(active_match.group(1))
                st_val = float(still_match.group(1))
                if abs((act_val + st_val) - 100.0) > 0.02:
                    print(
                        f"❌ Error: {found_file} has inconsistent classification rates: "
                        f"SIGA={act_val:.2f}% and INSIGA={st_val:.2f}%"
                    )
                    sys.exit(1)

                print(f"✅ Loaded {found_file.name} -> avg: {lat:.2f} ms, SIGA: {act_val:.2f}%, INSIGA: {st_val:.2f}%")
            else:
                print(f"❌ Error: Report not found for threshold {t} run {r} in {reports_dir}")
                sys.exit(1)

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
    Format: Threshold,Average_ms,WorstCase_ms,SIGA_Rate_Pct,INSIGA_Rate_Pct
    This is the single source of truth: graphs are always rendered from this file.
    """
    csv_path = output_dir / TEST2_CSV_NAME
    with open(csv_path, "w") as f:
        f.write("Threshold,Average_ms,WorstCase_ms,SIGA_Rate_Pct,INSIGA_Rate_Pct\n")
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
                       linestyle=':', color='#ff7f0e', label='% SIGA Rate')
        lines.append(l1)
        labels.append('% SIGA Rate')
        for i, x in enumerate(x_coords):
            texts.append(annotate_point(ax2, f"{active_rates[i]:.1f}%", (x, active_rates[i]),
                                        xytext=(0, 12), color='#ff7f0e'))
    if show_still and still_rates:
        l2, = ax2.plot(x_coords, still_rates, marker='v', markersize=7, linewidth=2,
                       linestyle=':', color='#2ca02c', label='% INSIGA Rate (Bypass)')
        lines.append(l2)
        labels.append('% INSIGA Rate (Bypass)')
        for i, x in enumerate(x_coords):
            texts.append(annotate_point(ax2, f"{still_rates[i]:.1f}%", (x, still_rates[i]),
                                        xytext=(0, 12), color='#2ca02c'))
    optimize_annotations(texts, ax=ax2)

    ax2.set_ylabel('Percentage Rate (%)', fontsize=AXIS_LABEL_FONT_SIZE, color='#555555', labelpad=10)
    ax2.set_ylim(-5, 115)
    l_lines, l_labels = ax.get_legend_handles_labels()
    add_collision_aware_legend(ax, l_lines + lines, l_labels + labels)


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
    figsize = SQUARE_FIGURE_SIZE if aspect_1_1 else STANDARD_FIGURE_SIZE

    x_coords = get_x_coordinates(thresholds, equidistant_x, log_x)
    # ── Graph 1: Average Monitor Latency vs. Threshold ───────────────────────
    try:
        color_lat = '#1f77b4'
        fig, ax = plt.subplots(figsize=figsize, dpi=FIGURE_DPI)
        if aspect_1_1:
            ax.set_box_aspect(1)
        ax.plot(x_coords, avg_latencies, marker='o', markersize=8, linewidth=2.5,
                color=color_lat)
        apply_log_scales(ax, x_coords, avg_latencies, log_x=log_x, log_y=log_y)
        texts = []
        for i, x in enumerate(x_coords):
            texts.append(annotate_point(ax, f"{avg_latencies[i]:.2f} ms", (x, avg_latencies[i]),
                                        xytext=(0, 12), color=color_lat))
        optimize_annotations(texts, ax=ax)

        ax.set_xlabel('Motion Threshold Value, Log scale', fontsize=AXIS_LABEL_FONT_SIZE, labelpad=10)
        ax.set_ylabel(f'Average {LATENCY_DISPLAY_NAME} (ms)', fontsize=AXIS_LABEL_FONT_SIZE, color=color_lat, labelpad=10)
        set_threshold_x_ticks(ax, x_coords, thresholds, log_x)
        if equidistant_x:
            set_equidistant_x_limits(ax, len(x_coords))
        if not log_y:
            set_latency_y_limits(ax, avg_latencies)
        ax.grid(True, linestyle='--', alpha=0.4)
        _add_rate_twinx(ax, x_coords, active_rates, still_rates, show_active, show_still)

        plot_title = f"{test_label}: Average {LATENCY_DISPLAY_NAME} vs. Motion Threshold — {mode_label}"
        if not no_title:
            fig.suptitle(plot_title, fontsize=FIGURE_TITLE_FONT_SIZE, fontweight='bold', y=1.01)
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
        fig, ax = plt.subplots(figsize=figsize, dpi=FIGURE_DPI)
        if aspect_1_1:
            ax.set_box_aspect(1)
        ax.plot(x_coords, wc_latencies, marker='s', markersize=8, linewidth=2.5,
                linestyle='--', color=color_wc)
        apply_log_scales(ax, x_coords, wc_latencies, log_x=log_x, log_y=log_y)
        texts = []
        for i, x in enumerate(x_coords):
            texts.append(annotate_point(ax, f"{wc_latencies[i]:.2f} ms", (x, wc_latencies[i]),
                                        xytext=(0, 12), color=color_wc))
        optimize_annotations(texts, ax=ax)

        ax.set_xlabel('Motion Threshold Value, Log scale', fontsize=AXIS_LABEL_FONT_SIZE, labelpad=10)
        ax.set_ylabel(f'Worst-Case {LATENCY_DISPLAY_NAME} (ms)', fontsize=AXIS_LABEL_FONT_SIZE, color=color_wc, labelpad=10)
        set_threshold_x_ticks(ax, x_coords, thresholds, log_x)
        if equidistant_x:
            set_equidistant_x_limits(ax, len(x_coords))
        if not log_y:
            set_latency_y_limits(ax, wc_latencies)
        ax.grid(True, linestyle='--', alpha=0.4)
        _add_rate_twinx(ax, x_coords, active_rates, still_rates, show_active, show_still)

        plot_title = f"{test_label}: Worst-Case {LATENCY_DISPLAY_NAME} vs. Motion Threshold — {mode_label}"
        if not no_title:
            fig.suptitle(plot_title, fontsize=FIGURE_TITLE_FONT_SIZE, fontweight='bold', y=1.01)
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
    figsize = SQUARE_FIGURE_SIZE if aspect_1_1 else STANDARD_FIGURE_SIZE

    # ── Plot 1: Average Latency (Local vs Remote) ────────────────────────────
    try:
        fig, ax = plt.subplots(figsize=figsize, dpi=FIGURE_DPI)
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
                                        xytext=(0, local_offset), color='#1f77b4'))
            texts.append(annotate_point(ax, f"{r_avg[i]:.2f}", (x, r_avg[i]),
                                        xytext=(0, remote_offset), color='#d62728'))
        optimize_annotations(texts, ax=ax)

        ax.set_xlabel('Motion Threshold Value, Log scale', fontsize=AXIS_LABEL_FONT_SIZE, labelpad=10)
        ax.set_ylabel(f'Average {LATENCY_DISPLAY_NAME} (ms)', fontsize=AXIS_LABEL_FONT_SIZE, labelpad=10)
        set_threshold_x_ticks(ax, x_coords, common_t, log_x)
        if equidistant_x:
            set_equidistant_x_limits(ax, len(x_coords))
        if not log_y:
            set_latency_y_limits(ax, l_avg + r_avg)
        ax.grid(True, linestyle='--', alpha=0.4)
        add_collision_aware_legend(ax)

        _add_rate_twinx(ax, x_coords, plot_act, plot_st, show_active, show_still)

        plot_title = f"{test_label}: Average {LATENCY_DISPLAY_NAME} vs. Threshold — Local vs. Remote Auth"
        if not no_title:
            fig.suptitle(plot_title, fontsize=FIGURE_TITLE_FONT_SIZE, fontweight='bold', y=1.01)
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
        fig, ax = plt.subplots(figsize=figsize, dpi=FIGURE_DPI)
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
                                        xytext=(0, local_offset), color='#2ca02c'))
            texts.append(annotate_point(ax, f"{r_wc[i]:.2f}", (x, r_wc[i]),
                                        xytext=(0, remote_offset), color='#9467bd'))
        optimize_annotations(texts, ax=ax)

        ax.set_xlabel('Motion Threshold Value, Log scale', fontsize=AXIS_LABEL_FONT_SIZE, labelpad=10)
        ax.set_ylabel(f'Worst-Case {LATENCY_DISPLAY_NAME} (ms)', fontsize=AXIS_LABEL_FONT_SIZE, labelpad=10)
        set_threshold_x_ticks(ax, x_coords, common_t, log_x)
        if equidistant_x:
            set_equidistant_x_limits(ax, len(x_coords))
        if not log_y:
            set_latency_y_limits(ax, l_wc + r_wc)
        ax.grid(True, linestyle='--', alpha=0.4)
        add_collision_aware_legend(ax)

        _add_rate_twinx(ax, x_coords, plot_act, plot_st, show_active, show_still)

        plot_title = f"{test_label}: Worst-Case {LATENCY_DISPLAY_NAME} vs. Threshold — Local vs. Remote Auth"
        if not no_title:
            fig.suptitle(plot_title, fontsize=FIGURE_TITLE_FONT_SIZE, fontweight='bold', y=1.01)
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
    header = f"{'Threshold (τ)':>14} | {'Average (ms)':>14} | {'Worst-Case (ms)':>16} | {'SIGA (%)':>11} | {'INSIGA (%)':>10}"
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
# Test 3: Validity Period x Motion Threshold Monitor-Latency Heatmap
# ─────────────────────────────────────────────────────────────────────────────

TEST3_CSV_NAME = "validity_threshold_latency.csv"
TEST3_EXPECTED_AXIS_SIZE = 5
TEST3_REPORT_PATTERN = re.compile(
    r"val_(\d+(?:\.\d+)?)s_thresh_(\d+(?:\.\d+)?)_run_(\d+)\.txt",
    re.IGNORECASE,
)


def validate_test3_axes(validities, thresholds) -> None:
    validity_set = set(validities)
    threshold_set = set(thresholds)
    if (len(validity_set) == TEST3_EXPECTED_AXIS_SIZE
            and len(threshold_set) == TEST3_EXPECTED_AXIS_SIZE):
        return

    raise ValueError(
        f"Test 3 requires {TEST3_EXPECTED_AXIS_SIZE} unique validity values "
        f"and {TEST3_EXPECTED_AXIS_SIZE} unique threshold values, but found "
        f"validities={sorted(validity_set)} and thresholds={sorted(threshold_set)}. "
        "Threshold values are discovered from report filenames and are not "
        "restricted to a predefined list."
    )


def discover_test3_reports(reports_dir: Path) -> tuple[list, list, int, dict]:
    """Load a complete rectangular Test 3 report grid, failing on missing data."""
    report_files = sorted(reports_dir.glob("val_*s_thresh_*_run_*.txt"))
    discovered = {}
    validities = set()
    thresholds = set()

    for report_file in report_files:
        match = TEST3_REPORT_PATTERN.fullmatch(report_file.name)
        if not match:
            continue
        validity = float(match.group(1))
        threshold = float(match.group(2))
        run = int(match.group(3))
        key = (validity, threshold, run)
        if key in discovered:
            print(f"❌ Error: Duplicate Test 3 report for validity={validity}s, "
                  f"threshold={threshold}, run={run}.")
            sys.exit(1)
        discovered[key] = report_file
        validities.add(validity)
        thresholds.add(threshold)

    if not discovered:
        print(f"❌ Error: No val_*s_thresh_*_run_*.txt files found in {reports_dir}")
        sys.exit(1)

    try:
        validate_test3_axes(validities, thresholds)
    except ValueError as error:
        print(f"❌ Error: {error}")
        sys.exit(1)

    sorted_validities = sorted(validities)
    sorted_thresholds = sorted(thresholds)
    max_run = max(run for _, _, run in discovered)
    expected_runs = set(range(1, max_run + 1))
    errors = []

    for threshold in sorted_thresholds:
        for validity in sorted_validities:
            actual_runs = {
                run for val, thresh, run in discovered
                if val == validity and thresh == threshold
            }
            missing = sorted(expected_runs - actual_runs)
            extra = sorted(actual_runs - expected_runs)
            if missing or extra:
                errors.append(
                    f"validity={validity:g}s, threshold={threshold:g}: "
                    f"missing runs={missing or 'none'}, extra runs={extra or 'none'}"
                )

    if errors:
        print("❌ Error: Test 3 report grid is incomplete:")
        for error in errors:
            print(f"   - {error}")
        sys.exit(1)

    results = {(validity, threshold): []
               for threshold in sorted_thresholds
               for validity in sorted_validities}
    for threshold in sorted_thresholds:
        for validity in sorted_validities:
            for run in range(1, max_run + 1):
                report_file = discovered[(validity, threshold, run)]
                match, _ = extract_latency_summary(report_file.read_text())
                if not match:
                    print("❌ Error: no supported average latency metric was found in "
                          f"{report_file}")
                    sys.exit(1)
                latency = float(match.group(1))
                results[(validity, threshold)].append(latency)
                print(f"✅ Loaded {report_file.name} -> avg: {latency:.2f} ms")

    print("🔍 Auto-discovered Test 3: "
          f"validities={[f'{value:g}' for value in sorted_validities]}s, "
          f"thresholds={[f'{value:g}' for value in sorted_thresholds]}, "
          f"runs={max_run} per cell")
    return sorted_validities, sorted_thresholds, max_run, results


def write_test3_csv(validities: list, thresholds: list, run_count: int,
                    averages: dict, output_dir: Path) -> Path:
    csv_path = output_dir / TEST3_CSV_NAME
    with open(csv_path, "w") as csv_file:
        csv_file.write(
            "Validity_s,Threshold,Run_Count,Average_Monitor_Latency_ms\n"
        )
        for threshold in thresholds:
            for validity in validities:
                csv_file.write(
                    f"{validity:g},{threshold:.10g},{run_count},"
                    f"{averages[(validity, threshold)]:.4f}\n"
                )
    print(f"📊 CSV saved to: {csv_path}")
    return csv_path


def read_test3_csv(csv_path: Path) -> tuple[list, list, dict, dict]:
    validities = set()
    thresholds = set()
    run_counts = {}
    averages = {}

    with open(csv_path) as csv_file:
        header = csv_file.readline().strip().split(",")
        expected_header = [
            "Validity_s", "Threshold", "Run_Count",
            "Average_Monitor_Latency_ms",
        ]
        if header != expected_header:
            raise ValueError(
                f"Unexpected Test 3 CSV header in {csv_path}: {header}"
            )
        for line_number, line in enumerate(csv_file, start=2):
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) != 4:
                raise ValueError(
                    f"Invalid Test 3 CSV row at {csv_path}:{line_number}"
                )
            validity = float(parts[0])
            threshold = float(parts[1])
            key = (validity, threshold)
            if key in averages:
                raise ValueError(f"Duplicate Test 3 CSV cell {key} in {csv_path}")
            validities.add(validity)
            thresholds.add(threshold)
            run_counts[key] = int(parts[2])
            averages[key] = float(parts[3])

    sorted_validities = sorted(validities)
    sorted_thresholds = sorted(thresholds)
    validate_test3_axes(sorted_validities, sorted_thresholds)
    expected_cells = {
        (validity, threshold)
        for threshold in sorted_thresholds
        for validity in sorted_validities
    }
    missing_cells = expected_cells - set(averages)
    if missing_cells:
        missing_text = ", ".join(
            f"({validity:g}s, {threshold:g})"
            for validity, threshold in sorted(missing_cells)
        )
        raise ValueError(f"Test 3 CSV is missing cells: {missing_text}")

    return sorted_validities, sorted_thresholds, run_counts, averages


def plot_test3_heatmap(csv_path: Path, output_dir: Path, auth_mode: str,
                       no_title: bool = False) -> None:
    if not MATPLOTLIB_AVAILABLE:
        print("\n⚠️  Warning: matplotlib is not available — heatmap skipped.")
        return

    validities, thresholds, _, averages = read_test3_csv(csv_path)
    matrix = [
        [averages[(validity, threshold)] for validity in validities]
        for threshold in thresholds
    ]

    fig, ax = plt.subplots(figsize=HEATMAP_FIGURE_SIZE, dpi=FIGURE_DPI)
    image = ax.imshow(matrix, cmap="RdYlGn_r", aspect="equal", origin="lower")
    ax.set_xticks(range(len(validities)))
    ax.set_xticklabels(
        [f"{validity:g}s" for validity in validities],
        fontsize=HEATMAP_TICK_LABEL_FONT_SIZE,
    )
    ax.set_yticks(range(len(thresholds)))
    ax.set_yticklabels(
        [f"{threshold:.10g}" for threshold in thresholds],
        fontsize=HEATMAP_TICK_LABEL_FONT_SIZE,
    )
    ax.set_xlabel(
        "Relative Validity Period (seconds)",
        fontsize=HEATMAP_AXIS_LABEL_FONT_SIZE,
    )
    ax.set_ylabel("Motion Threshold", fontsize=HEATMAP_AXIS_LABEL_FONT_SIZE)

    for row_index, threshold in enumerate(thresholds):
        for column_index, validity in enumerate(validities):
            value = averages[(validity, threshold)]
            red, green, blue, _ = image.cmap(image.norm(value))
            luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
            text_color = "black" if luminance > 0.55 else "white"
            ax.text(
                column_index, row_index, f"{value:.2f}",
                ha="center", va="center", color=text_color,
                fontsize=HEATMAP_DATA_LABEL_FONT_SIZE, fontweight="bold",
            )

    colorbar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    colorbar.set_label(
        f"Average {LATENCY_DISPLAY_NAME} (ms)",
        fontsize=HEATMAP_COLORBAR_LABEL_FONT_SIZE,
    )
    colorbar.ax.tick_params(labelsize=HEATMAP_COLORBAR_TICK_FONT_SIZE)
    if not no_title:
        fig.suptitle(
            f"TEST3 {LATENCY_DISPLAY_NAME} Heatmap — {auth_mode.capitalize()} Auth",
            fontsize=HEATMAP_TITLE_FONT_SIZE, fontweight="bold", y=0.98,
        )
    fig.tight_layout(rect=(0, 0, 1, 0.95) if not no_title else None,
                     pad=LAYOUT_PADDING)

    file_stem = f"test3_{auth_mode}_validity_threshold_latency_heatmap"
    save_plot(fig, output_dir / f"{file_stem}.png")
    save_plot(fig, output_dir / f"{file_stem}.pdf")
    plt.close(fig)
    print(f"🌡️  Heatmap saved to: {output_dir / file_stem}.png / .pdf")


def generate_test3_outputs(validities: list, thresholds: list, run_count: int,
                           results: dict, output_dir: Path, auth_mode: str,
                           no_title: bool = False) -> Path:
    averages = {
        key: sum(run_latencies) / len(run_latencies)
        for key, run_latencies in results.items()
    }

    report_path = output_dir / "validity_threshold_latency_report.txt"
    with open(report_path, "w") as report_file:
        report_file.write("VALIDITY PERIOD x MOTION THRESHOLD LATENCY REPORT\n")
        report_file.write("=================================================\n")
        report_file.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        report_file.write(f"Runs per cell: {run_count}\n\n")
        report_file.write(
            f"{'Validity (s)':>12} | {'Threshold':>12} | "
            f"{'Runs':>6} | {f'Average {LATENCY_DISPLAY_NAME} (ms)':>40}\n"
        )
        report_file.write("-" * 69 + "\n")
        for threshold in thresholds:
            for validity in validities:
                report_file.write(
                    f"{validity:>12g} | {threshold:>12.10g} | "
                    f"{run_count:>6} | {averages[(validity, threshold)]:>28.4f}\n"
                )
    print(f"📄 Text report saved to: {report_path}")

    csv_path = write_test3_csv(
        validities, thresholds, run_count, averages, output_dir
    )
    plot_test3_heatmap(csv_path, output_dir, auth_mode, no_title=no_title)
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
        "--test-name", default=None, choices=["test1", "test2", "test3", None],
        help="Test identifier for plot title and output filename (test1, test2, or test3). "
             "Auto-inferred from the reports-dir path if not specified."
    )
    parser.add_argument(
        "--auth-mode", default=None, choices=["local", "remote", None],
        help="Auth mode for plot title and output filename (local or remote). "
             "Auto-inferred from the reports-dir path if not specified."
    )
    parser.add_argument(
        "--bypass-mode", default="insiga", choices=["insiga", "siga", "still", "active"],
        help="Definition of bypass rate for Test 2 graphs. INSIGA/SIGA are canonical; still/active remain accepted for old reports."
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
        "--no-auto-compare", action="store_true",
        help="Do not auto-discover an opposite-auth CSV when --compare-csv is omitted."
    )
    parser.add_argument(
        "--show-siga-rate", "--show-active-rate", action="store_true", dest="show_active_rate",
        help="Include the average %% SIGA Rate on a secondary y-axis for Test 2 graphs."
    )
    parser.add_argument(
        "--show-insiga-rate", "--show-still-rate", action="store_true", dest="show_still_rate",
        help="Include the average %% INSIGA Rate (Bypass Rate) on a secondary y-axis for Test 2 graphs."
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
    if args.bypass_mode == "insiga" and inferred_bypass and inferred_bypass != "insiga":
        args.bypass_mode = inferred_bypass
        print(f"🔍 Auto-inferred bypass mode: {args.bypass_mode}")

    # Test 3 has the most specific filename pattern, so detect it before the
    # one-dimensional Test 1 and Test 2 report formats.
    is_test3 = (
        test_name.lower() == "test3"
        or bool(list(reports_dir.glob("val_*s_thresh_*_run_*.txt")))
        or (reports_dir / TEST3_CSV_NAME).is_file()
        or (output_dir / TEST3_CSV_NAME).is_file()
    )
    if is_test3:
        test_name = "test3"

    # Check if there are any Test 2 reports (thresh_*_run_*.txt) or explicitly requested test2
    is_test2 = (
        not is_test3
        and (
            test_name.lower() == "test2"
            or bool(list(reports_dir.glob("thresh_*_run_*.txt")))
            or (reports_dir / TEST2_CSV_NAME).is_file()
            or (output_dir / TEST2_CSV_NAME).is_file()
        )
    )
    if is_test2:
        test_name = "test2"

    if is_test2 and args.equidistant_x and args.log_x:
        parser.error("--equidistant-x and --log-x cannot be used together")
    if is_test3 and (args.equidistant_x or args.log_x or args.log_y):
        print("ℹ️  Ignoring axis-spacing options for Test 3; both heatmap axes are categorical.")
    elif not is_test2 and args.log_x:
        print("ℹ️  Ignoring --log-x for Test 1; its validity-period x-axis is always linear.")

    plot_options = {
        "equidistant_x": args.equidistant_x,
        "aspect_1_1": args.aspect_1_1,
        "no_title": args.no_title,
        "log_x": args.log_x if is_test2 else False,
        "log_y": args.log_y if not is_test3 else False,
    }
    print_plot_options(
        test_name, output_dir, plot_options,
        show_active=args.show_active_rate, show_still=args.show_still_rate
    )

    if is_test3:
        if args.compare_csv:
            parser.error("--compare-csv is not supported for Test 3 heatmaps")
        compare_csv = None
    else:
        compare_csv = Path(args.compare_csv).resolve() if args.compare_csv else (
            None if args.no_auto_compare else discover_compare_csv(
                reports_dir, test_name, auth_mode, args.bypass_mode
            )
        )
        if compare_csv and not args.compare_csv:
            print(f"🔍 Auto-discovered comparison CSV: {compare_csv}")
        elif not compare_csv and not args.compare_csv:
            other_mode = "remote" if auth_mode == "local" else "local"
            print(f"ℹ️  No completed {other_mode} comparison run was found; "
                  "only single-mode graphs will be generated.")

    if is_test3:
        primary_csv = find_existing_summary_csv(
            reports_dir, output_dir, TEST3_CSV_NAME
        )
        if primary_csv:
            print(f"♻️  Existing Test 3 CSV found: {primary_csv}")
            print("   Skipping report aggregation and CSV generation.")
            try:
                plot_test3_heatmap(
                    primary_csv, output_dir, auth_mode,
                    no_title=args.no_title,
                )
            except ValueError as error:
                print(f"❌ Error reading Test 3 CSV: {error}")
                sys.exit(1)
        else:
            validities, thresholds, runs, results = discover_test3_reports(
                reports_dir
            )
            print("=" * 80)
            print("AGGREGATING TEST 3 REPORTS & GENERATING LATENCY HEATMAP")
            print("=" * 80)
            print(f"Reports dir : {reports_dir}")
            print(f"Output dir  : {output_dir}")
            print(f"Validities  : {[f'{value:g}' for value in validities]} seconds")
            print(f"Thresholds  : {[f'{value:g}' for value in thresholds]}")
            print(f"Runs        : {runs} per grid cell")
            print(f"Grid        : {len(validities)} x {len(thresholds)}")
            print(f"Test / Mode : {test_name} / {auth_mode}")
            print("=" * 80)
            generate_test3_outputs(
                validities, thresholds, runs, results, output_dir, auth_mode,
                no_title=args.no_title,
            )
    elif is_test2:
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
                reports_dir, thresholds, runs
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
                print("\n📊 Generating Test 2 comparative plots from CSVs...")
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

                print("\n📊 Generating comparative plots from CSVs...")
                print(f"   Local  CSV: {local_csv}")
                print(f"   Remote CSV: {remote_csv}")
                generate_comparative_plots(
                    local_csv, remote_csv, output_dir, test_name=test_name,
                    **plot_options
                )
    print("\n✅ Done!\n")


if __name__ == "__main__":
    main()
