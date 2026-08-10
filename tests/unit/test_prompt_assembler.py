"""Unit tests for PromptAssembler (task 7.3)."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
from pa_agent.ai.prompt_assembler import VPA_KNOWLEDGE_MODULES, PromptAssembler
from pa_agent.ai.pa_provider_contracts import STAGE1_DRAFT_SCHEMA, STAGE2_DRAFT_SCHEMA
from pa_agent.ai.prompts.schemas import STAGE1_SCHEMA, STAGE2_SCHEMA
from pa_agent.ai.role_contracts import JOINT_DECISION_SCHEMA, VPA_EVIDENCE_SCHEMA
from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from pa_agent.config.settings import PromptSettings
from pa_agent.config.paths import PROMPT_DIR
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame


def _make_frame(n: int = 5) -> KlineFrame:
    bars = tuple(
        KlineBar(
            seq=i + 1,
            ts_open=float(1_700_000_000 - i * 3600),
            open=2600.0 + i,
            high=2610.0 + i,
            low=2590.0 + i,
            close=2605.0 + i,
            volume=1000.0,
            closed=(i != 0),
        )
        for i in range(n)
    )
    indicators = IndicatorBundle(
        ema20=tuple(2600.0 + i for i in range(n)),
        atr14=tuple(5.0 for _ in range(n)),
    )
    return KlineFrame(
        symbol="XAUUSD",
        timeframe="1h",
        bars=bars,
        indicators=indicators,
        snapshot_ts_local_ms=1_700_000_000_000,
    )


def _required_schema_keys(schema: dict) -> set[str]:
    """Collect required keys reachable only through required object fields."""
    keys: set[str] = set()
    properties = schema.get("properties", {})
    for key in schema.get("required", []):
        keys.add(key)
        child = properties.get(key)
        if isinstance(child, dict):
            keys.update(_required_schema_keys(child))
    return keys


def _schema_contract_tokens(schema: dict) -> set[str]:
    """Collect provider-owned required keys and enums through local refs."""
    tokens: set[str] = set()

    def visit(node: dict) -> None:
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = schema.get("$defs", {}).get(ref.rsplit("/", 1)[-1])
            if isinstance(target, dict):
                visit(target)
            return
        for value in node.get("enum", []):
            if isinstance(value, str):
                tokens.add(value)
        properties = node.get("properties", {})
        for key in node.get("required", []):
            tokens.add(str(key))
            child = properties.get(key)
            if isinstance(child, dict):
                visit(child)
        items = node.get("items")
        if isinstance(items, dict):
            visit(items)
        for branch in node.get("anyOf", []):
            if isinstance(branch, dict):
                visit(branch)

    visit(schema)
    return tokens


@pytest.fixture()
def assembler(tmp_path: Path) -> PromptAssembler:
    """PromptAssembler with fake prompt files."""
    for fname in [
        "提示词大纲_人设与思维方式.txt",
        "市场诊断框架.txt",
        "二元决策.txt",
        "二元决策_闸门.txt",
        "文件16-K线信号识别.txt",
        "逐棒分析检查单.txt",
        "文件17-止损和止盈与仓位管理.txt",
        "文件18-突破失败与突破测试.txt",
        "文件19-H1H2-L1L2计数.txt",
        "文件20-AlwaysIn与20GB.txt",
        "文件21-铁丝网与无交易环境.txt",
        "文件22-信号失败后的磁力位.txt",
        "上涨通道分析识别.txt",
        "上涨通道交易策略.txt",
        "下跌通道分析识别.txt",
        "下跌通道交易策略.txt",
        "极速上涨分析识别.txt",
        "极速上涨交易策略.txt",
        "极速下跌分析识别.txt",
        "极速下跌交易策略.txt",
        "震荡区间分析识别.txt",
        "震荡区间交易策略.txt",
        "文件13-窄通道与宽通道策略.txt",
        "文件14-楔形形态分析交易.txt",
        "文件15-二次入场机会.txt",
        "文件23-MeasuredMove与结构目标.txt",
        "文件24-最终旗形与趋势末端.txt",
        "文件25-主要趋势反转MTR.txt",
        "文件27-三角形与收敛形态.txt",
        "文件28-双重顶底与微型结构.txt",
    ]:
        (tmp_path / fname).write_text(f"[CONTENT OF {fname}]", encoding="utf-8")
    for module, filename in VPA_KNOWLEDGE_MODULES.items():
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "# Wyckoff VPA Core\nIndependent evidence report; "
            "use insufficient_data when evidence is unavailable."
            if module == "core"
            else f"# VPA module {module}"
        )
        path.write_text(content, encoding="utf-8")
    return PromptAssembler(prompt_dir=tmp_path)


def test_stage1_system_prompt_order(assembler: PromptAssembler):
    """Static system stays cacheable; Stage 1 owns decision sections 0-8."""
    frame = _make_frame()
    messages = assembler.build_stage1(frame)
    system = messages[0]["content"]
    user = messages[1]["content"]
    pos_persona = system.find("提示词大纲_人设与思维方式")
    assert pos_persona >= 0
    assert "二元决策" not in system
    assert "市场诊断框架" not in system

    pos_binary_user = user.find("二元决策")
    pos_diag = user.find("市场诊断框架")
    pos_signal = user.find("文件16-K线信号识别")
    assert "是否为尖峰 / 极速行情" not in system
    assert "[CONTENT OF 二元决策.txt]" in user
    assert "[CONTENT OF 二元决策_闸门.txt]" not in system
    assert 0 <= pos_binary_user < pos_diag < pos_signal
    assert "逐棒分析检查单" not in user, "Bar-by-bar checklist is Stage 2 only"
    assert "文件18-突破失败与突破测试" not in user
    assert "文件13-窄通道与宽通道策略" not in user


def test_stage1_user_prompt_contains_required_fields(assembler: PromptAssembler):
    """Stage 1 user prompt must contain symbol, timeframe, bar count."""
    frame = _make_frame()
    messages = assembler.build_stage1(frame)
    user = messages[1]["content"]
    assert "XAUUSD" in user
    assert "1h" in user
    assert "序号" in user
    assert "程序可复算 K线事实" in user
    assert "程序可复算市场事实" in user
    assert "Evidence first" in user
    assert "更高时间框架" not in user


def test_pa_stage1_prompt_is_price_only_and_uses_fact_catalog(
    assembler: PromptAssembler,
) -> None:
    user = assembler.build_stage1(_make_frame())[1]["content"]

    assert "ANALYSIS_FACT_CATALOG" in user
    assert "PA.WINDOW.K4" in user
    assert "Evidence first" in user
    assert "成交量" not in user
    assert "| volume" not in user.lower()
    assert "| 1000" not in user
    assert "ANALYSIS_FACT_CATALOG" in user


def test_prompt_assembler_reuses_injected_fact_catalog(monkeypatch) -> None:
    frame = _make_frame()
    catalog = build_analysis_fact_catalog(frame)
    assembler = PromptAssembler(PROMPT_DIR, analysis_fact_catalog=catalog)

    def fail_rebuild(_frame):
        raise AssertionError("fact catalog must be built once per frozen snapshot")

    monkeypatch.setattr(
        "pa_agent.ai.prompt_assembler.build_analysis_fact_catalog",
        fail_rebuild,
    )

    stage1 = assembler.build_stage1(frame)
    stage2 = assembler.build_stage2_continuation(
        frame=frame,
        stage1_messages=stage1,
        stage1_reply_content="{}",
        stage1_json={"direction": "neutral", "gate_result": "proceed"},
        strategy_files=[],
        experience_entries=[],
        use_prefix_chain=True,
    )

    assert "ANALYSIS_FACT_CATALOG" in stage1[1]["content"]
    assert stage2


def test_stage2_user_prompt_includes_gate_trace(assembler: PromptAssembler):
    frame = _make_frame()
    stage1_json = {
        "cycle_position": "normal_channel",
        "direction": "bullish",
        "gate_result": "proceed",
        "gate_trace": [{"node_id": "0.1", "question": "q", "answer": "是", "reason": "r"}],
    }
    messages = assembler.build_stage2(frame, stage1_json, [], [])
    user = messages[1]["content"]
    # gate_trace and gate_result are embedded inside the compact stage1 JSON block
    assert "gate_result" in user
    assert "gate_trace" in user or "0.1" in user
    # The redundant separate gate_block section should no longer exist
    assert "## 阶段一闸门路径" not in user


def test_stage2_system_prompt_order(assembler: PromptAssembler):
    """Stage 2 adds only its execution tree to the user task."""
    frame = _make_frame()
    stage1_json = {"cycle_position": "normal_channel", "direction": "bullish"}
    strategy_files = ["上涨通道分析识别.txt", "上涨通道交易策略.txt", "文件13-窄通道与宽通道策略.txt"]
    messages = assembler.build_stage2(frame, stage1_json, strategy_files, [])
    system = messages[0]["content"]
    user = messages[1]["content"]

    pos_persona = system.find("提示词大纲_人设与思维方式")
    assert pos_persona >= 0
    assert "二元决策" not in system

    assert "[CONTENT OF 二元决策.txt]" in user
    pos_strategy = user.find("上涨通道分析识别")
    pos_bar_by_bar = user.find("逐棒分析检查单")
    pos_signal = user.find("文件16-K线信号识别")
    pos_risk = user.find("文件17-止损和止盈与仓位管理")
    assert 0 <= pos_strategy < pos_risk, "Stage 2 user task files are out of order"
    assert 0 <= pos_bar_by_bar < pos_signal < pos_risk


def test_stage2_user_prompt_contains_stage1_json(assembler: PromptAssembler):
    """Stage 2 user prompt must embed the Stage 1 JSON."""
    frame = _make_frame()
    stage1_json = {"cycle_position": "spike", "direction": "bearish"}
    messages = assembler.build_stage2(frame, stage1_json, [], [])
    user = messages[1]["content"]
    assert "spike" in user
    assert "bearish" in user


def test_stage2_prompt_does_not_assign_quality_without_a_signal_bar(
    assembler: PromptAssembler,
) -> None:
    user = assembler.build_stage2(
        _make_frame(),
        {"cycle_position": "normal_channel", "direction": "bullish"},
        [],
        [],
    )[1]["content"]

    assert "Stage 2 Provider Draft v2" in user
    assert "order_type=不下单" in user
    assert "`quality=invalid` 或 **weak**" not in user


def test_stage2_user_prompt_uses_routed_strategy_only_by_default(
    assembler: PromptAssembler,
):
    """Default Stage 2 loads router output + base files, not the full strategy pack."""
    frame = _make_frame()
    routed = ["上涨通道分析识别.txt", "上涨通道交易策略.txt"]
    messages = assembler.build_stage2(
        frame,
        {"cycle_position": "normal_channel", "direction": "bullish", "gate_result": "proceed"},
        routed,
        [],
    )
    user = messages[1]["content"]
    assert "上涨通道分析识别" in user
    assert "文件17-止损和止盈与仓位管理" in user
    assert "下跌通道分析识别" not in user
    assert "极速下跌分析识别" not in user


def test_stage1_output_reminder_present(assembler: PromptAssembler):
    """Stage 1 user turn must contain the output format reminder."""
    frame = _make_frame()
    messages = assembler.build_stage1(frame)
    user = messages[1]["content"]
    assert "cycle_position" in user
    assert "diagnosis_confidence" in user
    assert "bar_by_bar_summary" in user
    assert "protocol_version" in user
    assert "market_hypotheses" in user
    assert "supporting_fact_refs" in user
    assert "challenging_fact_refs" in user
    assert "detected_patterns" in user
    assert "gate_trace" in user
    assert "不要输出 `question/section/bar_range`" in user
    assert "禁止输出 10.3、14 或 `gate_result`" in user


def test_required_schema_keys_are_visible_in_role_prompts(
    assembler: PromptAssembler,
) -> None:
    frame = _make_frame()
    stage1_prompt = "\n".join(
        message["content"] for message in assembler.build_stage1(frame)
    )
    stage2_prompt = "\n".join(
        message["content"]
        for message in assembler.build_stage2(frame, {}, [], [])
    )

    assert sorted(
        key for key in _required_schema_keys(STAGE1_DRAFT_SCHEMA) if key not in stage1_prompt
    ) == []
    assert sorted(
        key for key in _required_schema_keys(STAGE2_DRAFT_SCHEMA) if key not in stage2_prompt
    ) == []


def test_stage1_bar_type_schema_matches_contextual_prompt_contract() -> None:
    allowed = set(
        STAGE1_SCHEMA["properties"]["bar_analysis"]["properties"]["bar_type"][
            "enum"
        ]
    )
    summary_allowed = set(
        STAGE1_SCHEMA["properties"]["bar_by_bar_summary"]["items"]["properties"][
            "bar_type"
        ]["enum"]
    )

    assert allowed == {"trend_bull", "trend_bear", "doji", "flat", "other"}
    assert summary_allowed == allowed


def test_vpa_and_joint_prompts_cover_provider_schema_contracts(
    assembler: PromptAssembler,
) -> None:
    frame = _make_frame()
    vpa_assembler = assembler.clone_with_prompt_settings(
        PromptSettings(vpa_mode="shadow")
    )
    baseline = {
        "cycle_position": "trading_range",
        "direction": "neutral",
        "gate_result": "proceed",
    }
    hypotheses = {"primary": {"hypothesis_id": "pa-primary"}, "setup_candidates": []}
    vpa_prompt = "\n".join(
        message["content"]
        for message in vpa_assembler.build_vpa_evidence_report(
            frame,
            baseline,
            hypotheses,
        )
    )
    joint_prompt = "\n".join(
        message["content"]
        for message in vpa_assembler.build_joint_decision(
            {"decision": {"order_type": "不下单"}},
            hypotheses,
            {"summary": "neutral", "entry_timing_relation": "neutral"},
        )
    )

    assert sorted(
        token
        for token in _schema_contract_tokens(VPA_EVIDENCE_SCHEMA)
        if token not in vpa_prompt
    ) == []
    assert sorted(
        token
        for token in _schema_contract_tokens(JOINT_DECISION_SCHEMA)
        if token not in joint_prompt
    ) == []
    assert "无需仅为格式在父级 `evidence_ids` 重复列出" in vpa_prompt


def test_stage1_original_mode_keeps_llm_owned_gate_semantics(assembler: PromptAssembler):
    """Original mode does not restore program-owned market judges."""
    frame = _make_frame()
    messages = assembler.build_stage1(frame, analysis_mode="original")
    user = messages[1]["content"]

    assert "原始分析过程闸门硬规则" not in user
    assert "PA 负责判断 Background" in user
    assert "程序只提供可复算事实" in user
    assert "完整语义 gate_trace" in user


def test_vpa_evidence_role_loads_modules_from_frozen_brooks_context(
    assembler: PromptAssembler,
) -> None:
    vpa_assembler = assembler.clone_with_prompt_settings(
        PromptSettings(vpa_mode="shadow")
    )
    baseline = {
        "cycle_position": "spike",
        "alternative_cycle_position": "trending_tr",
        "direction": "bearish",
        "spike_stage": "ending",
        "climax_risk": "warning",
        "detected_patterns": ["breakout_test"],
        "gate_result": "proceed",
    }

    messages = vpa_assembler.build_vpa_evidence_report(
        _make_frame(25),
        baseline,
        {"primary": {"hypothesis_id": "pa-primary"}, "setup_candidates": []},
    )
    prompt = "\n".join(message["content"] for message in messages)

    assert "Loaded modules: core, trend_pullback, breakout_retest, trading_range_events, climax_absorption_exhaustion" in prompt
    assert "VPA_EVIDENCE_ANALYSIS_TASK" in prompt
    assert "不要输出 `bar_range`" in prompt
    assert '"interpretation_label"' in prompt
    assert '"epistemic_status"' in prompt
    assert '"sequence_claims"' in prompt
    observation_block = prompt[prompt.index('"observations"'):prompt.index('"hypothesis_assessments"')]
    assert observation_block.index('"evidence_ids"') < observation_block.index('"interpretation"')
    assert observation_block.index('"epistemic_status"') < observation_block.index('"interpretation_label"')
    assert "针对该真实 Entry Attempt" in prompt
    assert "各自 `bar_range` 不得重叠" in prompt
    assert "VPA_ANALYSIS_ENVELOPE" in prompt
    assert "### ENTRY_ATTEMPT_CONTEXT" not in prompt
    assert "### FROZEN_PA_CONTEXT" not in prompt
    assert '"applicable": false' in prompt
    assert "entry_timing_relation=insufficient_data" in prompt


def test_vpa_module_profile_uses_only_explicit_pa_context() -> None:
    from pa_agent.ai.vpa_context_profile import build_vpa_knowledge_context

    explicit = build_vpa_knowledge_context(
        {
            "cycle_position": "spike",
            "detected_patterns": ["H2", "breakout_pullback"],
        }
    )
    absent = build_vpa_knowledge_context(
        {"cycle_position": "spike", "detected_patterns": ["unknown_label"]}
    )

    assert explicit.module_names == ("core", "trend_pullback", "breakout_retest")
    assert absent.module_names == ("core",)


def test_runtime_vpa_core_uses_current_dual_relation_contract() -> None:
    core = (
        Path(__file__).resolve().parents[2]
        / "prompt_engineering"
        / "wyckoff_vpa"
        / "core.txt"
    ).read_text(encoding="utf-8")

    assert "`data_status`" in core
    assert "`background_relation`" in core
    assert "`entry_timing_relation`" in core
    assert "`finding_kind`" in core
    assert "`epistemic_status`" in core
    assert "`sequence_claims`" in core
    assert "The program derives `requires_closed_bar`" in core
    assert "with exactly: `alignment`" not in core


def test_bundled_pa_knowledge_is_sliced_by_stage_without_theory_loss() -> None:
    prompt_dir = Path(__file__).resolve().parents[2] / "prompt_engineering"
    real = PromptAssembler(prompt_dir=prompt_dir)
    frame = _make_frame(25)

    stage1_user = real.build_stage1(frame)[1]["content"]
    stage2_user = real.build_stage2(
        frame,
        {"cycle_position": "normal_channel", "direction": "bullish"},
        [],
        [],
    )[1]["content"]

    assert "## 8. 是否出现楔形 / 三推" in stage1_user
    assert "## 9. 入场信号二元检查" not in stage1_user
    assert "## 第三层：逐K信号确认" not in stage1_user
    assert "## 9. 入场信号二元检查" in stage2_user
    assert "## 15. 最终执行口诀" in stage2_user
    assert "## 1. 是否可以决策" not in stage2_user


def test_detailed_geometry_is_limited_but_raw_history_remains_complete() -> None:
    frame = _make_frame(25)
    detailed = PromptAssembler._render_kline_feature_table(
        frame,
        limit=10,
    )
    raw = PromptAssembler._render_kline_table(frame)

    assert any(line.strip().startswith("10 ") for line in detailed.splitlines())
    assert not any(line.strip().startswith("11 ") for line in detailed.splitlines())
    assert any(line.strip().startswith("25 ") for line in raw.splitlines())


def test_joint_prompt_is_focused_and_uses_a_bounded_patch(
    assembler: PromptAssembler,
) -> None:
    enabled = assembler.clone_with_prompt_settings(
        PromptSettings(vpa_mode="shadow")
    )
    messages = enabled.build_joint_decision(
        {"decision": {"order_type": "限价单"}, "decision_trace": []},
        {"primary": {"hypothesis_id": "pa-primary"}, "setup_candidates": []},
        {
            "summary": "Independent timing evidence.",
            "entry_timing_relation": "challenges",
        },
    )
    prompt = "\n".join(item["content"] for item in messages)

    assert [item["role"] for item in messages] == ["system", "user"]
    assert '"decision_patch"' in prompt
    assert '"stage2_decision"' not in prompt
    assert "K线数据" not in prompt
    assert "ANALYSIS_FACT_CATALOG" not in prompt


def test_joint_prompt_injects_only_referenced_program_comparators(
    assembler: PromptAssembler,
) -> None:
    catalog = build_vpa_evidence_catalog(_make_frame(25))
    enabled = assembler.clone_with_prompt_settings(
        PromptSettings(vpa_mode="shadow"),
        vpa_evidence_catalog=catalog,
    )
    messages = enabled.build_joint_decision(
        {"decision": {"order_type": "限价单"}, "decision_trace": []},
        {
            "primary": {"hypothesis_id": "pa-primary"},
            "setup_candidates": [{"candidate_id": "pa-setup-1"}],
        },
        {
            "summary": "Independent sequence evidence.",
                "observations": [{"evidence_ids": ["vpa-w005"]}],
            "hypothesis_assessments": [],
            "entry_timing_relation": "neutral",
        },
    )
    prompt = "\n".join(item["content"] for item in messages)

    assert "REFERENCED_PROGRAM_COMPARATORS" in prompt
    assert '"evidence_id": "vpa-w005"' in prompt
    assert '"net_progress_direction_relation": "same"' in prompt
    assert '"evidence_id": "vpa-e001"' not in prompt


def test_stage2_win_rate_is_explicitly_an_uncalibrated_pa_prior(
    assembler: PromptAssembler,
) -> None:
    prompt = assembler.build_stage2(
        _make_frame(20),
        {"cycle_position": "normal_channel", "direction": "bullish"},
        [],
        [],
    )[1]["content"]

    assert "PA 主观胜率" in prompt
    assert "不得为了让方程通过而调整结构价格或胜率" in prompt


def test_stage1_prompt_injects_canonical_brooks_theory_rule_catalog(
    assembler: PromptAssembler,
) -> None:
    prompt = assembler.build_stage1(_make_frame(25))[1]["content"]

    assert "PA_THEORY_RULE_CATALOG" in prompt
    assert "BROOKS.CYCLE.SPIKE_TO_CHANNEL" in prompt
    assert "BROOKS.TRADER_EQUATION" in prompt


def test_vpa_evidence_role_receives_frozen_pa_evidence(
    assembler: PromptAssembler,
) -> None:
    enabled = assembler.clone_with_prompt_settings(PromptSettings(vpa_mode="shadow"))
    baseline = {
        "cycle_position": "normal_channel",
        "direction": "bullish",
        "gate_result": "proceed",
        "pa_evidence": {
            "fact_refs": ["PA.WINDOW.K5"],
            "theory_rule_ids": ["BROOKS.TREND.FOLLOW_THROUGH"],
            "spike_analysis": None,
        },
    }

    prompt = "\n".join(
        message["content"]
        for message in enabled.build_vpa_evidence_report(
            _make_frame(25),
            baseline,
            {"primary": {"hypothesis_id": "pa-primary"}, "setup_candidates": []},
        )
    )

    assert '"pa_evidence"' in prompt
    assert "PA.WINDOW.K5" in prompt


def test_stage1_optimized_mode_keeps_model_owned_gate(assembler: PromptAssembler):
    """Optimized mode still leaves the semantic gate to the model."""
    frame = _make_frame()
    messages = assembler.build_stage1(frame, analysis_mode="optimized")
    user = messages[1]["content"]

    # Hard-rule block must NOT appear in optimized mode
    assert "原始分析过程闸门硬规则" not in user
    assert "程序预填" not in user
    assert "程序投票" not in user
    assert "gate_result" in user


def test_stage2_output_contract_present(assembler: PromptAssembler):
    """Stage 2 user turn must contain the output contract with null rule."""
    frame = _make_frame()
    messages = assembler.build_stage2(frame, {}, [], [])
    user = messages[1]["content"]
    assert "不下单" in user
    assert "order_direction" in user
    assert "bar_analysis" in user
    assert "entry_basis_bar" in user
    assert "Stage 2 Provider Draft v2" in user
    assert "程序可复算 K线事实" in user
    assert "程序复算价格几何" in user
    assert "decision_trace" in user
    assert "不要输出 `question/section/bar_range`、§10.3 或 `terminal`" in user


def test_stage2_pending_entry_and_stop_semantics_are_unambiguous(
    assembler: PromptAssembler,
):
    frame = _make_frame()
    messages = assembler.build_stage2(frame, {}, [], [])
    user = messages[1]["content"]

    assert "bar_analysis" in user
    assert "结构三价" in user
    assert "模型仍需判断 §10.1/§10.2" in user
    assert "follow_through=false` 当作失败跟随，应写 `pending`" not in user


def test_stage2_experience_entries_included(assembler: PromptAssembler):
    """Stage 2 user turn must include experience entries when provided."""
    frame = _make_frame()
    entries = [{"cycle_position": "spike", "outcome": "success"}]
    messages = assembler.build_stage2(frame, {}, [], entries)
    user = messages[1]["content"]
    assert "经验库" in user
    assert "案例 1" in user


def test_stage2_system_prompt_only_matches_build_stage2(assembler: PromptAssembler):
    """stage2_system_prompt_only must return the same system content as build_stage2."""
    frame = _make_frame()
    strategy_files = ["上涨通道分析识别.txt"]
    entries = [{"note": "test"}]
    messages = assembler.build_stage2(frame, {}, strategy_files, entries)
    system_from_build = messages[0]["content"]
    system_only = assembler.stage2_system_prompt_only(strategy_files, entries)
    assert system_from_build == system_only


def test_kline_table_contains_nan_as_na(assembler: PromptAssembler):
    """K-line table renders NaN indicator values as 'N/A'."""
    bars = (
        KlineBar(seq=1, ts_open=1_700_000_000.0, open=2600.0, high=2610.0,
                 low=2590.0, close=2605.0, volume=1000.0, closed=False),
    )
    indicators = IndicatorBundle(
        ema20=(float("nan"),),
        atr14=(float("nan"),),
    )
    frame = KlineFrame(
        symbol="XAUUSD", timeframe="1h", bars=bars,
        indicators=indicators, snapshot_ts_local_ms=1_700_000_000_000,
    )
    messages = assembler.build_stage1(frame)
    user = messages[1]["content"]
    assert "N/A" in user


def test_stage1_message_roles(assembler: PromptAssembler):
    """build_stage1 must return exactly [system, user] messages."""
    frame = _make_frame()
    messages = assembler.build_stage1(frame)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_stage2_message_roles(assembler: PromptAssembler):
    """build_stage2 must return exactly [system, user] messages."""
    frame = _make_frame()
    messages = assembler.build_stage2(frame, {}, [], [])
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_stage2_continuation_is_standalone_not_stage1_chat(assembler: PromptAssembler):
    """Stage 2 standalone mode must not prepend Stage 1 (OpenClaw Agent chat-mode retries)."""
    frame = _make_frame()
    stage1_messages = assembler.build_stage1(frame)
    stage1_json = {"cycle_position": "spike", "direction": "bearish", "gate_result": "proceed"}

    messages = assembler.build_stage2_continuation(
        frame=frame,
        stage1_messages=stage1_messages,
        stage1_reply_content='{"cycle_position":"spike","direction":"bearish"}',
        stage1_json=stage1_json,
        strategy_files=["下跌通道分析识别.txt", "下跌通道交易策略.txt"],
        experience_entries=[],
        use_prefix_chain=False,
    )

    assert [m["role"] for m in messages] == ["system", "user"]
    s2_user = messages[1]["content"]
    assert "阶段二 API 任务模式" in s2_user
    assert "阶段二任务" in s2_user
    assert "你现在只执行阶段一" not in s2_user
    assert "沿用上一轮阶段一用户消息" not in s2_user
    assert "K线数据" in s2_user
    assert "序号 | 时间" in s2_user
    assert "下跌通道分析识别" in s2_user
    assert "上涨通道分析识别" not in s2_user
    assert "【输出】正文只写 Stage 2 Draft v2" in s2_user


def test_stage1_stage2_system_prompt_byte_identical(assembler: PromptAssembler):
    """Stage 1 and Stage 2 must share one system blob for KV prefix reuse."""
    frame = _make_frame()
    s1_system = assembler.build_stage1(frame)[0]["content"]
    s2_system = assembler.build_stage2(frame, {}, [], [])[0]["content"]
    assert s1_system == s2_system


def test_stage2_continuation_prefix_chain_reuses_stage1(assembler: PromptAssembler):
    """DeepSeek-style prefix chain: S2 reuses entire S1 message prefix."""
    frame = _make_frame()
    stage1_messages = assembler.build_stage1(frame)
    stage1_json = {
        "cycle_position": "normal_channel",
        "direction": "bullish",
        "gate_result": "proceed",
    }

    messages = assembler.build_stage2_continuation(
        frame=frame,
        stage1_messages=stage1_messages,
        stage1_reply_content='{"cycle_position":"normal_channel","direction":"bullish"}',
        stage1_json=stage1_json,
        strategy_files=["上涨通道分析识别.txt"],
        experience_entries=[],
        use_prefix_chain=True,
    )

    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[0]["content"] == stage1_messages[0]["content"]
    assert messages[1]["content"] == stage1_messages[1]["content"]
    assert "normal_channel" in messages[2]["content"]
    s2_user = messages[3]["content"]
    assert "阶段二任务" in s2_user
    assert "序号 | 时间" not in s2_user
    assert "完整 K 线表与几何特征已包含在上方阶段一用户消息中" in s2_user


def test_stage2_prompt_includes_balanced_stance_guidance(assembler: PromptAssembler):
    frame = _make_frame()
    messages = assembler.build_stage2(frame, {}, [], [], decision_stance="balanced")
    user = messages[1]["content"]
    assert "交易倾向" in user
    assert "均衡" in user
    assert "证据完整但不完美" in user


def test_stage2_prompt_conservative_omits_balanced_only_hints(assembler: PromptAssembler):
    frame = _make_frame()
    messages = assembler.build_stage2(frame, {}, [], [], decision_stance="conservative")
    user = messages[1]["content"]
    assert "证据均较清晰" in user
    assert "证据完整但不完美" not in user


def test_incremental_stage1_prompt_includes_previous_record_and_new_bars(
    assembler: PromptAssembler,
):
    """Incremental Stage 1 with full previous record uses 4-message continuation."""
    from pa_agent.records.schema import AnalysisRecord, RecordMeta

    frame = _make_frame(5)
    # Build a full Stage 1 to get realistic messages/response for the previous record
    full_s1_messages = assembler.build_stage1(frame)
    prev_user = next(m["content"] for m in full_s1_messages if m["role"] == "user")
    prev_assistant = '{"cycle_position":"normal_channel","gate_result":"proceed"}'

    previous = AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso="2026-01-01T00:00:00.000",
            timestamp_local_ms=1,
            symbol="XAUUSD",
            timeframe="1h",
            bar_count=5,
            ai_provider={},
        ),
        kline_data=[],
        htf_text="",
        stage1_messages=full_s1_messages,
        stage1_response={"content": prev_assistant},
        stage1_diagnosis={"cycle_position": "normal_channel"},
        stage2_messages=[],
        stage2_response=None,
        stage2_decision={"decision": {"order_type": "不下单"}},
        strategy_files_used=["上涨通道分析识别.txt"],
        experience_loaded=[],
        exception=None,
        usage_total={},
    )

    messages = assembler.build_incremental_stage1(frame, previous, 2)

    # 4-message continuation structure: system, user(prev S1), assistant(prev S1 reply), user(incremental)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    # Message [1] is previous full Stage 1 user prompt, refreshed with market features
    assert "程序可复算市场事实" in messages[1]["content"]
    assert messages[1]["content"].startswith(prev_user.split("\n", 1)[0])
    # Message [2] is normalized bare JSON from validated stage1_diagnosis
    assert messages[2]["content"].startswith("{")
    assert "cycle_position" in messages[2]["content"]
    assert "JSON 校验通过" not in messages[2]["content"]
    assert "```" not in messages[2]["content"]
    # Message [3] is the incremental task
    incremental_user = messages[3]["content"]
    assert "阶段一增量更新任务" in incremental_user
    assert "增量输出格式" in incremental_user
    assert "诊断更新摘要" in incremental_user
    assert "新增已收盘K线:2" in incremental_user
    assert "上一轮已完成分析" in incremental_user
    assert "normal_channel" in incremental_user
    # Anti-anchoring directives must be present
    assert "反锚定要求" in incremental_user
    assert "不要因为上一轮已得出结论就倾向于延续它" in incremental_user
    assert "宁可过度更新，不可锚定延续" in incremental_user
    assert "先独立审视完整 K 线数据" in incremental_user
    # No full K-line table in the incremental user message
    assert "当前完整 K线数据" not in incremental_user
    assert "当前完整 K线几何特征" not in incremental_user
    # But new K-line data is present
    assert "新增 K线数据" in incremental_user
    assert "完整" in incremental_user and "窗口计算" in incremental_user
    assert "新增程序事实" in incremental_user


def test_incremental_stage1_normalizes_fenced_previous_response(
    assembler: PromptAssembler,
) -> None:
    """Previous assistant with prose + ```json fence becomes bare diagnosis JSON."""
    import json

    from pa_agent.records.schema import AnalysisRecord, RecordMeta

    frame = _make_frame(5)
    full_s1_messages = assembler.build_stage1(frame)
    diagnosis = {
        "cycle_position": "trending_tr",
        "direction": "bullish",
        "gate_result": "proceed",
    }
    fenced = (
        "JSON 校验通过。以下是修正后的完整阶段一诊断 JSON：\n\n"
        f"```json\n{json.dumps(diagnosis, ensure_ascii=False)}\n```"
    )
    previous = AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso="2026-01-01T00:00:00.000",
            timestamp_local_ms=1,
            symbol="XAUUSD",
            timeframe="1h",
            bar_count=5,
            ai_provider={},
        ),
        kline_data=[],
        htf_text="",
        stage1_messages=full_s1_messages,
        stage1_response={"content": fenced},
        stage1_diagnosis=diagnosis,
        stage2_messages=[],
        stage2_response=None,
        stage2_decision={"decision": {"order_type": "不下单"}},
        strategy_files_used=[],
        experience_loaded=[],
        exception=None,
        usage_total={},
    )

    messages = assembler.build_incremental_stage1(frame, previous, 1)
    assistant = messages[2]["content"]
    assert assistant.startswith("{")
    assert "trending_tr" in assistant
    assert "JSON 校验通过" not in assistant
    assert "```" not in assistant
    parsed = json.loads(assistant)
    assert parsed["cycle_position"] == "trending_tr"


