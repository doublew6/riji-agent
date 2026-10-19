# iOS 自动回归

在仓库根目录运行以下命令。需要 macOS、Python 3.9+ 和已完成首次设置的完整 Xcode；无需安装 Python 依赖或启动后端。

```sh
python3 scripts/test_ios.py
```

默认执行 `smoke`。`DEVELOPER_DIR` 接受实际 Xcode `.app` 路径或其 `Contents/Developer` 路径；runner 将前者归一化后使用：

```sh
DEVELOPER_DIR=/Applications/Xcode_26.3.app/Contents/Developer python3 scripts/test_ios.py --suite full
```

需要分批运行 smoke 时，可指定 `--smoke-shard 1/4`、`2/4`、`3/4`、`4/4`。每片执行完整 package 和 Native 测试，并按排序后的 UI 测试标识轮流分配到各片；四片分别执行 11/10/10/10 条 UI，无重叠、合计覆盖全部 41 条。单片通过只表示该片通过，四片均通过才能代表完整 smoke。分片参数不适用于 `core`、`native` 或 `full`；不传参数仍执行完整 smoke。

CI 使用独立 `core` 与四片 `ui-smoke`，避免每片重复编译、执行相同的 Swift package。`ui-smoke` 只执行全量 Native 与所选 UI，不包含 package，不能单独代表完整 smoke；必须核对同一提交的 `core` 与全部 UI 分片都通过。本机默认 `smoke` 的范围保持不变。如需分别复现 CI，可运行：

```sh
python3 scripts/test_ios.py --suite core
python3 scripts/test_ios.py --suite ui-smoke --smoke-shard 1/4
# Repeat ui-smoke with 2/4, 3/4 and 4/4 for complete UI coverage.
```

| Suite | 实际执行范围 |
| --- | --- |
| `core` | 全量 Swift package：Core、State、Probe support，不需要 Simulator |
| `native` | 全量 hostless Native tests |
| `smoke`（默认） | 全量 package + Native + 41 条 UI journeys（清单以 runner 为准） |
| `ui-smoke` | 全量 Native + 41 条 UI journeys，或 `--smoke-shard` 指定的 UI 子集；不执行 package |
| `full` | 全量 package + Native + 全量 UI |

`core` 的 Probe support 是合成文件与协议测试，不执行真实 iCloud probe。命令始终执行测试；缺工具、缺运行时、工具失败、零执行、跳过或清单不符均返回非零，不降级成 build。

## 隔离与重复运行

runner 仅使用名为 **Riji Automated Tests** 的专用 iPhone Simulator。首次运行从已安装且可用的 iOS runtime 中选最新的兼容 iPhone 类型；已有专用设备则复用其 runtime，不自动升级。发现同名重复设备、不可用设备、非兼容 iPhone 或正在切换状态的设备时停止，让操作者在 Xcode 中核对。它不 erase、uninstall、克隆、清理或关闭任何 Simulator，不使用 **Riji Development iPhone**。

所有 Xcode 枚举与测试使用专用设备的明确 UDID，关闭并行测试，只允许一个测试 Simulator/worker。测试方法使用 UUID namespace、合成 Markdown/音频及注入的 mobile transport；不连接真实 Air、AI、Feishu 或原 vault，也不访问手机。runner 不把继承的 `RIJI_*` 或 `SIMCTL_CHILD_*` 配置传给工具。真实 iCloud 目录授权、物理手机输入/听写、实际连接/模型和 Air 部署属于[个人 MVP 独立验收](ios-personal-mvp-acceptance.md)。

构建缓存固定在当前用户的 `~/Library/Caches/riji-ios-automated-tests/`。同目录的进程锁覆盖所有 checkout；第二个 runner 会立即失败，不杀进程或夺取设备。子工具继承锁，父进程意外退出时仍避免另一 runner 抢用其缓存。不要删除正在使用的锁文件；普通退出后系统自动释放锁。运行结果单独保留，不随缓存复用覆盖。

## 执行期限与中断

每个子命令都有固定期限，无需额外参数：普通工具 2 分钟、Simulator boot status 3 分钟、Swift discovery 15 分钟、Swift tests 15 分钟、Xcode discovery 10 分钟、Xcode tests 40 分钟。`swift test list` 在首次运行时也需要编译 package，因此 discovery 使用与 Swift tests 相同的预算。整轮执行另有 50 分钟上限，先到的期限生效。

