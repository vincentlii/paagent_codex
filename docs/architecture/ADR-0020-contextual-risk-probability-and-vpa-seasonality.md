# ADR-0020：结构风险、概率语义与 VPA 窗口时段归一化

- 状态：Accepted
- 日期：2026-08-13

## 背景

旧知识文件同时存在“结构失效决定止损”与“信号棒极点外固定跳数”两套表述，并把部分
Brooks 教学频率写成可直接用于当前 `estimated_win_rate` 的数字锚点。Stage 2 还强制填写
TP2，即使冻结图表没有独立远端结构目标。三者都会诱导模型为满足格式而编造精度。

单棒 seasonality 已能按严格因果同槽历史比较，但 PA 选定的多棒窗口仍只比较 raw volume
或 rolling RVOL。跨日内时段的窗口总量因此可能把正常时段差异误读为 Effort 变化。

## 决策

1. Stop 由 LLM 根据当前 Brooks Setup 的结构失效判断。信号棒、摆动、边界或测试极点只是
   候选锚点；程序只提供 OHLC、真实 tick、ATR/窗口尺度与几何安全，不命名结构止损。
2. `estimated_win_rate` 保持未校准 PA 主观先验。教材频率只给竞争假设定性排序，不得复制、
   不得由 Breakeven 反推；统计校准必须绑定同品种、周期、数据源与执行协议的 calibration ID。
3. TP1 是可执行目标和 Trader Equation 的唯一回报基础。TP2 是可选研究扩展：无独立、
   可核对的远端结构目标时为 `null`；null 不触发重试、不降低置信度、不抑制订单。
4. TP2 存在时仍须严格位于 TP1 盈利方向之外。Paper 基线继续在 TP1 全量退出。
5. VPA catalog 升级为 2.3。PA-window seasonality 对窗口每根棒分别匹配严格早于分析窗的
   exact local-clock slot，汇总实际量/预期量；只有全窗口 volume 与时槽覆盖完整时才输出关系。
6. VPA 知识模块只描述证据与假设，不携带 Joint action enum。下游动作仅由隔离研究中的
   Joint contract 定义，防止 Evidence Analyst 被执行语言锚定。

## 后果

- 模型可以诚实表达“有 TP1、没有可靠 TP2”，减少虚构 MM 与不必要的 Schema 失败。
- 止损距离可能比旧固定信号棒止损更宽或更窄，但其理由必须是 Setup 失效；不可承担时通过
  sizing、等待或放弃处理，不靠收紧止损美化 RR。
- 胜率数字仍有模型不确定性，但不再由通用教学百分比制造伪校准。
- VPA 可在日内时段差异明显的资产上比较多棒 Effort，同时不把 seasonality 变成 Gate 或事件分类器。

## 验证

- 可执行 Stage 2 在 `take_profit_price_2=null` 时通过 Schema、handoff、执行策略与 Paper 计划。
- TP2 非 null 且几何错误时仍由执行策略抑制，原 PA 候选保持不可变。
- Window seasonality 完整覆盖时产生可复算 ratio/relation；任一时槽缺失时关系为 unavailable。
- `prompt_engineering/wyckoff_vpa/*.txt` 不含 Joint action enum。
- PA 理论目录明确包含 structural invalidation 与 contextual uncalibrated prior 两条规则。
