# Hermes 飞书接入与导师路由

本文档说明如何把飞书私聊作为 riji-agent 的 MVP 入口，通过 Hermes 的内置飞书接入把消息路由到对应导师。

> 版本假设：Hermes 的飞书接入与 Provider 配置以**当前官方文档**为准。下面给出 riji-agent 侧的稳定契约；Hermes 侧字段名/路径请对照你所用 Hermes 版本的官方说明，不要照搬本文的占位值。

## 架构边界

```
飞书私聊 ──> Hermes（内置飞书接入 + DeepSeek Provider）──HTTP──> riji-agent /hermes/messages
```

- **Hermes 不直接读写日记 vault / SQLite**；它只通过 HTTP 调用 riji-agent 的网关端点。
- riji-agent 持有 DeepSeek API Key、日记 vault 与索引；模型只能调用已注册的检索工具。

## Hermes 侧配置（对照官方文档）

1. **飞书接入**：按当前 Hermes 版本的官方方式接入飞书自建应用，仅启用**私聊**消息事件；订阅消息接收事件，开启事件去重所需的 `event_id` 透传。
2. **DeepSeek Provider**：base url `https://api.deepseek.com`，模型默认 `deepseek-reasoner`（可切 `deepseek-chat`）。API Key 只配置在本地 Hermes/riji-agent 环境，不出云、不进飞书侧。
3. **转发到 riji-agent**：把每条私聊消息 POST 到 riji-agent 的 `/hermes/messages`，携带共享密钥头。

## riji-agent 端点契约

`POST http://127.0.0.1:8765/hermes/messages`

请求头：

```
X-Hermes-Secret: <HERMES_SHARED_SECRET>
```

请求体（JSON）：

| 字段 | 说明 |
| --- | --- |
| `event_id` | 飞书事件唯一 ID，用于幂等去重 |
| `feishu_user_id` | 飞书用户 open_id，用于白名单校验 |
| `chat_id` | 会话 ID |
| `chat_type` | `p2p`（私聊）才被接受；其余视为群聊拒绝 |
| `text` | 用户消息文本 |

响应（JSON）：`{request_id, persona_id, reply, deduplicated}`。

错误：`401`（共享密钥无效）、`403`（非白名单用户或群聊）。

## 导师路由

- `/导师 <名称>`、`/persona <id>`、`/切换 <名称>`：切换当前导师并**持久化**为用户偏好。
- `@<名称> <内容>`：仅本条消息使用该导师，不改变当前偏好。
- 普通消息：沿用用户当前导师（默认 `gentle_reviewer`）。
- 预置导师：温柔回顾者（`gentle_reviewer`）、直率教练（`blunt_coach`）、未来的我（`future_self`）。
- 未识别的导师名返回可用导师列表，不调用模型。

## 草稿确认

- 模型只能**提出**草稿，写入需用户显式确认；确认命令：`确认保存` / `确认写入` / `确认` / `/确认`。
- 默认确认当前导师会话里的待确认草稿。
- **跨导师确认**：若在草稿生成与确认之间切换了导师，普通 `确认保存` 会因会话不同而找不到草稿。此时用「`确认保存 <草稿号>`」按草稿号显式确认——草稿号在预览文本中给出。
- 显式确认仍校验：草稿归属本人（他人草稿一律按「未找到」处理，不泄露其存在）、未过期、单次 token、状态为待确认。

## 安全与幂等

- **白名单 + 私聊**：仅 `RIJI_ALLOWED_FEISHU_USER_IDS` 中的用户在私聊里可用日记能力；群聊一律拒绝。
- **共享密钥**：`X-Hermes-Secret` 用常量时间比较校验，证明调用方是本地 Hermes。
- **幂等**：相同 `event_id` 只处理一次，重复事件返回首次的回复（`deduplicated=true`），不重复调用模型或写入。

## 故障诊断

| 现象 | 排查 |
| --- | --- |
| 飞书无回复 | 确认 Hermes 私聊事件已订阅、`event_id` 已透传；查 riji-agent 是否收到 POST。 |
| 全部 `401` | `X-Hermes-Secret` 与 riji-agent 的 `HERMES_SHARED_SECRET` 不一致。 |
| 授权用户却 `403` | 检查 `feishu_user_id` 是否在 `RIJI_ALLOWED_FEISHU_USER_IDS`；确认 `chat_type=p2p`。 |
| 重复回复 | 确认 Hermes 透传了稳定的 `event_id`；不稳定的 ID 会绕过幂等。 |
| 启动即退出 | 配置无效（路径/必填项），见 README 的配置与安全一节。 |