期限到达或收到 SIGTERM/SIGINT 后，runner 将当前命令记录为 `timed_out` / `interrupted`，最终 `summary.json` 为 `failed`。只对自己启动、仍由 `Popen` 持有的直属子进程发送 terminate，等待 5 秒后仍未退出才对同一子进程发送 kill，再有界等待 5 秒；不会向进程组、其他任务或 Simulator 服务发信号。命令证据保留 PID、开始/结束时间、期限、退出码及清理结果，已有日志和部分 `.xcresult` 不删除。普通非零退出和启动失败也保留失败记录。

SIGKILL、主机崩溃或平台强制取消无法由 Python 捕获，可能留下 `running` 状态和不完整结果；应将它们视为未完成，不能当作通过。父进程意外退出后，仍持有继承描述符的子进程继续占有锁，直到它退出。即使 CI 使用 `always()`，平台立即取消、整机终止或上传失败仍可能阻止 artifacts 保存；runner 不承诺终止所有由工具派生的服务，也不强行删除锁文件。

## Smoke 的具体方法

2026-10-16 已将本轮新增的三个回归登记进默认 smoke 与 CI：群名取消及未修改禁用保存、消息主动多选及取消、私聊/群发送后直接继续输入。对应方法为 `testMobileGroupNameEditorCancelsWithoutChangingNameAndDisablesUnchangedSave`、`testMobileGroupMessageSelectionOnlyAppearsAfterExplicitActionAndCanCancel`、`testMobilePrivateAndGroupSendKeepInputFocusForFollowingDraft`。本轮聚焦执行与完整 smoke 的结果分别记录，不将登记视为远程 CI 已通过。

| 风险路径 | `DiaryFlowTests` 方法 |
| --- | --- |
| 普通正文冲突逐段采用、编辑、退出恢复、保存及冷重开精确字节 | `testOrdinaryBodyConflictFragmentChoiceEditRecoveryAndSaveExactBytes` |
| 设置导出入口经真实系统文件保存并核验 Markdown 字节摘要 | `testSettingsExportsTheExactCurrentMarkdownThroughSystemFiles` |
| 已确认设置四组、恢复入口与日期回今天 | `testApprovedSettingsGroupsKeepDiaryAndRecoveryRoutesReachable` |
| 深色最大字号、主题筛选与周报告不可用 | `testApprovedMemoryFiltersAndUnavailableReportWithDarkLargeText` |
| 原正文编辑、冷重开精确原文 | `testOriginalHomeManualEditingSavesAndColdReopensExactSource` |
| 长正文确认、自动保存与写作位置 | `testOriginalDiaryConfirmationAndAutosaveKeepScrolledWritingPosition` |
| 双向切日、目录及页面往返阅读位置 | `testNavigationGesturesAndDestinationRoundTripsPreserveActualReadingPosition` |
| 双向浏览缺失未来日期，不创建文件 | `testBothSwipesPreviewMissingFutureOriginalDaysAndReturnWithoutCreatingFiles` |
| 缺失未来日期首次编辑与冷重开 | `testMissingFutureDayFirstEditSavesAndColdReopensWithoutChangingSource` |
| 保存失败与编辑手势不离开原文 | `testFailedSaveAndEditingGestureCannotLeaveDiaryOrLoseInput` |
| 表格横向与正文纵向手势 | `testOriginalTableHorizontalAndVerticalGesturesStayInSameDiary` |
| 单元格编辑、回车与加行加列、冷重开 | `testInlineTableReturnAddRowAddColumnAndColdReopenKeepExactSource` |
| 前台自动读取外部改动、继续编辑与冷重开 | `testActiveExternalFileRefreshAllowsContinuedEditingAndColdReopen` |
| 双方修改时选择文件/本机版本，保留手动合并入口 | `testCompetingExternalEditsOfferLatestAndLocalChoicesWithoutReauthorization` |
| 系统冲突冷打开、目录进入、选择版本及备份入口 | `testCloudConflictColdOpenDirectoryChoiceAndBackupRemainReachable` |
| 整份保留当前日记、无需逐段选择及冷重开 | `testWholeCurrentDiaryChoiceNeedsNoPerFragmentDecisionsAndResumesEditing` |
| 系统版本手动合并暂存、关闭与冷重开 | `testCloudManualMergeClosesAndColdReopensWithoutChangingCurrentFile` |
| 仅看差异、逐段采用并保留其他段落、冷恢复和最终差异确认 | `testCloudDifferencesShowOnlyChangesAndPerHunkChoicesPersistThenConfirm` |
| 只编辑差异片段，退出恢复、确认及冷重开保留整篇精确内容 | `testCloudDifferenceFragmentEditingKeepsOtherParagraphsAndColdReopensExactResult` |
| 实际 Home/activate 往返保存尚未自动保存的正文与表格 | `testHomeAndActivatePreservePendingBodyAndTableInput` |
| 未授权目录时四主入口 | `testUnselectedOriginalHomeShowsFolderActionAndFourMainTabsWork` |
| 设置切换保存与子页面返回 | `testSettingsMainTabSavesInputAndKeepsSubpagesAndRecordingsReachable` |
| 受控安装与四主入口 | `testMobileManagedInstallationConnectsWithoutLeavingFourPeerTabs` |
| 导师草稿冷恢复、回复、记忆纠正 | `testMobileMentorDraftColdRecoveryReplyAndMemoryCorrection` |
| 北极星显式关联、前后台恢复、跨导师和来源隔离 | `testMentorNorthStarOriginalParagraphPersistsAcrossChatsAndSourceSwitchClearsIt` |
| 最大无障碍字号、软件键盘和导师/圆桌操作可达 | `testMentorAccessibilityTextKeyboardAndRoundtableControlsRemainReachable` |
| 聊天保持打开，15 秒核验撤下外部变化目标并可取消关联 | `testMentorNorthStarExternalChangeInvalidatesOpenChat` |
| 结构化记忆主题、依据、纠正及旧摘要失效 | `testMobileMemoryOverviewThemeEvidenceCorrectionAndInvalidation` |
| 纠正确认期间来源变化，保留输入且拒绝旧修改 | `testMobileCorrectionConfirmationRejectsRemoteChangeAndRetainsInput` |
| 圆桌主持人、邀请及本地保存预览 | `testMobileRoundtableHostInvitationAndLocalSavePreview` |
| 圆桌停止后总结及冷启动离线草稿 | `testMobileRoundtableStoppedSummaryAndColdOfflineDraft` |
| 导师历史分页保留当前页与草稿 | `testMobileOlderHistoryKeepsCurrentPageAndDraft` |
| 记忆远端变化后的显式比较 | `testMobileCorrectionRequiresVisibleComparisonAfterRemoteChange` |
| 损坏档案保留健康内容及警告 | `testMobileCorruptArchiveWarningPreservesHealthyContentAndNeverClaimsEmpty` |

