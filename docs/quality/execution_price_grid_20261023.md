# 执行价格规则与未提交流水

- 范围：本地修复；未commit、未发布、未调用真实模型或交易所私有API。
- 基线：`c66b1aa64c105e23b390ea20d2cecf4b2bd1d0cb`。
- 用户授权：自动转换合规执行价格；无额外LLM步骤，保留原结构提案并审计变化。

## 根因与方案

北京时间2026-10-22 23点XAU建议Entry=4101.32、SL=4109.69、TP=4088.8，来源tick0.01，
目标OKX tick0.1。Host分析eligible，Worker持久接收后以target_tick_mismatch拒绝，未生成request。
流水只读request非空记录，提交前拒绝被隐藏。原价在真实bind_demo_limit离线复现同一拒绝。

采用ADR-0027的2026-10-23增补，所有执行价格转换由程序负责，无新增模型/网络调用。
新任务冻结严格`execution_price_grid_v1`到frame metadata/Host身份/cohort及Stage 2协议。
唯一materializer保持模型原decision，单独生成price_adjustment，先定向取整再核对方程与资格。
PA限价TP朝入场取整，fixed_rr按有效Entry/SL重算；信心/概率不改，只应用原阈值。
限价买向下/卖向上，SL向结构外侧；每次直接Entry/SL调整不足一tick，不猜tick或加跳凑几何。

这不是新的市场判断或无损语法归一，不修改stage1、Router、VPA解释或normalizer。
原引用/信号检查保持，执行几何/方程使用有效价格；取整无效时保留原candidate并抑制执行。
下游计划/Intent统一消费有效投影，读取复算，Worker核对当前tick；旧无marker保持严格原价。

XAU原例转换：Entry=4101.4、SL=4109.7、TP=4089.0（1:1.5，目标按tick朝entry取整）。
TP相对原值可改变多于一个tick，因为它按新风险重新计算；不能宣称全部价格变化都小于一tick。
没有更改这笔旧订单、补发计划或改写历史分析。

## 可见性与统计

已登记但request为空的limit/rejected计划进入最近150项流水；普通no_trade不进入。
显示“未提交（原因）”，不假称已撤单或成交；真实入场数量统计仍只计request非空。
只有明确拒绝、filled=0且不存在执行legs、退出子单或自身fills冲突时显示0，其余未知并等待同步。
保留源身份以绑定自身原复盘；新计划可在复盘查看模型原价、执行价与tick。
未扩展Dashboard数据库权限或添加模型调用，也没有修改现有历史PnL/统计。

## 三角色复核

- PA：结构、Setup及概率仍归模型；程序只做用户授权的最近价格网格适配。原案与执行价分离；
  invalid geometry或非正方程不能靠改结构、补跳或改概率绕过。
- VPA：量价证据及反证不受价格网格影响，没有独立投票、方向或订单权限。
- 量化/架构：唯一物化器，Decimal运算，policy冻结/恢复/篡改校验，旧协议不升级；
  订单/日志/页脚分别表达原价、有效价、实际成交及未提交，不混算状态或PnL。

一名协作者先完成审查，再独立实现流水可见性；价格链由主代理完成并由其只读复核。
审查指出fixed_rr TP变化可能超过tick，已纠正说明，未引入新的距离Gate。

## 备份、清理与验证

主备份`docs/changelog/backups/20261023-execution-tick/source-before.zip`，493成员，SHA256
`90d310f0af385214e9bd7c6dd1913bd1f029f3b393c7449d1dad707d128e4a22`；manifest逐成员核验。
流水原件备份`docs/changelog/backups/20261023-execution-prices/journal-before.zip`，SHA256
`93c0ed5185329b0f534f19eb3a37546589bd8fa007fad2362020985a054f072e`。
保留既有ICT四项变更和上次排障笔记；未覆盖用户既有修改。无数据库migration或新增依赖。

| 验证 | 结果 |
| --- | --- |
| 最终完整非live（Hypothesis/AnyIO启用） | **2113 passed，7 deselected，115.23秒** |
| 价格策略与实际Host/Worker链 | 15项，包含在完整数量中 |
| 未提交流水与页面边界 | 8项新用例及实际Node渲染/复盘点击，包含在完整数量中 |
| 严重静态错误及diff检查 | `ruff --select E9,F63,F7,F82`、`git diff --check`通过 |
| 最终wheel构建/离线安装 | 270成员与源码及安装目录逐字节一致，5类CLI导入通过，Headless无Qt |

命令：`python -B -m pytest -p _hypothesis_pytestplugin -p anyio.pytest_plugin -m 'not live' -q -o addopts= -p no:cacheprovider --tb=short`。
两条warning来自既有tvDatafeed转义弃用，不是新增业务校验警告。未新增依赖，uv.lock不变。
最终wheel SHA256：`43d0d59f122d24b5739c5a1895905c9a785ffef79f4bd2d7c38515bd8f863d88`。

覆盖原XAU价格、双向/非十进制幂tick、PA/fixed目标、调整后方程失败、风险重合抑制、原概率不变、
Direct/Codex既有三调用、Frozen恢复篡改拒绝、原/有效Intent复算、当前tick变化拒绝、
Host→Intent→Worker→投影与同账本重启不重复。模型canonical不能自报有效投影绕过唯一物化器。

第一轮完整回归2112通过/1失败：新可选price_adjustment=None被序列化进模拟旧Intent的测试输入。
修复新空字段省略后，旧形状/摘要/客户ID保持；38项兼容及最终完整回归通过。没有改旧摘要或宽松验证。
价格/身份/流水边界先红后绿；个别集成fixture原本缺role_runtime或残留无关TP2，按真实测试场景校正，
未因此放宽程序规则。原全量失败、最终JUnit及性能/安装凭证保存在本地备份目录。

临时pytest目录、构建/安装目录及egg-info已清理；源码旧helper调用已删除，保留有调用者的旧协议兼容。
未调用真实模型/交易所、未改VPS或订单、未commit。离线Mock交易所测试仅证明程序行为，
不能替代真实Demo成交生命周期、Linux发布或收益评价。后续发布需协调新reader，且必须考虑
已知维护退出会撤挂单的行为，不得承诺停服保留活动挂单。

## 性能

本机2000次执行Entry/SL适配、fixed_rr目标、投影复算及数学/置信度阈值检查，
中位约0.049毫秒，p95约0.0553毫秒。不新增LLM或网络请求；目标tick复用Worker已有投影，
不在每次分析前多查一次交易所API。真实负载耗时仍以生产实测为准。

依据：项目现有OKX合约price_rules/绑定校验及[Python Decimal官方文档](https://docs.python.org/3/library/decimal.html)
的精确十进制与定向取整；不硬编码XAU当前tick。
