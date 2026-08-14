from pathlib import Path

import yaml


def test_phase3_contract_is_versioned_and_preserves_source_timing() -> None:
    root = Path(__file__).resolve().parents[1]
    with (root / "docs/contracts/phase_3_tables.yaml").open(encoding="utf-8") as stream:
        contract = yaml.safe_load(stream)

    assert contract["schema_version"] == "3.0"
    navigation = contract["tables"]["navigation_samples"]["columns"]
    assert {"event_time_ns", "receive_time_ns"} <= navigation.keys()
    assert contract["tables"]["fault_labels"]["feature_eligible"] is False
    truth = contract["tables"]["truth_samples"]["columns"]
    assert {
        "attitude_quaternion_body_to_lvlh_wxyz",
        "port_angular_error_deg",
        "capture_eligible",
        "stack_inertia_about_com_target_frame_kg_m2",
        "capture_angular_momentum_residual_kg_m2_s",
        "controller_authority",
        "handoff_packet_age_s",
        "authority_invariant_valid",
        "stack_controller_vehicle",
    } <= truth.keys()
    assert contract["compatibility"]["silent_forward_fill"] == "forbidden"
    estimates = contract["tables"]["navigation_estimates"]["columns"]
    assert {
        "covariance_trace",
        "normalized_innovation_squared",
        "innovation_consistent",
    } <= estimates.keys()


def test_phase_c_ensemble_contract_has_reproducible_artifacts() -> None:
    root = Path(__file__).resolve().parents[1]
    with (root / "docs/contracts/phase_c_ensemble.yaml").open(encoding="utf-8") as stream:
        contract = yaml.safe_load(stream)
    assert contract["schema_version"] == "1.1"
    assert contract["reproducibility"]["parameter_order_is_semantic"] is True
    assert {"runs.parquet", "convergence.parquet", "risk_summary.json"} <= contract[
        "artifacts"
    ].keys()
    assert contract["reproducibility"]["fault_sampling"] == (
        "independent_bernoulli_from_sample_child_stream"
    )
