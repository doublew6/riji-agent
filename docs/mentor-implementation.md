# 导师沟通实现与验收记录

2026-09-10。对应 [产品 PRD](product/mentor-dialogue-and-debate.md) 与 [技术设计](architecture/mentor-dialogue-and-debate.md)。本地导师入口已部署到 Air；四个独立导师应用已在飞书后台发布，正式接收器已安装运行，四位双端绑定及固定虚构问题的真实回复已验收，私人圆桌群未启用。这是分阶段交付记录，不替代整份 PRD 的验收。新安装仍默认关闭导师功能。

最新基础代码发布、四应用后台配置与身份探针进度见[飞书接入 Air 交付记录](feishu-mentor-air-2026-09-10.md)；前次本地版模型验证及关闭演练见 [Air 本地版交付记录](mentor-local-air-2026-09-10.md)。

飞书后续工作跟踪 [Issue #43](https://github.com/doublew6/riji-agent/issues/43)，详见[飞书接入实施设计](architecture/feishu-mentor-integration.md)。S1 双端账号绑定、S2 持久接收与未知结果处理、S3 原入口命令分派已在代码中实现；四应用后台配置和发布已完成；可信租户证明已齐、正式接收器已安装；四位双端绑定与固定虚构问题的真实回复已验收；群能力、记忆隔离和其他完整验收继续单列，不能据此认定整份产品已完成验收。

## 已实现的本地闭环

- 固定导师私聊、独立问题、续聊、新问题和历史选择。内部身份保留既有记忆 owner，不按显示名称合并用户。
- 2–4 位导师独立参考、共识比较、最多两轮辩论和综合。参考可一次升级为辩论，沿用 24 次请求、600 秒预算；保留最后一次综合机会，无实质分歧时允许提前收束。
- 补充、停止、提前总结及结束后的单导师追问。输入变化或停止使迟到结果失效；格式或引用失败最多修复一次并计入预算。
- 本地 SQLite、事务 outbox、持久化租约与请求预算；未知结果暂停，不自动重复生成或投递。重启后不自动继续旧讨论。
- 复用获准的已确认记忆，圆桌只选所有入选导师都可用的交集。发送、展示、回看时重新检查来源。新路径尚未自动捕获记忆，圆桌及导师输出不会污染长期事实；旧入口捕获规则保留。
- 所选发言转交预览与确认 API，继承来源限制；保存讨论只形成转交意图，须在已验证的 Riji 私聊看草稿后明确确认。
- 记录查询、删除、校验和导出及只读恢复。删除清除讨论产物、未保存转交草稿与 checkpoint，保留墓碑；明确转交、确认保存或导出的副本独立管理。
- 本地 API 与 Feishu SDK 共用业务端口。`/admin/mentors` 提供本地验证页面，访问令牌只保存在浏览器内存中。

## 实现入口

代码位于 `src/riji_agent/mentors/`：

| 模块 | 职责 |
| --- | --- |
| `models`、`identity`、`store` | 通用身份、独立问题、持久化 |
| `service`、`policy`、`budget` | 命令、来源/场所授权、预算 |
| `planner`、`worker`、`langgraph_adapter` | 有限步骤、租约、标识级 checkpoint |
| `generation`、`sources`、`transfer` | 原模型适配器、已确认记忆、显式材料转交 |
| `delivery`、`notices` | 顺序投递与中性私聊异常通知 |
| `history`、`handoff` | 回看、删除、导出恢复、日记确认 |
| `api`、`ui`、`local_channel` | 已认证本地 API 和第二渠道验证 |
| `feishu`、`receiver`、`receiver_spool`、`receiver_worker` | SDK、独立接收队列和回执状态 |
| `legacy_route` | 原 Riji 已鉴权入口的讨论命令分派 |

可选 `mentors` extra 锁定 LangGraph 1.2.11、`langgraph-checkpoint-sqlite` 3.1.1 和 `lark-oapi` 1.7.3。checkpoint 不保存问题、正文、来源内容或凭据，编排期间关闭 LangSmith tracing。

## 本地配置

使用 `uv sync --extra dev --extra mentors` 安装开发依赖。生产安装遵循 Air 部署顺序。设置 `RIJI_MENTORS_ENABLED=true` 和 `RIJI_MENTORS_CONFIG_PATH`；配置 JSON 必须位于 vault 外且权限为 `0600`。凭据只通过环境变量引用，不写入 JSON、URL 或仓库。

配置包含 `applications` 和 `users` 数组，以下均为虚构结构：

常驻服务可以另设 `credentials_env_file`，引用 vault 外权限为 `0600` 的私有环境文件。仅解析所需凭据，不执行变量插值；进程环境变量优先。这样无需将令牌放入 launchd plist。该文件、配置文件和访问令牌均不进入仓库。

```json
{
  "applications": [
    {"platform":"local","tenant":"synthetic","external_id":"host","persona_id":"host","transport_token_env":"LOCAL_HOST_TRANSPORT_TOKEN"},
    {"platform":"local","tenant":"synthetic","external_id":"gentle","persona_id":"gentle_reviewer","transport_token_env":"LOCAL_GENTLE_TRANSPORT_TOKEN"},
    {"platform":"local","tenant":"synthetic","external_id":"blunt","persona_id":"blunt_coach","transport_token_env":"LOCAL_BLUNT_TRANSPORT_TOKEN"}
  ],
  "users": [{"account":{"platform":"local","tenant":"synthetic","subject":"synthetic-user"},"legacy_owner_key":"synthetic-owner","review_token_env":"LOCAL_MENTOR_REVIEW_TOKEN"}]
}
```

其余导师键为 `future_self`、`wang_yangming`，按相同结构配置。每个令牌至少 32 字符且互不相同。示例只能用于合成验证；真实 owner 必须来自已验证身份。

本地模式在启动时建立对应私聊 binding；通过项目原有 `127.0.0.1:8765/admin/mentors` 使用用户查看令牌进入。Air 已配置本地主持人与四位导师，关联现有已核验 owner，使用原 Memory Review 私人令牌登录。凭据仅保存在 Air 私有文件中；开发机通过既有 loopback SSH 转发访问。没有在开发机启动生产服务。

本地圆桌的固定成员配置持久化在业务库中，重启后可继续验证历史访问；删除讨论同时清除对应本地房间记录。这不替代飞书的外部成员连续性核验。

API 前缀为 `/api/mentors/v1`：`/me`、`/conversations`、`/conversations/{id}/share-preview`、`/conversations/{id}/commands`、`/conversations/{id}/export`、`/restore`。恢复默认 dry-run，`apply=true` 写入只读记录。

`/transfers/preview` 接受来源讨论、所选发言 ID 和目标导师；`/transfers/{id}/accept` 将确认材料加入未运行的目标问题，使旧分享预览失效。随后圆桌仍须确认新的分享范围。

用户查看令牌与每个应用传输令牌分离，通过 Authorization 请求头传递；传输应用不能凭自己的令牌读取完整历史，用户令牌不能冒充渠道事件。验证错误不回显输入。

## Feishu 接入与发布门槛

四导师显示名已统一为“日记导师·温柔回顾者”“日记导师·直率教练”“日记导师·未来的我”“日记导师·王阳明”，`1.0.1` 均已发布审核通过。每个应用的可用范围弹窗均核验为部分成员且只选本人，外部私聊/对外群关闭；仅有 `im:message.p2p_msg:readonly`、`im:message:send_as_bot` 两个 scope。App Secret 仅保存在 Air 的 `0600` 私密文件中。

四应用官方认证及 `GET bot/v3/info` 均为 HTTP 200、`code=0`、`activate_status=2`，完整前缀名称匹配。四份可信测试私聊证明齐全且租户一致，临时探针已全部停止。主服务现为原 5 个 local 加 4 个 Feishu 应用，保留唯一 owner 和 Review 凭据；四个正式接收器已运行并核验进程归属、应用锁、spool 权限，各有 ESTABLISHED TCP。TCP 不代表 SDK 认证或真实私聊收发；安装核验未发消息或调用模型。

主服务 doctor、健康检查和导师页面通过，未认证 connections 返回 401。四位导师均已通过真实本机 Feishu 用户客户端发送绑定命令、由机器人返回 8 位码、同一 owner 在已认证网页确认；网页均显示“已连接”。随后四个正确应用对同一固定虚构读书拖延问题均实际返回接收提示和独立导师答案，客户端已逐一核验。绑定后 Air 只读 API 四项 connected=true；数据库唯一 owner、四份外部身份绑定、四份私聊绑定和四份已确认链接映射一致，owner 配置、account 与 legacy_owner 和安装前备份一致，待确认请求为 0，无额外应用、账户或私聊绑定。共享记忆归属一致已核验；本次链路验收不代表完整质量、共享事实或私聊隔离实测通过。Hermes、Mem0 未重启，群能力门槛保留。

事件标准化及私聊发送按已安装的官方 SDK 类型实现。独立接收进程入口为 `python -m riji_agent.mentors.receiver`，参数见 `--help`。应用配置使用 `platform=feishu`、租户标识、应用 ID、`secret_env` 和独立传输令牌引用。用户映射采用已验证的租户 `user_id`，`open_id` 按应用隔离。

新导师使用 `feishu_receiver_ownership=dedicated_apps` 和默认 `receiver=dedicated`。每个接收进程使用应用互斥锁，不能与 Hermes 同时接收同一应用。原 Riji 主持人可显式配置 `receiver=hermes`，必须匹配既有 App ID，且不会获发独立 transport token。其讨论命令在旧 Gateway 完成鉴权后分派；本阶段保留原接收者，群路径仍关闭。

支持的文本入口：

- 固定导师直接接收问题；`/新问题 内容`、`/历史`、`/切换 问题ID`。
- 主持人：`/圆桌参考 gentle_reviewer,blunt_coach | 问题` 或 `/圆桌辩论 ...`；随后按预览发送 `/分享 问题ID 预览指纹`。
- 控制：`/停止 问题ID`、`/继续 问题ID`、`/先总结 问题ID`、`/开始辩论 问题ID`、`/补充 问题ID | 内容`、`/追问 问题ID 导师ID | 内容`。
- 保存：`/保存讨论 问题ID` 形成转交；在 Riji 私聊发送 `/接收转交 转交ID`，看草稿后发送 `/确认转交 转交ID`。

**私人 Feishu 群尚未启用。** 当前 SDK 的群详情不能完整证明新增成员的历史可见性、全部受管机器人以及中断期间连续性。建群方法在调用任何外部建群 API 前返回能力未验证；不能用配置布尔值关闭这个门槛。模拟测试未被当作真实群验收。

必须完成技术设计 FG-01..05：合成群创建及完整成员核验、历史可见性和事件连续性、跨应用身份、去重及未知投递核对。参考官方 [创建群](https://open.feishu.cn/document/server-docs/group/chat/create) 与 [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create) 文档。若平台无法满足保证，回到产品层选择圆桌载体。

## 验证与尚未完成的验收

确定性测试覆盖完整讨论路径、参考升级共用预算、停止与补充竞态、来源撤回、未知结果暂停、固定身份、事件重投、私聊保存、转交、导出删除抑制、真实 LangGraph checkpoint 与本地 HTTP wiring，并回归旧 Riji 日记确认和工具权限。

本地核心阶段历史全量回归：876 项中 872 项通过、4 项跳过，0 失败；新增文件和已跟踪文件的隐私扫描均无发现。报告保存在 `output/mentor-evaluation/regression.xml`。CI 已加入可选导师依赖；私有配置的运行接入要求可核验的文件权限，Windows 的该组集成测试暂不执行。

后续飞书接入基础代码的历史回归为开发机 957 项（953 通过、4 跳过）及 Air 172 项相关回归全部通过。本次身份探针在开发机与 Air 均通过 61 项合成检查，接收器安装材料在两端均通过 34 项合成检查；各组独立记录，不合并计数。本轮正式安装另有开发机 957 项（953 通过、4 跳过）与 Air 154 项相关回归全部通过；Air 154 与历史 172 单列，不能相加或互相替代。实际安装和可信私聊证明另行记录，详见[飞书接入 Air 交付记录](feishu-mentor-air-2026-09-10.md)。

真实模型仅使用 `evals/mentors/cases.json` 的虚构问题，通过 `scripts/evaluate_mentors.py --live --output ...` 执行，没有读取私人日记或真实记忆。输出保存在忽略提交的 `output/mentor-evaluation/`。四导师样例及一轮双导师辩论已完成；直率语气与用户消息引用问题经修正后复测。自动完成不等于全面语义质量达标。

各场景的最新有效结果及助手逐项审阅保存在 `output/mentor-evaluation/reviewed-samples.json`。这些是开发样例，不是用户验收或全面质量评分；综合输出行动项的数量还需要扩大回归。

以下仍属于后续交付工作，不能记作已完成：

- 真实内容共享、独立共享事实、私聊隔离及重启恢复验收；含原 Riji 的五应用完整收发、S4 FG-01..05、手机端交互和原 Riji 新增讨论入口验收。
- 自然语言群指令、活跃讨论中点名导师优先回答的完整调度、材料删改与更友好的恢复界面；当前行为以已列文本命令和 API 为准。
- 未知模型结果、未知投递的人工核对入口；当前暂停，不允许通过“继续”盲目重试。
- 原始日记和导师知识工具在新路径的逐次来源计量与调用；当前只使用获准已确认记忆和显式转交材料，旧入口工具能力保留。
- 新私聊自动捕获与删除抑制的衔接：须覆盖已排队、正在提取和正在写入的记忆任务，再启用自动捕获。
- 更大范围的质量回归、负载/延迟评估和备份压缩；Air 本地入口已部署，飞书群及手机端仍待接入验收。

SQLite 使用 secure deletion，checkpoint 可按讨论清除；这不等于擦除系统快照、用户旧备份、提供方留存或飞书历史。恢复记录保持只读，不自动关联跨渠道身份或重新授权来源。
