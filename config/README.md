# 本地配置说明

本目录下的**运行时文件**默认已被 `.gitignore` 忽略，不会进入 Git 仓库。

仓库同样**不会上传**：`records/`（分析落盘）、`experience/`（经验库内容）、`logs/`、`trade_records/`（交易 CSV/截图）、`.env`、根目录临时图片与个人笔记等。仅源代码、`prompt_engineering/` 策略文本、`tests/` 与 `docs/` 说明文档会进入 GitHub。

## 首次使用

1. 复制模板为本地配置：

   ```cmd
   copy config\settings.example.json config\settings.json
   ```

2. 启动程序，在 **设置** 中填写你的 **API Key**（会加密写入 `api_key_encrypted`，本机解密密钥为同目录下已忽略的 `secret.key`）。

   也可直接编辑 `config/settings.json` 中的 `base_url`、`model` 等字段，Key 仍建议通过 GUI 保存以便自动加密。

3. `config/exception_state.json` 由程序在需要时自动创建，一般无需手动复制。结构可参考 `exception_state.example.json`。

4. 如需自定义 TradingView 品种别名，复制模板：

   ```cmd
   copy config\tv_symbol_aliases.example.json config\tv_symbol_aliases.json
   ```

## `settings.json` 字段说明

配置分为四个顶层组：`provider`、`general`、`prompt`、`validation`。

### provider — AI 提供商

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `provider.model` | string | `"deepseek-v4-flash"` | 模型名称（须与网关支持的名称一致） |
| `provider.base_url` | string | `"https://api.deepseek.com"` | OpenAI 兼容 API 根地址。DeepSeek：`https://api.deepseek.com`；MiMo：`https://api.xiaomimimo.com/v1`（程序自动处理 `enable_thinking` 与 `reasoning_content` 回放） |
| `provider.api_key` | string | `""` | API Key 的内存字段；保存时强制清空，不以明文写入 JSON |
| `provider.api_key_encrypted` | string | `""` | Fernet 密文；由 GUI/`save_settings` 自动写入，本机 `secret.key` 用于解密 |
| `provider.thinking` | bool | `true` | 是否启用思考/推理类扩展参数（依模型与网关而定）。关闭可 3–5 倍提速但分析质量下降 |
| `provider.reasoning_effort` | string | `"high"` | 推理深度：`low` / `medium` / `high` / `max` |
| `provider.context_window` | int | `2000000` | 用于上下文占用提示的窗口大小（tokens） |

### general — 通用设置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `general.last_data_source` | string | `"tradingview"` | K 线数据来源：`mt5` / `tradingview`（GUI 下拉选项）；`akshare` / `eastmoney` / `tushare`（代码支持） |
| `general.last_tradingview_exchange` | string | `""` | TradingView 交易所。空字符串 =（自动）依次探测预设列表。如 `OANDA`、`SSE`、`HKEX` 等 |
| `general.last_symbol` | string | `"XAUUSDm"` | 默认品种。MT5 需含后缀（如 `m`），TradingView 用标准名（如 `XAUUSD`） |
| `general.last_timeframe` | string | `"15m"` | 默认周期，如 `1m`、`5m`、`15m`、`1h`、`4h`、`1d` |
| `general.analysis_bar_count` | int | `100` | 提交分析时使用的 K 线数量（20–5000） |
| `general.higher_timeframe_enabled` | bool | `true` | 是否加载一档更高周期的价格结构背景；不改变当前周期 Gate、方向或 Router |
| `general.higher_timeframe_override` | string | `""` | 高周期覆盖值；留空使用通用映射 |
| `general.higher_timeframe_bar_count` | int | `60` | 高周期取样根数（20–300） |
| `general.refresh_interval_ms` | int | `1000` | 图表自动刷新间隔（毫秒） |
| `general.context_warning_threshold_pct` | float | `80.0` | 上下文占用警告阈值（百分比） |
| `general.decision_stance` | string | `"balanced"` | 阶段二交易倾向：`conservative` / `balanced` / `aggressive` / `extreme_aggressive` |
| `general.incremental_max_new_bars` | int | `10` | 增量分析触发阈值：新增已收盘 K 线 ≤ 此值时自动走增量模式（0–500） |
| `general.auto_resume_chart_after_analysis` | bool | `false` | 分析结束后是否自动恢复「图表实时更新」 |
| `general.keep_analysis` | bool | `false` | 持续跟踪分析：新 K 线收盘时自动触发新一轮分析 |
| `general.cancel_keep_analysis_on_retry` | bool | `false` | 校验失败触发重试后自动关闭 `keep_analysis` |
| `general.decision_confidence_threshold` | int | `40` | 程序执行阈值；模型给出订单但 `trade_confidence` 低于此值时，正式结果改为不下单并写入审计 trace；`0` 表示关闭 |
| `general.counterfactual_horizon_bars` | int | `20` | 自动 OHLC 反事实复盘的目标观察窗（1–500 根）；每条记录实际取 `min(配置值, bar_count-1)`，仅评估计划，不代表真实成交 |
| `general.enable_next_bar_prediction` | bool | `false` | 是否请求下根 K 线预测；关闭可节省 token，且不影响 PA/VPA 决策字段 |
| `general.alert_on_order_opportunity` | bool | `true` | 阶段二给出交易方案时播放警报音、弹窗提示，并自动切换到「决策」页 |
| `general.decision_flow_auto_play` | bool | `true` | 决策树可视化自动播放 |
| `general.decision_flow_play_seconds` | int | `50` | 决策树可视化自动播放时长（秒） |
| `general.decision_flow_default_zoom_pct` | int | `600` | 决策树可视化默认缩放百分比（≥10） |
| `general.stream_pane_font_pt` | int | `11` | 「实时」页等宽字体字号（pt，8–28） |
| `general.chart_seq_label_font_pt` | int | `11` | K 线图上序号标签的字号（pt，6–24） |

