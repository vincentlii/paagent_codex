# 增量分析与等待条件连续性

- 状态：代码d9c9fe6已提交、推送私有main并发布VPS；真实模型质量未验。
- 基线：`f59b08f635ef770a9c9872152faaa0d4937527b6`。
- 范围：Headless Frozen任务、Direct/Codex Stage 2历史交接、旧分析兜底及相关知识原句。

## 根因与完成标准

Headless已有新闭盘轮询、同源成功记录选择和Stage 1增量诊断，不是完全无记忆。
缺口在Stage 2：旧Joint结果被price-only投影移除，旧连续性只识别有单方案，因而WAIT理由和
watch_points没有进入下轮联合决策。分析时CSV兜底未冻结来源与时间，不能补上这个缺口。

完成标准：成功WAIT也可交接；旧K按原时间映射且修订/缺失不猜；同源、可用时间和协议边界可验；
新冻结身份覆盖全部新消费字段，旧任务/无历史任务保持兼容；Direct与Codex真实组装路径均交付；
Stage 1与独立VPA不泄漏旧Joint结论；模型仍可合法WAIT/TRADE，已有订单推进不变；旧逻辑先备份
再替换，完整非live回归、静态检查与安装资源验证通过。

## 实现与三角色判断

`decision_history_v2`只携带一份上一轮成功记录。模型原reasoning、watch_points及相关计划字段
完整保留；排除旧概率、joint_analysis与执行状态。时间、同源身份、官方路径和协议由程序核验，
旧K引用通过开盘时间和OHLC精确对应当前K号。历史结果须已在当前as-of前可用，旧K1须已闭盘
且仍在当前已闭盘窗口；不能核验时直接不提供历史，不补造结论。

沿用Headless现有增量范围：新闭盘数量在1到`incremental_max_new_bars`内才选上一份成功记录，
默认上限10；无同源成功记录、旧锚点出窗或超过上限时重新全量分析，不恢复更早的“待办队列”。

模型在已有decision.reasoning中复核有实质意义的条件：满足、未满足、失效或未知，并用当前证据
解释。watch_points只延续仍有意义的条件，允许重新判断和放弃旧假设。不增加必填输出状态、
自然语言条件解析器、数据库或模型阶段，也不延续跨Snapshot会话。

- PA：结构、Setup、交易价值、Entry/SL及是否已确认仍由LLM在当前Stage 1/唯一Router内判断。
  不把“新证据满足旧条件”等同自动下单，亦不要求每轮重新等下一根。
- VPA：历史Joint意见仅进入Stage 2；Stage 1仍price-only，独立VPA保持当前冻结证据的独立分析。
  旧量价引用不成为本轮事实，不要求VPA支持交易或重复旧结论。
- 量化/架构：历史意见与当前测量分离；来源、已知时间、原棒及消费字段绑定Frozen身份，篡改拒绝。
  有界一份历史、无新增调用/依赖/执行状态机；不修改订单、盈亏、方程、风险或历史结果。

## 删除与保留

删除分析时全局CSV fallback；CSV读取与关系计算仍由trade_logger使用，保留日志职责。
替换原决策知识§9.0P及旧上下文中的撤单/反手指导，不追加第二套Prompt补丁。
旧Host Frozen任务缺marker仍按旧计划语义恢复，新含历史任务明确版本；未知版本拒绝。
旧兼容分支和日志函数有调用者，不能当作无用代码删除。Runtime Skills不重复抄入新增说明，
由既有digest-bound角色协议统一持有；现有Schema、materializer和知识章节锚点不改。

## 备份与恢复

目录：`docs/changelog/backups/20261022-wait-continuity/`（本地备份，不作为运行资源）。

| 归档 | 用途 | SHA-256 |
| --- | --- | --- |
| source-before.zip | 13个源码、测试、Runtime与文档原件 | `33437229e321561c6f89ddcf8a5fecb7c89b890c0d819fbf1c2cd3438b468fb1` |
| agent-before.zip | 冻结任务三模块及相关测试原件 | `89770b984100d036240ea79301f17b8e9df78018aaa7526dc5fed5beb99696ed` |
| knowledge-before.zip | 本任务修改前与HEAD一致的二元决策原文 | `0702df9c5b7cd4890c1d054c0ad597afdda3b0c59e5b36185e689582298652d2` |

各归档均有manifest与逐项原文哈希并在关闭后重读核验。恢复应在无排队/活动分析时整包恢复对应
源码、教材与Runtime身份，不能让旧reader接管新marker任务；不恢复或改写交易数据库。
既有ICT文档/AGENTS及导航变更保留，不属于本次清理或实现。

## 验证

| 项目 | 结果 |
| --- | --- |
| 完整非live回归（Windows，Hypothesis/AnyIO启用） | **2090 passed，7 deselected，119.66秒** |
| 本次领域/Frozen/实际交付用例 | 25 / 12 / 7项，已包含在上述总数中 |
| 新鲜扩大聚焦（完整回归之前） | 166 passed；不与完整回归累加 |
| 严重静态错误检查 | `ruff --select E9,F63,F7,F82`通过 |
| 空白与补丁检查 | `git diff --check`通过 |
| 离线wheel构建与临时目录安装 | 通过；269成员源码/wheel/安装目录逐字节一致 |
| 安装后5类CLI模块、知识文件与Headless无Qt导入 | 通过 |

