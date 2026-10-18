# Riji 长期记忆整理：现状与不可回退边界

日期：2026-09-21。用途：为“每日巩固、每周复盘、长期更新主题结构”的三端 PRD 提供 source extraction / reconciliation 基线。

本次仅阅读仓库文档、代码与 GitHub Issue；未访问 Air、未读取真实日记、未查询生产数据库、未运行或重试记忆作业。对话中此前出现的生产条数只能作为用户可见问题背景，不能当本次重新核验的现状。下列“已实现”指源码及已有记录，不等于当前生产/真机质量已验收。

## 1. 已确认需求与旧范围的关系

- 本次最新用户授权是**编写新 PRD**，要求 Air 后台、网页、手机 App 三端，并倾向 A-MEM；不是实施、部署、处理真实记忆或重新开启历史批次的授权。
- `docs/PRD.md` 是 2026-09-20 更新的 iOS 个人 MVP v3.0：四个底部入口为日记、导师、记忆、设置；手机直接编辑本人授权的原 iCloud Markdown，Air 管理长期记忆。文件同步、Air 读取、模型处理、记忆生效必须分别显示。Mem0/SQLite/凭据不进入 iCloud。
- `docs/product/PRD-ios-memory-overview.md` v0.2 于 2026-09-20 获准开发，文件记录 2026-09-21 更新 Air 与手机安装，但本人内容质量及实际使用验收待完成。首版明确固定六类 + 导师观察、可重建报告、最多三次点击到依据，关系网络图留待后续。
- 新 PRD 可以明确取代“六类固定首页”和“无周期性重写”的**未来产品设计**；仍需保留既有权限、来源、历史恢复和导师隔离。不能把新机制写成已经上线。
- `docs/PRD.md` FR-40–45 的周记/月记、MIT 与 Air 原有自动任务是独立工作流。记忆周复盘不是自动改写 weekly Markdown；若要保存入日记，应另走已有预览确认写入。当前任务没有核验/修改这些真实任务。

## 2. GitHub Issue 与依赖（完整正文已读取）

通过 Homebrew `gh` 的当前 host Keychain 校验成功后只读获取；现有凭据来源为 keyring，无登录/换 token 行为。

