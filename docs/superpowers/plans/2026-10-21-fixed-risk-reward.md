# 可选固定盈亏比退出模式

用户已授权完整实现；本轮范围为本地代码、面板与离线验收，不自动 commit、部署或改变线上配置。

## 设计与权限

- AnalysisProfile 增加 `take_profit_mode=pa|fixed_rr` 和 `fixed_reward_risk_ratio=1|1.5|2|2.5`；默认保留 PA，固定模式初值 1.5。复用 CAS、idle apply 和冻结身份。
- PA 交易员保留 Setup、入场、结构止损、主观概率及交易/等待判断。固定模式概率对应计划入场后固定目标先于结构止损的事件；不得用盈亏平衡胜率填充。
- VPA 保持独立证据角色，不增加方向、Setup、目标或概率权限。
- 固定模式 Provider Draft 不输出 TP1/TP2；唯一 PA materializer 从 Entry、SL、已冻结倍数和真实目标 tick 生成 TP1，再运行原 Trader Equation / Validator / execution disposition。无额外模型阶段。
- 目标向入场方向取整到真实 tick，原 Entry/SL 不改。记录请求倍数与实际计划 RR。没有真实 tick 时不猜精度；可执行候选明确失败，No Trade 保留原语义。
- OKX Demo 的目标 tick 从执行 Worker 已有只读投影交付，Host 只接收新鲜、完整、身份匹配的规则，冻结进任务及 cohort，不能进入市场 hash。Worker 下单前仍用当前合约规则复核，不偷偷改价。
- 模式、倍数、tick 和目标来源进入记录及新 Intent；旧 Frozen Job、Intent、挂单、持仓、历史不重算。PA 默认省略新默认身份字段，保留旧指纹。
- 不改数据库 schema、不回填历史、不增加依赖；原有 ICT 工作区变更保持不动。

## 验收边界

1. 唯一物化器：多空四档、半 tick、Trader Equation 抑制、不下单、非法几何及缺 tick；证明模型不能提交程序 TP。
2. Profile / Host：严格枚举、旧身份、CAS、idle apply、同市场不同模式或 tick 的 cohort、codec 重启与恢复。
3. 普通 PA、VPA off/shadow、联合 Stage 2 的 Direct/Codex 请求、Schema、digest、repair 与冷回退一致；研究默认保持 PA。
4. Demo：冻结价格到 Intent / 保护请求贯通，旧 Intent 摘要兼容、不同模式计划独立，当前 tick 改动拒绝而不改价；面板显示逐计划冻结模式与实际 RR。
5. 聚焦测试、完整非 live Headless 回归、关键静态检查、wheel 资源和无 Qt smoke、差异检查与临时产物清理。

离线测试不证明收益提高，不冒充真实交易所或模型验收。
