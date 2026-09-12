# Codex 启动指令

开始工作前，必须阅读并遵循 [PROJECT_GUIDE.md](PROJECT_GUIDE.md)。

随后阅读 `docs/PRD.md`、`docs/architecture/mvp-architecture.md` 和当前领取的 GitHub Issue。若本文件与 `PROJECT_GUIDE.md` 冲突，以 `PROJECT_GUIDE.md` 为准。

## 生产部署主机：Air Mac

- `riji-agent` 的唯一生产部署目标是用户的 **Air Mac**。当前开发机只用于编辑、测试、生成部署文档和检查 Compose 配置；除非当前任务明确运行在 Air，否则不得在当前机启动生产 Docker Compose、生成生产密钥、迁移真实记忆、安装或重启 `riji-agent`/Hermes 服务。
- Air 的既有 SSH 入口是 alias `air`。任何部署写操作前，先通过该 alias 运行 `system_profiler SPHardwareDataType`，确认 `Model Name` 为 `MacBook Air`；主机身份不符时立即停止，不得改用猜测的 IP、用户名或目录。
- Codex 当前没有连接到 Air host 时，只完成代码和本地无副作用验证，并明确报告“等待 Air 连接”；不得把当前 `local` host 当成 Air，也不得用当前机 Docker 代替生产验收。

Air 上的安装结构以远端登录用户的 `$HOME` 为基准。必须通过已经验证的 `air` 会话在远端确定 home，不从开发机 `$HOME` 推断，不猜用户名；执行前核对现有安装，不能用通用结构覆盖已部署配置：

- 源码镜像：远端 `$HOME/Documents/ai_agent/riji-agent`。它不是 Git repository，不得在 Air 上执行 `git pull` 或假设存在 branch/remote。
- 生产 runtime：远端 `$HOME/.local/share/riji-agent-runtime`。
- Python：远端 `$HOME/.local/share/riji-agent-runtime/venv`，其中 riji-agent 以 editable install 指向上述源码镜像。
- 生产 `.env`：runtime 目录下的 `.env`；源码镜像中的 `.env` 也属于 Air 本地秘密文件。两者都不得由开发机覆盖。
- 常驻服务：`~/Library/LaunchAgents/ai.riji-agent.plist`，label 为 `ai.riji-agent`。
- 开发机可用 `ops/launchd/ai.riji-agent.air-tunnel.plist` 模板配置常驻转发。模板中的 `__LOCAL_LOG_DIR__` 必须在安装副本中替换为安装机的绝对日志目录；launchd 不会展开该占位符，不能直接加载仓库模板，也不能覆盖已有 live plist。转发本机
  `127.0.0.1:8765` 到 Air 的 riji-agent。Fleet 现有 `riji-agent-air` 服务的
  `openUrl` 应为 `http://127.0.0.1:8765/admin/memory`；不得把 Review token
  写入 Fleet catalog、plist 或 URL，也不得把 Air 的 8765 暴露到非 loopback。
- Air 的非交互 SSH 不会自动包含 Homebrew 路径。执行 `docker`、`docker compose`、
  `colima` 或其他 Homebrew 命令前，固定设置
  `PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`。
- Air 使用 Colima `default` profile（Docker runtime）；该 profile 可能同时承载其他项目。
  未经用户明确授权，不得停止、重启或重建 Colima，也不得清理、停止或重启
  非 `riji-mem0` Compose project 的容器。
- `riji-mem0` 不从 Air 的 `Documents` 目录 bind mount 文件。初始化脚本和
  FastEmbed 模型种子打进固定镜像，运行数据只放命名 volume，避免 Colima
  在该目录的单文件 bind mount 卡住容器生命周期操作。

### Air 部署顺序

