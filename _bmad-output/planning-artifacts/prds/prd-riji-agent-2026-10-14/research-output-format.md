# PRD 的图解表达方式

2026-10-14，用户指出纯文字 PRD 阅读费力，希望使用图片、HTML 和更直观的表达，并要求查找 Karpathy 相关方法或 skill。

## 找到的来源

- [Karpathy 关于理解模型输出的原帖](https://x.com/karpathy/status/2105819303471976479)：社区引用将其概括为清楚文字、图解、交互 HTML、按需定制讲解视频。
- [较早的 HTML 输出原帖](https://x.com/karpathy/status/2053872850101285137)：社区引用的重点是在提示中明确要求 HTML 表达。
- [karpathy-output-style](https://github.com/ashryaagr/karpathy-output-style)：独立社区实现，包含 `clear-writing`、`explain-diagram`、`explain-webpage`、`explainer-videos`，作者明确不属于 Karpathy 官方项目。
- [visual-explainer](https://github.com/nicobailon/visual-explainer)：另一社区工具，将流程、方案和复杂关系输出为自包含的 HTML 页面。不是本次运行依赖。

核实范围：已阅读仓库说明并核对原帖链接。X 原帖直接访问受限，现有 Browser Bridge 未连接，未直接完整读到原文；不把社区措辞当作逐字原文，不推断 HTML 在所有任务中都优于文字。没有安装第三方插件或执行其脚本。

## 对这份 PRD 的应用

使用已安装的 `prd-wireframe`，把图解作为讨论入口，文字作为规则底稿。按真实使用顺序展示页面与状态，保留少量可点击的局部演示，并在旁边标清动作、结果与待确认项。

图必须解释人物状态、多人接收范围、话题和记录关系；不以装饰图片填充版面。全部场景使用合成内容与简图，离线查看，不调用真实服务、不改 App，也不把低保真线框误当作新的颜色或人物设计确认。
