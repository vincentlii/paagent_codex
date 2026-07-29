# ADR-0004：按职责切分 PA/VPA Prompt 与有界联合补丁

- 状态：Accepted
- 日期：2026-07-20

> Joint Prompt 与 Patch 仅用于 ADR-0009 定义的隔离研究 B 臂；生产 Shadow 不调用 Joint。

## 背景

旧链路会在普通 PA Stage 1/Stage 2 中注入 VPA 字段，Stage 1 与 Stage 2 还重复加载整份
PA 决策树。联合裁决需要复制完整 Stage 2 JSON，导致模型同时承担重新生成价格、订单、
交易者方程和 VPA 影响的任务。重复指令、冲突协议与大对象重写会增加 token、格式错误和
无意改写 PA baseline 的概率。

## 决策

1. 普通 PA Stage 1 与 PA-only Stage 2 始终保持 price-only；VPA 模式不会向这两次调用注入
   `vpa_context`、`vpa_impact`、量价目录或 VPA 理论。
2. `二元决策.txt` 按稳定 Markdown 语义边界切分：Stage 1 加载全局原则与 §1-§8；
   prefix-chain Stage 2 只追加 §9-§15。独立 Stage 2 才同时加载全局原则。
3. Stage 1 常驻 PA 理论保留 Background、八态频谱、Always In、Setup、竞争假设、HTF 和 Gate；
   固定百分比只可描述证据，不得机械决定市场状态。详细几何只展开最近 10 根已收盘 K 线，
   完整 OHLC 表仍保留全部窗口。
4. VPA 只由独立 `VpaEvidenceReport` 调用按 Context 加载核心与相关模块。它引用程序 evidence
   catalog，不进入 Brooks Router。
5. Joint Arbiter 只输出 `joint_arbitration + decision_patch`。补丁字段限于
   `trade_confidence`、`trade_confidence_reasoning`、`watch_points`、`risk_assessment` 和
   `invalidation_condition`。程序把合法补丁应用到冻结 PA baseline，再执行完整 Stage 2 校验。
6. `keep_brooks_baseline` 必须为空补丁；中性或证据不足必须保持 baseline；等待确认、偏好回测
   或拒绝当前尝试时，程序统一落实 no-order 不变量。Joint 不得复制或修改方向、三价、
   `estimated_win_rate`、PA trace 和预测。
7. 风险距离、RR 与交易者方程由程序复算。LLM 负责提出有结构依据的 entry、stop、target 和
   当前 PA 胜率先验，不得为了通过方程扭曲价格或套用未经校准的通用百分比。
8. VPA/Joint 的自由文本 grounding 发现遵循 ADR-0002：明确 `K#` 越界和结构化权限矛盾为
   hard error；数字转抄、比较措辞、时段或参与者归因等 regex 发现只记 warning。任何自由
   文本措辞差异都不得成为隐藏 Gate。

## 后果

- PA、VPA 与 Joint 的注意力边界和失败边界可以单独验证。
- 旧嵌入式 VPA Schema、normalizer、retry 分支和 paired sidecar Prompt 不再保留。
- GUI/records 可继续读取历史 `vpa_context` / `vpa_impact` 兼容摘要，但模型协议不得重新依赖它们。
- 自定义 Prompt 包缺少切分标题时仍回退为加载完整文件，以保持外部包兼容性。

## 验证

合成 20-bar 合同样本的确定性 token 估算为：Stage 1 `40,771`，Stage 2 增量 `35,203`，
PA-only 合计 `75,974`；VPA `6,161`，Joint `2,263`，Soft 合计 `84,398`，为 PA-only 的
`1.1109` 倍。自动测试要求 Stage 1 不超过迁移基线 `78,608` 的 60%，Soft 不超过 PA-only
的 1.35 倍。

发布前还必须验证：

- PA Stage 1/Stage 2 消息中无 VPA 泄漏。
- Joint Schema 不能承载订单、价格、胜率或 PA 结构字段。
- 非 baseline effect 必须引用真实相对量证据，并产生合法、可执行的时机变化。
- 完整 unit、property、integration 与 e2e 非 live 回归通过。
