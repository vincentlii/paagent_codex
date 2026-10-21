# 旧 Qt 桌面版退役验收

用户于2026-10-21明确授权“备份后删除，成套处理”。范围为本地代码、安装配置、测试、
开发/现行使用说明及可再生成的旧构建缓存。初次本地清理不自动commit/发布；后续用户
明确授权的提交、推送与VPS发布见末节，未额外操作交易账户或发送测试订单。
实施基准为 `42a167e3c6fc2918045d411f7ba12e8e9b2f8556`。

## 备份与恢复

本地备份使用现有忽略目录 `docs/changelog/backups/20261021-desktop-retirement/`：

- `source-before.zip`：删除前740个文件原文，包含跟踪源码/测试/文档和原有ICT未跟踪报告。
  `manifest.json` 记录每个成员的SHA256、原分支/提交及工作区状态；逐成员校验与ZIP CRC通过。
  ZIP SHA256为 `2309870438aea72b99540a605abee19819e15372561f9d80016a9fb33e2cb058`。
- `deleted-files.json`：本轮81个删除文件的精确清单；删除前当前文件与备份哈希逐一匹配。
- `generated-build-before.zip` 与其manifest：325个原build/egg-info派生成员，逐成员核验后清理，
  防止旧桌面文件及旧extra元数据被后续打包重新带入。该缓存可由原提交重新构建。
- 回归、构建、环境安装日志、新wheel和`wheel-smoke.json`保留；不含API key、生产数据库
  或交易账户凭据。原`.venv`与项目私有运行配置未修改，也未清理用户级缓存。

恢复先校验archive及成员哈希，在独立目录解包并对比；仅恢复所需具名桌面文件以及配套
pyproject/锁/CI/测试/入口。不要整包覆盖当前工作区，以免覆盖既有ICT改动或后续工作。
若将来清理版本已产生生产发布，回退仍须遵守对应版本的冻结任务、Runtime和Demo兼容流程，
不能把源码解包视作生产数据恢复或允许旧Worker接管活动单。

## 实际清理

删除旧`pa_agent/gui/`全部42个文件、桌面`main.py`/`app_context.py`/根`run.py`、
Qt专属refresh loop/event bus/session token ledger/桌面回放包，以及仅桌面消费的free chat。
同步移除两个包的桌面导出，旧两阶段smoke工具不再创建Qt EventBus，保留既有可选默认接口。
删除3份桌面部署/使用指南；历史图表快照说明标记为退役版本，保留其原理解释。

删除桌面专属图表、弹窗、线程、提示、自由会话、回放选择和桌面E2E测试。
混合测试只移除Qt部分：

- `integration/test_next_bar_prediction.py` 保留7项编排、记录、取消与失败链路测试。
- `property/test_next_bar_prediction_perf.py` 保留Prompt Token预算和装配性能两项测试。
- 原桌面replayer里的3项共享response解析测试并入`test_response_extract.py`。
- 共享`records/replay`测试迁到`test_records_replay.py`，使用固定20棒，不再无本地记录就直接return。
- 数据源测试及核心usage、normalizer、离线研究、历史回放测试继续保留。

移除`gui`/`desktop` extras、`pytest-qt`与`pa-agent`桌面console入口。由uv 0.11.19离线重锁，
仅移除PyQt6、PyQt6-Qt6、PyQt6-sip、pyqtgraph、pytest-qt五包；剩余package版本/source
记录不变，VPS的含hash导出依赖内容与原版本一致。其他独立可选数据源/Backend安装项保留。
Windows/Linux CI与贡献说明改为`vps + tradingview + cursor + dev`，不安装Qt。
最小VPS profile不增加Cursor或测试依赖。Makefile启动入口改为已配置的Headless。

## 保留边界

