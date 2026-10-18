# iOS 自动化验证 — 2026-09-18

范围是当前工作树的可重复测试入口，不新增生产功能。使用方法、测试清单及证据格式见[自动化指南](ios-automation.md)。未提交、推送或部署；GitHub workflow 尚未在远程运行。当前 iOS 实现及测试包含前序工作留下的未提交/未跟踪文件，workflow 必须和它们一起经过发布审查，单独发布 runner 不构成可复现的远程版本。

## 已发现并修复的 runner 问题

首次正式 smoke 的证据保留在忽略目录 `output/ios-tests/20260918T025328Z-smoke-36cab5c8/`。Xcode 27 的 `swift test --no-parallel --xunit-output` 返回 0，451 个 XCTest 方法实际通过，但未生成请求的 XML；runner 因缺证据正确返回非零。该轮不是成功验收。

runner 改为将 Swift 实际发现清单与日志中每个方法的 started/passed 终态逐项核对，输出 `swift-results.json`，不依赖最后一个 footer。以这次完整日志离线核验得到 Core 230、State 216、Probe support 5；删一个通过终态、将一个终态改为 skipped/failed、重复一个终态都被拒绝。保留串行测试和原有断言。

## 探查偏差及数据核验

实施时为读取 Xcode `-enumerate-tests` 的 JSON 格式，一次先行探查错误使用了手动 **Riji Development iPhone**。该命令实际构建并启动 UI test runner，说明枚举也不是纯静态读取；没有执行测试方法，也未 erase/uninstall。此探查不作为自动化通过证据。原始探查证据留在本机 `/tmp/riji-test-enumeration-inspection.json` 与同名 `.log`，不复制进 Git。

根任务随后重新核验手动 Simulator 中已有的数据文件，全部哈希与先前清单一致。正式 runner 的创建、启动、枚举及执行均限定为 **Riji Automated Tests**，文档也已明确此边界。

## 故障与边界检查

首次实现验证时，新增 runner pytest 56 项及既有手动 launcher pytest 10 项均通过（共 66 项）。检查覆盖设备选择、重复/不可用设备、版本上下界、缺 Xcode、工具退出码、Swift/Xcode 清单审计、跳过/失败/重复/缺失结果、环境隔离和锁冲突。Python 3.9 语法检查通过。

正式 smoke 运行期间再次执行 `--suite core`，第二个进程立即返回 1，未执行工具或干扰已有任务。拒绝证据为 `output/ios-tests/20260918T030429Z-core-bfb3006f/`。

## 最终运行

最终 smoke 的证据目录为 `output/ios-tests/20260918T030058Z-smoke-ab439c3b/`。命令返回 **0**，2026-09-18 11:18（Asia/Shanghai）结束。实际环境如下。

| 项目 | 实际值 |
| --- | --- |
| macOS / 架构 | 26.6.2 / arm64 |
| Xcode | 27.0，build 27A266a |
| Swift | Apple Swift 6.4 |
| Python | 3.9.6 |
| 设备 | Riji Automated Tests，iPhone 18 Pro Max |
| Simulator runtime | iOS 27.0（24A434） |
| 重复运行 | 复用首次创建的同一专用设备，`created: false` |
| 完整 Xcode 发现清单 | Native 40、UI 42 |
| smoke 请求清单 | Native 40、UI 14 |

实际通过结果：

| 目标 | 通过 | 失败 | 跳过 |
| --- | ---: | ---: | ---: |
| RijiCoreTests | 230 | 0 | 0 |
| RijiStateTests | 216 | 0 | 0 |
| RijiICloudProbeSupportTests | 5 | 0 | 0 |
| RijiNativeTests | 40 | 0 | 0 |
| RijiUITests（smoke） | 14 | 0 | 0 |

