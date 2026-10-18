# 导师首页直接群聊验证 — 2026-10-17

**状态：本轮本机实现与回归已完成，待用户体验确认。** 用户明确确认“在导师首页直接聊天，不跳页面”，本轮按 [PRD v0.3.3](../../_bmad-output/planning-artifacts/prds/prd-riji-agent-2026-10-14/prd.md)取消首页到群聊天页的跳转。下文区分实际执行结果与未覆盖环境，不把旧版验收当成本轮结果。

## 先看实际变化

上方保留北极星和四位导师，下方直接显示当前群的消息与输入框。切换控件在原位更换群；单导师仍点人物进入自己的私聊。一次只呈现一个群，旧圆桌沿用原身份与原消息，没有合并或转换记录。

| 修改前：下方是群入口列表 | 本轮实现：下方已经是消息与输入 |
| --- | --- |
| ![修改前导师首页，群入口仍需打开聊天页](../../output/mentor-inline-chat-2026-10-17/before-home.png) | ![最终导师首页直接显示群消息和输入框](../../output/mentor-inline-chat-2026-10-17/home-final.png) |

两图均为未重绘的实际模拟器截图，使用合成数据。最终普通字号页面已手工确认北极星、导师、群消息、输入框与标签栏同屏，无默认多选、键盘或弹层，并留在模拟器中供用户体验。

[最大辅助字号](../../output/mentor-inline-chat-2026-10-17/large-type-final.png) · [切群后无需滚动找回原选择](../../output/mentor-inline-chat-2026-10-17/final-attachments/E6434816-61B5-4C15-9EFE-30BC70630C3B.png) · [回原群找回确切保存预览](../../output/mentor-inline-chat-2026-10-17/final-attachments/F16DE752-EEB7-48DA-96D3-9CB3B11A536C.png) · [已保存内容冷启动复核](../../output/mentor-inline-chat-2026-10-17/final-attachments/BA1044D3-FBBC-422F-8B40-42CB906962FB.png)

[原位切群菜单](../../output/mentor-inline-chat-2026-10-17/switcher-final.png) · [手工横屏检查](../../output/mentor-inline-chat-2026-10-17/landscape-final.png)。横屏几何可达检查通过，控件完整可见，但消息可视区明显更短，只能同时显示几行；长消息需要滚动，阅读舒适度仍待用户体验。最终已恢复竖屏。

## 版本与范围

- App：**0.1.0（构建号 2）**；系统版本 **iOS 27.0** 与 App 版本分别标识。
- 设备：**Riji Automated Tests**，UDID `<SIMULATOR_UDID>`。
- 仅使用独立命名空间的合成数据与回复；不将演示文字称为真实日记或真实模型回复。
- 范围为首页群呈现、原位切换、键盘与字号、群记录/草稿隔离及现有保存流程回归。F 视觉、旧圆桌身份、资料权限和 AI 保存确认规则保持。
- 本轮不部署 Air、不迁移真实数据、不安装手机，不操作真实数据的 iOS 26.5 Development 模拟器。

## 已通过的验证

**62 项状态测试通过，原生构建通过；10 个不同的相关 UI 场景在定向批次中分别获得通过结果。** 这是本次变更范围内的验证，不是全 App 测试，也不是同一批次一次性通过 10 条。重复执行的用例只计一次。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| AI 恢复、群状态、旧圆桌状态 | **62 通过，0 失败**：18 + 24 + 20 | [final-state-tests.log](../../output/mentor-inline-chat-2026-10-17/final-state-tests.log) |
| 最终原生构建 | **BUILD SUCCEEDED** | [final-build.log](../../output/mentor-inline-chat-2026-10-17/final-build.log) |
| 最终安装包 | **0.1.0 / 2**；`Riji` 与 `Riji.debug.dylib` 的已安装 SHA-256 均等于最终构建产物 | [installed-build.json](../../output/mentor-inline-chat-2026-10-17/installed-build.json) |
| 原有合成日记保护 | 仍为 **1169 bytes**，与任务前 SHA-256 完全一致 | [diary-after.json](../../output/mentor-inline-chat-2026-10-17/diary-after.json) |
| 最后字号调整 | 超大字号未读文字改为圆点后重新构建、安装，并手工检查圆点和箭头完整、消息与输入框同屏 | [最终截图](../../output/mentor-inline-chat-2026-10-17/large-type-final.png)、[源码快照](../../output/mentor-inline-chat-2026-10-17/source-final-installed.json) |

