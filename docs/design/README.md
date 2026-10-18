# Riji 页面设计索引

整理日期：2026-10-14。本索引把需求、视觉规范、历史原型、确认稿和实现证据分开。未移动或删除历史文件，历史讨论稿仍保留原状态；日记、记忆与设置 18 屏经用户于 2026-10-14 明确确认并授权实施。

## 从这里开始

1. 跨页面颜色、字体、组件：[视觉规范 v1.1](../DESIGN.md)。
2. 已确认的功能与操作：[个人 MVP PRD](../PRD.md)。
3. 导师已确认视觉：[F · 北极星指引 v1.0](mentor-dialogue.md)。
4. **已确认 v1.0：[其他页面设计与现状盘点](ios-pages-2026-10-14/proposal.md) · [18 屏可交互设计稿](ios-pages-2026-10-14/index.html)**。2026-10-14 已确认布局，后续新增页面沿用；真实能力与未接服务见逐屏状态表。

## 历史文件如何使用

| 材料 | 定位与效力 | 这次如何使用 |
| --- | --- | --- |
| [9 月 12 日全流程](../product/ios-design-2026-09-12/wireframe-riji-ios.html) | 早期产品稿；包含后续已调整的入口与概念 | 查沿革，不作为当前布局依据 |
| [9 月 14 日个人版](../product/ios-personal-mvp-2026-09-14/wireframe-riji-personal-mvp.html) | 个人 MVP 讨论原型 | 与后续反馈一起查阅 |
| [9 月 15 日 v0.2](../../output/ios-personal-mvp-2026-09-15/wireframe-riji-personal-mvp.html) | 已确认交互的主要历史来源；包含私人模板字段，仅本地保存 | 以总 PRD 和后续修正覆盖旧手势、设置位置等描述，不复制私人内容 |
| [个人版确认记录](../product/ios-personal-mvp-review.md) | 记录范围、反馈和确认过程 | 追溯正文编辑、原目录、四个底部入口等决定；其中早期实现状态是历史快照 |
| [9 月 18 日记忆概览 PRD](../product/PRD-ios-memory-overview.md) / [16 屏线框](../product/ios-feature-review-2026-09-18/wireframe-memory.html) | 六分类概览、证据与纠正的旧阶段方案，9 月 20 日获准开发 | 保留证据、纠正、隔离与失效规则；首页分类结构按后续演化 PRD 更新 |
| [9 月 18 日圆桌线框](../product/ios-feature-review-2026-09-18/wireframe-roundtable.html) | 材料、邀请、运行和恢复流程 | 业务背景；不替代导师当前视觉或新的交互讨论 |
| [9 月 21 日记忆演化 PRD](../../_bmad-output/planning-artifacts/prds/prd-riji-agent-2026-09-21/prd.md) | 已确认实施；动态主题、每日变化、周复盘、待判断 | 本轮记忆视觉稿的功能依据。六大类降为筛选；不用知识图谱替代证据 |
| [全局视觉规范](../DESIGN.md) | A1 暖光书桌方向已确认；旧十页只用于探索风格 | 奶油纸、沉静深绿、系统无衬线；不是十页布局全部确认 |
| [导师页面确认](mentor-dialogue.md) | F v1.0；10 月 13 日实现，10 月 14 日安装检查完成 | 独立的局部深色场景，不把其他页面改成宇宙场景 |
| [10 月 14 日导师交互 PRD](../../_bmad-output/planning-artifacts/prds/prd-riji-agent-2026-10-14/prd.md) | 另一任务正在讨论的 v0.1，未授权实现 | 人物聚焦、自然加人、话题记录均留给该讨论，不在本轮重新设计 |

## 核对范围与分支

读取了用户指定任务 **「多导师对话」**的近期记录。该任务主工作目录与本任务相同，主要代码分支为 `codex/56-57-ios-mentor-memory`；大量设计与实现存在工作区改动中，不能只检查 Git 已提交文件。该任务另在 `codex/publish-approved-design-docs` 的隔离 worktree 处理旧设计文档发布；本轮不修改它。

记忆演化分支 `codex/58-memory-evolution` 中另有 `MobileEvolutionViews.swift` 和 `MobileEvolutionState.swift`。因此“主目录没有新主题页面”不等于整个项目没有相关实现；该分支有功能界面，但本轮未发现独立的已确认页面视觉稿，也未据此认定已装到手机。

Obsidian 项目归档含 `设计规范-暖光书桌.md`、`design/mentor-dialogue.md` 与导师 v1.0 图片。本轮仅更新项目设计归档，不修改个人日记。已确认页面保存在本目录；原生实现与验证证据见[专项验收记录](../testing/ios-approved-pages-2026-10-14.md)，主稿与 Obsidian 使用同一版本。

## 已发现的状态文字滞后

- `docs/DESIGN.md` 的导师实现状态表已对齐[页面专项记录](mentor-dialogue.md)和[10 月 14 日安装记录](../testing/ios-mentor-north-star-install-2026-10-14.md)；布局、实现与本人验收仍分别记录。
- [记忆演化入口页](../product/PRD-long-term-memory-evolution.md)仍写待评审，完整 PRD 已是 `final / approved-for-development`。以完整正文的确认记录为准。
- 旧原型 README 的“尚未开发”等文字属于当时记录，不能用来判断今天的 App。

尚未同步的历史入口已在本索引标出；本轮不覆盖另一任务正在整理的原文。后续更新入口摘要时，应保留设计、实现、部署与本人验收四种独立状态。
