# Threshold Selection Methodology for Latency vs. Motion Threshold Evaluation

This document defines the formal experimental methodology and step-by-step procedure for selecting motion-significance threshold values ($\tau$) when evaluating **Average Monitor Latency vs. Motion Threshold** in token-based action monitoring.

---

## 1. Problem Statement & Motivation

### 1.1 Zero-Heavy Shannon Entropy Distributions
In tokenized robotic control sequences (FAST action tokens), stationary or repetitive intervals generate low-entropy or single-token sequences ($H \approx 0$). Consequently, across an evaluation suite of records ($N=30$), Shannon entropy scores often exhibit a **zero-inflated, skewed distribution**:
- A significant cluster of records scores exactly $s_i = 0.00$.
- Non-zero scores scatter sparsely across $(0.0, 1.0]$.

### 1.2 Failure of Naive Uniform Grid Search
Selecting candidate thresholds via linearly spaced grid search (e.g., $\tau \in \{0.1, 0.2, \dots, 0.9\}$) introduces two experimental artifacts:
1. **Empty Intervals (Flat Lines):** Increasing $\tau$ across intervals containing no empirical scores leaves the number of bypassed records unchanged, producing artificial flat segments on the latency curve.
2. **Abrupt Jumps:** Crossing dense clusters causes sudden step-function drops in latency that obscure true system scaling behavior.

### 1.3 Stochastic Variation Across Runs
In multi-run evaluations, non-zero entropy occurrences and exact numeric scores can shift across records from run to run. Selecting quantile thresholds from a single run risks **overfitting** to that run's specific trajectory ordering.

---

## 2. Two-Stage Empirical Quantile Calibration Protocol

To ensure reproducibility, scientific rigor, and smooth monotonic scaling across runs, we adopt a **Two-Stage Calibration & Evaluation Protocol**:

```
[STAGE 1: PROFILING & CALIBRATION (Pre-Test Phase)]
  ├── Run M profiling passes across N=30 records -> aggregate M * N scores into S_pool
  ├── Isolate positive scores: S_pos = { s in S_pool | s > 0 }
  └── Derive fixed thresholds τ_1 ... τ_k from empirical quantiles of S_pos

[STAGE 2: FORMAL EVALUATION & PLOTTING (Test Phase)]
  ├── Fix calibrated thresholds τ_1 ... τ_k across all formal evaluation runs
  ├── Measure average end-to-end monitor latency and bypass rate at each fixed τ
  └── Generate Latency vs. Threshold graph with multi-run error bars
```

---

## 3. Detailed Step-by-Step Procedure

### Step 1: Multi-Run Score Profiling
1. Execute $M$ independent profiling runs (e.g., $M = 3 \text{ to } 5$) across the benchmark suite ($N = 30$ records).
2. Collect all $M \times N$ token Shannon entropy scores into a pooled empirical dataset $S_{\text{pool}}$.

### Step 2: Distribution Partitioning
1. Calculate the stationary ratio $p_{\text{zero}}$, representing the fraction of scores where $s_i \le \epsilon$ ($\epsilon = 10^{-6}$).
2. Extract the strictly positive score distribution:
   $$S_{\text{pos}} = \{ s \in S_{\text{pool}} \mid s > \epsilon \}$$

### Step 3: Fixed Quantile Threshold Derivation
Select $k$ candidate thresholds that span the workload's dynamic range and ensure controlled increments in bypass rate:

| Threshold | Formula / Quantile | Physical / Operational Meaning |
| :--- | :--- | :--- |
| $\tau_1$ | $-0.001$ | **Baseline (0% Bypass):** All records classified as ACTIVE; forces full authentication/session key request on every record. |
| $\tau_2$ | $0.001$ | **Stationary Bypass:** Bypasses purely stationary records ($H=0$) while keeping any motion sequence ACTIVE. |
| $\tau_3$ | $\text{Percentile}(S_{\text{pos}}, 25\%)$ | **Lower Quartile:** Bypasses stationary + bottom 25% of positive entropy records. |
| $\tau_4$ | $\text{Percentile}(S_{\text{pos}}, 50\%)$ | **Median:** Bypasses stationary + bottom 50% of positive entropy records. |
| $\tau_5$ | $\text{Percentile}(S_{\text{pos}}, 75\%)$ | **Upper Quartile:** Bypasses stationary + bottom 75% of positive entropy records. |
| $\tau_6$ | $\max(S_{\text{pool}}) + 0.05$ | **Full Bypass Ceiling (100% Bypass):** All records classified as STILL; no session key requests. |

### Step 4: Monotonicity Pre-Validation
Before running formal benchmarks, verify that for any threshold sequence $\tau_1 < \tau_2 < \dots < \tau_k$, the expected number of active session key requests strictly decreases:
$$\mathbb{E}[N_{\text{active}}(\tau_1)] > \mathbb{E}[N_{\text{active}}(\tau_2)] > \dots > \mathbb{E}[N_{\text{active}}(\tau_k)]$$

Because average monitor latency $L(\tau)$ is governed by:
$$L(\tau) = \frac{N_{\text{active}}(\tau) \cdot L_{\text{auth}} + N_{\text{bypass}}(\tau) \cdot L_{\text{bypass}}}{N}$$
where $L_{\text{auth}} \gg L_{\text{bypass}}$, monotonic reduction in $N_{\text{active}}(\tau)$ guarantees a smoothly decreasing average latency curve.

### Step 5: Formal Multi-Run Benchmark & Visualization
1. Run evaluation tests across the fixed threshold set $\{\tau_1, \tau_2, \dots, \tau_k\}$.
2. Record average end-to-end latency (ms) and standard deviation across runs.
3. Plot **Average Latency vs. Threshold Value** with standard deviation error bars, annotating points with their corresponding average bypass rate (%).

---

## 4. Publication-Ready Paper Write-Up

Copy and adapt the following excerpt for the **Evaluation / Methodology** section of your research paper:

> ### **Threshold Selection and Calibration Methodology**
>
> To evaluate the trade-off between the motion-significance threshold ($\tau$) and end-to-end monitor latency, we calibrate candidate thresholds using the Empirical Cumulative Distribution Function (ECDF) of Shannon entropy scores across the evaluation trajectory suite ($N=30$). Because stationary and repetitive robotic control intervals frequently exhibit near-zero token entropy ($H \approx 0$), uniformly spaced linear grid search across $[0, 1]$ introduces uninformative intervals where no records change classification state.
>
> Furthermore, because individual record token sequences and non-zero entropy occurrences exhibit slight stochastic variations across simulation runs, deriving motion thresholds from a single run risks overfitting to specific trajectory orderings. To establish robust, reproducible threshold operating points, we employ a two-stage calibration and evaluation protocol.
>
> In the **Profiling Stage**, we aggregate token-level Shannon entropy scores across $M$ preliminary simulation passes over the benchmark suite, forming a pooled empirical distribution $S_{\text{pool}}$. We isolate stationary sequences ($H \approx 0$) from active motion sequences ($S_{\text{pos}} > 0$). Fixed evaluation thresholds $\tau \in \{\tau_1, \dots, \tau_k\}$ are then established at the empirical quartiles of $S_{\text{pos}}$, bounded by a zero-motion cutoff ($\tau=0^+$) and a full-bypass ceiling ($\tau > \max S_{\text{pool}}$).
>
> In the **Evaluation Stage**, these calibrated thresholds are fixed across all formal test runs. This pooled quantile calibration ensures that each increment in $\tau$ reliably reduces the expected session key request frequency across independent runs, producing statistically consistent and monotonically decreasing latency measurements.
