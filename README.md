# 工作空间（Work Workspace）

这是一套面向人与 AI Agent 长期协作的公开文件工作空间模板：包含按动线设计的规则体系、零第三方依赖的 `work` CLI（Python 标准库 + SQLite）、本机 Inbox 关注视图，以及支持实时自动保存与一键应用的本机 HTML 审阅工作台。

## 结构与入口

| 部分 | 用途 | 入口 |
| --- | --- | --- |
| Agent 入口与整体设计 | 四条基本前提、按需路由、角色上移与园丁原则 | [AGENTS.md](AGENTS.md)、[design.md](design.md)、[garden.md](garden.md) |
| Chief of Staff 与 Inbox | 全局统筹、调度与关注视图维护 | [Chief of Staff](practices/chief.md) |
| 协作、规划与上下文 | 纵向切片、原始意图记录、多 Agent 分工与 `work` CLI | [协作与上下文](practices/collaboration.md) |
| 跨任务长期记忆 | 查阅检索、执行中极简速记与后台整理原则 | [长期记忆](practices/memory.md) |
| 工程设计、验证与 Review | Deep Module、行为导向 TDD、按影响独立审阅与收敛 | [工程与验证](practices/engineering.md)、[独立 Review](practices/review.md) |
| 高带宽沟通与呈现 | F 形 → 千层蛋糕排版、对外汇报与载体选择 | [沟通与呈现](practices/communication.md)、[Prompt Me / Prompt Yourself](practices/prompt-me.md) |
| 工具实现 | Python 标准库 CLI、SQLite 存储、版本与工作树管理 | [workflow](workflow/README.md)、[bin/work](bin/work) |
| 本地界面 | 实时 Inbox 读写、实时审阅工作台与一键显式应用 | [Inbox](practices/communication/html/inbox/README.md)、[审阅工作台](practices/communication/html/review/review.md) |
| 知识、参考与适配 | 目录边界、个人笔记、外部参考与 Codex 局部补偿 | [文件与工作材料](workspace.md)、[个人笔记](personal/personal.md)、[外部参考](references/references.md)、[Codex 接入](adapters/codex/README.md) |

现有目录结构保持完整。`dev/` 放独立项目，`agent-data/` 放工作记录、SQLite 工作流库和产物，`personal/`、`memory/` 保存使用者自己的材料；这些实际数据不随本发行包提供。Git 中的空目录占位仅供开始使用，不包含历史任务。

## 五分钟开始

需要 Python 3.9+；Git 资源管理支持 Windows、macOS 和 Linux。无需安装 Python 第三方包。Node.js 仅用于运行前端测试，不是日常使用依赖。

先获取仓库，再在仓库根目录运行：

```sh
git clone https://github.com/albert-zen/workspace.git
cd workspace
```

```sh
python3 bin/work start '示例项目/第一件事' --summary '先确定一个可检查的成果，再执行并保存上下文。'
python3 bin/work resume '示例项目/第一件事'
python3 bin/work inbox
```

`bin/work inbox` 会启动本机 loopback 服务并打开浏览器（默认 `http://127.0.0.1:8765/`，空目录也可以直接打开）。审阅规则或代码变更时，可生成 bundle 后直接启动实时自动保存的本机审阅工作台：

```sh
python3 practices/communication/html/review/scripts/workbench.py serve --repo . --bundle <bundle.json>
```

Agent 从 `AGENTS.md` 进入；接手已有事项先 `resume`。普通问答无需登记。改正文用 `edit` / `save`，过程用 `record`，记录用户原话用 `intent add`，复杂计划用 `plan`。`finish` 表示业务完成并按保护规则收尾，不是“这一轮说完了”。命令选项以 `python3 bin/work <操作> --help` 为准。需要 Chief 时，可参考 [虚构全局索引模板](examples/portfolio.md)。

## 使用隔离的虚构示例

```sh
python3 scripts/create_demo.py ./agent-data/demo
python3 bin/work --root ./agent-data/demo inbox --port 8767
```

脚本拒绝覆盖非空目录，只创建虚构的读书会事项。任务正文和计划示例在 [examples/task-context.md](examples/task-context.md) 与 [examples/plan.md](examples/plan.md)。不读取原作者数据，不连接第三方业务系统，不发消息。

## 外部参考与发行方式

两个外部参考保留为固定版本的 Git submodule，不是运行依赖。Git 版本中可按需获取：

```sh
git submodule update --init --recursive
```

发行 ZIP 提供本体系完整自有源码、方法与测试，外部参考不内嵌；需要读取这些链接时，克隆本公开仓库，再执行上面的初始化命令。公开仓库不包含原私人工作空间的历史或数据。

## 验证

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py'
python3 -B -m unittest discover -s practices/communication/html/inbox/tests -p 'test_*.py'
python3 -B -m unittest discover -s practices/communication/html/review/tests -p 'test_*.py'
node --test practices/communication/html/inbox/tests/*.test.cjs
```

测试使用临时虚构数据。浏览器检查也应使用隔离示例目录，避免把验收数据写入个人正式 Inbox。

数据、备份、审阅 HTML 与临时恢复副本的边界见 [PRIVACY.md](PRIVACY.md)。公开前的检查方法见 [RELEASING.md](RELEASING.md)。