1. 在开发机完成全量测试。部署前用 rsync dry-run 比对，再把经过验证的源码树同步到 Air 的源码镜像；必须排除 `.git/`、`.env`、`.env.*`（但允许 `.env.example`）、`.venv/`、缓存、`output/`、SQLite、日志和其他运行数据，且不得使用 `--delete`。
2. 同步前在 Air 的 `~/.local/share/riji-agent-runtime/deploy-backups/<timestamp>-<change>/` 备份即将替换的源码和部署文件。发布失败时从该目录按原路径恢复。
3. 先查询 Fleet 的 `riji-agent` 项目，确认部署主机为 Air 且 Agent 心跳有效，再核对固定端口 `8765`、`38880`、`38881` 的主机、监听地址、PID、命令、工作目录及服务身份。已确认属于本项目的生产常驻服务正常监听不构成端口冲突；在用户已授权本次更新时，按备份、同步、测试和第 9 步执行受控升级，不因正常监听重复请求确认。只有监听者属于其他项目、身份未知、登记重复或实际归属与 Fleet 不符时，才停止相关部署步骤并报告归属，让用户决定；不得为释放端口直接杀进程。Fleet 登记不能单独代替实际进程及容器归属核验。
4. 同步完成后先用 Air runtime 的 Python 执行相关测试；只有测试通过才能变更依赖、配置或重启服务。依赖发生变化时，在 Air 源码镜像运行 `uv sync`，再确认生产 venv 仍以 editable install 指向源码镜像。
5. Mem0 首次部署只在 Air 创建未跟踪的 `infra/mem0/.env`。已有文件必须保留，不得覆盖；真实 `POSTGRES_PASSWORD`、`JWT_SECRET`、`ADMIN_API_KEY`、`DEEPSEEK_API_KEY` 只从 Air 本地凭据来源填写，禁止打印、提交或通过聊天传输。
6. 在 Air 源码镜像的 `infra/mem0/` 目录按该目录的 `README.md` 构建并启动
   固定版本的 Mem0 stack；Compose 命令必须显式使用该目录未跟踪的 `.env`。
   确认 PostgreSQL 没有宿主机端口，Dashboard/API 只监听
   `127.0.0.1:38880/38881`。首次构建从 FastEmbed 官方 Google Storage 备用源
   预置 `BAAI/bge-small-zh-v1.5`，运行期设置离线模式并从镜像种子填充模型
   volume，不依赖 Hugging Face 可达性。
7. 迁移前保持生产 `.env` 的 `RIJI_MEMORY_PROVIDER=sqlite`。在 Air 依次运行带一次性 `RIJI_MEMORY_PROVIDER=mem0` 覆盖的 `memory migrate --dry-run`、`--apply`、`--status` 和 `memory snapshot`；正式迁移必须先生成并核验 SQLite 备份。
8. 在 Air 的 Dashboard、Memory Review 和 `MEMORY.md` 核对数量、共享事实与导师隔离后，才把 Air 的生产 `.env` 永久切到 `RIJI_MEMORY_PROVIDER=mem0` 并启用自动捕获。
9. 在 Air 运行 production runtime 中的 `riji-agent doctor`，随后用同一 executable 的 `service install|restart|status` 管理 `launchd`；Hermes bridge 只用同一 executable 的 `hermes-bridge install|status` 管理。最后核验 `/healthz`、飞书私聊、严格日记确认和 Memory Review。
10. 若 Mem0 验证失败，先把 Air 的 provider 回退为 `sqlite` 并重启 riji-agent；不要删除旧 SQLite 备份或执行 `docker compose down -v`。PostgreSQL/history 恢复遵循 `infra/mem0/README.md`。

部署细节以 `docs/deployment.md`、`docs/long-term-memory.md` 和 `infra/mem0/README.md` 为准；本节定义不可越过的主机边界和执行顺序。

### Fleet 与端口归属

- `riji-agent-air`：Air 的 `127.0.0.1:8765`，对应 launchd `ai.riji-agent`；界面代码更新只重启该服务。
- `riji-agent-mem0-dashboard`：Air 的 `127.0.0.1:38880`，对应 `riji-mem0` project 的 Dashboard 容器端口 `3000`。
- `riji-agent-mem0-api`：Air 的 `127.0.0.1:38881`，对应同一 project 的 Mem0 API 容器端口 `8000`。
- Mem0 两项通过 Fleet 的发现认领接口登记为仅观测服务，容器健康仍在 Air 核验。其 loopback 地址只在 Air 有效；不能把它们当作开发机的直连入口。补登记不扩大端口监听范围，也不授予重启共享 Colima 的权限。
- Colima 在 Air 宿主机可能把两个 Mem0 监听显示为同一个 `ssh` 转发进程。须与容器 project 标签及发布端口交叉核对，不能仅凭进程名认定外部占用。开发机的 `127.0.0.1:8765` 是 `ai.riji-agent.air-tunnel` 转发入口，与 Air 的同号端口属于不同主机。

### 共享容器运行时维护边界

- 完整 `docker compose up` 或容器清理前，核对本地维护记录与现有容器状态；发现已知挂载卡住、Created 残留或 Docker 请求不返回时，不反复 inspect、rename、remove 或让 Compose 自动 reconcile。
- 不得为清理单个异常容器重启共享 Colima。只有用户批准共同维护窗口、完成 volume 备份并确认其他项目的影响后，才可按精确容器 ID 处理。
- 不得删除 `riji-mem0_postgres_data`、`riji-mem0_mem0_history` 或 `riji-mem0_fastembed_models` volumes。真实容器 ID、故障时间与维护现场保存在私人运维记录中，不提交仓库。
