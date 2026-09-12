# Agent v1 评测接入

本目录包含 120 个助手编写的合成场景，供 EvalMesh 调用本项目真实业务服务。
这不是独立人工标注集，也不是盲测题库。`acceptance-candidate` 仅表示留待评审的
候选分组；人工审核、来源组独立性和正式质量验收仍需另外完成。

| 文件 | 场景 | 质量边界 |
| --- | ---: | --- |
| `boundaries.jsonl` | 40 | 权限、确认、AI 来源、失败与恢复；受控模型及渠道 |
| `memory.jsonl` | 36 | 20 个提取/给定邻居关系题，16 个真实本地 FTS 问答题 |
| `mentors.jsonl` | 44 | 16 个私聊、16 个参考/辩论、12 个常驻问题题 |
| `memory-review.json`、`mentor-review.json` | 分离保存的判据 | 内容判据不进入被测 Agent 输入 |
| `comparison-review.json` | Issue #46 比较归因专项判据 | 核对双方逐字依据、条件、关系及综合，不能以结构通过代替语义通过 |

题面、合成模板和领域判据留在本项目；EvalMesh 负责重复执行、机器判分、隐私投影
和批次比较。通过 `observed` grader 只代表程序/协议不变量满足，不代表提取完整、
检索充分或导师回答有帮助。零候选、无引用但有回答等情况必须结合内容标注审阅。

## 准备与执行

使用 Python 3.11+ 的项目环境及本地 EvalMesh 源码。评测器不调用 `load_settings()`，
也不读取 `.env`。有真实模型题时，显式传入 `--live` 才允许运行。DeepSeek 仅使用
官方 API 地址和进程环境中的 `DEEPSEEK_API_KEY`；Codex 必须显式提供独立的
`RIJI_EVAL_CODEX_HOME`，可用 `RIJI_EVAL_CODEX_BIN` 指定已有兼容 executable。
不会复制个人 Codex 认证、安装服务、创建真实记忆或向飞书发消息。

Issue #49 将合成 DeepSeek 评测客户端固定为验证 TLS 的直连模式（`trust_env=false`），
忽略环境变量及 macOS 系统代理，也忽略 `SSL_CERT_FILE` / `SSL_CERT_DIR` 环境 CA
设置。不会关闭证书或主机名验证，也不在失败后自动切换路由或重试。生产 provider 的
默认代理继承行为不变；Codex 路由仍由其既有 runtime 管理。两种路径的探针均曾成功，
已确认的是隐式路由差异，不能据此认定旧传输失败由代理造成。

Issue #50 使 DeepSeek 默认消费有界 SSE，通用 OpenAI-compatible 默认仍为 JSON。
每条实际 provider 输出另记 `provider_response_mode=sse|json`，由执行中的 provider
读取，不从路由策略推测；没有该能力的 provider 不增加此字段。具体实现由封存源码
哈希绑定。没有更改模型、thinking effort、TLS、路由或自动重试策略。

执行前通过既有秘密管理方式提供独立且持久的 `EVALMESH_HMAC_KEY`，至少 32 字节，
与模型凭据分开。它用于用例身份和快照封存，不传给被测进程。不要在命令行或题库中
写入凭据。真实模型的 token/费用当前未知；只报告实际计数和计量口径。

`EVALMESH_SOURCE` 指向已检查的本地 EvalMesh 源码，`PRIVATE_EVAL_BATCH` 指向
Git 工作树之外的全新绝对路径。建议最后一级使用随机 UUID；EvalMesh 会保护转发的
路径值，路径与公开 case/tag 标识重叠也可能导致拒绝运行。

准备只写私有评测材料，不调用模型：

```sh
.venv/bin/python scripts/evaluate_agent_suite.py \
  --evalmesh-source "$EVALMESH_SOURCE" --output "$PRIVATE_EVAL_BATCH" \
  --selection boundary
```

准备阶段固定源码、EvalMesh、依赖版本、模型参数、用例和独立评审文件；目标 fixture
不含答案及评审文件。若之后执行准备好的批次，应在准备时已提供同一 HMAC 密钥。

