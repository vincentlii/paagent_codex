from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
from pa_agent.ai.json_validator import JsonValidator, Ok, ValidationError
from pa_agent.ai.prompt_assembler import PromptAssembler
from pa_agent.ai.pa_provider_contracts import (
    STAGE1_DRAFT_SCHEMA,
    STAGE2_DRAFT_SCHEMA,
    materialize_stage1_draft,
    materialize_stage2_draft,
    normalize_provider_draft,
    provider_draft_schema_digest,
    render_provider_draft_contract,
    schema_for_provider_draft,
)
from pa_agent.ai.prompts.schemas import STAGE1_SCHEMA, STAGE2_SCHEMA
from pa_agent.ai.retry_policy import should_retry
from pa_agent.config.settings import ValidationSettings
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from tests.fixtures.ai_payloads import VALID_STAGE2_NO_ORDER
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame


@pytest.mark.parametrize("hypothesis_path", ["primary", "alternatives"])
def test_unknown_hypothesis_reference_reports_original_draft_pointer(hypothesis_path) -> None:
    draft = _stage1_draft()
    hypothesis = draft["market_hypotheses"][hypothesis_path]
    path = f"market_hypotheses.{hypothesis_path}"
    if isinstance(hypothesis, list):
        hypothesis = hypothesis[0]
        path += ".0"
    hypothesis["supporting_fact_refs"] = ["PA.NOT_REAL"]
    result = JsonValidator().validate(
        "stage1", json.dumps(draft), kline_frame=make_frame(),
    )
    assert isinstance(result, ValidationError)
    assert f"{path}.supporting_fact_refs.0" in result.invalid_fields
    assert "pa_evidence.fact_refs" not in result.invalid_fields
    assert any(issue["pointer"] == "/" + path.replace(".", "/") + "/supporting_fact_refs/0"
               for issue in result.contract_issues)


def test_provider_draft_schema_interface_is_isolated_and_stable() -> None:
    stage1 = schema_for_provider_draft("stage1")
    stage2 = schema_for_provider_draft("stage2")

    assert stage1 is not STAGE1_DRAFT_SCHEMA
    assert stage2 is not STAGE2_DRAFT_SCHEMA
    assert {
        "cycle_position",
        "alternative_cycle_position",
        "direction",
        "entry_setup",
        "gate_result",
    }.isdisjoint(stage1["properties"])
    assert {"terminal", "next_cycle_prediction"}.isdisjoint(stage2["properties"])
    assert "gate_shortcircuited" not in stage2["properties"]
    assert "diagnosis_confidence" not in stage2["properties"]["decision"]["properties"]
    for schema in (stage1, stage2):
        trace_name = "gate_trace" if schema is stage1 else "decision_trace"
        trace_properties = schema["properties"][trace_name]["items"]["properties"]
        assert {"next_node", "skipped"}.isdisjoint(trace_properties)

    stage1["properties"].clear()
    assert STAGE1_DRAFT_SCHEMA["properties"]


def test_provider_contract_renderer_uses_the_exact_runtime_schema() -> None:
    rendered = render_provider_draft_contract("stage1")
    schema = schema_for_provider_draft("stage1")

    assert "PA_PROVIDER_DRAFT_CONTRACT_BEGIN" in rendered
    assert "PA_PROVIDER_DRAFT_CONTRACT_END" in rendered
    assert provider_draft_schema_digest("stage1") in rendered
    schema_text = rendered.split("PA_PROVIDER_DRAFT_CONTRACT_BEGIN\n", 1)[1].split(
        "\nPA_PROVIDER_DRAFT_CONTRACT_END", 1
    )[0]
    assert json.loads(schema_text) == schema
    assert len(rendered) < len(json.dumps(schema, ensure_ascii=False))


def test_orchestrator_rejects_split_brain_prompt_resources(tmp_path: Path) -> None:
    assembler = PromptAssembler(tmp_path)

    with pytest.raises(ValueError, match="prompt resource mismatch"):
        TwoStageOrchestrator(
            client=object(),
            assembler=assembler,
            router=object(),
            validator=JsonValidator(),
            pending_writer=object(),
            exp_reader=object(),
        )

    orchestrator = TwoStageOrchestrator(
        client=object(),
        assembler=assembler,
        router=object(),
        validator=JsonValidator(prompt_dir=tmp_path),
        pending_writer=object(),
        exp_reader=object(),
    )
    assert orchestrator._runtime_prompt_dir() == tmp_path


