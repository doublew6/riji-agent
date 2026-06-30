# riji-agent 项目协作指南

本文件是 Claude 与 Codex 的共享、唯一规则源。开始任何任务前，先阅读：

1. `docs/PRD.md`
2. `docs/architecture/mvp-architecture.md`
3. 当前 GitHub Issue 的完整描述和依赖

若这些文件冲突，以架构文档和最新已确认的 GitHub Issue 为准；不确定时停下来说明冲突，不自行扩大范围。

## 项目目标

构建一个通过 Hermes 内置飞书接入交互的日记 Agent：DeepSeek 负责多轮推理，`riji-agent` 负责本地日记检索、记忆、草稿确认、模板追加与审计。

## 不可违反的边界

- `riji-agent` 是唯一可访问日记 vault、SQLite、草稿和审计数据的组件；Hermes 不得直接读写 vault。
- 日记源路径由环境变量配置；只读检索不得修改源 Markdown。
- 不上传完整 vault、原始 Markdown 文件或本地 SQLite 到任何云服务。
- API Key、飞书凭据、真实日记、数据库、审计日志和 `.env` 不得提交到 Git。
- 模型只能通过已注册的受限工具访问日记，不能获得任意文件系统、Shell 或网络权限。
- MVP 不使用 `private: true` 内容排除；仍必须限制每次出云的片段数量与长度，并审计来源 ID。

## 飞书、导师与记忆

- MVP 使用一个 `Riji` 飞书 Bot；用户在同一 Bot 内切换导师。
- 日记、已确认长期记忆和稳定偏好共享；不同导师的聊天历史、临时观察和未确认候选记忆必须隔离。
- 会话身份必须包含 `feishu_user_id`、`persona_id`、`feishu_chat_id` 和 `request_id`。
- 只允许白名单用户的飞书私聊使用日记能力；群聊不得读取或写入日记。

## 日记写入规则

- 写入始终遵循：`draft_daily_entry` → 飞书 patch/diff 预览 → 用户确认 → `commit_draft`。
- 确认 token 绑定用户、会话和草稿，30 分钟失效且只能使用一次。
- 日记必须按 `riji/templates/daily.md` 的标题锚点追加，不可覆盖或重写既有内容。
- 无法可靠归类的内容追加到 `🧠 Notes`；无法找到目标模板区块时拒绝提交并保留草稿。
- 写入必须原子化；成功后再触发增量索引并记录审计事件。

## 开发方式

- 每项实现只处理一个领取的 GitHub Issue；不要顺手重构不相关模块。
- 开始前检查 Issue 的依赖；依赖未完成时只做不依赖它的工作，或明确阻塞原因。
- 尽可能添加单元测试；涉及权限、写入、幂等或模型工具调用时必须测试失败路径。
- 不要伪造模型、飞书或 Hermes 接口行为。对版本相关配置，引用官方当前文档并在 PR 说明假设。
- 变更配置、工具 Schema 或数据模型时，同步更新相关文档与测试。

## 通用编码与语言规范

适用于本项目所有贡献者（Claude / Codex 及人工）。本项目独立于其他代码库，不引入任何外部项目的领域规范。

**语言**

- 沟通用中文；代码、注释、提交信息、命名一律英文。
- 专有名词保持英文原文（FastAPI、Feishu、Hermes、DeepSeek、webhook 等）。

**编码**

- 遵循 PEP8 与 DRY / KISS / YAGNI / SOLID 原则。
- 函数名小写下划线、动词开头（`load_settings`、`ensure_data_directory`）；类名 CamelCase 名词（`Settings`、`ConfigurationError`）。
- 单个函数不超过 50 行、参数不超过 5 个；超出时用 dataclass 或 pydantic model 封装参数，过长逻辑抽到独立模块或 `utils`。
- 补齐类型注解；路径统一用 `pathlib.Path`，不用裸字符串拼接。

**日志与错误安全**

- 日志和对外错误信息中不得出现 secrets、API Key、飞书凭据、日记原文或绝对路径；对外错误参考 `ConfigurationError` 的处理方式。

**测试**

- 本项目要求 Python ≥3.9。宿主机 Python 为 3.6.8，禁止用宿主机解释器直接跑 pytest。
- 使用项目自带环境运行：`uv sync --extra dev && uv run pytest`。
- 无 uv 时用 Docker：
  ```bash
  docker run --rm -v $(pwd):/app -w /app python:3.11 \
    bash -c "pip install -e '.[dev]' -q && python -m pytest"
  ```

## Git 与多 agent 协作

- 一个 Issue 一个分支，建议命名 `issue/<number>-<short-slug>`。
- 提交应小而聚焦，提交信息写清 Issue 范围；不得混入其他 agent 的改动。
- 提交前运行最相关的格式化、静态检查和测试，并在 PR/Issue 中报告结果。
- 发现工作区已有不相关变更时，保留它们，不覆盖、不重置、不批量暂存。
- 合并前确保 PR 链接对应 Issue，并保留必要的架构或安全决策记录。