最后一次 UI 批次后的产品源码差异仅为 `MobileViews.swift` 的超大字号未读圆点呈现；普通字号继续显示“新回复”，辅助功能描述仍保留未读语义。该差异以最终构建和手工截图复核，没有把调整前的自动化结果冒充调整后的整套重跑。

### UI 执行矩阵

用例均位于 `ios/RijiUITests/DiaryFlowTests.swift`。

| 场景 / 用例 | 通过结果与实际覆盖 | 通过证据 |
| --- | --- | --- |
| 提及与大字号输入 `testMobileGroupMentionAndLargeTextKeepInputReachable` | 74.032 秒；大字号下群切换、提及和输入可达，最后圆点调整另经手工检查 | [inline-final-save-ui.log](../../output/mentor-inline-chat-2026-10-17/inline-final-save-ui.log) |
| 消息选择与跨群阅读 `testMobileGroupMessageSelectionOnlyAppearsAfterExplicitActionAndCanCancel` | 109.647 秒；显式操作后才进入多选，两轮消息选中较早回复，切到旧圆桌再返回，**不额外滚动**即可看见原选择，取消正常 | [inline-final-ui.log](../../output/mentor-inline-chat-2026-10-17/inline-final-ui.log) |
| 新群确切保存 `testMobileGroupResultPreviewRequiresConfirmationAndKeepsExactSelection` | 218.192 秒；取消不写入，手工日记编辑保留；A → B → 旧圆桌 → A 恢复编辑过的确切预览，日期与来源不串群，确认一次并冷启动核对 | [inline-final-ui.log](../../output/mentor-inline-chat-2026-10-17/inline-final-ui.log) |
| 横屏空间 `testMobileInlineGroupLandscapeKeepsMessagesAndComposerReachable` | 37.231 秒；实际横屏几何检查，四导师、切群、输入和有效消息视口位于导航/标签栏之间 | [inline-final-ui.log](../../output/mentor-inline-chat-2026-10-17/inline-final-ui.log) |
| 旧圆桌邀请与保存 `testMobileRoundtableHostInvitationAndLocalSavePreview` | 99.911 秒；邀请两导师、明确资料、确切保存、来源/原文读取与冷启动字节核对 | [inline-legacy-final-ui.log](../../output/mentor-inline-chat-2026-10-17/inline-legacy-final-ui.log) |
| 群生命周期与草稿 `testMobileGroupsEmptyCreationRenameIndependentDraftsAndColdReturn` | 176.087 秒；空态、建群、改名、双群与私聊草稿隔离、冷启动、归档、恢复及版本显示 | [inline-review-regression-ui.log](../../output/mentor-inline-chat-2026-10-17/inline-review-regression-ui.log) |
| 北极星原段落 `testMentorNorthStarOriginalParagraphPersistsAcrossChatsAndSourceSwitchClearsIt` | 110.193 秒；方向在私聊/群聊间保持，切换日记来源清除旧来源关联 | [inline-review-regression-ui.log](../../output/mentor-inline-chat-2026-10-17/inline-review-regression-ui.log) |
| 连续输入 `testMobilePrivateAndGroupSendKeepInputFocusForFollowingDraft` | 53.759 秒；私聊与群聊发送后保留后续输入焦点 | [inline-regression-ui.log](../../output/mentor-inline-chat-2026-10-17/inline-regression-ui.log) |
| 辅助字号圆桌控制 `testMentorAccessibilityTextKeyboardAndRoundtableControlsRemainReachable` | 49.230 秒；辅助字号、键盘及圆桌控制可达 | [inline-regression-ui.log](../../output/mentor-inline-chat-2026-10-17/inline-regression-ui.log) |
| 停止与离线恢复 `testMobileRoundtableStoppedSummaryAndColdOfflineDraft` | 69.157 秒；停止后摘要与冷启动离线草稿恢复 | [inline-repaired-ui.log](../../output/mentor-inline-chat-2026-10-17/inline-repaired-ui.log) |

## 发现并修复的问题

