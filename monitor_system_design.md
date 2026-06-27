# Design Document: Entropy-Based Motion Significance Monitor for VLA-Controlled Robotic Systems with IoTAuth Integration

---

## 1. Introduction

This document describes the design of a real-time monitoring system that observes the discrete output of a Vision-Language-Action (VLA) model controlling an ALOHA bimanual robot, classifies each output record by its motion significance using information-theoretic measures, and conditionally negotiates secure session keys through the IoTAuth framework before forwarding commands to the physical actuator.

The central challenge is: **given a discrete stream of model output records (arriving at approximately 1.2–1.5 second intervals, determined by GPU inference latency), how does the monitor determine which records represent significant intended motion — and therefore warrant requesting a session key and forwarding the authenticated command to the actuator — versus insignificant idle states, which are dropped entirely and never forwarded?**

---

## 2. Definitions and Terminology

This section defines every technical term used throughout the document. Each definition is grounded in the relevant literature.

### 2.1 Vision-Language-Action (VLA) Model

A VLA model is a neural network that accepts visual observations (video frames), natural language instructions, and **proprioceptive state** (the robot's current joint angles and positions) as inputs, and produces robot control commands as output. VLA models extend Vision-Language Models (VLMs) by adding an action generation component, enabling end-to-end visuomotor control in embodied AI systems [1][2].

The specific VLA used in this system is **π₀-FAST** (Pi-Zero-FAST), which combines a PaliGemma-based VLM backbone with the FAST action tokenizer [3][4]. In the model architecture description for π₀, the authors explicitly detail how the model handles these different modalities: the architecture features a dedicated "action expert" module. This action expert is responsible for taking the semantic outputs from the VLM backbone and integrating them with the robot's proprioceptive state—which is passed in as a 1D floating-point vector of joint positions and velocities—to generate the final continuous action chunks [1].

### 2.2 Token and Token ID

In the context of autoregressive language models, a **token** is the fundamental discrete unit of the model's vocabulary. Each token is assigned a unique integer called a **Token ID**, which serves as an index into the model's embedding matrix [5]. The PaliGemma backbone used in π₀-FAST maintains a vocabulary of approximately 257,000 tokens, which includes text subwords (via SentencePiece [6]), special structural markers, and action tokens.

### 2.3 FAST Action Token

FAST (Frequency-space Action Sequence Tokenization) is a compression-based tokenization scheme introduced by Pertsch et al. [4] that converts continuous robot trajectories into compact discrete token sequences. The encoding pipeline is:

1. **Normalization** of the continuous action chunk
2. **Discrete Cosine Transform (DCT)** applied per action dimension, projecting the trajectory into the frequency domain [7]
3. **Quantization** and removal of insignificant frequency coefficients
4. **Flattening** into a 1D sequence (low-frequency components first)
5. **Byte Pair Encoding (BPE)** compression [8] into a short sequence of discrete tokens

> [!IMPORTANT]
> A single FAST action token does **not** correspond to a single joint angle or a single motor command. FAST tokens are compressed frequency-domain representations. The entire sequence of tokens must be collectively decoded (via inverse-BPE and inverse-DCT) to recover the continuous action trajectory [4]. This is explicitly stated in the OpenPI codebase: *"A single FAST token is NOT 'move joint X'. FAST tokens are compressed trajectory tokens."*

### 2.4 Action Chunk

An **action chunk** is a fixed-size matrix of continuous-valued motor commands output by a single forward pass of the VLA model. Its shape is $[H \times D]$, where $H$ is the action horizon and $D$ is the action dimension. For the ALOHA simulation configuration used in this system, $H = 32$ and $D = 32$, yielding a matrix of 1,024 continuous values per inference step [3][9].

The action chunk is the **decoded** form of the FAST token sequence. It is the data structure that is ultimately sent to the physical actuator.

### 2.5 Action Horizon ($H$)

The **action horizon** is the number of future control timesteps predicted in a single inference pass. This concept was formalized by Zhao et al. [9] as part of the Action Chunking with Transformers (ACT) framework. Rather than predicting one action at a time, the model predicts a sequence of $H$ future actions simultaneously. This improves temporal consistency and reduces compounding errors in imitation learning [9].

In this system, $H = 32$, meaning the model predicts 32 future timesteps of motor commands per inference.

### 2.6 Action Dimension ($D$)

The **action dimension** is the number of independent control degrees of freedom at each timestep. For the ALOHA bimanual robot platform [9], only the first 14 of the 32 dimensions are robot-relevant:

| Dimensions | Description |
|---|---|
| 0–5 | Left arm joint angles (6 revolute joints) |
| 6 | Left gripper aperture |
| 7–12 | Right arm joint angles (6 revolute joints) |
| 13 | Right gripper aperture |

Dimensions 14–31 are padding for the model's internal alignment and are not used for physical control.

### 2.7 Record

A **record** is a single line in the `.jsonl` log file, corresponding to the complete output of one forward pass (one inference cycle) of the VLA model. Each record is produced in response to a single **observation** — a synchronized snapshot of camera images (vision tokens) and motor encoder readings (proprioceptive state tokens) [1][3].

Each record contains the following fields relevant to this system:

| Field                     | Type      | Description                                                                                             |
| ------------------------- | --------- | ------------------------------------------------------------------------------------------------------- |
| `record_index`            | int       | Sequential index of this inference step                                                                 |
| `time_iso`                | string    | ISO-8601 timestamp of when inference completed                                                          |
| `action_horizon`          | int       | $H$ (number of predicted future timesteps)                                                              |
| `action_dim`              | int       | $D$ (number of control dimensions per timestep)                                                         |
| `raw_paligemma_token_ids` | int[]     | Complete raw output of the VLA model, including structural tokens, FAST action tokens, EOS, and padding |
| `fast_action_token_ids`   | int[]     | Extracted FAST action tokens only (structural tokens and padding removed)                               |
| `decoded_actions`         | float[][] | The decoded continuous action chunk $[H \times D]$ (may be all-zero if decoding failed)                 |
| `state`                   | float[]   | Proprioceptive state vector at the time of observation (14 values for ALOHA)                            |

The empirically observed time interval between consecutive records is approximately **1.2–1.5 seconds**, determined by the GPU inference latency of the VLA model on the available hardware.

### 2.8 Observation

An **observation** is the synchronized input snapshot that triggers one inference cycle of the VLA model. It consists of:

1. **Vision tokens**: Video frames (from head and/or wrist cameras) encoded through a Vision Transformer (SigLIP) into a sequence of visual embeddings [10][1]
2. **Proprioceptive state tokens**: The robot's current joint angles and gripper positions, projected into the model's embedding space via a linear layer [1][3]
3. **Language tokens**: The task instruction (e.g., "pick up the cup"), tokenized via SentencePiece [6]

There is a strict **1-to-1 correspondence** between observations and records: each observation produces exactly one record.

### 2.9 Session Key

In the IoTAuth framework [11], a **session key** is a symmetric cryptographic key distributed by a trusted authorization server (Auth) to authenticated entities for secure peer-to-peer communication. Each session key contains:

| Field | Description |
|---|---|
| `id` | 8-byte unique identifier |
| `cipher_key` | Symmetric encryption key |
| `mac_key` | HMAC key (if HMAC is enabled) |
| `abs_validity` | **Absolute validity period**: duration (in milliseconds) for which the key is valid, measured from the moment of issuance by the Auth server |
| `rel_validity` | **Relative validity period**: duration (in milliseconds) for which the key is valid, measured from the moment of its **first use** for communication |
| `first_use_ms` | Timestamp (in milliseconds) of when the key was first used |

The Auth server maintains a cache of up to 10 session keys per entity [11].

---

## 3. System Security Model

In this system, **no command can reach the physical actuator without a valid session key**. The monitor operates as a hard security gatekeeper positioned between the VLA model's output and the robot's physical actuators.

This enforces a strict invariant: the actuator will not execute any action chunk — regardless of its content — unless the monitor has successfully negotiated a valid session key with the IoTAuth server. Records classified as insignificant (motion label `STILL` or `LOW`) are **discarded at the monitor** and never forwarded. They are not passed through unauthenticated.

This architecture ensures that:

1. **No unauthenticated commands reach hardware**: Even if the VLA model produces valid motor commands, they cannot bypass the authentication layer.
2. **Idle records incur zero network cost**: The system does not request session keys for records that will never be forwarded.
3. **Authentication is demand-driven**: Session keys are requested only when the monitor classifies a record as significant enough to warrant forwarding.

---

## 4. The Monitoring Pipeline

### 4.1 System Architecture

```
┌─────────────────┐    ┌──────────────┐    ┌──────────────────────┐    ┌──────────────┐
│  ALOHA Robot     │    │  .jsonl      │    │  Token Monitor       │    │  IoTAuth     │
│  + VLA Model     │───▶│  Log File    │───▶│  (this system)       │───▶│  Auth Server │
│  (π₀-FAST)       │    │              │    │                      │    │              │
└─────────────────┘    └──────────────┘    │  1. Extract tokens    │    └──────┬───────┘
                                           │  2. Compute entropy   │           │
                                           │  3. Classify motion   │    Session Key
                                           │  4. Check key validity│           │
                                           │  5. Request/reuse key │◀──────────┘
                                           │  6. Forward to actuator│
                                           └──────────────────────┘
```

### 4.2 Data Flow Per Record

For each new record appended to the `.jsonl` log file:

1. **Extract**: Parse the JSON line and retrieve the `fast_action_token_ids` array
2. **Classify**: Compute Shannon entropy over the token ID distribution to classify motion significance
3. **Decide**: Apply the edge-triggered decision logic with session key validity checks
4. **Act**: Either request a new session key, reuse an existing one, or do nothing

---

## 5. Shannon Entropy as a Motion Significance Classifier

### 5.1 Theoretical Basis

Shannon entropy [12] quantifies the average "surprise" or information content of a discrete random variable. For a discrete distribution $X$ with $n$ distinct outcomes and probability mass function $p(x_i)$:

$$H(X) = -\sum_{i=1}^{n} p(x_i) \log_2 p(x_i)$$

$H(X)$ is measured in bits. It achieves its minimum value of $0$ when the distribution is degenerate (one outcome has probability 1), and its maximum value of $\log_2(n)$ when all outcomes are equiprobable (uniform distribution).

### 5.2 Application to FAST Action Tokens

Given a record's `fast_action_token_ids` array $T = [t_1, t_2, \ldots, t_N]$, where $N$ is the number of extracted FAST tokens, we compute:

1. **Frequency count**: $c(v) = |\{i : t_i = v\}|$ for each unique token value $v$
2. **Empirical probability**: $\hat{p}(v) = c(v) / N$
3. **Shannon entropy**: $H(T) = -\sum_{v} \hat{p}(v) \log_2 \hat{p}(v)$
4. **Normalized entropy** (the **motion proxy**):

$$\hat{H}(T) = \frac{H(T)}{\log_2(N)}, \quad \hat{H} \in [0, 1]$$

Normalization by $\log_2(N)$ (the maximum achievable entropy for $N$ tokens) produces a scale-invariant score between 0 and 1, regardless of the number of tokens in the sequence [13].

### 5.3 Why This Works for FAST Tokens

The effectiveness of this approach is grounded in the structure of the FAST tokenizer [4]:

- **FAST uses DCT (frequency-domain encoding)**: A stationary (idle) trajectory has energy concentrated in the DC coefficient only. The DCT of a constant signal produces a single non-zero coefficient, which quantizes to repeated zeros. BPE then compresses these repeated zeros into very few, highly repeated tokens.
- **A moving trajectory excites higher frequencies**: Complex, multi-joint movements produce energy across many DCT coefficients, which quantize to diverse values. BPE produces a longer, more varied token sequence.

Therefore, **token diversity directly tracks trajectory complexity**, even though individual tokens do not map to individual joints. This is explicitly noted in the OpenPI codebase: *"token diversity tracks whole-chunk motion magnitude"* [interpret_fast_tokens.py, line 176].

This approach is analogous to entropy-based anomaly detection in network traffic analysis, where Shannon entropy of packet feature distributions is used to detect state changes without inspecting packet contents [13][14].

### 5.4 Classification Thresholds

The normalized entropy $\hat{H}$ is classified into three motion states using empirically calibrated thresholds:

| Normalized Entropy $\hat{H}$ | Motion Label | Interpretation |
|---|---|---|
| $\hat{H} < 0.35$ | `STILL` | Model output is dominated by repeated "zero-motion" tokens. Robot is idle. |
| $0.35 \leq \hat{H} \leq 0.60$ | `LOW` | Moderate token diversity. Minor adjustments or stabilization movements. |
| $\hat{H} > 0.60$ | `ACTIVE` | High token diversity. Model is commanding complex, multi-joint movement. |

These thresholds (0.35 and 0.60) are defined in the OpenPI interpreter as `STILL_PROXY_T` and `ACTIVE_PROXY_T` respectively.

### 5.5 Supporting Metrics

In addition to the motion proxy, the interpreter computes:

| Metric | Definition | Role |
|---|---|---|
| `unique_ratio` | $|\text{unique tokens}| / N$ | Fraction of tokens that are distinct |
| `dominant_fraction` | $\max_v \hat{p}(v)$ | Probability of the most common token |
| `entropy_bits` | $H(T)$ (unnormalized) | Raw entropy in bits |

These metrics provide supplementary evidence but are not used as primary decision criteria, because normalized entropy subsumes the information they carry [12].

---

## 6. Decision Mechanism: Edge-Triggered State Transition with Key Lifecycle Management

### 6.1 Why Edge-Triggered

Robot manipulation tasks naturally decompose into temporally coherent **action episodes** — contiguous sequences of active movement separated by idle periods [15][16]:

```
[IDLE] → [REACH] → [GRASP] → [LIFT] → [TRANSPORT] → [PLACE] → [IDLE]
```

Within a single action episode, the motion label remains `ACTIVE` for many consecutive records (typically 10–20 records, spanning 12–25 seconds of real time). Requesting a new session key for every `ACTIVE` record would:

1. Generate 10–20 redundant cryptographic handshakes for a single physical action
2. Exhaust the 10-key session cache rapidly
3. Impose unnecessary network latency on the control loop

Instead, we adopt **edge triggering**: the system requests a session key only when the motion label transitions from a non-active state (`STILL` or `LOW`) to `ACTIVE`. This is analogous to edge-triggered interrupts in embedded systems, where hardware fires on the **rising edge** of a signal rather than while the signal is held high [17].

### 6.2 Incorporating Session Key Validity

The decision mechanism must also account for the temporal validity of cached session keys. IoTAuth defines two validity parameters [11]:

- **Absolute validity** ($V_{abs}$): The key expires at time $t_{grant} + V_{abs}$, regardless of whether it has been used.
- **Relative validity** ($V_{rel}$): The key expires at time $t_{first\_use} + V_{rel}$, where $t_{first\_use}$ is the timestamp of the key's first use. If the key has never been used, this clock has not started.

A session key $K$ is considered **valid** at time $t$ if and only if:

$$\text{valid}(K, t) = \begin{cases} \text{true} & \text{if } t < t_{grant} + V_{abs} \;\wedge\; (K.\text{first\_use} = \text{null} \;\vee\; t < t_{first\_use} + V_{rel}) \\ \text{false} & \text{otherwise} \end{cases}$$

### 6.3 Complete Decision Logic

Let $L_t$ denote the motion label at record $t$, and $K$ denote the currently cached session key (or `null` if none exists). The decision function is:

$$\text{action}(t) = \begin{cases} \text{REQUEST\_KEY} & \text{if } L_t = \text{ACTIVE} \;\wedge\; (K = \text{null} \;\vee\; \neg\,\text{valid}(K, t)) \\[6pt] \text{REUSE\_KEY} & \text{if } L_t = \text{ACTIVE} \;\wedge\; K \neq \text{null} \;\wedge\; \text{valid}(K, t) \\[6pt] \text{DO\_NOTHING} & \text{if } L_t \in \{\text{STILL}, \text{LOW}\} \end{cases}$$

This produces four concrete scenarios:

| # | Robot State | Key Status | System Action |
|---|---|---|---|
| 1 | `STILL`/`LOW` → `ACTIVE` | No cached key | **Request** new session key from Auth server |
| 2 | `STILL`/`LOW` → `ACTIVE` | Cached key still valid | **Reuse** cached key (zero network overhead) |
| 3 | Sustained `ACTIVE` | Key expires mid-action ($V_{abs}$ exceeded) | **Request** new key (renewal) |
| 4 | `STILL`/`LOW` | Key expires while idle | **Do nothing** (no need to renew during idle) |

> [!NOTE]
> Scenario 4 is critical for efficiency. If the robot is idle and a cached key expires, the system does **not** proactively request a replacement. It waits until the next `ACTIVE` transition, at which point it requests a fresh key. This minimizes unnecessary network traffic during extended idle periods.

---

## 7. Summary

The monitoring system makes its significance decision through three layers:

1. **Information-theoretic layer**: Shannon entropy of the FAST action token distribution classifies each record as `STILL`, `LOW`, or `ACTIVE`
2. **Temporal layer**: Edge-triggered logic ensures only state transitions (not sustained states) generate authentication events
3. **Cryptographic layer**: Session key validity checks ($V_{abs}$ and $V_{rel}$) determine whether the system must request a new key or can reuse an existing one

This design minimizes network overhead, respects the IoTAuth session key cache constraints, and operates within the ~1.2–1.5 second inference interval of the VLA model.

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
