# FAST Token & Continuous Action Interpreter Guide (`interpret_fast_tokens.py`)

`interpret_fast_tokens.py` is the **Offline Information-Theoretic and Kinematic Trajectory Interpreter** for the OpenPI Vision-Language-Action ($\pi_0$-FAST) log outputs. Operating as a standalone analysis utility alongside our secure architecture (see [`secure_architecture_design.md`](secure_architecture_design.md)), it parses raw model output logs (`pi0_fast_tokens*.jsonl`), extracts discrete FAST tokens, computes Shannon entropy proxies without inverse transforms, and inspects decoded continuous 14D ALOHA joint kinematics.

---

## 1. Architectural Role & Dual-Level Inspection

`interpret_fast_tokens.py` bridges theoretical frequency-domain analysis with physical robotics by evaluating both representations present in inference logs:
1. **Discrete Frequency Space (`fast_action_token_ids`)**: Provides fast, decode-free heuristics (`motion_proxy` and `entropy_bits`) based on Byte Pair Encoding (BPE) and Discrete Cosine Transform (DCT) compression behavior.
2. **Continuous Kinematic Space (`decoded_actions`)**: Provides exact physical verification across the 14 active ALOHA joint degrees of freedom (`actions[:, :14]`), identifying which specific joints moved across the action horizon.

---

## 2. Token-Level Output Schema & 9 Core Metrics

When evaluating discrete FAST action tokens (`fast_action_token_ids`), the interpreter produces a JSON summary dictionary containing 9 core metrics:

```json
{
  "token_count": 1024,
  "unique_tokens": 15,
  "unique_ratio": 0.0146,
  "entropy_bits": 1.25,
  "normalized_entropy": 0.125,
  "dominant_token": 257023,
  "dominant_fraction": 0.85,
  "motion_proxy": 0.125,
  "motion_label": "still"
}
```

### Metric Definitions

#### 1. `motion_proxy` (Float, $0.0$ to $1.0$)
An alias for `normalized_entropy`. It serves as a rapid decode-free heuristic determining trajectory complexity across the entire action chunk.
- **Near $0.0$**: Model output is dominated by repeated zero-motion padding tokens. Robot is idle (`still`).
- **Near $1.0$**: Model output exhibits high frequency-domain diversity, commanding complex multi-joint movement (`active`).

#### 2. `motion_label` (String)
A coarse classification of `motion_proxy` against empirical thresholds (`STILL_PROXY_T = 0.35` and `ACTIVE_PROXY_T = 0.60`):
- `"still"`: `motion_proxy < 0.35` (Idle / zero-motion)
- `"low"`: `0.35 <= motion_proxy <= 0.60` (Stabilization or minor adjustments)
- `"active"`: `motion_proxy > 0.60` (Complex multi-joint manipulation)

#### 3. `entropy_bits` (Float)
The unnormalized Shannon entropy of the token distribution $T$:
$$H(T) = -\sum_{v} \hat{p}(v) \log_2 \hat{p}(v)$$
Measured in bits, capturing total information content and vocabulary diversity within the chunk.

#### 4. `normalized_entropy` (Float)
`entropy_bits` divided by the theoretical maximum entropy for $N$ tokens ($\log_2(\text{token\_count})$). This scale-invariant score ($[0, 1]$) enables consistent comparisons even if the action horizon $H$ or dimension $D$ changes.

#### 5. `unique_tokens` (Integer)
The absolute count of distinct vocabulary IDs present in the chunk (`len(set(tokens))`).

#### 6. `unique_ratio` (Float, $0.0$ to $1.0$)
The proportion of distinct tokens relative to total tokens (`unique_tokens / token_count`). Sudden spikes indicate behavioral state transitions.

#### 7. `dominant_token` (Integer)
The PaliGemma vocabulary ID occurring most frequently in the chunk. When stationary, this corresponds to the learned zero-motion DC padding token (`257023`).

#### 8. `dominant_fraction` (Float, $0.0$ to $1.0$)
The percentage of the chunk consisting of `dominant_token`. When idle, `dominant_fraction` typically exceeds $80\%$.

#### 9. `token_count` (Integer)
Total number of extracted FAST action tokens ($N$). For standard ALOHA simulation ($H=32, D=32$), $N = 1024$.

---

## 3. Continuous Action-Level View (`decoded_actions`)

When the `.jsonl` log contains `decoded_actions` (the inverse-DCT and inverse-BPE float array $[H \times D]$), `interpret_fast_tokens.py` extracts the first 14 dimensions (`actions[:, :14]`) corresponding to the physical ALOHA bimanual joints:

