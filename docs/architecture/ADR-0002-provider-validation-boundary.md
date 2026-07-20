# ADR-0002：模型调用与校验严重度边界

- 状态：Accepted
- 日期：2026-07-20

## 背景

旧链路的首轮分析、校验修复和自由追问分别调用 provider，错误分类与重试行为不一致。
`APIStatusError` 曾被统一视为网络错误，400/401/402/422 也可能触发 fallback；本地 fallback
还会原地修改并保存用户设置。校验器同时把 Schema、安全错误、事实矛盾和自由文本措辞问题
都当作硬失败，导致合法的模型表达变化中断整次分析。

## 决策

所有模型调用委托 `ModelCallRunner`：

- 根据明确配置或已验证 provider 能力选择 `json_schema`、`json_object` 或 `text`。
- DeepSeek 原生接口使用官方支持的 `json_object`；未知 OpenAI-compatible 网关默认 `text`。
- 仅对 429、timeout、connection、5xx 与 empty response 做最多两次 transport attempt。
- 400/422 请求错误、401/403 鉴权错误与 402 配额错误不可重试，也不可触发 provider fallback。
- provider 明确拒绝 structured-output 参数时，允许一次可审计的 text 降级。
- fallback 使用 Settings 深拷贝并创建本次运行专属 client；不得修改、保存或泄漏回用户配置。
- 每次正式 call ledger 保存 provider、structured-output mode、fallback 状态和 transport attempts。

校验结果分为三类：

- `hard_error`：关键 Schema 无法恢复、未知或越界证据、结构化程序事实矛盾、权限越界、
  非法订单、no-order invariant 与订单数学错误。
- `normalized`：无损容器、枚举别名和记号规范化。
- `warning`：自由文本措辞、可选引用、摘要完整度、事件标签不确定性和基于 regex 的
  叙述质量发现。warning 写入审计，但不重试、不阻断。

缺少 `jsonschema` 时必须 fail-closed，不得跳过后续校验并伪装成功。

## 后果

- 外部 provider 故障成为类型化 `provider_error`，不得形成半成品订单或未处理崩溃。
- 同一逻辑调用可有多个 transport attempt，但 validation retry 仍是独立、可审计的模型调用。
- 叙述 warning 不代表内容正确；后续应通过结构化 Claim 与 evidence ID 提高可验证性，
  不能重新把自由文本 regex 升格为交易安全 Gate。
- provider fallback 只改善本次可用性，不改变下一次运行的用户选择。

## 验证

- provider 状态码、structured-output 降级、empty response 与 fallback 均有故障注入测试。
- 1000 次合法 JSON 包装、空白与键序扰动的误硬拒绝率必须低于 1%。
- 完整非实时测试必须同时覆盖原 PA、VPA off、VPA Shadow、HTF 与自由追问。
