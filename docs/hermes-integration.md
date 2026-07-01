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

### Feishu Bot 头像

仓库内提供默认头像资产：`assets/integrations/feishu/riji-bot-avatar.png`。它是 512x512 PNG，适合作为 Feishu/Lark 自建应用的 Bot 头像上传。

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

响应（JSON）：`{request_id, persona_id, reply, deduplicated}`。当启用飞书语音回复且本地 TTS 成功时，还会包含可选字段：

```json
{
  "audio": {
    "path": "/absolute/path/to/reply.opus",
    "mime_type": "audio/ogg"
  }
}
```

`audio.path` 是本机临时/运行目录中的音频文件路径。bridge 会把它转换成 Hermes 的 `MEDIA:` 附件指令，由 Hermes/Feishu adapter 上传发送；普通 HTTP 调用方可以忽略该字段。

错误：`401`（共享密钥无效）、`403`（非白名单用户或群聊）。

## 飞书 → Hermes → bridge → riji-agent 配置步骤

Hermes 的内置飞书接入默认会让 Hermes 自己回复飞书消息，这会绕开 riji-agent 的本地日记隐私边界。为此本仓库提供一个**很薄的 bridge**：`src/riji_agent/integrations/hermes_bridge.py`。它在 Hermes 进程一侧运行，把飞书消息**原样转发**给 riji-agent，取回 `reply` 文本和可选 `audio` 元数据，由 Hermes 发回飞书。

bridge 的边界：

- 不解析、不改写文本（`/导师 王阳明` 透传，persona 切换交给 riji-agent）；
- 不自己生成事件 ID（透传飞书 `event_id`，幂等去重仍在 riji-agent 生效）；
- 不自行放行群聊或非白名单用户（透传 `chat_type`/`feishu_user_id`，由 riji-agent 的 403 拦截）；
- **无任何 vault / SQLite / 索引 / DeepSeek key 访问**——只转发消息与回复；
- 语音回复开启时，只把 riji-agent 已生成的本地音频路径转换为 Hermes 媒体附件指令；
- 共享密钥只放在 `X-Hermes-Secret` 头里，绝不出现在日志、异常或回复中。

### bridge 侧环境变量

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `HERMES_SHARED_SECRET` | 是 | 与 riji-agent 的 `HERMES_SHARED_SECRET` 一致；常量时间比较校验。 |
| `RIJI_AGENT_URL` | 否 | bridge POST 的目标，默认 `http://127.0.0.1:8765/hermes/messages`。 |
| `RIJI_AGENT_TIMEOUT_SECONDS` | 否 | bridge 等待 riji-agent 回复的秒数，默认 `240`。复杂日记问题可能需要更久。 |

### 可选：飞书语音回复

默认只发送文字。若想在飞书里同时收到文字和语音附件，可在 riji-agent 的本地 `.env` 中启用：

```bash
RIJI_FEISHU_VOICE_REPLY_MODE=text_and_voice
RIJI_TTS_PROVIDER=macos_say
# RIJI_TTS_VOICE=Tingting
# RIJI_TTS_MAX_CHARS=1200
```

`macos_say` 使用 macOS 本机 `say` 命令，不调用云端 TTS；如果本机有 `ffmpeg`，会把临时 `.m4a` 转成飞书更稳定支持的 `.opus` 语音文件。生成的音频保存在 `RIJI_DATA_DIR/voice`（或 `RIJI_TTS_OUTPUT_DIR`）下，不写入日记 vault。若 TTS 不可用或生成失败，bridge 仍会发送原文字回复。

### Hermes 飞书侧官方变量（对照，不在本仓库管理）

以下变量属于 Hermes 的飞书 Provider，按你所用 Hermes 版本官方文档配置，仅供对照：

| 变量 | 说明 |
| --- | --- |
| `FEISHU_APP_ID` | 飞书自建应用 App ID。 |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret。 |
| `FEISHU_DOMAIN` | 飞书开放平台域名（国内 / Lark 国际站不同）。 |
| `FEISHU_CONNECTION_MODE=websocket` | 长连接接收事件，无需公网回调地址。 |
| `FEISHU_ALLOWED_USERS` | Hermes 侧的飞书用户过滤（可选）；真正的日记授权仍以 riji-agent 的 `RIJI_ALLOWED_FEISHU_USER_IDS` 为准。 |

### 正式安装步骤

1. 在 Hermes 一侧按官方文档接入飞书自建应用，仅启用**私聊**消息接收事件，确保事件携带稳定的 `event_id`。
2. 在 Hermes 进程环境里设置 `HERMES_SHARED_SECRET`（与 riji-agent 一致），需要时设置 `RIJI_AGENT_URL`。
3. 运行 installer，把 bridge hook 安装进当前 Hermes gateway：

   ```bash
   uv run riji-agent hermes-bridge install
   uv run riji-agent hermes-bridge status
   ```

   默认目标是：

   ```text
   ~/.hermes/hermes-agent/gateway/run.py
   ```

   如果 Hermes 安装路径不同，显式传入：

   ```bash
   uv run riji-agent hermes-bridge install --gateway-run /path/to/hermes-agent/gateway/run.py
   ```

4. 重启 Hermes gateway，让 hook 生效。
5. **不要**把 riji-agent 端口暴露到公网；bridge 与 riji-agent 在同一主机经 `127.0.0.1` 通信。Hermes 不直读 vault / SQLite，只能经此 HTTP 边界访问日记能力。

installer 的行为：

- 幂等：重复运行不会重复插入 hook；
- 可更新：若存在旧版本本机 patch，会替换成受管理的 marker block；
- 会在修改前创建 `run.py.riji-agent.bak` 备份；
- 可移除：`uv run riji-agent hermes-bridge uninstall`；
- 如果 Hermes 升级覆盖了 gateway 文件，再运行一次 `install` 即可恢复。

> bridge 在非 2xx（含 401/403）或网络异常时返回一段安全失败文案，不抛异常、不回传任何内部细节或密钥。授权失败（如群聊、非白名单）的语义由 riji-agent 决定，bridge 只如实转发并降级展示。

## 导师路由

- `/导师 <名称>`、`/persona <id>`、`/切换 <名称>`：切换当前导师并**持久化**为用户偏好。
- `@<名称> <内容>`：仅本条消息使用该导师，不改变当前偏好。
- 普通消息：沿用用户当前导师（默认 `gentle_reviewer`）。
- 预置导师：温柔回顾者（`gentle_reviewer`）、直率教练（`blunt_coach`）、未来的我（`future_self`）。
- 未识别的导师名返回可用导师列表，不调用模型。

任意导师窗口里都可以询问导师列表和切换方法，例如“我有哪些导师可以选择？”、
“怎么切换导师？”、“导师列表”或单独发送 `/导师`。这类问题由 riji-agent
固定回复，不调用模型、不检索日记；回复会列出全部导师、当前导师、默认切换命令
和单条消息临时指定导师的方法。

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
| Hermes 更新后又绕开 riji-agent | 运行 `uv run riji-agent hermes-bridge status`；若不是 `installed`，重新执行 `install` 并重启 Hermes。 |
| 启动即退出 | 配置无效（路径/必填项），见 README 的配置与安全一节。 |
