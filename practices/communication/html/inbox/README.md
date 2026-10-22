# Interactive Personal Inbox

这是一个零新增依赖的本机 Web App。`personal/inbox/inbox.md` 保存个人关注内容，关联 Work 保存事项说明与执行记录；Web App 与 `bin/work` 都调用根目录 [`workflow/`](../../../../workflow/README.md) 的维护逻辑。HTML、CSS 与 JavaScript 是无内容应用 shell，页面启动后通过固定 API 读取和原子回写 Markdown。

运行时需要 Python 3.9 或以上；Git 资源管理使用各平台对应的文件锁，支持 Windows、macOS 和 Linux。从 workspace 根目录运行：

```text
python practices/communication/html/inbox/scripts/inbox_app.py --repo-root /path/to/workspace
```

默认地址是 `http://127.0.0.1:8765/`。使用 `--port <n>` 选择其他端口，或加 `--open` 请求系统浏览器打开。服务只监听 loopback；端口占用会打印可执行错误，不会切换到外网地址。

## 产品边界

- Inbox 内容写入配置 root 下固定的 `personal/inbox/inbox.md`；whole-document PUT 携带 revision，并与 CLI 共用名称解析、身份和版本检查逻辑。
- 写入使用同目录临时文件、flush/fsync、紧邻 replace 的第二次 stale 检查和 `os.replace`；409 不覆盖来源，浏览器保留草稿。
- Work 文档接口只枚举、读取 `agent-data/projects/**/work/active|archive/*.md` 的文档；可以只读显示完整正文，没有任意文件 API。
- Work Markdown 先解析成安全 AST，浏览器用 DOM API 构造 H1–H4、段落、列表、代码、引用与链接。来源 HTML 只显示为文字。
- Issue 只保存和打开安全 HTTPS URL；没有 GitHub token、provider API 或远端 mutation。
- 没有数据库、localStorage、登录、云服务、daemon、框架或包安装。Work 和 Inbox 正文仍是直接可读的 Markdown。

日常维护见 [`practices/collaboration/workflow.md`](../../../collaboration/workflow.md)。Work 工具的 pause/cancel/reopen/finish 会按状态契约同步 Inbox 分类或可见性；Inbox 的个人 arrange 仍只改变关注安排。任何一种同步都不控制、中断或暂停运行中的 Agent 或平台任务。

运行 `bin/work` 可直接查看常用操作，无需先通读手册。已有事项保留原上下文；计划由 `plan` 命令版本化，步骤可选 checkbox 与语义名称依赖图；`record` 追加 append-only 执行记录，时间和计划版本由工具填写，并可在同一写入操作提供摘要。接手输出默认折叠 `## 历史记录`，`resume --full` 展开；缺少标准章节的旧文档完整返回。`--json` 保持完整原始记录，图形界面的正文读取不受折叠影响。

详细 Markdown grammar 见 [`personal/inbox/schema.md`](../../../../personal/inbox/schema.md)。

## 验证

Windows PowerShell 在仓库根目录使用 `python -X utf8 ./bin/work`，无需安装或符号链接权限；
返回的后续命令可以直接在 PowerShell 粘贴执行。GUI 的“复制给 AI”采用同一入口。
需要短命令时运行 `python -X utf8 ./bin/work install --bin-dir ./bin`，
会创建 `work.cmd`，保留已有程序，不修改 PATH 或执行策略。未加入 PATH 时用 `./bin/work.cmd`。
换 Python 安装位置后需核对并重新安装入口。Windows 回归测试使用 UTF-8：
`$env:PYTHONUTF8='1'; python -m unittest discover -s practices/communication/html/inbox/tests -p "test_*.py"`。

```text
python -m unittest discover -s tests -p "test_*.py"
python -m unittest discover -s practices/communication/html/inbox/tests -p "test_*.py"
python -m compileall -q workflow
python -m py_compile practices/communication/html/inbox/scripts/inbox_app.py
node --check practices/communication/html/inbox/assets/inbox.js
```

浏览器验证使用临时 fixture workspace，不能向正式 Inbox 写虚构条目。证据约定见 [`docs/README.md`](docs/README.md)。

## 视觉与导航

