# 本地部署、备份与故障恢复

> #40 日记长期记忆的开发实现见 [操作说明](journal-memory.md) 和 [验收记录](journal-memory-acceptance.md)。生产目标仍只有 Air。启用前需按 AGENTS.md 的 Air 备份、同步、测试和服务顺序更新 Mem0 API 镜像；开发机不得启动生产 Compose 或迁移真实日记。不要使用下面的通用安装示例覆盖 Air 已有 runtime、凭据或服务配置。

riji-agent 是本地隐私边界，固定监听 `127.0.0.1`。远程访问由 Hermes/飞书或受控网络入口处理。既有混合服务和管理端口不得暴露到公网；2026-09-17 已授权的个人手机公网入口只可指向下述独立认证代理 `8767`。

## 依赖与部署

需要 Python ≥3.11（宿主机若为旧版本，用项目自带环境或容器）。

```bash
cp .env.example .env
# 编辑 .env：填入真实绝对路径、DeepSeek API Key、飞书白名单、Hermes 共享密钥
uv sync --extra dev
uv run riji-agent index    # 首次部署：先预热索引（见下）
uv run riji-agent          # 监听 http://127.0.0.1:8765
```

真实 Agent Trace 默认关闭。需要在本机 Opik 中查看根 Input/Output、LLM
span 和 tool span 时，按[私有运行时可观测性](runtime-observability.md)安装
兼容的 EvalMesh，并配置仓库外的 mode-`0600` policy。

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
- 启用 Mem0 后，`memory-operations.sqlite3` 保存捕获队列与变更日志，`memory/MEMORY.md` 是 mode-`0600` 的只读快照；长期记忆正文的权威副本位于本机 Mem0 PostgreSQL。

## 备份

需要备份（均在运行目录，**不进 Git**）：

- `*.sqlite3`：索引、记忆、草稿、事件、审计。
- `.env`：凭据（建议单独离线保管，不随仓库备份）。
- Mem0 PostgreSQL：按 [长期记忆文档](long-term-memory.md) 和 `infra/mem0/README.md` 使用 `pg_dumpall` 备份；仅备份 SQLite 不足以恢复长期记忆。

日记 vault 由用户自己的 Obsidian/iCloud 等机制备份；riji-agent 不复制它。

## 故障恢复

| 情况 | 恢复 |
| --- | --- |
| 索引损坏/丢失 | 删除索引 SQLite，重新运行全量 `build_index`（只读重建，不动 vault）。 |
| 运行目录丢失 | 索引可重建；已确认记忆/审计若无备份则丢失，故应纳入备份。 |
| 草稿/事件库损坏 | 删除对应 SQLite 重建；未确认草稿丢失，重新发起即可。 |
| 配置无效启动即退出 | 进程以不含路径/密钥的安全错误退出；检查 `.env` 必填项与路径存在性。 |
| DeepSeek 超时/失败 | 返回简短失败说明，不自动写入、不重试提交（见架构 §7）。 |
| Mem0 检索失败 | 本次不注入长期记忆，聊天和日记工具继续工作；自动捕获进入后台重试。 |
| `MEMORY.md` 刷新失败 | Mem0 变更不回滚，后台 worker 重试生成快照。 |

## 升级

拉取新代码后重新 `uv sync`（或重建镜像）；索引为增量，源文件变更会被检测并增量更新，无需手动全量重建。

## Personal mobile ingress (opt-in)

The versioned [mobile contract](architecture/ios-mobile-api.md) reuses the existing
Air runtime and worker. Set `RIJI_MOBILE_ENABLED=true` only after the backend
regressions and verified-owner checks pass. `RIJI_DEPLOYMENT_REVISION` is the
validated source revision; `/.well-known/fleet` freezes it and the installed
package version at startup, and is never forwarded by the mobile proxy.

The existing mixed listener remains `127.0.0.1:8765`. Register and inspect the
project-specific `127.0.0.1:8766` listener before starting `riji-agent mobile proxy`;
port occupation is a hard failure, never permission to kill an unrelated process.
The proxy forwards only the documented mobile method/path set, ignores environment
proxies, rejects escaped paths and redirects, and cannot forward admin/Hermes/Fleet
routes. Tailscale Serve may terminate private HTTPS to **8766 only**, after checking
existing Serve configuration and tailnet access; do not use Funnel for this legacy
8766 proxy or expose 8765. The separately authenticated public proxy below is the
approved exception for the managed personal installation.
Preserve all existing Serve listeners and mappings. Select a separate unused private
HTTPS port (for example 8443) when the default endpoint already serves another
project, and include that port in the pairing URL. Valid explicit HTTPS ports are
1–65535; the URL must remain an origin without credentials, query or fragment.
No Serve configuration is installed automatically by these commands.

Use the existing runtime working directory/environment. The LaunchAgent template
`ops/launchd/ai.riji-agent.mobile-proxy.plist` requires verified absolute runtime
and log paths before loading; never load placeholders or overwrite a live plist.
It starts a proxy, not a second gateway, model scheduler or memory worker.