| Issue | 当前状态与正文范围 | 对本 PRD 的作用 |
|---|---|---|
| [#39](https://github.com/doublew6/riji-agent/issues/39) Add Mem0 self-hosted long-term memory and MEMORY.md review | OPEN；Mem0 权威存储、共享事实/导师观察、后台捕获、dead-letter、只读快照、Review、备份幂等迁移、loopback stack；要求隔离、失败降级、确认回归 | 基础能力契约；OPEN 不能解读成完全没有实现，也不能解读成生产验收 |
| [#40](https://github.com/doublew6/riji-agent/issues/40) Implement journal-derived memory initialization and lifecycle | OPEN；依赖 #39 已有本地 backend/capture/review；日记发现、初始化增量、证据时间、关系判断、来源失效、人工纠正、删除抑制、跨批次观察、导出恢复；原实现包 MEM-01–06 尚用未勾选列表 | 保留来源生命周期与有界处理；旧正文排除独立客户端，不覆盖后来已确认的 iOS 范围 |
| [#57](https://github.com/doublew6/riji-agent/issues/57) Design a structured mobile memory overview with a PRD and screen flows | OPEN；正文仍写“product discussion only”，无 comments；依赖 #55、#39、#40；禁止该旧任务改写生产记忆/引入图数据库，图视图是可选后续 | **正文已落后于仓库中的 2026-09-20 开发授权与 09-21 发布记录**。新 PRD 是下一阶段需求，不应被旧讨论限定误写成未实现首页，也不自动取得图数据库/生产操作授权 |
| [#55](https://github.com/doublew6/riji-agent/issues/55) Connect the personal iOS app to device-scoped mentor and memory services | OPEN；依赖现有导师/记忆服务，原目录编辑继续在 #54；单次过期配对、独立可撤销设备凭据、只转发 mobile routes、稳定操作 ID/回执、来源重验、Keychain/恢复 | 手机沿用设备范围 API，不能调用 loopback 管理接口或获取 Review/模型密钥；不另建日记同步 |

## 3. 三端当前入口与能力

### Air 后台

- Mem0 Self-Hosted + PostgreSQL/pgvector 保存权威记忆文本；本地 FastEmbed；SQLite 保存来源、段落指纹、任务租约、证据、阶段关系、预算、遗忘抑制、追加式操作。`MEMORY.md` 只读投影，不能反向导入。
- `MemoryWorker.run_once()` 在原生聊天捕获、日记引擎、整理器间轮转。日记扫描默认 60 秒；只发现变化，无变化不等于每分钟模型调用。线程空闲等待默认 2 秒，不是日/周整理调度。
- `JournalOrganization._organize()` 读取前次有效报告及 coverage，从未覆盖、未停止的 eligible seeds 继续；每轮最多 5 个种子，每种子最多 12 条同范围近邻、4500 字符事实正文。来源或事实变化及人工请求触发整理。中断超过 15 分钟的 organization run 变 failed，不永久卡 processing。
- 有稳定操作 ID、提取缓存、租约、发送前和返回后重验；模型结构化输出经严格校验，显式写 Mem0（不授权模型任意删除/改写）。
- 已有新/重复/补充/状态变化/冲突/相关关系、明确时间阶段、人工修订保护；语义近邻只找候选。

### 网页

- `http://127.0.0.1:8765/admin/memory` 为本地 Memory Review，开发机通过已配置 SSH loopback 转发访问 Air；`38880/38881` 分别是 Air Mem0 Dashboard/API，仅 loopback，不能视作开发机直连地址。
- Review 有认识地图、比较与复核、处理轨迹、原始记忆、来源进度与权限、暂停/继续、有界重试、纠正、归档/恢复/删除、导出/恢复及快照。
- 身份使用 Review token 换 HttpOnly/SameSite cookie，写操作 CSRF，CSP/no-store；浏览器不拿 Mem0 API key。新网页需求应复用该入口与安全边界，不能顺手公开管理站。

### iPhone

- `GET /memory-overview` 为过滤后的版本化投影；`POST /memory-overview/actions` 提交受控整理请求。不是把管理报告原封不动发手机。
- `MobileOverviewViews.swift` 已实现首页、主题、摘要、证据、观察、比较、最近变化以及深入原始记忆管理。首页固定 `CATEGORIES` 六类，导师观察单独入口。
- 服务端按用户、范围、导师、当前来源和记忆版本验证 group；绑定 snapshot version 分页，最多一页 20 个对象/1 MiB。发送前二次快照校验，变化则 409 重读。
- 手机保持阅读位置、处理乱序返回和未知写入回执；离线概览当前仅短期首屏副本，900 秒有效，凭据绑定、文件保护、排除备份；已知失效撤回先禁止 cache admission，即便物理删除失败也不能重启后复活。新机制若加离线日报/周报/主题详情，需显式扩展对象范围与失效规则，不能默认永久缓存。

## 4. 固定分类与“未处理”问题的源码证据

`src/riji_agent/memory/organization.py:19` 硬编码 preferences/goals/work/relationships/events/observations/other；提示词和 `_parse_report()` 要求每条输入记忆恰好归入一个主题，主题 category 必须在这个枚举中。标题和摘要由模型根据提供证据生成；没有持久、可编辑、可演化的用户主题目录。

`journal_organization.py:57` 的 `_eligible_seed()` 明确排除 `journal_kind=observation`；`:153` 又把这些非种子条目标成 covered，以便后台无需重新处理。`mobile/overview.py:112` 的 eligible 集合未复用同样 seed 规则；`:225–240` organized 只算实际有效 group 的 versions，再与 covered_seeds 相交。于是“覆盖但无需当种子生成主题”的观察可能落入 `unorganized`。这是由源码可解释的统计口径缺口；本次没有复核对话中的 135/137 条生产数字。

新版应分清：可处理事实、无需再次整理的观察/派生物、当前有效结果覆盖、来源失效、等待排队、正在运行、额度等待、暂停、终止失败。不能用“总量减已完成”推导“还在后台处理”。后台任务的 pending/processing/ready/failed 与覆盖质量是两个维度。

## 5. 不可回退约束

1. **来源与 AI 写入**：Air AI/飞书/Hermes 只能通过 riji-agent 的受限接口访问。源 Markdown 不被记忆整理器改写；AI 写日记沿用 draft → diff 预览 → 本人一次性确认 → 原子模板追加（30 分钟）。iOS 人工直接编辑是独立已授权路径。
2. **内容与用途**：`private:true`、memory none/local/cloud、局部标记和单条权限；四用途 history/incremental/organization/recall 分开。授权绑定来源范围与版本、接收模型及预算。目录访问/iCloud 同步/启用计划不扩大模型处理范围；无本地生成模型时 local-only 不伪装成可本地 AI 整理。
3. **出云最小量**：单原文片段 900 字符、同文件版本累计 4000 字符；日常处理每 UTC 日 100000 字符，含提示词、证据、旧事实等请求正文。不是 tokens/金额，也不是所有原生聊天总预算。可建议阶段子预算，但不能扩大现有总额或跨用途挪用例外。
4. **历史初始化与一次恢复**：冻结授权 `(evidence_id, version)`；批次不扩容，不因文件日期早/开关仍开而重新初始化。初始化账本独立计量，终态 completed/blocked 都关闭原豁免。2026-09-10 明确登记的失败 ID/版本只有一次整理恢复，pending/spent 单列；发送后不自动再领，失败终止。新每日/周/月调度、重新分类、重建图谱不能重开这个历史豁免，也不能顺手 retry 已停止项。
5. **来源变化/纠正/删除**：读、出云前、结果落库前重验来源和版本；撤权/删除即停止旧派生内容使用，不等后台重算。人工纠正优先、有独立本人依据；保留历史与原文。删除先落抑制，再清活动记录、history、派生报告、快照及含正文审计；后端失败也先退出召回。
6. **恢复**：恢复先暂停、合并当前和备份遗忘记录、映射 ID、重查本机授权与来源，结束仍暂停。不得从备份恢复旧授权、重建已删除记忆；不能切换到 sqlite 声称无损恢复新阶段/证据元数据。
7. **隔离与性质**：共享事实 vs 每个导师私有观察/聊天严格分开。用户计划不等于已完成；AI 建议、重复摘要、周月汇总不变为本人经历或独立证据。一次事实支持多个主题也只算一个事件。
8. **两个“观察”对象不能混写**：日记提取出的 `certainty=inferred` 持久记忆在 `journal_engine.py:358` 被设置 `privacy=local`，后续 `can_send` 拒绝出云。组织报告的 `derived-observation` 是另一类可重建推断：至少两个不同日期的明确 daily event 支撑，保留反例和限制；同用户/导师、权限、来源、版本验证后当前可作为“待验证观察”进入回答，最多两条（`journal_organization.py:297–337`）。不能把后一类当事实再喂给下一轮推断，不能因采用 A-MEM 自动升级前一类权限。
9. **观察证据局限**：当前以不同 daily observed_at 保守表示独立经历；同日不重复计数、weekly/monthly 汇总不计独立支持、未知日期不证明模式。近邻范围有限，不能声称已查全所有反例或完整日记。
10. **Air 与服务边界**：Air 是唯一生产部署。必须经 alias air 核验 MacBook Air、Fleet 心跳和真实监听归属、备份/dry-run、Air 测试后受控升级；现有 runtime/env/共享 Colima 和卷不得覆盖/重启。PRD阶段不执行这些操作。手机设备凭据与管理/模型凭据分离。

## 6. 真实缺口与 A-MEM 方向需要新增的对象

- 无每日巩固窗口、每周复盘窗口、月度/长期主题结构检查任务，缺计划状态、周期范围/时区、水位、补跑合并、版本/回执、预算公平性等完整契约。
- 当前报告主要是有界分组摘要，不是长期主题对象；缺稳定 topic ID、用户命名/锁定、别名、多主题证据关联、拆分/合并沿革、主题版本和可逆结构变更。
- 缺日/周整理**产物**与主题变化差异：本周期新增证据、改变的认识、未决冲突、反例、范围缺口、做了什么/没做什么。已有最近变化只是记录更新时间投影，不能冒充复盘。
- 缺对“旧事实未变，但新关联出现”的结构再评估。现有 covered_seeds 只按当前 memory version 继续未覆盖种子，不能表达链接演化及整棵主题结构健康。
- 已有事实时间阶段和关系不等于完整主题时间线。需要区分记录日期、事件生效日期、入库时间、整理时间；缺证据不能补出时间线。
- 无主题结构用户操作、机器建议接受/拒绝、改名持久保留、防止下轮模型回滚的产品契约。
- 无网页/手机共用的 daily/weekly/topic evolution 状态投影及缓存撤回依赖图。
- 在现有 Mem0 + SQLite 上实现链接元数据/版本化主题是可行架构选项，**A-MEM 并不自动要求更换权威存储或引入图数据库**。技术选型应留架构阶段，但 PRD必须要求行为与来源可检验。

## 7. 建议写入 PRD 的硬验收场景

| 场景 | 必须可观察的结果 |
|---|---|
| 全日无新增、无有效证据变化 | 到点记录 no-op/跳过原因，不空耗模型，不产空洞“每日复盘” |
| 同一天多次变更 + 任务重启/重复触发 | 一个稳定周期作业/receipt，变更窗口明确；重复执行不重复主题/边/报告/通知 |
| Air 休眠两周后恢复 | 有界合并补处理，展示实际覆盖与缺口；不无限逐日补调用或宣称十四日报告完整 |
| 本地时区变更/周边界/跨年 | 复盘周期不重叠不丢失；计划本地时区与 UTC 预算日分别可说明 |
| 队列拥堵/日额度耗尽 | 增量、每日、周与结构整理共用原日账本并公平有界；状态明确预算等待、下次可处理时间 |
| 历史已停止失败在新每日任务范围内 | 不自动重试原失败/恢复、不扩张冻结批次；可见“需单独处置”，不伪造全部成功 |
| 观察不符合 seed 条件 | 显示不适用/无需再次处理，不计入永远等待的未整理分母 |
| 新证据导致长期主题分化 | 可追踪旧 topic 到新 topic 的提议/版本/理由；保留用户命名与旧链接，事实原文不改写 |
| 一事实支持多个主题 | 多链接可追溯；原始事实/独立经历计数不被复制放大 |
| 日记→周总结→记忆→新周报循环 | 不增添独立证据、不把 AI 自我引用变强证据；明确保留事实根来源 |
| 只有一个事件/未知日期/只有周月汇总 | 不产生“重复模式”断言；记录证据不足 |
| 人工纠正/拒绝模型结构建议 | 之后每日/周/月任务不静默恢复旧结论/名称；所有派生展示以当前版本为准 |
| 模型调用已发送后撤权/来源修改/删除 | 不能收回已发送请求，但结果不生效；网页/手机/检索/快照/日报周报历史正文全部按依赖撤回或遮蔽 |
| 仅允许 local 或 organization 被撤回 | UI阅读不隐含模型调用；计划不能绕过权限，缓存不能恢复已知禁用资料 |
| 共享事实与多个导师观察混在候选池 | 分范围处理和输出；不得生成跨导师总画像/私聊材料泄露 |
| 同一报告手机第二页拉取时版本变化 | 不混页；重读当前版本并保留合理导航，当前阅读不能显示已失效文本 |
| 离线后纠正/删除/设备撤销、缓存文件删不掉 | 本机立即禁止旧缓存使用，远端生效等待确认；重启不能复活已撤回副本 |
| 模型超时但后端可能写入 | 先查原 operation ID，不另起重复记忆/主题/报告；未知状态不是失败成功二选一 |
| 升级迁移或中断恢复 | 先备份，原记忆 ID/证据/人工修订/遗忘抑制不丢；默认不重跑提取，不恢复旧授权 |
| 合成测试全绿，真实摘要有偏差 | 分列确定性边界、真实模型质量、Air 运行和本人三端可用性验收；不以条数/调用成功率代替质量 |

## 8. 主要定位路径

- 产品/约束：`PROJECT_GUIDE.md`；`docs/PRD.md` FR-29–35、40–45；`docs/product/PRD-ios-memory-overview.md`；`docs/architecture/mvp-architecture.md`。
- 来源/预算/恢复：`docs/journal-memory.md`；`docs/architecture/journal-memory.md`；`docs/long-term-memory.md`。
- 运行和整理：`src/riji_agent/memory/worker.py`；`organization.py`；`organization_store.py`；`journal_organization.py`；`journal_engine.py`；`journal_privacy.py`；`journal_transfer.py`。
- 网页：`src/riji_agent/memory/review.py`、`review_organization_ui.py`、`journal_ui.py`、`privacy_ui.py`。
- 手机投影：`src/riji_agent/mobile/overview.py`、`api.py`、`memory.py`；`ios/Riji/MobileOverviewViews.swift`、`MobileOverviewState.swift`；`ios/RijiCore/MobileOverviewCache.swift`、`MobileOverviewModels.swift`。
- 相关验证入口：`ios/RijiCoreTests/MobileOverviewTests.swift`、`ios/RijiStateTests/MobileOverviewStateTests.swift`；`docs/testing/ios-mentor-memory-2026-09-20.md`；`docs/testing/ios-mentor-memory-release-2026-09-21.md`。本次未重新运行测试或使用发布记录替代实时生产核验。
