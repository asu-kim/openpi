import argparse
import glob
import json
import os
import re
import sys
import time
import traceback
import numpy as np
import datetime


OPENPI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "."))
if OPENPI_DIR not in sys.path:
    sys.path.append(OPENPI_DIR)

try:
    from interpret_fast_tokens import joint_motion_analysis
except ImportError as e:
    print("Error: Could not import from interpret_fast_tokens. Make sure you are running this from the openpi root directory.")
    print(e)
    sys.exit(1)


def wait_for_new_file(log_dir):
    print(f"Scanning existing log files in {log_dir}...")
    existing_files = glob.glob(os.path.join(log_dir, "*.jsonl"))
    
    latest_time = 0
    if existing_files:
        latest_file = max(existing_files, key=os.path.getctime)
        latest_time = os.path.getctime(latest_file)
        print(f"  Found {len(existing_files)} logs. Latest: {time.ctime(latest_time)}. Waiting for new...")
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
    if not os.path.exists(file_path):
        print(f"Error: The file {file_path} does not exist yet. Waiting...")
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


class ActionMonitor:
    def __init__(self, config_file: str, motion_threshold: float = None, force_request: bool = None, always_request: bool = None):
        self.config_file = config_file
        env_thresh = os.environ.get("OPENPI_MOTION_THRESHOLD", "").strip()
        self.motion_threshold = motion_threshold if motion_threshold is not None else float(env_thresh) if env_thresh else 0.01
        self.force_request = force_request if force_request is not None else (os.environ.get("FORCE_IOTAUTH_REQUEST", "").strip().lower() in ("1", "true", "yes", "on"))
        self.always_request = always_request if always_request is not None else (os.environ.get("ALWAYS_IOTAUTH_REQUEST", "").strip().lower() in ("1", "true", "yes", "on"))
        self.ctx = None
        
        IOTAUTH_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../iotauth/entity/python"))
        if IOTAUTH_DIR not in sys.path:
            sys.path.append(IOTAUTH_DIR)
            
        try:
            from iotauth import IoTAuthContext
            import datetime
            
            abs_config_path = os.path.abspath(self.config_file)
            expected_anchor = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(abs_config_path))))
            
            original_cwd = os.getcwd()
            if expected_anchor == 'example_entities':
                os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(abs_config_path))))
            
            try:
                self.ctx = IoTAuthContext.from_config(abs_config_path)
                print(f"Initialized IoTAuth Context successfully from {abs_config_path}")
            finally:
                os.chdir(original_cwd)
                
        except Exception as e:
            print(f"Failed to initialize IoTAuth Context: {e}")
            raise

        self.purpose_payload = {
            "group": "Servers",
            "context": {
                "Number of People": 1,
                "Location": "Classroom",
                "Time of Day": ""
            }
        }
        
        self.previous_label = "unknown"
        self.current_session_key = None
        self.key_grant_time_ms = None

    def process_actions(self, actions: np.ndarray, record_idx: int = -1) -> np.ndarray:
        """
        Evaluates continuous float actions and enforces IoTAuth session keys.
        Returns the original actions if allowed, or a static (zero-motion) chunk if blocked.
        """
        start_time = time.perf_counter()
        
        # ALOHA uses first 14 dims. We evaluate motion on these dims if available.
        aloha_actions = actions[:, :14] if actions.shape[-1] >= 14 else actions
        
        jm = joint_motion_analysis(aloha_actions, move_threshold=self.motion_threshold)
        label = "still" if jm["chunk_is_static"] else "active"
        
        print(f"\n[Record {record_idx:^3}] Motion Label: {label.upper()} (Moving Joints: {jm['num_moving_joints']})")
        
        self.purpose_payload["context"]["Time of Day"] = datetime.datetime.now().strftime("%H:%M")
        current_time_ms = int(time.time() * 1000)
        
        is_valid_key = False
        if self.current_session_key is not None and not self.force_request and not self.always_request:
            is_valid_key = True
            if self.current_session_key.abs_validity is not None:
                if current_time_ms >= self.key_grant_time_ms + self.current_session_key.abs_validity:
                    is_valid_key = False
                    print("  -> [IoTAuth] Cached key expired (Absolute Validity reached).")
            
            if is_valid_key and self.current_session_key.rel_validity is not None and self.current_session_key.first_use_ms is not None:
                if current_time_ms >= self.current_session_key.first_use_ms + self.current_session_key.rel_validity:
                    is_valid_key = False
                    print("  -> [IoTAuth] Cached key expired (Relative Validity reached).")
        
        allowed = False
        
        if label == "active" or self.always_request:
            if not is_valid_key:
                print(f"  -> [IoTAuth] Requesting new session key for purpose: {self.purpose_payload}")
                try:
                    keys = self.ctx.request_session_keys(purpose=self.purpose_payload)
                    self.current_session_key = keys[0]
                    self.key_grant_time_ms = current_time_ms
                    self.current_session_key.first_use_ms = current_time_ms
                    
                    print(f"  -> [SUCCESS] Authorized! Received Session Key: {self.current_session_key.id.hex()}")
                    print(f"  -> [IoTAuth] Abs Validity: {self.current_session_key.abs_validity} ms | Rel Validity: {self.current_session_key.rel_validity} ms")
                    print("  -> [Actuator] Forwarding authenticated command to hardware.")
                    allowed = True
                except Exception as e:
                    traceback.print_exc()
                    print(f"  -> [ERROR] Failed to get session key: {e}")
                    print("  -> [Actuator] BLOCKED! Cannot forward command without valid session key.")
                    allowed = False
            else:
                if self.current_session_key.first_use_ms is None:
                    self.current_session_key.first_use_ms = current_time_ms
                    
                abs_left = "N/A"
                if self.current_session_key.abs_validity is not None:
                    abs_left = f"{((self.key_grant_time_ms + self.current_session_key.abs_validity) - current_time_ms):,} ms"
                rel_left = "N/A"
                if self.current_session_key.rel_validity is not None:
                    rel_left = f"{((self.current_session_key.first_use_ms + self.current_session_key.rel_validity) - current_time_ms):,} ms"
                    
                print(f"  -> [IoTAuth] Reusing valid cached Session Key: {self.current_session_key.id.hex()}")
                print(f"  -> [IoTAuth] Remaining -> Abs: {abs_left} | Rel: {rel_left}")
                print("  -> [Actuator] Forwarding authenticated command to hardware.")
                allowed = True
        else:
            print("  -> [Monitor] Insignificant motion. Safe to pass through.")
            allowed = True # Always safe to allow 'still' actions through.

        self.previous_label = label
        
        end_time = time.perf_counter()
        exec_time_ms = (end_time - start_time) * 1000
        print(f"  -> [Monitor] Execution time: {exec_time_ms:.2f} ms")
        self.append_latency_to_log(record_idx, exec_time_ms)
        
        if not allowed:
            # Block motion safely for absolute joint angles:
            # Replicate the first timestep's position across all timesteps to freeze the robot.
            frozen_actions = np.copy(actions)
            frozen_actions[:] = frozen_actions[0]
            return frozen_actions
            
        return actions

    def append_latency_to_log(self, record_idx: int, latency_ms: float):
        if record_idx < 0:
            return
        log_dir = "data/aloha_sim/token_logs"
        if env_log := os.environ.get("OPENPI_FAST_TOKEN_LOG"):
            log_dir = os.path.dirname(env_log)
        if not os.path.exists(log_dir):
            return
        existing_files = glob.glob(os.path.join(log_dir, "*.jsonl"))
        if not existing_files:
            return
        latest_file = max(existing_files, key=os.path.getctime)
        
        try:
            with open(latest_file, "r") as f:
                lines = f.readlines()
            
            updated = False
            for i in range(len(lines) - 1, -1, -1):
                line = lines[i]
                if re.search(rf'"record_index":\s*{record_idx}\b', line):
                    if '"monitor_latency"' in line:
                        lines[i] = re.sub(r'"monitor_latency":\s*[\d\.]+\s*,', f'"monitor_latency": {round(latency_ms, 2)},', line, count=1)
                    else:
                        pattern = r'("time_iso":\s*"[^"]+"\s*,)'
                        lines[i] = re.sub(pattern, rf'\1 "monitor_latency": {round(latency_ms, 2)},', line, count=1)
                    updated = True
                    break
            
            if updated:
                with open(latest_file, "w") as f:
                    f.writelines(lines)
        except Exception as e:
            print(f"  -> [Monitor] Warning: Could not update jsonl log: {e}")