网页Dashboard/交易面板、Headless、Codex Worker、PA/VPA、Router、Provider Draft/Schema、
materializer、Validator/Risk/Trader Equation、订单价格、Prompt/Skills/知识、records/audit、
共享取消及回放、核心调用用量和OKX Demo全链路均保留。旧桌面`pa_agent/demo`与
`execution/operations`下OKX Demo不同，后者未删除。

267个保留的生产源码/Prompt资源逐文件与删除前manifest字节相同；仅两个包`__init__`
移除桌面导出。既有ICT四文件与备份逐字节一致。配置里为旧历史兼容保留的字段不按“GUI”
字符串盲删，防止改变冻结身份、配置读取和历史记录；历史ADR/验收不批量改写。

## 验证与已知边界

退役打包契约先红后绿：旧metadata含gui/desktop时新断言失败，移除后继续验证现有Headless
入口和知识资源。新建独立开发环境，不包含Qt发行包；保留全部非live测试直接收集，
无桌面ignore列表。PA/VPA off/shadow、Joint、A/A/B/B、HTF、订单数学、records/audit及
网页控制面仍由原回归覆盖，不添加真实模型或交易所调用。

首轮`1972 passed / 4 failed / 7 deselected`的四项失败为新精简测试环境缺依赖：两项Cursor
SDK契约测试缺原有可选SDK，两项MT5时钟unit test误依赖真实平台SDK。保留Cursor可选
测试配置；MT5在外部SDK边界注入mock并仍断言真实adapter的毫秒优先/秒fallback和品种参数，
不要求Linux安装Windows SDK，不跳过原断言，也未修改生产adapter。修正后聚焦28项通过。

首轮锁更新受本机缓存写权限限制；改用正常缓存权限离线重锁。独立wheel环境首次从导出
requirements要求缓存原wheel而失败，随后按项目原有`uv sync --frozen --offline`成功复用
已验证缓存；无依赖下载/升级。原失败日志保留。

新wheel由干净源码副本构建，269个包资源与当前源码及实际安装逐字节相同，无桌面模块，
不复用旧build缓存。SHA256：
`0a570a5842c34b963f27d10755a17887bbac5b1d89e2f33088b23c8eec4cb6ca`。
实际最小VPS安装环境没有Qt、pytest或cursor-sdk，保留的5类CLI模块可导入，Prompt和Runtime
资源可加载，桌面console入口不存在；当前Runtime asset digest未因退役改变。

独立审阅者从PA、Wyckoff VPA和量化软件三视角审查实际diff与备份，未发现确定误删或权限
变化；保留源码/测试/工具的绝对`pa_agent.*`导入均可解析。

最终完整回归为 **1976 passed / 7 live deselected / 2 warnings，108.47s，exit=0**。
命令：独立无Qt环境中运行 `python -B -m pytest -m 'not live' -o addopts='' -q
-p no:cacheprovider --basetemp=.pytest-desktop-full-final`，没有GUI排除项或新增skip。
tokenizer使用已有精确词表缓存；本机HTTP测试获本机socket权限，未连模型或交易所。
两条warning为既有tvDatafeed invalid escape sequence，与本轮无关。
全仓关键Ruff `E9,F63,F7,F82` 与 `git diff --check`通过。测试、打包临时目录和
本轮临时环境均已清理，最终备份、构建物及失败/成功回执保留。

本轮不处理前次发布已记录的OKX成交覆盖缺口，不重置游标、不删除交易或审计数据，不改变
线上状态。VPS本来就不安装Qt，收益主要是维护面、CI依赖和源码体积，不能宣称模型Token
或当前VPS运行内存显著下降。初次本地验收与后续实际发布分别记录，不能互相替代。

