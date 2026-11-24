# Validation

From the workspace repository root:

```text
python3 -m unittest discover -s practices/communication/html/review/tests -p "test_*.py"
node --check practices/communication/html/review/assets/workbench.js
python3 -m py_compile practices/communication/html/review/scripts/workbench.py
git diff --check
```

The loopback HTTP tests start real `serve` processes on ephemeral ports and cover host/origin checks, restart and revision conflicts, direct check/apply, recovery, changed targets and bounds on file access.

With Google Chrome and Playwright available to Node, run the optional browser acceptance test:

```text
node practices/communication/html/review/tests/live_browser.cjs
```

It exercises the real review controls, including keyboard decisions, line, selection and theme comments, hand-edit drafts, a failed save with retry, competing tabs, direct write and recovery receipt, 390px layout and offline download. Playwright is only used by this optional test.

The offline tests cover exact Git objects, normalized hashing, reflection bundles, single-file rendering, theme-first shell hooks, claim-to-hunk mapping by `anchorText`, local restore controls, overview navigation hooks, successful application and stale-base rejection. They do not replace opening a real report and checking:

1. 主题总览 → 主题审阅 → 主题总览，浏览器 Back 与页面内返回都可用。
2. 主题栏、案例栏和工具栏的收起 → 恢复入口，专注模式退出。
3. 文件切换、按块接受 / 不接受 / 待定，以及同一文件部分接受。
4. 行批注和选中文本批注；手改后决定回到待定、旧批注标为需重新锚定；撤销手改。
5. 案例来源为空、证据有限和反向对照的显示。
6. 有待定块时“准备应用”停用；全部决定后可打开应用对话框，下载响应包并由 `check` / `apply` 核对。
7. 桌面 1440 与窄屏 390 都没有页面级横向溢出，键盘焦点可见。

真实浏览器验收可以直接读取 `window.__workbenchDebug.response()`、`.state()`、`.units()` 和 `.groups()`，再把响应包交给 `workbench.py check`；该接口只读，不写文件或发请求。

本轮真实浏览器与 CLI 已走通的路径：

- reflection：同一文件接受一块、拒绝另一块，`desiredText` 只写入接受块，拒绝块保留基线。
- git-review：接受一处、拒绝其余，拒绝的文件恢复基线；全部接受的 `desiredText` 与 head 逐字相同。
- git-review：新增文件全部拒绝时输出 `null` 并删除；删除文件全部接受时输出 `null`，全部拒绝时恢复基线。
- 手改后该块回到待定，其他块的决定保留；重复内容块经手改后不再串决定。
- 仍有待定块时“准备应用”停用；全部决定后可打开对话框并导出响应包。

可选浏览器测试已覆盖显式块的部分接受、键盘决定、输入焦点与 390px 宽度检查。修改自动分块、重复块 key 或 `lineRange` 映射时，仍需按上述清单检查对应样例。