def main():
    parser = argparse.ArgumentParser(description="Live monitor for OpenPI continuous actions.")
    parser.add_argument("--config-file", required=True, help="Path to the IoTAuth entity config file.")
    parser.add_argument("--log-file", help="Specific pi0_fast_tokens.jsonl file to read offline.")
    env_thresh = os.environ.get("OPENPI_MOTION_THRESHOLD", "").strip()
    default_thresh = float(env_thresh) if env_thresh else 0.01
    parser.add_argument("--motion-threshold", type=float, default=default_thresh, help="Motion threshold for joint variation (default: 0.01 or OPENPI_MOTION_THRESHOLD env var).")
    parser.add_argument("--force-iotauth-request", action="store_true", default=(os.environ.get("FORCE_IOTAUTH_REQUEST", "").strip().lower() in ("1", "true", "yes", "on")), help="Disable caching and send a session key request for every ACTIVE record.")
    parser.add_argument("--always-iotauth-request", action="store_true", default=(os.environ.get("ALWAYS_IOTAUTH_REQUEST", "").strip().lower() in ("1", "true", "yes", "on")), help="Send a session key request for EVERY record regardless of the motion threshold value.")
    args = parser.parse_args()

    target_file = args.log_file
    if not target_file:
        default_dir = "data/aloha_sim/token_logs/"
        if not os.path.exists(default_dir):
            os.makedirs(default_dir, exist_ok=True)
        target_file = wait_for_new_file(default_dir)

    monitor = ActionMonitor(args.config_file, motion_threshold=args.motion_threshold, force_request=args.force_iotauth_request, always_request=args.always_iotauth_request)

    for json_line in tail_log_file(target_file):
        try:
            record = json.loads(json_line)
        except json.JSONDecodeError:
            continue
            
        decoded = record.get("decoded_actions")
        if not decoded:
            continue
            
        actions_arr = np.array(decoded, dtype=np.float32)
        idx = record.get("record_index", -1)
        
        # Test standalone processing
        monitor.process_actions(actions_arr, record_idx=idx)

if __name__ == "__main__":
    main()
