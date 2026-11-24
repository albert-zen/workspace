# 文件与工作材料

工作空间同时存放通用规则、任务上下文、代码项目和个人材料。原则很简单：**按归属存放，用链接引用，不把同一份事实到处抄写。**

## 目录地图

| 路径 | 放什么 | 说明 |
| --- | --- | --- |
| `AGENTS.md`、`design.md`、`garden.md`、`practices/` | 工作空间规则与方法 | 通用协作与工程规范，可公开分享，不混入个人隐私或私有系统细节 |
| `workflow/`、`bin/work`、`tests/` | 工作流 CLI、Inbox 后端与自动化测试 | 确定性工具和规则自检入口，说明见 [workflow/README.md](workflow/README.md) |
| `dev/` | 独立代码项目 | 各项目维护自己的代码、测试和 `AGENTS.md`；手动建的隔离工作树放在 `dev/worktrees/<repo>/<task>/` |
| `agent-data/` | 任务上下文、原始意图、工作流数据库与临时产物 | 私有运行时数据（含 SQLite `agent-data/workflow/work.db`），结构见下文 |
| `memory/` | 跨任务长期记忆 | AI 认为值得留给后续 Agent 的环境细节、踩坑与稳定背景；由独立私有 Git 仓库管理，查阅、速记与后台整理见 [长期记忆](practices/memory.md) |
| `personal/` | 个人笔记 | 纯粹的个人思考笔记，归独立私有仓库管理（见 [personal/personal.md](personal/personal.md)），不再混入 Inbox 或项目原话 |
| `references/` | 外部方法与参考仓库 | 提供思路和反例，不自动生效；索引见 [references/references.md](references/references.md) |
| `adapters/` | 特定 Harness 的接入说明 | 只记录已核实的工具差异与临时补偿 |
| `archive/` | 暂不维护的历史材料 | 归档保留，不再作为当前依据 |

## 任务数据与“用户原始意图”放在哪

`agent-data/` 按项目或主题存放持续工作的私有材料：

```text
agent-data/projects/<context>/
  intent.md                     可选：项目级长期设计原话与核心意图（append-only）
  work/active/<work>.md         当前进行中的 Work 入口
  work/active/<work>/           可选：任务较长时拆出的子材料（如 intent.md、方案、证据）
  work/archive/<work>.md        已完成归档的 Work
  evidence/                     验证证据与日志
  artifacts/                    报告、HTML 页面等交付产物
agent-data/events/<year>/       重要事件复盘记录
```

- 代码项目用项目名作为 `<context>`，工作空间自身维护用 `workspace`，非项目持续事项可用 `personal` 作为归档键。
- **原始意图、Inbox 和原话都不属于个人笔记。** `personal/` 里只放我自己的个人笔记，Inbox 状态保存在 `agent-data/workflow/work.db`，项目需求或规则讨论原话留在 `agent-data/`。
- 需要保留某项工作或项目的原始用户意图时，直接记在对应的 Work `## 事项说明` 里；如果要保留原话档案，统一通过 `work intent add <项目/事项> --text "..."`（事项级）或 `work intent add --project <context> --text "..."`（项目级）写入 `intent.md`，避免 Agent 手工拼路径和格式。执行者、独立 Reviewer（通过 `work resume` / `work intent show`）以及 Inbox Web 界面都能直接读取。
- `intent.md` 由 `work intent` 以 append-only 方式追加，不润色或改写已有内容；只记录真正影响目标、边界和取舍的表达，不把“好的”“继续”这类过程消息塞进去。如果我只回了“按第二个方案”，用 `--context` 补一句说明它指向哪个方案及来源。

## 公私边界

- 提炼规则时，把个人身份、日程、内部系统地址、凭据和私有 trace 剥干净；只保留可复用的方法和足够说明问题的例子。
- `.gitignore` 只控制 Git 提交，不等于备份或加密；提交或对外分享前按实际内容核对，不要只凭目录名假设安全。
- `references/` 中的 Git submodule 保持上游独立历史，不要直接改写 submodule 里的文件；吸收的做法写进我们自己的 `garden.md` 或 `practices/`。
