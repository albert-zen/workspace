"""Parse and project small, semantic Markdown dependency plans.

The module owns plan syntax and validation only.  It does not run work or infer
completion from anything beyond the supplied checkboxes/status projection.
"""

from __future__ import annotations

import html
import re
import uuid
from collections.abc import Mapping


_STEP_RE = re.compile(r"^(?P<indent>[ \t]*)-\s+\[(?P<mark>[ xX])\]\s+(?P<name>.+?)\s*$")
_DEPENDENCY_RE = re.compile(r"^(?P<indent>[ \t]+)依赖\s*[：:]\s*(?P<names>.*?)\s*$")
_FENCE_RE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$")


def _open_fence(line):
    match = _FENCE_RE.match(line)
    if not match:
        return None
    marker = match.group("marker")
    if marker.startswith("`") and "`" in match.group("info"):
        return None
    return marker


def _closes_fence(line, marker):
    return re.fullmatch(r" {0,3}" + re.escape(marker[0]) +
                        "{" + str(len(marker)) + r",}[ \t]*", line) is not None


def _step_id():
    """Create identity for a newly introduced step; names remain its lookup key."""
    return "step-" + uuid.uuid4().hex


def _scan(markdown):
    """Return source steps with unresolved dependency names."""
    lines = markdown.splitlines()
    found = []
    fence = None
    current = None
    for line_number, line in enumerate(lines, 1):
        if fence:
            if _closes_fence(line, fence):
                fence = None
            continue
        opened = _open_fence(line)
        if opened:
            fence = opened
            current = None
            continue

        step = _STEP_RE.match(line)
        if step:
            name = step.group("name").strip()
            if not name:
                raise ValueError("计划步骤名称不能为空（第 %d 行）。" % line_number)
            current = {
                "name": name,
                "done": step.group("mark").lower() == "x",
                "dependencies": [],
                "indent": len(step.group("indent").expandtabs(4)),
                "line": line_number,
            }
            found.append(current)
            continue

        dependency = _DEPENDENCY_RE.match(line)
        if dependency and current is not None:
            dependency_indent = len(dependency.group("indent").expandtabs(4))
            if dependency_indent > current["indent"]:
                names = [name.strip() for name in
                         re.split(r"[、,]", dependency.group("names"))]
                if not names or any(not name for name in names):
                    raise ValueError("依赖名称不能为空（第 %d 行）。" % line_number)
                current["dependencies"].extend(names)
                continue
        current = None
    return found


def _previous_nodes(previous):
    if previous is None:
        return {}
    if not isinstance(previous, Mapping) or not isinstance(previous.get("nodes"), list):
        raise ValueError("previous 必须是此前 parse_plan 返回的计划。")
    result = {}
    for node in previous["nodes"]:
        if not isinstance(node, Mapping) or not isinstance(node.get("name"), str) or not isinstance(node.get("id"), str):
            raise ValueError("previous 包含无效节点。")
        if node["name"] in result:
            raise ValueError("previous 包含重名步骤：%s。" % node["name"])
        result[node["name"]] = node["id"]
    return result


