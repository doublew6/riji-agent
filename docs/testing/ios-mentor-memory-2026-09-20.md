# 圆桌与结构化记忆：集成验证

本记录对应用户在 2026-09-20 批准开发的 #56 和 #57。本地代码、接口、演示入口和回归于 2026-09-21 完成。自动化结果、Air 部署、手机安装和本人体验验收分别记录；本轮未部署或安装到手机。

## 可体验的功能

- 导师页顶部进入圆桌，先与主持人交流，再邀请 2–4 位导师讨论；可查看材料、停止、总结、追问及历史。保存选定结果前先预览，写入独立的 AI 区域，重复确认不会重复追加。
- 记忆首页按六个主题组织，再进入摘要与依据；导师观察、待复核比较、最近变化和原始记录分别可达。纠正依据后旧摘要立即失效，重新整理显示实际状态。
- 四个主标签保持“日记、导师、记忆、设置”。两个功能沿用受控的来源、设备权限与回执；不会把手机对话转发到飞书。

电脑端入口、操作清单和合成数据限制见[手动验收说明](ios-mentor-memory-manual.md)。演示不访问真实日记，也不调用真实模型；它用于核对交互，不能证明 Air 或手机已更新。

## 实际验证

| 检查 | 结果 | 本地证据 |
| --- | --- | --- |
| 审查修复后的完整 Python 回归 | 2211 passed、5 skipped；源码及共享 fixture 哈希未变 | `output/ios-mentor-memory-2026-09-20/python-final.xml`、`python-final-audit.json` |
| 新接口的代理分页与查询限制 | 15 passed，已纳入上述完整回归 | `output/ios-mentor-memory-2026-09-20/roundtable-list-proxy.xml` |
| 审查修复的原生定向测试 | 38 passed；最终修改已纳入下列完整 Swift 回归 | `output/ios-mentor-memory-2026-09-20/review-fix-native.log` |
| 最终实际 App 编译 | passed，包含最后的记忆焦点修复 | `output/ios-mentor-memory-2026-09-20/completion-ui-batch-1/xcode-tests.log` |
| 两条圆桌 UI 流程 | 2 passed，0 failed/skip，包含在下方 27 条中 | `output/ios-mentor-memory-2026-09-20/verified-ui-batch-1/` |
| 最终 smoke 注册及诊断收集调整后的测试工具回归 | 80 passed | `output/ios-mentor-memory-2026-09-20/runner-diagnostics.xml` |
| 最终完整 Swift Core/State/ProbeSupport | 587 passed、0 failed/skip（295 Core、287 State、5 ProbeSupport）；105 个输入哈希未变 | `output/ios-tests/20260920T164038Z-core-a91232bf/` |
| 单次完整 smoke | 中断，不计为通过；模拟器在重开 App 时卡于系统加载器，随后无法终止该进程以启动下一条 | `output/ios-tests/20260920T154930Z-smoke-00075973/` |
| 完整 Native 组件测试 | 59 passed、0 failed/skip；结果包清单及源哈希审计通过 | `output/ios-tests/20260920T160838Z-native-bb248d62/` |
| 分批 UI 复核 | 27 个不同用例均有 passed 证据，0 skipped；20 条先通过，View 焦点修复后重新通过全部 7 条手机服务流程 | `output/ios-mentor-memory-2026-09-20/completed-ui-audit.json`，`verified-ui-batch-1` 至 `-5`，`completion-ui-batch-1` 至 `-2` |
| 最终电脑手动入口 | `--memory-overview` 编译、覆盖安装和启动成功；复用既有开发模拟器，进入后点击“记忆”，只读合成服务 | `output/ios-mentor-memory-2026-09-20/final-manual-launch.log`、`final-manual-launch.png` |

5 个 Python skip 是 4 个未配置官方 Codex CLI 路径的可选 wire check，以及 1 个未配置本地浏览器运行时的可选 browser acceptance。没有将这些检查视为通过；两条已有的 websockets 弃用警告不影响已执行用例结果。

27 项是分批汇总结果，不是单次 smoke 成功。每个引用批次的用例清单、设备和执行前后源码审计均通过；最后两个批次与前五批仅有 `MobileViews.swift` 的纠正框焦点、`DiaryFlowTests.swift` 的新增键盘/标签断言差异，汇总审计明确保存两份输入快照。完整 Swift 回归之后也只有这两处 View/UI-test 修改，Core/State/ProbeSupport 实现及测试未变。59 项 Native 在最后 AI 状态修复前执行，Native 组件及其用例未变；这项阶段证据不替代之后的完整 State 和 UI 验证。

## 审查及修复

详细的逐项判定保存在两份实施规格的 Review Triage Log。审查发现并修复了完成回执丢失结果 ID、旧历史页无法选材、邀请预览迟到响应、撤权后请求失效、离线入口及缓存恢复、目标日记锁定与本地取消、停止后总结、首条计划/反馈类型、历史分页以及部分记忆页误报为空的问题。补强了本地保存的精确内容及冷启动不重复断言。

一个盲审来自新的独立上下文；运行时拒绝创建更多 agent 后，边界与测试证据检查复用了已有 agent。没有把它们称为三个独立盲审。审查比较以本轮开始时捕获的工作区为基线，保留此前未提交修改；没有批量暂存、提交或推送。

先前失败运行全部保留。`roundtable-ui-4` 在唤起键盘时卡于模拟器系统剪贴板的同步请求，经进程采样后中断，仅重启既有专用测试模拟器；未清除 App 或模拟器数据。这次中断不计为通过。

