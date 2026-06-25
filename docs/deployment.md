# 本地部署、备份与故障恢复

riji-agent 是本地隐私边界，固定监听 `127.0.0.1`。远程访问由 Hermes/飞书或受控的私有网络（如 Tailscale）处理，不要把端口暴露到公网。

## 依赖与部署

需要 Python ≥3.9（宿主机若为旧版本，用项目自带环境或容器）。

```bash
cp .env.example .env
# 编辑 .env：填入真实绝对路径、DeepSeek API Key、飞书白名单、Hermes 共享密钥
uv sync --extra dev
uv run riji-agent          # 监听 http://127.0.0.1:8765
```

无 uv 时用 Docker 运行测试与服务：

```bash
docker run --rm -v $(pwd):/app -w /app python:3.11 \
  bash -c "pip install -e '.[dev]' -q && python -m pytest"
```

`uv run riji-agent` 走生产入口 `create_production_app`，组装全部本地模块并挂载 `/healthz` 与 `/hermes/messages`。启动时会：

- 对日记索引执行一次安全的增量 `build_index`（只读，不写 vault）；
- 首次运行时载入王阳明知识库 seed（已有数据则跳过，不重复写入）；
- 用 `.env` 凭据构造 DeepSeek provider（API Key 只进 provider，不入日志/异常）。

配置无效时进程以不含路径/密钥的安全错误退出。Hermes 侧的飞书接入与 DeepSeek Provider 配置见 [hermes-integration.md](hermes-integration.md)。

## 数据与目录

- 日记 vault（`RIJI_JOURNAL_ROOT`）：源数据，**只读**，绝不被写入索引或运行目录。
- 运行目录（`RIJI_DATA_DIR`，默认 `~/.local/share/riji-agent`，权限 `0700`）：存放本地 SQLite。
- SQLite（默认在运行目录）：日记索引（`riji-agent.sqlite3`）、记忆/会话（`memory.sqlite3`）、草稿（`drafts.sqlite3`）、幂等事件（`events.sqlite3`）、审计（`audit.sqlite3`）、王阳明知识库（`yangming.sqlite3`）。

## 备份

需要备份（均在运行目录，**不进 Git**）：

- `*.sqlite3`：索引、记忆、草稿、事件、审计。
- `.env`：凭据（建议单独离线保管，不随仓库备份）。

日记 vault 由用户自己的 Obsidian/iCloud 等机制备份；riji-agent 不复制它。

## 故障恢复

| 情况 | 恢复 |
| --- | --- |
| 索引损坏/丢失 | 删除索引 SQLite，重新运行全量 `build_index`（只读重建，不动 vault）。 |
| 运行目录丢失 | 索引可重建；已确认记忆/审计若无备份则丢失，故应纳入备份。 |
| 草稿/事件库损坏 | 删除对应 SQLite 重建；未确认草稿丢失，重新发起即可。 |
| 配置无效启动即退出 | 进程以不含路径/密钥的安全错误退出；检查 `.env` 必填项与路径存在性。 |
| DeepSeek 超时/失败 | 返回简短失败说明，不自动写入、不重试提交（见架构 §7）。 |

## 升级

拉取新代码后重新 `uv sync`（或重建镜像）；索引为增量，源文件变更会被检测并增量更新，无需手动全量重建。
