# iOS 双向日期滑动：2026-09-18 验证记录

2026-09-18 用户确认的方向取代旧 FR-05：手指从左向右滑动打开前一个日历日，从右向左滑动打开后一个日历日。本文对应 Issue #53 的双向修正，规格为 `_bmad-output/implementation-artifacts/spec-53-ios-bidirectional-date-swipe.md`。这里只记录本机自动测试，真机体验仍需单独验收。

## 实现范围

- `MarkdownTextView` 用同一方向判定处理两个符号：右向 `.previous`、左向 `.next`。完成门槛仍为水平 80 pt，垂直位移不超过水平位移的 30%；编辑、组合输入、选区、表格、短拖动和明显斜向拖动仍不切日。
- `DiaryBodyEditor` 在回调前保留当前文档的视口；`TodayDiaryView` 和 `DiaryViewModel` 核对回调来源日期，繁忙或过期回调不导航。保留既有 TextKit 完整布局后恢复偏移的顺序、按文档保存的光标／阅读位置和跨日清除撤销规则。
- `DiaryViewModel` 用 Gregorian UTC 标签计算相邻日并复用既有 `navigate(to:)` 和保存／冲突／恢复门禁。不是查找最近的已有文件；没有今天上限。拒绝向 0001-01-01 之前或 9999-12-31 之后移动。
- 原模板加载和文件写入逻辑不变：缺失或未来日期浏览只预览原模板，未编辑或显式保留前不创建文件。
- 更新当前 PRD、开发计划、验收清单、README 和 Mac 手动步骤。历史导航与确认记录保留原文，附 2026-09-18 取代说明。根任务复核后，小范围校正 README／手动说明中已过时的服务状态：单导师私聊与逐条记忆已有接入，圆桌和结构化记忆概览待独立 PRD 评审；不把本次 fixture 结果当作真实服务验收。

实施前对本次涉及文件做了独立快照；窄差分按快照核对，不把既有混合 iOS 工作当作本次修改。没有暂存、提交、远程操作或 Air 服务操作；没有擦除或卸载主 App。个人数据的独立前后保全证据由根任务负责，本文不以合成测试替代该证据。

## 环境与结果

- Xcode **27.0 / 27A266a**。
- 复用 **Riji Development iPhone**，iPhone 17 Pro Simulator，iOS **26.5 / 23F77**；测试使用现有 UUID 隔离命名空间。
- 固定 Swift scratch：`/tmp/riji-ios-swift-build`；Xcode 复用启动脚本的 `riji-ios-manual-derived`。根任务确认独占 Xcode 后运行，关闭并行测试。
- Swift package：**451 项通过，0 失败**，包含 State 216、Core 230、Probe support 5。
- 第一轮 Xcode：**46 项通过，0 失败、0 跳过**，包含完整 Native 40（Body 27、Voice 13）及 focused UI 6。构建和测试均成功。
- 第二轮：精化后的正负 80 pt 原生门槛复验与托管连接 UI 通过；既有设置流程在旧连接文案断言处失败（共 3 项，2 通过、1 失败）。现有 `MobileClientModel` 的初始文案是“服务尚未激活”，测试仍期待“尚未连接”；首次只改文案仍失败，录屏确认画面已有正确状态，旧 `firstMatch` 查询未限定状态文字。再把查询限定为 `settingsAirStatus` 与“服务尚未激活”的静态文字；未修改连接实现。两次失败日志、结果包与录屏均保留。最终设置单项复验 **1 项通过、0 失败**；最终累计覆盖 **8 项独立 UI 流程全部通过**，原生正负 80 pt 门槛复验也通过。

所有输出位于 Git 忽略目录 `output/ios-roadmap-2026-09-18/swipe/`：

