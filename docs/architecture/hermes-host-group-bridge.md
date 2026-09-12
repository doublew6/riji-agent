# 原日记导师的 Hermes 群桥接

更新：2026-09-11。承接[常驻导师技术设计](persistent-mentor-roundtable.md)。本文记录本次新增代码和合成验证，**不代表 Air 已部署该桥接或生产群验收通过**。群历史权限与事件连续性门槛未放宽。

## 1. 接收权与身份

原日记导师继续由 Hermes 的唯一 WebSocket 接收。独立群 hook 使用 Hermes 保留的 `event.raw_message`，通过 `lark_oapi.JSON.marshal` 保留完整 SDK 对象；不从显示名称或客户端参数推断身份。它只把事件送到本机后端，群消息不进入旧日记 Gateway 或 Hermes 通用模型。既有私聊 bridge 保留，安装器使用独立群标记，不覆盖已经安装的私聊扩展。

后端固定使用配置中的 `receiver="hermes"`、`persona_id="host"` 应用，并要求其外部应用 ID 与原日记导师配置一致。该应用没有独立导师 transport token，也不能启动 dedicated receiver。

启动时只把原日记导师白名单里的 open_id，与导师库既有 `legacy_owner` 索引对应的 principal 建立 host-scoped 映射。不会创建新 owner，不把 app-scoped open_id 当成跨应用通用身份；已存在的冲突映射会使启动失败。未找到既有 owner 时保持未绑定，不能从群消息里的 `subject` 或 `user_id` 自行登记。Inspector 通过只读 resolver 查询这份映射。

## 2. 配置

以下仅展示合并到**现有配置**的主持人条目。四位导师、原 `users`、已有凭据文件配置和 owner 值继续保留，不按示例重建。

```json
{
  "feishu_receiver_ownership": "dedicated_apps",
  "applications": [
    {
      "external_id": "<ORIGINAL_FEISHU_APP_ID>",
      "platform": "feishu",
      "tenant": "<VERIFIED_TENANT_KEY>",
      "persona_id": "host",
      "receiver": "hermes"
    }
  ]
}
```

主持人复用原 `Settings.feishu_app_secret`，与已经匹配的 `Settings.feishu_app_id` 配对；即使秘密只存在生产 runtime `.env`、未写入进程 `os.environ`，仍使用 Settings 已解析的值。无需配置 host 的 `secret_env` 或把原 secret 复制到导师凭据文件；缺失原秘密时启动失败。四位独立导师继续使用各自秘密引用。沿用原 `HERMES_SHARED_SECRET` 和用户白名单，不生成另一份群认证凭据。配置文件保持私有权限，部署不得覆盖 Air `.env`。

Hermes 的 `RIJI_AGENT_URL` 必须仍为 `http://127.0.0.1:8765/hermes/messages`。群 hook 验证 scheme、主机、固定端口和路径，并拒绝用户信息、查询串和 fragment，然后使用固定群端点。HTTP 客户端禁用环境代理和重定向，不能把共享秘密转发到其他主机。

飞书能否把未 @ 主持人的普通群消息交给 Hermes，还取决于已获批的应用权限、发布版本以及 Hermes 的群策略。代码桥接不修改这些平台权限，不用扩大群白名单来替代授权。

## 3. 群入站接口

`POST /api/mentors/v1/host-events` 使用 `X-Hermes-Secret`，请求为：

```json
{"raw_event": {"schema": "2.0", "header": {}, "event": {}}}
```

上例只表示容器结构，空 header/event 会被拒绝。真实请求必须原样包含 SDK 信息：

- `schema="2.0"`、`header.event_type="im.message.receive_v1"`；header 的 app_id、tenant_key 匹配固定主持人应用。
- sender 为同租户真人，open_id 属于原白名单且映射到既有 owner；机器人、跨租户和用 subject 冒充的发送者被拒绝。
- 群聊文字消息具有非空 message_id、chat_id、event_id；`message.create_time` 为毫秒字符串，距当前不超过 24 小时且不超前超过 5 分钟。
- mention 保留有界的 ID、名称提示，用于澄清或点名，不参与身份认证。缺失或不合法字段失败关闭。

普通消息还必须命中该 host scope 下已经管理的问题群，并通过现有 ingress 的本人、成员和 audience 校验。只有明确的参考/辩论请求才开全桌；普通补充沿用主持人单次回应。群权限不完整时不调用模型，不读取未授权来源。

已处理的事件结果使用 `accepted`、`pending` 或 `rejected`，附带安全代码；已记录事件返回 receipt_id 和 duplicate，并指定 `delivery="backend_only"`、`hermes_reply=false`，不向 Hermes 返回正文。认证失败为 HTTP 401，外层事件拒绝为 409；校验错误不回显请求，处理结果不明保持 pending。Hermes 对非成功响应同样不发送回复、不回退旧入口。

