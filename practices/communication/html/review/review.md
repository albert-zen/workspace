# HTML 审阅工作台

审阅工作台是 HTML 呈现模块的第一个正式应用。它按**主题**组织一次变更，让人先判断“这组改动为什么存在、证据够不够”，再逐块、逐行决定接受、不接受或手改。每个决定都绑定实际文件内容和精确 diff，而不是只留一句总结。

这里的 diff、批注锚点、块决定、案例来源和写回协议都属于审阅领域。通用呈现原则见[沟通与呈现](../../../communication.md#5-按带宽选载体与-html-应用)。

## 审阅链路

1. **主题总览**：首屏按主题列出变更。仍待决定的排在最前，其次部分接受、已接受、不接受；颜色表示需要你做什么，不表示装饰。优先级（阻塞 / 需关注 / 事实依据 / 常规 / 说明 / 推断）用另一组小标签表达。
2. **主题审阅**：左栏常驻主题列表，中间按文件显示改动块，右栏显示“为什么改”、案例参考来源、当前块决定和批注。
3. **按块决定**：每个 Markdown 场景（一级/二级标题段）或源码改动块都有“接受 / 不接受 / 待定”。决定只影响这个块；同一个文件可以部分接受。
4. **按行批注**：悬停行号旁的 `+`，或选中一句话后点浮出的“批注选中文字”。批注记录行号、选中文本和块指纹；手改后旧批注标记为“需重新锚定”。
5. **手改**：编辑器只替换当前块。保存后该块回到“待定”，必须重新接受才会进入最终文件；可撤销上次手改。
6. **案例参考**：右栏按“可借鉴 / 反向对照 / 证据有限 / 事实 / 参考”列出主题引用的真实案例、失败对照和证据边界；没有来源也会如实写明，不把推断说成事实。
7. **显式应用**：底部汇总全局决定。仍有待定块时“检查并应用”停用；可逐块决定，或把剩余待定明确设为不接受。
8. **返回与专注**：浏览器 Back 和页面内返回都回到主题总览；专注模式只保留当前主题与改动；收起的面板在原位置留下带文字的恢复入口。

## 主题、块与文件

- 一个主题来自 `story.claims[]` 的一项 claim。`theme` 指定显示名，未提供时用 `label` 或 `text`；`priority` 可选，未提供时按 `epistemicStatus` 推导。
- 准备审阅材料的 Agent 可以明确指定决定块。同一标题下相邻的两处修改也可以分别决定。既有材料没有提供块时，页面沿用 Markdown 标题或连续改动的分法。
- 一个文件可以有多个主题。生成器先在 bundle 的 hunk 上按 `anchorText` / `lineRange` 写入 `claimIds`；页面在未手改时优先使用这个精确映射，再退回 `anchorText` / `lineRange`，最后才按文件级 claim 分配。一个文件只有一个 claim 时覆盖整个文件；多个 claim 都未命中且没有锚点时，未命中的块进入“未映射改动”，不能当作已解释。
- 决定按“块的实际变化内容”记录。手改会改变块内容，因此该块的旧决定失效并回到待定；其他块的决定保留。
- 文件级 `desiredText` 由块决定组合：以基线内容为底，接受的块写入提案（含手改），不接受和待定的块保留基线。折叠块的“接受”是接受整个块内的所有变化。
- 只有末尾换行不同、正文完全相同的文件目前不作为可审块显示；页面会明确提示“不会写入这个变化”，需要时用整文件编辑处理，避免把它误算成已接受。

## 决定如何变成文件

文件级协议保持简单，块级决定在页面内组合：

- **reflection bundle**：接受至少一个块的文件会输出组合后的 `desiredText`；全部不接受的文件不写入。
- **git-review**：所有块都不接受的文件输出基线内容，用于把工作树恢复到这个变更之前；新增文件全部不接受时输出 `null` 删除文件；删除文件全部接受时输出 `null` 删除文件。
- 部分接受只写入接受的块；被拒绝或仍待定的块保留基线。页面在仍有待定块时停用应用入口，避免把“还没看”当成“不接受”。

响应包除 `files` 外还带 `decisions[]`：主题、块、文件、行号、决定和是否手改。它用于审计“哪些块被接受”，不改变宿主按文件写入的协议。

## 案例参考来源

`sources[]` 是 bundle 内的事实材料，不是装饰：

- `role` 或 `kind` 决定颜色和标签：可借鉴、反向对照、证据有限、事实、参考。
- `excerpt` 是本人能直接阅读的案例摘要；`locator` 是原始记录、文件或对话位置。
- `note` 可写为什么不适用或还缺什么。
- 主题通过 `evidenceIds` 引用来源。没有来源时页面明确写“没有记录案例来源”，不能用空白暗示已证实。

## 输入模式

- **Git review**：固定 exact base/head，从 Git 派生文件和 hunk，再补充主题、理由和证据。
- **Reflection review**：用户指出的文件先作为 source material；按 Reflection 工作流取证后形成零个或多个提案文件。
- **Prepared bundle**：读取符合 [`schemas/review-bundle.schema.json`](schemas/review-bundle.schema.json) 的现有 bundle。

## 生成和打开

当前脚本支持 Python 3.9 及以上。从工作空间仓库根目录生成 Git bundle：

```text
python3 practices/communication/html/review/scripts/workbench.py git-bundle --repo <repo> --base <exact-base> --head <exact-head> --story <story.json> --output <bundle.json> --title <title>
```

生成 bundle 后，在目标工作树启动本机应用。把 `<repo>` 指向希望写回的仓库目录。

```text
python3 practices/communication/html/review/scripts/workbench.py serve --repo <repo> --bundle <bundle.json>
```

服务默认在 `127.0.0.1` 选择可用端口，在终端打印 URL 并打开浏览器。可用 `--no-open` 只打印地址，或用 `--port 8766` 固定端口。审阅期间保持进程运行，结束后按 Ctrl+C。再次运行同一命令可继续上次的审阅。

决定、手改和批注自动存档。页面底部显示保存状态，显示“已保存”后即可关闭。输入中的手改和批注草稿也会保留。点“保存手改”后，新正文进入审阅并回到待定。默认状态文件与 bundle 同目录，例如 `review.json` 对应 `review.state.json`。使用 `--state <path>` 可以指定位置。两个窗口同时修改时，旧版本会收到冲突提示，可下载本页草稿并载入最新版本。临时断线可直接重试保存。

需要传给他人离线审阅时，渲染自包含页面：

```text
python3 practices/communication/html/review/scripts/workbench.py render --bundle <bundle.json> --output <report.html>
```

在 story 中先抽离用户真正提出的问题，再把每个问题映射到实施它的文件和块。使用自动分块时，同一文件有多个主题就给各主题加 `anchorText` 或 `lineRange`。`anchorText` 取新增或删除行里稳定、唯一的片段。让用户看到尚未解释的变化和缺少证据的问题。

需要让用户分别决定时，在 Git story 或 reflection proposal 的顶层写 `blocks`。每一块写清可独立接受的变化，使用对应版本的行范围定位。以下示例替换 `AGENTS.md` 的第 15 行。

```json
{
  "id": "delegate-goal",
  "filePath": "AGENTS.md",
  "claimId": "agent-responsibilities",
  "title": "可以委托完整目标",
  "reason": "让事项负责人能从调查推进到验收。",
  "effect": "收到目标后，先找依据，再提出方案和验证办法。",
  "evidenceIds": ["user-2026-11-17"],
  "oldRange": [14, 15],
  "newRange": [14, 15]
}
```

`oldRange` 和 `newRange` 使用从零开始的半开区间。插入的旧范围可为空，删除的新范围可为空。按文件顺序给出范围，确保各块不重叠且覆盖文件的全部差异。`claimId` 指向已有主题，`evidenceIds` 指向该块所依据的来源，无可列来源时用空数组。可用 `dependsOn` 列出需要一起考虑的其他块。每个块有自己的决定、理由与证据。手改只替换该块的新文本，并让该块回到待定。

本机审阅交付服务地址，离线审阅交付生成的报告。聊天仍给出核心判断和报告绝对路径，不能把 HTML 当成完整回答的替代品。

## 显式应用

在本机应用中，逐块决定后点击底部的**检查并应用**。页面先等待当前修改存盘，再检查目标并写入。只想验证时可点**仅检查**。应用结果列出处理过的文件和恢复副本位置，重新打开页面后可以点“查看应用记录”。如果目标已被外部修改，预检查会列出冲突路径及期望、实际哈希，并保留审阅记录。

离线 HTML 保留“准备应用”和“下载响应包”。收到响应包后，在目标仓库用原 bundle 检查和应用：

```text
python3 practices/communication/html/review/scripts/workbench.py check --repo <repo> --bundle <bundle.json> --response <review-response.json>
python3 practices/communication/html/review/scripts/workbench.py apply --repo <repo> --bundle <bundle.json> --response <review-response.json>
```

应用前核对路径边界和所有目标的内容 hash。预检查发现任一文件 stale 时整批不写，并保留用户编辑和批注；当前应用不自动三方合并。

## 审阅不变量

- source material 与 modification target 是不同角色。
- 进入审阅时按注意力排序一次，之后保持顺序稳定；决定改变颜色和进度，不让卡片在鼠标下跳动。
- 颜色必须对应可解释的状态：待决定、已接受、不接受、部分接受；案例角色用另一组颜色，不能混用。
- 批注由选中文本、行号、块和 proposal fingerprint 锚定；手改后，旧理由、批注和决定必须标为需要重新确认。
- 问题选择可以筛选主题或文件，但不能触发无解释的页面跳转。
- 收起区域必须在相邻位置留下带文字、键盘可操作的恢复入口。
- exact Git commit、内容 hash 和证据状态必须诚实；moving branch 或 merge ref 不能标成 exact head。
- 键盘顺序与视觉顺序一致，每个可操作控件都有可见焦点；hover 预览同时支持 focus。
- 每个块的决定必须能追溯到最终文件内容；不能把待定块悄悄当成接受或拒绝。
- 浏览器 Back 和页面内返回都能回到主题总览，不制造不可逆导航。

## 验证

从仓库根目录运行：

```text
python3 -B -m unittest discover -s practices/communication/html/review/tests -p "test_*.py"
python3 -B -m unittest discover -s tests -p "test_*.py"
node --check practices/communication/html/review/assets/workbench.js
node --check practices/communication/html/review/assets/live.js
python3 -m py_compile practices/communication/html/review/scripts/workbench.py
git diff --check
```

真实交付还要打开生成页面，检查主题总览 → 主题审阅 → 主题总览、每个面板的收起 → 恢复、专注模式、文件切换、按块接受/不接受/待定、行与选中文本批注、手改后的决定与锚点失效、撤销手改、案例来源、成功与 stale apply 路径。桌面和窄屏都不能产生页面级横向溢出，键盘必须能完成主题选择、块决定、批注、返回和退出编辑。

页面暴露只读的 `window.__workbenchDebug`（`response()`、`state()`、`units()`、`groups()`），用于离线浏览器验收与导出结果核对；它不写文件、不发请求。生产使用不需要调用它。

## apply 的并发与恢复边界

response 必须符合 schema：accepted 仅接受布尔值，desiredText 必须显式给出，null 才表示删除；非法输入在创建锁或写入目标前失败。同一目标根下的 apply 通过 `.work-review.lock` 串行化，锁文件保留，不手动删除正在使用的锁。

所有暂存完成后再次核对全体目标，逐文件替换前再核对。它不是文件系统级比较交换，也不是多文件事务：不参与该锁的外部编辑器仍可能在最终检查与替换间写入。apply 期间停止其他写入；发生中途失败时错误报告已写入列表与恢复副本位置，不自动回滚覆盖后来的编辑。原内容复制到权限受限的系统临时目录，成功结果也返回 recoveryCopy；先检查差异，再人工恢复需要的文件。不要宣称对任意并发写入或断电提供原子事务保证。
