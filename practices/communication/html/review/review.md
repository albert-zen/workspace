# HTML 审阅工作台

审阅工作台是 HTML 呈现模块的第一个正式应用。它解决当前已经存在的真实问题：先判断 Agent 对问题的理解是否成立，再把每项问题映射到实际文件和改动块，完成阅读、批注、编辑、接受或拒绝，并在工作空间内容未变化时显式应用结果。

它不是所有 HTML 页面的基础框架。通用呈现原则见[图形化呈现](../../presentation.md)；这里的 diff、批注锚点、审阅决定和写回协议都属于审阅领域。

## 审阅链路

1. 首屏直接列出 Agent 提炼的问题，不添加重复的 `Start here` 根卡片。
2. hover 或键盘聚焦问题时，预览映射到它的真实 diff；点击后进入相关文件。
3. 完整审阅支持 Markdown、源文件和 Diff、行号、before/after、行或选中文本批注和直接编辑。
4. 问题说明、文件栏、批注栏和命令栏可独立收起，并在原位置留下带文字的恢复入口。
5. `问题总览` 是稳定上级路由，显式按钮和浏览器 Back 都能返回。
6. Zen 模式只保留当前文档和退出入口，并保留编辑与审阅状态。
7. 页面导出 patch、反馈或绑定原 bundle 的 response；它不直接获得任意工作空间写权限。

## 组件边界

当前应用自己维护：

- 问题列表与真实 hunk 预览；
- 文件导航和 Markdown、源码、Diff 视图；
- before/after、行号和差异状态；
- 行级或选中文本批注；
- accept/reject、编辑状态和 stale anchor；
- bundle、response、check 和 apply 协议。

页面 shell、token、证据块、状态标记或折叠交互只有在另一个 HTML 场景出现相同约束并实际复用时，才提升到通用层。提升时必须保持本应用的键盘导航、可见焦点、浏览器 Back、窄屏布局和离线单文件交付。

## 输入模式

- **Git review**：固定 exact base/head，从 Git 派生文件和 hunk，再补充问题、理由和证据。
- **Reflection review**：用户指出的文件先作为 source material；按 Reflection 工作流取证后形成零个或多个提案文件。
- **Prepared bundle**：读取符合 [`schemas/review-bundle.schema.json`](schemas/review-bundle.schema.json) 的现有 bundle。

## 生成和打开

当前脚本支持 Python 3.9 及以上。从工作空间仓库根目录生成 Git bundle：

```text
python3 practices/communication/html/review/scripts/workbench.py git-bundle --repo <repo> --base <exact-base> --head <exact-head> --story <story.json> --output <bundle.json> --title <title>
```

渲染离线单文件页面：

```text
python3 practices/communication/html/review/scripts/workbench.py render --bundle <bundle.json> --output <report.html>
```

在 story 中先抽离用户真正提出的问题，再把每个问题映射到实施它的文件和 hunk。未覆盖的问题和未解释的 hunk 必须保持可见，不能用“全部完成”的文案掩盖。

生成后直接打开报告。聊天仍给出核心判断和报告绝对路径，不能把 HTML 当成完整回答的替代品。

## 显式应用

离线 HTML 不声称可以直接写任意工作空间。页面导出绑定原 bundle digest 的响应包，再由宿主显式检查和应用：

```text
python3 practices/communication/html/review/scripts/workbench.py check --repo <repo> --bundle <bundle.json> --response <review-response.json>
python3 practices/communication/html/review/scripts/workbench.py apply --repo <repo> --bundle <bundle.json> --response <review-response.json>
```

应用前核对路径边界和所有目标的内容 hash。预检查发现任一文件 stale 时整批不写，并保留用户编辑和批注；当前应用不自动三方合并。

## 审阅不变量

- source material 与 modification target 是不同角色。
- 批注由选中文本、source range 和 proposal fingerprint 锚定；编辑后，旧理由与批注必须标为待重新锚定。
- 问题选择可以筛选文件，但不能触发无解释的页面跳转。
- 收起区域必须在相邻位置留下带文字、键盘可操作的恢复入口。
- exact Git commit、内容 hash 和证据状态必须诚实；moving branch 或 merge ref 不能标成 exact head。
- 键盘顺序与视觉顺序一致，每个可操作控件都有可见焦点；hover 预览同时支持 focus。
- 浏览器 Back 和页面内返回都能回到问题总览，不制造不可逆导航。

## 验证

从仓库根目录运行：

```text
python3 -m unittest discover -s practices/communication/html/review/tests -p "test_*.py"
node --check practices/communication/html/review/assets/workbench.js
python3 -m py_compile practices/communication/html/review/scripts/workbench.py
git diff --check
```

真实交付还要打开生成页面，检查问题预览 → 完整审阅 → 问题总览、每个面板的收起 → 恢复、Zen、Markdown/源码/Diff、行级与选中文本批注、编辑后的锚点失效，以及成功与 stale apply 路径。桌面和窄屏都不能产生页面级横向溢出，键盘必须能完成问题选择、文件切换、视图切换、批注和返回。

## apply 的并发与恢复边界

response 必须符合 schema：accepted 仅接受布尔值，desiredText 必须显式给出，null 才表示删除；非法输入在创建锁或写入目标前失败。同一目标根下的 apply 通过 `.work-review.lock` 串行化，锁文件保留，不手动删除正在使用的锁。

所有暂存完成后再次核对全体目标，逐文件替换前再核对。它不是文件系统级比较交换，也不是多文件事务：不参与该锁的外部编辑器仍可能在最终检查与替换间写入。apply 期间停止其他写入；发生中途失败时错误报告已写入列表与恢复副本位置，不自动回滚覆盖后来的编辑。原内容复制到权限受限的系统临时目录，成功结果也返回 recoveryCopy；先检查差异，再人工恢复需要的文件。不要宣称对任意并发写入或断电提供原子事务保证。
