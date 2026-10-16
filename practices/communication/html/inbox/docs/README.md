# Inbox 浏览器验证证据

Personal Inbox 不再保存 checked-in 内容截图或派生 HTML。每次涉及交互、布局、写回或错误模型的实现，在仓库外 evidence 目录用临时 fixture workspace 保存 exact-head 证据。

最小浏览器 journey：运行时加载四状态；新增带 Issue 与 Work 的事项；重载；编辑；依次移动状态并核对 Markdown checkbox；打开 Work 文档并检查 provenance、目录、H1–H4、段落、列表、代码和链接；制造外部编辑触发 409 并确认草稿与外部版本都保留；检查无效 Issue、失效 Work 与写入失败；验证桌面和 390px 的 list/detail route、浏览器 Back、焦点、键盘、console、network 与页面级 overflow。

截图用于对照 current Inbox 与 review workbench 的既定层级、排版、focus 和窄屏行为，但不能替代行为测试。证据应记录 source/implementation viewport、exact head、测试命令与结果；仓库内只保留长期产品说明。
