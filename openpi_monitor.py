import argparse
import glob
import json
import os
import sys
import time

OPENPI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
if OPENPI_DIR not in sys.path:
    sys.path.append(OPENPI_DIR)

try:
    from interpret_fast_tokens import (
        trim_raw_tokens,
        extract_fast_action_tokens,
        token_motion_proxy
    )
except ImportError as e:
    print("Error: Could not import from interpret_fast_tokens. Make sure you are running this from the openpi root directory.")
    print(e)
    sys.exit(1)


def wait_for_new_file(log_dir):
    """
    Records the timestamp of the newest existing log file and waits for a newer one.
    """
    print(f"Scanning existing log files in {log_dir}...")
    existing_files = glob.glob(os.path.join(log_dir, "*.jsonl"))
    
    latest_time = 0
    if existing_files:
        latest_file = max(existing_files, key=os.path.getctime)
        latest_time = os.path.getctime(latest_file)
        print(f"  Found {len(existing_files)} existing logs. The latest is from {time.ctime(latest_time)}. Waiting for a newer one...")
    else:
        print("  No existing logs found. Waiting for the first one...")
            
    while True:
        current_files = glob.glob(os.path.join(log_dir, "*.jsonl"))
        if current_files:
            newest_current = max(current_files, key=os.path.getctime)
            if os.path.getctime(newest_current) > latest_time:
                print(f"Detected new log file: {newest_current}")
                return newest_current
            
        time.sleep(0.1)


def tail_log_file(file_path):
    """ 
    Tails a log file (similar to `tail -f`) and yields new lines as they are written.
    """
    if not os.path.exists(file_path):
        print(f"Error: The file {file_path} does not exist yet. Waiting for it to be created...")
        while not os.path.exists(file_path):
            time.sleep(1)
            
    print(f"Started live monitoring {file_path}...")
    with open(file_path, "r") as f:
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1)
                continue
            
            line = line.strip()
            if line:
                yield line


def process_record(json_str):
    try:
        record = json.loads(json_str)
    except json.JSONDecodeError:
        return None
        
    raw_tokens = record.get("raw_paligemma_token_ids", [])
    if not raw_tokens:
        return None
        
    trimmed = trim_raw_tokens(raw_tokens)
    fast_tokens = extract_fast_action_tokens(trimmed)
    motion_metrics = token_motion_proxy(fast_tokens)
    
    return {
        "record_index": record.get("record_index", "?"),
        "motion_metrics": motion_metrics
    }


def main():
    parser = argparse.ArgumentParser(description="Live monitor for OpenPI FAST token logs.")
    
    # Optional argument for offline testing
    parser.add_argument("--log-file", help="Specific existing pi0_fast_tokens.jsonl file to read offline.")
    
    args = parser.parse_args()

    # Determine which file to tail based on arguments
    if args.log_file:
        target_file = args.log_file
    else:
        # Default behavior: monitor the default token logs directory
        default_dir = "data/aloha_sim/token_logs/"
        if not os.path.exists(default_dir):
            os.makedirs(default_dir, exist_ok=True)
        target_file = wait_for_new_file(default_dir)

    # Live processing loop
    for json_line in tail_log_file(target_file):
        result = process_record(json_line)
        
        if result:
            record_idx = result["record_index"]
            metrics = result["motion_metrics"]
            
            proxy_score = metrics["motion_proxy"]
            label = metrics["motion_label"]
            
            print(f"[Record {record_idx:^3}] Motion Proxy: {proxy_score:.3f} ({label.upper()})")
            
            # ---------------------------------------------------------
            # We will add IoTAuth logic here ONLY after verification!
            # ---------------------------------------------------------


if __name__ == "__main__":
    main()
