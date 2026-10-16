# 外部参考

这里保存我愿意长期参考的外部项目和工作方法。它们用于提供思路、例子和反例，不是自动生效的规则包。

## 使用原则

阅读外部参考时，先理解它解决的问题和适用环境，再决定是否借鉴。不要因为作者有影响力或某项实践看起来完整，就整套搬进我的工作方式。

优先级通常是：我当前的明确要求、当前项目规则、这个工作空间的规则，最后才是外部参考。外部材料与前面的规则冲突时，不静默采用。如果冲突揭示了值得重新考虑的取舍，说明两边的理由和实际影响，再问我是否调整自己的规则。

外部仓库通过 Git submodule 保持独立历史。父仓库记录当前使用的精确版本，但不会自动跟随上游变化；什么时候更新由我决定。不要直接修改 submodule 中的参考内容，需要采纳的做法应写进我们自己的 `practices/`。

## 沟通与汇报

- [向人汇报与交流](communication/human-reporting.md)：比较 BLUF、金字塔原理、SBAR、例外管理、状态汇报和事件沟通，记录可迁移的信息顺序、使用边界与来源证据。

## Matt Pocock Skills

- 上游：[mattpocock/skills](https://github.com/mattpocock/skills)
- 本地 submodule：[matt-pocock-skills](matt-pocock-skills/)

Matt 的 Skills 值得参考的地方是：每个 Skill 保持小而可组合，先说明何时使用，再把详细材料按需加载，不试图用一套重流程接管所有开发活动。

- [Writing for Agents](matt-pocock-skills/skills/productivity/writing-for-agents/SKILL.md)：原 `writing-great-skills`，上游于 2026-07-23 改名并扩展到普通 Agent 文档。讨论入口触发条件、按需读取、信息层级与单一维护来源；Skill 特有的封装说明另见同目录 `SKILL-MECHANICS.md`。调用机制的描述须结合实际 Harness 核实，不作为本工作空间的运行事实。
- 2026-09-11 更新本地参考：`9c9f36c` → `3cca18b368ae95cdbdebbff572ccafa662551015`（本次获取的上游 main）。只更新参考版本，不安装或启用其中的 Skills。

## Theo / T3 Code

T3 Code 的 [AGENTS.md](https://github.com/pingdotgg/t3code/blob/main/AGENTS.md) 是很好的项目级说明范例：直接解释产品、真实危险、词汇和验证方式。
