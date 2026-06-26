import argparse
import glob
import json
import os
import sys
import time
import traceback

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
    
    # Required argument for IoTAuth Config
    parser.add_argument("--config-file", required=True, help="Path to the IoTAuth entity .config or .json file.")
    
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

    # ---------------------------------------------------------
    # IoTAuth Setup (Done once at startup)
    # ---------------------------------------------------------
    IOTAUTH_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../iotauth/entity/python"))
    if IOTAUTH_DIR not in sys.path:
        sys.path.append(IOTAUTH_DIR)
        
    try:
        from iotauth import IoTAuthContext
        import datetime
        
        abs_config_path = os.path.abspath(args.config_file)
        expected_anchor = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(abs_config_path))))
        
        original_cwd = os.getcwd()
        if expected_anchor == 'example_entities':
            os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(abs_config_path))))
        
        try:
            ctx = IoTAuthContext.from_config(abs_config_path)
            print(f"Initialized IoTAuth Context successfully from {abs_config_path}")
        finally:
            os.chdir(original_cwd)
            
    except Exception as e:
        print(f"Failed to initialize IoTAuth Context: {e}")
        sys.exit(1)

    purpose_payload = {
        "group": "Servers",
        "context": {
            "Number of People": 1,
            "Location": "Classroom",
            "Time of Day": datetime.datetime.now().strftime("%H:%M")
        }
    }

    # ---------------------------------------------------------
    # Monitor State
    # ---------------------------------------------------------
    previous_label = "unknown"
    current_session_key = None
    key_grant_time_ms = None

    # Live processing loop
    for json_line in tail_log_file(target_file):
        result = process_record(json_line)
        
        if result:
            record_idx = result["record_index"]
            metrics = result["motion_metrics"]
            
            proxy_score = metrics["motion_proxy"]
            label = metrics["motion_label"]
            
            print(f"\n[Record {record_idx:^3}] Motion Proxy: {proxy_score:.3f} ({label.upper()})")
            
            # Update time context payload
            purpose_payload["context"]["Time of Day"] = datetime.datetime.now().strftime("%H:%M")
            current_time_ms = int(time.time() * 1000)
            
            # 1. Check if we have a valid key
            is_valid_key = False
            if current_session_key is not None:
                is_valid_key = True
                
                # Check Absolute Validity
                if current_session_key.abs_validity is not None:
                    if current_time_ms >= key_grant_time_ms + current_session_key.abs_validity:
                        is_valid_key = False
                        print("  -> [IoTAuth] Cached key expired (Absolute Validity reached).")
                
                # Check Relative Validity
                if is_valid_key and current_session_key.rel_validity is not None and current_session_key.first_use_ms is not None:
                    if current_time_ms >= current_session_key.first_use_ms + current_session_key.rel_validity:
                        is_valid_key = False
                        print("  -> [IoTAuth] Cached key expired (Relative Validity reached).")
            
            # 2. Decision Logic
            if label == "active":
                if not is_valid_key:
                    # Scenarios 1 & 3: Request new key
                    print(f"  -> [IoTAuth] Requesting new session key for purpose: {purpose_payload}")
                    try:
                        keys = ctx.request_session_keys(purpose=purpose_payload)
                        current_session_key = keys[0]
                        key_grant_time_ms = current_time_ms
                        current_session_key.first_use_ms = current_time_ms
                        
                        print(f"  -> [SUCCESS] Authorized! Received Session Key: {current_session_key.id.hex()}")
                        print(f"  -> [IoTAuth] Abs Validity: {current_session_key.abs_validity} ms | Rel Validity: {current_session_key.rel_validity} ms")
                        print("  -> [Actuator] Forwarding authenticated command to hardware.")
                    except Exception as e:
                        traceback.print_exc()
                        print(f"  -> [ERROR] Failed to get session key: {e}")
                        print("  -> [Actuator] BLOCKED! Cannot forward command without valid session key.")
                else:
                    # Scenario 2: Reuse key
                    if current_session_key.first_use_ms is None:
                        current_session_key.first_use_ms = current_time_ms
                        
                    # Calculate remaining validity for the print statement
                    abs_left = "N/A"
                    if current_session_key.abs_validity is not None:
                        abs_left = f"{((key_grant_time_ms + current_session_key.abs_validity) - current_time_ms):,} ms"
                        
                    rel_left = "N/A"
                    if current_session_key.rel_validity is not None:
                        rel_left = f"{((current_session_key.first_use_ms + current_session_key.rel_validity) - current_time_ms):,} ms"
                        
                    print(f"  -> [IoTAuth] Reusing valid cached Session Key: {current_session_key.id.hex()}")
                    print(f"  -> [IoTAuth] Remaining -> Abs: {abs_left} | Rel: {rel_left}")
                    print("  -> [Actuator] Forwarding authenticated command to hardware.")
            else:
                # Scenario 4: Do nothing
                print("  -> [Monitor] Insignificant motion. Record dropped. No network activity.")
                
            previous_label = label


if __name__ == "__main__":
    main()