def test_incremental_stage1_raises_without_previous_messages(
    assembler: PromptAssembler,
):
    """Incremental Stage 1 raises ValueError when previous record lacks messages."""
    import pytest

    from pa_agent.records.schema import AnalysisRecord, RecordMeta

    frame = _make_frame(5)
    previous = AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso="2026-01-01T00:00:00.000",
            timestamp_local_ms=1,
            symbol="XAUUSD",
            timeframe="1h",
            bar_count=5,
            ai_provider={},
        ),
        kline_data=[],
        htf_text="",
        stage1_messages=[],  # empty → must raise
        stage1_response=None,  # None → must raise
        stage1_diagnosis={"cycle_position": "normal_channel"},
        stage2_messages=[],
        stage2_response=None,
        stage2_decision={"decision": {"order_type": "不下单"}},
        strategy_files_used=["上涨通道分析识别.txt"],
        experience_loaded=[],
        exception=None,
        usage_total={},
    )

    with pytest.raises(ValueError, match="stage1_messages contains no user message"):
        assembler.build_incremental_stage1(frame, previous, 2)


def test_incremental_stage1_raises_without_previous_response(
    assembler: PromptAssembler,
):
    """Incremental Stage 1 raises ValueError when previous record lacks response content."""
    import pytest

    from pa_agent.records.schema import AnalysisRecord, RecordMeta

    frame = _make_frame(5)
    # Build full Stage 1 messages so stage1_messages is populated
    full_s1 = assembler.build_stage1(frame)
    previous = AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso="2026-01-01T00:00:00.000",
            timestamp_local_ms=1,
            symbol="XAUUSD",
            timeframe="1h",
            bar_count=5,
            ai_provider={},
        ),
        kline_data=[],
        htf_text="",
        stage1_messages=full_s1,  # has user message
        stage1_response={},  # empty dict → no 'content' key
        stage1_diagnosis={},  # no validated diagnosis either → must raise
        stage2_messages=[],
        stage2_response=None,
        stage2_decision={"decision": {"order_type": "不下单"}},
        strategy_files_used=["上涨通道分析识别.txt"],
        experience_loaded=[],
        exception=None,
        usage_total={},
    )

    with pytest.raises(ValueError, match="stage1_response has no 'content' field"):
        assembler.build_incremental_stage1(frame, previous, 2)


