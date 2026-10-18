# 导师首页直达群聊验证 — 2026-10-16

本次根据用户的模拟器反馈修正 [PRD v0.3.2](../../_bmad-output/planning-artifacts/prds/prd-riji-agent-2026-10-14/prd.md)：导师首页保留大人物与北极星，下方每个群直接列一行；点群一次就进入消息与输入框，聊天页不再重复首页的大人物/圆桌场景。旧圆桌记录仍可直接查看原消息，保留原身份与接收范围。

**本地模拟器验证完成：五条相关 UI 流程全部通过，零失败。** 本次未部署 Air、未安装手机；仍等待用户先查看模拟器体验，再按其确认进行后续操作。本地回归通过不代表真实模型、生产服务或真机体验已经验收。

## 变更范围

| 文件 | 本次调整 |
| --- | --- |
| `ios/Riji/MobileRoundtableViews.swift` | 新群与旧圆桌聊天页去掉重复的大人物场景，显示紧凑成员/接收范围与北极星信息；旧记录导航标题使用该记录名称。消息、草稿、结果保存与历史身份沿用原流程。 |
| `ios/Riji/NorthStarAnchorViews.swift` | 紧凑北极星栏继续提供当前目标的无障碍标识，让收起场景后仍能核验目标及来源切换。 |

这是两份 Swift 视图的局部修正，未改变后端 API 或权限范围。首页演示使用具名、独立的合成群，避免把“已有合成圆桌记录”误当作全部群聊的总入口；旧圆桌不转换或合并到新群。

## 环境与隔离

验证在 **Riji Automated Tests · iOS 27.0** 专用模拟器运行，使用独立命名空间的合成资料与回复；不访问 Air，不调用真实模型，不读取真实日记。测试数据与用户日常的 **Riji Development iPhone · iOS 26.5** 数据分开。本次向用户展示的也是隔离合成演示，不将示例群名、对白或目标称为用户真实资料。

## 回归结果

日志记录五条测试零失败，测试套件耗时 **296.162 秒**；整条运行命令的墙钟耗时约 **320 秒**。结果保存在 [chat-layout.xcresult](../../output/mentor-flat-list-2026-10-16/chat-layout.xcresult) 与 [chat-layout.log](../../output/mentor-flat-list-2026-10-16/chat-layout.log)。

| 测试 | 覆盖内容 | 结果 |
| --- | --- | --- |
| `testMentorNorthStarOriginalParagraphPersistsAcrossChatsAndSourceSwitchClearsIt` | 北极星原段落在聊天切换中保留，来源切换后清除旧关联 | 通过，75.399 秒 |
| `testMobileGroupMentionAndLargeTextKeepInputReachable` | @ 指定回应与大字号下输入区可操作 | 通过，37.900 秒 |
| `testMobileGroupResultPreviewRequiresConfirmationAndKeepsExactSelection` | 群结果预览、显式确认及精确选定内容保留 | 通过，50.163 秒 |
| `testMobileGroupsEmptyCreationRenameIndependentDraftsAndColdReturn` | 空群创建、改名、独立群草稿及冷启动找回 | 通过，80.470 秒 |
| `testMobileRoundtableHostInvitationAndLocalSavePreview` | 旧圆桌邀请与本地保存预览兼容 | 通过，52.217 秒 |

本次是针对视图调整的五条 UI 回归，没有将此前的全量 Python/Swift 测试算作本次新执行。原版本的更广验证仍保存在 [2026-10-15 记录](ios-mentor-groups-2026-10-15.md)。

## 截图与体验确认

已在隔离演示中通过原生界面创建“本周学习安排”和“长期同行群”，并在前者发送一条“学习计划”演示消息，确认直接进入消息与输入区。两群均为合成测试资料；保留原有旧圆桌记录，没有转换或合并历史。

- [首页原生截图](../../output/mentor-flat-list-2026-10-16/home.png)：保留导师人物与北极星，下方各群独立成行。
- [群聊原生截图](../../output/mentor-flat-list-2026-10-16/group-chat.png)：紧凑成员与北极星、消息及输入框，没有重复的大人物场景。

模拟器已停在导师首页，键盘捕获已关闭。实际体验仍由用户确认；图解 PRD 不能代替原生界面截图。

## 尚未验收

本次没有更新 Air、迁移真实资料或安装手机。生产服务能力、真实回复、实际网络及真机触控/输入体验仍需后续验证；用户查看模拟器后再决定是否进入手机安装流程。