def test_deepseek_style_draft_reports_all_contract_errors_at_once() -> None:
    draft = _stage1_draft()
    draft["cycle_position"] = "normal_channel"
    draft["diagnosis_confidence"] = "medium"
    draft["key_signals"] = {"always_in": "bullish"}
    draft["gate_trace"][0]["answer"] = "yes"

    result = JsonValidator().validate(
        "stage1",
        json.dumps(draft, ensure_ascii=False),
        kline_frame=make_frame(),
    )

    assert isinstance(result, ValidationError)
    assert result.code == "provider_draft_contract"
    assert result.category == "c"
    assert "cycle_position" in result.invalid_fields
    assert "diagnosis_confidence" in result.invalid_fields
    assert "key_signals" in result.invalid_fields
    assert "gate_trace.0.answer" in result.invalid_fields
    assert result.allowed_values["gate_trace.0.answer"] == [
        "是",
        "否",
        "中性",
        "等待",
        "不适用",
    ]
    assert should_retry(
        result.category,
        result.invalid_fields,
        result.missing_fields,
        attempt=0,
        settings=ValidationSettings(retry_max=3),
        error_code=result.code,
    )


def test_non_structural_contract_errors_also_report_fact_conflicts() -> None:
    draft = _stage1_draft()
    draft["gate_trace"][0]["answer"] = "yes"
    draft["market_hypotheses"]["primary"]["thesis"] = (
        "K1低点999999，因此可能支持延续。"
    )

    result = JsonValidator().validate(
        "stage1",
        json.dumps(draft, ensure_ascii=False),
        kline_frame=make_frame(),
    )

    assert isinstance(result, ValidationError)
    assert result.code == "provider_draft_contract"
    issue_paths = {issue["path"] for issue in result.contract_issues}
    assert "gate_trace.0.answer" in issue_paths
    assert "market_hypotheses.primary.thesis" in issue_paths


def test_materializable_leaf_type_error_does_not_hide_fact_conflicts() -> None:
    draft = _stage1_draft()
    draft["bar_analysis"]["signal_bar"].update(
        {"bar": None, "quality": "invalid", "pattern": 123}
    )
    draft["diagnosis_confidence_reasoning"] = (
        "K1低点999999，因此当前诊断可信。"
    )

    result = JsonValidator().validate(
        "stage1",
        json.dumps(draft, ensure_ascii=False),
        kline_frame=make_frame(),
    )

    assert isinstance(result, ValidationError)
    assert result.code == "provider_draft_contract"
    issue_paths = {issue["path"] for issue in result.contract_issues}
    assert "bar_analysis.signal_bar.pattern" in issue_paths
    assert "diagnosis_confidence_reasoning" in issue_paths


def test_stage1_classifier_answers_are_normalized_without_losing_meaning() -> None:
    draft = _stage1_draft()
    node_22 = next(item for item in draft["gate_trace"] if item["node_id"] == "2.2")
    node_23 = next(item for item in draft["gate_trace"] if item["node_id"] == "2.3")
    node_22.update(answer="同向", branch=None)
    node_23.update(answer="是多头", branch=None)

    result = JsonValidator().validate(
        "stage1",
        json.dumps(draft, ensure_ascii=False),
        kline_frame=make_frame(),
    )

    assert isinstance(result, Ok)
    trace = {item["node_id"]: item for item in result.obj["gate_trace"]}
    assert (trace["2.2"]["answer"], trace["2.2"]["branch"]) == ("是", "同向")
    assert (trace["2.3"]["answer"], trace["2.3"]["branch"]) == ("是", "多头")


def test_trace_normalization_audit_matches_nodes_after_preflight_removal() -> None:
    draft = _stage1_draft()
    draft["gate_trace"].insert(
        0,
        {
            "node_id": "1.1",
            "answer": "是",
            "reason": "模型重复了程序数据预检。",
            "evidence_refs": [],
        },
    )
    node_22 = next(item for item in draft["gate_trace"] if item["node_id"] == "2.2")
    node_22.update(answer="同向", branch=None)

    result = JsonValidator().validate(
        "stage1",
        json.dumps(draft, ensure_ascii=False),
        kline_frame=make_frame(),
    )

    assert isinstance(result, Ok)
    events = {event["path"]: event for event in result.normalization_events}
    assert events["gate_trace.2.2.answer"]["after"] == "是"
    assert events["gate_trace.2.2.branch"]["after"] == "同向"
    assert not any(path.startswith("gate_trace.1.2.") for path in events)