## 证据与失败排查

每次运行生成 `output/ios-tests/<UTC时间>-<suite>-<随机ID>/`，该目录被 Git 忽略：

- `summary.json`：最终状态、环境、实际设备/runtime、各目标的执行数量；`smoke` 与 `ui-smoke` 记录分片编号、总片数和该片精确 UI 清单，suite 字段保留二者区别，`ui-smoke` 没有 package 结果；失败记录原因。
- `commands.json` 与各阶段 `.log`：精确命令、PID、期限、终态、退出码及中断清理结果。stdout/stderr 直接写日志，不经过可能掩盖退出码的管道。
- `swift-inventory.json`、`swift-tests.log` 与 `swift-results.json`：Swift 实际发现清单与逐测试 started/passed 终态。兼容 Xcode 26/27 的 XCTest 命名格式，身份保留 target/class/method，同名类和方法在不同 target 中独立计数；无法审计的 test-shaped discovery 行直接失败，不假称支持 Swift Testing。逐项比对所有目标，末尾未选择 suite 的 `Executed 0` 不会覆盖前面的真实结果。
- `source-inputs-before.json` 与 `source-inputs-after.json`：执行前后对 runner 和 iOS Swift 源码/测试、Xcode project/shared scheme、非私有 build config、Info.plist、依赖锁定文件、asset 资源及跨语言 AI 来源 fixture 计算 SHA-256，包含未跟踪文件。新增、删除或字节变化均拒绝通过。排除缓存、生成目录、xcuserdata、隐藏配置、`*.local.*` / `*.private.*` 和 private/secrets/credentials 目录；不读取密钥或本地签名配置。这是前后内容快照，不是文件锁，也不覆盖 SDK、系统组件或排除的私人配置。
- `xcode-inventory.json` 与 `xcode-requested.json`：完整编译后 Xcode 枚举清单与本次选定清单。枚举会加载测试 runner，因此也只在专用设备执行。
- `tests.xcresult`、`xcode-summary.json` 和 `xcode-cases.json`：Native/UI 实际结果、附件及每个方法。汇总与完整结果树交叉检查；任一失败、skip、expected failure、重复、缺失或非目标设备都会失败。`enumeration.xcresult` 仅用于发现，不能当测试通过证据。
- 默认不启动 Xcode 额外的整机 Simulator 诊断收集，避免测试结束后卡在 `simctl diagnose`；测试结果包、用例日志、截图、录像和源码审计仍保留。需要系统级诊断时，针对故障设备单独采集并记录。