官方工具依据：[uv依赖管理](https://docs.astral.sh/uv/concepts/projects/dependencies/)及
[锁定/同步](https://docs.astral.sh/uv/concepts/projects/sync/)。复用已有extras与锁流程，
没有自行手工删锁包或升级版本。

## 提交与发布前复核

用户随后明确授权 commit 并发布VPS。本轮提交前新鲜复核：保留入口、元数据、records回放、
response解析、MT5外部SDK边界及Linux部署资源共33项通过（5.66s），关键静态及diff检查通过。
740成员备份、ICT四文件、剩余锁版本及269个候选wheel成员再次核验通过。
另一轮独立只读发布审查未发现阻断；安装必须使用干净构建副本，按旧RECORD正常卸载后重装，
检查旧桌面Python文件及`bin/pa-agent`命令均不存在。正式发布结果在末节追加。

## 正式提交与 VPS 发布

固定退役提交 `123adb1ee88f359e227d2d9fca16599ed8603579` 已推送既有私有仓库
`vincentlii/paagent_codex` 的main，未推送公共origin，既有ICT四文件未纳入。
VPS源码从42a167e仅快进，工作区保持干净。初次SSH握手超时/关闭均发生在会话建立前，
未修改远端；独立探测收到正常OpenSSH banner后连接恢复，再开始暂停与发布。

发布前线上用户当前配置已为fixed_rr/1.5、控制代次58；本次原样保存、暂停至59，核验
Host queued/running/unfinished=0、无恢复阻塞、Codex inbox为空，执行账户无活动计划/持仓，
再协调停止四个加载包的服务。不是将线上模式改回上一份报告中的PA默认。

Host备份 `/var/backups/pa-agent/pa-agent-20261021T025735Z` 与Demo备份
`/var/backups/pa-agent-demo/demo-1792551489447` 全部manifest通过，Demo共175成员。
四库quick_check通过，安装前后逐库逻辑dump SHA256完全相同；无迁移、回填或历史删除。
原控制/非敏感配置、旧源码、候选源码、发布bundle、wheel、构建/安装日志与回执位于
`/var/backups/pa-agent/releases/desktop-retirement-20261021`，凭据不复制或导出。

目标Linux从干净Git archive副本离线构建，防止旧build缓存复活桌面；正常pip按旧RECORD
卸载并以`--no-index --no-deps --force-reinstall`安装，新wheel SHA256为
`0c83d9c1833e24ac82a221c54d4f7a8476765e519405ebce52f07c3b98511c05`。
269包成员逐字节匹配固定Git blob、源码与安装结果；51个删除源码路径在安装目录均不存在，
桌面`bin/pa-agent`命令不存在，退役模块`find_spec`返回None。48个已安装distribution
名称/版本保持一致，pip check通过；Headless/Dashboard/Demo/Codex/研究入口无Qt导入通过。
Runtime asset digest保持`0ca8f2b676a450a6a724ce1d1da3457f6f6db9f5f13c5f4e19229bddbc375364`，
source/install与Linux权限审计通过，没有跨资产版本续接或重新调用模型。

发布后UTC2026-10-21 03:01:24（北京时间11:01:24）回执：四服务active/running、
Result=success、NRestarts=0，Host恢复running60，无控制错误或恢复阻塞。
`/healthz`、认证`/api/status`、`/api/trading`、`/trading`、首页均200，未认证status为401。
固定RR四档可用；原fixed_rr/1.5、BTC/ETH/XAU、500 USDT/3h、Fast、Profile指纹与其他
模型配置均原样保留。发布不证明新的真实模型分析或交易效果已通过。

**既有未解决问题仍保留：** Demo为`blocked/reconciliation_gap`、fills覆盖false，其他覆盖
为真；活动计划、持仓、外部单为0。执行安全拒绝没有放宽，本轮清理与发布未修复该缺口，
不能把四服务正常说成模拟盘可正常新开单。

VPS旧源码build/egg-info及已退役模块缓存先归档校验325成员，再仅清理具名派生目录；
保留归档和manifest，未遍历删除运行数据。候选干净构建副本与上传暂存清理，源码tar、
wheel、日志、备份与账本保留。本地发布聚焦测试目录已清理，脱敏回执另存本地忽略备份目录。
