# 飞书群权限与主持人接入验证

日期：2026-09-11。对应 [Issue #43](https://github.com/doublew6/riji-agent/issues/43)、[PRD v0.3](product/PRD-persistent-mentor-roundtable.md)及[Air 部署记录](persistent-mentor-roundtable-air-2026-09-11.md)。

**本文记录本轮修复前的验证基线。后续主持人群桥接已完成并部署到 Air，见[桥接部署记录](hermes-host-group-bridge-air-2026-09-11.md)；新增应用权限与真实群验收仍未通过。** 下文关于缺失桥接和应用配置的描述保留为当时证据。群历史可见性和断连期间成员变化另有尚未解决的证明要求，生产群讨论入口继续关闭。

## 1. Air 真实只读检查

通过既有 SSH 入口再次核验 MacBook Air，直接复用该机现有凭据查询既有合成圆桌群。未修改应用权限、群设置、运行配置或服务，未发送消息、调用模型或访问正式日记。

| 检查 | 实际结果 | 判定 |
| --- | --- | --- |
| 原日记导师与四位导师的应用认证、机器人信息 | 五个应用均成功 | 凭据与机器人身份查询可用 |
| 五个应用分别查询自身是否在群 | 全部返回 `99991672`，提示成员读取相关权限不足 | 无法证明五个机器人集合 |
| 成员列表 | 主持人请求返回 `99991672` | 无法证明唯一真人、完整人数或本地主体映射 |
| 四位导师查询群资料 | 全部返回 `99991672` | 当前缺少群资料权限；后续由主持人统一查询即可 |
| 原日记导师查询群资料 | 返回成功码，但群主、群类型及管理设置为空，仅有群状态与零人数 | 受限响应不构成完整快照，不能据此认定空群或权限合格 |
| 应用配置 | 五个 local 应用、四个 Feishu 导师；没有 Feishu host | 原日记导师尚未注册到圆桌运行时 |
| 原日记导师连接 | 既有 Hermes profile 对应原 App ID，唯一受管进程运行 | 接收权仍由 Hermes 持有 |

官方说明：调用身份在群内且与内部群同租户时才能获取完整群资料；群外可能只得到部分字段。本次受限响应与该情况一致，但自身在群查询仍缺权限，不能单靠该响应确定成员身份。[群资料接口](https://open.feishu.cn/document/server-docs/group/chat/get.md)

脱敏 API 报告保存在 Air runtime 的 `acceptance/group-host-20260911T075730Z/`；真实应用、群、用户标识和凭据未写入本文。

## 2. 主持人路径的已确认缺口

Air 当前 Hermes 适配器保留原始事件，但默认群消息要求 @，默认群策略为 allowlist；日记 profile 未设置对应环境变量覆盖。已安装的事件处理器包含自身机器人加入/移出，未包含真人成员变更及群设置变更处理。

现有桥接把事件送到 `/hermes/messages`，使用旧私聊身份字段及回复、图片扩展，没有把完整的应用、租户、发送者类型和 mention 证据交给独立群入口。旧 Gateway 在讨论路由前执行私聊授权，群消息不会到达 `DiscussionIngress`。

在 Air 生产解释器中，对实际部署代码执行了无存储、无网络的合成调用：本人形状的群请求得到 `group_chat_denied`，讨论 ingress 调用次数为 **0**。这是拒绝分支的代码验证，不是真实飞书消息收发验收。

因此，`hermes-bridge status=installed` 只证明旧桥接安装，不能证明群主持已实现。已有 `normalize_host_group`、`GroupDialogue` 和讨论状态机还需要接上以下链路：

1. 保留 Hermes 唯一 WebSocket，通过受控群分支转发可信原始身份和稳定 `message_id`；普通私聊及严格日记确认继续使用原路径。
2. 增加本机群入口，校验共享密钥、固定主持人应用、租户、本人映射、受管群与实时成员证据；拒绝路径不能回退到通用模型或旧日记入口。
3. 使用持久接收回执和唯一投递方，避免 Hermes 回复与讨论 outbox 重复发送。导师群事件副本不能触发模型。
4. 原日记导师登记为 `receiver=hermes` 的 host，并核验其加入目标群；群身份键使用实际 host 平台与租户，不能使用网页 principal 最初登记的 local scope。

## 3. 最小权限与事件增量

以下是下一步应核对的最小权限组合，**本轮未改动或发布应用**。已有等效权限时保留，不重复申请；主持人已能得到部分群资料，须先核对其已发布 scope，再决定是否增加群资料读取权限。保留既有私聊及发送权限；当前只读验证不需要 `im:chat` 读写大包或通讯录全员权限。

| 应用 | 最小权限能力 | 用途 |
| --- | --- | --- |
| 日记导师主持人 | `im:chat:read` | 完整群资料及群设置更新事件 |
| 日记导师主持人 | `im:chat.members:read` | 真人成员分页、自身在群查询与真人加入、移出、退出事件 |
| 日记导师主持人 | `im:message.group_msg` | 接收未 @ 的普通用户群消息 |
| 四位导师 | `im:chat.members:read` | 各用自己的身份查询是否在群 |
| 五个应用 | `im:chat.members:bot_access` | 各自机器人加入或移出事件 |

四位导师无需读取任意普通群正文，也不需要为了当前 Inspector 读取群资料。主持人的普通群消息权限不包含其他机器人消息，后端仍统一安排发言。[成员查询](https://open.feishu.cn/document/server-docs/group/chat-member/get.md)、[消息事件权限](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/events/receive.md)、[群更新事件](https://open.feishu.cn/document/server-docs/group/chat/events/updated.md)、[机器人入群事件](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/chat-member-bot/events/added.md)

新增事件须同时完成后台订阅、应用发布和接收端处理。只加 scope 不会生成业务处理代码；必须复测原本人可用范围及私聊。

## 4. 权限读取成功后仍需解决的要求

- **当前管理设置**：核验 private、同租户、正常群状态、完整真人和受控机器人集合、管理员集合、邀请/分享、发言限制。`only_owner` 包含管理员；邀请 `only_owner` 与分享 `not_allowed` 是组合限制，不能解释为群主和管理员也无法分享。[群设置](https://open.feishu.cn/document/server-docs/group/chat/update-2.md)
- **新成员历史**：当前公开 GetChat/Create/Update 契约没有提供本产品所需的新人历史可见性读回证明。`join_message_visibility` / `leave_message_visibility` 只是提示消息可见性。原客户端开关观察不能替代新增成员的实际测试，也不能承诺平台绝对不支持该能力。
- **断连连续性**：当前官方长连接实现没有可用于补拉成员历史的游标。重连后成员集合相同，不能证明期间没有加入后退出的人；事件订阅加当前快照也不能单独满足该要求。[官方长连接实现](https://raw.githubusercontent.com/larksuite/oapi-sdk-python/v2_main/lark_oapi/ws/client.py)

代码审查还发现成员接口兼容性问题：Inspector 强制要求 SDK 可选字段 `trigger_security_conf_limit` 为 `False`，而官方标准响应未列该字段。后续应拒绝显式截断，并结合完整分页、唯一 ID、独立人数及前后稳定查询判定，不能把可选字段缺失直接等同截断。另需检查 `chat_mode`、`chat_status` 与 `moderation_permission`。这些是待修改代码，不是本次真实接口已通过的证据。[成员响应契约](https://open.feishu.cn/document/server-docs/group/chat-member/get.md)

## 5. 后续验收顺序

先补并发布上述最小权限，复查完整群资料、本人映射和五个机器人身份；再实现唯一连接上的主持人群桥接及成员失效处理，完成拒绝、重复事件、断连和私聊确认回归。最后用本人真实群消息测试普通补充、明确发起多导师、停止及私聊结果保存，并单独完成历史可见性和手机检查。

缺失的群桥接可以开发；无法获得历史或连续性证明时，需要明确调整产品边界后才能启用相应群路径。不能将人工布尔开关、旧机器人发言记录或合成快照当作生产授权。
