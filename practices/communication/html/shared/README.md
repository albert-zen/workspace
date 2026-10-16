# 共享 HTML 组件

Inbox 与 Review Workbench 共用的轻量组件层。无依赖、无网络请求，不读取业务数据或执行保存。页面只负责数据、路由和自己的布局。

## 使用

加载 `components.css`，再加载页面 CSS；加载 `components.js`，再加载页面 JS。每个页面创建一个实例，供本页所有控件共享：

```js
const ui = WorkspaceUI.create({
  selectOption(option) {
    // 可选；业务解释由页面提供。tone: amber / blue / purple / green。
    return {label: option.textContent, description: '', tone: 'blue'};
  }
});
ui.enhanceSelect(document.getElementById('filter'));
ui.bindPreview(button, panel => {
  const title = document.createElement('h3');
  title.textContent = '这条记录的完整标题';
  panel.append(title);
}, true); // true 增加点击固定/再次点击关闭；默认 hover 和键盘 focus
```

- `enhanceSelect(select)`：增强单选原生 select。保留原生 value、input/change 事件；将原 id 转给可聚焦的 combobox，原生控件 id 变为 `<id>-native`。调用者保留 select 引用；重复增强和空选项列表直接跳过。动态修改 options 后应重新渲染控件。
- `syncSelect(select)`：页面异步保存完成、失败回滚或修改 disabled 后调用，同步显示。组件只传递选择事件，不决定何时写入或如何处理错误。
- `bindPreview(anchor, build, toggle=false)`：页面用 DOM API 填充只读内容。内容应不含交互控件；链接如需展示请转为文字。组件管理350ms悬停、焦点、点击固定、Esc/外部点击、悬停/焦点保持与屏幕边界。
- `closeLayer()`：路由切换、重建内容前调用，清理浮层、待触发计时器和可访问属性。外层滚动、resize也自动清理。实例和全局监听器随整页销毁，不作为反复挂载的SPA子组件创建。

Select 支持方向键、Home/End、PageUp/PageDown、输入文字匹配、Enter/Space/Tab提交、Esc取消。键盘浏览只滚动选项菜单，不滚动宿主页面。保存时以 `aria-disabled` 禁止再操作且保留焦点。

## 视觉约定

页面提供基础变量 `--text`（或 `--ink`）、`--muted`、`--faint`、`--line`、`--accent`、`--mono`。选择色族可用 `--ui-amber/blue/purple/green` 和对应 `-soft` 覆盖，默认值在共享样式中。页面负责触发器尺寸、侧栏/工具栏布局，以及快览中专属的差异数量等内容；通用菜单、快览表面、箭头、动画关键帧由共享样式维护。

减少动态效果规则同时覆盖两页。浮层定位用未受入场变换影响的布局高度，防止动画期间的测量误差。

## 交付与验证

- Inbox 的现有 `/assets/inbox.js` 与 `/assets/inbox.css` 响应分别拼接共享源码和页面源码；没有新增静态路径或扩大文件读取范围。修改Python装配逻辑需要重启服务；之后JS/CSS修改刷新即可。
- Workbench 的 `render` 将同一份共享JS/CSS内联到生成HTML，组件先于页面初始化；无需CDN或运行服务器。已有HTML需重新生成。
- 保持共享文件和页面源文件相对目录一起分发，不再单独复制某页 assets 当作完整运行时。
- Inbox测试通过实际HTTP核对拼接内容；Review测试核对共享源码恰好内联一次、初始化顺序以及无外部脚本/样式依赖。交互仍须浏览器验证。

修改共享组件时同时核查两页的菜单/快览、Esc、焦点、窄屏和保存失败回滚；不要把业务状态或文件结构移入这里。
