# 参与贡献

感谢你对 PA Agent 的关注。本项目欢迎 Issue 与 Pull Request。

## 开发环境

1. Windows 10/11、macOS 或 Linux，Python 3.11+
2. 仅在需要 MT5 真实 K 线联调时，于 Windows 安装并登录 MetaTrader 5
3. 克隆仓库后：

   ```cmd
   python -m venv .venv
   .venv\Scripts\activate
   python -m pip install uv==0.11.19
   uv sync --frozen --extra desktop --extra dev
   copy config\settings.example.json config\settings.json
   ```

4. 在 GUI **设置** 中配置 API Key，或仅跑不依赖网络的测试。

## 提交代码前

```cmd
uv run --frozen python -m pytest -m "not live"
uv run --frozen python -m ruff check pa_agent tools tests --select E9,F63,F7,F82 --no-cache
```

（若已安装 `black`，可按团队习惯格式化。）

## 请勿提交

- `config/settings.json`、`config/exception_state.json`
- `logs/`、`records/pending/`、`experience/` 下的运行数据
- 任何 API Key、`.env`、私钥文件

启用本地 pre-commit 钩子：

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup_git_secrets.ps1
```

## Pull Request 建议

- 一个 PR 聚焦一类改动（功能 / 修复 / 文档）
- 说明动机与测试方式
- 若改 JSON schema、提示词或路由，请补充或更新 `tests/` 中相关用例

## 问题反馈

- Bug：附上日志片段（`logs/pa_agent.log`）、复现步骤、品种/周期
- 功能建议：说明使用场景与期望行为

讨论与交流也可加入 README 中的 QQ 群。
