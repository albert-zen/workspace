# 工作空间流程工具

`bin/work` 是命令入口，本目录保存 CLI 和 Inbox 共用的实现。无需安装 Python 包或第三方依赖；需要 Python 3.9+，Git 工作树管理需要 macOS/Linux。

- `work_cli.py`、`work_interface.py`：命令参数、自然语言输出和本机入口安装。
- `work_store.py`、`work_flow.py`：统一读写、并发保护、状态同步和收尾。
- `work_plan.py`、`work_resources.py`：计划版本与依赖、工作树关联和安全清理。
- `inbox_model.py`、`work_documents.py`：Inbox Markdown 与 Work 文档读写模型。

Inbox 的 HTTP 服务和前端仍在 [`practices/communication/html/inbox/`](../practices/communication/html/inbox/README.md)，通过导入本包使用相同逻辑。本包不依赖该界面目录。

默认数据根目录是本代码所属的工作空间，`--root` 可指定其他数据目录。数据仍保存在 `personal/inbox/` 与 `agent-data/`，没有迁移数据或新增数据库。日常操作运行 `bin/work`；状态含义见[工具语义](../practices/collaboration/workflow.md)。

在工作空间根目录验证：

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py'
python3 -B -m unittest discover -s practices/communication/html/inbox/tests -p 'test_*.py'
```

## 内部存储边界

`pending.json` 是短期恢复日志，写入前持久化完整 before/after，事务完成后删除；旧 pending 格式仍可通过现有 `work recover` 恢复。完成回执 `operations/<id>.json` 的 v2 格式只保留起止时间、文件路径、前后 UTF-8 字节数与 SHA-256（不存在为 null），不重复保存全文；它不是任意时间点回滚功能。业务历史继续由 Work 文档、事件与计划版本保存。

工作树检查临时读取 Git 状态，只返回是否有改动、是否合入、能否清理及原因，不把文件名清单存入事项状态。忽略项按目录检查；检测到任何内容仍保留工作树，不因省略清单而放宽删除条件。

用户的记录方式和命令不变，无需执行存储维护命令。依据是[恢复日志只服务未完成事务](https://sqlite.org/tempfiles.html)：保留故障恢复能力，避免把已完成操作的完整副本当作业务历史永久累积。