深青导航栏、浅灰蓝列表区与白色正文区建立空间层级；需要处理、进行中、等待、完成分别使用杏橙、蓝、紫、绿的状态标签。条目使用统一行布局，避免大面积分组底色与多层卡片。中文标题、摘要与正文各自保持清晰字号，摘要自然换行，不以固定行数隐藏判断依据。桌面双栏，720px 以下使用独立列表与详情路由；页面内“返回”始终回到事项列表，并清除活动选中状态；键盘焦点仍返回来源条目。进入窄屏详情会回到页首，保存完成替换编辑历史，浏览器 Back 保留正常导航能力。

字体来自本机，不依赖外部请求。样式位于 `assets/inbox.css`，可见焦点、窄屏触控与减少动画规则随组件维护。

### 选择控件与快览

状态和 Work 选择包含统一的展开菜单。方向键/Home/End浏览，Enter或Tab提交，Esc取消；表单选择只更新草稿，工具栏状态选择按原协议保存。保存失败会同步回滚按钮文案和颜色，保留焦点及错误提示。

事项默认显示列表摘要；鼠标在条目上实际移动后停留450ms，用 Work 的 `## 事项说明` 纯文本预览替换原摘要的可见区域。原摘要保留自然布局尺寸，预览在同一区域内裁切并轻微渐隐，因此当前行与后续行的位置、高度不变；不插入章节标题、不展开行、不出现浮层。移出整个事项180ms后恢复摘要，键盘聚焦等价预览，Esc立即恢复；触屏或点击标题直接进入完整详情。没有独立说明、读取中或读取失败时保留摘要，不用占位文案替换。

滚动取消待触发计时，并在最后一次滚动后的220ms内不排定新预览；静止光标不触发连锁预览。异步结果仅供当前仍有效的预览使用；窗口尺寸改变即恢复摘要，布局始终由原文自然计算，不持有旧像素高度。预览为视觉辅助且不重复加入可访问名称，完整说明仍在可访问详情中。

列表每项始终显示复制图标；鼠标操作的竖排按钮为 28px，触控主指针下为 44px，详情保留复制和次要编辑图标；复制状态使用图标与可访问文本反馈。新增事项为加号加文字，窄屏 Inbox 标题复用应用图标。
详情顺序为个人安排与工作进展、事项说明、当前推进、可展开的执行细节与来源。列表和详情均提供“复制给 AI”：先读取最新 Inbox，按稳定身份找到事项，再复制一句接手请求及服务端生成的 `bin/work --root … resume 项目/事项名` 命令。命令用当前实现入口和真实数据目录，正确转义名称与路径，只读取目标事项；无需读取整份 Inbox。条目删除时保留页面并提示刷新，改名和分类变化后仍按原身份定位。旧版数据缺少 CLI 字段时才回退到精确 `rg` 命令。

复制不会修改工作状态或执行资源操作。Work 文档 API 只读；其 `sections.description/progress` 返回章节安全 AST 与原 Markdown，`remainingBlocks` 保留其他内容，`absolutePath` 定位原件。

状态与 Work 选择仍使用 [共享组件层](../shared/README.md)，本次事项展开不改变共享选择组件。修改 Python 解析接口后须重启本地服务。

### 分类折叠

四个分类标题均可点击或通过 Enter/Space 展开、收起；已完成在每次打开页面时默认收起，其他分类默认展开。同一页面内刷新内容、编辑和切换详情保留展开状态，不写入 Inbox 数据。

### Work 读取性能

列表仅枚举 `agent-data/projects/<context>/work/active|archive/*.md`，不会递归扫描发布包、证据或嵌套测试夹具；列表提取标题，Inbox 关联校验只枚举路径。单篇读取直接校验路径及符号链接实际目标，读取后再次核对目标，避免扫描整个项目目录。每次请求读取当前文件，不引入缓存陈旧窗口。

### 项目筛选

列表顶部使用与状态一致的共享选择组件，项目图标、当前名称与事项数在一行呈现；展开菜单将项目名与数量对齐，并标明选中项。支持方向键、文字匹配、Enter/Tab 确认与 Esc 取消；窄屏触控目标至少 44px。可选择全部项目、单个项目或未分类；项目及数量从 Inbox 关联 Work 路径推导，不新增手填字段，也不依赖 Work 正文读取。没有关联路径的事项归入未分类，workspace 显示为工作空间。分类计数随筛选更新；筛选只改变视图，保存仍提交完整 Inbox。

