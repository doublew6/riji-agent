# Codex 模型接入

Codex 可作为导师回答和后台记忆处理的独立选项，使用用户自己的 ChatGPT 套餐及项目专属的官方登录。日记、会话、记忆和任务的权威存储仍由 riji-agent 本地持有。对应 [PRD v1.4](PRD.md#16-codex-可选模型增量2026-09-09v14)；验证方法见 [部署与验收](codex-provider-air-2026-09-09.md)，本文是通用操作说明。

导师与记忆可以分别选择模型。日常日记处理每 UTC 日 100000 字符；用户可显式授权一次固定历史批次不设每日总量上限，仍保留单片段、来源版本、权限和独立计量限制。当前规则见 [PRD 16.6](PRD.md#166-用户确认的预算调整2026-09-09)。个人部署状态与真实记忆处理结果在本地核验、私下保管，不随接入说明发布。

## 配置

以下示例同时选择 Codex 导师回答和记忆处理。只需要更换记忆时，保留原 `RIJI_MODEL_PROVIDER`：

```dotenv
RIJI_MODEL_PROVIDER=codex
RIJI_MEMORY_MODEL_PROVIDER=codex
RIJI_CODEX_BIN=codex
# Optional: defaults to the codex subdirectory of RIJI_DATA_DIR.
# RIJI_CODEX_HOME=/absolute/path/to/riji-data/codex
# Optional: use an existing loopback proxy only for Codex child processes.
# RIJI_CODEX_PROXY_URL=http://127.0.0.1:8080
RIJI_CODEX_MODEL=gpt-5.6-terra
RIJI_MEMORY_CODEX_MODEL=gpt-5.6-luna
RIJI_CODEX_TIMEOUT_SECONDS=90
```

| 设置 | 用途 | 默认值 |
| --- | --- | --- |
| `RIJI_MODEL_PROVIDER` | 导师回答 provider，支持 `deepseek`、`openai`、`codex` | `deepseek` |
| `RIJI_MEMORY_MODEL_PROVIDER` | 日记提取、关系比较、聊天捕获及后台整理，支持 `deepseek`、`codex` | `deepseek` |
| `RIJI_CODEX_BIN` | 官方 Codex 可执行文件；后台服务建议指定可用的绝对路径 | `codex` |
| `RIJI_CODEX_HOME` | 本项目专属、持久的 Codex 配置及官方凭据目录 | `RIJI_DATA_DIR/codex` |
| `RIJI_CODEX_PROXY_URL` | 可选的现有本机 HTTP(S) 代理，仅用于 Codex 子进程；按秘密配置处理 | 未设置；保留既有允许的代理环境变量继承 |
| `RIJI_CODEX_MODEL` | Codex 导师模型 | `gpt-5.6-terra` |
| `RIJI_MEMORY_CODEX_MODEL` | Codex 记忆模型 | `gpt-5.6-luna` |
| `RIJI_CODEX_TIMEOUT_SECONDS` | 单次调用的有界等待时间 | `90` |

模型名需要在该用户账号上实际可用。更换模型或接收方会使已有日记授权失效；先检查 Memory Review 的权限栏，再登记新授权并继续处理。`RIJI_MEMORY_PROVIDER` 仍表示本地存储后端，选择 Codex 不要求改变 Mem0/PostgreSQL/FastEmbed，也不迁移日记数据。

同一次导师请求的所有模型轮次共用有界执行期限，排队、启动及发送前权限检查不会给每轮重新分配完整额度。

本次适配器仅接受已完成协议与能力隔离验证的 Codex CLI 版本 `0.153.4`、`0.153.0-alpha.5`。未知版本失败关闭，不因升级客户端而自动启用其新工具或配置行为。新增支持版本须重新验证实际请求的空工具列表、全局指令隔离及认证方式，再更新允许列表；模型也不自动升级或替换。

### 现有本机代理

macOS 已配置系统代理，不代表 SSH 或 LaunchAgent 自动具有 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量。若浏览器可访问 ChatGPT、后台 Codex 却直连超时，可显式设置 `RIJI_CODEX_PROXY_URL`，让 Codex 使用同一台机器上已有的代理服务；此选项不安装代理，也不读取或改写系统代理设置。

该值只接受 HTTP(S)、loopback 主机和显式端口，不接受用户名、密码、业务路径、query 或 fragment。未设置时沿用既有的允许环境变量继承；显式设置后，仅注入 Codex 子进程的代理变量，并把该子进程的 `NO_PROXY` 限定为本地地址，不改变父进程、系统、Feishu 或 Mem0 的连接配置。配置使用 `SecretStr` 保护，普通日志和诊断不回显代理 URL。

上面的 loopback 端口仅是格式示例，实际值应来自运行服务主机上已配置的代理。后台连接依赖该代理可用，项目不启动、停止或修改代理进程。官方账号登录保存在服务主机专属目录，完成首次设备授权后不需要授权浏览器持续在线。

网络超时不能自动解释为登录过期；先区分已有官方登录状态与网络可达性，避免因连接问题反复登录。此配置不会把模型接收方从 OpenAI 改成另一个模型服务，也不扩大日记范围、调用预算或模型工具权限。正式启用前仍须以合成内容验证运行服务时的实际网络通路。

## 登录与计费

为了避免个人 Codex 的全局 AGENTS、MCP、skills 和其他能力被带入日记调用，项目使用独立的持久 `RIJI_CODEX_HOME`。因此首次需要在这个目录完成一次官方登录；即使个人 Codex 已登录，也不复制或链接它的凭据或配置。同项目的导师对话、后台提取和服务重启共用这份独立登录，后续续期由官方运行时完成。

用运行 riji-agent 服务的同一个系统用户，在 Air 上将 `CODEX_HOME` 指向项目配置的专属目录，再检查所配置的 Codex 可执行文件。以下路径为示例，应替换为实际 `RIJI_CODEX_HOME`：

```sh
codex --version
CODEX_HOME=/absolute/path/to/riji-data/codex codex login status
CODEX_HOME=/absolute/path/to/riji-data/codex codex login
```

只有这个专属目录尚未登录或官方确实要求重新认证时，才运行上面的 `codex login`。不要读取、复制或自行刷新登录 token，不把登录文件同步到另一台设备，不写入项目 `.env`、日志或仓库。专属目录不得放入 vault、共享同步目录或源码发布清单，也不得加载通用编码配置；其官方凭据须由用户本机妥善保护。

ChatGPT 登录使用套餐访问，API Key 登录属于 API 按量计费；本适配器要求前者。凭据保存和刷新由官方 Codex 管理，重启 riji-agent 不应主动退出或重新登录。授权撤销、刷新失败或账号策略变化仍可能要求人工登录。[官方认证文档](https://learn.chatgpt.com/docs/auth)

后台任务和日常 Codex 使用共享套餐额度。项目不会购买额度、兑换重置、配置 API Key fallback 或在失败时切到 DeepSeek。用户自己在 ChatGPT 中设置的账号计费及额度政策仍以账号实际状态为准；“使用套餐”不是无限调用承诺。

## 调用边界

适配器使用官方非交互 `codex exec`，通过 stdin 传入有界请求并验证结构化输出，不自行重现 ChatGPT 私有 HTTP 请求或管理 OAuth 续期。每次采用 `--ephemeral`、独立工作目录、隔离的持久登录目录，并禁用 skills、MCP、hooks、apps、Shell、网络浏览等通用能力。空工具列表及无全局指令继承必须通过实际请求验证，未通过不得接入真实日记。[非交互 CLI 文档](https://learn.chatgpt.com/docs/non-interactive-mode)

1. riji-agent 校验用户、导师、来源权限和预算，构造本轮有界消息。
2. Codex 在独立临时上下文中接收这些消息，不继承个人 Codex 任务和其他导师的上下文。
3. 通用 Shell、文件、网络浏览、MCP/连接器和编码工具不可用。模型只能返回项目允许的工具意图，实际权限判断与执行在 riji-agent。
4. 记忆候选通过原有 JSON、来源原文引用、时间、版本及关系校验后，才由本地代码写入 Mem0。写日记继续经过草稿、diff 预览、用户确认及模板追加。
5. 完成、失败或超时后清理本次运行；本地日志只保存安全错误类别和用量等元数据，不保存 Codex 的原始事件流、推理过程或模型错误正文。

空目录和 read-only sandbox 不能单独阻止读取其他文件，所以工具禁用及异常请求拒绝属于发布门槛，不能用提示词“不要访问文件”替代。

Code Mode 同样保持关闭：使用已验证 CLI 支持的 `features.code_mode.enabled=false` 和 `features.code_mode_host=false`。官方启动事件可能通过 `ErrorItem` 提示该能力已经禁用；适配器只精确识别这一条已审计的关闭提示，其他错误和近似文本继续安全分类或拒绝，任何原生工具活动仍拒绝。不得为了消除启动提示安装或开启 Code Mode host；客户端版本及配置变更需要重新执行真实 CLI 请求隔离检查。

## 隐私与授权

选择 Codex 后，接收方是 **OpenAI · Codex / ChatGPT（chatgpt.com）**。选定日记片段、相关旧记忆及用户问题仍可能出云；整库、原始文件、数据库和绝对日记路径不提交给模型。

Memory Review 显示提取模型、回答模型、接收方及四种用途。授权指纹绑定实际配置，不能沿用 DeepSeek 的授权静默启动 Codex。`private: true`、`memory: local/none`、段落排除和单条记忆权限继续生效；派生内容不能绕过来源限制。

临时上下文用于避免本项目将请求保存在可恢复的 Codex 会话中，不构成服务商零留存承诺。ChatGPT 登录适用该账号的数据控制，不能套用 OpenAI API 的处理承诺。权限、留存说明及独立飞书链路见 [数据出云边界](data-egress.md) 和 [隐私模型](privacy.md)。

## 调度与故障恢复

同一 riji-agent 服务内的 Codex 请求共用调度：导师请求优先于仍在排队的后台记忆；正在执行的背景请求不被强行取消。这一优先级不控制其他 Codex 应用对套餐的使用。

| 情况 | 行为与处理 |
| --- | --- |
| 未登录或登录失效 | 请求失败并保留本地任务；在 Air 使用同一官方客户端恢复登录后继续，不自动启动交互登录 |
| 额度用尽或服务限流 | 有界退避或暂停背景处理；不切换 provider、不购买额度；恢复额度后继续 |
| 模型不可用或 CLI 不支持必要协议 | 明确失败，先核实该账号/版本支持；不得降级为具有通用工具权限的执行方式 |
| 浏览器能访问而 SSH / LaunchAgent 直连超时 | 核对是否需要显式配置已有 loopback 代理；只调整 `RIJI_CODEX_PROXY_URL`，不修改系统代理、Feishu 或 Mem0 |
| 超时、进程失败、无效输出 | 终止本次调用，保留可恢复任务；不得在异常消息中暴露原始输入/输出 |
| 换 provider 或模型后日记授权失效 | 先在权限页核对接收方和范围，登记新授权，再继续 |
| 回退 DeepSeek | 由用户显式修改配置；如涉及日记模型变更，必须登记相应新授权，接受该 API 的按量费用 |

## Air 首轮历史初始化

遵循 [Air 部署流程](deployment.md) 先完成代码和合成数据测试，确认主机、源代码备份及生产数据备份。不得在开发机读取真实日记代替 Air 验收。

在 Air 核对实际 Codex 版本、专属目录的 ChatGPT 登录、所选模型、禁用工具和服务重启后的登录复用；尚未登录时完成官方首次登录，不能把个人目录已有登录当成此步骤已通过。安全检查通过后才切换生产配置。先保持日记任务暂停，扫描当前来源并在权限页核对新的提取/回答接收方。取得明确的接收方和范围授权，记录新授权后显式继续。

默认建议 `🧠 Notes` 区块、单片段 900 字符、每来源版本累计原文 4000 字符。历史、增量、整理和日记记忆召回四种用途分别确认，不能借由切换 provider 增加目录、日期、导师或外部对话导入。历史文件数量和版本以启用时重新扫描为准，不把之前预检的数量当成完成数。

日常默认 `RIJI_JOURNAL_MEMORY_DAILY_CHARS=100000`。需要历史批次例外时，由用户明确授权并设置 `RIJI_JOURNAL_MEMORY_INITIALIZATION_UNLIMITED=true`（默认 `false`）。先扫描，再登记绑定新预算的历史授权，最后恢复；首次冻结的证据 ID / 版本必须同时属于授权历史清单和当前有效版本。未扫描或空授权保持 `awaiting_scan`，不能把空授权后出现的文件自动纳入。重新授权、扫描、重试不扩充已冻结批次。其提取、关系判断及以本批产出记忆为种子的必要有界整理不设每日总量上限，按 `initialization:UTC-day` 单独计量，不占日常额度；旧日账本不清零、不追溯转移。新增、修改后的版本及普通整理种子仍受日常 100000 字符约束。

新策略生效应立即唤醒本批仅因旧 `daily_budget` 等待的任务，不清空账本、缓存或幂等记录，不重跑旧失败。提取达到终态且必要整理完成后持久关闭豁免，配置仍为 `true` 也不重开。来源限制、权限、账号额度、登录及网络错误仍生效；初始化无每日总量限制不等于 Codex 套餐无限，也不授权调用 usage reset、购买额度或收费 API fallback。

先记录真实首条提取及本地持久化，再报告成功、剩余、跳过、失败、日常预算和初始化单独用量。取消本批日总量限制不保证特定时间完成。真实运行的来源 ID、计数、哈希、状态、模型及必要用量记录在本地私有材料中；公开仓库只保存合成样本与通用验收方法，不包含真实日记或记忆正文。

## 开源边界

适配器可随项目开源，每位自托管用户自行登录自己的 Codex 账号。维护者个人套餐不作为多个商业客户共用的推理服务。托管服务的用户身份、计费和服务商授权另行设计；基础本地记忆管理、导出和权限控制保持免费。
