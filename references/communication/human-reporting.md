# 向人汇报与交流

这份参考整理 Agent 向人汇报和交流时可借鉴的外部方法。它解释这些方法各自解决什么问题、哪些结论能相互印证，以及哪些内容只是结合当前工作方式得到的推断。它不是直接生效的规则；实际采用的做法见[与人沟通](../../practices/communication/communication.md)。

资料最后核对于 2026-08-31。这里只记录公开可访问的说明和稳定链接，不保存受版权限制的原文。

## 跨来源能够相互印证的结论

多个来源使用了不同术语，但在以下方面高度一致：

- 开头应先给接收者作判断所需的信息，而不是按调查或执行的时间顺序叙述。通常至少包括当前结论或状态、它造成的影响，以及是否需要接收者采取行动。
- 结论之后才展开关键理由和证据。细节应分层，使人可以只读开头，也可以继续追查依据。
- 进度不是活动清单，而是相对于目标、里程碑、上次状态或约定容差的变化。风险、偏差和需要的决策比过程流水更值得提前出现。
- 坏消息和阻塞应尽早暴露，同时交代已知影响、正在采取的行动、仍然未知的部分和下一次更新；不能等到根因完全查明才汇报。
- 同步沟通适合快速澄清、真实分歧和高频互动；异步文字需要自包含、可追溯，并把最终决定写回稳定载体。

这些是对来源的综合，不代表任何单一方法都完整规定了上述顺序。

## 方法解决的不同问题