同一页面内切换详情、返回和刷新内容保留筛选；重新打开页面恢复全部项目。编辑时暂时禁用筛选以保护草稿；保存到其他项目后跟随已保存事项，刷新后原项目不再存在则恢复全部。已完成仍默认收起，归档事项仍归入已完成，并在摘要说明结束结果。

项目推导及过滤回归：`node --test practices/communication/html/inbox/tests/project-filter.test.cjs`。

### 编辑冲突恢复

保存遇到 409 时，表单内提供“读取最新版本并保留草稿”，窄屏也可用。读取后显示最新事项的全部可编辑字段供对照，保留当前输入，不自动重试保存。原事项已删除或改名时不刷新写入基线、不自动创建，保留草稿并明确提示；读取失败可重试。

CLI 与 GUI 的写入共用本机锁，并在替换文件前再次核对版本。外部编辑器不参与这把锁；维护操作期间不要同时从外部编辑器改写同一个文件。需要并发编辑时用 `work edit` / `work save`，让版本信息随副本保存。

### 自动同步

服务内只有一个共享观察线程，每 750ms 检查 Inbox、工作流状态与允许的 Work 文件元信息；未变化时不解析正文。`/api/events` 通过 SSE 通知页面版本变化，`/api/snapshot` 在工作流锁内读取关联数据；文件读取期间再次变化则重试。仍以 Markdown 为来源，不增加数据库、框架或第三方依赖。

页面合并连续通知，只刷新变动内容，保留项目筛选、分类折叠、选中事项、滚动位置与键盘焦点。滚动、打开选择菜单、选择文本和编辑期间延后更新；草稿及其保存版本不被后台更新覆盖，保存冲突仍通过现有 409 对照流程处理。页面隐藏时关闭订阅，返回或断线重连后核对最新版本；读取失败保留当前内容并重试。

### 同一事项的多个工作树

`work add-worktree '项目/事项' --name 开发 --repo /仓库路径` 可在开始工作后随时创建第一个工作树；追加时使用另一个名称，例如 `--name 验收 --at <提交>`，仓库唯一时可省略 `--repo`。创建和关联一次完成。同名重复请求复用，显式路径、仓库、分支或起点冲突时报错；`start --worktree` 复用同一逻辑和默认名称“开发”，兼容之前未命名的受管工作树。固定提交模式仍创建独立分支，清理时核对 HEAD 是否进入基线分支；未合入、共享、有修改或忽略文件的树仍保留。已经清理的名称不会悄悄创建新目录，重新创建时使用新名称。

### 关注顺序与置顶

列表每条事项右侧竖排显示同尺寸的复制、上移、置顶纯图标，无需进入详情；悬停提示与可访问名称说明动作，置顶图标以选中态表示持续状态。详情工具栏也保留相同操作。置顶是持续状态，固定在当前区块顶部；上移是一次移到所属置顶组或普通组的最前面，不是逐项上移。新事项默认追加在区块末尾，不会改变已排好的顺序。

整条事项可按住拖动，无独立手柄；右侧操作按钮保持普通点击，不触发拖动。拖到同组事项前后调整顺序；拖到区块标题可移入该区块，包括空区块和收起的区块。插入线显示落点。置顶与普通组之间需先明确切换置顶状态，拖动不会隐式切换。触屏和键盘可使用详情的按钮和“移到区块”选择。筛选只改变显示，排序保存整份 Inbox，保留未显示的项目。

顺序与置顶写入同一 Markdown 来源，刷新、编辑和 CLI 更新均保留；保存期间暂停自动刷新和其他写入操作。版本冲突不覆盖后台内容，显示未保存提示，读取最新后可重新操作。

### 未保存输入与导航

编辑中切换事项、返回列表、新建或使用浏览器 Back 时，阻止离开并在表单提示先保存或点击取消；草稿保持原样。保存期间阻止这些导航以及取消编辑，避免异步保存返回到错误事项。关闭或刷新页面触发浏览器原生未保存提醒；是否显示由浏览器决定。草稿仍只在当前页面内存中，不承诺崩溃或强制退出后恢复。
