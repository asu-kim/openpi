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
    shoulder_range = classifier.ALOHA_ACTION_RANGES[1]
    actions[:10, 1] = np.linspace(0.0, 0.25 * shoulder_range, 10)

    result = analyze(actions, threshold=0.2)

    assert result.label == classifier.SIGA
    assert np.isclose(result.intra_score, 0.25)
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


def test_inter_score_is_fraction_of_joint_range():
    actions = np.zeros((10, 14), dtype=np.float32)
    reference = np.zeros(14, dtype=np.float32)
    elbow_range = classifier.ALOHA_ACTION_RANGES[2]
    actions[:, 2] = 0.25 * elbow_range

    result = analyze(actions, reference=reference, threshold=0.2)

    assert result.label == classifier.SIGA
    assert result.intra_score == 0.0
    assert np.isclose(result.inter_score, 0.25)
    assert np.isclose(result.combined_per_dimension[2], 0.25)


def test_full_range_displacement_produces_score_one():
    actions = np.zeros((10, 14), dtype=np.float32)
    actions[:, 0] = np.linspace(
        classifier.ALOHA_ACTION_LOWER_LIMITS[0],
        classifier.ALOHA_ACTION_UPPER_LIMITS[0],
        10,
    )
    reference = actions[0].copy()

    result = analyze(actions, reference=reference, threshold=0.99)

    assert result.label == classifier.SIGA
    assert np.isclose(result.intra_score, 1.0)
    assert np.isclose(result.combined_score, 1.0)


def test_normalization_prevents_large_range_joint_from_dominating():
    actions = np.zeros((10, 14), dtype=np.float32)
    actions[:, 0] = np.linspace(0.0, 0.2 * classifier.ALOHA_ACTION_RANGES[0], 10)
    actions[:, 1] = np.linspace(0.0, 0.3 * classifier.ALOHA_ACTION_RANGES[1], 10)

    result = analyze(actions, reference=actions[0], threshold=0.25)

    assert result.label == classifier.SIGA
    assert np.isclose(result.intra_per_dimension[0], 0.2)
    assert np.isclose(result.intra_per_dimension[1], 0.3)
    assert np.isclose(result.combined_score, 0.3)
    assert result.num_significant_dimensions == 1


def test_out_of_range_executable_action_is_rejected():
    actions = np.zeros((10, 14), dtype=np.float32)
    actions[3, 1] = classifier.ALOHA_ACTION_UPPER_LIMITS[1] + 0.01

    with np.testing.assert_raises_regex(ValueError, "step 3, dimension 1"):
        analyze(actions)


def test_out_of_range_reference_action_is_rejected():
    actions = np.zeros((10, 14), dtype=np.float32)
    reference = np.zeros(14, dtype=np.float32)
    reference[6] = 1.01

    with np.testing.assert_raises_regex(ValueError, "reference action at dimension 6"):
        analyze(actions, reference=reference)


def test_threshold_must_be_normalized():
    actions = np.zeros((10, 14), dtype=np.float32)

    with np.testing.assert_raises_regex(ValueError, "between 0 and 1"):
        analyze(actions, threshold=1.01)
