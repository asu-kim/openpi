import json

from scripts.lamps_2026 import analyze_latency


def test_analyzer_reports_monitor_actuator_latency(tmp_path):
    token_log = tmp_path / "tokens.jsonl"
    token_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "record_index": 0,
                        "time_unix": 1.0,
                        "motion_label": "siga",
                        "motion_intra_ptp": 0.4,
                        "motion_inter_delta": 0.2,
                        "motion_combined_score": 0.4,
                    }
                ),
                json.dumps(
                    {
                        "record_index": 1,
                        "time_unix": 1.2,
                        "motion_label": "insiga",
                        "motion_intra_ptp": 0.01,
                        "motion_inter_delta": 0.02,
                        "motion_combined_score": 0.02,
                    }
                ),
            ]
        )
        + "\n"
    )
    actuator_log = tmp_path / "actuator.jsonl"
    actuator_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "record_id": 0,
                        "observation_id": 0,
                        "motion_label": "siga",
                        "monitor_start_ms": 1_000,
                        "driver_handoff_ms": 1_025,
                        "monitor_actuator_ms": 25,
                    }
                ),
                json.dumps(
                    {
                        "record_id": 1,
                        "observation_id": 1,
                        "motion_label": "insiga",
                        "monitor_start_ms": 2_000,
                        "driver_handoff_ms": 2_015,
                        "monitor_actuator_ms": 15,
                    }
                ),
            ]
        )
        + "\n"
    )
    report = tmp_path / "report.txt"

    analyze_latency.analyze_latencies(
        str(token_log),
        str(report),
        actuator_log=str(actuator_log),
    )

    content = report.read_text()
    assert "Total Actuator Records: 2" in content
    assert "SECURE ARCHITECTURE PER-INFERENCE RESULTS" in content
    assert "0.400000" in content
    assert "0.200000" in content
    assert "SIGA Records: 1 (50.00%)" in content
    assert "INSIGA Records: 1 (50.00%)" in content
    assert "Average Monitor-Actuator Latency: 20.00 ms" in content
    assert "Worst-Case Monitor-Actuator Lat.: 25.00 ms" in content
    assert "Absolute maximum occurred at Record 0" in content


def test_analyzer_rejects_duplicate_actuator_record_ids(tmp_path):
    actuator_log = tmp_path / "actuator.jsonl"
    record = {
        "record_id": 0,
        "observation_id": 0,
        "motion_label": "siga",
        "monitor_start_ms": 1_000,
        "driver_handoff_ms": 1_010,
        "monitor_actuator_ms": 10,
    }
    actuator_log.write_text(json.dumps(record) + "\n" + json.dumps(record) + "\n")

    try:
        analyze_latency._read_actuator_latency_records(str(actuator_log))
    except ValueError as exc:
        assert "duplicate actuator latency record_id 0" in str(exc)
    else:
        raise AssertionError("duplicate record IDs should be rejected")
