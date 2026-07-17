"""Intra- and inter-chunk motion classification for executable robot actions."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


INSIGA = "insiga"
SIGA = "siga"


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
    """Classify the executable prefix using within- and between-chunk motion."""
    array = np.asarray(actions, dtype=np.float32)
    reference = np.asarray(reference_action, dtype=np.float32).reshape(-1)
    if array.ndim != 2 or not array.shape[0] or not array.shape[1]:
        raise ValueError(f"actions must be a non-empty 2D array, got {array.shape}")
    if execution_horizon < 1:
        raise ValueError("execution_horizon must be positive")
    if threshold < 0:
        raise ValueError("motion threshold must be non-negative")
    if reference.size < array.shape[1]:
        raise ValueError(f"reference action has {reference.size} dimensions; expected at least {array.shape[1]}")
    if not np.isfinite(array).all() or not np.isfinite(reference).all():
        raise ValueError("actions and reference action must contain only finite values")

    horizon = min(execution_horizon, array.shape[0])
    executable = array[:horizon]
    intra = np.ptp(executable, axis=0)
    inter = np.abs(executable[0] - reference[: array.shape[1]])
    combined = np.maximum(intra, inter)
    combined_score = float(np.max(combined))
    label = SIGA if combined_score > threshold else INSIGA
    return ActionMotionAnalysis(
        label=label,
        execution_horizon=horizon,
        intra_score=float(np.max(intra)),
        inter_score=float(np.max(inter)),
        combined_score=combined_score,
        intra_per_dimension=np.ascontiguousarray(intra),
        inter_per_dimension=np.ascontiguousarray(inter),
        combined_per_dimension=np.ascontiguousarray(combined),
        num_significant_dimensions=int(np.count_nonzero(combined > threshold)),
    )
