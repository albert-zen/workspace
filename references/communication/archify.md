# Archify：技术图的 HTML 呈现

上游：[tt-a1i/archify](https://github.com/tt-a1i/archify)。本地参考仓库为 [archify](../archify/)，通过 Git submodule 固定版本；本次接入 v2.16.0，提交 `c826e6c3a7abad19c0f3cd1ca57207d54b1ad8de`。许可为 MIT。父仓库固定 gitlink，不自动跟随上游。

## 何时使用

需要可探索的架构、流程、时序、数据流或生命周期图时，可使用 Archify 的结构化 JSON 输入与 HTML/SVG 渲染能力。Agent 负责从真实来源理解结构、表达含义，程序负责校验和生成；校验通过不能证明输入就是实际系统的正确架构。

普通短说明、简单 Mermaid 或表格已经清楚时，无需额外生成页面。Archify 不替代 Inbox 的状态来源，也不承担可编辑 diff 审阅应用的写回职责。

## 按需使用本地能力

开始具体制图时读取 [Skill 入口](../archify/archify/SKILL.md)，再读匹配图类型的 schema 与示例。入口、CLI 和模板均由固定版本的参考仓库提供，不向各项目复制另一套。未注册全局 Skill；工作空间 HTML 规则通过本文件路由，具备文件与 Node.js 访问能力的 Agent 可以直接使用。

从实际工作空间根目录执行，输入与输出使用当前事项自己的路径：

```text
node references/archify/archify/bin/archify.mjs doctor
node references/archify/archify/bin/archify.mjs validate <type> <input.json> --quality showcase --json
node references/archify/archify/bin/archify.mjs deliver <type> <input.json> <output.html> --quality showcase --json
```

JSON 与 HTML 保存在所属事项的 artifacts 中，保留事实来源。修改 JSON 后重新验证和生成，避免手改 HTML 形成另一份事实源。实际交付按照上游当前版本的交付约定核对浏览器行为与视觉效果，分别说明确定性检查、浏览器证据和视觉审阅；不把某一层通过当成全部通过。

每次实际使用时，在所属事项中保存这次输入、输出和验证依据；上一次验证不能代替当前环境的检查。

外部 Skill 是按需参考，工作空间目标、用户指令与实际工具授权保持优先。升级时同时复核入口、schema、命令和生成结果，再更新 gitlink；不直接修改参考仓库实现。
