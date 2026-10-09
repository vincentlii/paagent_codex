# Stage 1 子 Agent 真实冻结行情试验与三角色审阅

日期：2026-09-28。分支：`codex-analysis-backend-v1`，未提交 working tree。
状态：**本轮分析与证据整理完成；四臂完整运行验收未通过，性能与质量增益未证明，生产仍 no-go。**

## 结论

当前 `stage1_challenge_v1` 不能晋级：四份 Stage 1 原始 Draft 已取得，但完整 PA＋VPA 链只有三份持久成功记录，B2 在会话中断后缺少 Stage 2 最终回执。Stage 1 政策对照平均慢 **17.9%**；子 Agent 两次委托中一次完成并收集、一次参数错误被拒绝。无重跑替换、无修改过期 Job、无补造 Host receipt。

独立模型盲审没有检出决定性事实、因果或越权错误，但发现竞争假设字段倒置、突破边界尺度绑定不足和 VPA 窗口比较说明不足。四份 Stage 1 的 B−A 描述性分差仅 **+1.3/100**；最高分 B2 没有获得子意见，成功协作的 B1 反而低于两个 A 臂。不能归因为 child 的质量增益，也不能称统计非劣。

按原预登记门禁，**暂停 VPA、Stage 2 和组合 child 的付费扩展**。这是否定当前“较宽泛反证挑战＋父角色继续全量综合”的晋级证据，不是否定所有 Stage 内协作。Direct、默认单 Agent、Runtime AGENTS/Skills、知识、Validator、Router、Risk 与正式配置本轮均未改动；没有 VPS 部署或交易。

## 实验边界与完整性

| 项目 | 本轮条件 |
| --- | --- |
| 唯一研究变量 | A=`off`；B=`stage1_challenge_v1`，均为 agentic 调查 |
| 模型 | 父子均 `gpt-6-luna/xhigh`，未降低思考强度 |
| 顺序 | 模型调用前登记 B1→A1→A2→B2，逐臂独立 runtime |
| 行情 | 同一真实 Binance USD-M XAUUSDT 1h，100 根主周期＋60 根 4h |
| 事实边界 | `as_of=2026-09-10 16:33:35.684 UTC`，主周期与 HTF 均已闭盘；不是当前行情 |
| 知识 | 同一完整冻结知识集合，37 个资源；未摘要、删减或截断原文以换速度 |
| 执行链 | `pa_vpa_joint`，price-only Stage 1→原 Validator/Router→独立 VPA→联合 Stage 2→原校验/持久化 |
| 会话 | 每臂独立；同快照 PA Stage 1→Stage 2 受控续接；VPA 独立 ephemeral |
| 时间限制 | 隔离研究 harness 将 role wall 设为 3600 秒、cycle wall 10800 秒；无 240 秒截断。其余预算及 Frozen Job expiry 未改 |
| 工具/权限 | Host 托管 child，只读同 Job 有限资源；未启用 native CLI 子角色、shell、Web、数据库或改代码 |
| 发布与成本 | 无 fallback、Paper、实时交易、部署或模型切换；订阅用量不可换算为虚构美元成本 |

固定 CLI 为 `codex-cli 0.158.0-alpha.2.1`，复制并校验四个运行二进制，避免桌面更新删掉执行路径。实际 source/settings domain、知识、研究 driver 和 Codex binary 指纹在整理时均重新核验相同。

正式 coverage 为 `assigned=4 / completed=3 / failed=0 / missing=1`。B2 的 Stage 1、VPA Worker 封套存在，并有下一阶段发布事件，证明当时链路已在内存推进；**没有持久完整 Host 记录，不冒充最终接受**。四份 Stage 1 一律使用原始 Worker Provider Draft 单独盲评，避免把 B2 原 Draft 与其他三份物化结果混为同一表示。

## 实际速度与用量

单位：秒。阶段耗时来自相同 Worker/Host 调用边界；整链耗时来自 receipt 起止。B2 前两阶段是 Worker 已返回的局部数据。

| 臂 | Stage 1 | VPA | Stage 2 | 整链 | 校正后整链 total tokens | Stage 1 child |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| A1 | 360.219 | 211.079 | 185.891 | 757.734（12.6 分） | 631,270 | off |
| A2 | 452.687 | 129.313 | 317.250 | 899.803（15.0 分） | 557,523 | off |
| B1 | 556.407 | 280.047 | 203.031 | 1,040.132（17.3 分） | 923,187 | 完成并收集 |
| B2 | 401.703 | 202.609 | 未返回 | 缺失 | 未知；已报告下界 485,726 | start_rejected |

