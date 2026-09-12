# 长期记忆、Mem0 与 MEMORY.md

2026-09-09 新增日记来源的历史初始化、增量维护和证据生命周期，启用步骤见 [日记长期记忆](journal-memory.md)。以下历史聊天回填与基础 Mem0 配置仍适用；日记来源不使用聊天回填命令。实际发布状态见 [验收记录](journal-memory-acceptance.md)。

riji-agent 使用混合长期记忆架构：原始日记 Markdown 是不可被 Agent
随意改写的事实源；Mem0 Self-Hosted 是 Agent 长期记忆的权威存储；
`MEMORY.md` 是方便人阅读的只读快照。

## 数据边界

| 数据 | 权威位置 | 写入规则 |
| --- | --- | --- |
| 原始日记 | `RIJI_JOURNAL_ROOT` 下的 Markdown | `draft_daily_entry` → 预览 → 用户确认 → `commit_draft` |
| 共享用户事实 | 本地 Mem0 | 普通对话后后台提取；所有导师可检索 |
| 导师观察 | 本地 Mem0 | 后台提取；仅同一 `persona_id` 可检索 |
| 会话与运行偏好 | 本地 SQLite | riji-agent 内部维护 |
| 捕获队列与变更日志 | 本地 SQLite | 追加记录，不进入模型上下文 |
| `MEMORY.md` | `RIJI_DATA_DIR/memory/MEMORY.md` | 从 Mem0 原子生成；手工改动会被覆盖 |

自动捕获只分析用户原话，不把助手回复、日记检索片段或确认命令复制进长期记忆。
日记记录请求中的明确自述与“帮我记住……”可以提炼事实，但不据此认定日记已经保存。
排除假设、编造、测试/验收探针和一次性指令。DeepSeek `deepseek-chat` 返回结构化事实后，
riji-agent 使用 `infer=false` 写入 Mem0，避免 Mem0 自动删除或静默重写旧
事实。包含凭据特征的消息不会进入捕获队列；即使 Mem0 被其他工具写入此类
内容，`MEMORY.md` 也只显示脱敏占位。冲突事实可以带各自时间和来源并存。

## 历史聊天回填与召回

Mem0 不会自动扫描旧聊天。`memory migrate` 只搬迁已确认记忆和偏好；
需要主动从历史中提取时，在 Air runtime 工作目录使用生产 executable：

```bash
./venv/bin/riji-agent memory backfill --dry-run
./venv/bin/riji-agent memory backfill --apply
./venv/bin/riji-agent memory backfill --status
```

`--dry-run` 只统计白名单用户的历史消息，不调用模型、不加入捕获队列。
`--apply` 先生成 mode-`0600` 的 SQLite 一致性备份并通过完整性检查，再把
符合预筛选的用户消息逐条加入持久队列。服务运行时由后台 worker 处理。
预筛选通过不代表一定产生记忆；模型可以返回空结果。

回填和实时捕获都以 `session_messages.id` 去重，已成功、正在处理和待重试的
来源不会重新排队。首次成功提取的结构化结果保存在队列中供失败重试复用，
任务完成后清除原文和临时提取结果。跨消息完全相同的事实按用户/导师范围去重，
不覆盖已归档或校勘的记录。已有旧版本任务没有消息 ID 时不会自动猜测关联。

记忆保留 `source_message_id`、`source_id=conversation/<id>` 和
`source_created_at`，页面、快照与模型上下文均展示原始消息时间。
历史阶段性状态和计划不得因刚刚回填而被当作当前事实；原始聊天保持不变。

导师可以调用只读 `session_search(query, top_k)` 查询最近上下文以外的用户原话。
搜索严格限制在同一用户、同一导师、同一聊天；不允许模型指定其他身份。
使用中文友好的字面关键词检索，最多 10 个结果、每段 400 字符、合计 1500 字符；
凭据内容和助手回复不返回。结果与审计使用 `[[conversation/<id>]]`，不得冒充日记来源。

## 启动 Mem0

基础设施固定在 `infra/mem0/`：Mem0 API 与官方 Dashboard 使用同一固定
upstream commit，embedding 由本地 FastEmbed
`BAAI/bge-small-zh-v1.5` 完成，PostgreSQL 不暴露宿主机端口。

