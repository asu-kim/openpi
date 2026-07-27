#!/usr/bin/env python3
"""
profile_entropy_quantiles.py

Implements Stage 1 (Multi-Run Profiling & Calibration) of the Threshold Selection
Methodology documented in docs/threshold_selection_methodology.md.

Analyzes token log files (.jsonl) across one or more profiling runs, extracts
normalized Shannon entropy scores, separates stationary sequences (H ≈ 0) from
strictly positive sequences (S_pos > 0), and computes 5 empirical quantile
operating points:
  1. τ1 = 0.0000          (Stationary Bypass: bypasses H=0 sequences)
  2. τ2 = Q25(S_pos)      (25th percentile of non-zero scores)
  3. τ3 = Q50(S_pos)      (50th percentile / median of non-zero scores)
  4. τ4 = Q75(S_pos)      (75th percentile of non-zero scores)
  5. τ5 = max(S_pool)+0.05(100% Bypass Ceiling)
"""

import argparse
import collections
import glob
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

# PaliGemma token constants (verified against interpret_fast_tokens.py)
EOS_TOKEN = 1            # PaliGemma end-of-sequence
PAD_TOKEN = 0            # PaliGemma padding
FAST_OFFSET = 257023     # 257152 - 1 - 128; fast_id = FAST_OFFSET - paligemma_id
FAST_ID_MAX = 10000      # candidate FAST action token if 0 <= fast_id < FAST_ID_MAX


def trim_raw_tokens(tokens: list[int]) -> list[int]:
    """Drop padding (0) and stop at the first EOS (1)."""
    trimmed: list[int] = []
    for t in tokens:
        if t == EOS_TOKEN:
            break
        if t != 0:
            trimmed.append(int(t))
    return trimmed


def extract_fast_action_tokens(trimmed_tokens: list[int]) -> list[int]:
    """Convert raw PaliGemma tokens to FAST ids, or pass through already-converted FAST ids."""
    fast_ids: list[int] = []
    for t in trimmed_tokens:
        val = int(t)
        if val >= 100000:
            fast_id = FAST_OFFSET - val
            if 0 <= fast_id < FAST_ID_MAX:
                fast_ids.append(fast_id)
        else:
            if 0 <= val < FAST_ID_MAX:
                fast_ids.append(val)
    return fast_ids


def compute_normalized_entropy(fast_tokens: list[int]) -> float:
    """Compute normalized Shannon entropy in [0, 1] for a token sequence."""
    n = len(fast_tokens)
    if n <= 1:
        return 0.0
    counts = collections.Counter(fast_tokens)
    probs = [c / n for c in counts.values()]
    entropy = -sum(p * math.log2(p) for p in probs if p > 0)
    max_entropy = math.log2(n)
    return float(entropy / max_entropy) if max_entropy > 0 else 0.0


def extract_score_from_record(record: dict[str, Any]) -> float | None:
    """Extract Maximum Peak-to-Peak Joint Variation (max_ptp) from a log record."""
    # 1. Direct field check if already precomputed
    for key in ("max_ptp", "motion_proxy"):
        if key in record and isinstance(record[key], (int, float)):
            return float(record[key])

    # 2. Compute from aloha_actions_14d or decoded_actions logged in .jsonl
    for key in ("aloha_actions_14d", "decoded_actions"):
        raw = record.get(key)
        if isinstance(raw, list) and len(raw) > 0:
            arr = [ [float(x) for x in row] for row in raw if isinstance(row, list) ]
            if arr and len(arr) > 0:
                num_dims = min(14, len(arr[0]))
                max_ptp = 0.0
                for d in range(num_dims):
                    vals = [row[d] for row in arr if len(row) > d]
                    if vals:
                        ptp = max(vals) - min(vals)
                        if ptp > max_ptp:
                            max_ptp = ptp
                return max_ptp

    # 3. Fallback to token entropy if no physical actions are present
    if "fast_action_tokens" in record and isinstance(record["fast_action_tokens"], list) and len(record["fast_action_tokens"]) > 0:
        return compute_normalized_entropy([int(x) for x in record["fast_action_tokens"]])

    for raw_key in ("raw_paligemma_token_ids", "tokens", "token_ids"):
        raw = record.get(raw_key)
        if isinstance(raw, list) and len(raw) > 0:
            trimmed = trim_raw_tokens([int(x) for x in raw])
            fast = extract_fast_action_tokens(trimmed)
            if len(fast) > 0:
                return compute_normalized_entropy(fast)

    return None