| 证据 | 文件 |
| --- | --- |
| 实施前文件快照与窄差分 | `before/`、`implementation.diff` |
| 精确执行命令 | `commands.txt` |
| 完整 Swift package 输出 | `swift-test.log` |
| Native + 6 项 UI | `navigation-matrix.log`、`navigation-matrix.xcresult`、`navigation-summary.json` |
| 兼容及门槛复验，含过期断言失败证据 | `compatibility-matrix.log`、`compatibility-matrix.xcresult`、`compatibility-summary.json` |
| 仅改文案仍失败的单项复验 | `settings-recheck.log`、`settings-recheck.xcresult`、`settings-failure.png` |
| 设置查询及文案修正后的最终复验 | `settings-final.log`、`settings-final.xcresult`、`settings-final-summary.json` |
| 第一轮截图及长文性能附件 | `attachments/`、`attachments/manifest.json` |

第一轮结果包含两条 `NativeVoiceTests` 的 QoS priority-inversion runtime warning；对应测试通过，本次未修改语音线程调度。未把这些 warning 描述成无警告构建。

## 实际运行的验收矩阵

| 场景 | 已运行证据与结果 |
| --- | --- |
| 右滑到前一天、左滑回后一天 | UI `testNavigationGesturesAndDestinationRoundTripsPreserveActualReadingPosition`：2026-09-12 → 09-11 → 09-12，日期与正文一致，原文逐字不变。 |
| 缺失／未来原日期 | State `testFutureMissingDayPreviewsOriginalTemplateWithoutCreatingFiles`：09-14 → 09-15，不跳到已存在的 09-17，保留原模板的 CRLF、表格和未知宏。UI `testBothSwipesPreviewMissingFutureOriginalDaysAndReturnWithoutCreatingFiles`：09-12 → 09-13 → 09-14 → 09-13 → 09-12，原目录无新增 09-13/14 文件。 |
| 年／月／闰日／DST／本地跳日 | State 使用独立字面预期，双向覆盖 2025-01-01、2024-02-29、2026-03-01、Los Angeles 2026-03-08/11-01、São Paulo 2018-11-04、Apia 2011-12-30，并反向回到起始标签。UI `testTodayDatePickerAndBlankPreviewKeepSkippedLocalDateLabels` 运行 São Paulo 和 Apia 两例。 |
| 年界拒绝 | State `testAdjacentDaysRejectUnsupportedYearBoundsWithoutChangingInput`：0001-01-01 向前、9999-12-31 向后均拒绝，当前日期和正文保持；向支持范围内移动成功。 |
| 保存失败与恢复 | State 双向失败后保留正文及持久恢复内容，恢复存储后再导航保存源日。UI `testFailedSaveAndEditingGestureCannotLeaveDiaryOrLoseInput` 双向测试编辑态、收键盘后的失败保存和四项底部导航，重启后最新正文仍在。 |
| 未解决合并 | State `testBothDirectionsAndTabsRejectOpenAndRetainedUnresolvedMerge`：打开的冲突及“稍后处理”的保留合并均阻止双向切日和离开主页面，双方原文保持。 |
| 并发／过期来源 | State `testOneAdjacentTransitionOwnsSaveAndRejectsRapidAndStaleCallbacks`：两方向分别持有保存门禁；重复、反向和旧日期回调均拒绝，源日只保存自己的正文。 |
| 手势准入与完成 | Native 生产 editor harness 运行正负方向准入、短／竖／斜拖、表格行、停用、选字、编辑和 `setMarkedText`；真实完成 handler 传递来源日期与方向。 |
| 长文与 TextKit 视口 | 完整 Native 包含长文输入、选区、按文档往返、卸载再挂载和表格后正文滚动。上述实际 UI 日期往返和底部／目录往返核对同一光标、阅读偏移（2 pt 容差）及正文。 |
| 设置与手机连接兼容 | `testMobileManagedInstallationConnectsWithoutLeavingFourPeerTabs` 合成托管连接和四个并列入口通过；修正过期断言及查询后的 `testSettingsMainTabSavesInputAndKeepsSubpagesAndRecordingsReachable` 最终通过，覆盖保存、设置子页、冷启动与录音库可达性。 |
| 表格与原位保存 | UI `testOriginalTableHorizontalAndVerticalGesturesStayInSameDiary` 左右横滚都停留原日，竖向滚动正文；`testOriginalHomeManualEditingSavesAndColdReopensExactSource` 验证原目录修改并冷启动保留精确内容。 |

