"""Order readiness is shared guidance; pending remains a causal schema state."""

import copy
import re

import jsonschema
import pytest

from pa_agent.ai.json_validator import JsonValidator
from pa_agent.ai.pa_provider_contracts import schema_for_joint_stage2_draft
from pa_agent.config.paths import PROMPT_DIR
from tests.unit.test_joint_pipeline_runtime import joint_draft, run_joint


def test_documented_no_signal_qualities_are_accepted_by_actual_provider_schema():
    text = (PROMPT_DIR / "二元决策.txt").read_text(encoding="utf-8")
    qualities = re.search(r"`signal_bar.bar=null`，`quality=([^`]+)`", text)[1].split("/")
    validator = jsonschema.Draft7Validator(schema_for_joint_stage2_draft())
    for quality in qualities:
        draft = joint_draft()
        draft["bar_analysis"]["signal_bar"].update(bar=None, quality=quality, pattern="none")
        errors = [e.message for e in validator.iter_errors(draft) if "signal_bar" in e.path]
        assert errors == [], (quality, errors)


@pytest.mark.parametrize("order_type", ["限价单", "突破单"])
@pytest.mark.parametrize("signal", ["K1", "K2"])
def test_pending_order_does_not_require_an_entry_bar_or_future_follow_through(order_type, signal):
    draft = joint_draft()
    draft["decision"]["order_type"] = order_type
    draft["bar_analysis"]["signal_bar"]["bar"] = signal
    draft["bar_analysis"]["entry_bar"] = {
        "bar": None, "strength": "not_triggered", "freshness": "pending",
        "follow_through": None, "still_valid": None,
    }
    assert JsonValidator._check_signal_chain(draft) == []
    entry_schema = schema_for_joint_stage2_draft()["properties"]["bar_analysis"]["properties"]["entry_bar"]
    jsonschema.Draft7Validator(entry_schema).validate(draft["bar_analysis"]["entry_bar"])
    market = copy.deepcopy(draft)
    market["decision"]["order_type"] = "市价单"
    assert JsonValidator._check_signal_chain(market)


@pytest.mark.parametrize("codex", [False, True])
def test_actual_direct_and_codex_joint_share_order_readiness_contract(codex):
    from pa_agent.ai.prompt_assembler import _stage2_output_rules

    requests = []
    record, calls, _ = run_joint(codex=codex, captured_requests=requests)
    assert record.exception is None
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    shared = _stage2_output_rules(joint_stage2=True)
    assert "`trade` 表示当前已具备提交订单的条件" in shared
    assert "未来新增已收盘证据" in shared
    assert "`entry_rule` 是说明文字" in shared
    if codex:
        request = next(r for r in requests if r.role == "stage2")
        delivered = "\n".join(request.agent_context["role_protocol"]["embedded_rules"])
        expected = shared.replace("只输出裸 JSON。", "按 Host 输出模式提交当前 Draft，不输出额外文字。")
        expected = expected.replace("紧随其后的自动生成 Provider Contract", "core_context.provider_output_schema")
        assert expected in delivered
    else:
        assert shared in "\n".join(m["content"] for m in record.stage2_messages)


@pytest.mark.parametrize("order_type", ["限价单", "突破单"])
@pytest.mark.parametrize("fixed_rr", [False, True])
def test_full_joint_accepts_pending_with_trace_not_applicable_under_both_target_policies(order_type, fixed_rr):
    from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy

    draft = joint_draft()
    draft["decision"]["order_type"] = order_type
    draft["bar_analysis"]["entry_bar"] = {
        "bar": None, "strength": "not_triggered", "freshness": "pending",
        "follow_through": None, "still_valid": None,
    }
    index = next(i for i, item in enumerate(draft["decision_trace"]) if item["node_id"] == "10.1")
    draft["decision_trace"].insert(index, {
        "node_id": "9.7", "answer": "不适用",
        "reason": "等待价格触发，尚无入场棒。", "evidence_refs": ["PA.BAR.K2"],
    })
    policy = FixedRiskRewardPolicy(reward_risk_ratio="1.5", tick_size="0.1") if fixed_rr else None
    original_target = draft["decision"]["take_profit_price"]
    if policy:
        draft["decision"].pop("take_profit_price")
        draft["decision"].pop("take_profit_price_2")
    record, calls, _ = run_joint(stage2_drafts=[draft], take_profit_policy=policy)
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    canonical = record.stage2_decision
    assert canonical["terminal"]["outcome"] == "trade"
    assert next(item for item in canonical["decision_trace"] if item["node_id"] == "9.7")["answer"] == "不适用"
    assert canonical["bar_analysis"]["entry_bar"]["follow_through"] is None
    decision = canonical["decision"]
    expected_target = (float(policy.target(decision["entry_price"], decision["stop_loss_price"], decision["order_direction"]))
                       if policy else original_target)
    assert decision["take_profit_price"] == expected_target
