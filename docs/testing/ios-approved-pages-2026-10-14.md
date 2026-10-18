# 日记、记忆与设置 v1.0 原生验收

日期：2026-10-14。范围为 Issue #60、[已确认 18 屏页面](../design/ios-pages-2026-10-14/proposal.md)。实现限定在独立 `codex/60-approved-ios-pages` worktree，以复制的当前未提交版本为基线；没有导入 #58 未核验实现，没有读取真实日记、部署 Air 或安装手机。

## 本次实现

- `ApprovedPageStyle.swift` 提供 SwiftUI/UIKit 共用语义浅深色与页面、列表、表单、行、不可用状态和主操作组件。导师仍使用独立视觉。
- 日记保持同一原文编辑器；动态 UIColor 与表格 CGColor 边线换色不重设正文/选择/撤销。目录顶部提供已下载文件搜索，方向页串联本人定义与当日 MIT，日期面板提供回今天，周月记分清周期、缺口和本人手动复盘。
- 普通正文冲突复用现有片段 diff。每次应用前比较 `Data(model.bodyMergeText.utf8) == expected`，保留完整原文、手动合并、最终差异和原写入保护。
- 记忆首页按已下载共享依据更新、真实主题摘要/分类筛选、独立周报告入口组织。沿用过期、授权、来源失效、离线和纠正确认检查；原分类保留在展开入口。
- 设置四组连接实际文件/模板、资料与 AI、任务与处理、导出与恢复页面。导出列明当前 Markdown 范围；录音、草稿、原操作与目录恢复有工作入口。

## 验证进度

| 检查 | 当前证据 |
| --- | --- |
| Simulator 编译预检 | `xcodebuild -project ios/Riji.xcodeproj -scheme Riji -destination 'generic/platform=iOS Simulator' -derivedDataPath output/approved-pages-build CODE_SIGNING_ALLOWED=NO build` 成功；预检早于最后测试增补，最终编译以 smoke 为准 |
| 完整 smoke | 首轮被中断，原证据 `output/ios-tests/20261014T103431Z-smoke-80a35c5b/`。正式 runner `output/ios-tests/20261014T130601Z-smoke-0f417f3b/` 完整执行：604 项 package、62 项 native 通过；34 条 UI 中 32 通过、2 条定位失败。修复后的定向补验全部通过，完整失败结果保留，不能称单轮 smoke 全绿 |
| 设计生成器 | 重新生成 18 屏，原批准布局不变；v1.0/确认日期/实际能力同步到 HTML 和 `screen-map.json` |
| 静态设计核对 | ID 无重复、页内和本地文档链接存在、18 个能力映射完整、无外部资源；嵌入 JavaScript 通过 `node --check` |
| 真实服务、iCloud、手机 | 本次未执行；合成模拟器不能替代这些验收 |

Smoke 包含全量 Swift package 的 Core/State/Probe support、全量 Native tests 和 34 条 UI journeys。原 30 条保留编辑、手势、表格、目录、冲突、导师隔离、记忆失效和回执覆盖；旧入口测试改走真实的新设置分组和直接主题摘要。新增：

- `NativeBodyEditorTests.testAppearanceChangePreservesSourceSelectionViewportAndUndo`：动态外观切换前后精确正文、选区、阅读位置、回调与撤销。
- `NativeBodyEditorTests.testAppearanceChangeRefreshesTableBordersWithoutReplacingComposition`：表格输入法组合文字、同一编辑实例与边线换色。
- `NativeBodyEditorTests.testOrdinaryBodyDiffRejectsStaleDraftAndSourceBeforeWriting`：实际普通正文差异适配器拒绝过期草稿；确认前源文件再次变化时保留外部文件和合并稿。
- `DiaryFlowTests.testOrdinaryBodyConflictFragmentChoiceEditRecoveryAndSaveExactBytes`：普通正文冲突采用片段并编辑、关闭冷恢复、最终确认与冷重开，逐字节核对未触碰内容、CRLF 与组合 Unicode。
- `DiaryFlowTests.testSettingsExportsTheExactCurrentMarkdownThroughSystemFiles`：设置新入口经真实系统“文件”保存，核验导出 SHA-256 与当前 Markdown 字节一致。
- `DiaryFlowTests.testApprovedSettingsGroupsKeepDiaryAndRecoveryRoutesReachable`：四组往返、草稿/录音恢复，先进入另一日期再从日期面板回到今天，断言日期与正文。
- `DiaryFlowTests.testApprovedMemoryFiltersAndUnavailableReportWithDarkLargeText`：最大无障碍字号、深色、主题筛选和报告真实不可用；保留日记、设置、主题、依据和报告页截图。