```bash
cp infra/mem0/.env.example infra/mem0/.env
# 填入 POSTGRES_PASSWORD、JWT_SECRET、ADMIN_API_KEY 和 DEEPSEEK_API_KEY
docker compose --env-file infra/mem0/.env -f infra/mem0/compose.yaml up -d --build
```

端口固定为：

- Mem0 API：`http://127.0.0.1:38881`
- 官方 Dashboard：`http://127.0.0.1:38880`
- riji-agent Memory Review：`http://127.0.0.1:8765/admin/memory`

先在 `.env` 中写入 Mem0 地址与密钥，但在迁移核验完成前保留
`RIJI_MEMORY_PROVIDER=sqlite`。最终启用时的完整配置为：

```text
RIJI_MEMORY_PROVIDER=mem0
RIJI_MEM0_BASE_URL=http://127.0.0.1:38881
RIJI_MEM0_DASHBOARD_URL=http://127.0.0.1:38880
RIJI_MEM0_API_KEY=<ADMIN_API_KEY 或 Dashboard 创建的用户 API key>
RIJI_MEMORY_AUTO_CAPTURE=true
RIJI_MEMORY_CONTEXT_MAX_CHARS=2000
RIJI_MEMORY_SNAPSHOT_ENABLED=true
RIJI_MEMORY_REVIEW_ENABLED=true
RIJI_MEMORY_REVIEW_TOKEN=<独立随机令牌，至少 16 字符>
```

运行 `uv run riji-agent doctor` 检查 API、Dashboard、快照权限和后台队列。

## 迁移与快照

迁移期间用单条命令临时覆盖 provider，不提前改变正在运行服务的读取路径：

```bash
RIJI_MEMORY_PROVIDER=mem0 uv run riji-agent memory migrate --dry-run
RIJI_MEMORY_PROVIDER=mem0 uv run riji-agent memory migrate --apply
RIJI_MEMORY_PROVIDER=mem0 uv run riji-agent memory migrate --status
RIJI_MEMORY_PROVIDER=mem0 uv run riji-agent memory snapshot
```

正式迁移前会为 `memory.sqlite3` 创建 mode-`0600` 备份。迁移包含旧的已确认
记忆和用户可见偏好，不包含候选记忆、聊天记录和 `current_persona`。稳定
`legacy_id` 保证重复执行不会创建重复记忆。

在 Dashboard 和生成的 `MEMORY.md` 中核对数量与内容后，再把 `.env` 的
provider 永久切换为 `mem0`，启用 Memory Review 和自动捕获并重启服务。

`MEMORY.md` 只包含有效的共享事实和导师观察，不包含日记正文、聊天历史、
已归档/删除记忆、失败队列负载或密钥。每次成功变更后都会请求刷新；刷新
失败不会回滚 Mem0，后台 worker 会继续重试。

## Memory Review 安全与操作

Memory Review 只接受 loopback 客户端。登录令牌换取 HttpOnly、SameSite
会话 Cookie；所有写操作同时检查 CSRF，页面启用 CSP 和 `no-store`。
浏览器永远不会收到 Mem0 API key。

页面可搜索和筛选当前记忆，查看 Mem0 history 与本地追加式校勘记录，并
执行纠正、归档、恢复、永久删除、dead-letter 重试和 `MEMORY.md` 重新生成。
永久删除要求再次输入 `DELETE`；删除后的事件仍保留在本地变更日志中。

### 认识地图、比较与复核

Memory Review 默认打开“认识地图”：按偏好、目标、工作、关系、经历等主题
归纳已保存记忆，每条摘要可展开证据。它是可重建的整理层，不是新的事实来源；
Mem0 仍保存原始记忆。共享事实和每个导师的观察分批处理，不跨用户或导师比较。

- **认识地图**：显示主题摘要、证据、最近整理时间和覆盖数量。事实发生修改、
  归档或删除后，与其版本不符的摘要和比较立即隐藏。
- **比较与复核**：以双栏展示疑似重复、冲突、明确状态变化和仅相关的记忆，
  附模型判断依据。模型只生成建议；用户通过“校勘与历史”纠正正文，或归档
  冗余条目。系统不会因为较新就自动覆盖旧记录。
- **处理轨迹**：显示处理规则、真实捕获队列和追加式动作，包括新增、完全
  相同文本的去重跳过、无长期事实、人工纠正、归档、恢复及复核。旧数据缺少
  的比较过程不会补造。语义建议与实际修改分开显示。
