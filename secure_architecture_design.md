# Secure Architecture Design: Joint Motion Analysis Gatekeeper & IoTAuth Actuator Mediation for OpenPI / ALOHA

---

## 1. Introduction

This document describes the design of a real-time monitoring and secure actuator mediation system for the OpenPI Vision-Language-Action ($\pi_0$-FAST) model controlling an ALOHA bimanual robot (both physical hardware and MuJoCo simulation). 

To ensure strict physical safety and cryptographic integrity without degrading high-frequency control loop performance, the secure architecture replaces static or unmediated execution with a **Runtime Kinematic Gatekeeper (`openpi_monitor.py`)** paired with a **Dual-Channel Actuator Gateway (`secure_actuator_gateway.py`)**:

1. **Joint Motion Analysis Gatekeeper (`openpi_monitor.py`)**: Intercepts decoded continuous action chunks (`actions[:, :14]`) output by the VLA policy and evaluates physical movement across all 14 ALOHA degrees of freedom over an execution horizon ($H=10$). Using **intra-chunk peak-to-peak range (`intra_score`)** and **inter-chunk displacement (`inter_score`)**, it classifies each action chunk as either significant or insignificant:
   - **Significant Actions (`SIGA`)**: Intended multi-joint movements that exceed the safety threshold (`combined_score >= threshold`). These actions dynamically request or reuse cryptographic session keys from the IoTAuth authorization framework (`SST`) and are transmitted over an encrypted, authenticated TCP channel (`port 21100`).
   - **Insignificant Actions (`INSIGA`)**: Zero-motion padding or sub-threshold micro-adjustments (`combined_score < threshold`). These actions are routed over a trusted local plaintext IPC channel (`port 21102`), eliminating cryptographic overhead when the robot is idle or holding position.
2. **Dual-Channel Actuator Gateway (`secure_actuator_gateway.py`)**: Wraps the physical servo controllers or ALOHA MuJoCo simulation engine. It validates decrypted `SIGA` payloads on TCP `21100` and plaintext `INSIGA` payloads on TCP `21102`, enforcing strict monotonically increasing global sequence ordering (`action_record_id`) across both channels to prevent replay attacks, skipped actions, or out-of-order execution while recording end-to-end latency (`monitor_actuator_ms`).

---

## 2. Definitions and Terminology