def test_stage1_prompt_has_kline_indicator_disclaimer(assembler: PromptAssembler) -> None:
    """Stage 1 user prompt and tables warn that indicators are window-recomputed."""
    frame = _make_frame()
    messages = assembler.build_stage1(frame)
    user = messages[1]["content"]
    assert "指标非全历史延续" in user
    assert "不输出 bar_type、趋势或 Setup" in user


def test_stage2_prompt_has_kline_indicator_disclaimer(assembler: PromptAssembler) -> None:
    frame = _make_frame()
    messages = assembler.build_stage2(frame, {}, [], [])
    user = messages[1]["content"]
    assert "指标非全历史延续" in user


def test_render_kline_feature_table_limit_uses_full_window_header(
    assembler: PromptAssembler,
) -> None:
    """Incremental «新增» feature table: header notes full-window computation."""
    frame = _make_frame(5)
    table = assembler._render_kline_feature_table(frame, limit=2)
    assert "最近 2 根已收盘 K 线" in table
    assert "勿逐点对比" in table
    assert table.count("序号 | 阳阴事实") == 1
    assert "K2" in table or " 2 " in table


# ── T13: Prompt assembler tests for next_bar_prediction ──────────────────────


def test_stage2_prompt_contains_prediction_instruction(assembler: PromptAssembler):
    """Stage 2 prompt always includes next_cycle; next_bar only when enabled."""
    frame = _make_frame()
    messages = assembler.build_stage2(frame, {}, [], [])
    user = messages[1]["content"]
    assert "next_cycle_prediction" in user
    assert "用户已关闭" in user  # next_bar disabled by default

    messages_on = assembler._build_stage2_user_prompt(
        frame=frame,
        stage1_json={"cycle_position": "normal_channel", "direction": "bullish", "gate_result": "proceed"},
        strategy_files=[],
        experience_entries=[],
        enable_next_bar_prediction=True,
    )
    assert "next_bar_prediction" in messages_on
    assert "next_cycle_prediction" in messages_on
    assert "probabilities" in messages_on


