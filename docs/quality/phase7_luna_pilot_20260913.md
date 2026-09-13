# 阶段 7：Luna/max 真实工程实验补充

更新：2026-09-13。基线 `e21903d` 加本次阶段 8 及兼容性修复。结论：真实工程试跑已执行并揭示适配缺陷，尚无成功完整分析；不得宣称调查增量、非劣、降本或交易增益。

## 输入与实验边界

- 单资产、单来源：Binance USD-M `XAUUSDT`，1h 主周期 100 根已闭盘 K 线，4h HTF；包含冻结 VPA catalog 和完整授权知识资源。
- 冻结 as-of：`1789058015684` ms；快照 hash：`231e5fc40656507ff08aed916b09414ee7917dfce2961c2d3aff1980b74c1eb9`。
- 模型固定 `gpt-5.6-luna`，推理 `max`。两个条件均 `codex_agent / pa_vpa_joint`，只比较 fixed 与 agentic，不能据此比较 DeepSeek 与 GPT。
- 每批调用前登记四个独立臂，实际顺序 BAAB。一个冻结市场样本，不是四个独立样本；跨版本复核也不增加独立样本数。
- Windows 独立 CODEX_HOME 完成登录，使用生产 Spool/Worker/Orchestrator。未部署 Linux，未启用 Paper、监控或真实交易。
- 原 Windows 旧记录缺失严格 as-of/HTF 证据，未猜测补齐；新 `study_capture` 入口复用现有行情与快照校验器，仅捕获输入，不调用模型。

## 已执行结果

| 批次 | 登记摘要 | 完成 / 失败 / 缺失 | 已定位原因 |
| --- | --- | --- | --- |
| pilot-01 | `7d389ec1e04b1c6446c4ae6240f5b904a9133c7d9776bcbb3edb5bd725ee745f` | 0 / 4 / 0 | 本机未安装项目已锁定的 MCP optional dependency；MCP 握手前退出。 |
| pilot-02 | `b37f25372e497bebcc70076510e4b5c7095a4812d55ae5d6fd3bfea03fad08ea` | 0 / 4 / 0 | Codex 服务拒绝输出 Schema 中的 `uniqueItems`；Stage 1 生成前失败。 |

失败回执、原始冻结输入及研究记录保留，不替换、不洗掉失败、不倒填收益。两批不能合并成八个独立行情样本。

额外诊断不计入四臂结果：Schema 修复后 CLI 返回 0，但模型返回合法 `incomplete`，说明只读 Evidence 调用要求审批、无法读取 mandatory 数据。CLI 退出成功不等于分析成功。2026-09-13 再读取旧 Job 时因过期被拒绝，未更改 expiry 或冻结身份。

## 根因修复

1. 依赖：按 `uv.lock` 安装 `desktop + vps + dev` extras；不新增依赖，不删 Windows GUI 依赖。研究运行使用已同步环境，避免无 extras 的再次同步移除 MCP。
2. 错误分类：CLI 新消息 `required MCP servers failed to initialize` 纳入 availability；仍不让 Schema、事实、权限或专业判断失败触发 fallback。
3. Schema：共享 generation projection 移除 `uniqueItems`，本地原始 Schema 继续验证唯一性；同名业务属性不误删，其他约束不因单次错误放宽。参照 [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) 的受限 Schema 原则，具体拒绝由真实错误确认。
4. MCP SDK：旧定义有只读 annotations，但 SDK decorator 未携带，实际 `tools/list` 缺失。所有冻结 Evidence 工具现在复用原只读、非破坏、封闭世界标记；未更改沙箱和 `approval_policy=never`，未设置全局无条件放行。参照 [Codex MCP 配置](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。真实审批问题是否完全消失仍待新 Job 验证。
5. MCP 反馈：只将受控权限/预算拒绝转为 SDK `ToolError`，保留可执行的停止说明；非预期异常仍由 SDK 隐藏。测试客户端补足标准 initialize、capabilities、clientInfo 和 initialized 通知，参照 [MCP 生命周期](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)。

这些是接入层缺陷，不是 PA/VPA 理论、Router 或交易数学的缺陷；没有通过修改知识或交易结论来让样本通过。

## 验证和三角色自审

239 项本地定向检查通过；其中真实 SDK 子进程验证完整工具列表、全部只读标记、冻结事实读取与越权拒绝。不是模拟 MCP 握手。关键 Ruff 与 diff 检查通过。

| 视角 | 判断 |
| --- | --- |
| 软件架构师 | 继续使用单一 Host/Router/Validator；修复 SDK 边界而不放开 shell/网络/代码修改权限。Windows 验证不替代 Linux 隔离验收。 |
| 量化工程师 | 两批失败留在覆盖率分母；缺失 usage 不记为零成本。单快照无统计效力，不能盲评不存在的完整结果或编造校准数据。 |
| PA / Wyckoff VPA 交易员 | 未删 mandatory、HTF、反证或案例；没有真实完整输出，尚不能评价专业分析质量。模型无法取证时停为 incomplete 而非编造 No Trade，是正确失败边界。 |

以上为同一实现者的三个视角，不是独立专家盲审。

## 后续执行顺序与当前阻塞

新的 pilot-03 登记和执行命令被权限审核整体拒绝，尚未建立 pilot-03 或发送该批请求。理由是需要对具体数据向 OpenAI Codex 发送的明确授权。不能改用别的命令绕过。

1. 获用户明确授权：发送上述冻结行情、PA/VPA 知识及角色上下文至 OpenAI Codex，用于 Luna/max 实验；不发送账号凭据、数据库或整套项目源码。
2. 用新登记与新 ephemeral Job 运行同一输入的四臂工程复核，保留旧登记。历史冻结输入只作回放，不冒充当前可执行行情；不重写 as-of。
3. 先核对 required reads、Schema/Validator、完整 Stage 1→VPA→Router/Stage 2 交付与失败指标，再导出盲评包。未产生完整结果不得填评分。
4. 另行收集足量前向、跨市场背景样本，完成盲评、重复臂稳定性、完整用量和延迟评估；到这一步之前不做效果晋级。

本机研究产物位于用户指定的研究目录（当前 `D:\new_paagent\research\codex-luna`）；凭据仅在其独立 `home/auth.json`，不进入 Git、评审包或报告内容。Linux VPS 未改动。
