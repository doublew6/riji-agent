# iOS 平台约束与首版取舍依据

核查日期：2026-09-12。范围：个人使用的 iPhone App、用户自有 Air 后端及相关平台边界。本文是官方资料调研，不代表已完成 iPhone、Air、中文转写或移动网络实测；产品默认值均为建议，等待 PRD 评审。

> 适用范围更新：以下私网与自有 Air 部分记录最初个人部署研究。PRD v2.2 已改为产品统一提供服务；普通用户不需要配置 Air 或 Tailscale。账号、产品入口与数据去向以当前 PRD 和 addendum 为准，平台事实仍供维护者参考。


## 1. 可以据此设计的核心分工

iPhone 可以负责本地记录、录音、已下载日记阅读、转写校正和操作界面；Air 可以复用现有 riji-agent 的受控写入、记忆、导师编排和云端模型接入。手机请求提交到 Air 并获得持久化回执后，长任务由 Air 执行，手机再次打开时取回进度和结果。

这是本项目的架构建议。私网连通不代表 AI 完全离线；Air 上调用云端模型仍须遵守既有用途、来源、版本和预算授权。手机与 Air 同步、模型推理、系统备份、远程通知应分别披露，不能用“本地保存”笼统代替。

## 2. 语音：可以做端上转写，但须按设备与语言探测