### prompt — Prompt 组装调优

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `prompt.stage2_load_full_strategy_library` | bool | `false` | 阶段二是否加载全部 22 个策略文件（通常仅路由匹配的策略文件） |
| `prompt.experience_max_entries` | int | `0` | 经验库最大加载条目数（0–10） |
| `prompt.experience_max_chars_per_entry` | int | `400` | 每条经验最大字符数（100–4000） |
| `prompt.stage1_inject_pattern_briefs` | bool | `true` | 阶段一是否注入模式判定表和速查 brief（减少 missed tags） |
| `prompt.vpa_mode` | string/null | `null` | `off` / `shadow` / `soft`；生产只执行 `off` 或 `shadow`，正式结果均为 PA-only；普通运行请求 `soft` 会记录 requested=`soft`、effective=`shadow`，真正 Joint 只在隔离 A/A/B/B 的 B 臂授权 |
| `prompt.vpa_enabled` | bool | `false` | 旧配置兼容字段；仅在 `vpa_mode` 未设置时，`true` 迁移为 `shadow` |

真正的 VPA 增量实验不由运行配置模拟。它使用已保存的冻结 Snapshot 离线执行独立 A1/A2/B1/B2，并将分配、失败和结果写入实验 manifest。单次入口为 `tools/run_pa_vpa_aabb.py`；持续抽样入口为 `tools/run_pa_vpa_aabb_monitor.py --once|--forever`。持续模式只消费生产 Shadow record，按 cohort/market snapshot 原子去重，默认每轮最多启动一个四臂实验。

### validation — 校验与重试

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `validation.retry_enabled` | bool | `true` | 校验失败时是否自动重试 |
| `validation.retry_max` | int | `3` | 格式错误（category a）最大重试次数（0–5） |
| `validation.retry_max_semantic` | int | `1` | 语义错误（category c）最大重试次数（0–3） |
| `validation.retry_stage2` | bool | `true` | 阶段二校验失败时是否重试 |

### monitoring — Linux Headless 多资产监控

| 字段 | 说明 |
|------|------|
| `monitoring.enabled` | Headless 总开关；VPS 运行前必须设为 `true` |
| `monitoring.assets` | 资产列表；每项包含 `source`、`exchange`、`symbol`、`timeframe` 与 `enabled` |
| `monitoring.max_concurrency` | 同时进行的 AI 分析上限；每个任务使用独立 client/orchestrator |
| `monitoring.max_data_concurrency` | 同时拉取行情的上限；同一资产的数据源实例不共享 |
| `monitoring.poll_interval_seconds` | 新收盘 K 线轮询间隔 |
| `monitoring.run_on_startup` | 启动时是否立即分析当前最新快照；关闭时从下一根新收盘 K 线开始 |
| `monitoring.max_analysis_retries` | 同一冻结快照在 Provider/持久化失败后的额外重试次数 |
| `monitoring.retry_backoff_seconds` | 行情和分析失败的指数退避基数 |

Headless API Key 优先级：`PA_AGENT_API_KEY` → `PA_AGENT_API_KEY_FILE` → systemd `pa_agent_api_key` credential → 本地密文配置。前三种只进入进程内存，不会写回 `settings.json`。

## 安全提醒

- `secret.key` 与 `settings.json` 必须一起保密；本地加密防止明文误泄露，但不能抵御已取得运行账户权限的攻击者。VPS 优先使用 systemd credentials。

- **不要**将 `config/settings.json`、`config/exception_state.json`、`config/tv_symbol_aliases.json` 提交到 Git。
- 若曾误提交 API Key，请立即在服务商处**作废并轮换**密钥。
- 建议在仓库根目录执行：`powershell -ExecutionPolicy Bypass -File tools\setup_git_secrets.ps1`
