# 数据出云边界

本文件明确哪些数据离开本机、哪些绝不离开。

## 绝不离开本机

- 完整日记 vault、原始 Markdown 文件、本地 SQLite（索引/记忆/草稿/审计）。
- 任何被标记 `private: true` 的日记内容。
- API Key、飞书凭据、Hermes 共享密钥（仅存在于本地 `.env` / 环境变量；不进日志、不进异常消息、不进审计）。
- 文件系统路径与 vault 目录结构（模型只见 `source_id`，见不到路径）。

## 会离开本机（最小化）

| 去向 | 内容 | 约束 |
| --- | --- | --- |
| 飞书 | 用户消息与回复 | 仅白名单用户私聊；群聊禁用日记能力 |
| DeepSeek | 系统提示、用户问题、**检索工具返回的最小片段** | 片段条数/单段长度/总字数有上限；**永不含 private 内容**；按 `source_id` 引用 |

## 强制点（代码层）

- 检索工具一律 `include_private=False`：private 日记不进搜索结果，`read_note` 对 private 二次拦截。
- `search_journal`/`timeline` 等对结果条数、单片段长度、总字数设上限。
- `read_note` 需先有同会话检索证据；工具只接受 `source_id`，无任意路径读取。
- 王阳明思想资料库与日记**分库分流**：思想引文与日记事实在工具结果中分列，互不串源。
- DeepSeek Provider 仅把 Key 放进 Authorization 头；HTTP 错误消息不含 Key。
- 审计只记录元数据：工具名、来源 `source_id`、结果状态、时间——不记内容、不记 Key。

## 可证明性

`tests/test_e2e_acceptance.py::test_private_content_never_egresses` 端到端断言：标记 private 的日记其 `source_id` 不出现在审计来源中，其正文不出现在发往模型的任何消息里，且对其 `read_note` 被拦截。
