import argparse
import json
import os
import sys
import time

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


def tail_log_file(file_path):
    """
    Tails a log file (similar to `tail -f`) and yields new lines as they are written.
    """
    if not os.path.exists(file_path):
        print(f"Error: The file {file_path} does not exist yet. Waiting for it to be created...")
        while not os.path.exists(file_path):
            time.sleep(1)
            
    print(f"Started monitoring {file_path}...")
    with open(file_path, "r") as f:
        while True:
            line = f.readline()
            if not line:
                # No new line found, wait a fraction of a second and try again
                time.sleep(0.1)
                continue
            
            line = line.strip()
            if line:
                yield line


def process_record(json_str):
    """
    Parses the JSON string, extracts the raw tokens, and applies the FAST token
    interpretation logic to calculate the motion proxy.
    """
    try:
        record = json.loads(json_str)
    except json.JSONDecodeError:
        print("Warning: Received malformed JSON line.")
        return None
        
    raw_tokens = record.get("raw_paligemma_token_ids", [])
    if not raw_tokens:
        return None
        
    # Apply the exact logic from interpret_fast_tokens.py
    trimmed = trim_raw_tokens(raw_tokens)
    fast_tokens = extract_fast_action_tokens(trimmed)
    
    # Calculate the motion proxy (normalized entropy)
    motion_metrics = token_motion_proxy(fast_tokens)
    
    # Attach record index for tracking
    record_index = record.get("record_index", "?")
    
    return {
        "record_index": record_index,
        "motion_metrics": motion_metrics
    }


def main():
    parser = argparse.ArgumentParser(description="Live monitor for OpenPI FAST token logs.")
    parser.add_argument("log_file", help="Path to the pi0_fast_tokens.jsonl log file to monitor.")
    args = parser.parse_args()

    # Continuously read new lines from the log file
    for json_line in tail_log_file(args.log_file):
        result = process_record(json_line)
        
        if result:
            record_idx = result["record_index"]
            metrics = result["motion_metrics"]
            
            proxy_score = metrics["motion_proxy"]
            label = metrics["motion_label"]
            
            print(f"[Record {record_idx:^3}] Processed tokens. Motion Proxy: {proxy_score:.3f} ({label.upper()})")
            
            # ---------------------------------------------------------
            # TODO: IoTAuth Integration goes here!
            # 
            # Context to send to Auth Server:
            # context = {
            #     "motion_proxy": proxy_score,
            #     "motion_label": label
            # }
            # 
            # Example: secure_client.request_session_key(context)
            # ---------------------------------------------------------


if __name__ == "__main__":
    main()
