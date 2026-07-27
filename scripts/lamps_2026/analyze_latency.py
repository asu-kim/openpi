#!/usr/bin/env python3
import json
import argparse
import re
from pathlib import Path


def _read_actuator_latency_records(actuator_log: str | None) -> list[dict]:
    if actuator_log is None:
        return []
    records = []
    seen_record_ids = set()
    with open(actuator_log, "r", encoding="utf-8") as log_file:
        for line_number, line in enumerate(log_file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid actuator latency JSON on line {line_number}: {exc}") from exc
            required = {
                "record_id",
                "observation_id",
                "motion_label",
                "monitor_start_ms",
                "driver_handoff_ms",
                "monitor_actuator_ms",
            }
            missing = sorted(required.difference(record))
            if missing:
                raise ValueError(
                    f"actuator latency line {line_number} is missing fields: {', '.join(missing)}"
                )
            record_id = record["record_id"]
            if record_id in seen_record_ids:
                raise ValueError(f"duplicate actuator latency record_id {record_id}")
            latency = record["monitor_actuator_ms"]
            if not isinstance(latency, (int, float)) or isinstance(latency, bool) or latency < 0:
                raise ValueError(f"invalid monitor_actuator_ms for record {record_id}: {latency!r}")
            expected_latency = record["driver_handoff_ms"] - record["monitor_start_ms"]
            if latency != expected_latency:
                raise ValueError(
                    f"record {record_id} latency mismatch: stored {latency}, calculated {expected_latency}"
                )
            seen_record_ids.add(record_id)
            records.append(record)
    records.sort(key=lambda record: record["record_id"])
    return records


def analyze_latencies(input_file: str, output_file: str, actuator_log: str | None = None):
    records = []
    actuator_records = _read_actuator_latency_records(actuator_log)

    # Read the JSONL file using fast regex to avoid parsing massive token arrays
    with open(input_file, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            idx_match = re.search(r'"record_index":\s*(\d+)', line)
            time_match = re.search(r'"time_unix":\s*([\d\.]+)', line)
            if idx_match and time_match:
                record = {"record_index": int(idx_match.group(1)), "time_unix": float(time_match.group(1))}
                mon_match = re.search(r'"monitor_latency":\s*([\d\.]+)', line)
                if mon_match:
                    record["monitor_latency"] = float(mon_match.group(1))
                lbl_match = re.search(r'"motion_label":\s*"([^"]+)"', line)
                if lbl_match:
                    record["motion_label"] = lbl_match.group(1)
                for field in ("motion_intra_ptp", "motion_inter_delta", "motion_combined_score"):
                    score_match = re.search(rf'"{field}":\s*([-+\d.eE]+)', line)
                    if score_match:
                        record[field] = float(score_match.group(1))
                records.append(record)
            else:
                try:
                    data = json.loads(line)
                    if "time_unix" in data and "record_index" in data:
                        records.append(data)
                except json.JSONDecodeError:
                    pass

    # Sort records by their index to guarantee sequential order
    records.sort(key=lambda x: x["record_index"])

    secure_rows = []
    if actuator_records:
        records_by_index = {record["record_index"]: record for record in records}
        for actuator_record in actuator_records:
            observation_id = actuator_record["observation_id"]
            token_record = records_by_index.get(observation_id)
            if token_record is None:
                raise ValueError(
                    f"actuator record {actuator_record['record_id']} references missing observation {observation_id}"
                )
            missing_scores = [
                field
                for field in ("motion_intra_ptp", "motion_inter_delta", "motion_combined_score")
                if field not in token_record
            ]
            if missing_scores:
                raise ValueError(
                    f"observation {observation_id} is missing motion metrics: {', '.join(missing_scores)}"
                )
            token_label = token_record.get("motion_label")
            if token_label is not None and token_label != actuator_record["motion_label"]:
                raise ValueError(
                    f"motion label mismatch for observation {observation_id}: "
                    f"monitor={token_label}, actuator={actuator_record['motion_label']}"
                )
            secure_rows.append(
                {
                    **actuator_record,
                    "motion_intra_ptp": token_record["motion_intra_ptp"],
                    "motion_inter_delta": token_record["motion_inter_delta"],
                    "motion_combined_score": token_record["motion_combined_score"],
                }
            )

    if len(records) < 2:
        print("Not enough records found in the file to calculate latencies.")
        return

    latencies = []

    with open(output_file, "w") as out:
        out.write("Inference Latency Analysis\n")
        out.write(f"Source: {input_file}\n")
        out.write("=" * 50 + "\n\n")

        if "monitor_latency" in records[0]:
            out.write(
                f"Record {records[0]['record_index']:>4} (Initial Step)        | Monitor Latency: {records[0]['monitor_latency']:>6.2f} ms\n"
            )

        for i in range(1, len(records)):
            prev = records[i - 1]
            curr = records[i]

            latency = curr["time_unix"] - prev["time_unix"]
            latencies.append(latency)

            mon_str = ""
            if "monitor_latency" in curr:
                mon_str = f" | Monitor Latency: {curr['monitor_latency']:>6.2f} ms"

            out.write(
                f"Record {prev['record_index']:>4} -> Record {curr['record_index']:>4}: {latency:.4f} seconds{mon_str}\n"
            )

        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        min_latency = min(latencies)

        # Find where max latency occurred
        max_idx = latencies.index(max_latency)
        worst_prev = records[max_idx]["record_index"]
        worst_curr = records[max_idx + 1]["record_index"]

        out.write("\n" + "=" * 50 + "\n")
        out.write("SUMMARY STATISTICS (Step Inference Latency)\n")
        out.write("=" * 50 + "\n")
        out.write(f"Total Records Analyzed: {len(records)}\n")
        out.write(f"Average Step Latency:   {avg_latency:.4f} seconds\n")
        out.write(f"Best-Case Step Latency: {min_latency:.4f} seconds\n")
        out.write(f"Worst-Case Step Latency:{max_latency:.4f} seconds\n")
        out.write(f"  └─ Occurred between Record {worst_prev} and Record {worst_curr}\n")

        monitor_lats = [(r["record_index"], r["monitor_latency"]) for r in records if "monitor_latency" in r]
        if monitor_lats:
            vals = [val for _, val in monitor_lats]
            avg_mon = sum(vals) / len(vals)
            min_mon = min(vals)
            max_mon = max(vals)
            worst_mon_idx = [idx for idx, val in monitor_lats if val == max_mon][0]

            out.write("\n" + "=" * 50 + "\n")
            out.write("SUMMARY STATISTICS (Monitor Execution Latency)\n")
            out.write("=" * 50 + "\n")
            out.write(f"Total Monitor Records:  {len(monitor_lats)}\n")
            out.write(f"Average Monitor Latency:{avg_mon:.2f} ms\n")
            out.write(f"Best-Case Monitor Lat.: {min_mon:.2f} ms\n")
            out.write(f"Worst-Case Monitor Lat.:{max_mon:.2f} ms\n")
            out.write(f"  └─ Occurred at Record {worst_mon_idx}\n")

            motion_lats = [r["motion_label"] for r in records if "motion_label" in r]
            if motion_lats:
                total_lbl = len(motion_lats)
                siga_cnt = sum(1 for label in motion_lats if label in {"siga", "active"})
                insiga_cnt = sum(1 for label in motion_lats if label in {"insiga", "still"})
                siga_rate = (siga_cnt / total_lbl) * 100.0 if total_lbl else 0.0
                insiga_rate = (insiga_cnt / total_lbl) * 100.0 if total_lbl else 0.0

                out.write("\n" + "=" * 50 + "\n")
                out.write("SUMMARY STATISTICS (Motion Classification & Bypass)\n")
                out.write("=" * 50 + "\n")
                out.write(f"Total Labeled Records:  {total_lbl}\n")
                out.write(f"SIGA Records:           {siga_cnt} ({siga_rate:.2f}%)\n")
                out.write(f"INSIGA Records:         {insiga_cnt} ({insiga_rate:.2f}%)\n")
                out.write(f"SIGA Rate:              {siga_rate:.2f}%\n")
                out.write(f"INSIGA Rate (Bypass):   {insiga_rate:.2f}%\n")

        if secure_rows:
            out.write("\n" + "=" * 112 + "\n")
            out.write("SECURE ARCHITECTURE PER-INFERENCE RESULTS\n")
            out.write("=" * 112 + "\n")
            out.write(
                f"{'Record':>8} | {'Inference':>9} | {'Label':>6} | {'Intra PTP':>12} | "
                f"{'Inter Delta':>12} | {'Combined':>12} | {'Monitor-Actuator (ms)':>21}\n"
            )
            out.write("-" * 112 + "\n")
            for row in secure_rows:
                out.write(
                    f"{row['record_id']:>8} | {row['observation_id']:>9} | "
                    f"{row['motion_label'].upper():>6} | {row['motion_intra_ptp']:>12.6f} | "
                    f"{row['motion_inter_delta']:>12.6f} | {row['motion_combined_score']:>12.6f} | "
                    f"{float(row['monitor_actuator_ms']):>21.2f}\n"
                )

            actuator_lats = [float(record["monitor_actuator_ms"]) for record in secure_rows]
            avg_actuator = sum(actuator_lats) / len(actuator_lats)
            min_actuator = min(actuator_lats)
            max_actuator = max(actuator_lats)
            worst_actuator_record = next(
                record["record_id"]
                for record in secure_rows
                if float(record["monitor_actuator_ms"]) == max_actuator
            )
            siga_count = sum(1 for record in secure_rows if record["motion_label"] == "siga")
            insiga_count = sum(1 for record in secure_rows if record["motion_label"] == "insiga")
            total_secure_records = len(secure_rows)
            out.write("\n" + "=" * 50 + "\n")
            out.write("SECURE ARCHITECTURE EPISODE SUMMARY\n")
            out.write("=" * 50 + "\n")
            out.write(f"Source: {actuator_log}\n")
            out.write(f"Total Actuator Records: {total_secure_records}\n")
            out.write(f"SIGA Records: {siga_count} ({siga_count / total_secure_records * 100:.2f}%)\n")
            out.write(f"INSIGA Records: {insiga_count} ({insiga_count / total_secure_records * 100:.2f}%)\n")
            out.write(f"SIGA Rate: {siga_count / total_secure_records * 100:.2f}%\n")
            out.write(f"INSIGA Rate (Bypass): {insiga_count / total_secure_records * 100:.2f}%\n")
            out.write(f"Average Monitor-Actuator Latency: {avg_actuator:.2f} ms\n")
            out.write(f"Best-Case Monitor-Actuator Lat.:  {min_actuator:.2f} ms\n")
            out.write(f"Worst-Case Monitor-Actuator Lat.: {max_actuator:.2f} ms\n")
            out.write(f"  └─ Absolute maximum occurred at Record {worst_actuator_record}\n")

    print(f"✅ Analysis complete! Detailed report saved to: {output_file}")
    print("-" * 40)
    print(f"Total Records Analyzed: {len(records)}")
    print(f"Average Step Latency:   {avg_latency:.4f}s")
    print(f"Worst-Case Step Latency:{max_latency:.4f}s")

    if monitor_lats:
        vals = [val for _, val in monitor_lats]
        print("-" * 40)
        print(f"Average Monitor Latency: {sum(vals) / len(vals):.2f} ms")
        print(f"Worst-Case Monitor Lat.: {max(vals):.2f} ms")

        motion_lats = [r["motion_label"] for r in records if "motion_label" in r]
        if motion_lats:
            total_lbl = len(motion_lats)
            siga_rate = (
                (sum(1 for label in motion_lats if label in {"siga", "active"}) / total_lbl) * 100.0
                if total_lbl
                else 0.0
            )
            insiga_rate = (
                (sum(1 for label in motion_lats if label in {"insiga", "still"}) / total_lbl) * 100.0
                if total_lbl
                else 0.0
            )
            print("-" * 40)
            print(f"SIGA Rate:               {siga_rate:.2f}%")
            print(f"INSIGA Rate (Bypass):    {insiga_rate:.2f}%")

    if secure_rows:
        actuator_lats = [float(record["monitor_actuator_ms"]) for record in secure_rows]
        siga_count = sum(1 for record in secure_rows if record["motion_label"] == "siga")
        insiga_count = sum(1 for record in secure_rows if record["motion_label"] == "insiga")
        print("-" * 40)
        print(f"Total Actuator Records: {len(actuator_lats)}")
        print(f"SIGA Rate: {siga_count / len(secure_rows) * 100:.2f}%")
        print(f"INSIGA Rate (Bypass): {insiga_count / len(secure_rows) * 100:.2f}%")
        print(f"Average Monitor-Actuator Latency: {sum(actuator_lats) / len(actuator_lats):.2f} ms")
        print(f"Worst-Case Monitor-Actuator Lat. (absolute): {max(actuator_lats):.2f} ms")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze step latencies from openpi JSONL logs.")
    parser.add_argument("input_file", help="Path to the input .jsonl log file")
    parser.add_argument(
        "--output", "-o", default=None, help="Path to save the output report (default: latency_report_<date>.txt)"
    )
    parser.add_argument(
        "--actuator-log",
        default=None,
        help="Optional secure-actuator JSONL log containing monitor_actuator_ms records.",
    )
    args = parser.parse_args()

    # Ensure input exists
    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Error: Could not find input file '{args.input_file}'")
        exit(1)
    if args.actuator_log is not None and not Path(args.actuator_log).exists():
        print(f"Error: Could not find actuator latency log '{args.actuator_log}'")
        exit(1)

    # Generate default output name based on input filename
    output_file = args.output
    if output_file is None:
        stem = input_path.stem
        # Extract the date part from pi0_fast_tokens_YYYY-MM-DD-HH-MM-SS
        if stem.startswith("pi0_fast_tokens_"):
            date_str = stem.replace("pi0_fast_tokens_", "")
            output_name = f"latency_report_{date_str}.txt"
        else:
            output_name = f"{stem}_latency_report.txt"

        out_dir = Path("latency_reports")
        out_dir.mkdir(exist_ok=True)
        output_file = str(out_dir / output_name)

    analyze_latencies(args.input_file, output_file, actuator_log=args.actuator_log)
