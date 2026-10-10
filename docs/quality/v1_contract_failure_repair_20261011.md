# V1 ETH / BTC 契约失败根因与最小修复

日期：2026-10-11（Asia/Shanghai）。范围：Headless Provider 修复与 Codex Evidence 交付；不涉及 GUI。

## 结论

两次失败均有确定的契约层根因，不应以新增交易 Gate、Prompt 或 Skills 规则修复。
本轮仅修改三个产品文件；未改知识、Runtime AGENTS、Skills、Prompt、模型、
hash 算法、数据库 schema 或交易执行逻辑。用户已授权提交发布，实际发布回执另行登记。

## ETH：canonical 错误路径没有映射回原始 Draft

2026-10-10 14:00 的 ETH 任务：

- run：`62f9cd62edfe45b1b97d3ec03bed664c`。
- task：`bcfd3fba5fc715f308b03a97ef817ad12ce914b076c90ec835670c0a75f2bb8f`。
- 首稿 Job：`1b9dabfb5aae963401785c2058378742a9fd1378856d30632820baa3c2d835f6`。

首稿遗漏整个 `incremental_delta`。Normalizer 仅补程序拥有的增量计数和变动字段，
没有替模型编写 summary；这是正确边界。错误分类却丢失 `err.absolute_path`，
将嵌套 required 错误变成 `/summary`，与正确的 `/incremental_delta/summary` 并存。
既有修复发布器在第二次模型派发前拒绝了 Schema 之外的 `/summary`。

只修正叶路径仍不完整：原 Draft 没有父对象，现有 Direct 投影及 Codex Patch 赋值器
不能在不存在的容器内赋值。因此在唯一 Validator 映射处同时解决两层问题：

1. required 错误保留完整父路径及数组下标。
2. 完整目标先通过 Provider Draft Schema 所有权确认，再沿原 Draft 找第一个实际
   缺失的字典 key，授权其路径；原父对象存在时仍只授权叶字段。

缺整个容器时授权 `/incremental_delta`，已有容器时授权
`/incremental_delta/summary`。保留原错误信息，模型提供缺失判断，然后完整重验。
不自动生成容器或 summary，不提升 null、错误类型、数组越界或 canonical-only
字段的权限，不改两个赋值器，不增加重试次数。

## BTC：两个正确但不同作用域的 hash 被模型当作同一种身份

2026-10-11 00:00 的 BTC 任务：

- run：`13235a61098d443eae3ed676bc6fb75e`。
- task：`64114a53b4d9889416e410803b21c1682cee1daf7784086391538dbeea317ecc`。
- VPA Job：`f62a677c25891925ee52069f472e063f07e039bcb9294fdc06bd85eaed162b0c`。

Stage 1 已验证。VPA 约 86.18 秒后返回 `review_required`，认为
`vpa_catalog_context.snapshot_hash` 与 Frozen Job 的 `snapshot_hash` 不一致；
联合 Stage 2 未调用。这不是 Fast、线程容量或真实行情错配。

原 frozen frame 复算结果：

| 身份 | hash | 范围 |
| --- | --- | --- |
| Job / PA | `51c6112839ae8819900a8eae8b7ae006eadafd55518fadc22f4e355cc1bbdbdb` | OHLC + EMA20 / ATR14 |
| VPA catalog | `230d41e712a2c71bbddfa4bd527e1b6711ac51f090e1e542c3869a1d174f8cd0` | OHLCV + 来源 / seasonality |

两者分别匹配同一原始 frame；完整 catalog 通过既有
`validate_frozen_vpa_catalog`，handoff 价格身份和 cutoff 也一致。
模型交付层却均命名为泛化 `snapshot_hash`，没有明确作用域及同源关系。

最小修复：Publisher 在筛选 Evidence rows 之前复用完整 catalog verifier，
形成 `vpa_catalog_binding`，含 `price_snapshot_hash`、`ohlcv_snapshot_hash`、
`catalog_id` 和 `verified_same_frame`。Gateway 只对带此 binding 的新 Job 将
模型可见 catalog 字段改名为 `ohlcv_snapshot_hash`；旧冻结 Job 原投影不变。
原 catalog、catalog ID、事实引用和 hash 算法均不改，不把两种 hash 强行设为相等。

## 验证与边界

- 根因用例先失败，再修复：ETH 路径 / 容器和 BTC binding 三类问题均离线复现。
- 278 项定向回归通过，耗时 26.11 秒；覆盖 Validator、Direct / Codex 修复、
  Publisher、mandatory Evidence、MCP、联合编排、handoff、VPA 测量、Paper 交付及旧 Job。
- 关键 Ruff 检查（`E9,F63,F7,F82`）及 `git diff --check` 通过。
- ETH 原真实首稿经过实际 retry seam，权限仅为 `/incremental_delta`；使用明确标注
  的 **离线 summary 测试夹具** 后完整重验通过，所有其他原始字段不变，只有一次修复。
  这不是模型重新完成专业判断，不能宣称真实分析质量已复验。
- BTC 原真实冻结任务经当前 Publisher / baseline / mandatory-read 检查通过，
  所选 Evidence 与原任务完全相同；去除 binding 的旧 Job 保留旧投影。
- 独立复核另覆盖六个权限边界：完整容器缺失、已有父、canonical-only、null 父、
  错误类型父和空 trace 数组；未发现授权扩大。

没有调用付费模型、重跑历史失败或修改原始记录。部署后的下一自然任务才提供
真实模型验收；离线验证不证明所有未来模型错误、延迟或交易收益已解决。

## 三角色复核

- 软件架构师：只修 canonical → Draft 路径映射和模型边界身份表达，不建立第二套
  materializer、Patch 赋值器或身份算法；旧冻结协议兼容。
- 量化工程师：同源证据经原 verifier 确认，权限可追溯，历史失败保留；离线夹具
  与真实模型质量分开，不能把测试成功计为分析样本。
- PA / VPA 交易员：summary 仍由模型判断；完整知识、Router、Stage 1 → VPA →
  联合 Stage 2、结构失效、主观先验及执行安全保持原样，不增加交易限制。

## 发布与回退

用户已授权备份、提交发布。发布前暂停并排空，验证一致性备份，停止三个服务后
安装固定提交；先在 paused 状态核对身份与预检，再恢复原 Paper 配置。
无 schema 迁移；回退应重新安装固定旧包，不顺便回滚正常新增 Paper 数据。
实际运行提交、备份及恢复核验待发布后追加，不能以本地测试代替 VPS 回执。
