# 本地部署、备份与故障恢复

riji-agent 是本地隐私边界，固定监听 `127.0.0.1`。远程访问由 Hermes/飞书或受控的私有网络（如 Tailscale）处理，不要把端口暴露到公网。

## 依赖与部署

需要 Python ≥3.9（宿主机若为旧版本，用项目自带环境或容器）。

```bash
cp .env.example .env
# 编辑 .env：填入真实绝对路径、DeepSeek API Key、飞书白名单、Hermes 共享密钥
uv sync --extra dev
uv run riji-agent index    # 首次部署：先预热索引（见下）
uv run riji-agent          # 监听 http://127.0.0.1:8765
```

**首次部署建议先预热索引**：真实 Obsidian/iCloud 目录冷启动需扫描全部 Markdown，iCloud 还可能逐个下载阻塞。先跑一次 `uv run riji-agent index` 把索引建好，再启动服务接入 Hermes，可避免首启动卡顿。

### 索引 CLI

```bash
uv run riji-agent index            # 增量索引，输出 added/updated/unchanged/deleted/duration
uv run riji-agent index --rebuild  # 清空重建（索引损坏或 schema 变更后恢复）
uv run riji-agent index --status   # 只读查看 note 数、最近索引时间、DB 路径、语义开关；不输出正文/Key
```

### 索引调度

服务运行时由后台调度器定期增量索引（默认每 10 分钟），把新增/修改/删除的日记同步到本地 SQLite。启动只在后台预热、最多等待 `RIJI_INDEX_STARTUP_TIMEOUT_SECONDS`（默认 10s），超时不阻塞、索引在后台继续。可配置：

- `RIJI_INDEX_SCHEDULE_ENABLED`（默认 `true`）：关闭后只在启动预热 + 手动 `index`。
- `RIJI_INDEX_INTERVAL_SECONDS`（默认 `600`）：增量间隔。
- 调度有防重入：上一轮未结束不会并发再起；索引失败只记安全日志（异常类名，不含正文/路径/Key），不影响服务。

确认保存草稿后目标日记会被即时 `update_note`，无需等下一轮调度即可检索。

无 uv 时用 Docker 运行测试与服务：

```bash
docker run --rm -v $(pwd):/app -w /app python:3.11 \
  bash -c "pip install -e '.[dev]' -q && python -m pytest"
```

`uv run riji-agent` 走生产入口 `create_production_app`，组装全部本地模块并挂载 `/healthz` 与 `/hermes/messages`。启动时会：

- 在后台预热日记索引（只读增量，不写 vault），最多等待 `RIJI_INDEX_STARTUP_TIMEOUT_SECONDS` 后即放行，之后由调度器定期刷新；
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
