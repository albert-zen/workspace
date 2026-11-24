(() => {
  "use strict";

  const bundle = JSON.parse(document.getElementById("review-data").textContent);
  const byId = (id) => document.getElementById(id);
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const makeIcon = (name) => {
    const svgNamespace = "http:" + "//www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNamespace, "svg");
    svg.setAttribute("class", "octicon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(svgNamespace, "use");
    use.setAttribute("href", `#octicon-${name}`);
    svg.append(use);
    return svg;
  };
  const setButtonLabel = (button, iconName, text) => {
    button.replaceChildren(makeIcon(iconName), make("span", "button-label", text));
  };
  const basename = (path) => path.split("/").pop();
  const shortHash = (value) => value ? value.slice(0, 10) : "—";
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const prefersReduced = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  function canonicalText(value) {
    return (value ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  }

  function splitText(value) {
    const normalized = canonicalText(value ?? "");
    if (!normalized) return { lines: [], trailing: false };
    const trailing = normalized.endsWith("\n");
    const parts = normalized.split("\n");
    if (trailing) parts.pop();
    return { lines: parts, trailing };
  }

  function joinText(lines, trailing) {
    if (!lines.length) return trailing ? "\n" : "";
    return lines.join("\n") + (trailing ? "\n" : "");
  }

  function contentFingerprint(value) {
    const input = canonicalText(value ?? "");
    let hash = 0x811c9dc5;
    for (let index = 0; index < input.length; index += 1) {
      hash ^= input.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193);
    }
    return `fnv1a-${(hash >>> 0).toString(16).padStart(8, "0")}`;
  }

  const claimPaths = (claim) => claim?.filePaths || (claim?.filePath ? [claim.filePath] : []);
  const allClaims = (bundle.story?.claims || []).filter((claim) => claim && claim.id);
  const sources = Array.isArray(bundle.sources) ? bundle.sources : [];
  const firstFilePath = bundle.files[0]?.path || null;
  const initialText = (file) => file.proposed ? file.proposed.text : null;
  const baseText = (file) => file.baseline ? file.baseline.text : "";
  const currentText = (file) => state.edits.get(file.path);
  const isMarkdown = (file) => Boolean(file && /\.(md|mdx|markdown)$/i.test(file.path));
  const decisionOf = (group) => state.decisions.get(group.key) || "pending";
  const UNMAPPED = "__unmapped__";

  const PRIORITY_ORDER = { blocker: 0, attention: 1, evidence: 2, normal: 3, info: 4, inference: 5 };
  const STATUS_ORDER = { pending: 0, mixed: 1, accepted: 2, rejected: 3, idle: 4 };
  const STATUS_LABEL = { pending: "待决定", mixed: "部分接受", accepted: "已接受", rejected: "不接受", idle: "无改动" };
  const DECISION_LABEL = { accepted: "接受", rejected: "不接受", pending: "待定" };

  function priorityFor(claim) {
    const explicit = String(claim?.priority || "");
    if (["blocker", "attention", "evidence", "normal", "info", "inference"].includes(explicit)) return explicit;
    const epistemic = String(claim?.epistemicStatus || claim?.kind || "analysis");
    if (epistemic === "recommendation") return "attention";
    if (epistemic === "unknown") return "attention";
    if (epistemic === "fact") return "evidence";
    if (epistemic === "evidence") return "evidence";
    if (epistemic === "inference") return "inference";
    return "normal";
  }

  function priorityLabel(priority) {
    return ({ blocker: "阻塞", attention: "需关注", evidence: "事实依据", normal: "常规", info: "说明", inference: "推断" })[priority] || "常规";
  }

  function roleTone(role) {
    const text = String(role || "");
    if (/反向|反例|失败|误报/.test(text)) return "contrast";
    if (/证据有限|待核实|不足|未知/.test(text)) return "limited";
    if (/可借鉴|成功|好例|正面/.test(text)) return "good";
    if (/事实|数据/.test(text)) return "fact";
    return "reference";
  }

  const themes = allClaims.map((claim, index) => ({
    id: String(claim.id),
    letter: index < 26 ? String.fromCharCode(65 + index) : String(index + 1),
    title: String(claim.theme || claim.label || claim.text || claim.id),
    claim,
    priority: priorityFor(claim),
    index,
  }));
  const unmappedTheme = {
    id: UNMAPPED,
    letter: "·",
    title: "未映射改动",
    claim: { id: UNMAPPED, theme: "未映射改动", text: "这些改动没有对应的主题说明。不能把它们当作已解释或已核对。", epistemicStatus: "unknown" },
    priority: "attention",
    index: themes.length,
    unmapped: true,
  };

  const state = {
    screen: "overview",
    themeId: themes[0]?.id || UNMAPPED,
    filePath: firstFilePath,
    blockId: null,
    filter: "all",
    decisions: new Map(),
    edits: new Map(bundle.files.map((file) => [file.path, initialText(file)])),
    blockEdits: new Map(),
    manualMarks: new Set(),
    comments: Array.isArray(bundle.comments) ? bundle.comments.map((comment) => ({ ...comment })) : [],
    anchor: null,
    commentTarget: null,
    commentDraft: "",
    editTarget: null,
    editDraft: "",
    lastEditUndo: null,
    selection: null,
    canGoBack: false,
    expandedUnits: new Set(),
    themeOrder: null,
    previewThemeId: null,
    themeRailOpen: !window.matchMedia?.("(max-width: 900px)").matches,
    evidenceRailOpen: false,
    themeEvidence: false,
    themeDrafts: new Map(),
    commandOpen: true,
    zen: false,
    log: [],
  };

  let live = null;

  // Persist only edited files. The original proposal is already in the bundle.
  function captureReview() {
    return {
      decisions: [...state.decisions], blockEdits: [...state.blockEdits],
      edits: bundle.files.filter((file) => currentText(file) !== initialText(file))
        .map((file) => [file.path, currentText(file)]),
      manualMarks: [...state.manualMarks], comments: state.comments, lastEditUndo: state.lastEditUndo,
      themeDrafts: [...state.themeDrafts], log: state.log,
      editTarget: state.editTarget, editDraft: state.editTarget ? state.editDraft : "",
      commentTarget: state.commentTarget, commentDraft: state.commentTarget ? state.commentDraft : "",
    };
  }

  function restoreReview(saved) {
    if (!saved) return;
    for (const key of ["decisions", "blockEdits", "themeDrafts"]) state[key] = new Map(saved[key]);
    state.edits = new Map(bundle.files.map((file) => [file.path, initialText(file)]));
    for (const [path, value] of saved.edits) state.edits.set(path, value);
    state.manualMarks = new Set(saved.manualMarks);
    for (const key of ["comments", "lastEditUndo", "log", "editTarget", "editDraft", "commentTarget", "commentDraft"]) {
      state[key] = saved[key];
    }
    invalidate();
  }

  const fileFor = (path = state.filePath) => bundle.files.find((file) => file.path === path);
  const themeById = (id) => id === UNMAPPED ? unmappedTheme : themes.find((theme) => theme.id === id) || null;
  const sourceFor = (id) => sources.find((source) => String(source.id) === String(id));
  const evidenceFor = (theme) => ((theme?.claim?.evidenceIds || []).map(sourceFor).filter(Boolean));

  // ---------- text diff -----------------------------------------------------------------

  function diffOperations(beforeValue, afterValue) {
    const before = splitText(beforeValue).lines;
    const after = splitText(afterValue).lines;
    const n = before.length;
    const m = after.length;
    if (n * m > 1_500_000) return fallbackDiff(before, after);
    const matrix = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i -= 1) {
      for (let j = m - 1; j >= 0; j -= 1) {
        matrix[i][j] = before[i] === after[j]
          ? matrix[i + 1][j + 1] + 1
          : Math.max(matrix[i + 1][j], matrix[i][j + 1]);
      }
    }
    const operations = [];
    let i = 0;
    let j = 0;
    let oldLine = 1;
    let newLine = 1;
    while (i < n && j < m) {
      if (before[i] === after[j]) {
        operations.push({ kind: "context", text: before[i], oldLine, newLine });
        i += 1; j += 1; oldLine += 1; newLine += 1;
      } else if (matrix[i + 1][j] >= matrix[i][j + 1]) {
        operations.push({ kind: "remove", text: before[i], oldLine, newLine: null });
        i += 1; oldLine += 1;
      } else {
        operations.push({ kind: "add", text: after[j], oldLine: null, newLine });
        j += 1; newLine += 1;
      }
    }
    while (i < n) operations.push({ kind: "remove", text: before[i++], oldLine: oldLine++, newLine: null });
    while (j < m) operations.push({ kind: "add", text: after[j++], oldLine: null, newLine: newLine++ });
    return operations;
  }

  function fallbackDiff(before, after) {
    let prefix = 0;
    while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix += 1;
    let suffix = 0;
    while (
      suffix < before.length - prefix && suffix < after.length - prefix &&
      before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
    ) suffix += 1;
    const operations = [];
    for (let i = 0; i < prefix; i += 1) operations.push({ kind: "context", text: before[i], oldLine: i + 1, newLine: i + 1 });
    for (let i = prefix; i < before.length - suffix; i += 1) operations.push({ kind: "remove", text: before[i], oldLine: i + 1, newLine: null });
    for (let i = prefix; i < after.length - suffix; i += 1) operations.push({ kind: "add", text: after[i], oldLine: null, newLine: i + 1 });
    for (let i = 0; i < suffix; i += 1) {
      const oldIndex = before.length - suffix + i;
      const newIndex = after.length - suffix + i;
      operations.push({ kind: "context", text: before[oldIndex], oldLine: oldIndex + 1, newLine: newIndex + 1 });
    }
    return operations;
  }

  function keyGroups(groups, path, operations) {
    const seen = new Map();
    return groups.map((group) => {
      const first = group.indexes?.[0] ?? 0;
      const last = group.indexes?.[group.indexes.length - 1] ?? first;
      const contextBefore = operations
        ? operations.slice(Math.max(0, first - 2), first).map((op) => op.text).join("\u0000")
        : "";
      const contextAfter = operations
        ? operations.slice(last + 1, last + 3).map((op) => op.text).join("\u0000")
        : "";
      const base = contentFingerprint(`${contextBefore}\u0001${group.before.join("\n")}\u0000${group.after.join("\n")}\u0001${contextAfter}`);
      const occurrence = (seen.get(base) || 0) + 1;
      seen.set(base, occurrence);
      const key = `${path}::${base}${occurrence > 1 ? `#${occurrence}` : ""}`;
      return { ...group, key, filePath: path };
    });
  }

  function changeGroups(operations, path) {
    const groups = [];
    let current = null;
    operations.forEach((op, index) => {
      if (op.kind === "context") {
        if (current) { groups.push(current); current = null; }
        return;
      }
      if (!current) current = { indexes: [], before: [], after: [], oldLine: op.oldLine, newLine: op.newLine };
      current.indexes.push(index);
      if (op.kind === "remove") current.before.push(op.text);
      if (op.kind === "add") current.after.push(op.text);
      if (current.oldLine === null && op.oldLine !== null) current.oldLine = op.oldLine;
      if (current.newLine === null && op.newLine !== null) current.newLine = op.newLine;
    });
    if (current) groups.push(current);
    return keyGroups(groups, path, operations);
  }

  function groupAt(groups, index) {
    return groups.find((group) => group.indexes.includes(index)) || null;
  }

  function opWindow(operations, groups, context = 4) {
    const keep = new Set();
    const grouped = new Set();
    groups.forEach((group) => {
      group.indexes.forEach((index) => {
        grouped.add(index);
        for (let cursor = Math.max(0, index - context); cursor <= Math.min(operations.length - 1, index + context); cursor += 1) keep.add(cursor);
      });
    });
    const result = [];
    let last = -1;
    for (let index = 0; index < operations.length; index += 1) {
      if (keep.has(index)) {
        if (!grouped.has(index) && operations[index].kind !== "context") continue;
        if (last >= 0 && index > last + 1) result.push({ kind: "skip", text: `… ${index - last - 1} 行未在此块显示 …`, oldLine: null, newLine: null, skipped: index - last - 1 });
        const op = { ...operations[index], groupIndex: groupAt(groups, index)?.key || null };
        result.push(op);
        last = index;
      }
    }
    return result;
  }

  // ---------- document model -------------------------------------------------------------

  function parseMarkdown(value) {
    const source = splitText(value).lines;
    const blocks = [];
    let index = 0;
    const isSpecial = (line) => /^\s*(#{1,6}\s+|```|[-*+]\s+|\d+\.\s+|>\s?|---\s*$)/.test(line);
    while (index < source.length) {
      if (!source[index].trim()) { index += 1; continue; }
      const start = index + 1;
      const line = source[index];
      if (index === 0 && line.trim() === "---") {
        const closing = source.findIndex((item, candidate) => candidate > 0 && item.trim() === "---");
        if (closing > 0) {
          blocks.push({ type: "code", language: "yaml", value: source.slice(1, closing).join("\n"), start: 1, end: closing + 1 });
          index = closing + 1;
          continue;
        }
      }
      if (/^\s*```/.test(line)) {
        const language = line.trim().slice(3);
        const body = [];
        index += 1;
        while (index < source.length && !/^\s*```/.test(source[index])) body.push(source[index++]);
        if (index < source.length) index += 1;
        blocks.push({ type: "code", language, value: body.join("\n"), start, end: index });
        continue;
      }
      const heading = /^(#{1,6})\s+(.+)$/.exec(line);
      if (heading) {
        blocks.push({ type: "heading", level: heading[1].length, value: heading[2], start, end: start });
        index += 1;
        continue;
      }
      if (/^\s*---\s*$/.test(line)) {
        blocks.push({ type: "rule", value: "", start, end: start });
        index += 1;
        continue;
      }
      if (/^\s*>/.test(line)) {
        const body = [];
        while (index < source.length && /^\s*>/.test(source[index])) body.push(source[index++].replace(/^\s*>\s?/, ""));
        blocks.push({ type: "quote", value: body.join("\n"), start, end: index });
        continue;
      }
      const listMatch = /^\s*([-*+]|\d+\.)\s+(.+)$/.exec(line);
      if (listMatch) {
        const ordered = /\d/.test(listMatch[1]);
        const items = [];
        while (index < source.length) {
          const item = /^\s*([-*+]|\d+\.)\s+(.+)$/.exec(source[index]);
          if (!item || /\d/.test(item[1]) !== ordered) break;
          items.push(item[2]);
          index += 1;
        }
        blocks.push({ type: ordered ? "ol" : "ul", items, start, end: index });
        continue;
      }
      const paragraph = [line];
      index += 1;
      while (index < source.length && source[index].trim() && !isSpecial(source[index])) paragraph.push(source[index++]);
      blocks.push({ type: "paragraph", value: paragraph.join("\n"), start, end: index });
    }
    return blocks;
  }

  function markdownSections(text) {
    const blocks = parseMarkdown(text);
    if (!blocks.length) return [];
    const sections = [];
    let current = { id: "preamble", title: "开头", level: 0, start: blocks[0].start, end: blocks[0].start, heading: null, blocks: [] };
    blocks.forEach((block) => {
      if (block.type === "heading" && block.level <= 2) {
        if (current.blocks.length) sections.push(current);
        current = { id: `s-${block.start}`, title: block.value, level: block.level, start: block.start, end: block.end, heading: block, blocks: [block] };
        return;
      }
      if (!current.blocks.length && block.start > current.start) current.start = block.start;
      current.blocks.push(block);
      current.end = Math.max(current.end, block.end);
    });
    if (current.blocks.length) sections.push(current);
    return sections;
  }

  function sectionForLine(sections, line) {
    if (!sections.length) return null;
    const exact = sections.find((section) => line >= section.start && line <= section.end);
    if (exact) return exact;
    const previous = [...sections].reverse().find((section) => section.start <= line);
    return previous || sections[0];
  }

  function groupAnchorLine(group, operations) {
    const added = group.indexes
      .map((index) => operations[index])
      .filter((op) => op.kind === "add" && op.text.trim());
    if (added.length) return added[0].newLine;
    const removed = group.indexes
      .map((index) => operations[index])
      .filter((op) => op.kind === "remove" && op.text.trim());
    if (removed.length) return removed[0].oldLine;
    return group.newLine ?? group.oldLine ?? 1;
  }

  function splitGroupsBySections(operations, groups, sections, path) {
    if (sections.length < 2) return groups;
    const split = [];
    groups.forEach((group) => {
      let current = null;
      const flush = () => {
        if (current && current.indexes.length) split.push(current);
        current = null;
      };
      group.indexes.forEach((index, position) => {
        const op = operations[index];
        let line = op.newLine ?? op.oldLine ?? 1;
        let section = sectionForLine(sections, line);
        if (!op.text.trim()) {
          const next = group.indexes
            .slice(position + 1)
            .map((candidate) => operations[candidate])
            .find((candidate) => candidate.text.trim());
          if (next) {
            const nextSection = sectionForLine(sections, next.newLine ?? next.oldLine ?? line);
            if (nextSection && nextSection.id !== section?.id) section = nextSection;
          }
        }
        const sectionId = section?.id || "unassigned";
        if (!current || current.sectionId !== sectionId) {
          flush();
          current = { sectionId, indexes: [], before: [], after: [], oldLine: op.oldLine, newLine: op.newLine };
        }
        current.indexes.push(index);
        if (op.kind === "remove") current.before.push(op.text);
        if (op.kind === "add") current.after.push(op.text);
        if (current.oldLine === null && op.oldLine !== null) current.oldLine = op.oldLine;
        if (current.newLine === null && op.newLine !== null) current.newLine = op.newLine;
      });
      flush();
    });
    return keyGroups(split, path, operations);
  }

  let unitsCache = null;
  const fileCache = new Map();
  const invalidate = () => { unitsCache = null; fileCache.clear(); };

  const linesWithEndings = (value) => canonicalText(value).match(/[^\n]*\n|[^\n]+$/g) || [];
  const explicitKey = (file, block) => `${file.path}::${encodeURIComponent(block.id)}`;

  function blockText(file, block) {
    const key = explicitKey(file, block);
    if (state.blockEdits.has(key)) return state.blockEdits.get(key);
    return linesWithEndings(initialText(file)).slice(...block.newRange).join("");
  }

  function composeBlocks(file, include) {
    const before = linesWithEndings(baseText(file));
    const pieces = [];
    let cursor = 0;
    file.reviewBlocks.forEach((block, index) => {
      pieces.push(before.slice(cursor, block.oldRange[0]).join(""));
      pieces.push(include(index) ? blockText(file, block) : before.slice(...block.oldRange).join(""));
      cursor = block.oldRange[1];
    });
    pieces.push(before.slice(cursor).join(""));
    return pieces.filter(Boolean).map((piece, index, kept) =>
      index < kept.length - 1 && !piece.endsWith("\n") ? `${piece}\n` : piece
    ).join("");
  }

  function explicitFileState(file) {
    const before = linesWithEndings(baseText(file));
    const operations = [];
    const groups = [];
    const units = [];
    let oldCursor = 0;
    let newCursor = 0;
    const appendContext = (end) => {
      before.slice(oldCursor, end).forEach((line) => {
        operations.push({ kind: "context", text: line.replace(/\n$/, ""), oldLine: ++oldCursor, newLine: ++newCursor });
      });
    };
    file.reviewBlocks.forEach((block) => {
      appendContext(block.oldRange[0]);
      const oldText = before.slice(...block.oldRange).join("");
      const newText = blockText(file, block);
      const local = diffOperations(oldText, newText);
      const indexes = [];
      const group = {
        key: explicitKey(file, block),
        filePath: file.path, indexes, before: [], after: [],
        oldLine: block.oldRange[0] + 1, newLine: newCursor + 1,
      };
      local.forEach((op) => {
        const adjusted = { ...op, oldLine: op.oldLine === null ? null : op.oldLine + oldCursor,
          newLine: op.newLine === null ? null : op.newLine + newCursor };
        if (op.kind !== "context") indexes.push(operations.length);
        if (op.kind === "remove") group.before.push(op.text);
        if (op.kind === "add") group.after.push(op.text);
        operations.push(adjusted);
      });
      if (!indexes.length && oldText !== newText) {
        indexes.push(operations.length);
        operations.push({ kind: "skip", text: newText.endsWith("\n") ? "增加末尾换行" : "移除末尾换行", oldLine: null, newLine: null });
      }
      groups.push(group);
      units.push({ id: `explicit-${explicitKey(file, block)}`, file, title: block.title,
        start: newCursor + 1, end: newCursor + Math.max(1, splitText(newText).lines.length),
        level: 0, groups: [group], rawText: newText, changed: true, kind: "explicit", definition: block });
      oldCursor = block.oldRange[1];
      newCursor += splitText(newText).lines.length;
    });
    appendContext(before.length);
    units.forEach((unit) => { unit.ops = opWindow(operations, unit.groups); });
    return { operations, groups, units };
  }

  function fileState(file) {
    const current = currentText(file);
    const base = baseText(file);
    const key = `${file.path}::${contentFingerprint(current)}::${contentFingerprint(base)}`;
    if (fileCache.has(key)) return fileCache.get(key);
    if (file.reviewBlocks) {
      const value = explicitFileState(file);
      fileCache.set(key, value);
      return value;
    }
    const operations = diffOperations(base, current);
    let groups = changeGroups(operations, file.path);
    if (isMarkdown(file)) groups = splitGroupsBySections(operations, groups, markdownSections(current || ""), file.path);
    const value = { operations, groups };
    fileCache.set(key, value);
    return value;
  }

  function buildMarkdownUnits(file, fs) {
    const text = currentText(file) || "";
    const sections = markdownSections(text);
    const hasSections = sections.some((section) => section.level > 0);
    if (!hasSections) return buildSourceUnits(file, fs);
    const textLines = splitText(text).lines;
    const baseSections = markdownSections(baseText(file));
    const currentTitles = new Set(sections.filter((section) => section.level > 0).map((section) => section.title));
    const deletedSections = new Map();
    const deletedGroups = new Set();
    const sectionForGroup = (group) => {
      const line = groupAnchorLine(group, fs.operations);
      if (group.newLine !== null) return sectionForLine(sections, line);
      const baseSection = sectionForLine(baseSections, line);
      if (baseSection?.level > 0) {
        const same = sections.find((section) => section.level > 0 && section.title === baseSection.title);
        if (same) return same;
      }
      return sectionForLine(sections, line);
    };
    fs.groups.forEach((group) => {
      if (group.newLine !== null) return;
      const line = groupAnchorLine(group, fs.operations);
      const section = baseSections.find((item) => item.level > 0 && line >= item.start && line <= item.end && !currentTitles.has(item.title));
      if (!section) return;
      if (!deletedSections.has(section.id)) deletedSections.set(section.id, { section, groups: [] });
      deletedSections.get(section.id).groups.push(group);
      deletedGroups.add(group.key);
    });
    const assigned = new Map();
    fs.groups.forEach((group) => {
      if (deletedGroups.has(group.key)) return;
      const section = sectionForGroup(group);
      if (!section) return;
      if (!assigned.has(section.id)) assigned.set(section.id, []);
      assigned.get(section.id).push(group);
    });
    const units = [];
    sections.forEach((section) => {
      const groups = assigned.get(section.id) || [];
      const raw = textLines.slice(section.start - 1, section.end).join("\n");
      if (!groups.length) {
        units.push({
          id: `u-${file.path}-${section.start}`,
          file,
          title: section.title,
          level: section.level,
          start: section.start,
          end: section.end,
          groups: [],
          ops: [],
          rawText: raw,
          changed: false,
          kind: "markdown",
        });
        return;
      }
      const tooLarge = groups.length > 1 && section.end - section.start > 120;
      if (!tooLarge) {
        units.push({
          id: `u-${file.path}-${section.start}`,
          file,
          title: section.title,
          level: section.level,
          start: section.start,
          end: section.end,
          groups,
          ops: opWindow(fs.operations, groups),
          rawText: raw,
          changed: true,
          kind: "markdown",
        });
        return;
      }
      groups.forEach((group, index) => {
        const line = group.newLine ?? group.oldLine ?? section.start;
        units.push({
          id: `g-${file.path}-${line}-${index}`,
          file,
          title: `${section.title} · 改动 ${index + 1}`,
          level: section.level,
          start: Math.max(section.start, line - 4),
          end: Math.min(section.end, line + Math.max(group.before.length, group.after.length) + 4),
          groups: [group],
          ops: opWindow(fs.operations, [group]),
          rawText: group.after.join("\n"),
          changed: true,
          kind: "markdown-fragment",
        });
      });
    });
    deletedSections.forEach(({ section, groups }) => {
      units.push({
        id: `d-${file.path}-${section.start}`,
        file,
        title: `${section.title}（已删除）`,
        level: section.level,
        start: section.start,
        end: section.end,
        groups,
        ops: opWindow(fs.operations, groups),
        rawText: "",
        changed: true,
        kind: "markdown",
      });
    });
    return units;
  }

  function buildSourceUnits(file, fs) {
    return fs.groups.map((group, index) => {
      const line = group.newLine ?? group.oldLine ?? 1;
      const sample = (group.after[0] || group.before[0] || "").replace(/^#+\s*/, "").trim();
      return {
        id: `g-${file.path}-${line}-${index}`,
        file,
        title: sample ? sample.slice(0, 42) + (sample.length > 42 ? "…" : "") : `改动 ${index + 1}`,
        level: 0,
        start: group.oldLine ?? line,
        end: line,
        groups: [group],
        ops: opWindow(fs.operations, [group]),
        rawText: group.after.join("\n"),
        changed: true,
        kind: "source",
      };
    });
  }

  function buildUnits(file, fs) {
    if (fs.units) return fs.units;
    return isMarkdown(file) ? buildMarkdownUnits(file, fs) : buildSourceUnits(file, fs);
  }

  function applyThemes(units) {
    const byFile = new Map();
    units.forEach((unit) => {
      const path = unit.file.path;
      if (!byFile.has(path)) byFile.set(path, []);
      byFile.get(path).push(unit);
    });
    byFile.forEach((fileUnits, path) => {
      if (fileFor(path).reviewBlocks) {
        fileUnits.forEach((unit) => { unit.themeId = unit.definition.claimId; });
        return;
      }
      const fileClaims = allClaims.filter((claim) => claimPaths(claim).includes(path));
      if (!fileClaims.length) {
        fileUnits.forEach((unit) => { unit.themeId = UNMAPPED; });
        return;
      }
      if (fileClaims.length === 1) {
        fileUnits.forEach((unit) => { unit.themeId = String(fileClaims[0].id); });
        return;
      }
      const fileText = `${baseText(fileFor(path))}\n${currentText(fileFor(path)) || ""}`;
      const hunks = fileFor(path)?.diff?.hunks || [];
      const anchored = new Map();
      const plain = [];
      fileClaims.forEach((claim) => {
        const anchor = typeof claim.anchorText === "string" ? claim.anchorText.trim() : "";
        const range = claim.lineRange && typeof claim.lineRange === "object" ? claim.lineRange : null;
        const anchorHere = Boolean(anchor) && fileText.includes(anchor);
        const rangeHere = Boolean(range) && Number.isFinite(range.start) && Number.isFinite(range.end);
        if (anchorHere || rangeHere) {
          anchored.set(String(claim.id), { claim, anchor, range });
        } else if (rangeHere || (anchor && claimPaths(claim).length === 1)) {
          // A one-file claim with an anchor that no longer matches is a broken anchor,
          // not permission to cover every block in the file.
          anchored.set(String(claim.id), { claim, anchor, range });
        } else {
          plain.push(claim);
        }
      });
      let plainCursor = 0;
      fileUnits.forEach((unit) => {
        const hunkClaims = new Set();
        unit.groups.forEach((group) => {
          const line = group.newLine ?? group.oldLine;
          if (line == null) return;
          hunks.forEach((hunk) => {
            const start = group.newLine != null ? hunk.newStart : hunk.oldStart;
            const count = group.newLine != null ? hunk.newCount : hunk.oldCount;
            const end = start + Math.max(0, count - 1);
            if (line >= start && line <= end) {
              (hunk.claimIds || []).forEach((id) => hunkClaims.add(String(id)));
            }
          });
        });
        const onlyHunkClaim = hunkClaims.size === 1 ? [...hunkClaims][0] : null;
        if (onlyHunkClaim && allClaims.some((claim) => String(claim.id) === onlyHunkClaim)
          && currentText(fileFor(path)) === initialText(fileFor(path))) {
          unit.themeId = onlyHunkClaim;
          return;
        }
        const probe = `${unit.title}\n${unit.ops.map((op) => op.text).join("\n")}`;
        const hit = [...anchored.values()].find(({ anchor, range }) => {
          if (anchor && probe.includes(anchor)) return true;
          if (range) {
            return unit.groups.some((group) => {
              const spans = [];
              if (Number.isFinite(group.oldLine)) {
                spans.push([group.oldLine, group.oldLine + Math.max(0, group.before.length - 1)]);
              }
              if (Number.isFinite(group.newLine)) {
                spans.push([group.newLine, group.newLine + Math.max(0, group.after.length - 1)]);
              }
              return spans.some(([start, end]) => !(end < range.start || start > range.end));
            });
          }
          return false;
        });
        if (hit) { unit.themeId = String(hit.claim.id); return; }
        if (plain.length) {
          const claim = plain[Math.min(plainCursor, plain.length - 1)];
          if (plainCursor < plain.length - 1) plainCursor += 1;
          unit.themeId = String(claim.id);
          return;
        }
        unit.themeId = anchored.size ? UNMAPPED : String(fileClaims[0].id);
      });
    });
    return units;
  }

  function allUnits() {
    if (unitsCache) return unitsCache;
    const list = [];
    bundle.files.forEach((file) => {
      const fs = fileState(file);
      buildUnits(file, fs).forEach((unit) => {
        unit.fingerprint = contentFingerprint(unit.rawText || unit.ops.map((op) => op.text).join("\n"));
        list.push(unit);
      });
    });
    unitsCache = applyThemes(list);
    return unitsCache;
  }

  function unitsForTheme(themeId) {
    return allUnits().filter((unit) => unit.changed && unit.themeId === themeId);
  }

  function unitStatus(unit) {
    if (!unit.groups.length) return "idle";
    const decisions = unit.groups.map(decisionOf);
    if (decisions.every((value) => value === "accepted")) return "accepted";
    if (decisions.every((value) => value === "rejected")) return "rejected";
    if (decisions.some((value) => value === "pending")) return "pending";
    return "mixed";
  }

  function unitCounts(unit) {
    const counts = { accepted: 0, rejected: 0, pending: 0 };
    unit.groups.forEach((group) => { counts[decisionOf(group)] += 1; });
    return counts;
  }

  function themeCounts(themeId) {
    const units = unitsForTheme(themeId);
    const counts = { total: units.length, accepted: 0, rejected: 0, pending: 0 };
    units.forEach((unit) => {
      unit.groups.forEach((group) => { counts[decisionOf(group)] += 1; });
    });
    return counts;
  }

  function themeStatus(themeId) {
    const counts = themeCounts(themeId);
    if (!counts.total) return "idle";
    if (counts.pending) return counts.accepted || counts.rejected ? "mixed" : "pending";
    if (!counts.rejected) return "accepted";
    if (!counts.accepted) return "rejected";
    return "mixed";
  }

  function themeFiles(themeId) {
    const paths = [...new Set(unitsForTheme(themeId).map((unit) => unit.file.path))];
    const theme = themeById(themeId);
    const claimed = theme?.claim ? claimPaths(theme.claim) : [];
    return bundle.files.filter((file) => paths.includes(file.path)
      || (trailingOnlyChange(file) && (claimed.includes(file.path) || themeId === UNMAPPED)));
  }

  function themeUnitsForFile(themeId, path) {
    return unitsForTheme(themeId).filter((unit) => unit.file.path === path);
  }

  function globalStats() {
    const allThemes = [...themes, ...(unitsForTheme(UNMAPPED).length ? [unmappedTheme] : [])];
    const stats = { total: 0, accepted: 0, rejected: 0, pending: 0, files: 0, themes: allThemes.length };
    const touched = new Set();
    allUnits().filter((unit) => unit.changed).forEach((unit) => {
      touched.add(unit.file.path);
      unit.groups.forEach((group) => {
        stats.total += 1;
        stats[decisionOf(group)] += 1;
      });
    });
    stats.files = touched.size;
    stats.openComments = state.comments.filter((comment) => !comment.resolved).length;
    stats.themesPending = allThemes.filter((theme) => ["pending", "mixed"].includes(themeStatus(theme.id))).length;
    return stats;
  }

  function themeForComment(comment) {
    if (comment.themeId) return comment.themeId;
    const unit = allUnits().find((item) => item.id === comment.unitId);
    return unit?.themeId || themes[0]?.id || UNMAPPED;
  }

  // ---------- navigation -----------------------------------------------------------------

  function writeHistory(screen, themeId, mode = "push") {
    const hash = screen === "review" && themeId ? `#theme=${encodeURIComponent(themeId)}` : "#themes";
    const target = `${location.href.replace(/#.*$/, "")}${hash}`;
    try {
      history[mode === "replace" ? "replaceState" : "pushState"]({ screen, themeId }, "", target);
    } catch (_) {
      // Some file viewers restrict history writes; visible controls still provide navigation.
    }
  }

  function showOverview({ focus = true, historyMode = "none" } = {}) {
    state.screen = "overview";
    state.zen = false;
    state.editTarget = null;
    state.commentTarget = null;
    if (historyMode !== "none") writeHistory("overview", null, historyMode);
    renderAll();
    if (focus) byId("overview-title")?.focus();
  }

  function enterTheme(themeId, { focus = true, historyMode = "push" } = {}) {
    const theme = themeById(themeId) || themes[0] || unmappedTheme;
    state.themeId = theme.id;
    const files = themeFiles(theme.id);
    if (!files.some((file) => file.path === state.filePath)) state.filePath = files[0]?.path || firstFilePath;
    const units = themeUnitsForFile(theme.id, state.filePath);
    state.blockId = units.find((unit) => unit.changed)?.id || null;
    state.screen = "review";
    state.zen = false;
    state.editTarget = null;
    state.commentTarget = null;
    state.anchor = null;
    if (historyMode === "push") state.canGoBack = true;
    if (window.matchMedia?.("(max-width: 900px)").matches) state.themeRailOpen = false;
    if (historyMode !== "none") writeHistory("review", theme.id, historyMode);
    renderAll();
    if (focus) byId("theme-title")?.focus();
  }

  function selectTheme(themeId) {
    const historyMode = state.screen === "overview" ? "push" : "replace";
    if (state.themeId !== themeId) {
      state.themeEvidence = false;
      state.filePath = null;
      if (window.matchMedia?.("(max-width: 1200px)").matches) state.evidenceRailOpen = false;
    }
    enterTheme(themeId, { historyMode });
    byId("review-surface").scrollTop = 0;
  }

  function selectFile(path) {
    state.filePath = path;
    state.themeEvidence = false;
    state.blockId = themeUnitsForFile(state.themeId, path).find((unit) => unit.changed)?.id || null;
    state.editTarget = null;
    state.commentTarget = null;
    state.anchor = null;
    renderAll();
  }

  function selectBlock(unitId) {
    state.themeEvidence = false;
    state.blockId = unitId;
    document.querySelectorAll(".block-card.is-selected").forEach((node) => node.classList.remove("is-selected"));
    const card = document.querySelector(`[data-unit-id="${CSS.escape(unitId)}"]`);
    card?.classList.add("is-selected");
    document.querySelectorAll(".rail-block").forEach((button) => {
      button.setAttribute("aria-current", String(button.dataset.unit === unitId));
    });
    renderEvidence();
  }

  function nextBlock(delta) {
    const units = unitsForTheme(state.themeId).filter((unit) => unit.changed);
    if (!units.length) return;
    const index = units.findIndex((unit) => unit.id === state.blockId);
    const next = units[clamp(index + delta, 0, units.length - 1)];
    if (!next) return;
    if (next.file.path !== state.filePath) {
      state.filePath = next.file.path;
      renderAll();
    }
    selectBlock(next.id);
    document.querySelector(`[data-unit-id="${CSS.escape(next.id)}"]`)?.scrollIntoView({ block: "center", behavior: prefersReduced() ? "auto" : "smooth" });
  }

  function nextTheme(delta) {
    const ordered = sortedThemes();
    if (!ordered.length) return;
    const index = ordered.findIndex((theme) => theme.id === state.themeId);
    const next = ordered[clamp(index + delta, 0, ordered.length - 1)];
    if (next) selectTheme(next.id);
  }

  // ---------- rendering: top bar and overview --------------------------------------------

  function renderTopbar() {
    const inReview = state.screen === "review";
    const theme = themeById(state.themeId);
    byId("command-title").textContent = inReview ? `${theme?.letter || "·"} · ${theme?.title || "主题"}` : "主题总览";
    const brand = byId("brand-mark");
    brand.replaceChildren(inReview ? makeIcon("arrow-left") : document.createTextNode("T"));
    byId("show-themes").setAttribute("aria-label", inReview ? "返回主题总览" : "主题总览");
    byId("toggle-theme-rail").setAttribute("aria-pressed", String(state.themeRailOpen));
    byId("toggle-evidence").setAttribute("aria-pressed", String(state.evidenceRailOpen));
    renderTriage();
  }

  function filterLabel(filter) {
    return ({ all: "全部", pending: "待决定", accepted: "已接受", rejected: "不接受", commented: "有批注" })[filter] || filter;
  }

  function renderTriage() {
    const strip = byId("triage-strip");
    strip.replaceChildren();
    const stats = globalStats();
    const entries = [
      ["pending", "待决定", stats.pending],
      ["accepted", "已接受", stats.accepted],
      ["rejected", "不接受", stats.rejected],
      ["commented", "批注", stats.openComments],
    ];
    entries.forEach(([filter, label, count]) => {
      const button = make("button", `triage-chip tone-${filter}`);
      button.type = "button";
      button.dataset.action = "filter";
      button.dataset.filter = filter;
      button.setAttribute("aria-pressed", String(state.filter === filter));
      button.append(make("span", "triage-label", label), make("strong", "", String(count)));
      strip.append(button);
    });
    const hint = make("span", "triage-hint");
    hint.append(make("strong", "", `${stats.themesPending}/${stats.themes}`), document.createTextNode(" 个主题仍待你决定"));
    strip.append(hint);
  }

  function themeMatchesFilter(theme) {
    if (state.filter === "all") return true;
    const status = themeStatus(theme.id);
    if (state.filter === "pending") return status === "pending" || status === "mixed";
    if (state.filter === "accepted") return status === "accepted";
    if (state.filter === "rejected") return status === "rejected";
    if (state.filter === "commented") {
      return state.comments.some((comment) => !comment.resolved && themeForComment(comment) === theme.id);
    }
    return true;
  }

  function sortedThemes() {
    const all = [...themes, ...(unitsForTheme(UNMAPPED).length ? [unmappedTheme] : [])];
    if (!state.themeOrder) {
      state.themeOrder = [...all].sort((left, right) => {
        const statusDelta = (STATUS_ORDER[themeStatus(left.id)] ?? 9) - (STATUS_ORDER[themeStatus(right.id)] ?? 9);
        if (statusDelta) return statusDelta;
        const priorityDelta = (PRIORITY_ORDER[left.priority] ?? 9) - (PRIORITY_ORDER[right.priority] ?? 9);
        if (priorityDelta) return priorityDelta;
        return left.index - right.index;
      }).map((theme) => theme.id);
    }
    const lookup = new Map(all.map((theme) => [theme.id, theme]));
    const ordered = state.themeOrder.map((id) => lookup.get(id)).filter(Boolean);
    all.forEach((theme) => { if (!ordered.includes(theme)) ordered.push(theme); });
    return ordered;
  }

  function evidenceChips(theme) {
    const list = evidenceFor(theme);
    if (!list.length) return null;
    const counts = new Map();
    list.forEach((source) => {
      const tone = roleTone(source.role || source.kind);
      counts.set(tone, (counts.get(tone) || 0) + 1);
    });
    const labels = { good: "可借鉴", contrast: "反向对照", limited: "证据有限", fact: "事实", reference: "参考" };
    const wrap = make("span", "evidence-chips");
    counts.forEach((count, tone) => wrap.append(make("span", `mini-chip tone-${tone}`, `${labels[tone]} ${count}`)));
    return wrap;
  }

  function renderOverview() {
    const comparison = bundle.comparison || {};
    const stamps = [];
    if (comparison.base) stamps.push(`base ${shortHash(comparison.base)}`);
    if (comparison.head) stamps.push(`head ${shortHash(comparison.head)}`);
    if (comparison.workspaceRevision) stamps.push(`workspace ${shortHash(comparison.workspaceRevision)}`);
    byId("overview-bundle").textContent = `${bundle.title}${stamps.length ? ` · ${stamps.join(" · ")}` : ""}`;
    byId("overview-summary").textContent = bundle.summary || bundle.question || "";
    const stats = globalStats();
    const strip = byId("overview-summary-strip");
    strip.replaceChildren();
    [
      ["待决定", stats.pending, "tone-pending"],
      ["已接受", stats.accepted, "tone-accepted"],
      ["不接受", stats.rejected, "tone-rejected"],
      ["涉及文件", stats.files, "tone-neutral"],
    ].forEach(([label, value, tone]) => {
      const cell = make("span", `summary-cell ${tone}`);
      cell.append(make("strong", "", String(value)), make("span", "", label));
      strip.append(cell);
    });

    const grid = byId("theme-grid");
    grid.replaceChildren();
    const visible = sortedThemes().filter(themeMatchesFilter);
    visible.forEach((theme) => {
      const counts = themeCounts(theme.id);
      const status = themeStatus(theme.id);
      const card = make("button", `theme-card status-${status} prio-${theme.priority}`);
      card.type = "button";
      card.dataset.action = "select-theme";
      card.dataset.theme = theme.id;
      card.setAttribute("aria-label", `${theme.title}，${STATUS_LABEL[status]}，${counts.pending + counts.accepted + counts.rejected} 处改动`);
      card.addEventListener("pointerenter", () => { state.previewThemeId = theme.id; renderOverviewPreview(); });
      card.addEventListener("focus", () => { state.previewThemeId = theme.id; renderOverviewPreview(); });
      const stripe = make("span", "theme-stripe");
      const letter = make("span", "theme-letter", theme.letter);
      const copy = make("span", "theme-card-copy");
      const top = make("span", "theme-card-top");
      const badges = make("span", "theme-badges");
      badges.append(make("span", `priority-chip tone-${theme.priority}`, priorityLabel(theme.priority)));
      badges.append(make("span", `status-pill status-${status}`, STATUS_LABEL[status]));
      top.append(make("strong", "", theme.title), badges);
      copy.append(top);
      copy.append(make("span", "theme-card-claim", theme.claim?.text || ""));
      const meta = make("span", "theme-card-meta");
      meta.append(make("span", "", `${counts.pending + counts.accepted + counts.rejected} 处改动 · ${themeFiles(theme.id).length} 文件`));
      const evidence = evidenceFor(theme);
      if (evidence.length) meta.append(make("span", "", `${evidence.length} 条案例`));
      else meta.append(make("span", "muted", "无案例来源"));
      copy.append(meta);
      const bar = make("span", "progress-track");
      const total = Math.max(1, counts.accepted + counts.rejected + counts.pending);
      [["accepted", counts.accepted], ["rejected", counts.rejected], ["pending", counts.pending]].forEach(([tone, value]) => {
        if (!value) return;
        const segment = make("span", `progress-segment ${tone}`);
        segment.style.width = `${(value / total) * 100}%`;
        bar.append(segment);
      });
      copy.append(bar);
      const firstUnit = unitsForTheme(theme.id)[0];
      const sampleLine = firstUnit?.ops.find((op) => op.text.trim() && op.kind === "add")
        || firstUnit?.ops.find((op) => op.text.trim() && op.kind === "remove");
      if (sampleLine) {
        const sample = make("span", `theme-card-sample ${sampleLine.kind}`);
        sample.append(make("span", "sample-sign", sampleLine.kind === "add" ? "+" : "−"), document.createTextNode(sampleLine.text.trim()));
        copy.append(sample);
      }
      card.append(stripe, letter, copy);
      grid.append(card);
    });
    byId("overview-empty").hidden = Boolean(visible.length);
    renderOverviewPreview();
  }

  function renderOverviewPreview() {
    const panel = byId("overview-preview");
    panel.replaceChildren();
    const theme = themeById(state.previewThemeId);
    if (!theme) {
      panel.append(make("p", "eyebrow", "Reading order"));
      panel.append(make("h2", "", "先决定，再细读"));
      panel.append(make("p", "context-copy", "主题按注意力和状态排序：仍待决定的排在最前，其次是部分接受、已接受与不接受。颜色不是装饰，表示这块内容现在需要你做什么。"));
      const legend = make("div", "legend-list");
      [
        ["pending", "待决定", "仍有块没有选择接受或不接受"],
        ["accepted", "已接受", "会进入最终文件"],
        ["rejected", "不接受", "保留原内容"],
        ["mixed", "部分接受", "同一主题里有不同决定"],
      ].forEach(([tone, label, note]) => {
        const row = make("div", `legend-row tone-${tone}`);
        row.append(make("span", "legend-dot"), make("strong", "", label), make("span", "", note));
        legend.append(row);
      });
      panel.append(legend);
      panel.append(make("p", "context-copy", "悬停或键盘聚焦任一主题，这里会显示它的改动样例与案例来源。"));
      return;
    }
    const counts = themeCounts(theme.id);
    const status = themeStatus(theme.id);
    panel.append(make("p", "eyebrow", `主题 ${theme.letter} · ${priorityLabel(theme.priority)}`));
    panel.append(make("h2", "", theme.title));
    panel.append(make("p", "preview-claim", theme.claim?.text || ""));
    const meta = make("p", "preview-meta", `${counts.accepted + counts.rejected + counts.pending} 块改动 · ${themeFiles(theme.id).length} 个文件 · ${STATUS_LABEL[status]}`);
    panel.append(meta);
    const evidence = evidenceFor(theme);
    if (evidence.length) {
      panel.append(make("h3", "", "案例参考"));
      const list = make("div", "preview-evidence");
      evidence.slice(0, 3).forEach((source) => {
        const card = make("div", "mini-source");
        card.append(make("span", `role-chip tone-${roleTone(source.role || source.kind)}`, source.role || source.kind || "参考"));
        card.append(make("strong", "", source.label || source.id || "来源"));
        if (source.excerpt) card.append(make("p", "", source.excerpt.slice(0, 140)));
        list.append(card);
      });
      panel.append(list);
    }
    const first = unitsForTheme(theme.id)[0];
    if (first) {
      panel.append(make("h3", "", "改动样例"));
      const sample = make("pre", "preview-code");
      sample.textContent = first.ops.filter((op) => op.kind !== "skip").slice(0, 8).map((op) => `${op.kind === "add" ? "+" : op.kind === "remove" ? "−" : " "} ${op.text}`).join("\n");
      panel.append(sample);
    }
    const enter = make("button", "primary-button preview-enter", "进入这个主题");
    enter.type = "button";
    enter.dataset.action = "select-theme";
    enter.dataset.theme = theme.id;
    panel.append(enter);
  }

  // ---------- rendering: review shell ----------------------------------------------------

  function renderThemeRail() {
    const filter = byId("theme-filter");
    filter.replaceChildren();
    const counts = {
      all: themes.length + (unitsForTheme(UNMAPPED).length ? 1 : 0),
      pending: themes.filter((theme) => ["pending", "mixed"].includes(themeStatus(theme.id))).length,
      accepted: themes.filter((theme) => themeStatus(theme.id) === "accepted").length,
      rejected: themes.filter((theme) => themeStatus(theme.id) === "rejected").length,
      commented: themes.filter((theme) => state.comments.some((comment) => !comment.resolved && themeForComment(comment) === theme.id)).length,
    };
    ["all", "pending", "accepted", "rejected", "commented"].forEach((key) => {
      const button = make("button", `filter-pill tone-${key}`);
      button.type = "button";
      button.dataset.action = "filter";
      button.dataset.filter = key;
      button.setAttribute("aria-pressed", String(state.filter === key));
      button.append(document.createTextNode(filterLabel(key)), make("span", "", String(counts[key])));
      filter.append(button);
    });

    const list = byId("theme-list");
    list.replaceChildren();
    const visible = sortedThemes().filter(themeMatchesFilter);
    visible.forEach((theme) => {
      const status = themeStatus(theme.id);
      const themeCount = themeCounts(theme.id);
      const button = make("button", `theme-row status-${status} prio-${theme.priority}`);
      button.type = "button";
      button.dataset.action = "select-theme";
      button.dataset.theme = theme.id;
      button.setAttribute("aria-current", String(state.themeId === theme.id));
      const head = make("span", "theme-row-head");
      head.append(make("span", "theme-row-letter", theme.letter), make("strong", "", theme.title), make("span", `status-dot status-${status}`));
      button.append(head);
      if (themeCount.pending) head.append(make("span", "theme-pending", `${themeCount.pending} 待定`));
      list.append(button);
    });
    if (!visible.length) list.append(make("p", "empty-note", "当前筛选下没有主题。"));

    const blocks = byId("rail-blocks");
    blocks.replaceChildren();
    const theme = themeById(state.themeId);
    if (!theme) return;
    blocks.append(make("h3", "", theme.title));
    if (theme.claim?.text) blocks.append(make("p", "rail-theme-claim", theme.claim.text));
    unitsForTheme(theme.id).filter((unit) => unit.changed).forEach((unit) => {
      const button = make("button", "rail-block");
      button.type = "button";
      button.dataset.action = "jump-block";
      button.dataset.unit = unit.id;
      button.setAttribute("aria-current", String(state.blockId === unit.id));
      button.append(make("strong", "", unit.title));
      if (state.manualMarks.has(`${unit.file.path}::${unit.id}`)) button.append(make("span", "rail-block-edited", "已手改 · 以下是原提案"));
      if (unit.definition?.reason) button.append(make("span", "rail-block-reason", unit.definition.reason));
      const evidence = unit.definition ? unit.definition.evidenceIds.map(sourceFor).filter(Boolean) : evidenceFor(theme);
      if (evidence.length) button.append(make("span", "rail-block-sources", evidence.map((source) => source.label || source.id).join(" · ")));
      blocks.append(button);
    });
  }

  function renderThemeHeader() {
    const theme = themeById(state.themeId);
    const summary = byId("file-theme-title");
    const actions = byId("theme-actions");
    summary.replaceChildren();
    actions.replaceChildren();
    if (!theme) return;
    const counts = themeCounts(theme.id);
    const title = make("h1", "", theme.title);
    title.id = "theme-title";
    title.tabIndex = -1;
    summary.append(title);
    const comment = make("button", "tiny-button", "主题批注");
    comment.type = "button";
    comment.dataset.action = "theme-comment";
    comment.dataset.focusKey = "theme:comment";
    actions.append(comment);
    const decisions = byId("theme-decisions");
    decisions.hidden = !counts.total;
    const choices = byId("theme-decision-options");
    choices.replaceChildren();
    [["accepted", "接受全部"], ["rejected", "不接受全部"], ["pending", "恢复待定"]].forEach(([decision, label]) => {
      const button = make("button", `decision-button ${decision}`, label);
      button.type = "button";
      button.dataset.action = "theme-decision";
      button.dataset.decision = decision;
      choices.append(button);
    });
  }

  function renderFileTabs() {
    const tabs = byId("file-tabs");
    tabs.replaceChildren();
    const files = themeFiles(state.themeId);
    tabs.hidden = files.length === 0;
    files.forEach((file) => {
      const units = themeUnitsForFile(state.themeId, file.path).filter((unit) => unit.changed);
      const counts = { accepted: 0, rejected: 0, pending: 0 };
      units.forEach((unit) => unit.groups.forEach((group) => { counts[decisionOf(group)] += 1; }));
      const button = make("button", "file-tab");
      button.type = "button";
      button.dataset.action = "select-file";
      button.dataset.file = file.path;
      button.setAttribute("aria-pressed", String(file.path === state.filePath));
      button.title = file.path;
      button.append(make("span", "file-tab-name", basename(file.path)));
      if (counts.pending) button.append(make("span", "file-tab-dot tone-pending", `${counts.pending} 待定`));
      tabs.append(button);
    });
  }

  // ---------- rendering: blocks ----------------------------------------------------------

  function renderBlocks() {
    const list = byId("block-list");
    list.replaceChildren();
    const file = fileFor();
    if (file && trailingOnlyChange(file)) {
      const warning = make("div", "empty-card warning-card");
      warning.append(make("h2", "", "还有末尾换行变化"));
      warning.append(make("p", "", "这个文件的正文相同，只有末尾换行不同。逐块模型不把末尾换行当作可审块，也不会写入这个变化；若要保留它，请使用整份文件编辑或保留原文件。"));
      list.append(warning);
    }
    const units = themeUnitsForFile(state.themeId, state.filePath);
    if (!units.length) {
      if (file && trailingOnlyChange(file)) return;
      const empty = make("div", "empty-card");
      empty.append(make("h2", "", themeById(state.themeId)?.unmapped ? "没有未映射改动" : "这个文件没有属于当前主题的改动"));
      empty.append(make("p", "", "换一个主题或文件继续；主题说明与案例仍保留在右侧。"));
      list.append(empty);
      return;
    }
    units.forEach((unit) => {
      list.append(unit.changed ? renderBlock(unit) : renderUnchanged(unit));
    });
  }

  function trailingOnlyChange(file) {
    if (file.reviewBlocks) return false;
    const base = splitText(baseText(file));
    const current = splitText(currentText(file));
    return base.lines.join("\n") === current.lines.join("\n") && base.trailing !== current.trailing;
  }

  function renderUnchanged(unit) {
    const expanded = state.expandedUnits.has(unit.id);
    if (!expanded) {
      const button = make("button", "collapsed-block");
      button.type = "button";
      button.dataset.action = "toggle-unit";
      button.dataset.unit = unit.id;
      button.append(make("span", "collapsed-mark", "§"), make("span", "collapsed-title", unit.title), make("span", "collapsed-note", `第 ${unit.start}–${unit.end} 行 · 未改动`));
      return button;
    }
    const card = make("article", "block-card is-unchanged");
    card.dataset.unitId = unit.id;
    const head = make("header", "block-head");
    const copy = make("div", "block-head-copy");
    copy.append(make("span", "block-index", "§"));
    const copyInner = make("div", "");
    copyInner.append(make("h3", "", unit.title), make("p", "", `第 ${unit.start}–${unit.end} 行 · 未改动`));
    copy.append(copyInner);
    const actions = make("div", "block-actions");
    const hide = make("button", "quiet-button compact-button", "收起");
    hide.type = "button";
    hide.dataset.action = "toggle-unit";
    hide.dataset.unit = unit.id;
    actions.append(hide);
    head.append(copy, actions);
    card.append(head);
    const body = make("div", `block-body${isMarkdown(unit.file) ? " markdown-body" : ""}`);
    splitText(unit.rawText).lines.forEach((text, index) => {
      body.append(makeLineRow(unit, { kind: "context", text, oldLine: unit.start + index, newLine: unit.start + index }));
    });
    card.append(body);
    return card;
  }

  function appendInline(parent, text) {
    const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g;
    let cursor = 0;
    for (const match of text.matchAll(pattern)) {
      if (match.index > cursor) parent.append(document.createTextNode(text.slice(cursor, match.index)));
      const token = match[0];
      if (token.startsWith("`")) parent.append(make("code", "", token.slice(1, -1)));
      else if (token.startsWith("**")) parent.append(make("strong", "", token.slice(2, -2)));
      else {
        const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
        const label = make("span", "md-link", link[1]);
        label.title = link[2];
        parent.append(label);
      }
      cursor = match.index + token.length;
    }
    if (cursor < text.length) parent.append(document.createTextNode(text.slice(cursor)));
  }

  function makeLineRow(unit, op) {
    const row = make("div", `line-row ${op.kind}`);
    if (op.kind !== "context" && op.groupIndex) {
      row.classList.add(`decision-${state.decisions.get(op.groupIndex) || "pending"}`);
    }
    const lineNo = op.newLine ?? op.oldLine ?? "";
    row.dataset.line = String(lineNo);
    row.dataset.unit = unit.id;
    row.append(make("span", "line-no old", op.oldLine ? String(op.oldLine) : ""));
    row.append(make("span", "line-no new", op.newLine ? String(op.newLine) : ""));
    row.append(make("span", "line-sign", op.kind === "add" ? "+" : op.kind === "remove" ? "−" : " "));
    const text = make("span", "line-text");
    const heading = /^(#{1,6})\s+/.exec(op.text);
    if (heading) {
      text.classList.add("md-heading", `md-h${Math.min(heading[1].length, 3)}`);
      text.append(document.createTextNode(op.text));
    } else if (/^\s*([-*+]|\d+\.)\s+/.test(op.text)) {
      text.classList.add("md-list");
      appendInline(text, op.text);
    } else {
      appendInline(text, op.text);
    }
    row.append(text);
    const comment = make("button", "line-comment");
    comment.type = "button";
    comment.dataset.action = "line-comment";
    comment.dataset.unit = unit.id;
    comment.dataset.line = String(lineNo);
    comment.dataset.quote = op.text;
    const lineComments = state.comments.filter((item) => item.unitId === unit.id
      && !item.resolved && Number(item.anchor?.startLine) === Number(lineNo));
    if (lineComments.length) {
      row.classList.add("has-comment");
      comment.classList.add("has-comments");
      comment.textContent = String(lineComments.length);
      comment.setAttribute("aria-label", `第 ${lineNo} 行有 ${lineComments.length} 条批注，继续批注`);
    } else {
      comment.setAttribute("aria-label", `批注第 ${lineNo} 行`);
      comment.textContent = "+";
    }
    row.append(comment);
    return row;
  }

  function renderOps(unit) {
    const body = make("div", `block-body${isMarkdown(unit.file) ? " markdown-body" : ""}`);
    let inCode = false;
    unit.ops.forEach((op) => {
      if (op.kind === "skip") {
        body.append(make("div", "diff-skip", op.text));
        return;
      }
      const fence = /^\s*```/.test(op.text);
      if (fence) inCode = !inCode;
      const row = makeLineRow(unit, op);
      if (inCode || fence) row.querySelector(".line-text")?.classList.add("md-code");
      body.append(row);
    });
    return body;
  }

  function renderBlock(unit) {
    const theme = themeById(unit.themeId);
    const status = unitStatus(unit);
    const counts = unitCounts(unit);
    const selected = state.blockId === unit.id;
    const card = make("article", `block-card status-${status} prio-${theme?.priority || "normal"}${selected ? " is-selected" : ""}`);
    card.dataset.unitId = unit.id;
    card.id = `unit-${unit.id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
    card.addEventListener("pointerdown", (event) => {
      if (event.target.closest("button, a, textarea, input")) return;
      if (!window.getSelection()?.isCollapsed) return;
      selectBlock(unit.id);
    });

    const head = make("header", "block-head");
    const copy = make("div", "block-head-copy");
    copy.append(make("span", "block-index", "§"));
    const copyInner = make("div", "block-head-text");
    const titleRow = make("div", "block-title-row");
    titleRow.append(make("h3", "", unit.title), make("span", `status-pill status-${status}`, STATUS_LABEL[status]));
    copyInner.append(titleRow);
    const line = unit.kind === "source" ? `第 ${unit.start} 行起` : `第 ${unit.start}–${unit.end} 行`;
    const notes = [line, `${unit.groups.length} 处改动`];
    if (state.manualMarks.has(`${unit.file.path}::${unit.id}`)) notes.push("已手改");
    copyInner.append(make("p", "", notes.join(" · ")));
    copy.append(copyInner);

    const actions = make("div", "block-actions");
    if (unit.definition) {
      const evidence = make("button", "quiet-button compact-button", "理由与来源");
      evidence.type = "button";
      evidence.dataset.action = "block-evidence";
      evidence.dataset.unit = unit.id;
      actions.append(evidence);
    }
    [["accepted", "接受"], ["rejected", "不接受"], ["pending", "待定"]].forEach(([decision, label]) => {
      const button = make("button", `decision-button compact ${decision}`);
      button.type = "button";
      button.dataset.action = "block-decision";
      button.dataset.decision = decision;
      button.dataset.unit = unit.id;
      button.dataset.focusKey = `${unit.id}:${decision}`;
      button.setAttribute("aria-pressed", String(
        decision === "accepted" ? counts.accepted === unit.groups.length
          : decision === "rejected" ? counts.rejected === unit.groups.length
            : counts.pending === unit.groups.length
      ));
      button.append(make("span", "decision-key", decision === "accepted" ? "A" : decision === "rejected" ? "R" : "?"), make("span", "", label));
      actions.append(button);
    });
    const edit = make("button", "quiet-button compact-button");
    edit.type = "button";
    edit.dataset.action = "edit-unit";
    edit.dataset.unit = unit.id;
    setButtonLabel(edit, "pencil", state.editTarget?.unitId === unit.id ? "取消手改" : "手改");
    actions.append(edit);
    const comment = make("button", "quiet-button compact-button");
    comment.type = "button";
    comment.dataset.action = "comment-unit";
    comment.dataset.unit = unit.id;
    setButtonLabel(comment, "comment", "评论");
    actions.append(comment);
    head.append(copy, actions);
    card.append(head);

    const bar = make("div", "progress-track block-progress");
    const total = Math.max(1, counts.accepted + counts.rejected + counts.pending);
    [["accepted", counts.accepted], ["rejected", counts.rejected], ["pending", counts.pending]].forEach(([tone, value]) => {
      if (!value) return;
      const segment = make("span", `progress-segment ${tone}`);
      segment.style.width = `${(value / total) * 100}%`;
      bar.append(segment);
    });
    card.append(bar);
    card.append(renderOps(unit));

    const comments = state.comments.filter((comment) => comment.filePath === unit.file.path && (comment.unitId ? comment.unitId === unit.id : unit.groups.some((group) => group.key === comment.groupKey)));
    if (comments.length) card.append(renderCommentList(comments));
    if (state.commentTarget?.unitId === unit.id) card.append(renderCommentForm(unit));
    if (state.editTarget?.unitId === unit.id) card.append(renderEditor(unit));
    if (unit.kind === "source" && !unit.groups[0].after.length) {
      const note = make("p", "unit-note", "这是删除块，没有可手改的正文；接受会删除这些行，不接受会保留原内容。");
      card.append(note);
    }
    return card;
  }

  function renderCommentList(comments) {
    const list = make("div", "unit-comments");
    comments.forEach((comment) => {
      const stale = commentStale(comment);
      const card = make("article", `comment-card${comment.resolved ? " resolved" : ""}${stale ? " stale" : ""}`);
      const head = make("div", "comment-head");
      const anchor = comment.anchor || {};
      const range = anchor.startLine ? `第 ${anchor.startLine}${anchor.endLine && anchor.endLine !== anchor.startLine ? `–${anchor.endLine}` : ""} 行` : comment.themeId ? "主题" : "整块";
      head.append(make("span", "comment-range", range));
      if ((anchor.type || "") === "text") head.append(make("span", "comment-kind", "选中文字"));
      if (stale) head.append(make("span", "comment-kind tone-pending", "需重新锚定"));
      head.append(make("span", "comment-time", comment.createdAt ? new Date(comment.createdAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : ""));
      card.append(head);
      card.append(make("p", "comment-body", comment.body));
      if (anchor.quote) card.append(make("blockquote", "comment-quote", `“${anchor.quote.slice(0, 220)}${anchor.quote.length > 220 ? "…" : ""}”`));
      const actions = make("div", "comment-actions");
      const resolve = make("button", "tiny-button", comment.resolved ? "重新打开" : "标记解决");
      resolve.type = "button";
      resolve.dataset.action = "resolve-comment";
      resolve.dataset.comment = comment.id;
      actions.append(resolve);
      card.append(actions);
      list.append(card);
    });
    return list;
  }

  function renderCommentForm(unit) {
    const target = state.commentTarget;
    const form = make("form", "comment-form");
    form.dataset.unit = unit.id;
    const head = make("div", "form-head");
    const anchor = target.anchor || {};
    const range = anchor.startLine ? `第 ${anchor.startLine}${anchor.endLine && anchor.endLine !== anchor.startLine ? `–${anchor.endLine}` : ""} 行` : "整块";
    head.append(make("strong", "", `批注 · ${range}`));
    if (anchor.quote) head.append(make("span", "form-quote", `“${anchor.quote.slice(0, 120)}${anchor.quote.length > 120 ? "…" : ""}”`));
    form.append(head);
    const field = make("textarea", "comment-input");
    field.rows = 3;
    field.placeholder = anchor.startLine ? "这一行哪里需要改？写下具体意见…" : "针对这个块，写下希望怎么改…";
    field.value = state.commentDraft;
    field.dataset.commentInput = "true";
    form.append(field);
    const actions = make("div", "form-actions");
    const save = make("button", "primary-button compact", "保存批注");
    save.type = "submit";
    const cancel = make("button", "quiet-button compact", "取消");
    cancel.type = "button";
    cancel.dataset.action = "cancel-comment";
    actions.append(save, cancel);
    form.append(actions);
    return form;
  }

  function renderEditor(unit) {
    const wrap = make("div", "unit-editor");
    const head = make("div", "form-head");
    head.append(make("strong", "", "手改当前块"));
    head.append(make("span", "form-note", "只影响这一块；保存后回到待定，接受后才会写入。"));
    wrap.append(head);
    const field = make("textarea", "edit-input");
    field.value = state.editDraft;
    field.rows = Math.min(24, Math.max(5, state.editDraft.split("\n").length + 1));
    field.spellcheck = false;
    field.dataset.editInput = "true";
    field.setAttribute("aria-label", "手改当前块正文");
    wrap.append(field);
    const actions = make("div", "form-actions");
    const save = make("button", "primary-button compact", "保存手改");
    save.type = "button";
    save.dataset.action = "save-edit";
    actions.append(save);
    const cancel = make("button", "quiet-button compact", "取消");
    cancel.type = "button";
    cancel.dataset.action = "cancel-edit";
    actions.append(cancel);
    if (state.lastEditUndo) {
      const undo = make("button", "quiet-button compact", "撤销上次手改");
      undo.type = "button";
      undo.dataset.action = "undo-edit";
      actions.append(undo);
    }
    wrap.append(actions);
    return wrap;
  }

  // ---------- rendering: evidence rail ---------------------------------------------------

  function renderEvidence() {
    const panel = byId("evidence-content");
    panel.replaceChildren();
    const theme = themeById(state.themeId);
    if (!theme) return;
    const current = state.themeEvidence ? null : allUnits().find((unit) => unit.id === state.blockId);
    const definition = current?.definition;
    const evidence = definition ? definition.evidenceIds.map(sourceFor).filter(Boolean) : evidenceFor(theme);

    const why = make("section", "evidence-section");
    why.append(make("h2", "", current?.title || theme.title));
    if (definition && state.manualMarks.has(`${current.file.path}::${current.id}`)) {
      why.append(make("p", "edited-note", "以下理由和来源对应原提案。手改后请重新确认它们是否适用。"));
    }
    why.append(make("p", "claim-copy", definition?.reason || theme.claim?.text || "这个主题没有文字说明。"));
    if (definition?.effect) why.append(make("p", "source-note", definition.effect));
    if (definition?.dependsOn?.length) {
      const names = definition.dependsOn.map((id) => allUnits().find((unit) => unit.definition?.id === id)?.title || id);
      why.append(make("p", "source-note", `关联块 · ${names.join("、")}`));
    }
    const cases = make("section", "evidence-section");
    const caseHead = make("div", "section-head");
    caseHead.append(make("h2", "", "参考来源"));
    caseHead.append(make("span", "count-badge", String(evidence.length)));
    cases.append(caseHead);
    if (!evidence.length) {
      cases.append(make("p", "empty-note", "这个主题没有记录案例来源。不能据此把判断说成已有事实证明。"));
    } else {
      evidence.forEach((source) => {
        const role = source.role || source.kind || "参考";
        const card = make("article", `source-card tone-${roleTone(role)}`);
        const top = make("div", "source-top");
        top.append(make("span", `role-chip tone-${roleTone(role)}`, role));
        const label = source.label || String(source.id || "来源");
        if (/^https?:\/\/\S+$/.test(source.locator || "")) {
          const link = make("a", "source-title", label);
          link.href = source.locator;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          top.append(link);
        } else {
          top.append(make("strong", "", label));
        }
        card.append(top);
        if (source.excerpt) card.append(make("p", "source-excerpt", source.excerpt));
        if (source.locator && !/^https?:\/\/\S+$/.test(source.locator)) card.append(make("p", "source-locator", source.locator));
        if (source.note) card.append(make("p", "source-note", source.note));
        cases.append(card);
      });
    }
    panel.append(cases);

    const comments = state.comments.filter((comment) => themeForComment(comment) === theme.id);
    const commentSection = make("section", "evidence-section");
    const commentHead = make("div", "section-head");
    commentHead.append(make("h2", "", state.themeEvidence ? "主题批注" : "批注"));
    commentHead.append(make("span", "count-badge", String(comments.filter((comment) => !comment.resolved).length)));
    commentSection.append(commentHead);
    const themeComments = comments.filter((comment) => comment.themeId === theme.id);
    const blockComments = comments.filter((comment) => !comment.themeId);
    if (state.themeEvidence) {
      const form = make("form", "theme-comment-form");
      const input = make("textarea", "");
      input.id = "theme-comment-input";
      input.dataset.themeCommentInput = theme.id;
      input.setAttribute("aria-label", "主题批注");
      input.placeholder = "写下对整个主题的建议…";
      input.rows = 3;
      input.required = true;
      input.value = state.themeDrafts.get(theme.id) || "";
      const submit = make("button", "secondary-button", "保存主题批注");
      submit.type = "submit";
      form.append(input, submit);
      commentSection.append(form);
    }
    if (themeComments.length) commentSection.append(renderCommentList(themeComments));
    if (!comments.length && !state.themeEvidence) {
      commentSection.append(make("p", "empty-note", "还没有批注。点行号旁的 +，或选中一句话。"));
    } else {
      blockComments.forEach((comment) => {
        const card = make("button", "comment-jump");
        card.type = "button";
        card.dataset.action = "jump-comment";
        card.dataset.comment = comment.id;
        const anchor = comment.anchor || {};
        card.append(make("span", "comment-range", anchor.startLine ? `第 ${anchor.startLine} 行` : "整块"));
        card.append(make("span", "comment-jump-body", comment.body));
        if (comment.resolved) card.append(make("span", "comment-kind", "已解决"));
        commentSection.append(card);
      });
    }
    if (state.themeEvidence) panel.prepend(commentSection);
    panel.insertBefore(why, cases);
    if (!state.themeEvidence) panel.append(commentSection);

    const log = make("section", "evidence-section");
    log.append(make("p", "eyebrow", "Log"));
    log.append(make("h2", "", "决定记录"));
    if (!state.log.length) log.append(make("p", "empty-note", "你的接受、不接受、手改与批注会按时间记录在这里。"));
    else {
      const list = make("ol", "log-list");
      state.log.slice(-8).reverse().forEach((entry) => {
        const item = make("li", "");
        item.append(make("span", "log-time", entry.time), make("span", "", entry.text));
        list.append(item);
      });
      log.append(list);
    }
    panel.append(log);

    const details = make("details", "provenance");
    details.append(make("summary", "", "bundle 与来源信息"));
    const rows = [
      ["bundle", bundle.bundleId],
      ["digest", bundle.bundleDigest],
      ["mode", bundle.mode],
      ["base", bundle.comparison?.base || "—"],
      ["head", bundle.comparison?.head || "—"],
      ["workspace", bundle.comparison?.workspaceRevision || "—"],
    ];
    const pre = make("pre", "");
    pre.textContent = rows.map(([key, value]) => `${key}: ${value}`).join("\n");
    details.append(pre);
    panel.append(details);
  }

  // ---------- rendering: footer and layout ----------------------------------------------

  function renderFooter() {
    const stats = globalStats();
    const node = byId("review-state");
    node.replaceChildren();
    const line = make("div", "state-line");
    line.append(make("strong", "", `${stats.accepted}/${stats.total} 处改动已接受`));
    const rest = [];
    if (stats.pending) rest.push(`${stats.pending} 待定`);
    if (stats.rejected) rest.push(`${stats.rejected} 不接受`);
    if (stats.openComments) rest.push(`${stats.openComments} 条批注`);
    if (rest.length) line.append(document.createTextNode(` · ${rest.join(" · ")}`));
    node.append(line);
    const bar = make("div", "progress-track footer-progress");
    const total = Math.max(1, stats.total);
    [["accepted", stats.accepted], ["rejected", stats.rejected], ["pending", stats.pending]].forEach(([tone, value]) => {
      if (!value) return;
      const segment = make("span", `progress-segment ${tone}`);
      segment.style.width = `${(value / total) * 100}%`;
      bar.append(segment);
    });
    node.append(bar);
    if (stats.pending) {
      const clear = make("button", "tiny-button pending-clear", "把剩余待定设为不接受");
      clear.type = "button";
      clear.dataset.action = "pending-as-rejected";
      node.append(clear);
    }
    const prepare = byId("prepare-apply");
    const undecided = stats.pending > 0 || (stats.accepted === 0 && stats.rejected === 0);
    const editing = Boolean(live && state.editTarget);
    prepare.disabled = undecided || editing;
    byId("check-only").disabled = prepare.disabled;
    prepare.title = stats.pending > 0 ? `仍有 ${stats.pending} 处改动待定；先决定或设为不接受。` : editing ? "先完成或取消当前手改" : live ? "检查并写入已决定的改动" : "检查目标并导出响应包";
    setButtonLabel(prepare, "check-circle", stats.pending ? `还有 ${stats.pending} 处改动待定` : live ? "检查并应用" : "准备应用");
    byId("download-patch").disabled = stats.accepted === 0 && stats.rejected === 0;
  }

  function renderLayout() {
    const overview = byId("overview-screen");
    const review = byId("review-screen");
    const inReview = state.screen === "review";
    overview.hidden = inReview;
    review.hidden = !inReview;
    byId("action-bar").hidden = (!inReview && !live) || state.zen;
    byId("command-bar").hidden = !state.commandOpen || state.zen;
    byId("restore-command").hidden = state.commandOpen || state.zen;
    byId("zen-exit").hidden = !state.zen;
    document.body.classList.toggle("zen-mode", state.zen);
    document.body.classList.toggle("overview-mode", !inReview);
    review.classList.toggle("themes-collapsed", !state.themeRailOpen || state.zen);
    review.classList.toggle("evidence-collapsed", !state.evidenceRailOpen || state.zen);
    byId("theme-rail").hidden = !state.themeRailOpen || state.zen;
    byId("evidence-rail").hidden = !state.evidenceRailOpen || state.zen;
    byId("restore-theme-rail").hidden = !inReview || state.themeRailOpen || state.zen || state.commandOpen;
    byId("restore-evidence").hidden = !inReview || state.evidenceRailOpen || state.zen || state.commandOpen;
    byId("collapse-command").setAttribute("aria-expanded", "true");
    byId("selection-bubble").hidden = true;
    byId("toggle-theme-rail").hidden = !inReview;
    byId("toggle-evidence").hidden = !inReview;
    if (!inReview) {
      byId("toggle-theme-rail").hidden = true;
      byId("toggle-evidence").hidden = true;
    }
  }

  function restoreFocus(focusKey) {
    if (!focusKey) return;
    const candidates = [...document.querySelectorAll("[data-focus-key]")];
    const next = candidates.find((node) => node.dataset.focusKey === focusKey);
    next?.focus({ preventScroll: true });
  }

  function renderAll() {
    const focusKey = document.activeElement?.dataset?.focusKey;
    if (state.screen === "review" && !themeFiles(state.themeId).some((item) => item.path === state.filePath)) {
      state.filePath = themeFiles(state.themeId)[0]?.path || firstFilePath;
      state.blockId = themeUnitsForFile(state.themeId, state.filePath).find((unit) => unit.changed)?.id || null;
    }
    renderTopbar();
    if (state.screen === "overview") renderOverview();
    else {
      renderThemeRail();
      renderThemeHeader();
      renderFileTabs();
      renderBlocks();
      renderEvidence();
    }
    if (live || state.screen === "review") renderFooter();
    renderLayout();
    restoreFocus(focusKey);
    updateDebug();
    live?.changed();
  }

  // ---------- decisions and edits --------------------------------------------------------

  function logAction(text) {
    state.log.push({ time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }), text });
    if (state.log.length > 40) state.log.shift();
  }

  function setGroupDecision(group, decision) {
    if (decision === "pending") state.decisions.delete(group.key);
    else state.decisions.set(group.key, decision);
  }

  function setUnitDecision(unit, decision) {
    unit.groups.forEach((group) => setGroupDecision(group, decision));
    logAction(`${DECISION_LABEL[decision]} · ${unit.title}`);
  }

  function setThemeDecision(themeId, decision) {
    const units = unitsForTheme(themeId);
    units.forEach((unit) => unit.groups.forEach((group) => setGroupDecision(group, decision)));
    logAction(`${DECISION_LABEL[decision]} · 主题「${themeById(themeId)?.title || themeId}」全部 ${units.length} 块`);
  }

  function pendingAsRejected() {
    allUnits().filter((unit) => unit.changed).forEach((unit) => {
      unit.groups.filter((group) => decisionOf(group) === "pending").forEach((group) => state.decisions.set(group.key, "rejected"));
    });
    logAction("把所有剩余待定块设为不接受");
    renderAll();
    toast("剩余待定已明确设为不接受");
  }

  function openComment(unit, anchor) {
    state.commentTarget = { unitId: unit.id, filePath: unit.file.path, anchor };
    state.commentDraft = "";
    state.editTarget = null;
    state.blockId = unit.id;
    renderAll();
    document.querySelector(`[data-unit-id="${CSS.escape(unit.id)}"] textarea[data-comment-input]`)?.focus();
  }

  function openEditor(unit) {
    if (unit.kind !== "explicit" && !unit.groups.some((group) => group.after.length)) {
      toast("删除块没有可手改的正文");
      return;
    }
    state.editTarget = { unitId: unit.id, filePath: unit.file.path };
    state.editDraft = unit.kind === "source" ? unit.groups[0].after.join("\n") : unit.rawText;
    state.commentTarget = null;
    state.blockId = unit.id;
    renderAll();
    document.querySelector(`[data-unit-id="${CSS.escape(unit.id)}"] textarea[data-edit-input]`)?.focus();
  }

  function saveEditor(unit) {
    const file = fileFor(unit.file.path);
    if (!file) return;
    if (unit.kind === "explicit") {
      const before = blockText(file, unit.definition);
      let next = canonicalText(state.editDraft);
      const original = linesWithEndings(initialText(file));
      const needsEnding = unit.definition.newRange[1] < original.length || before.endsWith("\n");
      if (next && needsEnding && !next.endsWith("\n")) next += "\n";
      const key = explicitKey(file, unit.definition);
      state.lastEditUndo = { filePath: file.path, block: unit.definition, before, unitId: unit.id,
        decision: state.decisions.get(key), marked: state.manualMarks.has(`${file.path}::${unit.id}`) };
      state.decisions.delete(key);
      state.blockEdits.set(key, next);
      state.edits.set(file.path, composeBlocks(file, () => true));
      state.manualMarks.add(`${file.path}::${unit.id}`);
      state.editTarget = null;
      state.commentTarget = null;
      invalidate();
      logAction(`手改 · ${unit.title}（需重新决定）`);
      renderAll();
      toast("已保存手改；这个块回到待定");
      return;
    }
    const current = currentText(file);
    if (current === null) return;
    const { lines, trailing } = splitText(current);
    let nextLines;
    let undo;
    if (unit.kind === "markdown") {
      const start = Math.max(0, unit.start - 1);
      const end = Math.min(lines.length, unit.end);
      const draft = splitText(state.editDraft).lines;
      nextLines = [...lines.slice(0, start), ...draft, ...lines.slice(end)];
      undo = { filePath: file.path, start, end: start + draft.length, before: lines.slice(start, end).join("\n"), unitId: unit.id, kind: unit.kind };
    } else {
      const group = unit.groups[0];
      const start = Math.max(0, (group.newLine ?? 1) - 1);
      const removeCount = group.after.length;
      const draft = splitText(state.editDraft).lines;
      nextLines = [...lines.slice(0, start), ...draft, ...lines.slice(start + removeCount)];
      undo = { filePath: file.path, start, end: start + draft.length, before: lines.slice(start, start + removeCount).join("\n"), unitId: unit.id, kind: unit.kind };
    }
    state.edits.set(file.path, joinText(nextLines, trailing));
    state.lastEditUndo = undo;
    state.manualMarks.add(`${file.path}::${unit.id}`);
    state.editTarget = null;
    state.commentTarget = null;
    invalidate();
    logAction(`手改 · ${unit.title}（需重新决定）`);
    renderAll();
    toast("已保存手改；这个块回到待定");
  }

  function undoEditor() {
    const undo = state.lastEditUndo;
    if (!undo) return;
    const file = fileFor(undo.filePath);
    if (!file) return;
    if (undo.block) {
      const key = explicitKey(file, undo.block);
      state.blockEdits.set(key, undo.before);
      state.edits.set(file.path, composeBlocks(file, () => true));
      state.decisions.delete(key);
      if (undo.decision) state.decisions.set(key, undo.decision);
      state.lastEditUndo = null;
      state.editTarget = null;
      state.editDraft = "";
      if (!undo.marked) state.manualMarks.delete(`${file.path}::${undo.unitId}`);
      invalidate();
      logAction("撤销上次手改");
      renderAll();
      return;
    }
    const current = currentText(file);
    if (current === null) return;
    const { lines, trailing } = splitText(current);
    const draft = splitText(undo.before).lines;
    const nextLines = [...lines.slice(0, undo.start), ...draft, ...lines.slice(undo.end)];
    state.edits.set(file.path, joinText(nextLines, trailing));
    state.lastEditUndo = null;
    if (undo.unitId) state.manualMarks.delete(`${undo.filePath}::${undo.unitId}`);
    invalidate();
    logAction("撤销上次手改");
    renderAll();
    toast("已撤销上次手改");
  }

  function addComment(unit, anchor, body) {
    const text = body.trim();
    if (!text) return;
    const comment = {
      id: `comment-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
      filePath: unit.file.path,
      unitId: unit.id,
      groupKey: unit.groups[0]?.key || null,
      anchor: { ...anchor, view: anchor.type || "block" },
      body: text,
      resolved: false,
      createdAt: new Date().toISOString(),
      unitFingerprint: unit.fingerprint,
      proposalFingerprint: contentFingerprint(currentText(fileFor(unit.file.path))),
    };
    state.comments.push(comment);
    state.commentTarget = null;
    state.commentDraft = "";
    state.anchor = null;
    logAction(`批注 · ${unit.title}${anchor.startLine ? ` · 第 ${anchor.startLine} 行` : ""}`);
    renderAll();
    toast("批注已保存");
  }

  function commentStale(comment) {
    if (comment.unitId) {
      const unit = allUnits().find((item) => item.id === comment.unitId);
      if (!unit) return true;
      if (comment.unitFingerprint && unit.fingerprint !== comment.unitFingerprint) return true;
      if (unit.kind === "explicit" && !comment.anchor?.startLine) return false;
    }
    if (!comment.proposalFingerprint) return false;
    const file = fileFor(comment.filePath);
    return Boolean(file) && comment.proposalFingerprint !== contentFingerprint(currentText(file));
  }

  // ---------- compose and export ---------------------------------------------------------

  function composeFile(file, fs) {
    if (file.reviewBlocks) {
      return composeBlocks(file, (index) => decisionOf(fs.groups[index]) === "accepted");
    }
    const groups = fs.groups;
    const accepted = groups.filter((group) => decisionOf(group) === "accepted");
    const output = [];
    let trailingSource = "base";
    fs.operations.forEach((op, index) => {
      if (op.kind === "context") {
        output.push(op.text);
        trailingSource = "base";
        return;
      }
      const group = groupAt(groups, index);
      const decision = group ? decisionOf(group) : "pending";
      if (op.kind === "remove" && decision !== "accepted") {
        output.push(op.text);
        trailingSource = "base";
      }
      if (op.kind === "add" && decision === "accepted") {
        output.push(op.text);
        trailingSource = "current";
      }
    });
    const sourceText = accepted.length === groups.length
      ? currentText(file)
      : accepted.length === 0 || trailingSource === "base" ? baseText(file) : currentText(file);
    return joinText(output, splitText(sourceText).trailing);
  }

  function fileDesired(file, fs) {
    const accepted = fs.groups.filter((group) => decisionOf(group) === "accepted");
    const decided = fs.groups.filter((group) => decisionOf(group) !== "pending");
    if (!fs.groups.length || !decided.length) return { accepted: false, desiredText: null, deleted: false };
    if (file.reviewBlocks && accepted.length) {
      const text = composeFile(file, fs);
      const deleted = !file.proposed && text === "" && accepted.length === fs.groups.length;
      return { accepted: true, desiredText: deleted ? null : text, deleted };
    }
    if (bundle.mode === "git-review") {
      if (!accepted.length) {
        if (file.baseline?.text == null) return { accepted: true, desiredText: null, deleted: true };
        return { accepted: true, desiredText: baseText(file), deleted: false };
      }
      if ((!file.proposed || file.proposed.text == null) && accepted.length === fs.groups.length) {
        return { accepted: true, desiredText: null, deleted: true };
      }
      return { accepted: true, desiredText: composeFile(file, fs), deleted: false };
    }
    if (!accepted.length) return { accepted: false, desiredText: null, deleted: false };
    if ((!file.proposed || file.proposed.text == null) && accepted.length === fs.groups.length) {
      return { accepted: true, desiredText: null, deleted: true };
    }
    return { accepted: true, desiredText: composeFile(file, fs), deleted: false };
  }

  function responsePackage() {
    const files = bundle.files.map((file) => {
      const fs = fileState(file);
      const result = fileDesired(file, fs);
      return { path: file.path, accepted: result.accepted, desiredText: result.desiredText };
    });
    const decisions = [];
    const themesToScan = [...themes, unmappedTheme].filter((theme) => unitsForTheme(theme.id).length);
    themesToScan.forEach((theme) => {
      unitsForTheme(theme.id).forEach((unit) => {
        unit.groups.forEach((group) => {
          decisions.push({
            claimId: theme.id,
            theme: theme.title,
            blockId: unit.id,
            blockTitle: unit.title,
            filePath: unit.file.path,
            line: group.newLine ?? group.oldLine ?? null,
            decision: decisionOf(group),
            edited: state.manualMarks.has(`${unit.file.path}::${unit.id}`),
          });
        });
      });
    });
    return {
      schemaVersion: "1.0",
      bundleId: bundle.bundleId,
      bundleDigest: bundle.bundleDigest,
      files,
      comments: state.comments.map((comment) => ({
        ...comment,
        anchorState: commentStale(comment) ? "needs-reanchor" : "current",
        currentProposalFingerprint: fileFor(comment.filePath) ? contentFingerprint(currentText(fileFor(comment.filePath))) : null,
      })),
      decisions,
    };
  }

  function feedbackMarkdown() {
    const response = responsePackage();
    const stats = globalStats();
    const out = [
      `# Review feedback — ${bundle.title}`,
      "",
      `- Bundle: \`${bundle.bundleId}\``,
      `- Digest: \`${bundle.bundleDigest}\``,
      `- Blocks: ${stats.accepted} accepted · ${stats.rejected} rejected · ${stats.pending} pending`,
      "",
      "## Themes",
      "",
    ];
    sortedThemes().forEach((theme) => {
      const counts = themeCounts(theme.id);
      const status = themeStatus(theme.id);
      out.push(`- ${theme.letter} — ${theme.title}: ${STATUS_LABEL[status]} (${counts.accepted} accepted / ${counts.rejected} rejected / ${counts.pending} pending)`);
      if (theme.claim?.text) out.push(`  - ${theme.claim.text}`);
    });
    out.push("", "## Files", "");
    response.files.forEach((item) => {
      const fs = fileState(fileFor(item.path));
      const accepted = fs.groups.filter((group) => decisionOf(group) === "accepted").length;
      out.push(`- ${item.accepted ? "Write" : "Skip"}: \`${item.path}\` — ${accepted}/${fs.groups.length} blocks accepted`);
    });
    const comments = state.comments.filter((comment) => !comment.resolved);
    if (comments.length) {
      out.push("", "## Unresolved annotations", "");
      comments.forEach((comment) => {
        const anchor = comment.anchor || {};
        const range = anchor.startLine ? `L${anchor.startLine}${anchor.endLine && anchor.endLine !== anchor.startLine ? `–${anchor.endLine}` : ""}` : "block";
        const scope = comment.themeId ? `主题 · ${themeById(comment.themeId)?.title || comment.themeId}` : `${comment.filePath} · ${range}`;
        out.push(`- [${scope}] ${comment.body}${anchor.quote ? ` — “${anchor.quote.replace(/\s+/g, " ").slice(0, 180)}”` : ""}`);
      });
    }
    return out.join("\n");
  }

  function buildPatch(path, before, after) {
    const oldText = splitText(before);
    const newText = splitText(after);
    const oldLength = oldText.lines.length;
    const newLength = newText.lines.length;
    const operations = diffOperations(before ?? "", after ?? "");
    if (!operations.some((op) => op.kind !== "context") && oldText.trailing === newText.trailing) return "";
    const header = [
      `--- a/${path}`,
      `+++ b/${path}`,
      `@@ -${oldLength ? 1 : 0},${oldLength} +${newLength ? 1 : 0},${newLength} @@`,
    ];
    const noNewline = "\\ No newline at end of file";
    const body = operations.flatMap((op) => {
      const oldUnterminated = op.oldLine === oldLength && !oldText.trailing;
      const newUnterminated = op.newLine === newLength && !newText.trailing;
      if (op.kind === "context" && oldUnterminated !== newUnterminated) {
        return [`-${op.text}`, ...(oldUnterminated ? [noNewline] : []),
          `+${op.text}`, ...(newUnterminated ? [noNewline] : [])];
      }
      const prefix = op.kind === "add" ? "+" : op.kind === "remove" ? "-" : " ";
      return oldUnterminated || newUnterminated ? [`${prefix}${op.text}`, noNewline] : [`${prefix}${op.text}`];
    });
    return [...header, ...body, ""].join("\n");
  }

  function download(name, content, type) {
    const url = URL.createObjectURL(new Blob([content], { type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 500);
  }

  async function copyText(value) {
    try {
      await navigator.clipboard.writeText(value);
    } catch (_) {
      const area = document.createElement("textarea");
      area.value = value;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
  }

  function toast(message) {
    const node = byId("toast");
    node.textContent = message;
    node.classList.add("visible");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => node.classList.remove("visible"), 2800);
  }

  function updateDebug() {
    // Offline QA hook: read-only view of the exact response the page would export.
    window.__workbenchDebug = {
      response: () => responsePackage(),
      feedback: () => feedbackMarkdown(),
      state: () => ({
        screen: state.screen,
        themeId: state.themeId,
        filePath: state.filePath,
        blockId: state.blockId,
        stats: globalStats(),
      }),
      units: () => allUnits().filter((unit) => unit.changed).map((unit) => ({
        id: unit.id,
        file: unit.file.path,
        themeId: unit.themeId,
        title: unit.title,
        start: unit.start,
        end: unit.end,
        groups: unit.groups.length,
        decisions: unit.groups.map(decisionOf),
      })),
      groups: () => bundle.files.map((file) => ({
        path: file.path,
        groups: fileState(file).groups.map((group) => ({
          key: group.key,
          oldLine: group.oldLine,
          newLine: group.newLine,
          before: group.before,
          after: group.after,
        })),
      })),
    };
  }

  // ---------- events ---------------------------------------------------------------------

  function updateSelectionBubble() {
    const bubble = byId("selection-bubble");
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) { bubble.hidden = true; state.selection = null; return; }
    const text = selection.toString().trim();
    if (text.length < 1) { bubble.hidden = true; state.selection = null; return; }
    const range = selection.getRangeAt(0);
    const row = (range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement)?.closest?.(".line-row");
    if (!row) { bubble.hidden = true; state.selection = null; return; }
    const unitNode = row.closest("[data-unit-id]");
    if (!unitNode) { bubble.hidden = true; state.selection = null; return; }
    const endContainer = range.endContainer.nodeType === 1 ? range.endContainer : range.endContainer.parentElement;
    const endRow = endContainer?.closest?.(".line-row");
    state.selection = {
      unitId: unitNode.dataset.unitId,
      quote: text,
      startLine: Number(row.dataset.line) || null,
      endLine: Number(endRow?.dataset.line) || Number(row.dataset.line) || null,
    };
    const rect = range.getBoundingClientRect();
    bubble.style.left = `${Math.min(window.innerWidth - 150, Math.max(8, rect.left))}px`;
    bubble.style.top = `${Math.min(window.innerHeight - 50, rect.bottom + 8)}px`;
    bubble.hidden = false;
  }

  document.addEventListener("mouseup", (event) => {
    if (event.target.closest("button, a, textarea, input")) return;
    setTimeout(updateSelectionBubble, 0);
  });
  document.addEventListener("keyup", (event) => {
    if (event.key === "Shift" || event.key.startsWith("Arrow")) updateSelectionBubble();
  });
  document.addEventListener("mousedown", (event) => {
    if (!event.target.closest("#selection-bubble")) byId("selection-bubble").hidden = true;
  });

  byId("selection-bubble").addEventListener("click", () => {
    const chosen = state.selection;
    if (!chosen) return;
    const unit = allUnits().find((item) => item.id === chosen.unitId);
    if (!unit) return;
    openComment(unit, { type: "text", startLine: chosen.startLine, endLine: chosen.endLine, quote: chosen.quote });
  });

  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-action]");
    if (!target) {
      const card = event.target.closest(".block-card[data-unit-id]");
      if (card && !event.target.closest("button, a, textarea, input") && window.getSelection()?.isCollapsed) selectBlock(card.dataset.unitId);
      return;
    }
    const action = target.dataset.action;
    if (action === "filter") {
      state.filter = target.dataset.filter || "all";
      renderAll();
      return;
    }
    if (action === "select-theme") {
      selectTheme(target.dataset.theme);
      return;
    }
    if (action === "select-file") {
      selectFile(target.dataset.file);
      return;
    }
    if (action === "jump-block") {
      const unit = unitsForTheme(state.themeId).find((item) => item.id === target.dataset.unit);
      if (!unit) return;
      if (window.matchMedia?.("(max-width: 900px)").matches) state.themeRailOpen = false;
      if (unit.file.path !== state.filePath) selectFile(unit.file.path);
      selectBlock(unit.id);
      renderLayout();
      const card = document.querySelector(`[data-unit-id="${CSS.escape(unit.id)}"]`);
      card?.querySelector("[data-action=block-evidence], [data-action=block-decision]")?.focus({ preventScroll: true });
      card?.scrollIntoView({ block: "nearest" });
      return;
    }
    if (action === "theme-comment") {
      state.themeEvidence = true;
      state.evidenceRailOpen = true;
      state.zen = false;
      renderAll();
      byId("theme-comment-input")?.focus();
      return;
    }
    if (action === "theme-decision") {
      byId("theme-decisions").open = false;
      setThemeDecision(state.themeId, target.dataset.decision);
      renderAll();
      byId("theme-decisions").querySelector("summary").focus();
      return;
    }
    if (action === "block-evidence") {
      state.themeEvidence = false;
      state.blockId = target.dataset.unit;
      state.evidenceRailOpen = true;
      state.zen = false;
      renderAll();
      byId("collapse-evidence")?.focus();
      return;
    }
    if (action === "block-decision") {
      const unit = allUnits().find((item) => item.id === target.dataset.unit);
      if (!unit) return;
      setUnitDecision(unit, target.dataset.decision);
      renderAll();
      return;
    }
    if (action === "edit-unit") {
      const unit = allUnits().find((item) => item.id === target.dataset.unit);
      if (!unit) return;
      if (state.editTarget?.unitId === unit.id) { state.editTarget = null; state.editDraft = ""; renderAll(); }
      else openEditor(unit);
      return;
    }
    if (action === "save-edit") {
      const unit = allUnits().find((item) => item.id === state.editTarget?.unitId);
      if (unit) saveEditor(unit);
      return;
    }
    if (action === "cancel-edit") { state.editTarget = null; state.editDraft = ""; renderAll(); return; }
    if (action === "undo-edit") { undoEditor(); return; }
    if (action === "comment-unit") {
      const unit = allUnits().find((item) => item.id === target.dataset.unit);
      if (!unit) return;
      openComment(unit, { type: "block", startLine: null, endLine: null, quote: "" });
      return;
    }
    if (action === "line-comment") {
      const unit = allUnits().find((item) => item.id === target.dataset.unit);
      if (!unit) return;
      openComment(unit, { type: "line", startLine: Number(target.dataset.line), endLine: Number(target.dataset.line), quote: target.dataset.quote || "" });
      return;
    }
    if (action === "cancel-comment") { state.commentTarget = null; state.commentDraft = ""; renderAll(); return; }
    if (action === "resolve-comment") {
      const comment = state.comments.find((item) => item.id === target.dataset.comment);
      if (!comment) return;
      comment.resolved = !comment.resolved;
      logAction(`${comment.resolved ? "解决" : "重新打开"}批注 · ${comment.body.slice(0, 24)}`);
      renderAll();
      return;
    }
    if (action === "jump-comment") {
      const comment = state.comments.find((item) => item.id === target.dataset.comment);
      if (!comment) return;
      const unit = allUnits().find((item) => item.id === comment.unitId);
      const themeId = unit?.themeId || themeForComment(comment);
      if (unit) {
        state.themeId = themeId;
        state.filePath = unit.file.path;
        state.blockId = unit.id;
        state.screen = "review";
        writeHistory("review", themeId, state.canGoBack ? "replace" : "push");
        renderAll();
        const card = document.querySelector(`[data-unit-id="${CSS.escape(unit.id)}"] .comment-card`);
        card?.scrollIntoView({ block: "center", behavior: prefersReduced() ? "auto" : "smooth" });
        card?.focus?.();
      }
      return;
    }
    if (action === "toggle-unit") {
      const id = target.dataset.unit;
      if (state.expandedUnits.has(id)) state.expandedUnits.delete(id);
      else state.expandedUnits.add(id);
      renderAll();
      return;
    }
    if (action === "pending-as-rejected") { pendingAsRejected(); return; }
    if (action === "zen-exit") { state.zen = false; renderAll(); return; }
    if (action === "toggle-help") { byId("help-dialog").showModal(); return; }
    if (action === "collapse-theme-rail") { state.themeRailOpen = false; renderAll(); byId(state.commandOpen ? "toggle-theme-rail" : "restore-theme-rail").focus(); return; }
    if (action === "collapse-evidence") { state.evidenceRailOpen = false; renderAll(); byId(state.commandOpen ? "toggle-evidence" : "restore-evidence").focus(); return; }
  });

  document.addEventListener("submit", (event) => {
    const themeForm = event.target.closest("form.theme-comment-form");
    if (themeForm) {
      event.preventDefault();
      const input = themeForm.querySelector("[data-theme-comment-input]");
      const body = input.value.trim();
      if (!body) return;
      const themeId = input.dataset.themeCommentInput;
      state.comments.push({
        id: `comment-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
        themeId, filePath: null, anchor: { type: "theme", view: "theme" },
        body, resolved: false, createdAt: new Date().toISOString(),
      });
      state.themeDrafts.delete(themeId);
      logAction(`主题批注 · ${themeById(themeId).title}`);
      renderAll();
      byId("theme-comment-input")?.focus();
      toast("主题批注已保存");
      return;
    }
    const form = event.target.closest("form.comment-form");
    if (!form) return;
    event.preventDefault();
    const unit = allUnits().find((item) => item.id === form.dataset.unit);
    if (!unit) return;
    addComment(unit, state.commentTarget?.anchor || { type: "block" }, state.commentDraft);
  });

  document.addEventListener("input", (event) => {
    if (event.target.matches("[data-comment-input]")) state.commentDraft = event.target.value;
    if (event.target.matches("[data-theme-comment-input]")) state.themeDrafts.set(event.target.dataset.themeCommentInput, event.target.value);
    if (event.target.matches("[data-edit-input]")) state.editDraft = event.target.value;
    live?.changed();
  });

  byId("show-themes").addEventListener("click", () => {
    if (state.screen === "review" && state.canGoBack && history.state?.screen === "review") history.back();
    else showOverview({ historyMode: "replace", focus: true });
  });
  byId("toggle-theme-rail").addEventListener("click", () => { state.themeRailOpen = !state.themeRailOpen; renderAll(); });
  byId("toggle-evidence").addEventListener("click", () => { state.evidenceRailOpen = !state.evidenceRailOpen; renderAll(); });
  byId("restore-theme-rail").addEventListener("click", () => { state.themeRailOpen = true; renderAll(); byId("collapse-theme-rail")?.focus(); });
  byId("restore-evidence").addEventListener("click", () => { state.evidenceRailOpen = true; renderAll(); byId("collapse-evidence")?.focus(); });
  byId("enter-zen").addEventListener("click", () => { state.zen = true; state.themeRailOpen = false; state.evidenceRailOpen = false; renderAll(); });
  byId("zen-exit").addEventListener("click", () => { state.zen = false; state.themeRailOpen = true; state.evidenceRailOpen = true; renderAll(); });
  byId("collapse-command").addEventListener("click", () => { state.commandOpen = false; renderAll(); byId("restore-command").focus(); });
  byId("restore-command").addEventListener("click", () => { state.commandOpen = true; renderAll(); byId("collapse-command").focus(); });

  byId("download-patch").addEventListener("click", () => {
    const patch = bundle.files.map((file) => {
      const fs = fileState(file);
      const result = fileDesired(file, fs);
      if (!result.accepted) return "";
      const current = file.proposed?.text ?? "";
      return buildPatch(file.path, current, result.desiredText);
    }).join("\n");
    if (!patch.trim()) { toast("还没有接受任何改动"); return; }
    download(`${bundle.bundleId}.patch`, patch, "text/x-diff;charset=utf-8");
    toast("已下载当前接受内容的 Patch");
  });

  byId("copy-feedback").addEventListener("click", async () => {
    await copyText(feedbackMarkdown());
    toast("结构化反馈已复制");
  });

  byId("prepare-apply").addEventListener("click", () => {
    const stats = globalStats();
    if (stats.pending) { toast(`仍有 ${stats.pending} 处改动待定`); return; }
    if (live) { live.run("apply"); return; }
    const preview = byId("apply-preview");
    preview.replaceChildren();
    let count = 0;
    bundle.files.forEach((file) => {
      const fs = fileState(file);
      const result = fileDesired(file, fs);
      const accepted = fs.groups.filter((group) => decisionOf(group) === "accepted").length;
      const row = make("div", `apply-file${result.accepted ? "" : " skipped"}`);
      row.append(make("span", "apply-path", file.path));
      const countLabel = !result.accepted
        ? "不写入"
        : accepted
          ? `${accepted}/${fs.groups.length} 处改动接受`
          : bundle.mode === "git-review" ? "全部不接受 · 恢复基线" : "全部不接受";
      row.append(make("span", "apply-count", countLabel));
      row.append(make("span", "apply-precondition", file.applyPrecondition.kind === "exists" ? `expect ${shortHash(file.applyPrecondition.sha256)}` : "expect absent"));
      if (result.accepted) count += 1;
      preview.append(row);
    });
    preview.append(make("p", "apply-note", `将处理 ${count} 个文件（含恢复基线与删除）。任一目标内容变化时，宿主会整批停止并保留你的决定与批注。`));
    byId("apply-dialog").showModal();
  });

  byId("download-feedback").addEventListener("click", () => {
    const response = responsePackage();
    response.files = [];
    download(`${bundle.bundleId}.review-feedback.json`, JSON.stringify(response, null, 2) + "\n", "application/json;charset=utf-8");
    toast("已下载决定与批注");
  });

  byId("download-response").addEventListener("click", () => {
    const response = responsePackage();
    download(`${bundle.bundleId}.review-response.json`, JSON.stringify(response, null, 2) + "\n", "application/json;charset=utf-8");
    toast("响应包已下载；尚未写入工作空间");
  });

  byId("copy-apply-command").addEventListener("click", async () => {
    const command = [
      "请使用 HTML 审阅工作台的显式 adapter 应用这份响应：",
      `1. 用原始 bundle \`${bundle.bundleId}\` 和下载的 review-response.json 运行 workbench.py check。`,
      "2. 如果任一文件 stale，停止写入并保留编辑与批注。",
      "3. check 通过后运行 workbench.py apply，再读取文件验证结果。",
      "4. 核对未被接受的块确实保留原内容。",
    ].join("\n");
    await copyText(command);
    toast("应用说明已复制");
  });

  byId("toggle-help").addEventListener("click", () => byId("help-dialog").showModal());

  window.addEventListener("popstate", (event) => {
    state.canGoBack = false;
    if (event.state?.screen === "review") {
      enterTheme(event.state.themeId, { historyMode: "none" });
      return;
    }
    showOverview({ historyMode: "none" });
  });

  document.addEventListener("keydown", (event) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
    if (event.key === "Escape") {
      if (state.editTarget || state.commentTarget) {
        state.editTarget = null;
        state.commentTarget = null;
        state.commentDraft = "";
        renderAll();
        return;
      }
      if (state.zen) { state.zen = false; state.themeRailOpen = true; state.evidenceRailOpen = true; renderAll(); return; }
      if (byId("apply-dialog").open) byId("apply-dialog").close();
      else if (byId("help-dialog").open) byId("help-dialog").close();
      else if (state.evidenceRailOpen && byId("evidence-rail").contains(document.activeElement)) {
        byId("collapse-evidence").click();
      }
      return;
    }
    if (typing) return;
    if (event.altKey && ["j", "k"].includes(event.key.toLowerCase())) {
      event.preventDefault();
      if (event.shiftKey) nextTheme(event.key.toLowerCase() === "j" ? 1 : -1);
      else nextBlock(event.key.toLowerCase() === "j" ? 1 : -1);
    }
  });

  if (window.startLiveReview) {
    live = window.startLiveReview(captureReview, restoreReview, responsePackage, download);
  }
  const initialHash = location.hash || "";
  if (initialHash.startsWith("#theme=")) {
    const id = decodeURIComponent(initialHash.slice("#theme=".length));
    state.screen = "review";
    state.themeId = themeById(id)?.id || themes[0]?.id || UNMAPPED;
    const files = themeFiles(state.themeId);
    const draft = state.editTarget || state.commentTarget;
    state.filePath = files.find((file) => file.path === draft?.filePath)?.path || files[0]?.path || firstFilePath;
    state.blockId = draft?.unitId || themeUnitsForFile(state.themeId, state.filePath).find((unit) => unit.changed)?.id || null;
    if (state.themeDrafts.get(state.themeId)) { state.themeEvidence = true; state.evidenceRailOpen = true; }
    writeHistory("review", state.themeId, "replace");
  } else {
    writeHistory("overview", null, "replace");
  }
  renderAll();
})();
