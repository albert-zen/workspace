# Work：人与 Agent 的文件工作空间

这是一个可自托管到本机文件夹的协作体系：人决定目标与验收，Agent 执行并留下可接手的上下文，工具负责确定性的保存、校验和呈现。

由 [albert-zen](https://github.com/albert-zen) 维护，采用 [MIT License](LICENSE)。第三方来源与许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 保留的完整体系

| 部分 | 用途 | 入口 |
| --- | --- | --- |
| Agent 入口与整体设计 | 按需读取规则，理解人的目标、决定与验收责任 | [AGENTS.md](AGENTS.md)、[design.md](design.md) |
| Work 与上下文 | 登记、推进、计划、恢复、交接和收尾 | [工具语义](practices/collaboration/workflow.md)、[共同上下文](practices/collaboration/context.md) |
| 多 Agent 与 Chief | 分工、咨询、结果回收和跨事项关注 | [事项推进](practices/collaboration/work.md)、[Chief](practices/collaboration/chief/README.md) |
| 验证与 Review | 固定审查对象，寻找反例，控制修复轮次 | [工程](practices/engineering/design.md)、[验证](practices/engineering/verification.md)、[Review](practices/collaboration/review.md) |
| 工具实现 | Python 标准库 CLI、Markdown 存储、版本与工作树管理 | [workflow](workflow/README.md)、[bin/work](bin/work) |
| 本地界面 | Inbox 读写、离线 HTML 审阅与显式应用 | [Inbox](practices/communication/html/inbox/README.md)、[审阅工作台](practices/communication/html/review/review.md) |
| 知识与维护 | 规则、任务事实、个人笔记与记忆各有归属 | [工作材料](workspace.md)、[个人笔记](personal/personal.md)、[维护](maintenance.md) |
| 宿主适配 | Codex 的协作工具与运行差异 | [适配入口](adapters/codex/README.md) |

现有目录结构保持完整。`dev/` 放独立项目，`agent-data/` 放工作记录和产物，`personal/`、`memory/` 保存使用者自己的材料；这些实际数据不随本发行包提供。Git 中的空目录占位仅供开始使用，不包含历史任务。

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
python3 practices/communication/html/inbox/scripts/inbox_app.py --repo-root .
```

然后打开终端给出的本地地址（默认 `http://127.0.0.1:8765/`）。第一次先运行 `start`，由工具初始化 Inbox 与存储；空目录直接启动 Inbox 不是完整初始化流程。需要结束服务器时在终端按 Ctrl+C。

Agent 从 `AGENTS.md` 进入；接手已有事项先 `resume`。普通问答无需登记。改正文用 `edit` / `save`，过程用 `record`，复杂计划才使用 `plan`。`finish` 表示业务完成并按保护规则收尾，不是“这一轮说完了”。命令选项以 `python3 bin/work <操作> --help` 为准。需要 Chief 时，再按[虚构全局索引模板](examples/portfolio.md)约定入口位置并写进工作空间说明。

## 使用隔离的虚构示例

```sh
python3 scripts/create_demo.py ./agent-data/demo
python3 practices/communication/html/inbox/scripts/inbox_app.py --repo-root ./agent-data/demo --port 8767
```

脚本拒绝覆盖非空目录，只创建虚构的读书会事项。任务正文和计划示例在 [examples/task-context.md](examples/task-context.md) 与 [examples/plan.md](examples/plan.md)。不读取原作者数据，不连接第三方业务系统，不发消息。

## 外部参考与发行方式

两个外部参考保留为固定版本的 Git submodule，不是运行依赖。Git 版本中可按需获取：

```sh
git submodule update --init --recursive
```

发行 ZIP 提供本体系完整自有源码、方法与测试，外部参考不内嵌；需要读取这些链接时，克隆本公开仓库，再执行上面的初始化命令。公开仓库从一个干净根提交开始，不包含原私人工作空间的历史或数据。

## 验证

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py'
python3 -B -m unittest discover -s practices/communication/html/inbox/tests -p 'test_*.py'
python3 -B -m unittest discover -s practices/communication/html/review/tests -p 'test_*.py'
node --test practices/communication/html/inbox/tests/*.test.cjs
```

测试使用临时虚构数据。浏览器检查也应使用隔离示例目录，避免把验收数据写入个人正式 Inbox。

数据、备份、审阅 HTML 与临时恢复副本的边界见 [PRIVACY.md](PRIVACY.md)。公开前的检查方法见 [RELEASING.md](RELEASING.md)。