| Dimensions | Joint Name Mapping (`ALOHA_JOINT_NAMES`) |
| :---: | :--- |
| `0..5` | Left arm revolute joints (`j1_left` through `j6_left`) |
| `6` | Left gripper aperture (`gripper_left`) |
| `7..12` | Right arm revolute joints (`j1_right` through `j6_right`) |
| `13` | Right gripper aperture (`gripper_right`) |

### Decoded Summary Statistics Schema
The interpreter evaluates per-dimension peak-to-peak movement (`max - min` over all timesteps) against `--joint-move-threshold` (default `0.01` radians/meters) and produces a continuous kinematic summary block:

```json
{
  "shape": [32, 14],
  "move_threshold": 0.01,
  "moving_joints": ["j1_right", "j2_right", "gripper_right"],
  "still_joints": ["j1_left", "j2_left", "j3_left", "j4_left", "j5_left", "j6_left", "gripper_left", "j3_right", "j4_right", "j5_right", "j6_right"],
  "num_moving_joints": 3,
  "chunk_is_static": false,
  "num_moving_timesteps": 28,
  "step_delta_mean": 0.0142,
  "step_delta_max": 0.0381,
  "top_moving_joints": [
    {"name": "j2_right", "dim": 8, "peak_to_peak": 0.142},
    {"name": "j1_right", "dim": 7, "peak_to_peak": 0.089},
    {"name": "gripper_right", "dim": 13, "peak_to_peak": 0.045}
  ]
}
```

- **`moving_joints` / `still_joints`**: Exact lists of ALOHA joints whose peak-to-peak displacement across the chunk exceeded or fell below `move_threshold`.
- **`chunk_is_static`**: Boolean flag (`num_moving_joints == 0`).
- **`step_delta_mean` / `step_delta_max`**: Average and maximum $L_2$ norm velocity ($\|a_t - a_{t-1}\|_2$) across consecutive timesteps in the chunk.
- **`top_moving_joints`**: Ranked list of up to 5 joints exhibiting the greatest peak-to-peak movement in the chunk.

---

## 4. Per-Timestep Kinematic Breakdown (`--per-timestep`)

Passing `--per-timestep` instructs the interpreter to output a granular row for every predicted timestep $t \in [0, H-1]$ within the decoded action chunk:

```json
{
  "timestep": 1,
  "step_delta": 0.0154,
  "num_relevant_joints": 2,
  "relevant_joints": "j1_right|j2_right",
  "j1_left": 0.0,
  "j2_left": -0.12,
  "...",
  "gripper_right": 0.84
}
```
Each row reports exact values for all 14 joints at step $t$, the Euclidean step delta from $t-1$, and `relevant_joints` (any joint changing by $\geq \text{move\_threshold}$ in that single timestep).

---

## 5. CLI Usage & Flags Reference

```bash
python interpret_fast_tokens.py [OPTIONS] [LOG_FILES...]
```

### Command-Line Arguments Table
| Flag | Default | Description |
| :--- | :---: | :--- |
| `LOG_FILES...` | — | One or more `.jsonl` log files to inspect. If omitted, tails or reads recent logs from `data/aloha_sim/token_logs/`. |
| `--joint-move-threshold` | `0.01` | Peak-to-peak displacement threshold for classifying an ALOHA joint as `moving` inside `decoded_actions`. |
| `--per-timestep` | `False` | When present alongside `decoded_actions`, prints the step-by-step joint breakdown for every chunk. |
| `--watch` / `-w` | `False` | Live-tailing mode: monitors specified (or newest discovered) `.jsonl` log file and outputs analysis as records arrive. |
| `--sleep` | `1.0` | Polling interval in seconds when operating in `--watch` mode. |
| `--csv` | `False` | Outputs summary results formatted as comma-separated values (CSV) rather than JSON blocks. |

### Example Invocations

```bash
# Analyze all existing token logs with default thresholds
python interpret_fast_tokens.py data/aloha_sim/token_logs/*.jsonl

# Watch the newest active simulation log in real time with custom joint threshold
LATEST=$(ls -t data/aloha_sim/token_logs/*.jsonl | head -1)
python interpret_fast_tokens.py --watch --joint-move-threshold 0.005 "$LATEST"

# Print granular per-timestep joint movements for a specific run
python interpret_fast_tokens.py --per-timestep data/aloha_sim/token_logs/pi0_fast_tokens_20260719.jsonl
```