Stage 1 的完整局部政策对照：A 平均 **406.453 秒**，B 平均 **479.055 秒**，B−A=**72.602 秒**，B/A=**1.1786**。A/A 差 **92.468 秒**，B/B 差 **154.704 秒**；一个快照不能估计稳定分布或 p95。仅成功 child 的 B1 比 A 平均慢 **36.9%**，但只一份，不是可推广的 per-protocol 效果估计。

Stage 1 total tokens：A1=114,693，A2=117,128，B1=467,939，B2=196,126；组均值为 115,910.5 对 332,032.5。额外 Token 本身已被用户接受，但没有速度或质量证据支撑这些额外消耗。缓存包含在 input，不能再叠加；总输入不等于“唯一知识量”。

三份完整记录均为 `completed_with_warnings`，最终 `wait`／无订单。九次持久角色调用均首稿 validated、无模型 repair；存在 normalization 与专业 warning，**不能称九次 first-pass clean**。B2 的未报告最终用量保持未知，在途消耗不计零。三份整链都远超 300 秒；这些是单样本观测，不是 p95 验收。

## 质量盲审与三角色复核

一名独立模型审阅者不接触分组、运行日志、性能或 private key；先冻结四份 price-only Stage 1 评分，再读完整链。主审程序校验 coverage、0–5 值域和 packet/score digest，冻结评分后解盲。元数据盲态不能完全消除正文暗示；这是模型三视角复核，**不是三名独立人类专家或统计质量非劣试验**。

| 臂 | Stage 1 /100 | 完整链 /100 | 独立 VPA /100 | 审阅范围 |
| --- | ---: | ---: | ---: | --- |
| A1 | 78.4 | 79.7 | 84.5 | 完整 |
| A2 | 75.5 | 77.9 | 79.0 | 完整 |
| B1 | 75.0 | 77.0 | 85.0 | 完整，成功 child |
| B2 | 81.5 | unscorable | unscorable | 仅原始 Stage 1 Draft |

Stage 1 权重沿用预登记：Market Context 35%、Brooks Diagnosis 30%、Evidence Traceability 20%、Counterevidence/Uncertainty 15%。A 均分 76.95，B 均分 78.25；+1.3 小于 A/A 的 2.9 和 B/B 的 6.5 分差，且 B2 未使用成功 child。完整链缺失 B2，不给完整四臂均值或零分。VPA/Stage 2 没有 child treatment，本表差异不能证明它们的子协作增益。

| 三角色 | 已核实判断 | 实际质量缺口 |
| --- | --- | --- |
| 软件架构师 | 原 Host 阶段链、角色权限与 Router 仍在；B2 非法参数在派生前拒绝；missing 不补造；父独占 Draft | B1 的替代假设 `confirmation_condition` 描述削弱区间，`disconfirmation_condition` 却描述支持区间，字段方向倒置并传入交接。正文含正确支持/削弱含义，未显示因此改变 Gate/订单，仍是有影响的语义缺陷 |
| 量化工程师 | closed/as-of、同源量基线、K4→K3→K2→K1 时序与目录引用一致；不以标签或最终 wait 判正确 | 必须区分 K40 旧低 4345.66 已被 K3→K1 收盘收回，与 K25/K5 近端 4378–4379 尚未收回。A2 的旧边界绑定不清；B1/A1 也没有充分说明两尺度。A2 VPA 对前 40 棒净推进“均有限”的概括丢失 +0.064ATR 对 −4.928ATR 的非对称 |
| PA/VPA 交易员 | Spike transitioning、宽区间/trending_tr 主次差异可合理存在；近期 bearish/AIS、proceed 与最后等待都有可辩护理由；VPA 不创造 Setup/方向/概率/Gate | 竞争假设确认与失效条件仍泛；计划型限价路径的锚点、结构失效、TP1 障碍组合说明不足。无信号、无当前 Entry Attempt 不能独立否决 §9.0P；也不能为凑标签自动确认 Spring/MTR/Absorption |

成交量方面，K3/K2/K1 相对前棒递减，但均高于各自严格因果基线；可评分 VPA 正确区分二者，保留候选/未决后续响应与季节性不可用。HTF.H1 是 K4→K1 的聚合，不是第二份独立确认。未检出决定性错误不是“以后无幻觉”；普通审计 warning 也不是已证明的事实错误。

B1 child 的八项 scope 是 K40/K20/K10/K5 聚合与 K4/K3/K2/K1 单棒，没有 Brooks section 或 HTF。意见对已读值无明显决定性矛盾，并承认结构依据不足；但只围绕 K4 区间及聚合统计，不能充分裁决较宽泛的“Spike vs 成熟区间/趋势型区间”。Schema／引用 accepted 只证明交付边界，不证明专业增量。

## 瓶颈：证据、解释与未知分开

