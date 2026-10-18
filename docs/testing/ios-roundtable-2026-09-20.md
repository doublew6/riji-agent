# iOS 持久圆桌本地验证 · 2026-09-20

2026-09-21 的 Air 更新、真机安装及公网验证见[发布核验记录](ios-mentor-memory-release-2026-09-21.md)；下文保留此前本地验证阶段的边界。

这是分阶段开发记录，保留当时的结果与待办；修复后的统一状态见[集成验证记录](ios-mentor-memory-2026-09-20.md)。

范围：[实施规格 #56](../../_bmad-output/implementation-artifacts/spec-56-mobile-roundtable.md)。本轮使用合成材料、临时目录和项目专用 Simulator；没有连接 Air、访问真实日记、调用真实模型、部署、暂存或提交。

## 实现

- 增加设备绑定的持久主持人聊天、移动房间和明确的代理路由；普通发送仅由主持人回应，不自动读取记忆。邀请时选择 2–4 位导师、参考/分歧模式及有限轮数；选定材料向主持人和四导师共享。
- 材料预览绑定完整选择、来源版本、会话修订及发言配置。切换材料与启动生命周期在同一事务中完成；拒绝启动不会偷偷更改上下文。AI 保存输入谱系，取消选择的材料不能借旧 AI 观点进入新运行。
- 历史只显示已送达 AI，来源失效保留占位。材料、历史和结果返回前再次复核；模型执行和投递继续使用既有来源、设备、运行版本和取消检查。结果回执只保留结果 ID，不能作为后续读取或写入授权。
- 原生提供主持人对话、邀请/材料预览、进度、补充、停止、先总结、继续、条件纠正、历史及本地保存。未知发送与停止使用独立回执目标。分页记忆选择保留完整快照；创建时等待期间较新草稿迁入新会话。
- 本地结果使用独立 preview/transaction/receipt，不进入自动恢复的正文事务。确认前与真正替换文件前检查 fresh validation；原目录通过协调写入，保留原字节与 CRLF。永久回执不因后续手改或删除而失效。已落盘结果可以恢复回执；未落盘需要显式新确认，也可取消后重新预览。
- AI provenance 与普通正文编辑隔离，保留来源、日期、修订和编辑标志。独立共享 fixture 用 Swift 精确输出与 Python `content_spans` 双端验证，防止 AI 内容被识别为本人经历。

## 已实际执行

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| 父任务完整 Python 回归（最后增补前） | 2180 passed，5 skipped；输入哈希一致 | `output/ios-mentor-memory-2026-09-20/python-full.xml` |
| 后端 mobile、mentor、journal privacy 回归 | 359 passed，1 skipped | `output/ios-mentor-memory-2026-09-20/roundtable-final-python.xml` |
| 最终来源/进度字段后的圆桌、Mobile API 与 persistent 回归 | 47 passed，1 optional browser skipped（其中圆桌 21 passed） | `output/ios-mentor-memory-2026-09-20/roundtable-handoff-python.xml` |
| 首次完整 Core/State/Probe（最后 6 条增补前） | 562 passed，0 failed/skip（290 Core、267 State、5 Probe） | `output/ios-tests/20260920T143215Z-core-7c8db080/` |
| 手动启动器参数/隔离 | 15 passed，shell 语法检查通过 | 父任务启动器记录 |

后端最后相关回归命令：

```sh
PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH uv run --no-sync pytest tests/test_mobile*.py tests/test_mentor*.py tests/test_journal_privacy.py -q --junitxml=output/ios-mentor-memory-2026-09-20/roundtable-final-python.xml
```

5 个全量 Python skip 分别为 4 个可选官方 Codex CLI wire check（未配置 `RIJI_TEST_CODEX_BIN`）和 1 个可选 browser acceptance。相关回归仅包含该 browser skip；圆桌新增用例无 skip。既有 websockets 两条弃用警告没有导致失败。

## 覆盖矩阵

| 契约 | 已有自动化覆盖 | 后续/边界 |
| --- | --- | --- |
| 主持人直接聊天、无隐式记忆 | API 真实 worker；State 新建与草稿迁移 | UI 聚焦最终结果由父任务补入 |
| 五个讨论情景 | planning、实际 debate 两轮发言及 synthesis、条件更正、部分停止后总结、实际反馈 | 合成输出检查不证明真实模型建议质量 |
| 三次重开 | 三次重建 Runtime/Service 读取同一持久历史；三次重建 native model 恢复新草稿选择 | 真机杀进程待实测 |
| 选择及撤权 | 完整选材/回执；启动事务回滚；撤权后模型计数不增加；生成后撤权不投递；history 组装后撤权拒绝；旧 AI 无法带回取消材料 | 生产 Mem0/跨进程外部写入未在本轮实测 |
| 修订、分页及未知结果 | correction 后旧结果无效；旧 cursor 拒绝；全体请求指纹；native 混页重新加载；unknown send 可独立 stop | UI 停止/继续完整人工流程由父任务补入 |
| 离线和身份变化 | 限时有界 cache、已知失效不恢复；source/privacy/device/account 重启覆盖已新增 | 最后新增 native 测试尚待最终 runner |
| 本地追加 | 字节/CRLF、事务/写入/回执/清理故障边界、唯一块证明、歧义拒绝、手改及删除不重建、目标改变保留预览、Notes 缺失/重复拒绝、marker 保护 | 最后新增 original 路径过期 guard、离线取消、冷预览恢复、跨语言 fixture 尚待最终 runner |

## 尚待最终集成验证

最早 App 首编在邀请页复杂 SwiftUI 表达式报 type-check 超时；拆分 helper 后手动启动成功。随后两条新功能 UI 聚焦中，记忆链路通过；圆桌已完成邀请确认，但邀请 sheet 只在 active 时关闭，合成运行立即 completed 导致 sheet 遮挡聊天选择按钮，原 isHittable 断言正确失败。失败日志与录像保留在 `output/ios-mentor-memory-2026-09-20/integrated-new-feature-ui/`。

最终源码已改为按完成回执关闭 sheet；发送/邀请会收起键盘，选择按钮提供 44pt 区域，保存入口在常驻操作栏。UI 增加返回聊天可点和 sheet 已关闭的断言。另补当前选定依据/真实阶段信息、五位参与者结果、阅读锚点/选择持久化。最后新增 3 Core、3 State 尚待父任务统一 Core/State 与全 smoke，不把预期 568 项当作已通过；独立 review 也留待父任务汇总。

未完成真实 Air/真实模型/真机/iCloud 服务端冲突验收，也未把深色模式、大字号、VoiceOver 人工走查记作通过。本机演示入口和具体限制见 [手测说明](ios-mentor-memory-manual.md)。