def test_downstream_trace_fact_error_maps_back_to_provider_index() -> None:
    draft = _stage1_draft()
    draft["gate_trace"].insert(
        0,
        {
            "node_id": "1.1",
            "answer": "是",
            "reason": "模型重复了程序数据预检。",
            "evidence_refs": [],
        },
    )
    node_23 = next(item for item in draft["gate_trace"] if item["node_id"] == "2.3")
    node_23["reason"] = "K4低点为9999.0，与当前结构不一致。"
    frame = make_frame()

    result = JsonValidator().validate(
        "stage1",
        json.dumps(draft, ensure_ascii=False),
        kline_frame=frame,
    )

    assert isinstance(result, ValidationError)
    assert result.code == "provider_draft_contract"
    assert "gate_trace.5.reason" in result.invalid_fields
    assert "gate_trace.4.reason" not in result.invalid_fields
    assert "gate_trace[5].reason" in result.message
    assert "gate_trace[4].reason" not in result.message


def test_aggregated_trace_issue_uses_original_provider_draft_index() -> None:
    draft = _stage1_draft()
    draft["gate_trace"].insert(
        0,
        {
            "node_id": "1.1",
            "answer": "是",
            "reason": "模型重复了程序数据预检。",
            "evidence_refs": [],
        },
    )
    node_22 = next(item for item in draft["gate_trace"] if item["node_id"] == "2.2")
    node_22["answer"] = "无法解析的回答"
    draft["market_hypotheses"]["primary"]["thesis"] = (
        "K1低点999999，因此可能支持延续。"
    )

    result = JsonValidator().validate(
        "stage1",
        json.dumps(draft, ensure_ascii=False),
        kline_frame=make_frame(),
    )

    assert isinstance(result, ValidationError)
    issue_paths = {issue["path"] for issue in result.contract_issues}
    assert "gate_trace.4.answer" in issue_paths
    assert "gate_trace.3.answer" not in issue_paths
    assert "gate_trace.4.answer" in result.invalid_fields
    assert "market_hypotheses.primary.thesis" in result.invalid_fields


def _trace_draft(trace: list[dict]) -> list[dict]:
    return [
        {
            "node_id": item["node_id"],
            "answer": item["answer"],
            "reason": item["reason"],
            **({"branch": item["branch"]} if item.get("branch") is not None else {}),
            "evidence_refs": ["PA.WINDOW.K5"],
        }
        for item in trace
    ]


def _stage1_draft() -> dict:
    draft = copy.deepcopy(VALID_STAGE1)
    for key in (
        "cycle_position",
        "alternative_cycle_position",
        "direction",
        "entry_setup",
        "gate_result",
    ):
        draft.pop(key)
    draft["protocol_version"] = "pa_draft_v2"
    draft["diagnosis_confidence_reasoning"] = "主假设证据更完整，但仍保留区间化解释。"
    draft["market_hypotheses"] = {
        "primary": {
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "setup_hint": "buy on pullback",
            "thesis": "多头通道仍在延续。",
            "supporting_fact_refs": ["PA.WINDOW.K5", "PA.BAR.K1"],
            "challenging_fact_refs": ["PA.BAR.K2"],
            "theory_rule_ids": ["BROOKS.CHANNEL.CONTEXT"],
            "confirmation_condition": "下一根已收盘K线保持推进。",
            "disconfirmation_condition": "通道结构被明确破坏。",
        },
        "alternatives": [
            {
                "cycle_position": "trading_range",
                "direction": "neutral",
                "setup_hint": "wait for range edge",
                "thesis": "近期推进也可能只是区间摆动。",
                "supporting_fact_refs": ["PA.WINDOW.K5"],
                "challenging_fact_refs": ["PA.BAR.K1"],
                "theory_rule_ids": ["BROOKS.CHANNEL.CONTEXT"],
                "confirmation_condition": "后续收盘继续重叠。",
                "disconfirmation_condition": "出现持续趋势跟随。",
            }
        ],
    }
    draft["pa_evidence"] = {"spike_analysis": None}
    draft["gate_trace"] = _trace_draft(VALID_STAGE1["gate_trace"])
    return draft