| 可复核观测 | 结论 | 不能据此断言 |
| --- | --- | --- |
| 四份 Stage 1 packet 均 181,613 bytes；Gateway 读取约 0.112–0.185 秒 | 当前读取 I/O 不是数百秒延迟主因；合并必读交付已经实际生效 | 无法因此称模型读入、prefill、网络与 Provider 排队为零 |
| B1 child 92.593 秒；collect 阻塞 0ms；收集后父角仍约 303.134 秒 | 仅加速 child 或缩短 collect 等待解决不了主瓶颈 | “工具没有其他读取”不证明父角在 child 期间闲置；post-collect 也不等于纯隐藏思考 |
| B1 父角色 6 次工具交互／7 次不同完成计数更新，A 各 2／3，B2 3／4 | 多余工具发现、派生、收集带来额外携带大上下文的模型交互；本次并行没有明显削去父负担 | 这些计数不是严格账单请求数；不能把全部差额都归因于单一工具或本机 CPU |
| B1 父 total 445,227，child 22,712；父 reported reasoning output 20,169，A 为 11,947/14,181 | 额外 Token 主要在父角色，不是 child 本身；增加反证后父仍承担完整综合 | 没有提取隐藏 reasoning 内容，不知道具体逐步思考；未隔离 Provider 时延与模型计算，不能精确拆秒 |
| B2 `get_htf_evidence` 请求用了 `evidence_ids`，实际要求 `fact_ids` | 已捕获请求并离线重放 JSON Schema：required/additionalProperties 两项失败，Host 正确拒绝 | 不能以加别名、忽略未知字段或重跑成功替换失败；尚未证明 typed interface 单独能提高行情速度 |
| 委托工具公开 `resources` 为通用对象，真实 SDK 为 `list[dict[str, Any]]`；Host 才按 operation Schema 验证 | 调用层嵌套契约暴露不足，是可修复的可靠性风险；需单一 Schema authority | 不为单条请求叠自然语言提示或容错分支；本轮未修改 runtime 来洗实验 |

主要架构问题是当前 child 接到较宽的竞争诊断，父仍完整读基线、完成自己的诊断，再核对意见并综合。**“工作并行”没有变成“关键路径工作被互补分担”**，反而增加回合与合并推理。这个机制与观测相符，但一次成功协作不能量化某项改造后的确定收益。

## 用量账本：已确认的会话累计口径错误