def test_previous_prediction_rendered_in_incremental_mode(assembler: PromptAssembler):
    """With previous_record containing prediction, prompt must show summary (R5.2)."""
    from pa_agent.records.schema import AnalysisRecord, RecordMeta

    frame = _make_frame()
    stage1_messages = assembler.build_stage1(frame)
    stage1_json = {"cycle_position": "normal_channel", "direction": "bullish", "gate_result": "proceed"}

    previous = AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso="2026-01-01T00:00:00.000",
            timestamp_local_ms=1,
            symbol="XAUUSD",
            timeframe="1h",
            bar_count=5,
            ai_provider={},
        ),
        kline_data=[],
        htf_text="",
        stage1_messages=[],
        stage1_response=None,
        stage1_diagnosis={"cycle_position": "normal_channel"},
        stage2_messages=[],
        stage2_response=None,
        stage2_decision={
            "decision": {"order_type": "不下单"},
            "next_bar_prediction": {
                "direction": "bullish",
                "probabilities": {"bullish": 60, "bearish": 30, "neutral": 10},
                "reasoning": "test",
                "unpredictable": False,
            },
        },
        strategy_files_used=[],
        experience_loaded=[],
        exception=None,
        usage_total={},
    )

    messages = assembler.build_stage2_continuation(
        frame=frame,
        stage1_messages=stage1_messages,
        stage1_reply_content='{"cycle_position":"normal_channel","direction":"bullish"}',
        stage1_json=stage1_json,
        strategy_files=["上涨通道分析识别.txt"],
        experience_entries=[],
        previous_record=previous,
    )

    user = messages[-1]["content"]  # Stage 2 user prompt (last turn)
    assert "上一轮下一根K线预测" in user
    assert "阳线" in user
    assert "60%" in user


