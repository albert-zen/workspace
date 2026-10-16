# Review Workbench visual reference

公开版本不包含私人历史审阅截图。使用公开虚构示例生成当前界面；截图验证保存在发布检查材料中。

## Visual direction

The workbench uses deep teal navigation, a blue canvas, white reading surfaces and teal selection markers. Blue evidence headers, muted violet context labels and warm apricot annotation accents preserve color hierarchy with restrained surface coverage. Problem navigation uses aligned rows instead of stacked cards. Problem descriptions remain visible in the overview; the actual file and hunk stay adjacent in the preview. Local Chinese sans-serif fallbacks improve reading without network dependencies. The full-review toolbar separates the file path from actions. On narrow screens the top actions scroll within their own group, preserving the page width and access to every control.

Generate a new offline report from a fixed bundle to inspect the current stylesheet; existing generated HTML embeds its original CSS and must be rendered again to receive these changes.

### 文件快览与动效

文件列表悬停或键盘聚焦可快览完整路径、增删数量、关联问题和前三行增删内容，不改变当前审阅文件；点击「⋯」可固定快览，触屏同样可用。Esc、外部点击或再次点击入口关闭；鼠标移入快览后仍可阅读。控件及内容切换加入轻量过渡，并尊重减少动态效果偏好；快览全部从内嵌bundle与当前编辑状态读取，保持离线单文件。

文件快览及通用浮层样式复用 [共享组件层](../../shared/README.md)。`render` 将共享源码与页面源码依次内联，生成结果保持离线单文件；修改共享组件后须重新生成已有审阅 HTML。
