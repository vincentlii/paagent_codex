from pathlib import Path

import pytest

from pa_agent.quality.contract import QualityContract, load_quality_contract
from pa_agent.records.quality_gate import default_quality_thresholds

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_repository_quality_contract_is_valid() -> None:
    contract = load_quality_contract(PROJECT_ROOT / "config" / "quality_contract.toml")

    assert contract.version == 2
    assert contract.invariants["max_fact_contradictions"] == 0
    assert contract.structured_output["certified_provider_first_pass_rate"] == 0.995
    assert (
        contract.research["external_numeric_probability_gate_requires_calibration"]
        is True
    )
    assert (
        default_quality_thresholds().min_unique_snapshots
        == contract.research["minimum_independent_snapshots"]
    )
    thresholds = default_quality_thresholds()
    assert thresholds.max_call_p95_latency_ms == contract.performance[
        "max_call_p95_latency_ms"
    ]
    assert thresholds.max_b_to_a_token_ratio_p95 == contract.performance[
        "max_b_to_a_token_ratio_p95"
    ]


def test_quality_contract_rejects_invalid_probability_threshold() -> None:
    with pytest.raises(ValueError, match="first_pass_rate"):
        QualityContract(
            version=1,
            invariants={"max_fact_contradictions": 0},
            structured_output={
                "certified_provider_first_pass_rate": 1.1,
                "max_false_hard_reject_rate": 0.01,
            },
            performance={},
            research={"max_unexplained_trade_frequency_decline": 0.15},
        )