def test_no_previous_prediction_no_summary(assembler: PromptAssembler):
    """Without previous_record, prompt must not contain prediction summary."""
    frame = _make_frame()
    messages = assembler.build_stage2(frame, {}, [], [])
    user = messages[1]["content"]
    assert "上一轮下一根K线预测" not in user


def test_vpa_disabled_by_default_keeps_prompts_out_of_vpa_path(
    assembler: PromptAssembler,
) -> None:
    frame = _make_frame(25)
    stage1_user = assembler.build_stage1(frame)[1]["content"]
    stage2_user = assembler.build_stage2(
        frame,
        {
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "gate_result": "proceed",
            "vpa_context": {"summary": "old"},
        },
        [],
        [],
    )[1]["content"]

    combined = stage1_user + stage2_user
    assert "Wyckoff" not in combined
    assert "VPA" not in combined
    assert "VPA_PROGRAM_EVIDENCE_CATALOG" not in combined
    assert "vpa_context" not in combined
    assert "vpa_impact" not in combined


def test_vpa_enabled_keeps_pa_prompts_pure_and_explicit_role_injects_vpa(
    assembler: PromptAssembler,
) -> None:
    frame = _make_frame(25)
    enabled = PromptAssembler(
        prompt_dir=assembler._prompt_dir,
        prompt_settings=PromptSettings(vpa_enabled=True),
    )

    stage1_user = enabled.build_stage1(frame)[1]["content"]
    stage2_user = enabled.build_stage2(
        frame,
        {
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "gate_result": "proceed",
            "vpa_context": {"summary": "volume facts only"},
        },
        [],
        [],
    )[1]["content"]

    assert "Wyckoff VPA Core" not in stage1_user + stage2_user
    assert "VPA_PROGRAM_EVIDENCE_CATALOG" not in stage1_user + stage2_user
    assert "vpa_context" not in stage1_user + stage2_user
    assert "vpa_impact" not in stage1_user + stage2_user

    vpa_prompt = "\n".join(
        item["content"]
        for item in enabled.build_vpa_evidence_report(
            frame,
            {
                "cycle_position": "normal_channel",
                "direction": "bullish",
                "gate_result": "proceed",
            },
            {"primary": {"hypothesis_id": "pa-primary"}, "setup_candidates": []},
        )
    )
    assert "Wyckoff VPA Core" in vpa_prompt
    assert "VPA_PROGRAM_EVIDENCE_CATALOG" in vpa_prompt
    assert "hypothesis_assessments" in vpa_prompt


