from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workflow.work_plan import graph, node_states, parse_plan, render_plan, validate_completion


class WorkPlanTests(unittest.TestCase):
    def test_parse_fork_and_join_dependencies(self):
        markdown = """# 发布计划

- [x] 准备
- [ ] 前端
  依赖：准备
- [ ] 后端
  依赖：准备
- [ ] 发布
  依赖：前端、后端
"""

        plan = parse_plan(markdown)

        self.assertEqual(plan["markdown"], markdown)
        by_name = {node["name"]: node for node in plan["nodes"]}
        self.assertIs(by_name["准备"]["done"], True)
        self.assertEqual(by_name["前端"]["after"], [by_name["准备"]["id"]])
        self.assertEqual(by_name["后端"]["after"], [by_name["准备"]["id"]])
        self.assertEqual(by_name["发布"]["after"], [by_name["前端"]["id"], by_name["后端"]["id"]])

    def test_plain_markdown_and_fenced_checkboxes_have_no_nodes(self):
        markdown = """# 说明

这里没有计划。

```markdown
- [ ] 代码示例
```

~~~
- [x] 另一个示例
~~~
"""
        self.assertEqual(parse_plan(markdown), {"markdown": markdown, "nodes": []})

    def test_ids_survive_reorder_and_explicit_rename(self):
        before = parse_plan("- [ ] 产品/旧名称\n- [ ] 产品/保持\n")
        after = parse_plan(
            "- [ ] 产品/保持\n- [ ] 产品/新名称\n",
            previous=before,
            renames={"产品/旧名称": "产品/新名称"},
        )
        old = {node["name"]: node["id"] for node in before["nodes"]}
        new = {node["name"]: node["id"] for node in after["nodes"]}
        self.assertEqual(new["产品/保持"], old["产品/保持"])
        self.assertEqual(new["产品/新名称"], old["产品/旧名称"])

    def test_deleted_then_recreated_name_gets_a_new_identity(self):
        original = parse_plan("- [ ] 产品/步骤\n")
        deleted = parse_plan("# 暂无步骤\n", previous=original)
        recreated = parse_plan("- [ ] 产品/步骤\n", previous=deleted)

        self.assertNotEqual(recreated["nodes"][0]["id"], original["nodes"][0]["id"])

    def test_semantic_errors_name_the_problem(self):
        cases = [
            ("- [ ] 同名\n- [x] 同名\n", "重名"),
            ("- [ ] 发布\n  依赖：构建\n", "依赖不存在"),
            ("- [ ] A\n  依赖：B\n- [ ] B\n  依赖：A\n", "形成环"),
            ("- [ ] A\n  依赖：A\n", "形成环"),
        ]
        for markdown, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                parse_plan(markdown)

    def test_ascii_commas_and_repeated_dependency_lines_are_supported(self):
        plan = parse_plan("""- [ ] A
- [ ] B
- [ ] C
- [ ] D
  依赖：A, B
  依赖: C
""")
        ids = {node["name"]: node["id"] for node in plan["nodes"]}
        self.assertEqual(plan["nodes"][-1]["after"], [ids["A"], ids["B"], ids["C"]])

    def test_render_projects_statuses_without_touching_other_markdown(self):
        markdown = """# Plan
- [ ] A
- [x] B
  依赖：A
```markdown
- [ ] example
```
"""
        plan = parse_plan(markdown)
        ids = {node["name"]: node["id"] for node in plan["nodes"]}

        rendered = render_plan(plan, {ids["A"]: "done", ids["B"]: "active"})

        self.assertEqual(rendered, markdown.replace("- [ ] A", "- [x] A").replace("- [x] B", "- [ ] B"))

    def test_states_follow_dependencies_and_preserve_explicit_execution_states(self):
        plan = parse_plan("""- [ ] A
- [ ] B
  依赖：A
- [ ] C
  依赖：A
- [ ] D
  依赖：B、C
""")
        ids = {node["name"]: node["id"] for node in plan["nodes"]}
        states = {node["name"]: node["state"] for node in node_states(
            plan, {ids["A"]: "done", ids["B"]: "active", ids["C"]: "blocked"}
        )}
        self.assertEqual(states, {"A": "done", "B": "active", "C": "blocked", "D": "waiting"})

    def test_pending_is_accepted_and_dependency_state_is_still_derived(self):
        plan = parse_plan("- [ ] A\n- [ ] B\n  依赖：A\n")
        ids = {node["name"]: node["id"] for node in plan["nodes"]}

        states = {node["name"]: node["state"] for node in node_states(
            plan, {ids["A"]: "pending", ids["B"]: "pending"}
        )}

        self.assertEqual(states, {"A": "ready", "B": "waiting"})
        self.assertEqual(render_plan(plan, {ids["A"]: "pending"}), plan["markdown"])

    def test_completion_rejects_done_node_with_incomplete_dependency(self):
        plan = parse_plan("- [ ] A\n- [x] B\n  依赖：A\n")
        with self.assertRaisesRegex(ValueError, "已完成.*依赖.*未完成"):
            validate_completion(plan)
        ids = {node["name"]: node["id"] for node in plan["nodes"]}
        self.assertIsNone(validate_completion(plan, {ids["A"]: True}))

    def test_active_and_blocked_dependencies_remain_incomplete(self):
        plan = parse_plan("- [ ] A\n- [ ] B\n- [ ] C\n  依赖：A、B\n")
        ids = {node["name"]: node["id"] for node in plan["nodes"]}

        with self.assertRaisesRegex(ValueError, "A、B"):
            validate_completion(plan, {
                ids["A"]: "active",
                ids["B"]: "blocked",
                ids["C"]: "done",
            })

    def test_graph_uses_dependency_direction_states_and_safe_labels(self):
        plan = parse_plan('- [x] A "quoted"\n- [ ] B/发布\n  依赖：A "quoted"\n')
        chart = graph(plan)
        self.assertIn("flowchart TD", chart)
        self.assertIn('n0["A &quot;quoted&quot;"]', chart)
        self.assertIn('n1["B/发布"]', chart)
        self.assertIn("n0 --> n1", chart)
        self.assertIn("class n0 done;", chart)
        self.assertIn("class n1 ready;", chart)


if __name__ == "__main__":
    unittest.main()
