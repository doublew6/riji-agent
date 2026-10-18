# 导师北极星界面验证 · 2026-10-13

后续更新：2026-10-14 已覆盖安装到本人 iPhone，App 自有数据文件核验未变；用户完成开发者信任后正常启动，进程检查通过。详见[真机安装记录](ios-mentor-north-star-install-2026-10-14.md)。以下保留开发与模拟器验证当时的范围。

依据：[F · 北极星指引 v1.0](../design/mentor-dialogue.md)及 [#56 实施规格](../../_bmad-output/implementation-artifacts/spec-56-mentor-north-star-ui.md)。本次为本地实现与合成数据验证，未使用真实日记、目标或记忆内容；人物资产来自已确认的用户形象衍生设计。未进行 Git 提交、服务部署或真机安装。

## 实现范围

- 导师入口、单导师对话与圆桌使用局部深绿灰外观。共享静态星空、北极星、暖光角色图集、空间圆桌和发言卡片；未来的我使用已准备的男性眼镜形象。单聊人物居中占容器宽度的 76%，底部渐隐；圆桌按实际 runPersonas 标记参与者，最新有效导师发言采用左文右人物构图，窄宽或无障碍字号改用纵向。日记、记忆、设置保持各自外观和四个并列入口。
- 北极星只关联已有来源。本地定义由用户选择具体当前版本；原目录由用户在已授权目录中逐层选择 `.md`、`.markdown` 或 `.txt` 文件，再选择以空行分隔的确切段落并核对确认。预览与确认均重读来源。
- 本机 JSON 只保存引用，按日记来源及服务账户隔离，引用目录排除设备备份。原文件引用包含相对路径、UTF-8 字节范围和整文件 SHA-256；不保存另一份目标正文，不写原文件，也不把目标自动加入聊天请求。
- 原文件读取上限 2 MiB，可选择段落上限 16 KiB；符号链接等特殊路径沿用原目录防护。来源变化、冲突、缺失或读取失败时撤下当前目标声明。重新进入、前台恢复和每 15 秒重新核验；同一来源读取中保留上一次已核验内容，版本变化需显式重选。失效关联仍可取消；来源刷新与保存/取消失败的反馈分别保留，重试会重新读取正在选择的文件。
- 输入聚焦或系统无障碍字号时折叠人物场景。目标完整内容可从入口展开；正文随系统字号换行。真实 activeActor 的指示线切换有 200 ms 淡入/淡出，开启 Reduce Motion 后直接切换，没有持续动画。全部既有消息、来源、草稿恢复、邀请核对、停止/总结/继续和结果保存仍由原状态与接口驱动。

## 自动化证据

环境：Apple Silicon，macOS 27.0.1，Xcode 27.0（27A266a），Swift 6.4。仅使用唯一的 **Riji Automated Tests** 专用模拟器，iPhone 18 Pro Max / iOS 27.0；未使用手动开发设备。

1. 首轮 `python3 scripts/test_ios.py --suite core`：588 项通过。证据：`output/ios-tests/20261013T092841Z-core-9a534191/`。
2. `xcodebuild` generic iOS Simulator / Debug / `CODE_SIGNING_ALLOWED=NO`：构建成功，包含 SwiftUI 与资源。日志：`output/mentor-dialogue-build-2026-10-13/build.log`。
3. 新增 4 项 Core 和 7 项 State 测试后，599 项 package 测试通过：299 Core、295 State、5 Probe support。涵盖 Unicode/CRLF 字节范围、伪造引用、来源/账户隔离、持久化失败、版本失效、原文件变化/冲突/缺失/超限、异步旧读取和选择窗口恢复。早期一轮新测试因合成 vault 缺少必要模板而失败，补齐合成 fixture 后重跑。
4. Native 与 7 条聚焦 UI journeys 使用原自动化 runner 的锁、环境过滤、设备核验、源码前后快照和结果清单审计；此阶段仅在调用脚本内指定 UI 清单。调用脚本：`output/mentor-dialogue-build-2026-10-13/run-focused.py`；证据目录：`output/ios-tests/20261013T095359Z-smoke-a87c7738/`。该运行的目录名含 `smoke`，实际为聚焦清单，不能称为当时默认 27 条 smoke 全量通过。复核后已将三条新增导师 UI 加入默认清单，默认 smoke 现为 30 条；本任务未执行全部 30 条或远程 CI。

| 聚焦 UI journey | 验证范围 |
| --- | --- |
| `testMobileMentorDraftColdRecoveryReplyAndMemoryCorrection` | 私聊草稿冷恢复、回复、记忆依据与纠正 |
| `testMobileRoundtableHostInvitationAndLocalSavePreview` | 主持人接话、邀请、结果选择与本地确认保存 |
| `testMobileRoundtableStoppedSummaryAndColdOfflineDraft` | 停止、总结及离线草稿冷恢复 |
| `testMobileRoundtableInvitationPinnedReviewAndTenRounds` | 大字号、邀请核对与十轮预算 |
| `testMobileOlderHistoryKeepsCurrentPageAndDraft` | 历史分页、已有消息和草稿保留 |
| `testMentorNorthStarOriginalParagraphPersistsAcrossChatsAndSourceSwitchClearsIt` | 显式段落关联、两屏一致、冷恢复、原文不变、换来源清除和返回日记 |
| `testMentorAccessibilityTextKeyboardAndRoundtableControlsRemainReachable` | 最大无障碍字号、两种输入框、键盘及发送/邀请可达 |
| `testMentorNorthStarExternalChangeInvalidatesOpenChat` | 保持聊天打开、修改合成原文、15 秒核验撤下旧目标、取消失效关联与冷恢复 |

### 结果与修复记录

| 证据目录（均位于 `output/ios-tests/`） | 实际结果 | 结论 |
| --- | --- | --- |
| `20261013T095359Z-smoke-a87c7738` | 599 package、7 UI 通过；Native 准备执行 worker 超时，运行总结果失败 | 保留启动基础设施失败，不能视为 Native 已执行或已通过 |
| `20261013T110422Z-smoke-f2e505dd` | 59 Native、6 UI 通过；北极星选择 UI 失败 | 大图集 `.clipped()` 仅限制绘制，透明溢出区域拦截上方按钮；失败 hierarchy 仍在聊天页，没有打开选择器。给纯装饰人物/舞台禁用 hit testing，保留 5 秒断言 |
| `20261013T111715Z-smoke-349d5f5a` | 599 package、59 Native、2 条新增 UI 全通过 | 修复点击、把来源核验移出 lazy banner 至页面，并在读源准备完成后发布 activationID；新增选择器打开状态下的前后台恢复检查通过 |
| `20261013T113532Z-smoke-865ae7e4` | 59 Native、2 条新增 UI 全通过，0 失败/跳过 | 最终人物居中、短状态反馈及左右对白排版后的验证；导出最终 7 张截图并人工读图 |
| `20261013T124809Z-smoke-e8ee3957` | 16 定向 package、2 UI 通过；新增外部变化 UI 失败 | 复核修复后的来源与存储逻辑通过；失败 hierarchy 显示已进入核对关联，但长段落使确认按钮处于 List 屏外。沿用有界滚动辅助方法展示按钮后再点确认，没有改生产逻辑或放宽断言 |
| `20261013T131808Z-smoke-ea5af139` | 604 package、1 条外部变化 UI 全通过，0 失败/跳过 | 最终回归；300 Core、299 State、5 Probe，113 个源码输入未变。确认真实轮询、来源失效、取消关联及冷重开 |

以上各轮源码输入审计均为 `unchanged`。第三轮 package 计数仍为 299 Core、295 State、5 Probe support；59 Native 为 46 BodyEditor 和 13 Voice。第三轮调用脚本是 `output/mentor-dialogue-build-2026-10-13/run-final-targeted.py`；最后一轮是 `run-final-visual.py`，113 个源码输入未变。最后仅调整 SwiftUI 视觉布局，因此复用第三轮 package 结果，未重跑其余五条已通过的 UI journey。所有调用脚本均保留了原 runner 的结果发现、锁和源码审计。

大字号截图已确认软件键盘实际显示，发送按钮位于键盘上方；通过测试内 `typeKey` 调出键盘，未修改通用模拟器设置或手动开发设备。第三轮带实际合成对话的截图还暴露人物偏左及 `ViewThatFits` 对长正文总是选退化布局的问题；随后用全宽舞台与有限列宽 Layout 修正。最终截图确认单导师近身构图居中、底部渐隐、圆桌实际两位参与者与其余可选导师有区分，最新温柔回顾者回复为左侧全文和右侧大人物。

失败附件和修复中间截图分别保留在 `output/mentor-dialogue-build-2026-10-13/screenshots-first/`、`screenshots-second/`、`screenshots-third/`。其中第二轮失败现场包括 UI hierarchy、屏幕录像；没有删除失败证据或放宽验证条件。

复核新增的 5 项 package 测试覆盖目录备份排除策略、慢读取期间显示稳定及操作错误保留、保存后的来源竞态、重读选中文件和取消失效引用、文件上限反馈。主持人与导师使用相同的发言切换指示；本地目标详情补齐测量和来源字段。最后一轮调用脚本为 `run-post-review.py`，保留原 runner 的环境隔离、进程锁、逐项结果及源码审计。未变的 Native 及其余 UI 复用已通过证据，共覆盖 8 条不同的聚焦 UI journey。

Python runner 回归命令 `uv run --no-sync pytest tests/test_ios_test_runner.py -q` 通过。三项新 UI 已登记在 `scripts/test_ios.py` 及[日常自动化清单](ios-automation.md)中。复核失败 hierarchy 另存于 `screenshots-review/`，恢复界面证据见下表。

### 最终截图

主体视觉由 `20261013T113532Z-smoke-865ae7e4/tests.xcresult` 导出；完整附件映射见 `output/mentor-dialogue-build-2026-10-13/screenshots-final/manifest.json`。已逐张检查，正常字号两屏使用同一显式选定合成目标，文字与回复来自实际客户端 fixture 状态。来源恢复截图来自最后一轮 `20261013T131808Z-smoke-ea5af139/tests.xcresult`。

| 场景 | 原生截图 |
| --- | --- |
| 单导师：选定目标、居中男性眼镜人物、已有用户消息 | [单导师舞台](../../output/mentor-dialogue-build-2026-10-13/screenshots-final/6BF7719F-CC24-4AC1-91B8-26D7364AEFBF.png) |
| 单导师：完整用户消息和导师回复 | [单导师对话](../../output/mentor-dialogue-build-2026-10-13/screenshots-final/2E544E19-B875-47E4-9350-9AE43F19B5C2.png) |
| 圆桌：同一目标、四导师空间与本轮两位发言者 | [圆桌舞台](../../output/mentor-dialogue-build-2026-10-13/screenshots-final/2EF56CAB-DA9E-4FAE-9A09-F48F150B63E2.png) |
| 圆桌：最新导师全文、右侧人物及主持人总结 | [圆桌对白](../../output/mentor-dialogue-build-2026-10-13/screenshots-final/31024437-0346-492F-9F19-260FD3AC3C38.png) |
| 最大无障碍字号：单导师软件键盘、可达发送按钮 | [单导师大字输入](../../output/mentor-dialogue-build-2026-10-13/screenshots-final/E1EA5859-0A46-455C-8DCA-04B141769FF0.png) |
| 最大无障碍字号：圆桌软件键盘、可达发送按钮 | [圆桌大字输入](../../output/mentor-dialogue-build-2026-10-13/screenshots-final/B18A3CB1-DF46-42A9-8B1E-FD8E2D09D4CB.png) |
| 返回日记：浅色正文与四个并列入口 | [日记外观隔离](../../output/mentor-dialogue-build-2026-10-13/screenshots-final/51C9C4DD-1638-4445-876D-265225E9C3CC.png) |
| 原目标失效：恢复提示、重读与取消本机关联 | [来源恢复](../../output/mentor-dialogue-build-2026-10-13/screenshots-recovery/85E0A458-39FF-4E40-8BF4-AF3DC192EEF6.png) |

## 验收边界

没有执行真实 iCloud 传播、物理键盘/听写、VoiceOver 朗读、真机小屏或 Air 服务验收。Reduce Motion 分支已实现，未在辅助功能设置中单独录制动画切换。截图只能证明本次专用模拟器与合成状态；真实目标未选择，设计稿的示例目标也没有进入默认产品内容。

两项已知交互限制：如果将同一资料集从“直接授权目录”改成“授权其父目录”，相对路径可能变化，需要重新选择目标段落；整文件校验仍防止把不同内容误当作原目标。应用进入后台或发生来源切换时，未确认的选择预览和目录位置会重置，返回后需要重新选择；已经保存的关联仍会重新核验后恢复。

对比以 `output/mentor-dialogue-build-2026-10-13/baseline/ios/` 为起点；保留任务开始前的未提交功能，不以 HEAD 总 diff 代表本次改动。