def test_explicit_vpa_role_matches_schema_and_grounding_contract(
) -> None:
    prompt_dir = Path(__file__).resolve().parents[2] / "prompt_engineering"
    enabled = PromptAssembler(
        prompt_dir=prompt_dir,
        prompt_settings=PromptSettings(vpa_mode="shadow"),
    )

    prompt = "\n".join(
        item["content"]
        for item in enabled.build_vpa_evidence_report(
            _make_frame(25),
            {
                "cycle_position": "normal_channel",
                "direction": "bullish",
                "gate_result": "proceed",
            },
            {"primary": {"hypothesis_id": "pa-primary"}, "setup_candidates": []},
        )
    )

    assert "weakened_bull" not in prompt
    assert "weakened_bear" not in prompt
    assert '"state": "pending_closed_bar | not_applicable"' in prompt
    assert '"vpa_report": {' in prompt
    assert '"hypothesis_assessments"' in prompt
    assert "reason 可引用程序表中已提供的数值" not in prompt
    assert "下一根已收盘 K 线" in prompt
    assert "K0" in prompt
    assert "`net_progress_direction_relation`" in prompt
    assert "when it is `opposite`" in prompt
    assert "Stage 1 MUST output `vpa_context`" not in prompt
    assert "Stage 2 MUST output `vpa_impact`" not in prompt