已有 UI journeys 继续保存浅色日记、目录、设置、记忆和差异截图。新的 DEBUG 外观参数只对 UUID 合成容器生效，并对导师保持深色；正常运行不强制外观。所有实际结果、截图与源码前后哈希保存在忽略目录。完整执行与最终补验结果见下文；截图人工复核已完成。

## 三轮评审修复与定向回归

- 搜索显示当前工作流实际范围；目录浏览原文件夹时不暗示搜索已经切换。方向页复用 `mitBodyChanged` 提醒并直达核对；日期面板采用可滚动的大面板。
- 记忆首页压缩依据/摘要，保留主题状态，按真正 `updatedAt` 标注更新日期并把 `observedAt` 单列为记录时间。详情保留全文；未接入的主题管理用次要说明呈现。
- 独立模板、导入、版本、复盘和云冲突确认页使用统一语义背景。周月记与原文件复用只读 Markdown 渲染。主按钮区分禁用状态，最大字号图标保留自然宽度。
- 导出恢复页显示云版本读取错误和重试；系统文件导出显示“上次导出”反馈。测试摘要来自既有 DEBUG 合成容器对实际导出文件重新读取后的 SHA-256，不是待导出的内存内容。
- 表格自身在新 traits 到达后刷新 CGColor，保持同一单元格实例、组合文字和撤销。完整版本 DisclosureGroup 的 AX 标识只放在标题，避免覆盖子编辑器。Native 测试在实际展开页面中要求可编辑且 UTF-8 原文完全匹配的唯一 UITextView，完整保留组合输入、明确捕获和保存断言；UI 仍按真实 AX 标识操作。
- 旧设置测试按真实四组路径调整。系统分享验收按当前系统的 Copy、Save to Files 和准确的原文预览检查，不依赖已变化的 `ActivityListView` 类型；明确 Copy 后继续重试失败保留和丢弃确认。

实际证据均位于忽略目录，失败结果保留，没有通过修改断言或删除结果伪装首轮通过：

| 定向运行 | 实际结果 |
| --- | --- |
| `20261014T110202Z-approved-pages-targeted` | 18 项中 14 通过、4 失败；失败为 Native 编辑器定位、系统分享旧元素类型、完整版本子编辑器 AX 标识和设置导出状态路径 |
| `20261014T111927Z-approved-pages-targeted` | 修复后 5 项中 4 通过；Native IME、最大字号、普通正文片段编辑→冷恢复→精确字节保存/冷重开、设置真实文件导出均通过。分享同时要求背景/浮层各有 StaticText 的断言不符合实际 AX 结构，继续修正定位 |
| `20261014T112422Z-approved-pages-targeted` | Xcode 2/2 通过：普通记忆纠正流程、系统分享与恢复/丢弃。临时定向包装器误要求 Native+UI 双包，原 `summary.json` 保留 failed；`targeted-audit.json` 单独核对全部请求身份、零失败/跳过、专用设备和源码前后未变化，结果通过 |
| `20261014T125832Z-approved-pages-targeted` | 记忆首页改用行内标题后，1/1 UI 通过、零失败/跳过；请求身份、专用设备与源码前后哈希核验通过 |
| Python runner 注册定向 | 本任务 4 passed；父任务在同一 worktree 上用既有项目环境运行完整 `tests/test_ios_test_runner.py`，报告 80 passed；两者单独记录，不能代替 iOS 完整 smoke |