def parse_plan(markdown, previous=None, renames=None):
    """Parse checkbox steps and validate their named, acyclic dependencies."""
    if not isinstance(markdown, str):
        raise ValueError("计划必须是 Markdown 文本。")
    source = _scan(markdown)
    seen = {}
    for item in source:
        if item["name"] in seen:
            raise ValueError("计划步骤重名：%s（第 %d、%d 行）。" %
                             (item["name"], seen[item["name"]], item["line"]))
        seen[item["name"]] = item["line"]

    previous_by_name = _previous_nodes(previous)
    if renames is None:
        renames = {}
    if not isinstance(renames, Mapping) or any(not isinstance(old, str) or not isinstance(new, str)
                                               for old, new in renames.items()):
        raise ValueError("renames 必须是旧名称到新名称的映射。")
    reverse_renames = {}
    current_names = set(seen)
    for old, new in renames.items():
        if old not in previous_by_name:
            raise ValueError("重命名的原步骤不存在：%s。" % old)
        if new not in current_names:
            raise ValueError("重命名后的步骤不存在：%s。" % new)
        if new in reverse_renames:
            raise ValueError("多个旧步骤不能重命名为同一步骤：%s。" % new)
        if new in previous_by_name and new != old:
            raise ValueError("重命名目标与已有步骤冲突：%s。" % new)
        reverse_renames[new] = old

    ids = {}
    used_ids = set()
    for item in source:
        name = item["name"]
        previous_name = reverse_renames.get(name, name)
        node_id = previous_by_name.get(previous_name)
        if node_id is None:
            node_id = _step_id()
        if node_id in used_ids:
            raise ValueError("步骤身份冲突：%s。" % name)
        ids[name] = node_id
        used_ids.add(node_id)

    nodes = []
    for item in source:
        after = []
        for dependency in item["dependencies"]:
            if dependency not in ids:
                raise ValueError("步骤“%s”的依赖不存在：%s。" % (item["name"], dependency))
            dependency_id = ids[dependency]
            if dependency_id not in after:
                after.append(dependency_id)
        nodes.append({"id": ids[item["name"]], "name": item["name"],
                      "after": after, "done": item["done"]})

    _validate_acyclic(nodes)
    return {"markdown": markdown, "nodes": nodes}


def _validate_acyclic(nodes):
    by_id = {node["id"]: node for node in nodes}
    visiting = set()
    visited = set()

    def visit(node_id, path):
        if node_id in visiting:
            start = path.index(node_id)
            cycle_ids = path[start:] + [node_id]
            names = [by_id[item]["name"] for item in cycle_ids]
            raise ValueError("计划依赖形成环：%s。" % " -> ".join(names))
        if node_id in visited:
            return
        visiting.add(node_id)
        path.append(node_id)
        for dependency in by_id[node_id]["after"]:
            visit(dependency, path)
        path.pop()
        visiting.remove(node_id)
        visited.add(node_id)

    for node in nodes:
        visit(node["id"], [])


def _validated_nodes(plan):
    if not isinstance(plan, Mapping) or not isinstance(plan.get("markdown"), str) or not isinstance(plan.get("nodes"), list):
        raise ValueError("plan 必须是 parse_plan 返回的计划。")
    ids = set()
    names = set()
    nodes = []
    for node in plan["nodes"]:
        if (not isinstance(node, Mapping) or not isinstance(node.get("id"), str)
                or not isinstance(node.get("name"), str) or not isinstance(node.get("after"), list)
                or not isinstance(node.get("done"), bool)
                or any(not isinstance(item, str) for item in node.get("after", []))):
            raise ValueError("plan 包含无效节点。")
        if node["id"] in ids or node["name"] in names:
            raise ValueError("plan 包含重复的步骤身份或名称。")
        ids.add(node["id"])
        names.add(node["name"])
        nodes.append({"id": node["id"], "name": node["name"],
                      "after": list(node["after"]), "done": node["done"]})
    for node in nodes:
        missing = [item for item in node["after"] if item not in ids]
        if missing:
            raise ValueError("plan 中步骤“%s”的依赖身份不存在。" % node["name"])
    _validate_acyclic(nodes)
    return nodes


def _status_values(nodes, statuses):
    if statuses is None:
        statuses = {}
    if not isinstance(statuses, Mapping):
        raise ValueError("statuses 必须是步骤 id 到状态的映射。")
    ids = {node["id"] for node in nodes}
    unknown = [node_id for node_id in statuses if node_id not in ids]
    if unknown:
        raise ValueError("statuses 包含未知步骤 id：%s。" % unknown[0])
    allowed = {"pending", "done", "ready", "waiting", "active", "blocked"}
    for node_id, value in statuses.items():
        if not isinstance(value, bool) and value not in allowed:
            raise ValueError("步骤 %s 的状态无效：%s。" % (node_id, value))
    return statuses