执行已经准备的确定性批次：

```sh
.venv/bin/python scripts/evaluate_agent_suite.py \
  --output "$PRIVATE_EVAL_BATCH" --run-prepared --execute
```

一次性准备并执行真实模型试跑时，用另一个全新私有目录，并显式指定实际模型：

```sh
.venv/bin/python scripts/evaluate_agent_suite.py \
  --evalmesh-source "$EVALMESH_SOURCE" --output "$PRIVATE_EVAL_BATCH" \
  --selection smoke --provider deepseek --model "$RIJI_EVAL_MODEL" \
  --memory-model deepseek-chat --live --execute
```

准备好的真实模型批次也必须加 `--live --run-prepared --execute`；执行读取已封存的
manifest，不能在执行时换模型。改模型或用例必须建立新批次。已开始、失败或完成的
目录不重用；诊断重放另存目录，保留首轮结果。

选择器为 `boundary`（40）、`smoke`（12，含 4 个边界题）、`quality-regression`
（40 个模型题）、`candidate`（40 个待审核模型题）、`all`（120）。`--case` 可重复
指定，`--repetitions` 为 1–3。Smoke 不是额外 12 个独立样本。

## 执行和数据边界

command target 从 stdin 接收 `evalmesh.case.v1`，实际调用临时状态中的受限服务，
返回 `evalmesh.result.v1`。适配器拒绝未知协议、重复 JSON 键、非合成标签、错误路径
及包含顶层 expected/rubric/gold 的输入。记录保留真实状态，禁止硬编码通过结果。

每次 attempt 都复制新的 fixture，并创建新的临时 vault/SQLite；多轮步骤只在同题
内延续。快照拒绝软链接，记录精确文件集合和文件哈希；运行前后验证 HMAC 封存的
源码、manifest、用例与标注。Python 环境的依赖版本被记录，但没有克隆整个虚拟环境，
不是可抵抗同一 OS 账户恶意修改的安全沙箱。运行期间不要变更该解释器的安装包。

私有批次保存：

- `snapshot.json`：源码、依赖、用例身份及 `provider_route_policy`；模型请求参数另在封存的 manifest 中。
- `runs.jsonl`、`summary.json`、`scorecard.json`：EvalMesh 的 PublicRun 派生成绩。
- `private-attempts/`：合成输出、有界模型输入/输出轨迹和安全错误分类，权限 0600。
- `review-index.json`：run/case/attempt 与私有记录的关联；串行时间窗口唯一匹配失败时
  明确标 unavailable，不猜测绑定。人工质量始终另存，不覆盖机器成绩。
- `reviews/`：冻结的独立内容判据；后续正式人工评分必须引用同一版本。

默认只写本地，未启用 Opik 内容发送。Private records 属于独立数据通道，不进入
PublicRun；不要把私有结果复制到仓库。工作台可以读取机器 summary，但当前宏观题
人评量表不能直接用于本题库。

当前 run 的模型 reasoning 仅随工具续轮回传，不进入最终答案、个人记忆或跨 run
聊天历史。实际请求字符计量包含它；私有轨迹只保留其字符数，不能据此原样重放完整
wire 请求。SSE 截断或缺少终态/`[DONE]` 不交付部分答案或工具。入站总量、单行、
reasoning 和工具片段均有界；逐块检查 600 秒 elapsed，但阻塞读取仍由既有 read
timeout 控制，不宣称严格墙钟截止，不延长调用方的 120/600 秒业务预算，也不把
180 秒 worker lease 当作模型请求总时限。详见 [模型失败边界](../../docs/model-failure-diagnostics.md)。

准备阶段要求冻结的 `providers.py` 与当前路由策略实现逐字节一致；程序化传入旧版或
不同 subject 时拒绝生成封存凭据，不执行其代码来推测路由。

