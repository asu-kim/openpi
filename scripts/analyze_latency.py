#!/usr/bin/env python3
import json
import argparse
from pathlib import Path

def analyze_latencies(input_file: str, output_file: str):
    records = []
    
    # Read the JSONL file
    with open(input_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
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
        
        for i in range(1, len(records)):
            prev = records[i-1]
            curr = records[i]
            
            latency = curr['time_unix'] - prev['time_unix']
            latencies.append(latency)
            
            out.write(f"Record {prev['record_index']:>4} -> Record {curr['record_index']:>4}: {latency:.4f} seconds\n")
            
        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        min_latency = min(latencies)
        
        # Find where max latency occurred
        max_idx = latencies.index(max_latency)
        worst_prev = records[max_idx]['record_index']
        worst_curr = records[max_idx+1]['record_index']
        
        out.write("\n" + "=" * 50 + "\n")
        out.write("SUMMARY STATISTICS\n")
        out.write("=" * 50 + "\n")
        out.write(f"Total Records Analyzed: {len(records)}\n")
        out.write(f"Average Latency:        {avg_latency:.4f} seconds\n")
        out.write(f"Best-Case Latency:      {min_latency:.4f} seconds\n")
        out.write(f"Worst-Case Latency:     {max_latency:.4f} seconds\n")
        out.write(f"  └─ Occurred between Record {worst_prev} and Record {worst_curr}\n")

    print(f"✅ Analysis complete! Detailed report saved to: {output_file}")
    print("-" * 40)
    print(f"Total Records Analyzed: {len(records)}")
    print(f"Average Latency:        {avg_latency:.4f}s")
    print(f"Worst-Case Latency:     {max_latency:.4f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze step latencies from openpi JSONL logs.")
    parser.add_argument("input_file", help="Path to the input .jsonl log file")
    parser.add_argument("--output", "-o", default="latency_report.txt", 
                        help="Path to save the output report (default: latency_report.txt)")
    args = parser.parse_args()
    
    # Ensure input exists
    if not Path(args.input_file).exists():
        print(f"Error: Could not find input file '{args.input_file}'")
        exit(1)
        
    analyze_latencies(args.input_file, args.output)
