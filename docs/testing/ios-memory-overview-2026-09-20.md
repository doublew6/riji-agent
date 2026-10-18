# iOS 结构化记忆概览本地验证 · 2026-09-20

2026-09-21 的 Air 更新、真机安装及公网验证见[发布核验记录](ios-mentor-memory-release-2026-09-21.md)；下文保留此前本地验证阶段的边界。

这是分阶段开发记录，保留当时的结果与待办；修复后的统一状态见[集成验证记录](ios-mentor-memory-2026-09-20.md)。

范围：[实施规格 #57](../../_bmad-output/implementation-artifacts/spec-57-mobile-memory-overview.md)。所有数据均为临时目录或原生测试夹具中的合成内容；未连接 Air、读取真实日记、修改生产记忆或调用模型。

## 实现

- 新增经过来源、版本、范围及用途授权复核的概览投影和有界整理请求，复用现有 operation receipt、整理队列和停止账本。GET 不触发模型或排队；本地及公共代理只放行明确的新路由。
- 记忆首页先显示六个主题，提供主题摘要、证据、导师观察、比较待复核、最近变化和深层原始记忆入口。证据继续使用原有来源查看、纠正、复核及归档/删除流程。原始记录搜索明确只搜索当前页；最近变化明确只包含已读取概览的证据。
- 新增类型化分页状态和独立限时缓存。同版本刷新保留已加载后续页；版本变化重新加载。纠正、已知来源或权限变化会立即隐藏正在查看的派生文本。独立持久化准入摘要先于缓存删除失效，删除失败或恢复旧文件后也不能在冷启动恢复旧摘要。
- 纠正确认绑定所查看记录的版本；确认期间远端变化禁止提交旧候选并保留输入。整理超时通过原操作 ID 对账。离线副本不能授权写操作。
- 前台和下拉均可刷新；pending/processing 时每 15 秒刷新，空闲每 60 秒刷新。缓存到期另有计时器，不等待轮询。

## 实际执行

| 验证 | 结果 | 本机证据目录 |
| --- | --- | --- |
| 后端概览、Mobile API、代理、记忆回归、公共协议及 journal organization | 108 passed；2 条既有 websockets 弃用警告 | `output/ios-mentor-memory-2026-09-20/memory-overview-python.xml` |
| 修改后完整 Swift Core/State/ProbeSupport | 552 passed，0 failed，0 skipped；95 个输入文件审计 unchanged | `output/ios-tests/20260920T133635Z-core-cd8c5fdf/` |
| 确认状态修复后 5 条 focused UI | 4 passed；概览完整链路在回执可见性断言失败 | `output/ios-mentor-memory-2026-09-20/memory-overview-ui-2/` |
| 回执位置修复后的完整概览链路 | 1 passed，0 failed/skip；95 个输入文件审计 unchanged | `output/ios-mentor-memory-2026-09-20/memory-overview-ui-3/` |

后端命令：

```sh
.venv/bin/python -m pytest tests/test_mobile_overview.py tests/test_mobile_api.py tests/test_mobile_proxy.py tests/test_mobile_memory_regressions.py tests/test_mobile_public_protocol.py tests/test_journal_organization.py --junitxml=output/ios-mentor-memory-2026-09-20/memory-overview-python.xml
```

原生命令：

```sh
.venv/bin/python scripts/test_ios.py --suite core
.venv/bin/python output/ios-mentor-memory-2026-09-20/run_focused.py --label memory-overview-ui-2 --test RijiUITests/DiaryFlowTests/testMobileMemoryOverviewThemeEvidenceCorrectionAndInvalidation --test RijiUITests/DiaryFlowTests/testMobileCorrectionConfirmationRejectsRemoteChangeAndRetainsInput --test RijiUITests/DiaryFlowTests/testMobileManagedInstallationConnectsWithoutLeavingFourPeerTabs --test RijiUITests/DiaryFlowTests/testMobileMentorDraftColdRecoveryReplyAndMemoryCorrection --test RijiUITests/DiaryFlowTests/testMobileCorrectionRequiresVisibleComparisonAfterRemoteChange
.venv/bin/python output/ios-mentor-memory-2026-09-20/run_focused.py --label memory-overview-ui-3 --test RijiUITests/DiaryFlowTests/testMobileMemoryOverviewThemeEvidenceCorrectionAndInvalidation
```

新增概览测试包含 38 个后端用例、5 个 Core 用例和 9 个 State 用例。覆盖 legacy 顶层版本清单、完整组失效、真实独立日期与反例、缺失 journal engine 时拒绝旧 journal 证据、导师隔离、返回前竞态、真实停止账本、页版本变化、第二页同版本刷新、迟到请求、超时冷启动对账、已知撤权冷启动及注入缓存删除失败后真实磁盘重读。

Core 分项为 RijiCoreTests 284、RijiStateTests 263、RijiICloudProbeSupportTests 5。focused UI 覆盖主题→摘要→依据→纠正→失效→请求整理、确认期远端变更、四个主 Tab、旧服务原始记录入口、草稿冷启动恢复与现有版本比较流程。

## 失败修复记录

初次编译因测试夹具的 String/NSNull 分支类型不一致失败，记录保留在 `output/ios-tests/20260920T132253Z-core-9f4daf23/`。修复后曾通过 550 项；随后修改了确认弹窗状态，因此该旧结果不作为最终验证。

第一轮 focused UI 中 2 项通过、2 项失败，证据保留在 `memory-overview-ui-1/`。实际可访问性树表明独立的 sheet 和提交状态在呈现时不一致，错误显示了重新比较模式。现将确认对象、版本与模式绑定到同一个 sheet item，并新增确认期间远端版本变化的页面回归。上述 552 项 Core 是该修复后的重新执行结果。

第二轮确认竞态、原始记忆版本比较、四 Tab 和导师草稿/记忆纠正均通过；概览链路已完成纠正、摘要即时隐藏及整理请求，但列表重新加载回到顶部，底部回执尚未进入可访问性树，最后的回执断言失败。将回执移入顶部状态区，并使概览暂不可用时也能查看/对账；测试增加滚动核验及首页、回执截图。此后仅改 View 布局和该 UI 测试，Core/State/后端文件未再变化，对应路径单独重跑通过。最终 5 条指定路径均有通过结果，不将失败轮次记作全绿。

已导出并人工查看最终运行的三个截图；默认浅色及普通字号下，主题、摘要和证据入口可读，四 Tab 保留，纠正后旧文本消失且回执可见：

- [主题首页](../../output/ios-mentor-memory-2026-09-20/memory-overview-ui-3/attachments/DE4F6AA3-D472-4E1E-ABA9-7A06F35F49B7.png)
- [摘要与证据](../../output/ios-mentor-memory-2026-09-20/memory-overview-ui-3/attachments/38B2EDA6-E375-4A7E-BF7B-42590A7E2907.png)
- [失效状态与整理回执](../../output/ios-mentor-memory-2026-09-20/memory-overview-ui-3/attachments/5DDCF31E-8F6D-42EF-A5CA-FD24A5D9BE6F.png)

## 验证边界

本轮按协作安排运行完整 Core 与相关 focused UI；完整 smoke 留待 #56 完成后统一运行。原生测试使用项目专用 Simulator、全局测试锁和运行前后源输入审计。实施文件摘要保存在 `output/ios-mentor-memory-2026-09-20/memory-overview-implementation-manifest.json`，用于与既有工作区基线区分。

尚未完成 Air 上真实报告投影和手机到 Air 链路验收，也未进行真机、大字体、深色模式及 VoiceOver 人工走查。首页分类和实际摘要质量仍需本人确认；本地合成回归不代表真实画像质量或生产验收通过。本轮未部署、未提交、未暂存，也未重开历史初始化或停止项。
