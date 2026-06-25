# MVP 验收记录

## 用户故事 → 自动化验收

| 故事 | 验收要点 | 自动化测试 |
| --- | --- | --- |
| US-01 带来源的日记问答 | 回答列出 `[[riji/...]]` 来源；无证据时明说 | `test_e2e_acceptance.py::test_us01_sourced_qa` |
| US-02 复杂时间性回顾 | 调用时间范围工具，多轮检索 | `test_e2e_acceptance.py::test_us02_timeline_review`、`test_timeline.py` |
| US-03 多个人设 | 独立提示词/会话/记忆；切换不泄露私有记忆 | `test_e2e_acceptance.py::test_us03_*`、`test_persona_context.py`、`test_memory.py` |
| US-04 飞书导师对话与日记记录 | 私聊路由到导师；写入经 patch 预览 + 确认 + 可回链 | `test_e2e_acceptance.py::test_us04_draft_then_confirm_writes`、`test_hermes_*`、`test_drafts_*` |
| 王阳明引用 | 引文可追溯、与日记来源分列、不冒充 | `test_e2e_acceptance.py::test_us05_yangming_citation`、`test_yangming.py` |
| 隐私边界 | private 内容不出云，审计可证 | `test_e2e_acceptance.py::test_private_content_never_egresses` |

运行：`docker run --rm -v $(pwd):/app -w /app python:3.11 bash -c "pip install -e '.[dev]' -q && python -m pytest"`。

## 手工验收（新环境最小对话）

按 [deployment.md](deployment.md) 部署后，在飞书私聊（白名单用户）依次验证：

1. 发普通问题 → 收到带 `[[riji/...]]` 来源的回答。
2. `/导师 直率教练` 切换 → 回复风格切换且会话隔离。
3. `记录…今天的事` → 收到草稿预览；回复「确认保存」→ 收到写入成功与 wikilink；Obsidian 中可见追加。
4. `/导师 王阳明 谈谈知行合一` → 回答区分日记事实 / 可核对引文（带出处）/ 现代阐释。

## 已知缺口与修复项（不静默忽略）

| 项 | 状态 | 跟踪 |
| --- | --- | --- |
| 本地 embedding 语义检索 + 混合排序 | 已实现（issue #17，`RIJI_SEMANTIC_SEARCH` 默认关闭；内置零依赖本地 embedder，可外接更强本地模型） | issue #17 |
| 会话历史注入多轮 loop 上下文（按导师隔离，轮数 12 / 总字数 4000 上限，最旧先裁） | 已实现（issue #28） | issue #28 |
| 网关为单进程锁串行化；多 worker 部署需共享锁/DB 级幂等（事件用 `INSERT OR IGNORE` 已安全，草稿 check-then-act 依赖进程锁） | 待办 | issue #29（部署为单进程可规避） |
| 草稿确认依赖「当前导师」定位会话；草稿与确认之间切换导师会找不到草稿 | 待办 | issue #30（按需加 draft_id 显式确认） |

以上均为显式记录的后续项，非安全静默忽略。隐私核心边界（private 不出云、Key 不外泄、Hermes 无 vault 直读写）已有自动化覆盖。