| 方法 | 主要解决的沟通失败 | 最值得借鉴的部分 | 使用边界 |
| --- | --- | --- | --- |
| [BLUF](https://www.7atc.army.mil/Media-News/Video/dvpTag/writing/dvpmoduleid/4969/videoid/1016589/) | 接收者读了很久仍不知道重点 | 把最重要的信息放在开头；长文的各段也先给段落主旨 | “先说结论”不等于省略证据、影响或请求 |
| [Minto Pyramid Principle / SCQ](https://www.barbaraminto.com/) | 结论、理由和细节混在一起，读者无法快速验证 | 由答案向下展开相互归组的关键理由；Situation–Complication–Question 可用于确认读者真正关心的问题 | SCQ 更适合作为思考工具，不必机械地把背景写在答案之前。McKinsey 对该方法的[历史介绍](https://www.mckinsey.com/alumni/news-and-events/global-news/alumni-news/barbara-minto-mece-i-invented-it-so-i-get-to-say-how-to-pronounce-it)能证明其广泛传播，但不是效果实验 |
| [SBAR](https://www.ahrq.gov/sites/default/files/wysiwyg/teamstepps-program/dx-improvement/module3-presenters-notes.pdf) | 紧急交接遗漏当前情况、判断或明确请求 | Situation → Background → Assessment → Recommendation/Request 提供了短而完整的升级结构 | 原方法来自医疗安全；迁移到软件和 Agent 沟通属于类比，不能照搬临床字段。WHO 的[交接材料](https://cdn.who.int/media/docs/default-source/integrated-health-services-(ihs)/csy/bec-slides/bec/sbar.pdf)还强调担忧、时间线、应急方案和接收确认 |
| [Management by Exception](https://community.peoplecert.org/public/clubs/prince2/blogs/success-and-failure-what-does-good-look-like) | 上级被日常细节淹没，真正越界的偏差却太晚出现 | 预先约定容差；在预计超出授权或容差时升级，而不是凡事请示 | 例外管理不能变成平时不汇报，也不能在没有明确目标和容差时假装能自动判断例外 |
| 状态与事件汇报 | 报告只有“做了什么”，没有趋势、影响和下一步 | PMI 强调目标状态、偏差、风险、行动和所需决策；Atlassian 的事件实践强调当前状态、影响、下一步和下次更新时间 | 百分比、红黄绿状态只有在基线和阈值明确时才有意义 |

## 按场景形成的信息顺序

下面的顺序是结合上述方法得到的候选设计，不是某个来源原样给出的模板：

- **建议或请求决策：**建议结论和需要的决定 → 影响与为什么现在要决定 → 两到四个关键依据 → 风险、替代方案与不确定性 → 负责人和期限。
- **日常进度：**相对目标的当前状态和趋势 → 自上次以来的实质变化 → 对里程碑或结果的影响 → 风险、阻塞及应对 → 下一个关键事件。
- **坏消息或事件：**发生了什么、严重度和已知影响 → 当前处置 → 已知与未知的原因 → 下一步 → 下次更新时间。
- **阻塞升级：**阻塞事实和若不处理的后果 → 为什么已超出当前能力或授权 → 已经验证和尝试的内容 → 推荐方案 → 具体需要谁在何时提供什么帮助。
- **完成汇报：**交付结果和是否可用 → 与风险相称的验证证据 → 剩余风险和未覆盖范围 → 是否还需要接收者行动。

若某个请求会决定接收者是否继续阅读，应在首段出现；长报告可以在末尾再次确认负责人和期限。这个“双位置”做法是为减少遗漏而作的设计推断。

## 同步与异步

[GitLab 的沟通指南](https://handbook.gitlab.com/handbook/communication/)把异步作为默认起点，同时建议在策略转向、真实分歧或上下文难以澄清时转为同步，并把同步所得结论重新写回。其[异步更新实践](https://handbook.gitlab.com/handbook/engineering/ai/ai-coding/how-we-work/async-updates/)使用进展与状态、下一步、阻塞和信心来帮助他人独立判断。

据此可以迁移出两点：同步表达应在最初几句话给出主结论，然后根据对方问题展开；异步文字则必须补足必要上下文、稳定链接、时间点、明确请求和预期响应。同步并不意味着可以省掉结论记录，异步也不意味着所有讨论都应坚持写到底。

## 不宜照搬的部分

- 不把 BLUF 简化成一个没有影响、依据和行动含义的标题。
- 不按字面顺序套用 SCQA；先铺陈 Situation 可能重新制造“读到最后才知道答案”的问题。
- 不把医疗 SBAR 的字段或术语直接变成所有工程交流的固定表单。
- 不把固定的周报栏目、3P、红黄绿或完成百分比当成通用规则。没有目标、基线和阈值时，它们会制造虚假的精确感。
- 不因采用例外管理就隐藏正常趋势。接收者仍需要足够信息判断目标是否稳健，只是不需要看到每一步操作。
- 不要求每条短消息都填满模板。结构的价值是防止遗漏重要判断，不是增加格式负担。

## 证据边界

这里的证据主要说明这些方法被成熟组织长期采用，并能互相印证常见的信息需求；它们不是比较不同汇报格式效果的随机对照实验。Minto 官方材料属于方法创立者的一手说明，传播情况由 McKinsey 的历史文章旁证。SBAR 的证据和权威性主要来自医疗场景，迁移到 Agent 与软件工作属于本工作空间的推断。PMI、GitLab 和 Atlassian 材料属于标准组织或实际团队的实践总结，适合借鉴，但不证明其格式适用于所有任务。

## 其他核对来源

- PMI, [The anatomy of a highly effective project status report](https://www.pmi.org/learning/library/2019/04/07/15/03/anatomy-highly-effective-status-report-2198)：区分按计划、可恢复偏差和不可恢复偏差，并要求报告解决动作而非只列问题。
- PMI, [In the dark](https://www.pmi.org/learning/library/remain-informed-governance-decisions-executives-2891)：强调在问题恶化前定义升级参数，并向管理者提供目标、里程碑、路障和决策需求。
- PMI, [Managing software projects: common risk pitfalls](https://www.pmi.org/learning/library/managing-software-projects-common-risk-pitfalls-7876)：讨论误导性或低频状态汇报，以及尽早传递坏消息。
- GitLab, [Fulfillment project updates](https://handbook.gitlab.com/handbook/engineering/development/fulfillment/)：管理层更新关注进度趋势、阻塞和下一个事件。
- Atlassian, [Incident response handbook](https://www.atlassian.com/incident-management/handbook/incident-response)：初始事件沟通包括当前状态、影响、下一步和下次更新时间，并允许明确说明根因未知。
- Harvard Business Review, [How to Present to Senior Executives](https://hbr.org/2012/10/how-to-present-to-senior-execu)：实践性总结，支持面向高层时避免把关键结论留作最后揭晓；公开页面信息有限，因此不作为核心规则的唯一依据。