**真实阅读恢复缺陷。** 新增跨群回归发现：选择和阅读锚点已保存，但视图在消息挂载前设置位置，切回后没有滚到对应消息。新群与旧圆桌改为在消息可用后通过 `ScrollViewReader` 恢复，并在加载时保留待恢复锚点。最终用例以“不再次滚动就能看见原选择”的直接视口断言通过。

**原位布局的测试定位。** 旧测试使用整屏滑动、旧导航层级与原生菜单中不稳定的标识，导致滑过目标或过早操作归档。测试改为在实际 `roundtableMessages` 视口内短距离慢拖、等归档/恢复状态确实更新，保留隔离、来源和确切字节断言。建群与完整保存流程随后通过。

**iOS 27 自动化确认按钮。** 旧圆桌确认按钮完整可见并启用，但 XCTest 的 `isHittable` 查询抛出 activation-point 错误。改为断言实际 frame 完整位于屏内且 enabled，再点击观察到的按钮中心；保存成功、正文、来源、去重和冷启动断言保留，单独复测通过。

**超大字号未读提示。** 截图发现“新回复”变成三字竖排；强制单行又挤压切群箭头。最终在辅助字号显示圆点，并保留辅助功能未读描述。最终安装包手工检查通过。

<details>
<summary>保留各轮失败与中断记录</summary>

| 批次 | 当次结果 | 后续处理 |
| --- | --- | --- |
| [inline-first-ui](../../output/mentor-inline-chat-2026-10-17/inline-first-ui.log) | 1 通过 / 1 失败 | 归档原生菜单定位修正；完整生命周期用例后来通过 |
| [inline-regression-ui](../../output/mentor-inline-chat-2026-10-17/inline-regression-ui.log) | 3 通过 / 4 失败 | 旧全屏滚动假设失效；消息视口定位及后续相关复测记录如上 |
| [inline-repaired-ui](../../output/mentor-inline-chat-2026-10-17/inline-repaired-ui.log) | 1 通过 / 4 失败 | 停止/摘要/离线恢复通过；继续修正拖动距离与归档等待 |
| [inline-slow-scroll-ui](../../output/mentor-inline-chat-2026-10-17/inline-slow-scroll-ui.log) | 1 失败 | 短拖解决越过目标；新断言发现真实阅读恢复缺陷，随后修复并复测通过 |
| [inline-review-regression-ui](../../output/mentor-inline-chat-2026-10-17/inline-review-regression-ui.log) | 2 通过 / 1 失败 | 北极星与完整群生命周期通过；预览流程的原生菜单关闭定位后来修正 |
| [inline-final-ui](../../output/mentor-inline-chat-2026-10-17/inline-final-ui.log) | 4 通过 / 1 失败 | 新群阅读、确切保存、大字号与横屏通过；旧圆桌 activation-point 问题如上处理 |
| [inline-final-save-ui](../../output/mentor-inline-chat-2026-10-17/inline-final-save-ui.log) | 大字号通过；旧圆桌流程**中断** | 平台反复等待动画通知，终止本任务确认身份的测试进程；中断流程不计通过 |
| [inline-legacy-final-ui](../../output/mentor-inline-chat-2026-10-17/inline-legacy-final-ui.log) | 旧圆桌单独复测通过 | 99.911 秒，`TEST EXECUTE SUCCEEDED` |

原日志、`.xcresult` 和截图保存在 `output/mentor-inline-chat-2026-10-17/`，不覆写早期失败，也不把一次批次的通过推断为另一批次全部通过。

</details>

## 仍需分别验收的事项

- 用户的日常使用体验，以及真实服务、iCloud 传播、VoiceOver 实际朗读、真机触控与中文听写，本轮合成数据模拟器检查没有覆盖。
- 创建结果未知时的恢复提示已移入消息滚动区，空态只显示一次；本轮有代码审查与状态回归，未单独注入该恢复提示来执行视觉故障场景。
- 两项**任务开始前已存在**的体验问题保留在[延期清单](../../_bmad-output/implementation-artifacts/deferred-work.md)：相同成员的多个群默认名相同（目前可手动改名）；旧主持人圆桌尚未呈现“有界历史加载未找回阅读位置”的继续加载提示。前者不合并群身份，后者不丢弃已保存锚点；本轮不声称已解决。

共享工作树原有修改和暂存状态均保留，本轮未提交或推送代码。
