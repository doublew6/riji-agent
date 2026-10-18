# iOS 首个导航增量：开发与验证记录

> 2026-09-18 日期手势约定已由用户修正：手指从左向右滑动打开前一个日历日，从右向左滑动打开后一个日历日。下文保留当时的单向设计及验证证据；当前约定见 [FR-05](../PRD.md#fr-05) 与[双向滑动验证记录](ios-bidirectional-swipe-2026-09-18.md)。

> 本页保留首个增量的历史证据。用户随后明确设置应与日记、导师、记忆在底部并列，已取代下文的目录固定栏设计。当前操作及验证见[设置入口修正](ios-settings-tab-2026-09-15.md)。

日期：2026-09-15。对应 [Issue #53](https://github.com/doublew6/riji-agent/issues/53)、[开发计划](../product/ios-personal-mvp-development-plan.md)中的 01 本地探针与 02 导航。本文记录证据，不表示 01 的真机文件验证或整个个人 MVP 已完成。

## 基线与范围

- 分支：`codex/53-ios-diary-navigation`；基础提交：`77c2c9b231d4e0280b18e3a0ca7270ba4616d5b5`。
- 本轮开始前，65 个已修改/未跟踪源码文件及原 index diff 已按字节备份；个人模拟器空间的既有日记、模板及状态文件已备份并记录哈希。私人内容仅存忽略目录。
- 复用固定的 `Riji Development iPhone`、Swift scratch 和 Xcode derived data。没有擦除模拟器、卸载主 App 或迁移外部目录。
- Xcode 26.6，iOS Simulator 26.5。基础 Core/State：**299 项通过，0 失败**，231.645 秒。该结果是增量前的回归基线。
- `devicectl` 返回一条已配对 iPhone 记录，记录显示 iOS 26.6.1、开发者模式关闭、设备连接隧道不可用。它不是当前可安装/调试或真机验收通过的证据；本轮没有变更手机设置或安装应用。
- 本轮不连接模型、不修改 Air、飞书、Hermes、Mem0 或已有任务。

## 本地文件可行性探针

使用 Foundation `NSFileCoordinator` 在新建的临时合成目录执行，结束后仅清理该临时目录。未读取或写入真实日记目录，也未启用 App 外部写入。

| 验证 | 结果 |
| --- | --- |
| 协调读取保留 UTF-8、CRLF、组合字符和 Markdown 引用 | 通过，原始字节一致 |
| 带基准核对的协调原子替换 | 通过，未编辑前缀不变 |
| 相同正文重复保存 | 通过，不重复追加 |
| 外部已修改，再以旧基准写入 | 拒绝；外部版本保留 |
| 外部删除，再尝试保存旧编辑 | 拒绝；没有创建空文件或复活原件 |
| 不合法 UTF-8 | 拒绝解析；原始字节不变 |
| 原始文件写入者在基准检查与替换之间插入写入 | **已重现限制**：协调本身不能防止这类未协调写入被后一次替换覆盖 |

最后一项是限制的复现，不能记作“并发安全通过”。下一步外部写入实现需要将版本、冲突证据与未同步草稿保留纳入同一个文档生命周期。真机目录授权、iCloud 下载与冲突版本、移动/撤权、后台中断和跨设备行为均尚未验证。

实现时须使用协调回调提供的 URL；系统可能调整实际位置。目录选择通过系统授予的 security-scoped URL，不能以开发机路径或模拟器访问成功替代真机授权。[Apple 文件协调文档](https://developer.apple.com/documentation/foundation/nsfilecoordinator/coordinate(readingitemat:options:writingitemat:options:error:byaccessor:))、[Apple 目录访问文档](https://developer.apple.com/documentation/uikit/providing-access-to-directories)。

## 本机证据

日志、工作区备份、个人数据备份及一次性探针保存在 Git 忽略的 `output/ios-personal-mvp-implementation-2026-09-15/`。其中 `coordinated-file-probe.swift` 只用于上述可行性实验，不是生产文件写入组件，不作为 App 构建输入。

## 实现与独立复核

已完成左滑前一个日历日、按日期保留当前会话的光标/阅读位置、日记／导师／记忆三个主入口，以及目录底部固定设置。未连接的 Air 能力如实显示不可用。正文编辑、原模板、只读外部文件树、导出和旧工作流继续使用原实现。

三层独立复核均完成，逐条裁定保存在 `spec-53-ios-diary-navigation.md`。修正了新建空白日记后返回列表未刷新的问题：App 日期列表随页面出现刷新，外部文件树仍按整个目录会话维护。补充正常正文中文组合输入的提交/失败保留测试、两个实际日期选择器的时区边界测试，并将新视口测试的固定延迟换为有界条件等待。原有测试方法和断言保留。

复核保留的限制：阅读位置按当前排版的像素位置恢复；改变字号/宽度后的段落锚点、复杂曲线拖动及完整表格交互未实现。含竖线的普通行也保留给文本交互，可换一行开始左滑或使用日期选择器。极早纪元边界不在本轮日常日期验收内。大字号/VoiceOver 导航效果仍待实测，已登记为未验证项。

## 最终验证

| 验证 | 最终结果 | 本机日志 |
| --- | --- | --- |
| 全部 Core/State | **304 通过，0 失败**，224.485 秒 | `navigation-root-final-swift.log` |
| 原生正文及既有语音组件 | **32 通过，0 失败**，9.609 秒 | `navigation-root-final-xcode.log` |
| 8 条关键实际 App 界面流程 | **8 通过，0 失败**，340.203 秒 | 同上，整轮 `TEST SUCCEEDED`，exit 0 |

界面流程覆盖：切日与位置返回、保存失败不离页、长目录与固定设置、原文件只读浏览、Markdown 编辑与导出、模板/MIT/周报、创建空白日记后返回两层列表，以及 São Paulo / Apia 特殊日期的真实选择器确认和空白预览。测试使用合成空间，没有请求麦克风权限。32 项原生测试包含原有语音组件回归，不表示语音整体或真机识别已验收。

Core/State 命令为 `swift test --package-path ios --scratch-path /tmp/riji-ios-swift-build`。Xcode 使用固定 `Riji Development iPhone`、现有临时 derived data、`-parallel-testing-enabled NO` 和 `CODE_SIGNING_ALLOWED=NO`；精确 8 个 UI 过滤及全 Native 目标保存在日志第一条命令。结果包为 `Test-Riji-2026.09.15_14-07-14-+0800.xcresult`，截图及索引在 `navigation-root-final-ui-evidence/`。

前面的失败尝试没有删除：最初的原生滚动识别和过早手势阈值问题已修正；测试也修正了无真实触摸时模拟 pan 位移无效、后台 sheet 元素作用域和编辑结束条件。较早混合测试的总体失败不能替代上述最终通过记录。

Mac 的额外 CUA 手动走查遇到锁屏，已请求用户解锁，未绕过锁屏；Xcode Simulator 的实际界面测试和截图证据独立完成。这不是实体 iPhone 验收。当前试用操作见[手动指南中的六项检查](ios-manual-acceptance.md)。

## 最终交接与保留检查

`./scripts/run_ios_simulator.sh --sample` 最终构建、安装和启动成功，记录的 App 进程仍在运行。它复用原 App 和固定样例空间。原个人空间 **全部既有文件保持哈希一致，没有缺失或新增普通空间文件**；日记、原模板、录音与状态继续保留。

本轮新出现的 `com.riji.local.uitests.xctrunner` 已先打包备份，并逐一核验包内 38 个普通文件后精确卸载。Simulator 中 Riji 只保留主 App 注册；没有抹掉设备、删除主 App、清理其他应用或删除测试证据。原 Git index 和两个无关隐私文件与基线完全一致。混合工作区没有批量暂存或提交，Air 服务未变更。

用户追加的 `design-mobile-apps` 参考已落实为[视觉设计约定](../product/ios-visual-design-direction.md)，用于后续排版和界面细化。本轮没有调用 Sleek 生成服务，不把设计方法的参考标成新视觉稿已经验收。

### 个人空间交接纠正（同日后续）

用户看到“产品初始模板”后反馈并非原模板。核实原因是上一次交接停在样例空间，个人空间中已选原模板仍完整保留。已通过普通启动入口重新构建并切回个人空间；CUA 此时可用，实际页面显示导入的个人模板与原目录，目录底部固定设置可见。模拟器最终停在个人目录页。

个人模板与此前备份逐字节完全一致。切换前分别备份普通空间与固定样例空间，切换后的 **全部既有文件保持哈希一致、无缺失**。证据存于忽略目录 `output/ios-personal-mode-2026-09-15/`，不包含新的业务代码改动。手动指南已明确：合成测试结束后，个人体验交接默认返回普通空间。