Enrollment is operator-only: verify the existing principal against the current
mentor configuration, then run `riji-agent mobile pair --principal <verified-id>
--url <verified-private-https-origin> --output <new-private-file>`. Output is a
mode-0600 JSON package containing `base_url`, a one-time code and its ten-minute
validity, never printed credentials. Transfer it privately to the owner's device;
no Review/model credential enters iOS. A lost pairing response needs a fresh code;
inspect/revoke the orphaned device with `mobile devices` / `mobile revoke --device`.
The phone persists its distinct token in Keychain. `mobile.sqlite3` holds hashes,
revocations, channel bindings and conservative operation receipts outside iCloud.
Back it up alongside the existing private runtime; never copy it into the repo.

Revocation affects native queued generation and delivery at policy boundaries,
including after restart; disabling mobile also fails existing native work closed.
Already sent model requests cannot be recalled. Existing IM bindings and delivery
are unchanged. Native history is device/persona scoped and does not silently import
Feishu history. Device re-pairing creates a distinct native conversation namespace.

Deployment remains governed by the Air identity/Fleet/process checks, backup,
rsync dry-run, secret/data exclusions, runtime tests, doctor and controlled restart
above. This implementation does not itself deploy, modify consent/providers,
open historical-initialization exceptions, or prove real-phone iCloud/model access.

### Managed personal installation: dedicated authenticated public ingress

The approved topology is HTTPS **10000 → 127.0.0.1:8767 → 127.0.0.1:8765**.
`riji-agent mobile public-proxy` starts only the independent public proxy; it does
not start a gateway, memory worker or model scheduler. Preserve the running main
service, private 8766 proxy, Serve 443/8443 and all other projects. Verify Air via
the existing `air` SSH alias, Fleet heartbeat and actual process ownership; inspect
8767 and 10000 before any launch. Occupied ports fail rather than switch or kill.
Back up replaced files, perform rsync dry-run with the documented secret/data
exclusions, synchronize only verified sources and run the relevant Air tests first.

The placeholder template `ops/launchd/ai.riji-agent.mobile-public-proxy.plist`
requires verified absolute `__RUNTIME_EXECUTABLE__`, `__RUNTIME_DIRECTORY__` and
`__LOCAL_LOG_DIR__` substitutions in a separate installation copy. Its working
directory is the existing runtime so the existing `.env` is used without copying
secrets. Never load the repository placeholders or overwrite an existing live
plist. Only after local denial/acceptance checks pass may the approved HTTPS 10000
Funnel mapping point to this proxy. Do not redirect another mapping or enable public
access to 8766. Public pairing, health, admin, Hermes and Fleet must all return 404;
missing/invalid/revoked credentials must return 401 without upstream work.

Immediately before a trusted USB installation, verify the existing principal and
issue the package with `riji-agent mobile provision --principal <verified-id>
--url <verified-public-https-origin> --previous-endpoint <exact-existing-origin>
--output <new-private-file> --device-name <personal-device-name>`. Omit the previous
origin for a fresh device. The output is exclusive mode 0600 outside the vault;
the command never prints tokens. The installer transfers it to the app container's
`Library/Caches/MobileInstallation.json`, not Documents, bundle resources, iCloud,
preferences or a URL. Back up existing app data without exporting Keychain before
updating the app. Keep the app terminated throughout USB transfer; finish the
complete private package before launching/unlocking. Do not replace a package while
the app is importing it. The reader atomically claims the pathname and preserves
unexpected replacement bytes under a private, non-imported recovery directory
rather than deleting a newer USB file. Inspect and resolve any such recovery files
with the app quiescent; never copy them back automatically. A retained
.MobileInstallationRecovery directory blocks further automatic imports until the
operator preserves/inspects its contents and resolves the interrupted installation.
No package is issued
while the target phone is unavailable.

Launch/unlock the app, inspect the secret-free installation receipt and verify
service capabilities. A success string alone is insufficient: the source
MobileInstallation.json must be absent and the current issuance's device ID must
match either used_device_id or unused_provisioned_device_id in the new receipt.
The app invalidates the previous receipt before processing a new package. Missing
receipts cannot establish success, even if the working credential was updated.
Existing credentials are retained across origin migration;
revoke any `unused_provisioned_device_id` with the existing operator command. A
missing/error receipt or failed Keychain update requires inspection and a fresh
package after revoking unused issuance; do not replay a consumed package. Verify
relaunch, foreground, mentor/memory reads, offline diary independence and explicit
revocation on the phone separately. Synthetic tests are not this phone evidence.

The public command explicitly selects standard Uvicorn h11 with a 16 KiB
incomplete-header limit, an application-level 16 KiB total-header check and the
existing 32-connection cap. Application responses, including unsupported methods
and routes, carry Cache-Control: no-store. Uvicorn default headers also mark its
transport-overload 503 non-cacheable; that response can be plain text. Pre-ASGI
malformed/incomplete HTTP parsing responses can likewise be non-JSON and do not
guarantee application headers. They contain no personal response content. The
mobile client treats these as failed service requests, never successful empty
data. Standard Uvicorn retains ownership of transport parsing and overload.

Rollback stops only this deployment's exact new public service and removes only its
new 10000 mapping. Keep all previous services/mappings and the original client
archives. Reverting an installed phone origin requires a fresh package bound to its
current endpoint; never overwrite device identity or erase drafts to force rollback.
The retained destination archive may now differ from drafts or pending operations
created at the current origin. Preserve/export both archives, inspect their
operation IDs and explicitly resolve the conflicting local data before retrying
the origin transition. An unresolved difference remains a rejected migration;
rollback must not auto-overwrite either archive or replay pending operations.
Do not restore database backups over current data or restart shared Colima.