快照与每条模型结果的 `provider_route_policy` 使用同一策略函数，包含配置的路由、
TLS 验证及环境继承策略，不记录代理地址、凭据或异常原文。纯边界批次在快照中标记
`no_model`，不构造模型 HTTP 客户端。策略描述应用客户端配置，不能证明操作系统
或网络中不存在其他转发。策略变更须建立新封存批次，保留旧结果及其原评测条件。

## 验证范围与已知限制

记忆部分只测实际提取器与给定邻居的关系判断，未调用真实 Mem0、BGE 或发现/写回
管线。检索使用真实 FTS、AgentRunner 和 ToolRegistry，仍不代表语义向量召回。
恢复场景验证 SQLite 重开和状态恢复，不代替进程崩溃、Air 或平台投递验收。

CountedProvider 记录尝试次数、应用请求字符和有界私有轨迹，不把尝试称为计费请求。
Issue #47 修复后，HTTP adapter 与导师 worker 保留固定的超时、认证、限流、拒答等
错误分类，详见[诊断契约](../../docs/model-failure-diagnostics.md)。不确定的请求结果仍
要求核对后恢复；历史通用错误无法补推根因。三个边界题更新了错误字段断言，旧封存
用例及成绩保持原样，因此两个版本的诊断字段成绩不能直接作为改善幅度。

Issue #44 修复后，私聊追问携带同一固定窗口内有界的用户材料与已投递导师回复，
保留原题“不得丢失首轮材料”的断言。比较阶段新增证据归因契约与独立内容判据，见
[比较说明](../../docs/mentor-comparison.md)。首次失败及内容质量疑点见
[首轮评测报告](../../docs/evalmesh-evaluation-results-2026-09-11.md)。未执行或未审核的场景不得计入通过率。

## Live 评测环境有效性（Issue #52）

macOS 上仅显式 `--live --execute` 且含模型题的批次启动本任务的
`caffeinate -i -w <runner PID>`，运行结束或异常时释放。启动失败则不执行批次；
子进程提前退出或清理失败会使环境验收失败。不保持屏幕常亮，不修改永久电源设置，
不采集原始电源日志或设备信息。它不能阻止合盖或手动睡眠；采样期间应保持主机唤醒、
不要合盖。其他平台不启动此 macOS 工具，但保留时钟连续性检查。纯 boundary
（即使带 `--live`）及只准备不执行均不启动休眠保护。

批次及每次 provider 调用记录 wall/monotonic elapsed 与差值；正负差绝对值超过
5 秒、负 elapsed 或非有限测量均标记环境无效。失败调用及超出正文轨迹上限的调用
也保留计时。这是环境连续性检查，不增加请求超时或业务预算；两种时钟一致的长请求
不会仅因耗时而判为环境失败。NTP 等系统时间跳变也可能触发失效，不能单凭差值把
模型失败归因于睡眠；它也不是覆盖所有睡眠情形的完整检测器。

新增私有 `environment.json` 与原始 `summary.json` 分开保存，记录环境结论、计划
attempt 分母、数值计时和固定错误码。机器通过但环境失败时，CLI 总体退出码仍为非零。
live 批次还核对私有记录总数与计划 attempt 数（包括混合批次中的边界题）；整条记录
缺失时不能宣称环境有效，即使机器报告通过。缺失或损坏的已有调用计时也不能当作
正常样本；目标进程来不及落私有记录就被终止时，
保留原 harness 失败分母，不伪造 provider 次数。环境检查不改机器分数、案例、模型、
次数、90 秒默认请求 timeout 或 120/600 秒业务预算，不自动重试，不重用旧批次。

封存 `snapshot.json` 增加 `environment_policy`，批次根保存 CLI 源码副本。
准备及 `--run-prepared` 均核对 environment/providers/suite/CLI 的实现字节与冻结版本
一致，再启动保护和执行；不能由新版 runner 把旧 fixture 描述成全程受保护。旧程序
不会被追溯改写，旧成绩继续按旧条件解释。实现变化后使用匹配的新进程、新封存批次。
详见[环境诊断范围](../../docs/model-failure-diagnostics.md#live-evaluation-environment-validity-issue-52)。