Swift 共 451 项；Xcode 共 54 项。完整发现清单、请求清单和实际逐测试终态一致，13 个工具命令全部退出 0。已检查 `tests.xcresult` 导出的 summary 和 case tree，只有既定专用设备，没有 clone/手动设备；全部 14 个请求 UI 方法都有通过终态，Native 完整 40 项无缺失。用最终 runner 对保留证据再次离线审计仍通过，完成后进程锁已释放。

此环境与 CI 的 Xcode 26.3 / 可用 iOS runtime 不同。当前本机同时存在 iOS 26.5 与 27.0；首次创建选择了可用的最新兼容组合，随后重跑复用它。

全量 UI `full`、物理 iPhone、真实 iCloud、Air 和实际模型验收不包含在此轮 smoke 结论中。


## 独立审查后的 runner 修补与离线复核

审查补丁只改变测试基础设施。定向执行 `uv run pytest tests/test_ios_test_runner.py`：**76 passed**。最后补充未知 target 的两段标识后，仅重跑相应参数化测试，**6 passed**（其中新增 1 项）；当前 runner 集合为 **77 项**，本 agent 未再次执行整个集合。本轮没有重跑 launcher、Swift 或 Xcode 测试。新增检查覆盖带 target 的完整测试身份、无法审计的发现条目、两种 DEVELOPER_DIR 形式、短期限合成子进程、SIGTERM/timeout 最终失败证据、子进程实际环境和 POSIX 锁继承。POSIX 信号/继承描述符相关用例在 Windows 跳过，其余测试保持可移植。

新版审计器离线重放原始 Swift discovery/log 和 `.xcresult` JSON，仍得到 **451 + 54 = 505** 通过、0 失败、0 跳过。后续复核另存于 `output/ios-tests/runner-review-fixes-20260918/replay.json`；旧 smoke 目录没有改写。

本次新增运行前后源码清单功能。后续离线检查取得当前 **80 个** scoped input 的 SHA-256，两次相同快照一致；合成的修改、新增和删除均被拒绝。当前清单另存于上述复核目录的 `current-source-inputs.json`。**原 505 项 smoke 执行前没有该清单检查**，当前快照不能追溯证明原运行输入，也不是又一次 iOS 验收。根任务随后独立运行 `--suite core` 并通过，证据为 `output/ios-tests/20260918T033710Z-core-b60c2006/`。最后两段标识修正发生在该运行结束后；最终解析器已离线重放这轮 451 项 Swift 日志及原 smoke 的 Swift/Xcode 日志，均保持通过，不把离线重放称为新一轮 iOS 执行。

runner 现在有固定阶段期限及整轮 50 分钟期限，SIGTERM/SIGINT 会完成直属子进程的有界清理并记录失败；CI 测试 step 55 分钟、job 60 分钟。SIGKILL/主机崩溃/平台强制取消仍可能留下未完成的状态或阻止 artifact 上传，不能据此宣称测试通过。详细期限、清理边界及排除的私人配置见自动化指南。spec 保持 `in-review`，由根任务完成最终审查。

## 根任务最终验收

最终 runner 测试文件已完整重新执行，结果 **77 passed，0 failed，0 skipped**；日志和 JUnit 位于 `output/ios-tests/runner-review-fixes-20260918/root-pytest.log` 与 `root-pytest.xml`。

根任务执行修补后的 `python3 scripts/test_ios.py --suite core`，**451 项通过**，全部五个工具命令退出 0；运行前后 **80 个输入文件哈希相同**。证据为 `output/ios-tests/20260918T033710Z-core-b60c2006/`。随后最后一个发现格式条件仅改变 runner 自身，iOS 输入均未改变；最终 77 项 pytest 和根任务对两次 Swift 日志、原 Xcode 54 项结果的独立审计均通过，详见 `root-final-verification.json`。这次离线审计没有重复计为真实 iOS 执行。

三路审查事项已处理，spec 已关闭为 `done`。既有暂存内容保持原样；本轮未提交、推送或更新 Air。CI 尚未远程执行，真机安装仍等待设备连接。
