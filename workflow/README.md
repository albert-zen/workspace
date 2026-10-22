# 工作空间流程工具

`bin/work` 是命令入口，本目录保存 CLI 和 Inbox 共用的实现。无需安装 Python 包或第三方依赖；需要 Python 3.9+，Git 工作树管理支持 Windows、macOS 和 Linux。

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

工作树检查临时读取 Git 状态，只返回是否有改动、是否合入、是否仅含 ignored 项及能否清理，不把文件名清单存入事项状态。tracked 修改与普通 untracked 文件仍保留工作树；只有有独立创建凭据、已合并且未共享的工作树，才自动处理 ignored 项。Git 报告的 ignored 文件或目录按原相对路径完整移至 `agent-data/worktree-recovery/<ownership-token>/<attempt>/files/`，不依赖 `node_modules`、`dist` 等名字，也不跟随其中的链接。先持久化 manifest 和归档位置，再逐项核对目录身份、分支状态及移动后的对象身份，最后仍使用不带 force 的 Git worktree remove。

`work finish --dry-run` 只预览，`finish` 与 `resume` 显示可恢复归档入口；归档的 manifest 保存原工作树、提交和相对路径。部分移动失败会保留工作树与已归档内容，重试不会覆盖归档，也不会丢弃新出现的 tracked/untracked 文件。归档保留原始内容及其磁盘占用，不承诺直接释放空间；确认无用后可另行清理恢复目录。跨文件系统不能原子移动时明确保留，不静默退回递归删除或复制。

用户的记录方式和命令不变，无需执行存储维护命令。依据是[恢复日志只服务未完成事务](https://sqlite.org/tempfiles.html)：保留故障恢复能力，避免把已完成操作的完整副本当作业务历史永久累积。