def _is_done(node, statuses):
    if node["id"] not in statuses:
        return node["done"]
    value = statuses[node["id"]]
    return value is True or value == "done"


def node_states(plan, statuses=None):
    """Return plan nodes with dependency-derived execution states.

    ``active`` and ``blocked`` are explicit execution observations.  All other
    incomplete states are derived from whether every dependency is done.
    """
    nodes = _validated_nodes(plan)
    statuses = _status_values(nodes, statuses)
    done_ids = {node["id"] for node in nodes if _is_done(node, statuses)}
    result = []
    for node in nodes:
        explicit = statuses.get(node["id"])
        if explicit in {"active", "blocked"}:
            state = explicit
        elif node["id"] in done_ids:
            state = "done"
        elif all(dependency in done_ids for dependency in node["after"]):
            state = "ready"
        else:
            state = "waiting"
        projected = dict(node)
        projected["state"] = state
        result.append(projected)
    return result


def validate_completion(plan, statuses=None):
    """Reject completion claims whose prerequisites are not complete."""
    nodes = _validated_nodes(plan)
    statuses = _status_values(nodes, statuses)
    by_id = {node["id"]: node for node in nodes}
    done_ids = {node["id"] for node in nodes if _is_done(node, statuses)}
    for node in nodes:
        if node["id"] not in done_ids:
            continue
        incomplete = [by_id[item]["name"] for item in node["after"] if item not in done_ids]
        if incomplete:
            raise ValueError("已完成步骤“%s”的依赖尚未完成：%s。" %
                             (node["name"], "、".join(incomplete)))


def render_plan(plan, statuses=None):
    """Project id-keyed statuses onto checkbox marks without rewriting prose."""
    nodes = _validated_nodes(plan)
    statuses = _status_values(nodes, statuses)
    if not statuses:
        return plan["markdown"]
    by_name = {node["name"]: node for node in nodes}
    source_names = [item["name"] for item in _scan(plan["markdown"])]
    if source_names != [node["name"] for node in nodes]:
        raise ValueError("plan 的 Markdown 与节点列表不一致。")

    lines = plan["markdown"].splitlines(keepends=True)
    result = []
    fence = None
    for line in lines:
        bare = line.rstrip("\r\n")
        if fence:
            if _closes_fence(bare, fence):
                fence = None
            result.append(line)
            continue
        opened = _open_fence(bare)
        if opened:
            fence = opened
            result.append(line)
            continue
        match = _STEP_RE.match(bare)
        if not match:
            result.append(line)
            continue
        name = match.group("name").strip()
        node = by_name[name]
        mark = "x" if _is_done(node, statuses) else " "
        start, end = match.span("mark")
        result.append(line[:start] + mark + line[end:])
    return "".join(result)


def graph(plan, statuses=None):
    """Render a Mermaid dependency graph with escaped semantic labels."""
    states = node_states(plan, statuses)
    graph_ids = {node["id"]: "n%d" % index for index, node in enumerate(states)}
    lines = ["flowchart TD"]
    for node in states:
        label = html.escape(node["name"], quote=True).replace("\n", "&#10;")
        lines.append('  %s["%s"]' % (graph_ids[node["id"]], label))
    for node in states:
        for dependency in node["after"]:
            lines.append("  %s --> %s" % (graph_ids[dependency], graph_ids[node["id"]]))
    for node in states:
        lines.append("  class %s %s;" % (graph_ids[node["id"]], node["state"]))
    lines.extend([
        "  classDef done fill:#dcfce7,stroke:#15803d,color:#14532d;",
        "  classDef ready fill:#dbeafe,stroke:#2563eb,color:#1e3a8a;",
        "  classDef waiting fill:#f3f4f6,stroke:#6b7280,color:#374151;",
        "  classDef active fill:#fef3c7,stroke:#d97706,color:#78350f;",
        "  classDef blocked fill:#fee2e2,stroke:#dc2626,color:#7f1d1d;",
    ])
    return "\n".join(lines) + "\n"
