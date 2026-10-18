# 系统 iCloud 版本冲突：2026-09-19

## 问题与范围

用户报告某天日记有版本冲突，连正文也无法打开。开发 Mac 的只读检查确认：当前 Markdown 可以读取，系统另保留了一份内容不同、同样可读取的冲突版本。检查与备份没有修改原文件或系统冲突标记；私人内容、对比页及设备标识只保存在忽略目录。

根因是 `OriginalVaultAccess.readOptional` 在读取成功后检查冲突标记并抛错，导致 `LocalJournalStore.open` 无法生成页面数据。没有本机恢复草稿时，主页无法显示任何可操作的正文。上一轮外部刷新修复处理的是普通先后编辑和正文竞争，并未实现系统 `NSFileVersion` 的选择与处理。

本轮目标是分开展示读取和严格写入读取；冲突文件可只读打开，版本处理页提供完整内容、导出、选择或手动合并。确认先保留证据并重验，不能自动挑选修改时间较晚的版本，也不能批量删除未审阅版本。已有本机草稿与普通编辑事务保持独立。

## 官方机制核对

- [Apple 文档](https://developer.apple.com/library/archive/documentation/General/Conceptual/iCloudDesignGuide/Chapters/DesigningForDocumentsIniCloud.html)：系统可以指定当前版本、保留其他冲突版本，由应用提供接受、选择或合并的处理方式。冲突标记本身不要求应用拒绝展示可读取的内容。
- [NSFileVersion](https://developer.apple.com/documentation/foundation/nsfileversion)：版本内容通过协调读取访问；处理仅针对已审阅的准确版本对象，不能将广泛清除其他版本作为恢复手段。
- [Obsidian 同步方式](https://obsidian.md/help/sync-notes)与 [Obsidian Sync 冲突机制](https://obsidian.md/help/sync/troubleshoot)：iCloud 文件同步和 Obsidian Sync 是不同方案。后者可自动合并或创建冲突副本，不能据此断言使用 iCloud 时也执行同一算法。公开帮助资料不足以核实 Obsidian 内部如何处理每个系统版本标记。

## 验证记录

本轮代码、自动化、签名构建和真机覆盖安装已完成；本人确认真实版本及跨设备传播尚未验收。

- 在临时合成目录执行 macOS `NSFileVersion` API 检查：创建本地历史版本、持久标识重查、协调读取（含外层协调写入内的版本读取）及精确移除成功，当前样例文件字节不变。这只验证 API 调用，不制造或证明真实 iCloud 冲突传播。
- 修复后的正式 `OriginalVaultAccess` 以默认只读策略检查开发 Mac 上已经存在的冲突文件：正文可读，另一系统版本可审阅，完整性检查通过；读取前后原文字节和系统版本集合相同。仅输出计数与布尔结果，不记录原文。此项证明实际系统版本的读取适配，未确认任何真实版本，也不代表手机已完成处理。
- 正式自动化使用临时合成目录与可注入版本提供器，最终结果如下。自动测试没有使用真实日记作为 fixture。
- 真机实际冲突的处理选择须由本人审阅后确认。开发过程不替本人选择真实日记版本；手机安装与 iCloud 跨设备传播分别记录。

| 验证 | 最终结果 | 本地证据 |
|---|---|---|
| 完整 Swift package | 518 项通过，零失败、零跳过：Core 263、State 250、Probe Support 5 | `output/ios-tests/20260919T015818Z-core-d7b6deb1/summary.json` |
| 原生控件 | 57 项通过，零失败、零跳过 | `output/ios-tests/20260919T020226Z-native-6f723113/summary.json` |
| 界面流程 | 5 项通过，零失败、零跳过 | `output/ios-cloud-conflict-2026-09-19/final-cloud-ui/summary.json` |
| 测试 runner 的 Python 回归 | 77 项通过 | `output/ios-cloud-conflict-2026-09-19/runner-pytest.log` |
| 正式读取适配器检查真实系统版本 | 当前正文和一个副版本可读；读取前后源字节和版本集合不变 | `output/ios-cloud-conflict-2026-09-19/production-adapter-readonly-final.json` |
| iPhone Debug 构建及签名 | 构建成功，签名校验通过；构建记录与以上测试的 86 项源码输入一致 | `output/ios-cloud-conflict-2026-09-19/iphone-build.json` |

三轮最终 iOS 检查的构建输入审计均为 unchanged。五条界面流程实际操作了：外部修改自动刷新后继续编辑；从原目录冷打开冲突日记、选择另一版本、查看备份并冷重开；手动合并暂存、冷恢复、核对整份结果、确认保存并再次冷重开；表格回车／增行／增列及精确重开；不存在日期的首次写入与精确重开。

纠正后的失败路径覆盖备份和确认落盘失败、写后中断、部分版本标记、比较后外部再次修改、结束过期恢复、已有正文草稿的隔离、迟到原生输入、缺失版本下载请求、历史合并稿发现，以及坏备份不影响其他日期或同日可读备份。版本处理明确限制最多 16 个副版本、每份及合并结果 2 MiB；超限会解释限制并保持系统版本不变，不伪装成等待下载。

用户随后连接并解锁手机，明确授权更新。安装前复核已测试源码清单、安装包可执行文件散列及签名均未变化；先备份 App 的 Documents 和 Library/Application Support，并确认 Preferences 为空。以相同 bundle ID 覆盖安装成功，没有卸载或重置；启动前重新复制并比对全部原有数据文件，字节全部一致，Preferences 仍为空。随后正常启动 App 成功，没有注入测试环境变量，也未替本人选择真实冲突版本。安装和数据核验证据分别保存在 `output/ios-cloud-conflict-2026-09-19/phone-install.json`、`phone-install-integrity.json` 和 `phone-launch.json`。安装成功仍不能代替真实 iCloud 版本处理和跨设备传播验收。

本轮差分基于忽略目录 `output/ios-cloud-conflict-2026-09-19/before/`，保留已有开发修改及暂存区。不涉及 Air 服务变更、Git 提交或推送。

## 首轮发现与纠正

这些记录不计为最终通过证据：

- 首轮完整 Swift 检查的构建输入保持一致，但 3 项既有正文恢复测试失败；原因是新增检查先于原事务替代恢复执行。
- 首轮两条界面测试中，目录打开／选择版本／查看备份通过；合并暂存测试的全选快捷键未选中原文，测试输入方式需纠正后再验证精确内容，并补上真正确认合并的操作。
- 首次 Native 检查虽有 57 项测试通过，但运行时测试 runner 的 smoke 清单更新，输入审计失败；保留记录，不能作为最终验收。
- 独立审查要求补齐：旧草稿的恢复归属、外部处理后的过期确认、损坏备份按日期隔离、合并落盘失败、缺失版本下载触发及历史合并稿恢复。真实日记的内容选择继续留给本人。
- 纠正后第一次完整检查仅有一条旧测试仍匹配已退役的冲突文案；更新为核对冲突分类及版本处理入口，保留原全部内容保护断言后，重跑完整检查通过。