普通记忆首页在等待标题 AX 可见后仍出现空白大标题区，稳定截图确认问题后改为原生行内标题；最终单条补验通过，并人工复核新截图标题正常显示。深色最大字号的新设置截图已确认图标不再重叠。浅色日记、文件与 AI 设置、记忆首页，以及深色最大字号日记与依据页均已人工复核。截图与映射保存在 `output/approved-pages-visual/`。上述早期定向执行本身不代表完整 smoke；最后完整执行与补验见下一节。真实服务、手机和真实 iCloud 未由本轮覆盖。

## 最终验证与整合

2026-10-14，在专用 `Riji Automated Tests` 模拟器（iOS 27）使用合成数据完成：

| 检查 | 结果 |
| --- | --- |
| Swift package | 604 passed：Core 300、State 299、Probe support 5；零失败、零跳过 |
| Native | 62 passed；覆盖外观切换、组合输入、撤销、原文保护和过期差异拒绝 |
| UI smoke | 34 条分轮通过。完整执行先为 32 passed / 2 failed，随后修复测试定位并补验；没有改写原失败结果 |
| 最终定向补验 | `20261014T135559Z-approved-pages-targeted` 3/3 passed：冲突片段→编辑→冷恢复→最终保存→冷重开，以及日记/设置两个真实文件导出入口；`20261014T140045Z-approved-pages-targeted` 2/2 passed：最终严格文件名等待与两条导出 SHA-256 核验 |
| Python runner | `tests/test_ios_test_runner.py` 80 passed；单独记账，不计为 iOS 用例 |
| 人工视觉复核 | 浅色日记、记忆、文件/AI 设置；深色最大字号日记、记忆依据与设置。修复空白记忆标题和最大字号图标重叠后复核 |

最后两处失败的实际截图与 AX 树显示：长列表中的 DisclosureGroup 外层按钮没有有效激活点，测试改点真实标题；系统文件视图与文件名控件异步出现，先等待保存面板再严格等待文件名，仍使用唯一名字并校验实际导出文件，未跳过导出或改用内存摘要。

每轮均核验 115 个源码输入在运行前后不变。完整执行至最终补验之间，生产源码完全一致，仅上述 UI 测试定位变化。合并核验结果保存为 `output/approved-pages-combined-verification.json`，逐个用例身份核对后才记为“分轮通过”。测试源与 `.xcresult` 完整证据保留在隔离工作树的 `output/ios-tests/`；主工作目录保留摘要、截图和整合清单于 `output/ios-approved-design-2026-10-14/`。

本次按开工快照逐文件核对并整合当前工作目录，只同步本任务 30 个文件；保留既有未提交改动和 Git 暂存内容。规范及已确认页面同步到本地 Obsidian 项目归档，未据本地写入声称其他设备已完成 iCloud 同步。没有安装手机、更新 Air 或发布代码。未创建本地 commit：当前功能依赖既有未提交实现，不能把另一任务工作混入本次提交。

## 服务依赖与限制

#58 仍 in-review，本次使用 #57 已有合同。稳定主题改名/置顶/历史时间线、真实记忆周复盘、结构建议接受/拆分/延后、远端任务时间与启停、AI 生成日记周月复盘、完整备份恢复均明确不可用。可从相关页面进入现有来源纠正、手动工作流或逐项导出恢复，不提供假开关、示例结果或成功回执。

本人工作流目标版本与导师原文段落的来源分别说明，不因页面合并而改写任一来源。原目录下的录音恢复通过显式切回原有 App 日记后进行，不自动迁移文件或改变录音权限。目录时区按现有模型只读展示；星期语言沿用原确认路径。