def test_vpa_enabled_prefix_chain_remains_pa_only(
    assembler: PromptAssembler,
) -> None:
    frame = _make_frame(25)
    enabled = PromptAssembler(
        prompt_dir=assembler._prompt_dir,
        prompt_settings=PromptSettings(vpa_enabled=True),
    )
    stage1_messages = enabled.build_stage1(frame)
    messages = enabled.build_stage2_continuation(
        frame=frame,
        stage1_messages=stage1_messages,
        stage1_reply_content='{"cycle_position":"normal_channel","direction":"bullish"}',
        stage1_json={
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "gate_result": "proceed",
            "vpa_context": {"summary": "volume facts only"},
        },
        strategy_files=[],
        experience_entries=[],
        use_prefix_chain=True,
    )

    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert "VPA_PROGRAM_EVIDENCE_CATALOG" not in messages[1]["content"]
    assert "VPA_PROGRAM_EVIDENCE_CATALOG" not in messages[3]["content"]
    assert "Wyckoff VPA Core" not in messages[3]["content"]
    assert '"vpa_context"' not in messages[3]["content"]
    assert "vpa_impact" not in messages[3]["content"]


def test_higher_timeframe_context_is_stage1_background_and_not_repeated_in_prefix_stage2(
    assembler: PromptAssembler,
) -> None:
    frame = _make_frame(25)
    context = {
        "status": "available",
        "timeframe": "4h",
        "current_timeframe": "1h",
        "frame": _make_frame(20),
    }
    stage1_messages = assembler.build_stage1(frame, htf_context=context)
    messages = assembler.build_stage2_continuation(
        frame=frame,
        stage1_messages=stage1_messages,
        stage1_reply_content='{"cycle_position":"normal_channel","direction":"bullish"}',
        stage1_json={
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "gate_result": "proceed",
        },
        strategy_files=[],
        experience_entries=[],
        htf_context=context,
        use_prefix_chain=True,
    )

    assert "更高周期背景" in messages[1]["content"]
    assert "非独立硬 Gate" in messages[1]["content"]
    assert "更高周期背景" not in messages[3]["content"]


