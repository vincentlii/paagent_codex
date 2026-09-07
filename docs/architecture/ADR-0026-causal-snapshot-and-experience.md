# ADR-0026：闭盘观测与经验因果边界

状态：本地实现；阶段 2 验收记录见 `../quality/joint_agent_implementation_plan.md`。

## 事实与责任

Binance 与 Bitget 必须先取得服务器时间，再抓取 K 线；一次分页使用同一时间边界。
服务器时间变晚不代表较早返回的 OHLC 已包含最后成交。快照只接纳 provider 确认闭盘的棒，
并在显式 as-of 下要求理论收盘时间不晚于边界。旧 `closed=False` 不因倒计时结束而重标。
需要新一次 Provider 响应确认；棒数不足明确不可用，不补棒。显示倒计时函数不拥有数据最终性。

Headless 在主周期取数前固定 `analysis_as_of_ms`，HTF 复用同一边界。快照保留该值，
完整 record 的 role_runtime 和 Codex frozen_snapshot 保留观察时间，与 cutoff_ts_open 区分。
它不是模型完成时间或订单可用时间；后两者在阶段 6 接入。没有可靠边界的历史保持未知。
当前构建器针对固定时长周期；不新增月历/交易日历推断能力。

## 经验池

经验默认关闭。启用时 Headless 在任务冻结前复制经验池；GUI 有明确 as-of 时在第一个模型调用前冻结。
Stage 2 只读该 pool，不再读取实时目录或回退到旧 top5。没有冻结池时记录
`unavailable_no_frozen_pool`，不补造边界。实验在启用经验但缺少预冻结池时，在模型调用前拒绝。

案例内容必须包含 `causal_context`：

```json
{
  "event_at_ms": 100,
  "outcome_known_at_ms": 200,
  "revision_at_ms": 300,
  "source_identity": {
    "provider": "example",
    "requested_symbol": "SYMBOL",
    "resolved_symbol": "SYMBOL",
    "venue": "VENUE",
    "instrument_type": "spot"
  },
  "timeframe": "1h"
}
```

数值是 UTC epoch milliseconds 示例；必须 `event <= outcome_known <= revision <= as_of`。
来源、venue、实际品种、合约类型与周期必须匹配。旧文件名不能证明结果何时已知，缺失字段的案例不注入。
旧列表功能仍可查看文件；文件名排序固定按 UTC 解析，不再依赖 Windows/Linux 本机时区，
但不得将这个排序时间用作案例因果凭证。历史案例不自动回填、改写或迁移。

pool 按完整规范化 JSON 计算 digest；选择前重新核验。pool 内容随 record 持久化并由 replay 恢复。
任务与实验 frozen_input_hash 绑定 pool digest 和 as-of，market_snapshot_hash 不因池变化增加样本数。
旧记录没有 pool 时保持旧 hash 组成。PA/VPA 四臂及 Backend 四臂从同一冻结输入读取，不能各自扫描目录。

## VPA 与回放

get_market_core 向 VPA/研究 Joint 暴露已有 digest-bound catalog 的全部非 evidence 行元数据，
包括 provenance、volume kind、availability、measurement spec 与 comparator 状态。PA-only 角色不可读。
证据行仍单独读取计费；没有复制新知识或推导交易标签。

回放必须保留数值表示：原先 `amount=0.0` 被 `or 0` 改成整数，导致原始 JSON hash 漂移。
保留原字段数值，不放宽 hash 校验，不修改旧记录。

## 验收范围

三角色自审：软件侧确认唯一快照/校验链与有界引用；量化侧确认前视、身份、池漂移和独立样本隔离；
PA/VPA 交易员视角确认没有机械识别市场结构、不把 unknown volume 解释为低量、保留证据不足状态。
测试只证明工程因果边界，不证明 Provider 数据准确、模型专业判断提升或交易获利。
未部署 VPS、未调用真实行情/模型。扩大回归中的两项基线失败保留为发布前待处理项。
