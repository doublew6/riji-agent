# 仅使用本群内容的导师圆桌

状态：实现与本地合成契约测试已完成；本文不代表生产飞书群验收。原私人来源圆桌门槛保持，`history_restricted`、`continuity_verified` 按 Inspector 的真实结果保留，不改为已验证。

## 来源与身份

`Conversation.source_scope` 新增 `personal | group_only`，旧记录默认 `personal`。只有服务端接管流程能够创建 `group_only`，同时固定原主持人 `source_application_id`、平台、租户和 `room_id`。网页请求和 Command 不能修改这些字段。

本模式只使用接管以后在该群接收到的本人原话，以及由这些内容产生的本问题摘要和 AI 发言。用户原话仍是自述，计划不代表已执行，AI 结论不升级为用户事实。日记、长期记忆、其他私聊、其他群及跨问题转交均不进入本模式。`policy.freeze/background`、MemorySources 和 TransferSources 在检索之前返回空来源；非空 `source_ids`、外来依赖或非本群 Artifact 会阻止生成。模型仍使用固定角色、结构化上下文和空工具集合。

原日记导师继续由 Hermes 持有唯一 WebSocket。主持人身份只使用原应用 allowlist 和已有 legacy owner 映射；群事件不会登记新 owner。群输入、模型请求以及每次发送前，核验当前唯一 canonical owner、原主持人及四个固定导师机器人、完整成员列表、同租户私人群、管理约束和发言许可。

## 接管与迁移接口

以下控制接口使用已有 owner Bearer token，令牌不放入 URL。示例中的值为占位符。

`POST /api/mentors/v1/host-groups/adoptions`

```json
{"expected_chat_id":"<verified-chat-id>"}
```

当前群通过真实 Inspector 后，服务端登记十分钟待接管请求，不接收正文。本人在登记之后发送的一条真实群消息提供新问题正文；普通内容启动一次主持人回应，带题设的明确全桌请求可直接启动参考或辩论。没有题设的纯启动口令不会创建空问题。

如果该群已经绑定旧个人来源问题，必须明确指定当前问题：

```json
{"expected_chat_id":"<verified-chat-id>","replace_conversation_id":"<existing-conversation-id>"}
```

旧问题必须与此 host、tenant、chat 的当前映射一致且属于本人。迁移只归档旧 `personal` 问题、关闭旧 grant、取消未发送输出并使在途模型结果失效，保留原 scope 和全部档案。新群消息到达后创建全新 `group_only` 问题并原子切换群映射，不复制旧问题、来源、消息、摘要或 AI 结果。发送中、结果未知或待协调失败的旧投递会阻止迁移；已删除或 sealed 的旧问题不能通过该入口恢复。

`POST /api/mentors/v1/host-groups/<conversation-id>/revalidate` 重新读取当前群证据。通过后只恢复为 `ready + stopped`，不自动开模型。后续由本人在群中继续。

## 输入、讨论与保存

- 普通群消息由主持人回应；明确“请四位分别给我参考”“请四位重新讨论”才开启全桌。
- 全桌期间的新补充或点名追问暂停本场，由指定角色回应一次，再等待本人显式 `/继续 <conversation-id>`，沿用原场额度。
- 群内和 owner 控制接口均保留停止；网页和私聊不能给本群问题注入正文。网页仍可查看、导出、停止、删除或归档。
- 保存结果沿用本人日记导师私聊的预览与确认；群内不创建或提交日记草稿。导出保留来源范围，剥离群定位字段；恢复包只用于只读档案，不能重新激活。

## 成员事件与剩余边界

`POST /api/mentors/v1/host-lifecycle` 接受 `{ "raw_event": { ... } }`，沿用 `X-Hermes-Secret`。要求 schema `2.0`、原 host 的 app/tenant、有效 `event_id`、二十四小时内的毫秒 `header.create_time`，仅接受：

- `im.chat.member.user.added_v1`
- `im.chat.member.user.deleted_v1`
- `im.chat.member.user.withdrawn_v1`
- `im.chat.updated_v1`
- `im.chat.member.bot.deleted_v1`

事件仅按真实 `event.chat_id` 失效同 host/tenant 的受管问题，不依据 operator 推断用户身份，不读取正文或私人来源，不调用模型和不回群消息。事件 ID 持久去重；去重记录达到容量上限时仍暂停已核实的目标群，只省略新记录。生命周期失效保留用户已停止或归档的状态。原始事件不保存。HTTP 群入口仍只返回接收状态，由后端唯一 outbox 投递，Hermes 不二次回复。

当前成员检查与已收到的生命周期通知不能证明两次检查之间从未发生过短暂成员变化，也不能证明通知没有遗漏；因此历史访问限制和事件连续性仍未验证。本模式没有借这些能力未通过而放开个人来源。检查失败或收到成员变更后暂停，须本人重新核验。平台已有旧群消息不由本实现删除。
