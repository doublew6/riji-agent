# riji-agent

`riji-agent` 是日记 Agent 的本地数据与隐私边界：它将来为 Hermes 提供受限工具，但绝不让 Hermes、DeepSeek 或飞书直接读取整个 Obsidian vault。

当前完成了 MVP-01 的服务骨架：配置校验、本地运行目录、仅回环监听的 FastAPI 服务，以及不泄漏配置的健康检查。

## 本地启动

需要 Python 3.9+ 和 [uv](https://docs.astral.sh/uv/)。在项目目录执行：

```bash
cp .env.example .env
# 编辑 .env，填入真实绝对路径、DeepSeek API Key、飞书用户 ID 与 Hermes 共享密钥
uv sync --extra dev
uv run riji-agent index    # 首次部署：先预热本地索引
uv run riji-agent          # 启动服务
```

首次部署建议先 `uv run riji-agent index` 把日记索引建好，再启动服务接入 Hermes，避免冷启动扫描全部 Markdown 时卡顿。服务运行时后台调度器会定期增量索引（默认每 10 分钟，可经 `RIJI_INDEX_*` 配置）。索引 CLI：`index`（增量）、`index --rebuild`（重建）、`index --status`（只读查看元数据）。详见 [docs/deployment.md](docs/deployment.md)。

服务固定监听 `http://127.0.0.1:8765`；浏览器打开 `http://127.0.0.1:8765/healthz`，应得到：

```json
{"service":"riji-agent","status":"ok"}
```

若缺少必需配置或日记路径不可访问，进程会以不含路径、密钥的可读错误退出。`RIJI_DATA_DIR` 默认位于 `~/.local/share/riji-agent`；它用于未来的 SQLite、草稿与审计数据，不会把日记复制进仓库。

## 配置与安全

- `.env`、SQLite、审计日志、`data/` 和可能误复制的 `riji/` 均被 Git 忽略。
- `RIJI_JOURNAL_ROOT` 必须指向一个已存在的 vault 内 `riji` 目录。
- `RIJI_DATA_DIR` 和可选的 `RIJI_DATABASE_PATH` 必须在日记目录之外，且数据库只能位于数据目录中。
- `RIJI_ALLOWED_FEISHU_USER_IDS` 是逗号分隔的飞书 open ID 白名单；群聊会在后续鉴权层无条件拒绝。
- 运行入口把 Uvicorn 固定到 `127.0.0.1`。如需手机访问，应由后续的 Hermes/飞书或受控的 Tailscale 方案处理，而非暴露这个端口到公网。

## 开发验证

```bash
uv run pytest
```

### 冒烟测试（smoke）

`tests/test_smoke_mvp.py` 是一条面向部署路径的端到端冒烟测试，通过真实的 HTTP app
（`/healthz` 与 `/hermes/messages`）跑通「本地索引 + 检索工具 + DeepSeek 工具循环（stub）
+ 导师路由 + 幂等 + 隐私最小化」主链路。它全程使用临时 fixture 与 stub 模型，**不读取真实
`.env`、真实日记库或真实 API Key**。

```bash
uv run pytest -m smoke        # 只跑冒烟测试，快速判断主链路是否通
uv run pytest -m "not smoke"  # 跑其余单元测试
uv run pytest                 # 全部
```

适用场景：升级依赖、改动网关/检索/工具循环、或部署前，用 `-m smoke` 做一次快速健康检查。