入站以固定 host、chat_id、message_id 去重，event_id 只作递送标识。桥接回执只存指纹及状态；普通业务复用 ingress 的持久幂等。执行中断后的 pending 不盲目重放。终态回执最多保留约 2,000 条、7 天；待核对回执达到 200 条时停止接受新事件。24 小时消息时间窗及既有业务回执共同防止旧消息在群状态变化后重放。

停止/删除仍先验证真实事件、本人和受管群，再直接复用已有业务入站回执，不新增紧急队列，也不等待普通桥接的全局锁。两类桥接队列已满时仍可停止；控制队列满时省略其群 ACK，不扩大队列。最终停止状态以问题档案为准。

## 4. 控制回复与模型结果

模型结果继续使用原讨论 outbox。澄清、停止提示、保存讨论的私聊接收指引等控制回复进入独立持久记录，由同一后端 dispatcher 发送，Hermes 不再发送第二份。

控制投递前重新校验原白名单、既有 owner、问题版本和取消版本，并核对当前群的完整成员、历史、管理和连续性证据与原 grant 一致。问题删除、版本变化或权限失效时取消投递并清空正文。控制提示的校验失败不改写已经停止的问题状态。

发送前持久记录 sending；成功后清空正文，缺少平台消息回执或传输异常记为 unknown，不自动重试。重启把遗留 sending 转为 unknown，尚未尝试的 pending 可继续；过期控制提示取消。待发送/正在发送上限为 200 条，达到上限后在业务操作前拒绝新普通输入，仍允许只收件诊断。终态控制记录独立按约 2,000 条、7 天清理，不依赖入站回执仍然存在。入站重送不会重复追加控制回复。

## 5. 只验证收件的诊断

以下接口使用本人的 Review token，与 Hermes secret、导师 transport token 分离：

| 接口 | 用途 |
| --- | --- |
| `POST /api/mentors/v1/host-diagnostics` | 请求体 `{"expected_chat_id":"<VERIFIED_CHAT_ID>"}`，创建限定群的收件挑战 |
| `GET /api/mentors/v1/host-diagnostics/{id}` | 仅本人查看 pending、received 或 expired 及时间 |
| `GET /api/mentors/v1/host-status` | 查看本人的接收回执和控制 outbox 状态计数 |

创建挑战先读取指定群的真实证据：私人群、真人分页完整、仅当前 owner、host 在群、已知机器人数量匹配、群设置读取期间稳定。未知、不可读取或当前成员不明确的群不能创建挑战。此检查不把尚未证明的历史权限或事件连续性设为已通过。

挑战返回 `/主持接入检查 <一次性令牌>`，绑定 owner、host 和指定 chat_id，10 分钟失效；新挑战使旧待领取挑战失效。数据库只保存令牌哈希。本人在指定群发送后，后端只保存接收时间与消息标识哈希，不调用 ingress、来源或模型，不创建日记草稿，也不发送群回复。状态 `received` 仅证明这一条消息通过接收链路，不能证明讨论功能已开放。

诊断命令出现在引语中，或已签发的令牌被单独粘贴、混入普通消息时，也不会进入模型背景。其他用户、其他群、过期挑战及新 message_id 对同一已使用令牌的重用均被拒绝；相同 message_id 的递送重试复用回执。

## 6. 验证与生产步骤

本地相关测试覆盖真实业务服务与合成 SDK 事件、原私聊回归、身份和字段拒绝、重复/冲突、诊断隔离与过期、控制 outbox 撤权/中断/未知结果、队列满时的紧急停止，以及秘密仅从 `Settings(_env_file=...)` 读取的启动方式。这些测试不使用真实飞书消息或私人日记；最终全量与远端结果单独记录。

生产操作依次为：核验 Air、备份并同步已验证源码；使用 Air runtime 完成全量测试；测试通过后合并原 host 配置和既有 owner 映射；执行 doctor、重启主服务、通过安装器更新独立群 hook，并有序重启唯一 Hermes 接收器；本人创建指定群的诊断挑战，在飞书发送真实指令后从本人接口核对状态；最后复测原私聊日记预览与确认链路。每一步只按已获授权的应用权限执行，不能用模拟 raw_event 代替飞书接收证据。

当前生产圆桌创建仍受服务和渠道门禁限制，history/continuity 未证明；新增诊断或群回调代码不会解除该限制。群成员生命周期事件的连续接收、断连恢复和失效处理还需单独验证。只有这些门槛满足后，才能宣称普通群输入、多导师讨论和私聊保存完整交付。
