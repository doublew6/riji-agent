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
uv run riji-agent index            # 增量索引，输出 added/updated/unchanged/deleted/skipped/duration
uv run riji-agent index --rebuild  # 清空重建（索引损坏或 schema 变更后恢复）
uv run riji-agent index --status   # 只读查看 note 数、最近索引时间、DB 路径、语义开关；不输出正文/Key
```

`index` 运行时把每个文件的进度打到 **stderr**（`[i/total] action source_id`），最终统计单行打到 stdout，便于长时间运行时观测。**冷读保护**：单个文件读取超过 `RIJI_INDEX_FILE_TIMEOUT_SECONDS`（默认 5s，常用于未水合的 iCloud 文件）会被**跳过并计入 `skipped`**，索引继续；被跳过的文件**不会**被当作「已删除」清掉其既有索引项。被跳过文件以脱敏的 wikilink id 汇总（不打印绝对路径或正文）。

### 索引调度

服务运行时由后台调度器定期增量索引（默认每 10 分钟），把新增/修改/删除的日记同步到本地 SQLite。启动只在后台预热、最多等待 `RIJI_INDEX_STARTUP_TIMEOUT_SECONDS`（默认 10s），超时不阻塞、索引在后台继续。可配置：

- `RIJI_INDEX_SCHEDULE_ENABLED`（默认 `true`）：关闭后只在启动预热 + 手动 `index`。
- `RIJI_INDEX_INTERVAL_SECONDS`（默认 `600`）：增量间隔。
- 调度有防重入：上一轮未结束不会并发再起；索引失败只记安全日志（异常类名，不含正文/路径/Key），不影响服务。

确认保存草稿后目标日记会被即时 `update_note`，无需等下一轮调度即可检索。

启动预热在调度器**守护线程**里跑、最多等待 `RIJI_INDEX_STARTUP_TIMEOUT_SECONDS` 即放行；即便某文件冷读卡住，`Ctrl+C` / 关闭也能干净退出，不会有非守护线程把进程吊住。

## 后台常驻服务（macOS / Linux / Windows）

如果 riji-agent 要长期配合 Hermes + Feishu 使用，建议把它安装成系统级用户服务，
而不是依赖某个终端窗口、`screen` 或 `nohup`。`service` 命令的子命令在三个平台上
完全一致，`--target` 默认为 `auto`，会按当前系统自动选择后端：

| 平台 | 后端（`--target`） | 服务定义 |
| --- | --- | --- |
| macOS | `launchd` | `~/Library/LaunchAgents/ai.riji-agent.plist` |
| Linux | `systemd` | `~/.config/systemd/user/riji-agent.service` |
| Windows | `windows`（Task Scheduler） | 计划任务 `\ai.riji-agent` |

```bash
uv run riji-agent service install   # --target auto：按平台自动选择后端
uv run riji-agent service start
uv run riji-agent service status
uv run riji-agent service logs
uv run riji-agent service restart
uv run riji-agent service stop
uv run riji-agent service uninstall
```

需要显式指定时用 `--target launchd|systemd|windows`；在非对应平台执行会以清晰的
「unsupported」提示拒绝，不做任何改动。

三个平台的共同约定：

- 启动命令优先使用绝对路径 `uv run riji-agent serve`；无 uv 时退回当前
  `riji-agent` 可执行文件 + `serve`。
- 日志统一为 `service.log` 与 `service.error.log`（POSIX 默认在
  `~/.riji-agent/logs/`，Windows 在 `%LOCALAPPDATA%\riji-agent\logs\`）。
- 监听地址仍为 `127.0.0.1`，不打开公网端口。
- 服务定义只保存可执行文件、工作目录和日志路径；**不写入** `.env` 内容、API
  Key、Feishu 凭据、日记正文、SQLite 数据库或 vault 内容。服务启动时仍按普通
  `riji-agent` 入口读取本地配置。
- `uninstall` 只移除生成的服务定义（plist / unit / 计划任务），不删除用户数据、
  `.env`、日志、日记或数据库。

### macOS（launchd）

通过 `launchctl bootstrap/kickstart` 管理，`RunAtLoad=true` + `KeepAlive=true`。
登录后随会话拉起。

### Linux（systemd --user）

生成 `~/.config/systemd/user/riji-agent.service`（`Restart=on-failure`、
`WantedBy=default.target`），`install` 会自动 `daemon-reload` + `enable`，登录后
随用户会话启动。若希望用户未登录时也保持运行，可自行 `loginctl enable-linger
<user>`（本项目不代为修改 linger 设置）。日志通过 `StandardOutput=append:` 写入
上述日志文件，需要 systemd ≥ 240。

### Windows（Task Scheduler）

通过 `schtasks` 注册一个登录触发（logon trigger）的计划任务，以**当前用户**身份
运行，**无需管理员 / UAC**；失败时按 `RestartOnFailure` 重启，等价于 launchd 的
`KeepAlive`。任务动作通过 `cmd /c "... >> service.log 2>> service.error.log"` 把输出
重定向到统一日志文件。

### 睡眠 / 注销行为

机器睡眠或用户注销期间，Hermes 的 Feishu websocket 和 riji-agent 本地 HTTP 服务
都无法处理消息；唤醒 / 重新登录后，各平台的服务管理器会按服务定义恢复
riji-agent。Hermes gateway 仍由 Hermes 自己的 `hermes gateway install/start`
管理，本项目的 `service` 命令只管理 riji-agent。

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

### Hermes bridge installer

Hermes 侧的 Feishu -> riji-agent 转发 hook 由 riji-agent installer 管理，不需要手工编辑 Hermes 源码：

```bash
uv run riji-agent hermes-bridge install
uv run riji-agent hermes-bridge status
```

默认修改 `~/.hermes/hermes-agent/gateway/run.py`，并在修改前创建 `run.py.riji-agent.bak` 备份。Hermes 升级或重装后，如果飞书消息又绕开 riji-agent，重新执行 `install` 并重启 `hermes gateway`。

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
