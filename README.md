# riji-agent

[![test](https://github.com/doublew6/riji-agent/actions/workflows/test.yml/badge.svg)](https://github.com/doublew6/riji-agent/actions/workflows/test.yml)

[English](README.en.md)

让日记不只是“写下来”，而是变成一个能陪你长期回看、复盘和行动的私人
AI 系统。

`riji-agent` 是一个本地优先的日记智能网关，面向 Obsidian 风格的
Markdown 日记库。它把日记 vault、本地索引、草稿、审计记录和写入权限
保留在用户自己的机器上，只向外部 Agent 或模型运行时暴露受限、可审计的
工具。

## 使命

这个项目的出发点很简单：用 AI 降低高质量日记和复盘的门槛，让普通人也能
低成本复刻历史人物长期自省的方法，同时把真正私密的日记控制权留在自己手里。

曾国藩用日记和“修身十二条”做长期自我监督，核心不是文采，而是“每天记录、
每天反省、每天改一点点”。更完整的复盘系统还会把每日记录延伸成周记、月记、
年记，并围绕工作、关系、健康、情绪、原则持续整理。

这些方法放到今天依然成立。不同的是，AI 可以让这套过程更稳定、更结构化、
更容易坚持：

- 把一句飞书消息整理成符合模板的日记草稿；
- 从每日记录里生成周复盘、月复盘和阶段主题；
- 帮你找回“这件事为什么会变成现在这样”的时间线证据；
- 提取行动项、反思点、情绪线索和反复出现的问题；
- 用不同导师视角陪你回看，而不是只给一段泛泛总结。

但这个项目有一个明确前提：**AI 只是辅助工具，个人成长真正关键的仍然是
持续反思与行动。** 日记不是喂给模型的素材库，而是一个人和自己长期对话的
地方。`riji-agent` 要做的是让 AI 更好地进入这场对话，同时不越界。

## 愿景

我们想要的不是一个“更会总结的聊天机器人”，而是一套 local-first 的个人成长
基础设施：

- 你的 Markdown 日记仍然是唯一真源，可以继续放在 Obsidian 或自己喜欢的笔记系统里；
- AI 可以通过受限工具理解过去，却不能随意读取完整 vault；
- 每一次写入都先变成草稿和 diff，确认后才真正落到文件；
- 日记模板、skills、导师人设和自动化工作流可以持续进化，但敏感数据边界始终稳定；
- 同一套日记事实可以支持温柔回顾、直率教练、未来的我、王阳明导师等不同反思方式。

`riji-agent` 就是这套系统里的本地网关。模板和 skill 仓库负责定义如何写日记、
做复盘、生成总结；本项目负责决定模型能看到什么、能调用哪些工具、以及什么时候
真的允许改动 Markdown 文件。

默认开箱栈是 **飞书 + Hermes + DeepSeek**：

- 飞书是默认的私聊入口；
- Hermes 是默认的 Agent 运行时和消息路由；
- DeepSeek 是默认的 OpenAI-compatible 推理模型 provider。

这套栈是目前最快跑通的路径，但不是唯一架构。项目的核心是一个本地日记
Core，IM、Agent runtime、模型 provider 都通过小型 registry 接入，可以按
配置替换。除 DeepSeek 外，项目也提供了通用 OpenAI-compatible adapter
作为示例。模块边界见 [docs/architecture/modules.md](docs/architecture/modules.md)。

`riji-agent` 也开始内置第一个日记能力包：`personal-growth`。它把
`doublew6/whit-riji-skills` 中的日记模板、复盘技能，以及
`doublew6/codex-automations` 中和 riji 相关的自动化工作流，收敛成一个
产品化边界。pack 当前只是 capability metadata：任何日记写入仍必须经过
draft preview 或受控 writer 边界，自动化也不得上传 complete vault、原始
Markdown 文件、SQLite 数据库、API keys 或 webhook URL。详见
[docs/architecture/packs.md](docs/architecture/packs.md)。

## 核心能力

`riji-agent` 给个人日记智能提供本地边界，也把日记复盘体验产品化：

- **自然记录**：在飞书里随手说一句“帮我记一下”，系统生成结构化日记草稿；
- **定期复盘**：从每日记录整理周记、月记、阶段主题和行动项；
- **带证据回看**：按日期、标签、关键词和语义检索旧日记，回答时引用 `[[riji/...]]` 来源；
- **多导师对话**：同一份日记事实可以被不同人设读取，但导师会话和临时观察相互隔离；
- **安全写入**：任何 Markdown 改动都必须经过 draft preview、用户确认和原子追加；
- **本地边界**：读取已有 Markdown 日记库，不复制 vault，不上传 complete vault、SQLite 或 API keys；
- **可扩展能力包**：把模板、skills 和自动化沉淀为 `personal-growth` 等 capability metadata。

它不是 prompt 集合，也不是模板 registry。日记 skill 层可以决定如何生成
每日日记、周复盘、月复盘、旅行记录或反思总结；`riji-agent` 提供这些
skills 触碰真实日记时需要的本地、安全、可审计执行边界。

## 设计理念

- **日记是主权数据**：原始 Markdown、索引、审计和草稿默认都在本机。
- **模型只看必要片段**：外部模型通过受限工具拿 bounded journal snippets，不拿完整文件。
- **写入必须可反悔**：先草稿、再预览、后确认；不能让 Agent 悄悄改日记。
- **复盘要有来源**：回答尽量回链到日记来源，区分事实、推断和证据不足。
- **工具服务于行动**：总结不是终点，真正重要的是看见模式、做出选择、推进下一步。
- **能力可以迁移**：默认是飞书 + Hermes + DeepSeek，但 IM、Agent runtime 和模型 provider 都应可替换。

## 典型工作流

一次完整的个人成长循环可以是：

1. 捕捉每日记录或聊天消息；
2. Agent 只检索最小必要的相关日记片段；
3. 带来源生成草稿、复盘或回答；
4. 在飞书里展示拟写入内容；
5. 用户明确确认后才写入 Markdown。

典型对话流程：

```text
用户：帮我在日记里记录一下：今天完成了开源前的隐私检查。
导师：起草好了，预览如下……回复「确认保存」即写入。
用户：确认保存
导师：已保存到 [[riji/daily/2026-06-30]]。
```

## 隐私模型

这不是“零出云”系统，而是一个本地控制、受限出云的设计。连接真实日记前，
请先阅读 [docs/privacy.md](docs/privacy.md) 和 [SECURITY.md](SECURITY.md)。

riji-agent 不会发送：

- 完整 vault；
- 原始 Markdown 文件；
- 本地 SQLite 数据库；
- API key、飞书凭证或 Hermes shared secret；
- 本地文件路径或 vault 目录结构；
- 标记为 `private: true` 的笔记。

启用默认栈时，可能离开本机的内容包括：

- 飞书/Lark 会收到用户发给机器人的消息和机器人回复；
- DeepSeek 会收到 system prompt、用户问题，以及本地工具返回的受限日记片段；
- Hermes 会收到路由元数据和本地 gateway 响应，但不应直接读取 vault 或 SQLite。

## 快速开始

要求：Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

先运行虚构 demo vault。它不会读取 `.env`、真实日记或真实 API key：

```bash
uv run riji-agent demo init --target /tmp/riji-demo-vault
uv run riji-agent chat --demo --question "launch planning"
```

demo 回答应该包含 `[[riji/...]]` 来源，并且不会泄漏示例中的
`private: true` 笔记。

接入自己的日记和默认栈：

```bash
uv run riji-agent init --preset feishu-hermes-deepseek
# 编辑 .env，填入日记路径、DeepSeek API key、飞书用户 allowlist。
uv run riji-agent doctor
uv sync --extra dev
uv run riji-agent index
# 在接飞书前，先验证本地模型 key + 日记检索链路：
uv run riji-agent chat --question "本周关于发布我都记了什么？"
uv run riji-agent
```

`riji-agent chat --question "..."` 会在 loopback 上运行真实 Agent loop 和
配置的模型 provider，不依赖飞书或 Hermes。建议先用它确认本地链路可用，
再接 IM。

安装为后台用户服务：

```bash
uv run riji-agent service install
uv run riji-agent service start
uv run riji-agent service status
```

macOS 使用 launchd，Linux 使用 systemd --user，Windows 使用 Task
Scheduler；`--target` 默认是 `auto`。机器睡眠或用户登出时，飞书机器人无法
回复；唤醒或登录后服务管理器会恢复本地服务。详见
[docs/deployment.md](docs/deployment.md#后台常驻服务macos--linux--windows)。

打开 `http://127.0.0.1:8765/healthz`，期望返回：

```json
{"service":"riji-agent","status":"ok"}
```

`RIJI_DATA_DIR` 默认是 `~/.local/share/riji-agent`，用于保存本地 SQLite
状态，不在代码仓库内。

## 默认栈：飞书 + Hermes + DeepSeek

飞书私聊通过 Hermes 侧的薄 bridge 到达 riji-agent：

```text
飞书私聊 -> Hermes -> riji-agent /hermes/messages -> 本地工具 -> DeepSeek
```

bridge 只把消息文本和身份元数据通过 loopback HTTP 转发给 riji-agent，不读取
日记 vault、SQLite、本地索引或模型 key。riji-agent 内部会把飞书 payload
归一化为中立 IM message contract，后续其它 IM adapter 可以复用同一条
gateway 路径。

```bash
uv run riji-agent hermes-bridge install
uv run riji-agent hermes-bridge status
```

然后重启 `hermes gateway`。配置细节见
[docs/hermes-integration.md](docs/hermes-integration.md)。

### 飞书语音回复

默认情况下，飞书回复只发送文字。若设置：

```bash
RIJI_FEISHU_VOICE_REPLY_MODE=text_and_voice
```

riji-agent 会在保留文字回复的同时，生成本地音频并交给 Hermes 发回飞书。

可用 TTS provider：

- `macos_say`：零额外依赖，完全本地，但声音较机械，适合作为兜底；
- `melotts`：可选本地开源 TTS，声音通常比 `macos_say` 自然。启用前需要
  把 MeloTTS 单独安装进同一个虚拟环境：

```bash
uv pip install melotts
```

然后配置：

```bash
RIJI_TTS_PROVIDER=melotts
RIJI_TTS_LANGUAGE=ZH
RIJI_TTS_VOICE=ZH
RIJI_TTS_DEVICE=auto
RIJI_TTS_SPEED=1.0
```

`melotts` 的依赖和模型缓存比较重，所以不放进默认依赖锁定范围。首次运行
可能会下载或准备模型缓存；这些资产不在代码仓库内，也不应放进日记 vault。
云端 TTS provider 不作为默认方案：若将来接入 `edge_tts`、Azure Speech 等，
应明确 opt-in，因为回复文本会离开本机。

## 配置与安全

- `.env`、SQLite、审计日志、`data/` 和误复制的本地日记目录都应被 Git 忽略；
- `RIJI_JOURNAL_ROOT` 必须指向已有日记目录；
- `RIJI_DATA_DIR` 和可选 `RIJI_DATABASE_PATH` 必须在日记目录之外；
- `RIJI_IM_PROVIDER=feishu` 选择默认飞书 IM adapter；
- `RIJI_AGENT_RUNTIME=hermes` 选择默认 Hermes Agent runtime；
- `RIJI_MODEL_PROVIDER=deepseek` 选择默认 DeepSeek adapter；也可以设为
  `openai`，用 `RIJI_MODEL_*` 变量接任意 OpenAI-compatible endpoint；
- `RIJI_ALLOWED_FEISHU_USER_IDS` 是飞书 open ID allowlist，群聊默认拒绝；
- 服务只绑定 `127.0.0.1`。不要把本地端口直接暴露到公网。

## 开发

```bash
python scripts/privacy_scan.py --tracked
uv run pytest -m smoke
uv run pytest -m "not smoke"
uv run pytest
```

Smoke tests 使用临时 fixture 和 stub provider，不读取真实 `.env`、真实日记库
或真实 API key。