完整命令：`python -B -m pytest -p _hypothesis_pytestplugin -p anyio.pytest_plugin -m 'not live' -q -o addopts= -p no:cacheprovider --tb=short --basetemp=.pytest-wait-full`。
两个warning均来自既有tvDatafeed依赖的非法转义弃用提示，不是本次业务校验警告。
新身份、协议/损坏历史、未闭盘与重复棒边界均先复现失败再修复；未放宽订单或事实验证。

实际组装使用Mock Provider但完整真实canonical materializer、Validator、Orchestrator、Publisher、
FrozenJob Store和MCP基线读取：Direct/Codex的WAIT与TRADE均仅3次角色调用，旧WAIT原文只到
Stage 2；PA-only/off实际2次调用且没有旧Joint/VPA泄漏；Schema保持，历史改变影响Job digest。
覆盖旧无marker恢复、无历史身份保持、7类新增历史消费字段篡改拒绝和deepcopy隔离。

wheel SHA-256：`73b6445259f5d4ea101b4b8f60b79769b3b1f4633c71b900420eb67280603067`。
JUnit、安装核验和开销JSON保存在上述本地备份目录。测试与安装临时目录、build/egg-info已清理；
保留备份及少量验收凭据，未改uv.lock、依赖、数据库或生产文件。

## 性能与未验证边界

100棒离线样本只加入一份等待意见，旧/新连续性块为92/2023字节，cl100k估算34/721 Token，
增量687 Token。组装/渲染500次约0.3毫秒量级；这是本机估算，不是Provider usage或模型延迟。
不增加LLM调用，不复制完整旧行情到模型，不递归拼接旧会话；实际Token仍取决于模型输出长度。

只用离线数据，不调用Provider、行情或交易所API；未验Linux本次构建、真实模型匹配回放/盲评或
发布后自然运行。工程通过不能证明模型会减少不合理WAIT、提高交易频率或改善收益。

测试同时发现一处**既有公共工厂边界**：显式`create_frozen_analysis_task(..., htf_context=None)`
按None计算hash，却把payload保存为{}，codec恢复复算不一致。本次Headless开/关HTF均传明确
available/unavailable/disabled字典，不受该边界影响；新身份测试使用实际字典形式，不把它当作
本次篡改红测。未在本轮夹带修改旧hash或宽松恢复，独立修复时须测试旧身份兼容。

## 固定提交 VPS 发布回执

用户明确授权commit并发布；运行代码`d9c9fe65817272c09d54766f3d6746af93bd25fe`，
既有ICT四项变更保留未提交。发布前新鲜44项聚焦回归、关键静态及diff检查通过。
分析暂停排空至paused63，queued/running/unfinished均0、inbox为空、无恢复阻塞。

Host备份`/var/backups/pa-agent/pa-agent-20261022T141644Z`（17961成员），
Demo备份`/var/backups/pa-agent-demo/demo-1792678630476`（242成员）；
manifest逐项及四库逻辑等价验证通过。安装269成员与固定源码/wheel逐字节一致，
48项依赖、4库、1465个受保护文件在安装前后保持；Runtime权限审计通过。
Linux wheel SHA256：`18b5fe936fea73d865641683df3dd1cd8a0a7cf9aaefb663f9ff00ad85b13fdc`。

恢复running64，四服务active/running、Result=success、NRestarts=0；三品种connected，
Codex认证healthy、Runtime digest保持；Demo ready且七项coverage全真，无持仓。
原三品种/500USDT/3h/fixed_rr1.5、gpt-6-luna/xhigh/Fast/并发3及Profile保持。
五类HTTP接口200、未认证status401，6笔复盘可读；18 fills/18 bills原文保持。
未通过重跑或补发旧计划验收。新完整模型链、等待判断改善及收益仍未验证。

### 本次发现的停服行为

发布前有1笔BTC零成交live挂单；发布后它为canceled/closed，fills未增加。
`demo_worker.main`正常接收SIGTERM退出时，将entry_enabled设false后执行最后一次tick；
`demo_independent.manage`在entry_enabled=false时调用_cancel_entry，故协调停服会撤未成交余量。
本次不是3h到期或新分析替换；发布脚本没有直接调用交易所撤单，也没有重新提交旧计划。
此前“保留现有挂单”的发布说明不成立，已向用户明确更正。需独立区分维护重启与显式停用入场，
并验证部分成交保护和恢复边界；未在本轮发布中追加未验收的执行语义改动。

发布资料及脱敏receipt位于`docs/changelog/releases/20261022-wait-continuity/`及
`/var/backups/pa-agent/releases/wait-continuity-20261022/`。临时构建和上传包已清理，完整备份保留。
