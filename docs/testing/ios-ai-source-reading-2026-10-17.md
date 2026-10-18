# 保存后 AI 建议的阅读体验修正

本轮处理[上一轮走查发现的来源 JSON 占满日记问题](ios-mentor-usability-2026-10-17.md)。沿用已确认的日记视觉规范，不改变导师页面布局。

## 本轮行为

- 日记正常阅读保留建议正文、AI 标识与性质提醒，收起内部 JSON、英文导师编号和讨论编号。
- 从“更多 → AI 讨论来源”查看中文导师、话题、讨论时间、保存日期及“保存前用户编辑”状态；主动展开才显示完整的只读 Markdown。
- 折叠信息不能被输入或删除误改；建议正文仍能换行、编辑和撤销。来源页先提交中文输入，再读取当前内容；原文件变化后，旧来源快照失效。
- 原始 Markdown 不迁移、不重写；不完整、未知或代码示例中的来源记录保持原样。通用文件预览与结果确认预览不启用此折叠。

## 验证范围

仅使用 Riji Automated Tests · iOS 27.0，所有文件、导师回复与讨论均为 UUID 隔离的合成数据。未连接真实模型、Air、飞书、真实日记或手机。

[核心测试日志](../../output/ai-source-reading-2026-10-17/core-final.log)：64 条相关 Core/存储/正文版本测试通过，0 失败。[最终 Xcode 日志](../../output/ai-source-reading-2026-10-17/final-native-ui.log)与[结果包](../../output/ai-source-reading-2026-10-17/final-native-ui.xcresult)：13 条 Native、2 条完整 UI 流程通过，0 失败。群聊保存 139.970 秒；逐项结果见[测试清单](../../output/ai-source-reading-2026-10-17/final-tests.json)。

原生编辑器与来源 session 测试验证字节、偏移、保护、中文组合、撤销、表格及文件更新导致快照失效。实际来源弹层的呈现、展开和关闭由完整 App 的 UI 测试验证；无界面 Native 宿主无法完成 iOS 27 弹层动画，不将其 presented 指针视为可见页面。

## 首轮失败与修正

保留[首次 Xcode 结果](../../output/ai-source-reading-2026-10-17/native-ui.xcresult)、[日志](../../output/ai-source-reading-2026-10-17/native-ui.log)及后续 native-repair、native-source-lifecycle 结果。首次编译成功，但 4 条 Native 与 2 条 UI 用例失败，未被计为通过。

- UIKit 原生 AX getter 可能返回 nil：显式回退到实际原文。
- UIKeyInput 的程序调用可绕过 delegate：隐藏字段保护同时覆盖插入和删除入口。
- 来源测试的 hostless 弹层转场不完成：将 session 行为与实际 App UI 呈现分层验证。
- 展开原文的录像显示成功，但 AX 归档中子 Text 的 ID 被父 DisclosureGroup 覆盖：改为按钮与原文并列，保留各自独立 ID，没有放宽完整原文比较。

实际 VoiceOver 朗读、真机触控与听写、iCloud 传播及真实服务仍需独立验收。本轮结果不代表全套 Core 或全部 App 回归通过。


## 实际页面与完整性

同一份既有合成日记的修正前后截图；未重绘、裁剪或拼接。

| 修正前 | 修正后 |
|---|---|
| ![内部来源占据日记](../../output/ai-source-reading-2026-10-17/before.png) | ![收起来源后的建议正文](../../output/ai-source-reading-2026-10-17/after.png) |

[中文来源摘要](../../output/ai-source-reading-2026-10-17/sources.png) · [明确展开后的只读原文](../../output/ai-source-reading-2026-10-17/raw-expanded.png)

两条完整 UI 流程均从真实确认页读取完整 block，再从来源页读取完整原文，校验每行引用、仅一次追加、取消零追加、人工文字保留及冷启动全文字节一致。来源默认不展开原文，查看和关闭后正常阅读值保持一致。

手工打开既有合成日记，进入来源、展开原文并关闭后，文件仍为 1169 字节，SHA-256 与修正前一致；见[只读检查](../../output/ai-source-reading-2026-10-17/synthetic-after.json)。最终构建到回归结束之间，106 个相关源码/配置文件的哈希均未改变，见[源码核对](../../output/ai-source-reading-2026-10-17/source-verification.json)。最终独立代码复核未发现未解决的确定故障。

模拟器停在修正后的正常日记阅读页。改动保留在本地共享工作区，未提交、推送、部署或安装到手机。
