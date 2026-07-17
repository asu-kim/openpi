import numpy as np

from openpi_client import action_motion_classifier as classifier


def analyze(actions, reference=None, threshold=0.1, horizon=10):
    if reference is None:
        reference = np.zeros(actions.shape[1], dtype=np.float32)
    return classifier.classify_action_motion(
        actions,
        reference_action=reference,
        execution_horizon=horizon,
        threshold=threshold,
    )


def test_inter_chunk_only_motion_is_siga():
    actions = np.full((32, 14), 0.5, dtype=np.float32)

    result = analyze(actions)

    assert result.label == classifier.SIGA
    assert result.intra_score == 0.0
    assert np.isclose(result.inter_score, 0.5)


def test_intra_chunk_motion_is_siga():
    actions = np.zeros((32, 14), dtype=np.float32)
    actions[:10, 3] = np.linspace(0.0, 0.5, 10)

    result = analyze(actions)

    assert result.label == classifier.SIGA
    assert np.isclose(result.intra_score, 0.5)
    assert result.inter_score == 0.0


def test_low_intra_and_inter_motion_is_insiga():
    actions = np.full((32, 14), 0.04, dtype=np.float32)
    actions[:10, 2] += np.linspace(0.0, 0.03, 10)

    result = analyze(actions, threshold=0.1)

    assert result.label == classifier.INSIGA
    assert result.combined_score < 0.1


def test_discarded_tail_does_not_change_classification():
    actions = np.zeros((32, 14), dtype=np.float32)
    actions[10:, :] = 10.0

    result = analyze(actions, threshold=0.1, horizon=10)

    assert result.label == classifier.INSIGA
    assert result.intra_score == 0.0
    assert result.inter_score == 0.0
