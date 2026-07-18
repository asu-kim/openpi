"""Intra- and inter-chunk motion classification for executable robot actions."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


INSIGA = "insiga"
SIGA = "siga"

# Action-space limits from the MuJoCo model distributed in gym-aloha 0.1.1.
# Arm positions are expressed in radians. Gripper positions are already
# normalized by gym-aloha to 0 (closed) through 1 (open).
_ARM_LOWER_LIMITS = (-3.14158, -1.85005, -1.76278, -3.14158, -1.86750, -3.14158, 0.0)
_ARM_UPPER_LIMITS = (3.14158, 1.25664, 1.60570, 3.14158, 2.23402, 3.14158, 1.0)
ALOHA_ACTION_LOWER_LIMITS = np.asarray(_ARM_LOWER_LIMITS * 2, dtype=np.float32)
ALOHA_ACTION_UPPER_LIMITS = np.asarray(_ARM_UPPER_LIMITS * 2, dtype=np.float32)
ALOHA_ACTION_RANGES = ALOHA_ACTION_UPPER_LIMITS - ALOHA_ACTION_LOWER_LIMITS


@dataclass(frozen=True)
class ActionMotionAnalysis:
    label: str
    execution_horizon: int
    intra_score: float
    inter_score: float
    combined_score: float
    intra_per_dimension: np.ndarray
    inter_per_dimension: np.ndarray
    combined_per_dimension: np.ndarray
    num_significant_dimensions: int


def classify_action_motion(
    actions: np.ndarray,
    *,
    reference_action: np.ndarray,
    execution_horizon: int,
    threshold: float,
) -> ActionMotionAnalysis:
    """Classify normalized within- and between-chunk motion.

    Each displacement is divided by the corresponding gym-aloha 0.1.1
    action range. Consequently, the returned scores describe fractions of a
    dimension's modeled travel and are bounded by ``[0, 1]`` for valid input.
    Only the executable prefix is analyzed; a discarded action tail cannot
    affect either validation or classification.
    """
    array = np.asarray(actions, dtype=np.float32)
    reference = np.asarray(reference_action, dtype=np.float32).reshape(-1)
    if array.ndim != 2 or not array.shape[0] or not array.shape[1]:
        raise ValueError(f"actions must be a non-empty 2D array, got {array.shape}")
    if execution_horizon < 1:
        raise ValueError("execution_horizon must be positive")
    if not 0 <= threshold <= 1:
        raise ValueError("motion threshold must be between 0 and 1")
    if array.shape[1] > ALOHA_ACTION_RANGES.size:
        raise ValueError(
            f"actions have {array.shape[1]} dimensions; gym-aloha supports at most {ALOHA_ACTION_RANGES.size}"
        )
    if reference.size < array.shape[1]:
        raise ValueError(f"reference action has {reference.size} dimensions; expected at least {array.shape[1]}")
    if not np.isfinite(array).all() or not np.isfinite(reference).all():
        raise ValueError("actions and reference action must contain only finite values")

    horizon = min(execution_horizon, array.shape[0])
    executable = array[:horizon]
    lower = ALOHA_ACTION_LOWER_LIMITS[: array.shape[1]]
    upper = ALOHA_ACTION_UPPER_LIMITS[: array.shape[1]]
    ranges = ALOHA_ACTION_RANGES[: array.shape[1]]
    reference = reference[: array.shape[1]]

    invalid_actions = np.argwhere((executable < lower) | (executable > upper))
    if invalid_actions.size:
        step, dimension = invalid_actions[0]
        value = float(executable[step, dimension])
        raise ValueError(
            f"executable action at step {step}, dimension {dimension} is outside the "
            f"gym-aloha limit [{lower[dimension]}, {upper[dimension]}]: {value}"
        )

    invalid_reference = np.flatnonzero((reference < lower) | (reference > upper))
    if invalid_reference.size:
        dimension = int(invalid_reference[0])
        raise ValueError(
            f"reference action at dimension {dimension} is outside the gym-aloha limit "
            f"[{lower[dimension]}, {upper[dimension]}]: {float(reference[dimension])}"
        )

    intra = np.ptp(executable, axis=0) / ranges
    inter = np.abs(executable[0] - reference) / ranges
    # Validation establishes the mathematical bounds. Clipping only absorbs
    # floating-point round-off at exact limits.
    intra = np.clip(intra, 0.0, 1.0)
    inter = np.clip(inter, 0.0, 1.0)
    combined = np.maximum(intra, inter)
    combined_score = float(np.max(combined))
    label = SIGA if combined_score >= threshold else INSIGA
    return ActionMotionAnalysis(
        label=label,
        execution_horizon=horizon,
        intra_score=float(np.max(intra)),
        inter_score=float(np.max(inter)),
        combined_score=combined_score,
        intra_per_dimension=np.ascontiguousarray(intra),
        inter_per_dimension=np.ascontiguousarray(inter),
        combined_per_dimension=np.ascontiguousarray(combined),
        num_significant_dimensions=int(np.count_nonzero(combined >= threshold)),
    )