def test_stage1_draft_materializes_legacy_view_without_inventing_hypotheses() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    draft = _stage1_draft()

    jsonschema.Draft7Validator(STAGE1_DRAFT_SCHEMA).validate(draft)
    result = materialize_stage1_draft(draft, catalog=catalog)
    jsonschema.Draft7Validator(STAGE1_SCHEMA).validate(result)

    assert result["cycle_position"] == "normal_channel"
    assert result["direction"] == "bullish"
    assert result["alternative_cycle_position"] == "trading_range"
    assert result["entry_setup"] == "buy on pullback"
    assert result["gate_result"] == "proceed"
    assert result["diagnosis_confidence_reasoning"].startswith("主假设")
    assert result["market_hypotheses"]["primary"]["hypothesis_id"] == "pa-primary"
    assert result["market_hypotheses"]["alternatives"][0]["direction"] == "neutral"
    assert result["gate_trace"][0]["question"]
    assert result["gate_trace"][0]["bar_range"] == "K5-K1"
    assert result["pa_evidence"]["fact_refs"] == [
        "PA.WINDOW.K5",
        "PA.BAR.K1",
        "PA.BAR.K2",
    ]


def test_materializer_uses_the_explicit_decision_tree_snapshot(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    source = (
        root / "prompt_engineering" / "\u4e8c\u5143\u51b3\u7b56.txt"
    ).read_text("utf-8")
    original = "### 1.2 \u662f\u5426\u80fd\u8bc6\u522b\u5e02\u573a\u5468\u671f\uff1f"
    replacement = "### 1.2 \u81ea\u5b9a\u4e49\u5e02\u573a\u72b6\u6001\u95ee\u9898\uff1f"
    assert original in source
    tree_path = tmp_path / "\u4e8c\u5143\u51b3\u7b56.txt"
    tree_path.write_text(source.replace(original, replacement, 1), encoding="utf-8")
    frame = make_frame()

    result = materialize_stage1_draft(
        _stage1_draft(),
        catalog=build_analysis_fact_catalog(frame),
        decision_tree_path=tree_path,
    )

    node = next(item for item in result["gate_trace"] if item["node_id"] == "1.2")
    assert node["question"] == (
        "\u81ea\u5b9a\u4e49\u5e02\u573a\u72b6\u6001\u95ee\u9898\uff1f"
    )


def test_provider_schema_digest_uses_the_explicit_decision_tree_snapshot(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    source = (
        root / "prompt_engineering" / "\u4e8c\u5143\u51b3\u7b56.txt"
    ).read_text("utf-8")
    custom_path = tmp_path / "\u4e8c\u5143\u51b3\u7b56.txt"
    custom_path.write_text(
        source.replace("### 11.4", "### 11.5", 1),
        encoding="utf-8",
    )

    default_digest = provider_draft_schema_digest("stage2")
    custom_digest = provider_draft_schema_digest(
        "stage2",
        decision_tree_path=custom_path,
    )

    assert custom_digest != default_digest
    custom_nodes = schema_for_provider_draft(
        "stage2",
        decision_tree_path=custom_path,
    )["properties"]["decision_trace"]["items"]["properties"]["node_id"]["enum"]
    assert "11.5" in custom_nodes
    assert "11.4" not in custom_nodes


def test_stage1_materializer_owns_preflight_and_accepts_non_pa_context_nodes() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    draft = _stage1_draft()
    draft["gate_trace"].insert(
        0,
        {
            "node_id": "1.1",
            "answer": "是",
            "reason": "模型重复了程序数据预检。",
            "evidence_refs": [],
        },
    )
    htf_node = next(
        item for item in draft["gate_trace"] if item["node_id"] == "2.2"
    )
    htf_node["evidence_refs"] = []

    result = materialize_stage1_draft(draft, catalog=catalog)

    assert "1.1" not in {item["node_id"] for item in result["gate_trace"]}
    materialized_htf = next(
        item for item in result["gate_trace"] if item["node_id"] == "2.2"
    )
    assert materialized_htf["bar_range"] == "不适用"


def test_stage1_gate_result_is_protocol_materialization() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    draft = _stage1_draft()
    draft["market_hypotheses"]["primary"]["cycle_position"] = "unknown"
    draft["gate_trace"] = [
        {
            "node_id": "1.2",
            "answer": "否",
            "reason": "无法稳定识别周期。",
            "evidence_refs": ["PA.WINDOW.K20"],
        }
    ]

    result = materialize_stage1_draft(draft, catalog=catalog)

    assert result["gate_result"] == "unknown"


def test_stage2_draft_inherits_diagnosis_and_materializes_math_trace_terminal() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    stage1 = materialize_stage1_draft(_stage1_draft(), catalog=catalog)
    draft = copy.deepcopy(VALID_STAGE2)
    draft["protocol_version"] = "pa_draft_v2"
    draft["decision_intent"] = "trade"
    draft.pop("terminal")
    draft.pop("next_cycle_prediction")
    draft["decision"].pop("diagnosis_confidence")
    draft["decision"].pop("diagnosis_confidence_reasoning")
    draft["decision_trace"] = [
        item
        for item in _trace_draft(VALID_STAGE2["decision_trace"])
        if item["node_id"] != "10.3"
    ]

    jsonschema.Draft7Validator(STAGE2_DRAFT_SCHEMA).validate(draft)
    result = materialize_stage2_draft(
        draft,
        stage1=stage1,
        catalog=catalog,
    )
    jsonschema.Draft7Validator(STAGE2_SCHEMA).validate(result)

    assert result["decision"]["diagnosis_confidence"] == 75
    assert result["decision"]["diagnosis_confidence_reasoning"].startswith("主假设")
    equation = next(
        item for item in result["decision_trace"] if item["node_id"] == "10.3"
    )
    assert equation["answer"] == "是"
    assert "risk=" in equation["reason"]
    assert equation["evidence_refs"] == ["PA.BAR.K2", "PA.BAR.K1"]
    assert equation["bar_range"] == "K2-K1"
    assert result["terminal"]["outcome"] == "trade"
    assert result["next_cycle_prediction"]["unpredictable"] is True


def test_stage2_provider_draft_accepts_an_explicitly_absent_tp2() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    stage1 = materialize_stage1_draft(_stage1_draft(), catalog=catalog)
    draft = copy.deepcopy(VALID_STAGE2)
    draft["protocol_version"] = "pa_draft_v2"
    draft["decision_intent"] = "trade"
    draft.pop("terminal")
    draft.pop("next_cycle_prediction")
    draft["decision"].pop("diagnosis_confidence")
    draft["decision"].pop("diagnosis_confidence_reasoning")
    draft["decision"]["take_profit_price_2"] = None
    draft["decision_trace"] = [
        item
        for item in _trace_draft(VALID_STAGE2["decision_trace"])
        if item["node_id"] != "10.3"
    ]

    jsonschema.Draft7Validator(STAGE2_DRAFT_SCHEMA).validate(draft)
    result = materialize_stage2_draft(draft, stage1=stage1, catalog=catalog)

    assert result["decision"]["take_profit_price_2"] is None
    jsonschema.Draft7Validator(STAGE2_SCHEMA).validate(result)


def test_stage2_contract_reports_invalid_section_node_and_missing_prohibition_scan() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    stage1 = materialize_stage1_draft(_stage1_draft(), catalog=catalog)
    draft = copy.deepcopy(VALID_STAGE2)
    draft["protocol_version"] = "pa_draft_v2"
    draft["decision_intent"] = "trade"
    draft.pop("terminal")
    draft.pop("next_cycle_prediction")
    draft["decision"].pop("diagnosis_confidence")
    draft["decision"].pop("diagnosis_confidence_reasoning")
    draft["decision_trace"] = [
        item
        for item in _trace_draft(VALID_STAGE2["decision_trace"])
        if item["node_id"] not in {"10.3", "14"}
    ]
    next(
        item for item in draft["decision_trace"] if item["node_id"] == "11.2"
    )["node_id"] = "11"

    result = JsonValidator().validate(
        "stage2",
        json.dumps(draft, ensure_ascii=False),
        kline_frame=frame,
        stage1_json=stage1,
        analysis_fact_catalog=catalog,
    )

    assert isinstance(result, ValidationError)
    assert result.code == "provider_draft_contract"
    assert "decision_trace.6.node_id" in result.invalid_fields
    assert "decision_trace" in result.invalid_fields
    assert "11" not in result.allowed_values["decision_trace.6.node_id"]
    assert "11.1" in result.allowed_values["decision_trace.6.node_id"]


def test_stage1_provider_owned_materialization_failures_are_contract_issues() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)

    unknown_evidence = _stage1_draft()
    unknown_evidence["gate_trace"][0]["evidence_refs"] = ["PA.NOT_REAL"]
    unknown_result = JsonValidator().validate(
        "stage1",
        json.dumps(unknown_evidence, ensure_ascii=False),
        kline_frame=frame,
        analysis_fact_catalog=catalog,
    )

    incomplete_path = _stage1_draft()
    incomplete_path["gate_trace"] = [
        item for item in incomplete_path["gate_trace"] if item["node_id"] != "2.5"
    ]
    incomplete_result = JsonValidator().validate(
        "stage1",
        json.dumps(incomplete_path, ensure_ascii=False),
        kline_frame=frame,
        analysis_fact_catalog=catalog,
    )

    assert isinstance(unknown_result, ValidationError)
    assert unknown_result.code == "provider_draft_contract"
    assert "gate_trace.0.evidence_refs.0" in unknown_result.invalid_fields
    assert isinstance(incomplete_result, ValidationError)
    assert incomplete_result.code == "provider_draft_contract"
    assert "gate_trace" in incomplete_result.invalid_fields


def test_no_order_draft_accepts_empty_probability_reason_and_full_reasoning() -> None:
    frame = make_frame()
    draft = copy.deepcopy(VALID_STAGE2_NO_ORDER)
    draft["protocol_version"] = "pa_draft_v2"
    draft["decision_intent"] = "wait"
    draft.pop("terminal")
    draft.pop("next_cycle_prediction")
    draft["decision"].pop("diagnosis_confidence")
    draft["decision"].pop("diagnosis_confidence_reasoning")
    draft["decision"]["estimated_win_rate_reasoning"] = ""
    draft["decision"]["reasoning"] = "等待结构确认。" * 50
    draft["decision_trace"] = [
        {
            "node_id": "9.0",
            "answer": "否",
            "reason": "当前没有合格信号棒。",
            "evidence_refs": ["PA.BAR.K1"],
        },
        {
            "node_id": "9.0P",
            "answer": "否",
            "reason": "当前没有可执行的背景限价计划。",
            "evidence_refs": ["PA.WINDOW.K5"],
        },
        {
            "node_id": "3.1",
            "answer": "否",
            "reason": "当前不按尖峰分支处理。",
            "evidence_refs": ["PA.WINDOW.K20"],
        },
        {
            "node_id": "14",
            "answer": "否",
            "reason": "没有触犯禁止行为，但当前没有入场依据。",
            "evidence_refs": [],
        },
    ]

    result = JsonValidator().validate(
        "stage2",
        json.dumps(draft, ensure_ascii=False),
        kline_frame=frame,
        stage1_json=VALID_STAGE1,
    )

    assert isinstance(result, Ok)
    assert result.obj["decision"]["estimated_win_rate_reasoning"] is None
    assert len(result.obj["decision"]["reasoning"]) > 280
    assert [item["node_id"] for item in result.obj["decision_trace"]] == [
        "3.1",
        "9.0",
        "9.0P",
        "14",
    ]


def test_no_order_null_trade_confidence_reason_is_normalized_without_retry() -> None:
    frame = make_frame()
    draft = copy.deepcopy(VALID_STAGE2_NO_ORDER)
    draft["protocol_version"] = "pa_draft_v2"
    draft["decision_intent"] = "wait"
    draft.pop("terminal")
    draft.pop("next_cycle_prediction")
    draft["decision"].pop("diagnosis_confidence")
    draft["decision"].pop("diagnosis_confidence_reasoning")
    draft["decision"]["trade_confidence_reasoning"] = None
    draft["decision_trace"] = [
        {
            "node_id": "9.0",
            "answer": "否",
            "reason": "当前没有合格信号棒。",
            "evidence_refs": ["PA.BAR.K1"],
        },
        {
            "node_id": "9.0P",
            "answer": "否",
            "reason": "当前没有可执行的背景限价计划。",
            "evidence_refs": ["PA.WINDOW.K5"],
        },
    ]

    result = JsonValidator().validate(
        "stage2",
        json.dumps(draft, ensure_ascii=False),
        kline_frame=frame,
        stage1_json=VALID_STAGE1,
    )

    assert isinstance(result, Ok)
    assert result.obj["decision"]["trade_confidence_reasoning"] == ""
    event = next(
        item
        for item in result.normalization_events
        if item["path"] == "decision.trade_confidence_reasoning"
    )
    assert event == {
        "path": "decision.trade_confidence_reasoning",
        "before": None,
        "after": "",
    }


def test_executable_order_null_trade_confidence_reason_is_not_normalized() -> None:
    draft = copy.deepcopy(VALID_STAGE2)
    draft["decision"]["trade_confidence_reasoning"] = None

    normalized = normalize_provider_draft("stage2", draft)

    assert normalized["decision"]["trade_confidence_reasoning"] is None


def test_validator_negotiates_draft_schema_and_returns_canonical_stage1() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    validator = JsonValidator()

    assert validator.schema_for("stage1")["properties"]["protocol_version"]["const"] == "pa_draft_v2"
    result = validator.validate(
        "stage1", json.dumps(_stage1_draft(), ensure_ascii=False),
        kline_frame=frame, analysis_fact_catalog=catalog,
    )

    assert isinstance(result, Ok)
    assert result.obj["gate_result"] == "proceed"
    assert result.obj["market_hypotheses"]["alternatives"][0]["direction"] == "neutral"


@pytest.mark.parametrize("intent", ["wait", "reject"])
def test_no_order_draft_does_not_invent_trader_equation(intent: str) -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    draft = copy.deepcopy(VALID_STAGE2)
    draft["protocol_version"] = "pa_draft_v2"
    draft["decision_intent"] = intent
    draft.pop("terminal")
    draft.pop("next_cycle_prediction")
    draft["decision"].pop("diagnosis_confidence")
    draft["decision"].pop("diagnosis_confidence_reasoning")
    draft["decision"].update({
        "order_type": "不下单", "order_direction": None, "entry_price": None,
        "entry_basis_bar": None, "entry_basis_extreme": None, "entry_rule": None,
        "take_profit_price": None, "take_profit_price_2": None, "stop_loss_price": None,
        "trade_confidence": None, "estimated_win_rate": None,
        "estimated_win_rate_reasoning": None, "invalidation_condition": None,
    })
    draft["decision_trace"] = [
        {"node_id": "9.0", "answer": "否", "reason": "no closed signal", "evidence_refs": ["PA.BAR.K1"]},
        {"node_id": "9.0P", "answer": "否", "reason": "no passive plan", "evidence_refs": ["PA.WINDOW.K5"]},
    ]

    result = materialize_stage2_draft(draft, stage1=materialize_stage1_draft(_stage1_draft(), catalog=catalog), catalog=catalog)

    assert all(item["node_id"] != "10.3" for item in result["decision_trace"])
    assert result["terminal"] == {
        "node_id": "9.0P",
        "outcome": intent,
        "label": f"LLM {intent} intent",
    }
    jsonschema.Draft7Validator(STAGE2_SCHEMA).validate(result)


def test_trader_equation_uses_declared_trace_evidence_when_no_bar_is_named() -> None:
    frame = make_frame()
    catalog = build_analysis_fact_catalog(frame)
    stage1 = materialize_stage1_draft(_stage1_draft(), catalog=catalog)
    draft = copy.deepcopy(VALID_STAGE2)
    draft["protocol_version"] = "pa_draft_v2"
    draft["decision_intent"] = "trade"
    draft.pop("terminal")
    draft.pop("next_cycle_prediction")
    draft.pop("gate_shortcircuited", None)
    draft["decision"].pop("diagnosis_confidence")
    draft["decision"].pop("diagnosis_confidence_reasoning")
    draft["decision"]["entry_basis_bar"] = None
    draft["bar_analysis"]["signal_bar"]["bar"] = None
    draft["bar_analysis"]["entry_bar"]["bar"] = None
    draft["decision_trace"] = [
        {
            "node_id": "9.0",
            "answer": "是",
            "reason": "结构候选仍成立。",
            "evidence_refs": ["PA.WINDOW.K5"],
        }
    ]

    result = materialize_stage2_draft(draft, stage1=stage1, catalog=catalog)

    equation = next(
        item for item in result["decision_trace"] if item["node_id"] == "10.3"
    )
    assert equation["evidence_refs"] == ["PA.WINDOW.K5"]
    assert result["gate_shortcircuited"] is False
