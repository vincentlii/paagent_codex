# 旧 Qt 桌面版退役

用户已授权备份后成套删除。仅本地实施、验证及文档同步，不自动 commit、部署或修改线上配置。

1. 在现有忽略备份目录保存当前跟踪文件原文、原有 ICT 文件、基准提交和 SHA256 清单，逐成员验证归档。
2. 删除 GUI、桌面入口/装配、专属 Qt refresh/event/session/replay 及仅桌面使用的自由会话；保留 Headless、网页 Dashboard、共享取消/回放、模型用量及 OKX Demo 全部执行代码。
3. 删除桌面专属测试；混合测试只删 GUI 部分，共享 response/record/prediction 测试继续执行。清理包导出和旧工具的 Qt 依赖。
4. 移除 gui/desktop extras、pytest-qt 和桌面脚本入口，同步 CI、开发命令与当前文档；由 uv 离线重锁，现存依赖不升级，VPS 导出前后完全一致。
5. 聚焦测试、全量非 live 测试、关键静态检查、无 Qt 安装环境及 wheel 内容核验；核对生产分析、Runtime、知识、Schema、交易执行代码/资源字节未改。

三视角预审：PA/VPA 权限及订单不变；程序移除展示和桌面专属生命周期，不改冻结身份、账本或数据。原有 ICT 四文件不覆盖。数据/凭据/历史结果不删除；历史 ADR/验收保留为证据，不批量重写。

完成标准：当前源码不再有桌面入口或 Qt 运行依赖；所有保留的测试可直接收集运行，无需 GUI ignore 清单；web/headless/OKX 和共享 Prompt/records 测试通过；备份可恢复。验收结果记录于既有质量目录并由 CONTEXT 导航。
