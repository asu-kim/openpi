#!/usr/bin/env python3
import json
import argparse
import re
from pathlib import Path

def analyze_latencies(input_file: str, output_file: str):
    records = []
    
    # Read the JSONL file using fast regex to avoid parsing massive token arrays
    with open(input_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            idx_match = re.search(r'"record_index":\s*(\d+)', line)
            time_match = re.search(r'"time_unix":\s*([\d\.]+)', line)
            if idx_match and time_match:
                record = {
                    "record_index": int(idx_match.group(1)),
                    "time_unix": float(time_match.group(1))
                }
                mon_match = re.search(r'"monitor_latency":\s*([\d\.]+)', line)
                if mon_match:
                    record["monitor_latency"] = float(mon_match.group(1))
                lbl_match = re.search(r'"motion_label":\s*"([^"]+)"', line)
                if lbl_match:
                    record["motion_label"] = lbl_match.group(1)
                records.append(record)
            else:
                try:
                    data = json.loads(line)
                    if 'time_unix' in data and 'record_index' in data:
                        records.append(data)
                except json.JSONDecodeError:
                    pass
                
    # Sort records by their index to guarantee sequential order
    records.sort(key=lambda x: x['record_index'])
    
    if len(records) < 2:
        print("Not enough records found in the file to calculate latencies.")
        return
        
    latencies = []
    
    with open(output_file, 'w') as out:
        out.write(f"Inference Latency Analysis\n")
        out.write(f"Source: {input_file}\n")
        out.write("=" * 50 + "\n\n")
        
        if "monitor_latency" in records[0]:
            out.write(f"Record {records[0]['record_index']:>4} (Initial Step)        | Monitor Latency: {records[0]['monitor_latency']:>6.2f} ms\n")
            
        for i in range(1, len(records)):
            prev = records[i-1]
            curr = records[i]
            
            latency = curr['time_unix'] - prev['time_unix']
            latencies.append(latency)
            
            mon_str = ""
            if "monitor_latency" in curr:
                mon_str = f" | Monitor Latency: {curr['monitor_latency']:>6.2f} ms"
            
            out.write(f"Record {prev['record_index']:>4} -> Record {curr['record_index']:>4}: {latency:.4f} seconds{mon_str}\n")
            
        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        min_latency = min(latencies)
        
        # Find where max latency occurred
        max_idx = latencies.index(max_latency)
        worst_prev = records[max_idx]['record_index']
        worst_curr = records[max_idx+1]['record_index']
        
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
                active_cnt = sum(1 for l in motion_lats if l == "active")
                still_cnt = sum(1 for l in motion_lats if l == "still")
                active_rate = (active_cnt / total_lbl) * 100.0 if total_lbl else 0.0
                still_rate = (still_cnt / total_lbl) * 100.0 if total_lbl else 0.0
                
                out.write("\n" + "=" * 50 + "\n")
                out.write("SUMMARY STATISTICS (Motion Classification & Bypass)\n")
                out.write("=" * 50 + "\n")
                out.write(f"Total Labeled Records:  {total_lbl}\n")
                out.write(f"Active Records:         {active_cnt} ({active_rate:.2f}%)\n")
                out.write(f"Bypassed (Still):       {still_cnt} ({still_rate:.2f}%)\n")
                out.write(f"Active Rate:            {active_rate:.2f}%\n")
                out.write(f"Still Rate (Bypass):    {still_rate:.2f}%\n")

    print(f"✅ Analysis complete! Detailed report saved to: {output_file}")
    print("-" * 40)
    print(f"Total Records Analyzed: {len(records)}")
    print(f"Average Step Latency:   {avg_latency:.4f}s")
    print(f"Worst-Case Step Latency:{max_latency:.4f}s")
    
    if monitor_lats:
        vals = [val for _, val in monitor_lats]
        print("-" * 40)
        print(f"Average Monitor Latency: {sum(vals)/len(vals):.2f} ms")
        print(f"Worst-Case Monitor Lat.: {max(vals):.2f} ms")
        
        motion_lats = [r["motion_label"] for r in records if "motion_label" in r]
        if motion_lats:
            total_lbl = len(motion_lats)
            active_rate = (sum(1 for l in motion_lats if l == "active") / total_lbl) * 100.0 if total_lbl else 0.0
            still_rate = (sum(1 for l in motion_lats if l == "still") / total_lbl) * 100.0 if total_lbl else 0.0
            print("-" * 40)
            print(f"Active Rate:             {active_rate:.2f}%")
            print(f"Still Rate (Bypass):     {still_rate:.2f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze step latencies from openpi JSONL logs.")
    parser.add_argument("input_file", help="Path to the input .jsonl log file")
    parser.add_argument("--output", "-o", default=None, 
                        help="Path to save the output report (default: latency_report_<date>.txt)")
    args = parser.parse_args()
    
    # Ensure input exists
    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Error: Could not find input file '{args.input_file}'")
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
        
    analyze_latencies(args.input_file, output_file)