- **原始记忆**：保留原有筛选、编辑、Mem0 history、快照下载和恢复入口。

“整理现有记忆”将请求持久化到 `memory-operations.sqlite3`，后台 worker
在捕获队列空闲时执行。每次成功捕获或人工修改也触发整理；同一用户尚未
开始的请求合并为一个，执行期间新到的请求保留。失败显示固定错误状态，
可重新请求；不影响原始事实或已完成报告。超过 15 分钟的中断任务在 worker
再次领取时标记为失败，不无限占用“正在整理”状态。

未启用日记记忆的既有整理路径，每次最多整理 100 条，每批最多 20 条、正文合计 6,000 字符、单条不超过
2,000 字符。过长内容、凭据模式、缺失导师身份的私有记录跳过；界面显示
已处理/总数和待整理项。语义比较只覆盖同批次，尚不提供全库任意两条的比较。
模型输出必须覆盖本批全部证据 ID；未知 ID、跨批引用、缺失归类或超长文本
会使整个请求失败。运行记录保留来源 ID、版本哈希和报告；不复制完整会话。

启用日记记忆的用户使用新的有界跨批次整理：每轮最多 5 个种子，每种子最多 12 条相关事实、4500 字符事实正文，进度持久化并逐轮继续。观察必须通过来源有效性检查才能进入回答，见 [提取、更新与主动整理](journal-memory.md#提取更新与主动整理)。

### 可恢复的遗忘

整理器仅标注明确阶段性计划、状态和待验证观察。它们以 `source_created_at`
或用户最近 `reviewed_at` 为起点，90 天未复核后进入复核列表，并在同范围
检索候选中排序后移；仍可进入上下文。稳定偏好、身份及历史事实不按年龄
失效；未知日期不推算到期日。列表按当前日期计算，不表示发生过删除。

“仍然有效”记下复核时间并重新开始计时；“归档”退出召回，可在原始记忆的
归档筛选中恢复。正文纠正也刷新复核时间。模型建议不自动归档、合并或删除，
此版本没有使用频次衰减或全自动冲突裁决。

本地界面验收可运行 `.venv/bin/python scripts/preview_memory_review.py`，固定
使用 `127.0.0.1:18765`，端口占用时启动失败。先检查监听者，不自动清理端口。
预览只使用合成记忆与本地模型替身，不读取生产配置或调用外部模型；登录令牌
为脚本打印的测试值。不要将该测试入口作为生产服务部署。

### 从开发机打开 Air Memory Review

`ops/launchd/ai.riji-agent.air-tunnel.plist` 是通用模板。安装前，在本地副本中把
`__LOCAL_LOG_DIR__` 替换为安装机的绝对日志目录，并用 `plutil -lint` 校验；
launchd 不会展开占位符或 shell 变量，不能直接加载仓库模板。保留已经安装的
live plist，按现有服务配置核验，不自动覆盖或重启它。

配置完成后，launchd 常驻维护 `127.0.0.1:8765` 到 Air 同端口的 SSH 转发。
若使用 Fleet，可把 `riji-agent-air` 服务登记为 frontend，并将 `openUrl` 设置为
`http://127.0.0.1:8765/admin/memory`。这样 Fleet 的打开按钮始终使用私有
隧道，不把 Memory Review 暴露到局域网或公网。

登录令牌仍只存放在 Air runtime 的 `.env`；Fleet catalog、plist 和浏览器
URL 均不得包含令牌。隧道必须使用 `BatchMode=yes`、
`ExitOnForwardFailure=yes` 和 loopback-only `-L` 绑定。

## 故障策略与备份

- Mem0 检索失败：本次对话不注入长期记忆，但聊天和日记工具继续工作。
- 自动捕获失败：指数退避重试；连续失败进入 dead-letter，由 Review 手动重试。
- 成功任务清除队列中的用户原文明文。
- v1 不启用自动删除、图记忆或周期性重写；阶段性信息只执行上述可恢复复核策略。

PostgreSQL 备份命令和恢复顺序见 `infra/mem0/README.md`。不要在没有验证
备份时执行 `docker compose down -v`，该操作会永久移除记忆、账号和 API key。

接口与自托管行为以 [Mem0 Self-Hosted 文档](https://docs.mem0.ai/open-source/setup)
和 [REST API 文档](https://docs.mem0.ai/open-source/features/rest-api)为准。
