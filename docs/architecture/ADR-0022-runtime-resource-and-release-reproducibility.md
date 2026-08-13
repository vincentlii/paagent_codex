# ADR-0022：运行资源一致性与可复现发布

## 状态

Accepted，2026-08-13。

## 背景

Headless 支持通过 `--state-dir`、`--settings` 与 `--prompt-dir` 指定运行目录，但旧实现中
PromptAssembler 使用显式 Prompt 路径，Provider Contract、decision-tree materializer 和
validator 仍可能读取 import-time 全局路径。TradingView 用户别名也使用单一全局缓存。
这会造成发送给模型的规则、接受模型输出的 Schema 以及审计指纹不属于同一快照。

源码目录运行时，`prompt_engineering/` 可直接读取；wheel 安装后它此前不是正式 package
data。依赖也仅有开放下界，VPS 与开发机可能解析到不同版本。

## 决策

1. 一次分析只使用一个显式 `prompt_dir`。同一目录驱动 Prompt 文本、Provider Draft Schema、
   contract digest、trace materialization、canonical validation、Joint 二次校验和 record runtime
   fingerprint。
2. 默认目录保持向后兼容；CLI 显式路径通过构造函数注入，不修改进程全局环境变量。
3. TradingView alias cache 以解析后的 `config_dir/tv_symbol_aliases.json` 为 key；Headless 显式传入
   `PA_AGENT_HOME/config`，不同运行根目录不得共享别名缓存。
4. `prompt_engineering` 作为只读资源 package 随 wheel 发布，`.txt` 与 `.md` 均列入 package data。
5. 依赖按 `core`、`desktop`、`gui`、`tradingview`、`native-exchanges`、`vps` 等 profile 分组；
   tvdatafeed 固定到已验证 commit，完整解析结果写入 `uv.lock`。
6. CI 在 Windows 与 Linux 使用 frozen lock 运行所有 `not live` 测试、关键 Python 静态错误检查、
   wheel 安装资源 smoke test 与 Linux shell syntax test。CI 永不调用真实行情或 LLM。

## 三角色审核

- 交易员：规则、Schema 与审计身份一致，避免“模型按 A 规则回答、程序按 B 规则拒绝”；不增加
  PA/VPA Gate，不改变市场判断、订单数学或触发频率。
- 量化架构：实验 cohort 能绑定真实 Prompt/contract snapshot；不同 state root 的别名和运行记录
  不会交叉污染，可重复安装支持可比研究。
- 软件工程：路径通过既有构造 seam 注入；默认接口兼容；依赖最小化且发布资源由 wheel 验证。

## 后果

- 自定义 Prompt pack 必须包含合法 `二元决策.txt`；缺失会在 `--check` 阶段明确失败。
- 修改依赖后必须更新并提交 `uv.lock`。
- 风格债务不以一次大规模格式化处理；CI 当前只阻断语法错误、未定义名等发布级问题。
- 本 ADR 不授权 Joint 进入生产，也不改变 PA 或 Wyckoff VPA 的专业解释权。