重启后的 `roundtable-ui-5` 两项均通过：邀请、选择、成功保存后的精确内容、冷启动后只出现一次，以及停止后部分总结、冷启动离线草稿和离线写入禁用。已人工检查[实际保存预览截图](../../output/ios-mentor-memory-2026-09-20/roundtable-ui-5/attachments/B034D86E-F80D-4F04-BAD6-CCC43AEDBB84.png)，页面显示 AI 区域、目标日期与确认按钮，不显示原始 provenance 标记。

完整 smoke 运行中，前 5 条 UI 已通过日志断言；第 6 条在冷启动时长期等待，采样显示进程仍处于 `dyld3::preflightCacheFile → __mmap`，没有进入 App 主函数，随后下一条在 setup 无法终止同一进程。保留采样及失败日志后中断该任务自己的 runner。中断结果包未完整封装，因此不把这些日志中的通过项作为最终 UI 清单证据；Native 单独执行、UI 按每批最多 4 条重新执行，批次间仅重启原专用测试设备，保留全部数据。完整 smoke 本次中断的状态保持不变，不用后续分批通过改写它。

Native 独立运行的 59 项全部结束后，Xcode 的额外 `simctl diagnose` 收集继续等待；采样确认后，核对父进程、测试设备、工作目录和本次输出路径，仅向该诊断子进程发送 SIGINT。没有中断测试或修改断言；随后 xcodebuild 正常 exit 0，完整结果包、59 项实际结果和源码审计均通过。系统诊断附件不完整，已有日志与进程采样保留。

首批分组运行 `final-ui-batch-1` 发现两个问题：一条原目录夹具启动后仍为空，尚未进入选定目录；另一条日期回切明确丢失滚动位置（0 对比预期 408）。后者追踪到新增的 AI 预览读取在发布日期与正文之间发生 `await`，破坏原来的成组更新。修复将读取前置，同时补查等待期间换日期/换目录的旧结果准入。该批次停止，保留失败，不调整正文或阅读位置断言；最终修复后重新冻结源码并执行全部 27 条 UI 清单。

补充修复为本地 AI 恢复、预览、确认和取消绑定 store、目录身份、日期、正文代次及请求代次。成功切源清理旧可操作预览，失败切源保留原状态；文件写入后回执报错先只读核验，再报告实际结果。新增 8 项受控并发 State 测试，与既有圆桌 16 项共同通过。限定补充审查还发现旧语音未知结果分支的异步读取位于发布阶段，已一并前置；审查者只读确认该点关闭，最终源码的完整 Swift 587 项通过。没有修改语音写入内容或恢复规则。

完整 Python 回归结束后，只有测试 runner 的 UI 清单及系统诊断选项变更；后端、Python 测试和共享 fixture 未改动。runner 已单独通过上述 80 项回归。Xcode 默认不再采集额外整机诊断，保留实际用例结果、截图、录像及输入哈希校验。

`verified-ui-batch-1` 至 `-5` 的 20 项全部通过，覆盖日期阅读位置、两条圆桌、正文/表格、外部更新、冲突与四标签导航。第 6 批的结构化记忆、远端纠正变化和预配置连接已通过，但原有单导师流程在记忆纠正成功后仍弹着键盘，遮住“设置”，因此该批次保留为失败。截图确认这是实际焦点问题；在 `MobileMemoryDetailView` 打开比较/确认前清除输入焦点，并增加提交后键盘消失及设置可点击断言。只改该 View 与该用例，Core/State/后端未变；全部 7 条手机服务流程重新执行，20 条未受影响的已封装证据保留，分阶段输入差异在汇总审计中明确记录。

最终 7 条全部通过，包括上述键盘断言、远端版本比较、主题/依据/纠正/整理、单导师草稿冷恢复、历史分页，以及部分存档损坏时保留健康内容。已查看最终[记忆依据页面](../../output/ios-mentor-memory-2026-09-20/completion-ui-batch-1/attachments/5F47E180-18F5-4C7B-BFD4-B58F7586BF4D.png)、[纠正完成且键盘关闭页面](../../output/ios-mentor-memory-2026-09-20/completion-ui-batch-1/attachments/47D16E1C-DFCF-40B3-8293-3C31948FA664.png)及[圆桌保存预览](../../output/ios-mentor-memory-2026-09-20/verified-ui-batch-1/attachments/FFC568CC-7D52-47E7-BA80-4004635D7A9C.png)。均为实际 App 的合成数据截图，不代表真实模型或用户记忆质量。

## 发布边界

**2026-09-21 后续：** Fleet 检查已恢复，Air 更新及手机覆盖安装、正常启动已完成；见[发布核验记录](ios-mentor-memory-release-2026-09-21.md)。以下保留本地验证结束时的部署阻塞现场，不代表当前部署状态。真实内容质量验收仍单列。

只读 SSH 已确认目标为 MacBook Air；Fleet 部署前检查连续三次超时，未取得最新登记和心跳核验。按项目的 Air 部署规定，没有继续同步源码或重启服务，也没有重启 Fleet 或共享 Colima。等待 Fleet 检查恢复后，再完成备份、差异核对、Air 测试和受控更新。

本轮未向手机安装新版，尚未验收真实模型输出、手机到 Air 链路、真实记忆摘要质量、真机 iCloud、大字号、深色模式和 VoiceOver。已有 iPhone 版本不会因本地代码完成而自动出现新功能。

各功能较早的验证与失败修复过程分别见[圆桌记录](ios-roundtable-2026-09-20.md)和[记忆记录](ios-memory-overview-2026-09-20.md)；本页用于汇总最终集成结果。