### 2.1 Vision-Language-Action (VLA) Model ($\pi_0$-FAST)
A neural network that accepts visual frames, natural language instructions, and **proprioceptive state** (the robot's current joint angles and positions) as inputs, and produces continuous robot control commands as output [1][2]. In $\pi_0$-FAST, a PaliGemma backbone integrates with a dedicated action expert module that decodes compressed trajectory representations into continuous action matrices [3][4].

### 2.2 Action Chunk $[H \times D]$
A fixed-size matrix of continuous-valued motor commands output by a single forward pass of the VLA model. Its shape is $[H \times D]$, where $H$ is the action horizon and $D$ is the action dimension. For the ALOHA configuration used in this system, $H = 32$ and $D = 32$, yielding a matrix of 1,024 continuous values per inference step [3][9].

### 2.3 Action Horizon ($H$) and Execution Horizon ($H_{exec}$)
- **Action Horizon ($H=32$)**: The total number of future control timesteps predicted simultaneously in one forward pass [9].
- **Execution Horizon ($H_{exec}=10$)**: Because inference is continuously re-triggered and action chunks overlap, only the first $10$ timesteps (`actions[:10, :14]`) are typically executed before a new chunk arrives. The monitor specifically evaluates physical kinematics across this active execution window.

### 2.4 Action Dimension ($D$) & ALOHA Joint Mapping
The number of independent control degrees of freedom at each timestep. For the ALOHA bimanual robot platform [9], only the first 14 of the 32 dimensions correspond to physical actuators:

| Dimensions | Joint Name | Description |
| :---: | :--- | :--- |
| `0–5` | `j1_left` .. `j6_left` | Left arm revolute joint angles (6 joints) |
| `6` | `gripper_left` | Left gripper aperture |
| `7–12` | `j1_right` .. `j6_right` | Right arm revolute joint angles (6 joints) |
| `13` | `gripper_right` | Right gripper aperture |

Dimensions 14–31 serve as internal model padding and are excluded from kinematic evaluation and physical execution.

### 2.5 Record & Observation
An **observation** is the synchronized input snapshot (camera frames + 14D proprioceptive state vector) that triggers one inference cycle. A **record** is the corresponding structured output (`decoded_actions`, timestamps, sequence ID) produced by the policy. There is a strict 1-to-1 correspondence: each observation yields exactly one record every ~1.2–1.5 seconds.

### 2.6 IoTAuth Session Key
In the IoTAuth framework [11], a **session key** ($K$) is a symmetric cryptographic key distributed by a trusted authorization server (`Auth` / `SST`) to authenticated entities for secure peer-to-peer communication. Each session key contains an 8-byte unique `id`, a symmetric `cipher_key`, and two temporal lifecycle bounds:
- **Absolute validity (`abs_validity` / $V_{abs}$)**: Total duration (in milliseconds) the key is valid from issuance.
- **Relative validity (`rel_validity` / $V_{rel}$)**: Duration (in milliseconds) the key remains valid after its **first use** for encryption.

---

## 3. Joint Motion Analysis & Kinematic Gatekeeper (`openpi_monitor.py`)

To accurately distinguish genuine manipulation commands from idle padding without relying on indirect token proxies, `openpi_monitor.py` performs direct **Joint Motion Analysis** on the decoded continuous action array (`actions[:10, :14]`).

### 3.1 Kinematic Evaluation Metrics
For each incoming action chunk $A \in \mathbb{R}^{32 \times 32}$, the gatekeeper extracts the active 14D joint window across the execution horizon ($A_{exec} = A[:10, :14]$) and computes two complementary physical metrics relative to each joint's maximum operating range ($R_j$):

1. **Intra-Chunk Peak-to-Peak Range (`intra_score`)**:
   Measures the maximum normalized movement span commanded across any individual joint *within* the upcoming execution window:
   $$\text{intra\_score} = \max_{j \in [0, 13]} \left( \frac{\max_{t \in [0, 9]} A_{exec}[t, j] - \min_{t \in [0, 9]} A_{exec}[t, j]}{R_j} \right)$$
2. **Inter-Chunk Displacement (`inter_score`)**:
   Measures the normalized step change between the robot's current physical state vector ($q_{curr} \in \mathbb{R}^{14}$ or the last executed target) and the very first timestep of the new incoming chunk ($A_{exec}[0, j]$):
   $$\text{inter\_score} = \max_{j \in [0, 13]} \left( \frac{|A_{exec}[0, j] - q_{curr}[j]|}{R_j} \right)$$

### 3.2 Gatekeeper Motion Classification (`classify_action_motion()`)
The gatekeeper computes the combined kinematic score across all joints:
$$\text{combined\_score} = \max(\text{intra\_score}, \text{inter\_score})$$

This score is evaluated against the configured safety threshold (`threshold`, default `0.01` or set via `--motion-threshold` / `OPENPI_MOTION_THRESHOLD`):
- **`SIGA` (Significant Action)**: $\text{combined\_score} \geq \text{threshold}$. The policy intends to move one or more ALOHA arm joints or grippers by at least $1\%$ of their full physical operating range.
- **`INSIGA` (Insignificant Action)**: $\text{combined\_score} < \text{threshold}$. The commanded trajectory is effectively static, representing idle padding or negligible stabilization micro-adjustments.

---

## 4. System Security Model & Dual-Channel Actuator Gateway

In the live control loop, `openpi_monitor.py` sits between the VLA policy wrapper and the physical or simulated ALOHA actuator (`secure_actuator_gateway.py`), mediating every trajectory before it reaches hardware.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        VLA Policy Execution                            │
│                  (Continuous 14D ALOHA Action Chunk)                  │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   openpi_monitor.py (Gatekeeper)                       │
│  1. Joint Motion Analysis: intra_score & inter_score over H_exec=10    │
│  2. Classify: combined_score >= threshold  ──► SIGA vs INSIGA          │
│  3. Increment global action_record_id sequence                         │
└────────┬───────────────────────────────────────────────────────┬───────┘
         │ (If SIGA: Requires IoTAuth Key)                       │ (If INSIGA: Plaintext)
         ▼                                                       ▼
┌─────────────────────────────────┐             ┌─────────────────────────────────┐
│     IoTAuth Session Renewal     │             │       InsigaActuatorClient      │
│  • Check abs_validity/rel_valid │             │  • Zero crypto overhead         │
│  • If expired/missing: Request  │             │  • TCP Port 21102               │
└────────┬────────────────────────┘             └────────────────┬────────────────┘
         │ Authenticated Key K                                   │
         ▼                                                       │
┌─────────────────────────────────┐                              │
│       SecureActuatorClient      │                              │
│  • Encrypt & Authenticate chunk │                              │
│  • TCP Port 21100               │                              │
└────────┬────────────────────────┘                              │
         │                                                       │
         ▼                                                       ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   secure_actuator_gateway.py                           │
│  • Port 21100: accept_secure() decrypts using Auth key ID              │
│  • Port 21102: accept_insiga() receives plaintext local IPC commands   │
│  • Enforces global sequence ordering across BOTH channels              │
│  • Fails closed on missing, duplicate, or out-of-order record IDs    │
└────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Security & Sequence Invariants
1. **Mandatory Cryptographic Authentication for Significant Motion (`SIGA`)**: No `SIGA` command can reach hardware without a valid, unexpired IoTAuth session key ($V_{abs}$ and $V_{rel}$). If authentication fails or the cached key expires, the monitor blocks the action and repeats the last valid executable state (`last_valid_action`) to preserve physical stability.
2. **Low-Overhead Plaintext Path for Stabilization (`INSIGA`)**: `INSIGA` commands (micro-adjustments beneath threshold) bypass encryption over TCP `port 21102`, avoiding unnecessary cryptographic handshakes during idle or near-stationary states.
3. **Global Sequence Protection**: Both ports (`21100` and `21102`) share one monotonically increasing record sequence (`action_record_id`). On every simulation/control tick, the actuator verifies that the incoming record matches the expected global ID. Any missing, duplicate, skipped, or reordered record places the actuator into a fail-closed emergency stop state immediately.
4. **End-to-End Latency Logging**: The monitor attaches `observation_timestamp_ms` to every delivered action chunk, enabling exact end-to-end latency measurement (`monitor_actuator_ms`) across both secure and plaintext boundaries.

---

## 5. Integration with OpenPI & ALOHA Simulation Stack

### 5.1 Baseline OpenPI & ALOHA Control Loop
In standard (unmediated) OpenPI operation, the Vision-Language-Action policy ($\pi_0$-FAST) runs inside an active inference server (`Policy`) while an environment client (`examples/aloha_sim` or physical robot harness) steps through control cycles:
1. **Observation**: The client captures dual-camera visual frames and a 14D proprioceptive joint vector ($q \in \mathbb{R}^{14}$), packaging them into an observation.
2. **Inference**: The policy server runs a forward pass through the PaliGemma backbone and FAST action expert, producing a $32 \times 32$ decoded action chunk at roughly 1.2–1.5 second intervals.
3. **Execution**: The client takes the predicted joint trajectory (`actions[:, :14]`) and directly commands the physical servo driver or ALOHA MuJoCo physics simulation environment step-by-step.

### 5.2 Decoupled Security Mediation & Gateway Encapsulation
Our secure architecture intercepts and enhances this baseline loop without modifying OpenPI's core neural network architecture or training weights:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        OpenPI Policy Wrapper                           │
│     (examples/aloha_sim client or active policy inference loop)        │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ Decoded Action Chunk [32 x 32]
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        openpi_monitor.py                               │
│  • Intercepts chunks and evaluates 14D ALOHA kinematics over H_exec=10 │
│  • Classifies chunk: combined_score >= threshold  ──► SIGA vs INSIGA   │
│  • Manages dynamic context (People=1, Location=Meeting Room, Time)     │
└────────┬───────────────────────────────────────────────────────┬───────┘
         │ (If SIGA: Requires Session Key)                       │ (If INSIGA: Plaintext)
         ▼                                                       │
┌─────────────────────────────────┐                              │
│     SST / IoTAuth Framework     │                              │
│  • Check abs/rel key validity   │                              │
│  • Request new symmetric key K  │                              │
└────────┬────────────────────────┘                              │
         │ Authenticated Key K                                   │
         ▼                                                       │
┌─────────────────────────────────┐                              │
│  SecureActuatorClient (TCP 21100)│                             │
│  • Encrypts & signs action chunk│                              │
└────────┬────────────────────────┘                              ▼
         │                                      ┌─────────────────────────────────┐
         │                                      │ InsigaActuatorClient (TCP 21102)│
         │                                      │ • Plaintext local IPC           │
         │                                      └────────────────┬────────────────┘
         ▼                                                       ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     secure_actuator_gateway.py                         │
│  • Port 21100: accept_secure() decrypts using Auth key ID              │
│  • Port 21102: accept_insiga() receives plaintext stabilization chunk  │
│  • Verifies monotonically increasing global action_record_id           │
│  • Executes action in ALOHA MuJoCo engine (driver_handoff_ms)          │
└────────────────────────────────────────────────────────────────────────┘
```

#### Key Integration Points:
1. **Non-Invasive Interception (`openpi_monitor.py`)**: The monitor runs alongside the inference client, either listening to live action arrays or tailing structured log buffers (`pi0_fast_tokens*.jsonl`). It acts as a hard safety gatekeeper before any joint target reaches physical hardware.
2. **Context-Aware Cryptographic Authorization (IoTAuth / SST)**: When intended motion exceeds the kinematic threshold (`SIGA`), `openpi_monitor.py` engages the local/remote Auth Server (`SST`) using `client.config`. It acquires a symmetric session key bound to dynamic environmental context (`IOTAUTH_CONTEXT_PEOPLE`, `LOCATION`, `TIME`), ensuring only authorized policies under valid conditions can command physical motion.
3. **Actuator Gateway Wrapping (`secure_actuator_gateway.py`)**: The ALOHA MuJoCo simulation (or physical servo driver) is wrapped inside a dedicated dual-socket server. By splitting traffic into TCP `port 21100` (`accept_secure()`) and TCP `port 21102` (`accept_insiga()`), the physics loop executes authenticated `SIGA` trajectories and low-latency `INSIGA` stabilization commands while strictly enforcing global sequence ordering across both sockets.

---

## 6. Decision Mechanism: Edge-Triggered State Transition & Key Lifecycle Management

### 6.1 Action Episodes and Demand-Driven Key Acquisition
Robot manipulation tasks naturally decompose into temporally coherent **action episodes** — contiguous sequences of active movement separated by idle periods [15][16]:

```
[IDLE] → [REACH] → [GRASP] → [LIFT] → [TRANSPORT] → [PLACE] → [IDLE]
```

Within a single active manipulation episode, `openpi_monitor.py` evaluates incoming action chunks and classifies them as **`SIGA`** (`combined_score >= threshold`). During active tasks, `SIGA` chunks arrive continuously across many consecutive cycles (typically spanning 12–25 seconds of real time). Requesting a new IoTAuth session key from the Auth server for every single `SIGA` record would:

1. Generate 10–20 redundant cryptographic handshakes for a single physical action
2. Exhaust the 10-key session cache rapidly
3. Impose unnecessary network latency on the control loop

Instead, the runtime gatekeeper adopts **demand-driven, edge-triggered key lifecycle management**: a session key is requested from the Auth server only when an incoming action chunk is classified as `SIGA` **and** no valid cached session key is currently available. Once acquired, the symmetric key $K$ is reused across all subsequent `SIGA` chunks in the episode until expiration.

### 6.2 Incorporating Session Key Validity
The decision mechanism accounts for the temporal validity of cached session keys ($K$). A session key $K$ is considered **valid** at time $t$ if and only if both absolute and relative timers are satisfied:

$$\text{valid}(K, t) = \begin{cases} \text{true} & \text{if } t < t_{grant} + V_{abs} \;\wedge\; (K.\text{first\_use} = \text{null} \;\vee\; t < t_{first\_use} + V_{rel}) \\ \text{false} & \text{otherwise} \end{cases}$$

### 6.3 Complete Runtime Decision Logic
Let $M_t \in \{\text{SIGA}, \text{INSIGA}\}$ denote the continuous kinematic motion classification of the action chunk at record $t$ (determined by `openpi_monitor.py` computing $\max(\text{intra\_score}, \text{inter\_score})$ against `threshold`). Let $K$ denote the currently cached session key (or `null` if none exists). The runtime decision and routing function is:

$$\text{action}(t) = \begin{cases} \text{REQUEST\_KEY \& SEND\_SECURE (Port 21100)} & \text{if } M_t = \text{SIGA} \;\wedge\; (K = \text{null} \;\vee\; \neg\,\text{valid}(K, t)) \\[6pt] \text{REUSE\_KEY \& SEND\_SECURE (Port 21100)} & \text{if } M_t = \text{SIGA} \;\wedge\; K \neq \text{null} \;\wedge\; \text{valid}(K, t) \\[6pt] \text{SEND\_PLAINTEXT (Port 21102)} & \text{if } M_t = \text{INSIGA} \end{cases}$$

This produces four concrete runtime execution scenarios:

| # | Robot State Transition | Key Cache Status | Gatekeeper Action & Channel Routing |
|---|---|---|---|
| 1 | `INSIGA` → `SIGA` (Onset of motion) | No valid cached key | **Request** session key $K$ via `client.config` (`SST`), encrypt chunk with $K$, and send over TCP **Port 21100** |
| 2 | Sustained `SIGA` (Ongoing movement) | Cached key $K$ still valid | **Reuse** cached key $K$ to encrypt chunk and send over TCP **Port 21100** (zero Auth network overhead) |
| 3 | Sustained `SIGA` (Ongoing movement) | Key $K$ expires mid-action ($V_{abs}$ / $V_{rel}$ exceeded) | **Request** new key renewal from Auth server, encrypt chunk, and send over TCP **Port 21100** |
| 4 | `INSIGA` (`combined_score < threshold`) | Any key status (valid, expired, or null) | **Forward directly** in plaintext over TCP **Port 21102** (zero cryptographic overhead, no key renewals) |

> [!NOTE]
> Scenario 4 is critical for control loop efficiency and key conservation. When the robot is idle or performing sub-threshold stabilization (`INSIGA`), the gatekeeper routes commands over trusted plaintext IPC (`port 21102`). Even if a cached session key expires while the robot is stationary, `openpi_monitor.py` does **not** proactively request a replacement. It waits until the next `SIGA` onset, preventing unnecessary network handshakes and cache turnover during extended idle periods.

---

## 7. Summary

The monitoring and secure actuator mediation system enforces kinematic safety and demand-driven cryptographic authentication through three integrated layers:

1. **Kinematic Gatekeeper Layer (`openpi_monitor.py`)**: Decoded continuous 14D ALOHA joint trajectories (`actions[:10, :14]`) are evaluated using intra-chunk peak-to-peak range and inter-chunk displacement scores over the execution horizon ($H_{exec}=10$) to strictly classify `SIGA` vs `INSIGA`.
2. **Cryptographic & Temporal Layer (IoTAuth / SST)**: Edge-triggered logic and session key checks ($V_{abs}$ and $V_{rel}$) ensure `SIGA` actions dynamically negotiate or reuse authenticated keys based on environmental context (`People`, `Location`, `Time`), while zero-overhead `INSIGA` routing minimizes network traffic during idle periods.
3. **Dual-Channel Actuator Gateway Layer (`secure_actuator_gateway.py`)**: `SIGA` actions flow encrypted/authenticated over TCP `port 21100`, while `INSIGA` micro-adjustments flow over trusted plaintext `port 21102`. Both channels strictly enforce a unified monotonically increasing global record sequence (`action_record_id`), failing closed immediately on any sequence discrepancy while logging exact end-to-end `monitor_actuator_ms` latency.

---

## References

[1] Black, K., Brown, N., Driess, D., et al. (2024). "π₀: A Vision-Language-Action Flow Model for General Robot Control." *arXiv preprint arXiv:2410.24164*.

[2] Brohan, A., Brown, N., Carbajal, J., et al. (2023). "RT-2: Vision-Language-Action Models Transfer Web Knowledge to Robotic Control." *arXiv preprint arXiv:2307.15818*.

[3] Physical Intelligence. (2025). *OpenPI: Open-source implementation of π₀ and π₀-FAST*. GitHub repository. https://github.com/Physical-Intelligence/openpi

[4] Pertsch, K., et al. (2025). "Fast Fine-Tuning and Inference of VLAs with π₀-FAST." *Physical Intelligence Technical Report*.

[5] Sennrich, R., Haddow, B., & Birch, A. (2016). "Neural Machine Translation of Rare Words with Subword Units." *Proceedings of the 54th Annual Meeting of the Association for Computational Linguistics (ACL)*, 1715–1725.

[6] Kudo, T., & Richardson, J. (2018). "SentencePiece: A simple and language independent subword tokenizer and detokenizer for Neural Text Processing." *Proceedings of the 2018 Conference on Empirical Methods in Natural Language Processing (EMNLP)*, 66–71.

[7] Ahmed, N., Natarajan, T., & Rao, K.R. (1974). "Discrete Cosine Transform." *IEEE Transactions on Computers*, C-23(1), 90–93.

[8] Gage, P. (1994). "A New Algorithm for Data Compression." *The C Users Journal*, 12(2), 23–38.

[9] Zhao, T., Kumar, V., Levine, S., & Finn, C. (2023). "Learning Fine-Grained Bimanual Manipulation with Low-Cost Hardware." *Proceedings of Robotics: Science and Systems (RSS)*.

[10] Beyer, L., et al. (2024). "PaliGemma: A Versatile 3B VLM for Transfer." *arXiv preprint arXiv:2407.07726*.

[11] Kim, H., Kang, E., Broman, D., & Lee, E.A. (2017). "An Architectural Mechanism for Resilient IoT Services." *Proceedings of the 1st ACM Workshop on the Internet of Safe Things (SafeThings)*, ACM.

[12] Shannon, C.E. (1948). "A Mathematical Theory of Communication." *Bell System Technical Journal*, 27(3), 379–423.

[13] Lakhina, A., Crovella, M., & Diot, C. (2005). "Mining Anomalies Using Traffic Feature Distributions." *ACM SIGCOMM Computer Communication Review*, 35(4), 217–228.

[14] Nychis, G., Sekar, V., Andersen, D.G., Kim, H., & Zhang, H. (2008). "An Empirical Evaluation of Entropy-Based Traffic Anomaly Detection." *Proceedings of the 8th ACM SIGCOMM Conference on Internet Measurement (IMC)*, 151–156.

[15] Kober, J., Bagnell, J.A., & Peters, J. (2013). "Reinforcement Learning in Robotics: A Survey." *International Journal of Robotics Research*, 32(11), 1238–1274.

[16] Flash, T., & Hogan, N. (1985). "The Coordination of Arm Movements: An Experimentally Confirmed Mathematical Model." *Journal of Neuroscience*, 5(7), 1688–1703.

[17] Patterson, D.A., & Hennessy, J.L. (2017). *Computer Organization and Design: The Hardware/Software Interface*. 5th Edition, Morgan Kaufmann.
