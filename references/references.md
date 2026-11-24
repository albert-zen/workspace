# 外部参考

这里收录值得长期参考的外部方法、演讲和工具仓库。它们用来提供思路、案例和反例，**不是自动生效的规则**；读原文时只取解决眼前问题的部分，不要整套照搬。

本地 Git submodule 保持独立历史，由父仓库固定精确提交，不自动跟随上游，也不直接修改 submodule 内部文件。

## 工程环境与自动化开发（Gardening & 验证）

- **Lauren Tan · *All Code Is Slop (Except Mine)*（AI Engineer CODE 2026）**：讨论大规模 Agent 自动化开发下如何靠“倒置信任金字塔”保住代码质量——把反复出现的 Agent 错误下沉为自定义 lint、API 禁令与依赖图约束；用浏览器/CDP 和特征图做运行时验证；在核心流程试验 TLA+ / Lean 形式化验证；让人审阅验证产物与证据而非逐行盯 diff。可作为 [园丁原则](../garden.md) 的具象案例。
- **Lauren Tan · [pstack（cursor/plugins）](https://github.com/cursor/plugins/tree/aaeb0ea3a0584995aa8f55ce44c6c5259414b896/pstack)**（Prompt Stack，位于 `cursor/plugins` 仓库固定提交 `aaeb0ea3`）：
  - [Figure it out](https://github.com/cursor/plugins/blob/aaeb0ea3a0584995aa8f55ce44c6c5259414b896/pstack/skills/figure-it-out/SKILL.md)：从可验证的完成条件倒推工作。
  - [Attack the Premise](https://github.com/cursor/plugins/blob/aaeb0ea3a0584995aa8f55ce44c6c5259414b896/pstack/skills/principle-attack-the-premise/SKILL.md)：多轮修补仍绕同一个假设打转时，先找能推翻它的证据。
  - [Verify and Ship](https://github.com/cursor/plugins/blob/aaeb0ea3a0584995aa8f55ce44c6c5259414b896/pstack/docs/guide/06-verify-and-ship.md)：按改动类型选择验证方式和交付证据。

## Matt Pocock Skills（本地 submodule）

- 上游：[mattpocock/skills](https://github.com/mattpocock/skills) · 本地：[matt-pocock-skills/](matt-pocock-skills/)
- 值得借鉴的特点：每个 Skill 保持短小、可组合、先讲触发条件再按需展开。常用入口：
  - [Writing for Agents](matt-pocock-skills/skills/productivity/writing-for-agents/SKILL.md)：写给 Agent 的文档结构、按需读取与单一维护来源。
  - [Codebase Design](matt-pocock-skills/skills/engineering/codebase-design/SKILL.md) / [Improve Codebase Architecture](matt-pocock-skills/skills/engineering/improve-codebase-architecture/SKILL.md)：Deep Module、seam、架构重构与 [HTML Report Format](matt-pocock-skills/skills/engineering/improve-codebase-architecture/HTML-REPORT.md)。
  - [Test-Driven Development](matt-pocock-skills/skills/engineering/tdd/SKILL.md) / [Diagnosing Bugs](matt-pocock-skills/skills/engineering/diagnosing-bugs/SKILL.md)：红绿循环、纵向切片与定位根因。
  - [Grill Me](matt-pocock-skills/skills/productivity/grill-me/SKILL.md) / [Grill With Docs](matt-pocock-skills/skills/engineering/grill-with-docs/SKILL.md) / [To Tickets](matt-pocock-skills/skills/engineering/to-tickets/SKILL.md) / [Wayfinder](matt-pocock-skills/skills/engineering/wayfinder/SKILL.md)：澄清设计、拆任务与大型探索。

## Theo · tstack 与 T3 Code

- **[t3dotgg/tstack](https://github.com/t3dotgg/tstack/tree/11e37a74d0a6868c121cd0910ab2b3bd9873b92a)**（固定提交 `11e37a74`）：按不同口令约定停点（`full-send`、`full-stop`、`file-pr`、`babysit-pr` 等）和多模型评审流程；其中 [Full Send · Proof](https://github.com/t3dotgg/tstack/blob/11e37a74d0a6868c121cd0910ab2b3bd9873b92a/skills/full-send/SKILL.md#proof) 的交付截图与证据写法值得参考。自动合并、代发评论和评审轮次仍以本工作空间的授权与 Review 约定为准。
- **[T3 Code · AGENTS.md](https://github.com/pingdotgg/t3code/blob/main/AGENTS.md)**：优秀的项目级说明范例——直接讲清产品、真实风险、术语和验证命令。

## 沟通、界面与写作

- [向人汇报与交流](communication/human-reporting.md)：BLUF、金字塔原理、SBAR、例外管理与状态汇报的适用边界。
- [Archify 接入](communication/archify.md)（本地 submodule [archify/](archify/)）：结构化 JSON 驱动的可探索架构/流程图渲染。
- [Anthropic Frontend Design](https://github.com/anthropics/skills/blob/e4bb5d960d87126ff3b3a8af1a104b7db67560ba/skills/frontend-design/SKILL.md)：新界面发散视觉方向的参考；已有产品优先沿用现有设计系统。
- [The Antislop](https://github.com/aplaceforallmystuff/the-antislop/blob/f123fdc53574b6bb84b9804c83b854d19cd210d7/skills/antislop/SKILL.md)：给人看的文稿交付前检查套话和 AI 腔的参考清单。