固定 CLI 的 PA `exec resume` `turn.completed.usage` 是会话累计计数，而 Worker 当前按单角色用量汇总，会重复计算 Stage 1 父角。该结论由同 thread/parent Job 身份、local rollout 的数值 `token_count` 以及[官方实现的 total→turn.completed 映射](https://github.com/openai/codex/blob/main/codex-rs/exec/src/event_processor_with_jsonl_output.rs)共同支持；官方 main 只作机制旁证，本轮实际计数决定该版本口径。

B1：Stage 1 父 445,227＋独立 child 22,712；VPA 64,181；Stage 2 原累计 836,294。Stage 2 新增为 **836,294−445,227=391,067**，与其三个已报告调用计数 100,676＋101,172＋189,219 一致。整链正确小计为 **923,187**，不是原求和 1,368,414。

只能减同一续接会话的已验证**父计数**，不能把独立 child 也减掉；缺字段、身份不符或未完成计数仍 unknown。本轮校正只在研究派生表中，原 records 与 runtime 未改；这是修正观测，不是物理提速。

另有指标口径漂移：A1 VPA 的正常 `auto_bound_evidence_ids` 物化被写为 normalization，进而设置 call 的 `normalization_applied`；与 AGENTS 的“常规自动绑定不算模型 normalization”冲突。保留原标志并注明，后续独立修复计量分类，不据该字段夸大首稿失败或降低专业错误门槛。

## 门禁与下一步

预登记的推进条件：四份完整链、零决定性错误、至少一份成功收集 child、B Stage 1 均值≤A 的 90%、描述性质量差≥−5 分。此次对应 **失败／未检出／通过／失败／描述性通过**。最后一项不是统计非劣；不能抵消完整性和时延失败。

1. **先做离线机械修正，不再付费重验相同设计。** 委托资源公开采用与 Host 校验同源的 operation-specific 参数 Schema；保留角色、窗口、字节与派生次数限制。捕获的 B2 参数用例只作合同回归，不通过猜别名放宽。修正 Worker session-vs-turn 用量及常规绑定指标分类，身份/coverage 不全仍 unknown。这些是后续实现建议，本轮未实施。
2. **再确认互补协作设计。** 保留父完整 mandatory 与裁决权，把一个确切的边界／后续闭盘序列核对尽早委托；scope 同时包含必要旧/近边界、连续棒和对应 Brooks section，禁止再次要求 child 做大半份周期诊断。父处理其余判断，合并只核对新证据与反证；不能省略知识、阻止合理专业推理或让 child 取代父 Draft。预期是减少重复调查与合并范围，不承诺达到 300 秒。涉及分工改变须与用户确认后实施。
3. **确定质量合同，不新增文本 Gate。** 主/替代的确认条件应支持该假设，证伪条件应削弱；明确已发生证据、未来条件、事件边界 ID/范围及其尺度。VPA 保留方向不同窗口的比较限制；§9.0P 给出候选/等待的结构组合依据。这些是跨行情语义原则，不是针对本快照的价位补丁。
4. **下次付费前固定任务生命周期。** 本次 `Start-Process -WindowStyle Hidden` 仍未跨过会话中断；退出机制与准确时刻未知，不归罪模型。后续须先用合成无付费进程验收独立任务容器、取消与持久进度，避免长实验因应用 turn 中断而失去回执；仍先本地，不据此提前部署 VPS。
5. **只有上述离线验收和新设计确认后，才登记新 cohort、新独立行情。** 保存此次负面/缺失结果，不补齐旧 B2、不反复调当前快照到迎合得分。Stage 1 的真实速度/质量门禁通过后，才按原计划推进含因果量价争议的 VPA、含真实 Router 候选的 Stage 2 和组合臂。

## 证据索引与验收命令

本轮不可变研究根目录标识为 `stage1-policy-20260927-03`；存于本机独立 `research/codex-luna`，不进入生产资源。核心文件：

- `registration.json`／`research_protocol.json`：调用前条件与顺序；registration digest=`5d99d3175f2dd39269cc1ac9b126b0c90784c8e0adeec0d0125ec88bdab7f0e4`。
- `receipts/`、`arms/*/host-output.json`、`arms/*/outbox/`：三份持久正式输出、四份 Stage 1 原始 Draft、child scope/意见/失败；B2 Stage 2 封套及完整 receipt 均不存在。
- `coverage-after-interruption.json`、`interruption.json`：3 完整／1 缺失及数值观测边界。
- `metrics-after-interruption.json`、`observed-worker-roles.json`、`parent-stage1-profile.json`：原始／校正用量、阶段耗时和仅数值 session metadata。
- `challenge-start-diagnosis.json`、`usage-scope-validation.json`：捕获参数重放、累计计数与 parent lineage 的根因证据。
- `review-public/packet.json`、`stage1-drafts.json`、`stage1-ratings.json`、`ratings.json`、`findings.md`、`stage1-scores.json`、`scores.json`：盲态资料与已冻结评价。
- `review-private-key.json`、`full-chain-unblinded.json`、`pilot-decision.json`：独立解盲映射与门禁；private key 未交给审阅者。

完整市场 hash=`231e5fc40656507ff08aed916b09414ee7917dfce2961c2d3aff1980b74c1eb9`；domain fingerprint=`bbe38f1bcc156f1a23e0358e5f087fff706a7d8190e5d57e580b153191977bfa`；Runtime asset digest=`35533106bbb41e8943163eab9d6f2ec6fde25d192d2913ff41f74c2492c12d4b`。PA Job hash 是不同投影范围，不能因与整个市场 hash 不同判漂移。

完整链盲包 digest=`2ed99b62e5bc592ab835e0def7d6657fe583cdade8b3ff92be56ece8f3da6e4d`；Stage 1 盲包 digest=`174a9594d4950d7b91374d9023176013bb13a1d2751b40e916d6ba98dc998433`。四份 Stage 1 评分 SHA256=`b38bd9a60711e6f86e27a765707992ffd76c82075736e15a205bcabf9b0d7b61`；全链评分 SHA256=`1b2e6d1b0457fa85cd249557a6d249855a3b5da92fb47a053a52a8e97e7bb107`。

本轮 offline `prepare_stage_policy_review.py` 与 `finalize_stage_policy_review.py` 已执行：复用现有 study export/scorer/unblind API，append-only artifact，provider_calls=0；校验现有 receipt/packet/score digest、全部 case 覆盖与固定权重，并重算结论。不新增运行功能，不重跑此前 217 项工程测试来代替本次行情证据。

`stage1-policy-20260927-01` 网络受限终止、`-02` 仅 Stage 1 返回后中断，两份历史失败另存，不混入本轮速度、质量或独立样本数。保留本轮诊断及未完成 evidence 临时审计，明确不是可发布回执；未删除用户资料，未将研究产物混入 Runtime。

未完成：B2 完整链；VPA/Stage 2/组合 child 市场对照；上述接口/计量/分工建议的实现；独立新行情验证；Linux/VPS、发布全套及前向 Paper。不得写为“subagent 优化全部通过”。