## 2026-09-18 独立复核后的定向修复

本轮仅修改三份测试与状态／证据文档，没有修改生产代码或日期解析。短拖动、编辑态滑动和失败保存滑动现在每做一个动作就独立核对日期、正文及日记 tab；编辑态另核对键盘，失败保存另核对错误和重试入口，避免相反手势抵消错误导航。

新增独立 UI 流程 `testMissingFutureDayFirstEditSavesAndColdReopensWithoutChangingSource`：从原目录 09-12 左滑到缺失的 09-13，核对原模板，首次编辑保存，右滑确认 09-12 字节未变，冷启动后再次打开 09-13 并核对精确正文、日期和保存状态。原有只浏览不建文件测试仍单独保留。

原生完成矩阵覆盖 `x=±80, y=±24` 的四种符号组合接受，以及 `x=±80, y=±24.1` 的四种组合拒绝，能发现方向比例被放宽到 0.4。年界测试在 0001-01-01 与 9999-12-31 先输入未保存文字、等待现有恢复缓冲落盘，再拒绝越界并核对完整 `BodyEdit`（包含原 ID、日期绑定与正文）不变；向内导航后确认文字保存到原边界日期。

定向结果：State 年界测试 **1 项通过、0 失败**；Native 手势 **1 项通过**与相关 UI **4 项通过**，Xcode 结果包合计 **5 项通过、0 失败、0 跳过**。根任务负责后续全量验证，未在本轮重复运行无关测试。精确命令、日志、结果包和修复前快照位于 `output/ios-roadmap-2026-09-18/swipe/review-fixes/`，分别为 `commands.txt`、`state.log`、`native-ui.log`、`native-ui.xcresult`、`native-ui-summary.json`、`before/` 和 `review-fixes.diff`。SwiftPM 的 `state.log` 第 11–18 行明确记录 `testAdjacentDaysRejectUnsupportedYearBoundsWithoutChangingInput` 实际执行并通过；日志末尾未匹配过滤器的 Probe/Core 套件各为 0 项，不计入通过数量。

既有历史日期限制：Foundation 的 Gregorian／DateFormatter 保留 1582 年改历边界及更早日期的历史日历行为；当前 UTC 标签运算不等于已验证完整的前推 Gregorian 日历。该限制登记为后续历史日期兼容工作，不在本次修复中更改解析或生产日期计算；现代日期、DST／本地跳日和最外层年界拒绝的证据范围保持明确。

## 最终自动回归与安装包

后续专用 **Riji Automated Tests**（iPhone 18 Pro Max、iOS 27.0）上的完整 smoke 已通过：Swift package **451**、Native **40**、UI **14**，合计 **505 项，0 失败、0 跳过**。实际发现、请求及执行清单逐项一致；原文编辑、双向切日、缺失日首次编辑、长文位置、设置、导师与记忆关键流程均包含在这 14 项中。全部 UI 清单为 42 项，本轮 smoke 不等于全量 UI 验收。完整证据及不同 Xcode/CI 环境边界见[自动化记录](ios-automation-2026-09-18.md)。

本轮生产源文件与已成功签名的 iPhone 构建哈希一致，安装包保留在本机忽略目录 `output/ios-roadmap-2026-09-18/iphone/Riji.app`。严格签名核验通过，但手机尚未连接，尚未覆盖安装。手动 Simulator 原有数据文件内容哈希保持不变；未操作 Air 服务。

## 尚待真机验收

根任务负责签名构建、手机安装与个人数据保全。需要在本人 iPhone 上确认：左右手指滑动的实际手感、中文输入法候选和光标拖动不误触、宽表格横滚、长文返回后的阅读位置、大字号及 VoiceOver。Simulator 的手势事件、原生 `setMarkedText` 和合成服务测试不证明这些真机项目已完成，也不证明真实 Air／外网连接已验收。