Apple 在 iOS 26 引入 `SpeechAnalyzer`，配合 `SpeechTranscriber` 提供端上语音转写，面向长录音、对话和较远距离音频；语言模型资源由系统管理，并通过 `AssetInventory` 按需安装。这是语音识别能力，不能推导为端上具备本项目的导师推理、日记整理和长期记忆能力。[Apple WWDC25：SpeechAnalyzer](https://developer.apple.com/videos/play/wwdc2025/277/)

官方要求通过 `isAvailable` 检查硬件能力，通过 `supportedLocales` 和 `installedLocales` 区分可下载语言与已安装语言；不支持时可关闭功能或评估 `DictationTranscriber`。本次官方文档核查没有得到足以作为产品承诺的固定“全部支持机型＋中文地区”清单，因此不能把“已升级 iOS 26”写成“必定支持离线中文长录音”。[SpeechTranscriber](https://developer.apple.com/documentation/speech/speechtranscriber)

旧 `SFSpeechRecognizer` 可作为兼容性研究对象，但只有 `supportsOnDeviceRecognition` 为真时才能依赖 `requiresOnDeviceRecognition`；不能仅设置后一个属性就宣称录音绝不出设备。[设备能力](https://developer.apple.com/documentation/speech/sfspeechrecognizer/supportsondevicerecognition)、[强制端上识别](https://developer.apple.com/documentation/speech/sfspeechrecognitionrequest/requiresondevicerecognition)

**首版建议：**录音首先可靠落盘，转写单独形成可恢复任务；优先本机已具备的中文转写能力，不可用时保留录音，允许手动输入或以后重试。若另行提供 Air / 云端转写，应先展示处理位置与接收方，禁止静默上传录音。下载语言资源需有“准备中／下载失败／设备不支持”状态，不阻塞基础录音。

**实测后才能承诺：**用户实际机型、iOS 版本、中文 locale、首次资源下载及离线重启后可用性；普通话、方言和中英混说；5 / 30 / 60 分钟录音的完整率、延迟、耗电、发热与中断恢复。长录音适用不等于无限时长、完美标点或无需人工校正。

录音还需正确配置 `AVAudioSession`，处理电话、Siri、耳机切换与媒体服务中断。锁屏持续录音属于需要实现和实测的音频行为，不能用一般后台任务权限代替。[AVAudioSession](https://developer.apple.com/documentation/avfaudio/avaudiosession)、[后台模式](https://developer.apple.com/documentation/xcode/configuring-background-execution-modes)

## 3. 本地文件：开放格式可以先交付，直接共用 Obsidian 目录需实测

从 iOS 13 起，系统文档选择器允许用户选择文件提供方中的目录，返回 security-scoped URL；App 可保存书签，在之后启动时恢复访问。访问需配合安全作用域和文件协调，用户撤回权限后读写会失败。这里授权的是用户选择且提供方允许访问的目录，不是任意沙盒路径。[Apple：目录访问](https://developer.apple.com/documentation/uikit/providing-access-to-directories)

App 可通过文件共享与原位打开配置，使自身 `Documents` 中的文件出现在“文件”App，供其他 App 打开和编辑。数据库、设备凭证、内部队列和审计数据不应一并放进暴露的共享目录。[Apple：File Provider](https://developer.apple.com/documentation/fileprovider)、[UIFileSharingEnabled](https://developer.apple.com/documentation/bundleresources/information-property-list/uifilesharingenabled)

**首版建议：**采用 App 管理的 Markdown 和附件目录，保存用户未同步输入及已下载副本；提供明确的“导出到文件”入口。Air 保持正式日记、记忆来源关系和写入确认的权威状态。App 的“已保存到手机”“等待同步”“已写入日记”必须是不同状态。外部编辑后的副本应重新比较版本并确认，不能自动覆盖 Air 原文。

**实测后才能承诺：**直接选中现有 Obsidian iCloud / 本地 vault 的目录是否可选、重启书签有效性、撤权恢复、离线未下载文件、双端同时编辑、附件路径和系统文件协调。首版不要同时启用 App 自建同步和 iCloud 对同一组文件并行写入。

Obsidian 官方确认其核心是本地文件夹中的 Markdown，可由其他编辑器管理；跨设备使用需要单独同步机制，其文档给出 iOS / macOS 使用 iCloud 的专门流程。因此“兼容 Markdown”可设计成首版目标，“所有 iOS Obsidian vault 零配置原位共用”不能由此推出。[Obsidian 数据存储](https://obsidian.md/help/data-storage)、[跨设备同步](https://obsidian.md/help/sync-notes)

## 4. 后台与通知：按恢复设计，不承诺全天实时同步

iOS 的一般后台刷新和处理由系统调度，前台转后台仅有有限完成时间。用户触发的长任务可评估 `BGContinuedProcessingTask`，但它要求从前台响应用户动作开始；不能用来承诺无人操作的常驻后台服务。[后台策略](https://developer.apple.com/documentation/backgroundtasks/choosing-background-strategies-for-your-app)、[长任务](https://developer.apple.com/documentation/backgroundtasks/performing-long-running-tasks-on-ios-and-ipados)

后台 `URLSession` 可让文件传输由系统独立进程处理，App 被系统终止后可恢复状态；但用户从多任务界面强制退出时，系统会取消后台传输，不会自动重启 App。[后台 URLSession](https://developer.apple.com/documentation/foundation/urlsessionconfiguration/background%28withidentifier%3A%29)

静默推送不保证投递，可能被节流、延迟或合并。它只能提示 App 尝试更新，不能成为结果交付、权限撤回或删除生效的唯一通道。[Apple：后台推送](https://developer.apple.com/documentation/usernotifications/pushing-background-updates-to-your-app)

**首版建议：**前台自动拉取＋手动刷新＋本地持久化待提交队列；音频按文件传输；Air 保存可查询任务 ID 与终态。网络响应丢失时先查任务和提交结果，避免重复写入。原型必须覆盖“仅在手机”“已交给 Air”“Air 处理中”“结果待下载”“部分同步失败”。

通知建议先做本地写日记提醒。若加入远程完成提醒，需要独立实现 APNs 与用户授权；内容默认仅为“有一项处理完成”，不携带日记标题、导师观点或记忆正文。App 不必开放公网入站才能使用 APNs，但 Air 需能对外连接 Apple，手机仍需私网连通才能取得私密结果。这是根据 APNs 服务端连接方式作出的方案推导，需实机端到端验证。[APNs 连接](https://developer.apple.com/documentation/usernotifications/establishing-a-connection-to-apns)、[通知内容边界](https://developer.apple.com/documentation/usernotifications/generating-a-remote-notification)

## 5. 私网连接与设备配对

Tailscale 有 iOS 客户端，安装时需要系统 VPN 配置与账户登录。Serve 可将 Air 的本地服务提供给同一 tailnet 中的设备，HTTPS 使用相应的 tailnet 域名和证书；应继续让项目服务保持 loopback 监听，由受控私网入口转发移动 API。[iOS 安装](https://tailscale.com/docs/install/ios)、[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)

**关键限制：**Tailscale 官方说明 iOS 不能同时激活多个 VPN；其他 VPN 可能与它冲突。VPN On Demand 可自动建立连接，但规则可能阻止连接，切到其他 VPN 后需要重新连接 Tailscale。不能承诺用户开启任意代理 / VPN 时都能持续连接 Air。[VPN 兼容性](https://tailscale.com/docs/reference/faq/other-vpns)、[VPN On Demand](https://tailscale.com/docs/features/client/ios-vpn-on-demand)

**首版建议：**连接页依次区分私网未连接、Air 不可达、服务不可用、设备凭证失效。App 提示检查连接，保留离线记录；不自动修改用户其他 VPN。采用 Serve 私网入口，不启用将服务提供给公网的 Funnel。HTTPS 证书的机器域名可能进入公开证书透明度日志，主机名避免包含个人身份信息。[Tailscale HTTPS](https://tailscale.com/docs/how-to/set-up-https-certificates)

设备配对本身需要本项目新增协议：建议 Air 生成短时一次性配对请求，iPhone 扫码后核对服务身份并换取独立、可撤销的设备凭证；密钥留在 Keychain，云端模型凭据仍只保存在 Air。不能把二维码 URL 中的长期 bearer token 或现有网页 Review token 直接当作设备注册方案。这些是设计建议，不是 Tailscale / Apple 自动提供的功能。

Apple Keychain 可限制凭证仅在设备解锁时访问；`ThisDeviceOnly` 使凭证不随备份迁移到另一台设备。需要后台凭证访问时，可另行评估首次解锁后可用等级。首版前台同步建议使用最严格且满足体验的等级，换机重新配对；Face ID 解锁界面不等于已经实现服务器撤销。[Keychain 可访问性](https://developer.apple.com/documentation/security/restricting-keychain-item-accessibility)、[WhenUnlockedThisDeviceOnly](https://developer.apple.com/documentation/security/ksecattraccessiblewhenunlockedthisdeviceonly)

## 6. 同类产品的范围校准

| 产品 | 官方资料能支持的定位 | 对本项目的启发 |
| --- | --- | --- |
| Obsidian | 本地 Markdown 文件库，跨设备同步另行配置 | 开放格式和可迁移是核心，不需要首版复制插件系统与任意目录能力。 |
| Apple Journal | 以记录和回顾为核心，支持打印 / 导出；可将条目连同媒体导出到文件 | 录音、照片、时间线和便捷导出可作为记录工具的基础体验；无需据此扩张成系统建议采集产品。 |
| Day One | 移动端在本机保存条目，开启 Sync 后还在其服务器保存；通过 App 使用本地数据，提供 PDF / JSON / Markdown 等导出 | “数据在本机”与“用户直接管理普通文件”是两个不同承诺；需要让导出是否含附件清楚可见。 |

来源：[Obsidian 数据存储](https://obsidian.md/help/data-storage)、[Apple Journal 导出](https://support.apple.com/en-ie/121822)、[Journal 打印与导出](https://support.apple.com/en-nz/guide/iphone/iph4cad323fe/ios)、[Day One 数据位置](https://dayoneapp.com/guides/getting-started-with-day-one/where-is-my-data-stored/)、[Day One 导出](https://dayoneapp.com/guides/tips-and-tutorials/exporting-entries/)。此表只作范围校准，不评价价格、质量、完整功能或市场优劣。

## 7. 原型和实现前的验证门槛

1. 用户真实 iPhone 上检测中文端上语音能力，先用合成 / 自拟音频验证长录音、中断、锁屏和语言资源恢复，不读取真实日记。
2. 用临时 Markdown 库验证 Files 导出、外部编辑、权限撤回和冲突保留，再决定是否支持原位打开外部 vault。
3. 在授权的 Air 测试环境验证 Wi-Fi / 蜂窝切换、其他 VPN、Air 休眠、模型限额、请求丢回执；无网络时记录仍可恢复。
4. 明确删除 / 撤权在手机副本和离线队列中的收敛：下次连接先取得权限与删除版本，再发出积压的云端操作；离线手机无法承诺即时远程擦除。
5. 真机分发、最低 iOS 版本、APNs 能力和后台范围在实现 Issue 中单独定案。个人首版可以优先用户实际设备；不能用平台 API 存在作为 TestFlight、锁屏录音或持续同步验收已通过的证据。