失败时先读 `summary.json`，再看对应阶段日志；Xcode 的 `.xcresult` 可以在 Xcode 中打开。缺 runtime 时在 Xcode Settings → Components 安装兼容组件。不要通过改断言、跳过测试、删除数据或换到手动设备获取绿色结果。日志可能包含本机绝对路径，分享前核对，不原样复制到仓库文档。runner 的故障与隔离回归可以跨平台执行：

```sh
uv run pytest tests/test_ios_test_runner.py
```

## CI

[独立 iOS workflow](../../.github/workflows/ios.yml) 在 PR 和 main push 将完整 smoke 分为一个 `core` job（当前 658 项 Swift package）与四个 `ui-smoke` job（每片完整 69 项 Native，UI 分别为 11/10/10/10 项）。五个 job 独立执行且全部必须成功，没有 `continue-on-error`；UI matrix 的 `fail-fast: false` 保留每片结果。每片有自己的专用 Simulator、锁、缓存，不在同一设备并发操作。artifact 名分别带 `core` 或 `ui-smoke-<片号>`，不能用某一片的成功代替 package 或其他片的结果。手动 `workflow_dispatch` 可选 smoke（同样的一个 core 加四片 UI）/full（单轮完整 package + Native + 全量 UI，不分片）。

只申请 `contents: read`，固定 `macos-15` 和 `/Applications/Xcode_26.3.app/Contents/Developer`，每个 job 上限 60 分钟，测试 step 上限 55 分钟，runner 内部上限 50 分钟，为正常结束后的证据上传留出时间。上传步骤使用 `always()`，结果保留 14 天；强制取消的限制如上。各 suite 继续逐项检查发现清单、实际执行结果、零跳过和源文件前后 SHA-256；`ui-smoke` 的 discovery 仍要求全部 41 项 smoke 存在，再核对本片精确请求集合。[Python workflow](../../.github/workflows/test.yml) 按当前发布范围在 macOS 与 Linux 执行隐私扫描、smoke 和完整测试；Windows 目前不纳入运行平台验收，其服务适配器的 fake-runner 单测继续执行。

2026-10-19 的[三分片运行](https://github.com/doublew6/riji-agent/actions/runs/39201422311)中，第一片在前置阶段用时约 23 分钟后触及 50 分钟整轮上限；第三片完整的群结果保存流程单项用时 7 分 18 秒，整片用时 44 分 35 秒。[随后的四分片运行](https://github.com/doublew6/riji-agent/actions/runs/39210419818)中，第四片前置阶段用时约 25.8 分钟，10 项 UI 与 69 项 Native 全部执行通过，但结果摘要读取仍被整轮截止时间中断，workflow 未通过。将重复的 package 阶段移到独立必过 `core` job 用于减少每片前置开销；用例、断言与 50 分钟上限保持不变，其他片的真实 UI 失败仍须单独修复，最终以新提交五个 job 的完整证据验收。

[GitHub 官方 macOS 15 runner image 清单](https://github.com/actions/runner-images/blob/main/images/macos/macos-15-Readme.md) 是 Xcode/runtime 配置依据。CI 按实际安装的兼容 runtime 选择设备；不存在时直接失败。`macos-15` 镜像仍会由 GitHub 更新，Xcode 路径变动应显式更新并验证 workflow。本机选中的 Xcode 与 CI 可以不同，实际版本写入每次证据，不能相互冒充。2026-10-18 的[首次云端运行](https://github.com/doublew6/riji-agent/actions/runs/39091642713) 在 Swift discovery 的旧 5 分钟限制处超时，尚未运行测试用例；延长预算后的结果须以新运行证据为准。

本次本机验证及探查记录见[2026-09-18 自动化证据](ios-automation-2026-09-18.md)。

2026-10-18 的[主分支运行](https://github.com/doublew6/riji-agent/actions/runs/39092125250) 虽通过 Swift discovery，但完整 smoke 在旧 50 分钟整轮预算内只完成 25/41 条 UI 流程，其中有 4 项失败。因此分片解决执行时长，具体 UI 失败仍需修复和实跑，不能用延长预算或分片代替断言验收。手动 full 若超时仍按失败处理。

圆桌和结构化记忆的电脑手动入口见[功能验收清单](ios-mentor-memory-manual.md)。这些合成演示与自动回归都不能替代 Air 部署或真机验收。