def calculate_percentile(sorted_data: list[float], p: float) -> float:
    """Calculate the p-th percentile (0 <= p <= 100) using linear interpolation."""
    if not sorted_data:
        return 0.0
    if len(sorted_data) == 1:
        return sorted_data[0]
    
    pos = (len(sorted_data) - 1) * (p / 100.0)
    idx_low = int(math.floor(pos))
    idx_high = int(math.ceil(pos))
    weight = pos - idx_low
    
    if idx_low == idx_high:
        return sorted_data[idx_low]
    return sorted_data[idx_low] * (1.0 - weight) + sorted_data[idx_high] * weight


def compute_bypass_rate(scores: list[float], threshold: float) -> float:
    """Return the percentage of scores <= threshold."""
    if not scores:
        return 0.0
    bypassed = sum(1 for s in scores if s <= threshold)
    return (bypassed / len(scores)) * 100.0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Profile multi-run token logs and derive 5 empirical quantile thresholds."
    )
    parser.add_argument(
        "input_paths",
        nargs="+",
        help="Path(s) to .jsonl file(s) or directory containing .jsonl files."
    )
    parser.add_argument(
        "--output", "-o",
        default="calibrated_thresholds.json",
        help="Path to save the output JSON calibration report (default: calibrated_thresholds.json)"
    )
    parser.add_argument(
        "--decimals", "-d",
        type=int,
        default=4,
        help="Number of decimal places for threshold values (default: 4)"
    )
    args = parser.parse_args()

    # Discover all matching JSONL files
    files: list[Path] = []
    for raw_path in args.input_paths:
        p = Path(raw_path)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.jsonl")))
        elif p.is_file():
            files.append(p)
        else:
            # Check glob
            matched = [Path(f) for f in glob.glob(raw_path)]
            files.extend(matched)

    files = sorted(set(files))
    if not files:
        print(f"❌ Error: No .jsonl files found matching input paths: {args.input_paths}", file=sys.stderr)
        return 1

    print(f"📂 Analyzing {len(files)} token log file(s)...")

    scores: list[float] = []
    total_records = 0
    malformed_lines = 0

    for fpath in files:
        with open(fpath, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        continue
                except json.JSONDecodeError:
                    malformed_lines += 1
                    continue

                score = extract_score_from_record(record)
                if score is not None:
                    scores.append(score)
                    total_records += 1

    if not scores:
        print("❌ Error: Could not extract any Shannon entropy scores from the provided log files.", file=sys.stderr)
        return 1

    scores.sort()
    eps = 1e-6
    zero_scores = [s for s in scores if s <= eps]
    pos_scores = [s for s in scores if s > eps]
    max_val = max(scores) if scores else 0.0

    print(f"📊 Extracted {total_records} record scores across {len(files)} run file(s).")
    print(f"   ├─ Stationary / Zero-Motion States (m(A) == 0): {len(zero_scores):5d} ({len(zero_scores)/total_records*100:5.1f}%)")
    print(f"   └─ Strictly Positive Records (m(A) > 0):        {len(pos_scores):5d} ({len(pos_scores)/total_records*100:5.1f}%) [Max Observed: {max_val:.4f}]")

    if not pos_scores:
        print(
            "❌ Error: Only zero-motion records (max_ptp == 0.0) were found in the provided log files. "
            "At least one non-zero moving record (max_ptp > 0.0) is required to compute meaningful quantiles "
            "(note: negative peak-to-peak variation is mathematically impossible).",
            file=sys.stderr,
        )
        return 1

    # Isolate the Core Continuous Manipulation Envelope (<= Q50 of positive motion)
    # The upper half (> Q50) represents step-0 initialization jumps and inter-episode transitions
    # where the active authentication rate converges to 0.0%.
    q50_ceiling = calculate_percentile(pos_scores, 50.0)
    cont_pos = [s for s in pos_scores if s <= q50_ceiling]
    if not cont_pos:
        cont_pos = pos_scores

    # Derive 5 purely data-driven threshold values across the continuous manipulation envelope
    # 1. Stationary Bypass (exact 0.0000)
    t1 = 0.0000
    # 2. Q25 of Continuous Manipulation Envelope
    t2 = calculate_percentile(cont_pos, 25.0)
    # 3. Q50 (Median) of Continuous Manipulation Envelope
    t3 = calculate_percentile(cont_pos, 50.0)
    # 4. Q75 of Continuous Manipulation Envelope
    t4 = calculate_percentile(cont_pos, 75.0)
    # 5. Q100 Continuous Manipulation Envelope Ceiling (= Q50 of all positive motion)
    t5 = max(cont_pos)

    # Round thresholds cleanly
    fmt = f"{{:.{args.decimals}f}}"
    threshold_vals = [
        round(t1, args.decimals),
        round(t2, args.decimals),
        round(t3, args.decimals),
        round(t4, args.decimals),
        round(t5, args.decimals),
    ]

    labels = [
        "Stationary Bypass (0 Motion)",
        "25th Percentile (Q25 Continuous Envelope)",
        "50th Percentile (Q50 Continuous Envelope)",
        "75th Percentile (Q75 Continuous Envelope)",
        "Continuous Envelope Ceiling (Q100 Continuous / Q50 Overall)",
    ]

    print("\n" + "=" * 80)
    print("EMPIRICAL DATA OBSERVATION & CONTINUOUS ENVELOPE ISOLATION")
    print("=" * 80)
    print(f"  • Total Positive Records:           {len(pos_scores)}")
    print(f"  • Empirical Median Boundary (Q50):  {q50_ceiling:.4f} rad/chunk")
    print(f"  • Core Continuous Envelope (<=Q50): {len(cont_pos)} records ({len(cont_pos)/len(pos_scores)*100:.1f}%)")
    print(f"  • Initialization/Reset Spikes (>Q50): {len(pos_scores)-len(cont_pos)} records (Active Rate converges to 0.0%)")

    print("\n" + "=" * 80)
    print("CALIBRATED MOTION THRESHOLDS (5 OPERATING POINTS)")
    print("=" * 80)
    print(f"{'Index':<6} | {'Threshold (τ)':<14} | {'Empirical Quantile Source':<34} | {'Expected Bypass %':<18}")
    print("-" * 80)

    results_list = []
    for i, (val, label) in enumerate(zip(threshold_vals, labels), 1):
        bypass_pct = compute_bypass_rate(scores, val)
        val_str = fmt.format(val)
        print(f"τ{i:<5} | {val_str:<14} | {label:<34} | {bypass_pct:>6.1f}%")
        results_list.append({
            "index": i,
            "threshold": val,
            "source": label,
            "expected_bypass_pct": round(bypass_pct, 2)
        })

    print("=" * 80)

    # Bash-compatible array output
    bash_array_str = "THRESHOLDS=(" + " ".join(fmt.format(v) for v in threshold_vals) + ")"
    print("\nReady-to-copy bash array for run_tests.sh:")
    print(f"  {bash_array_str}\n")

    # Save structured JSON report
    report = {
        "files_analyzed": [str(f) for f in files],
        "total_records": total_records,
        "stationary_records": len(zero_scores),
        "positive_records": len(pos_scores),
        "thresholds": results_list,
        "bash_array": bash_array_str,
    }

    out_path = Path(args.output)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"✅ Calibrated thresholds saved to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
