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


OPENPI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if OPENPI_DIR not in sys.path:
    sys.path.append(OPENPI_DIR)
OPENPI_CLIENT_DIR = os.path.join(OPENPI_DIR, "packages/openpi-client/src")
if OPENPI_CLIENT_DIR not in sys.path:
    sys.path.append(OPENPI_CLIENT_DIR)

from openpi_client.action_motion_classifier import (  # noqa: E402
    ALOHA_ACTION_LOWER_LIMITS,
    ALOHA_ACTION_UPPER_LIMITS,
    SIGA,
    classify_action_motion,
)

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
    def __init__(
        self,
        config_file: str,
        motion_threshold: float = None,
        force_request: bool = None,
        always_request: bool = None,
        execution_horizon: int = 10,
        secure_actuator=None,
        insiga_actuator=None,
    ):
        self.config_file = config_file
        env_thresh = os.environ.get("OPENPI_MOTION_THRESHOLD", "").strip()
        self.motion_threshold = motion_threshold if motion_threshold is not None else float(env_thresh) if env_thresh else 0.01
        self.force_request = force_request if force_request is not None else (os.environ.get("FORCE_IOTAUTH_REQUEST", "").strip().lower() in ("1", "true", "yes", "on"))
        self.always_request = always_request if always_request is not None else (os.environ.get("ALWAYS_IOTAUTH_REQUEST", "").strip().lower() in ("1", "true", "yes", "on"))
        if execution_horizon < 1:
            raise ValueError("execution_horizon must be positive")
        self.execution_horizon = execution_horizon
        self.ctx = None
        
        IOTAUTH_DIR = os.path.abspath(os.path.join(OPENPI_DIR, "../iotauth/entity/python"))
        if IOTAUTH_DIR not in sys.path:
            sys.path.append(IOTAUTH_DIR)
            
        try:
            from iotauth import IoTAuthContext
            
            abs_config_path = os.path.abspath(self.config_file)
            try:
                self.ctx = IoTAuthContext.from_config(abs_config_path)
                print(f"Initialized IoTAuth Context successfully from {abs_config_path}")
            except Exception as first_err:
                config_dir = os.path.dirname(abs_config_path)
                target_dir = os.path.dirname(os.path.dirname(os.path.dirname(abs_config_path)))
                for d in [config_dir, target_dir]:
                    if os.path.exists(d) and os.path.isdir(d):
                        original_cwd = os.getcwd()
                        os.chdir(d)
                        try:
                            self.ctx = IoTAuthContext.from_config(abs_config_path)
                            print(f"Initialized IoTAuth Context successfully from {abs_config_path} (after chdir to {d})")
                            break
                        except Exception:
                            pass
                        finally:
                            os.chdir(original_cwd)
                if self.ctx is None:
                    raise first_err
                
        except Exception as e:
            print(f"Failed to initialize IoTAuth Context: {e}")
            raise

        context_people = os.environ.get("IOTAUTH_CONTEXT_PEOPLE", "1").strip()
        try:
            context_people = int(context_people)
        except ValueError as exc:
            raise ValueError("IOTAUTH_CONTEXT_PEOPLE must be an integer") from exc
        self.fixed_context_time = os.environ.get("IOTAUTH_CONTEXT_TIME", "").strip() or None
        self.purpose_payload = {
            "group": "Servers",
            "context": {
                "Number of People": context_people,
                "Location": os.environ.get("IOTAUTH_CONTEXT_LOCATION", "Classroom").strip(),
                "Time of Day": ""
            }
        }
        
        self.previous_label = "unknown"
        self.current_session_key = None
        self.key_grant_time_ms = None
        self.last_valid_action = None
        self.action_record_id = 0
        self.current_delivery_record_id = None

        self.secure_actuator = secure_actuator
        self.insiga_actuator = insiga_actuator
        secure_enabled = os.environ.get("SECURE_ACTUATOR_ENABLED", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if self.secure_actuator is None and secure_enabled:
            from scripts.lamps_2026.insiga_actuator_client import InsigaActuatorClient
            from scripts.lamps_2026.secure_actuator_client import SecureActuatorClient

            secure_host = os.environ.get("SECURE_ACTUATOR_HOST") or None
            secure_port_raw = os.environ.get("SECURE_ACTUATOR_PORT", "").strip()
            secure_port = int(secure_port_raw) if secure_port_raw else None
            if (secure_host is None) != (secure_port is None):
                raise ValueError(
                    "SECURE_ACTUATOR_HOST and SECURE_ACTUATOR_PORT must be configured together"
                )
            secure_timeout = float(os.environ.get("SECURE_ACTUATOR_TIMEOUT", "5.0"))
            self.secure_actuator = SecureActuatorClient(
                self.ctx,
                host=secure_host,
                port=secure_port,
                timeout=secure_timeout,
            )
            insiga_host = os.environ.get("INSIGA_ACTUATOR_HOST", secure_host or "127.0.0.1")
            insiga_port = int(os.environ.get("INSIGA_ACTUATOR_PORT", "21102"))
            self.insiga_actuator = InsigaActuatorClient(
                host=insiga_host,
                port=insiga_port,
                timeout=secure_timeout,
            )
            print(
                "Initialized actuator transports: "
                f"SIGA={secure_host or 'configured target'}:{secure_port or 'configured port'}, "
                f"INSIGA={insiga_host}:{insiga_port}"
            )

    def process_actions(
        self,
        actions: np.ndarray,
        record_idx: int = -1,
        current_state: np.ndarray | None = None,
        observation_timestamp_ms: int | None = None,
    ) -> np.ndarray:
        """
        Evaluates continuous float actions and enforces IoTAuth session keys.
        Returns the original actions if allowed, or a static (zero-motion) chunk if blocked.
        """
        monitor_start_ms = int(time.time() * 1000)
        start_time = time.perf_counter()
        security_metrics = {}
        self.current_delivery_record_id = None

        actions = np.asarray(actions, dtype=np.float32)
        if actions.ndim != 2 or actions.shape[0] < 1 or actions.shape[1] < 1:
            raise ValueError(f"actions must be a non-empty 2D array, got {actions.shape}")

        # ALOHA uses first 14 dims. We evaluate motion on these dims if available.
        aloha_actions = actions[:, :14] if actions.shape[-1] >= 14 else actions
        if current_state is not None:
            reference_action = np.asarray(current_state, dtype=np.float32).reshape(-1)
        elif self.last_valid_action is not None:
            reference_action = np.asarray(self.last_valid_action, dtype=np.float32).reshape(-1)
        else:
            reference_action = aloha_actions[0]

        # Clamp simulated observations and action coordinates to ALOHA limits to absorb physics/numerical overshoot
        lower = ALOHA_ACTION_LOWER_LIMITS[: aloha_actions.shape[1]]
        upper = ALOHA_ACTION_UPPER_LIMITS[: aloha_actions.shape[1]]
        aloha_actions = np.clip(aloha_actions, lower, upper)
        if actions.shape[-1] >= 14:
            actions[:, :14] = aloha_actions
        else:
            actions = aloha_actions
        reference_action = np.clip(reference_action[: aloha_actions.shape[1]], lower, upper)

        motion = classify_action_motion(
            aloha_actions,
            reference_action=reference_action,
            execution_horizon=self.execution_horizon,
            threshold=self.motion_threshold,
        )
        executable_steps = motion.execution_horizon
        label = motion.label

        transport_enabled = self.secure_actuator is not None or self.insiga_actuator is not None
        if transport_enabled:
            self.current_delivery_record_id = self.action_record_id
            self.action_record_id += 1
        
        val_period = os.environ.get("TEST_VALIDITY_PERIOD", "unknown")
        run_iter = os.environ.get("TEST_RUN_ITERATION", "unknown")
        total_runs = os.environ.get("TEST_TOTAL_RUNS", "unknown")
        if val_period == "unknown" and self.config_file:
            import re
            m = re.search(r'val[_]?(\d+)', self.config_file, re.IGNORECASE)
            if m:
                val_period = m.group(1)
        
        print("\n" + "="*75)
        print(f" [METADATA] Validity Test: {val_period}s | Iteration/Run: {run_iter}/{total_runs} | Record Index: {record_idx}")
        print(
            f" [MOTION]   Label: {label.upper()} | "
            f"Normalized intra PTP: {motion.intra_score:.4f} | "
            f"Normalized inter delta: {motion.inter_score:.4f} | "
            f"Combined: {motion.combined_score:.4f} | "
            f"Significant Joints: {motion.num_significant_dimensions}/{aloha_actions.shape[-1]} | "
            f"Threshold: {self.motion_threshold}"
        )
        security_metrics.update(
            {
                "motion_intra_ptp": motion.intra_score,
                "motion_inter_delta": motion.inter_score,
                "motion_combined_score": motion.combined_score,
            }
        )
        print("-" * 75)
        
        self.purpose_payload["context"]["Time of Day"] = (
            self.fixed_context_time or datetime.datetime.now().strftime("%H:%M")
        )
        current_time_ms = int(time.time() * 1000)
        
        is_valid_key = False
        if self.current_session_key is not None and not self.force_request and not self.always_request:
            is_valid_key = True
            if self.current_session_key.abs_validity is not None:
                if current_time_ms >= self.current_session_key.abs_validity:
                    is_valid_key = False
                    print(
                        "  -> [IoTAuth] Cached key EXPIRED "
                        f"(expiration timestamp reached: {self.current_session_key.abs_validity:,} ms)."
                    )
            
            if is_valid_key and self.current_session_key.rel_validity is not None and self.current_session_key.first_use_ms is not None:
                elapsed_rel = current_time_ms - self.current_session_key.first_use_ms
                if current_time_ms >= self.current_session_key.first_use_ms + self.current_session_key.rel_validity:
                    is_valid_key = False
                    print(f"  -> [IoTAuth] Cached key EXPIRED (Relative Validity reached: {elapsed_rel:,} ms >= {self.current_session_key.rel_validity:,} ms since first use at {self.current_session_key.first_use_ms}).")
        
        allowed = False
        
        requires_auth = label == SIGA or self.always_request

        if requires_auth:
            if not is_valid_key:
                print(f"  -> [IoTAuth] Requesting new session key for purpose: {self.purpose_payload}")
                auth_request_start = time.perf_counter()
                try:
                    keys = self.ctx.request_session_keys(purpose=self.purpose_payload)
                    security_metrics["auth_request_ms"] = (
                        time.perf_counter() - auth_request_start
                    ) * 1000
                    self.current_session_key = keys[0]
                    self.key_grant_time_ms = current_time_ms
                    self.current_session_key.first_use_ms = current_time_ms
                    
                    print(f"  -> [SUCCESS] Authorized! Received Session Key: {self.current_session_key.id.hex()}")
                    print(
                        "  -> [IoTAuth] Key Metadata -> "
                        f"Grant/FirstUse: {current_time_ms} ms | "
                        f"Absolute Expiration: {self.current_session_key.abs_validity} ms | "
                        f"Relative Validity: {self.current_session_key.rel_validity} ms"
                    )
                    allowed = True
                except Exception as e:
                    security_metrics["auth_request_ms"] = (
                        time.perf_counter() - auth_request_start
                    ) * 1000
                    traceback.print_exc()
                    print(f"  -> [ERROR] Failed to get session key: {e}")
                    print("  -> [Actuator] BLOCKED! Cannot forward command without valid session key.")
                    allowed = False
            else:
                if self.current_session_key.first_use_ms is None:
                    self.current_session_key.first_use_ms = current_time_ms
                    
                abs_left = "N/A"
                if self.current_session_key.abs_validity is not None:
                    abs_left = f"{(self.current_session_key.abs_validity - current_time_ms):,} ms"
                rel_left = "N/A"
                elapsed_rel = "N/A"
                if self.current_session_key.rel_validity is not None:
                    rel_left = f"{((self.current_session_key.first_use_ms + self.current_session_key.rel_validity) - current_time_ms):,} ms"
                    elapsed_rel = f"{(current_time_ms - self.current_session_key.first_use_ms):,} ms"
                    
                print(f"  -> [IoTAuth] Reusing valid cached Session Key: {self.current_session_key.id.hex()}")
                print(f"  -> [IoTAuth] Status -> Elapsed Rel: {elapsed_rel} | Remaining Rel: {rel_left} | Remaining Abs: {abs_left}")
                allowed = True
        else:
            print("  -> [Monitor] INSIGA: forwarding through the unencrypted action path.")
            allowed = True

        if allowed and transport_enabled:
            if observation_timestamp_ms is None:
                observation_timestamp_ms = current_time_ms
            record_id = self.current_delivery_record_id
            try:
                if requires_auth:
                    if self.secure_actuator is None:
                        raise RuntimeError("SIGA secure actuator client is not configured")
                    transport_timing = self.secure_actuator.send_actions(
                        actions,
                        session_key=self.current_session_key,
                        record_id=record_id,
                        observation_id=max(record_idx, 0),
                        observation_timestamp_ms=observation_timestamp_ms,
                        monitor_start_ms=monitor_start_ms,
                        execution_horizon=executable_steps,
                        motion_label=label,
                    )
                    print(
                        "  -> [SIGA] Encrypted chunk submitted "
                        f"record={record_id} send={transport_timing['secure_send_ms']:.2f} ms"
                    )
                else:
                    if self.insiga_actuator is None:
                        raise RuntimeError("INSIGA plaintext actuator client is not configured")
                    transport_timing = self.insiga_actuator.send_actions(
                        actions,
                        record_id=record_id,
                        observation_id=max(record_idx, 0),
                        observation_timestamp_ms=observation_timestamp_ms,
                        monitor_start_ms=monitor_start_ms,
                        execution_horizon=executable_steps,
                    )
                    print(
                        "  -> [INSIGA] Unencrypted serialized chunk submitted "
                        f"record={record_id} send={transport_timing['insiga_send_ms']:.2f} ms"
                    )
                security_metrics.update(transport_timing)
            except Exception as e:
                traceback.print_exc()
                print(f"  -> [ActuatorTransport] Delivery failed: {e}")
                allowed = False

        if allowed:
            executed_index = min(executable_steps, actions.shape[0]) - 1
            self.last_valid_action = np.copy(actions[executed_index])
            if self.secure_actuator is None:
                print("  -> [Actuator] Authorization gate passed; returning ordinary action chunk.")
            else:
                print(f"  -> [Actuator] {label.upper()} chunk submitted for ordered execution.")

        self.previous_label = label
        
        end_time = time.perf_counter()
        exec_time_ms = (end_time - start_time) * 1000
        print(f"  -> [Monitor] Execution time: {exec_time_ms:.2f} ms")
        print("="*75)
        self.append_latency_to_log(
            record_idx,
            exec_time_ms,
            motion_label=label,
            security_metrics=security_metrics,
        )
        
        if not allowed:
            # Compatibility fallback for the existing in-process Gym runtime. In the
            # fully isolated actuator process this hold is generated locally instead.
            frozen_actions = np.copy(actions)
            if self.last_valid_action is not None and self.last_valid_action.shape == actions[0].shape:
                hold_action = self.last_valid_action
            else:
                hold_action = np.copy(actions[0])
                if current_state is not None:
                    state = np.asarray(current_state, dtype=np.float32).reshape(-1)
                    dims = min(state.size, hold_action.size)
                    hold_action[:dims] = state[:dims]
            frozen_actions[:] = hold_action
            return frozen_actions
            
        return actions

    def append_latency_to_log(
        self,
        record_idx: int,
        latency_ms: float,
        motion_label: str = None,
        security_metrics: dict[str, float] | None = None,
    ):
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
                    record = json.loads(line)
                    record["monitor_latency"] = round(latency_ms, 2)
                    if motion_label:
                        record["motion_label"] = motion_label
                    for name, value in (security_metrics or {}).items():
                        record[name] = round(float(value), 2)
                    lines[i] = json.dumps(record, separators=(",", ":")) + "\n"
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
    parser.add_argument(
        "--motion-threshold",
        type=float,
        default=default_thresh,
        help="Normalized motion threshold in [0, 1] (default: 0.01 or OPENPI_MOTION_THRESHOLD env var).",
    )
    parser.add_argument("--execution-horizon", type=int, default=10, help="Number of leading actions the actuator executes (default: 10).")
    parser.add_argument("--force-iotauth-request", action="store_true", default=(os.environ.get("FORCE_IOTAUTH_REQUEST", "").strip().lower() in ("1", "true", "yes", "on")), help="Disable caching and send a session key request for every SIGA record.")
    parser.add_argument("--always-iotauth-request", action="store_true", default=(os.environ.get("ALWAYS_IOTAUTH_REQUEST", "").strip().lower() in ("1", "true", "yes", "on")), help="Send a session key request for EVERY record regardless of the motion threshold value.")
    args = parser.parse_args()

    target_file = args.log_file
    if not target_file:
        default_dir = "data/aloha_sim/token_logs/"
        if not os.path.exists(default_dir):
            os.makedirs(default_dir, exist_ok=True)
        target_file = wait_for_new_file(default_dir)

    monitor = ActionMonitor(
        args.config_file,
        motion_threshold=args.motion_threshold,
        force_request=args.force_iotauth_request,
        always_request=args.always_iotauth_request,
        execution_horizon=args.execution_horizon,
    )

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