def test_vpa_evidence_catalog_reuses_same_frozen_snapshot(
    assembler: PromptAssembler,
) -> None:
    enabled = PromptAssembler(
        prompt_dir=assembler._prompt_dir,
        prompt_settings=PromptSettings(vpa_enabled=True),
    )
    stage1 = {
        "cycle_position": "normal_channel",
        "direction": "bullish",
        "gate_result": "proceed",
    }
    hypotheses = {
        "primary": {"hypothesis_id": "pa-primary"},
        "setup_candidates": [],
    }
    first = enabled.build_vpa_evidence_report(
        _make_frame(25), stage1, hypotheses
    )[1]["content"]
    second = enabled.build_vpa_evidence_report(
        replace(_make_frame(25), snapshot_ts_local_ms=1_700_000_000_001),
        stage1,
        hypotheses,
    )[1]["content"]

    first_id = next(line for line in first.splitlines() if line.startswith("catalog_id:"))
    second_id = next(line for line in second.splitlines() if line.startswith("catalog_id:"))
    assert first_id == second_id


def test_vpa_disabled_incremental_prompt_strips_previous_vpa_fields(
    assembler: PromptAssembler,
) -> None:
    from pa_agent.records.schema import AnalysisRecord, RecordMeta

    frame = _make_frame(25)
    full_s1_messages = assembler.build_stage1(frame)
    previous = AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso="2026-01-01T00:00:00.000",
            timestamp_local_ms=1,
            symbol="XAUUSD",
            timeframe="1h",
            bar_count=25,
            ai_provider={},
        ),
        kline_data=[],
        htf_text="",
        stage1_messages=full_s1_messages,
        stage1_response={"content": '{"cycle_position":"normal_channel"}'},
        stage1_diagnosis={
            "cycle_position": "normal_channel",
            "vpa_context": {"summary": "old"},
        },
        stage2_messages=[],
        stage2_response=None,
        stage2_decision={
            "vpa_impact": {
                "background_relation": "neutral",
                "entry_timing_relation": "neutral",
            }
        },
        strategy_files_used=[],
        experience_loaded=[],
        exception=None,
        usage_total={},
    )

    messages = assembler.build_incremental_stage1(frame, previous, 2)
    incremental_user = messages[3]["content"]
    assert "Wyckoff" not in incremental_user
    assert "VPA" not in incremental_user
    assert "vpa_context" not in incremental_user
    assert "vpa_impact" not in incremental_user


def test_unpredictable_previous_prediction_renders_note(assembler: PromptAssembler):
    """Unpredictable previous prediction must render note (R5.2)."""
    from pa_agent.records.schema import AnalysisRecord, RecordMeta

    frame = _make_frame()
    stage1_messages = assembler.build_stage1(frame)
    stage1_json = {"cycle_position": "normal_channel", "direction": "bullish", "gate_result": "proceed"}

    previous = AnalysisRecord(
        meta=RecordMeta(
            timestamp_local_iso="2026-01-01T00:00:00.000",
            timestamp_local_ms=1,
            symbol="XAUUSD",
            timeframe="1h",
            bar_count=5,
            ai_provider={},
        ),
        kline_data=[],
        htf_text="",
        stage1_messages=[],
        stage1_response=None,
        stage1_diagnosis={},
        stage2_messages=[],
        stage2_response=None,
        stage2_decision={
            "next_bar_prediction": {
                "direction": None,
                "probabilities": None,
                "reasoning": "test",
                "unpredictable": True,
            },
        },
        strategy_files_used=[],
        experience_loaded=[],
        exception=None,
        usage_total={},
    )

    messages = assembler.build_stage2_continuation(
        frame=frame,
        stage1_messages=stage1_messages,
        stage1_reply_content='{"cycle_position":"normal_channel"}',
        stage1_json=stage1_json,
        strategy_files=["上涨通道分析识别.txt"],
        experience_entries=[],
        previous_record=previous,
    )

    user = messages[-1]["content"]  # Stage 2 user prompt (last turn)
    assert "不可预测" in user


def test_stage2_prompt_states_the_final_trace_order(assembler: PromptAssembler) -> None:
    frame = _make_frame()
    stage1_messages = assembler.build_stage1(frame)
    stage1_json = {
        "cycle_position": "normal_channel",
        "direction": "bullish",
        "gate_result": "proceed",
        "entry_setup": "",
    }

    messages = assembler.build_stage2_continuation(
        frame=frame,
        stage1_messages=stage1_messages,
        stage1_reply_content='{"cycle_position":"normal_channel"}',
        stage1_json=stage1_json,
        strategy_files=[],
        experience_entries=[],
    )

    user = messages[-1]["content"]
    assert "不要输出 `question/section/bar_range`、§10.3 或 `terminal`" in user
    assert "Stage 2 是具体 PA 交易方案的唯一所有者" in user
    assert "order_type=不下单" in user
