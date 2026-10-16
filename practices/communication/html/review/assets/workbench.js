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
  const initialText = (file) => file.proposed ? file.proposed.text : null;
  const claimPaths = (claim) => claim?.filePaths || (claim?.filePath ? [claim.filePath] : []);
  const mappedClaims = (bundle.story?.claims || []).filter((claim) => claimPaths(claim).length);
  const firstFilePath = bundle.files[0]?.path || null;

  function canonicalText(value) {
    return (value ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  }

  function lines(value) {
    const normalized = canonicalText(value);
    if (!normalized) return [];
    const result = normalized.split("\n");
    if (result[result.length - 1] === "") result.pop();
    return result;
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

  const state = {
    screen: "overview",
    filePath: firstFilePath,
    view: bundle.files[0]?.path?.toLowerCase().endsWith(".md") ? "markdown" : "source",
    editMode: false,
    selectedClaimId: mappedClaims[0]?.id || null,
    edits: new Map(bundle.files.map((file) => [file.path, initialText(file)])),
    accepted: new Set(bundle.files.map((file) => file.path)),
    comments: Array.isArray(bundle.comments) ? [...bundle.comments] : [],
    rationaleFingerprints: new Map(bundle.files.map((file) => [file.path, contentFingerprint(initialText(file))])),
    preview: null,
    anchor: null,
    reportOpen: false,
    issueOpen: true,
    filesOpen: true,
    inspectorOpen: true,
    commandOpen: true,
    zen: false,
  };

  const fileFor = (path = state.filePath) => bundle.files.find((file) => file.path === path);
  const claimFor = (id) => (bundle.story?.claims || []).find((claim) => claim.id === id);
  const sourceFor = (id) => (bundle.sources || []).find((source) => source.id === id);
  const isMarkdown = (file) => Boolean(file && /\.(md|mdx|markdown)$/i.test(file.path));

  function writeHistory(screen, claimId, mode = "push") {
    const hash = screen === "review" && claimId
      ? `#problem-${encodeURIComponent(claimId)}`
      : "#problems";
    const target = `${location.href.replace(/#.*$/, "")}${hash}`;
    try {
      const method = mode === "replace" ? "replaceState" : "pushState";
      history[method]({ workbenchScreen: screen, claimId }, "", target);
    } catch (_) {
      // Some file viewers restrict history writes; visible controls still provide navigation.
    }
  }

  function mappingNeedsReview(file) {
    if (!file) return false;
    return state.rationaleFingerprints.get(file.path) !== contentFingerprint(state.edits.get(file.path));
  }

  function commentAnchorState(comment) {
    const file = fileFor(comment.filePath);
    if (!file) return "missing-file";
    const current = contentFingerprint(state.edits.get(file.path));
    const anchor = comment.proposalFingerprint || contentFingerprint(initialText(file));
    return current === anchor ? "anchored" : "needs-reanchor";
  }

  function diffOperations(beforeValue, afterValue) {
    const before = lines(beforeValue);
    const after = lines(afterValue);
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

  function compactOperations(operations, context = 4) {
    const changed = operations.map((op, index) => op.kind !== "context" ? index : -1).filter((index) => index >= 0);
    if (!changed.length) return operations;
    const keep = new Set();
    changed.forEach((index) => {
      for (let cursor = Math.max(0, index - context); cursor <= Math.min(operations.length - 1, index + context); cursor += 1) keep.add(cursor);
    });
    const result = [];
    let skipped = false;
    operations.forEach((op, index) => {
      if (keep.has(index)) {
        if (skipped) result.push({ kind: "skip", text: "… 未变内容已折叠 …", oldLine: null, newLine: null, sourceIndex: -1 });
        result.push({ ...op, sourceIndex: index });
        skipped = false;
      } else {
        skipped = true;
      }
    });
    return result;
  }

  function changeGroups(operations) {
    const groups = [];
    let current = null;
    operations.forEach((op, index) => {
      if (op.kind === "context") {
        if (current) {
          groups.push(current);
          current = null;
        }
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
    return groups;
  }

  function groupForIndex(groups, index) {
    return groups.find((group) => group.indexes.includes(index)) || null;
  }

  function buildPatch(path, before, after) {
    const beforeLines = lines(before);
    const afterLines = lines(after);
    const operations = diffOperations(before, after);
    if (!operations.some((op) => op.kind !== "context")) return "";
    const header = [
      `--- a/${path}`,
      `+++ b/${path}`,
      `@@ -${beforeLines.length ? 1 : 0},${beforeLines.length} +${afterLines.length ? 1 : 0},${afterLines.length} @@`,
    ];
    const body = operations.map((op) => `${op.kind === "add" ? "+" : op.kind === "remove" ? "-" : " "}${op.text}`);
    return [...header, ...body, ""].join("\n");
  }

  function applicationBase(file) {
    if (file.applyPrecondition.kind === "absent") return "";
    return bundle.mode === "git-review" ? (file.proposed?.text ?? "") : (file.baseline?.text ?? "");
  }

  function statusLabel(status) {
    return ({ added: "新增", deleted: "删除", modified: "修改", renamed: "重命名" })[status] || status;
  }

  function kindLabel(claim) {
    const epistemic = claim?.epistemicStatus || claim?.kind || "analysis";
    return ({ fact: "事实", evidence: "证据", inference: "推断", analysis: "分析", unknown: "未知", recommendation: "建议" })[epistemic] || epistemic;
  }

  function claimIdsForFile(file) {
    if (!file) return [];
    return [...new Set((file.diff?.hunks || []).flatMap((hunk) => hunk.claimIds || []))];
  }

  function claimHunks(claim) {
    return bundle.files.flatMap((file) => (file.diff?.hunks || [])
      .filter((hunk) => hunk.claimIds?.includes(claim.id))
      .map((hunk) => ({ file, hunk })));
  }

  function reviewFiles() {
    const claim = claimFor(state.selectedClaimId);
    if (!claim) return bundle.files;
    const paths = new Set(claimPaths(claim));
    return bundle.files.filter((file) => paths.has(file.path));
  }

  function activeClaim(file = fileFor()) {
    const selected = claimFor(state.selectedClaimId);
    if (selected && (!file || claimPaths(selected).includes(file.path))) return selected;
    return claimFor(claimIdsForFile(file)[0]) || selected || null;
  }

  function requirementKey(claim) {
    const index = mappedClaims.findIndex((item) => item.id === claim.id);
    return index >= 0 && index < 26 ? String.fromCharCode(65 + index) : String(index + 1);
  }

  function enterReview(claim, { historyMode = "push" } = {}) {
    if (claim) state.selectedClaimId = claim.id;
    const selected = claimFor(state.selectedClaimId);
    const path = claimPaths(selected).find((item) => fileFor(item));
    if (path) state.filePath = path;
    state.screen = "review";
    state.view = "diff";
    state.editMode = false;
    state.preview = null;
    state.anchor = null;
    state.zen = false;
    if (historyMode !== "none") writeHistory("review", state.selectedClaimId, historyMode);
    renderAll();
    byId("review-surface").focus();
  }

  const {closeLayer,bindPreview} = WorkspaceUI.create();

  function renderHeader() {
    const header = byId("review-header");
    header.replaceChildren();
    const copy = make("div", "header-copy");
    copy.append(make("h1", "", bundle.title));
    copy.append(make("p", "", bundle.summary));
    const meta = make("div", "header-meta");
    const comparison = bundle.comparison || {};
    if (comparison.base) meta.append(make("span", "stamp", `base ${shortHash(comparison.base)}`));
    if (comparison.head) meta.append(make("span", "stamp", `head ${shortHash(comparison.head)}`));
    if (comparison.workspaceRevision) meta.append(make("span", "stamp", `workspace ${shortHash(comparison.workspaceRevision)}`));
    meta.append(make("span", "status-pill", "精确基线"));
    header.append(copy, meta);
    header.hidden = !state.reportOpen || state.zen;
    const inReview = state.screen === "review";
    const brandMark = byId("brand-mark");
    brandMark.replaceChildren(inReview ? makeIcon("arrow-left") : document.createTextNode("W"));
    byId("command-title").textContent = inReview ? "问题总览" : "问题";
    byId("show-problems").setAttribute("aria-label", inReview ? "返回问题总览" : "当前位于问题总览");
    byId("command-context").textContent = !inReview
      ? bundle.title
      : (activeClaim()?.label || fileFor()?.path || bundle.title);
  }

  function renderProblemPreview(claim) {
    const preview = byId("problem-preview");
    preview.replaceChildren();
    byId("problem-list").querySelectorAll("[data-claim-id]").forEach((button) => {
      button.setAttribute("aria-current", String(Boolean(claim && button.dataset.claimId === claim.id)));
    });
    if (!claim) {
      preview.append(make("p", "eyebrow", "Quick preview"));
      preview.append(make("h2", "", "先看问题，再决定是否进入"));
      preview.append(make("p", "context-copy", "把指针停在一个问题上，或用键盘聚焦它；这里会展示相关文件和少量真实 diff。"));
      return;
    }
    preview.dataset.claimId = claim.id;
    const entries = claimHunks(claim);
    const firstEntry = entries[0];
    const route = make("div", "evidence-route");
    route.append(make("span", "evidence-route-key", `问题 ${requirementKey(claim)}`));
    route.append(make("span", "evidence-route-path", firstEntry
      ? `${firstEntry.file.path} · ${firstEntry.hunk.header || "changed"}`
      : "尚未映射到改动块"));
    preview.append(route);
    preview.append(make("h2", "", claim.label || claim.text));
    preview.append(make("p", "preview-rationale", claim.rationale || claim.text));
    entries.slice(0, 3).forEach(({ file, hunk }) => {
      const card = make("article", "preview-diff-card");
      const heading = make("div", "preview-diff-heading");
      heading.append(make("strong", "", file.path));
      heading.append(make("span", "", hunk.header || "changed"));
      card.append(heading);
      const code = make("div", "preview-diff-lines");
      const changed = (hunk.lines || []).filter((line) => line.kind !== "context");
      const visible = changed.length ? changed.slice(0, 6) : (hunk.lines || []).slice(0, 6);
      visible.forEach((line) => {
        const row = make("div", `preview-diff-line ${line.kind}`);
        row.append(make("span", "", line.kind === "add" ? "+" : line.kind === "remove" ? "−" : " "));
        row.append(make("code", "", line.text));
        code.append(row);
      });
      card.append(code);
      preview.append(card);
    });
    if (entries.length > 3) preview.append(make("p", "preview-more", `还有 ${entries.length - 3} 个改动块，进入后逐个查看。`));
    const open = make("button", "primary-button preview-open", "查看完整改动");
    open.type = "button";
    open.addEventListener("click", () => enterReview(claim));
    preview.append(open);
  }

  function renderProblems() {
    const list = byId("problem-list");
    list.replaceChildren();
    if (!mappedClaims.length) {
      list.append(make("p", "problem-empty", "这份报告还没有把问题映射到文件；不能据此声称改动已完整覆盖。"));
      renderProblemPreview(null);
      return;
    }
    mappedClaims.forEach((claim) => {
      const paths = claimPaths(claim);
      const hunks = claimHunks(claim);
      const button = make("button", "problem-card");
      button.type = "button";
      button.dataset.claimId = claim.id;
      button.setAttribute("aria-describedby", `problem-detail-${requirementKey(claim)}`);
      button.append(make("span", "requirement-key", requirementKey(claim)));
      const copy = make("span", "problem-card-copy");
      copy.append(make("strong", "", claim.label || claim.text));
      const detail = make("span", "problem-card-detail", claim.text);
      detail.id = `problem-detail-${requirementKey(claim)}`;
      copy.append(detail);
      const firstPath = paths[0] ? basename(paths[0]) : "未映射";
      copy.append(make("small", "", `${paths.length} 个文件 · ${hunks.length} 个改动块 · ${firstPath}`));
      button.append(copy);
      button.addEventListener("mouseenter", () => renderProblemPreview(claim));
      button.addEventListener("focus", () => renderProblemPreview(claim));
      button.addEventListener("keydown", (event) => {
        if (!["Enter", " "].includes(event.key)) return;
        event.preventDefault();
        enterReview(claim);
      });
      button.addEventListener("click", () => enterReview(claim));
      list.append(button);
    });
    renderProblemPreview(claimFor(state.selectedClaimId) || mappedClaims[0]);
  }

  function renderFiles() {
    const nav = byId("file-list");
    nav.replaceChildren();
    const visibleFiles = reviewFiles();
    byId("file-count").textContent = visibleFiles.length === bundle.files.length
      ? String(bundle.files.length)
      : `${visibleFiles.length}/${bundle.files.length}`;
    const selected = claimFor(state.selectedClaimId);
    const mappedPaths = new Set(claimPaths(selected));
    visibleFiles.forEach((file) => {
      const button = make("button", `file-button ${mappedPaths.has(file.path) ? "mapped" : ""}`);
      button.type = "button";

      button.setAttribute("aria-current", file.path === state.filePath ? "true" : "false");
      button.append(make("span", "file-name", file.path));
      const meta = make("span", "file-meta");
      const operations = diffOperations(file.baseline?.text ?? "", state.edits.get(file.path) ?? "");
      meta.append(make("span", "", statusLabel(file.status)));
      meta.append(make("span", "", `+${operations.filter((op) => op.kind === "add").length} −${operations.filter((op) => op.kind === "remove").length}`));
      if (state.edits.get(file.path) !== initialText(file)) meta.append(make("span", "", "已编辑"));
      if (!state.accepted.has(file.path)) meta.append(make("span", "", "已拒绝"));
      button.append(meta);
      button.addEventListener("click", () => {
        state.filePath = file.path;
        state.view = isMarkdown(file) ? "markdown" : "source";
        state.editMode = false;
        state.preview = null;
        state.anchor = null;
        renderAll();
        byId("review-surface").focus();
      });
      const build = panel => {
        panel.append(make('span','preview-label','文件快览'),make('h3','',basename(file.path)),make('code','preview-path',file.path));
        const metrics=make('div','preview-metrics');
        metrics.append(make('span','preview-add',`+${operations.filter(op=>op.kind==='add').length} 新增`),make('span','preview-remove',`−${operations.filter(op=>op.kind==='remove').length} 删除`));panel.append(metrics);
        panel.append(make('span','preview-label','关联问题'));
        const claims=mappedClaims.filter(claim=>claimPaths(claim).includes(file.path));
        panel.append(make('p','preview-summary',claims.map(claim=>claim.label||claim.text).join('；')||'尚未关联问题'));
        const changes=operations.filter(op=>['add','remove'].includes(op.kind)).slice(0,3);
        if(changes.length){const sample=make('pre','preview-code');sample.textContent=changes.map(op=>`${op.kind==='add'?'+':'−'} ${op.text}`).join('\n');panel.append(sample);}
        panel.append(make('footer','','点击文件进入审阅 · Esc 关闭快览'));
      };
      bindPreview(button,build);
      const peek=make('button','file-peek','⋯');peek.type='button';peek.setAttribute('aria-label',`${basename(file.path)} 快览`);bindPreview(peek,build,true);
      const row=make('div','file-entry');row.append(button,peek);nav.append(row);
    });
  }

  function renderToolbar(file) {
    byId("file-status").textContent = file ? statusLabel(file.status) : "无文件";
    byId("current-path").textContent = file?.path || "本次没有文件改动";
    const switcher = byId("view-switcher");
    switcher.replaceChildren();
    [
      ["markdown", "Markdown"],
      ["source", "源文件"],
      ["diff", "Diff"],
    ].forEach(([value, label]) => {
      const button = make("button", "segment-button", label);
      button.type = "button";
      button.dataset.view = value;
      button.setAttribute("aria-pressed", String(state.view === value && !state.editMode));
      button.disabled = value === "markdown" && !isMarkdown(file);
      button.addEventListener("click", () => {
        state.view = value;
        state.editMode = false;
        state.preview = null;
        renderAll();
      });
      switcher.append(button);
    });
    const edit = byId("edit-toggle");
    edit.disabled = !file || file.editable === false || state.edits.get(file.path) === null;
    setButtonLabel(edit, "pencil", state.editMode ? "完成编辑" : "编辑源文件");
    edit.setAttribute("aria-pressed", String(state.editMode));
    const accept = byId("accept-file");
    accept.disabled = !file;
    const accepted = Boolean(file && state.accepted.has(file.path));
    setButtonLabel(accept, accepted ? "check" : "x", accepted ? "已接受" : "已拒绝");
    accept.setAttribute("aria-pressed", String(Boolean(file && state.accepted.has(file.path))));
  }

  function renderIntent(file) {
    const intent = byId("file-intent");
    intent.classList.toggle("intent-stale", mappingNeedsReview(file));
    intent.replaceChildren();
    if (!file) {
      intent.append(make("strong", "", "没有规则文件提案"), document.createTextNode("这也是有效的反思结果。"));
      return;
    }
    const claim = activeClaim(file);
    const prefix = claim ? `要求 ${requirementKey(claim)}` : "未映射";
    intent.append(make("strong", "", prefix));
    intent.append(document.createTextNode(mappingNeedsReview(file)
      ? "你已编辑提案；原理由与覆盖关系需要重新确认。"
      : file.intent));
  }

  function appendInline(parent, text) {
    const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g;
    let cursor = 0;
    for (const match of text.matchAll(pattern)) {
      if (match.index > cursor) parent.append(document.createTextNode(text.slice(cursor, match.index)));
      const token = match[0];
      if (token.startsWith("`")) {
        parent.append(make("code", "", token.slice(1, -1)));
      } else if (token.startsWith("**")) {
        parent.append(make("strong", "", token.slice(2, -2)));
      } else {
        const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
        const anchor = make("a", "", link[1]);
        const href = link[2].trim();
        if (/^(https?:|mailto:|#|\/|\.\.?\/)/i.test(href)) anchor.href = href;
        else anchor.title = `未启用的链接：${href}`;
        parent.append(anchor);
      }
      cursor = match.index + token.length;
    }
    if (cursor < text.length) parent.append(document.createTextNode(text.slice(cursor)));
  }

  function parseMarkdown(value) {
    const source = lines(value);
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

  function blockChanged(block, groups) {
    return groups.some((group) => {
      const line = group.newLine ?? group.oldLine ?? 1;
      return line >= block.start && line <= block.end;
    });
  }

  function groupNearLine(groups, line) {
    return groups.find((group) => (group.newLine ?? group.oldLine ?? -1) >= line && (group.newLine ?? group.oldLine ?? -1) <= line + 1) || null;
  }

  function setAnchor(anchor) {
    state.anchor = {
      ...anchor,
      filePath: state.filePath,
      proposalFingerprint: contentFingerprint(state.edits.get(state.filePath)),
    };
    renderAnchor();
  }

  function setPreview(group) {
    state.preview = group ? { ...group, filePath: state.filePath } : null;
    renderContext();
  }

  function renderMarkdown(file, operations, groups) {
    const documentNode = make("article", "markdown-document");
    const blocks = parseMarkdown(state.edits.get(file.path) ?? "");
    if (!blocks.length) documentNode.append(make("p", "context-copy", "当前提案为空。"));
    blocks.forEach((block) => {
      const wrapper = make("section", `md-block ${blockChanged(block, groups) ? "changed" : ""}`);
      wrapper.tabIndex = 0;
      wrapper.dataset.sourceStart = String(block.start);
      wrapper.dataset.sourceEnd = String(block.end);
      wrapper.append(make("span", "md-line-number", block.start === block.end ? String(block.start) : `${block.start}–${block.end}`));
      let content;
      if (block.type === "heading") {
        content = document.createElement(`h${Math.min(block.level, 4)}`);
        appendInline(content, block.value);
      } else if (block.type === "code") {
        content = make("pre", "");
        content.append(make("code", "", block.value));
      } else if (block.type === "quote") {
        content = make("blockquote", "");
        appendInline(content, block.value);
      } else if (block.type === "ul" || block.type === "ol") {
        content = document.createElement(block.type);
        block.items.forEach((item) => {
          const li = document.createElement("li");
          appendInline(li, item);
          content.append(li);
        });
      } else if (block.type === "rule") {
        content = document.createElement("hr");
      } else {
        content = make("p", "");
        block.value.split("\n").forEach((part, index) => {
          if (index) content.append(document.createTextNode(" "));
          appendInline(content, part);
        });
      }
      wrapper.append(content);
      const selectBlock = () => {
        const selection = window.getSelection();
        if (selection && !selection.isCollapsed) return;
        setAnchor({ type: "line-range", startLine: block.start, endLine: block.end, quote: block.value || block.items?.join(" ") || "", view: "markdown" });
        const group = groupNearLine(groups, block.start);
        if (group) setPreview(group);
      };
      wrapper.addEventListener("click", selectBlock);
      wrapper.addEventListener("keydown", (event) => {
        if (!["Enter", " "].includes(event.key)) return;
        event.preventDefault();
        selectBlock();
      });
      wrapper.addEventListener("mouseenter", () => {
        const group = groupNearLine(groups, block.start);
        if (group) setPreview(group);
      });
      documentNode.append(wrapper);
    });
    byId("content-surface").append(documentNode);
  }

  function renderCode(file, operations, groups, compact) {
    const view = make("div", "code-view");
    const visible = compact ? compactOperations(operations) : operations.map((op, index) => ({ ...op, sourceIndex: index }));
    const selected = claimFor(state.selectedClaimId);
    const claimMapped = Boolean(selected && claimPaths(selected).includes(file.path) && !mappingNeedsReview(file));
    visible.forEach((op) => {
      if (op.kind === "skip") {
        view.append(make("div", "skip-row", op.text));
        return;
      }
      const group = groupForIndex(groups, op.sourceIndex);
      const row = make("div", `code-row ${op.kind} ${claimMapped && op.kind !== "context" ? "mapped" : ""}`);
      row.tabIndex = 0;
      row.dataset.sourceStart = String(op.newLine ?? op.oldLine ?? 1);
      row.dataset.sourceEnd = row.dataset.sourceStart;
      row.append(make("span", "line-no", op.oldLine ?? ""));
      row.append(make("span", "line-no", op.newLine ?? ""));
      row.append(make("span", "change-mark", op.kind === "add" ? "+" : op.kind === "remove" ? "−" : ""));
      row.append(make("span", "code-cell", op.text));
      const selectRow = () => {
        setAnchor({
          type: "line",
          startLine: op.newLine ?? op.oldLine,
          endLine: op.newLine ?? op.oldLine,
          oldLine: op.oldLine,
          newLine: op.newLine,
          quote: op.text,
          view: compact ? "diff" : "source",
        });
        if (group) setPreview(group);
      };
      row.addEventListener("click", selectRow);
      row.addEventListener("keydown", (event) => {
        if (!["Enter", " "].includes(event.key)) return;
        event.preventDefault();
        selectRow();
      });
      row.addEventListener("mouseenter", () => { if (group) setPreview(group); });
      row.addEventListener("focus", () => { if (group) setPreview(group); });
      view.append(row);
    });
    byId("content-surface").append(view);
  }

  function lineAtOffset(text, offset) {
    return canonicalText(text).slice(0, offset).split("\n").length;
  }

  function renderEditor(file) {
    const wrap = make("div", "source-editor-wrap");
    const gutter = make("pre", "editor-gutter");
    const editor = make("textarea", "proposal-editor");
    editor.id = "proposal-editor";
    editor.spellcheck = false;
    editor.setAttribute("aria-label", `编辑 ${file.path}`);
    editor.value = state.edits.get(file.path) ?? "";
    const updateGutter = () => {
      gutter.textContent = lines(editor.value).map((_, index) => String(index + 1)).join("\n") || "1";
      gutter.scrollTop = editor.scrollTop;
    };
    updateGutter();
    editor.addEventListener("scroll", updateGutter);
    editor.addEventListener("input", () => {
      state.edits.set(file.path, canonicalText(editor.value));
      updateGutter();
      renderProblems();
      renderFiles();
      renderIntent(file);
      renderContext();
      renderComments();
      renderState();
    });
    const captureEditorSelection = () => {
      const start = editor.selectionStart;
      const end = editor.selectionEnd;
      const quote = editor.value.slice(start, end);
      const startLine = lineAtOffset(editor.value, start);
      const endLine = lineAtOffset(editor.value, Math.max(start, end - 1));
      setAnchor({ type: quote ? "text" : "line", startLine, endLine, quote: quote || lines(editor.value)[startLine - 1] || "", view: "source-edit" });
    };
    editor.addEventListener("select", captureEditorSelection);
    editor.addEventListener("mouseup", captureEditorSelection);
    editor.addEventListener("keyup", captureEditorSelection);
    wrap.append(gutter, editor);
    const reset = make("button", "quiet-button editor-reset", "恢复 Agent 原提案");
    reset.type = "button";
    reset.style.position = "absolute";
    reset.style.right = "22px";
    reset.style.bottom = "18px";
    reset.addEventListener("click", () => {
      state.edits.set(file.path, initialText(file));
      state.editMode = false;
      state.anchor = null;
      renderAll();
      toast("已恢复 Agent 原提案");
    });
    const surface = byId("content-surface");
    surface.style.position = "relative";
    surface.append(wrap, reset);
    requestAnimationFrame(() => editor.focus());
  }

  function captureRenderedSelection() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.toString().trim()) return;
    const elementFor = (node) => (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement)?.closest?.("[data-source-start]");
    const startNode = elementFor(selection.anchorNode);
    const endNode = elementFor(selection.focusNode);
    if (!startNode || !endNode || !byId("content-surface").contains(startNode) || !byId("content-surface").contains(endNode)) return;
    const startLine = Math.min(Number(startNode.dataset.sourceStart), Number(endNode.dataset.sourceStart));
    const endLine = Math.max(Number(startNode.dataset.sourceEnd), Number(endNode.dataset.sourceEnd));
    setAnchor({ type: "text", startLine, endLine, quote: selection.toString().trim(), view: state.view });
  }

  function renderContent(file) {
    const surface = byId("content-surface");
    surface.replaceChildren();
    surface.style.position = "";
    if (!file) {
      const empty = make("article", "markdown-document");
      empty.append(make("h2", "", "本次没有文件改动"));
      empty.append(make("p", "context-copy", "反思可以得出“不修改规则”。上方要求与右侧依据仍可用于审阅这一结论。"));
      surface.append(empty);
      return;
    }
    if (state.editMode) {
      renderEditor(file);
      return;
    }
    const operations = diffOperations(file.baseline?.text ?? "", state.edits.get(file.path) ?? "");
    const groups = changeGroups(operations);
    if (state.view === "markdown" && isMarkdown(file)) renderMarkdown(file, operations, groups);
    else renderCode(file, operations, groups, state.view === "diff");
  }

  function renderContext() {
    const panel = byId("context-panel");
    panel.replaceChildren();
    const file = fileFor();
    const claim = activeClaim(file);
    panel.append(make("p", "eyebrow", "Why · How · Coverage"));
    if (!claim) {
      panel.append(make("h2", "context-title", "这项改动没有要求映射"));
      panel.append(make("p", "context-copy", file?.intent || "没有需要审阅的文件改动。"));
      return;
    }
    panel.append(make("span", "context-label", `要求 ${requirementKey(claim)} · ${kindLabel(claim)}`));
    panel.append(make("h2", "context-title", claim.label || claim.text));
    panel.append(make("p", "context-copy", claim.text));
    if (claim.rationale) panel.append(make("p", "context-copy", claim.rationale));
    if (file && mappingNeedsReview(file)) {
      const stale = make("div", "stale-notice");
      stale.append(document.createTextNode("当前文本已被编辑，原始理由不再自动解释它。"));
      const confirm = make("button", "tiny-button", "确认仍适用");
      confirm.type = "button";
      confirm.style.marginLeft = "8px";
      confirm.addEventListener("click", () => {
        state.rationaleFingerprints.set(file.path, contentFingerprint(state.edits.get(file.path)));
        renderAll();
        toast("理由已重新锚定到当前文本");
      });
      stale.append(confirm);
      panel.append(stale);
    }
    const mapping = make("ul", "mapping-list");
    claimPaths(claim).forEach((path) => {
      const item = document.createElement("li");
      const count = claimHunks(claim).filter((entry) => entry.file.path === path).length;
      item.append(make("code", "", path), document.createTextNode(` · ${count} 个改动块`));
      mapping.append(item);
    });
    panel.append(mapping);
    const evidence = (claim.evidenceIds || []).map(sourceFor).filter(Boolean);
    if (evidence.length) {
      const evidenceList = make("ul", "evidence-list");
      evidence.forEach((source) => evidenceList.append(make("li", "", `${source.label || source.id}${source.excerpt ? ` — ${source.excerpt}` : ""}`)));
      panel.append(evidenceList);
    }
    if (state.preview && state.preview.filePath === state.filePath) {
      const preview = make("section", "change-preview");
      preview.append(make("h3", "", "这处具体改了什么"));
      const before = make("div", "preview-side");
      before.append(make("strong", "", `原文件 · 行 ${state.preview.oldLine ?? "—"}`));
      before.append(make("pre", "", state.preview.before.length ? state.preview.before.join("\n") : "（新增内容）"));
      const after = make("div", "preview-side");
      after.append(make("strong", "", `当前提案 · 行 ${state.preview.newLine ?? "—"}`));
      after.append(make("pre", "", state.preview.after.length ? state.preview.after.join("\n") : "（已删除）"));
      preview.append(before, after);
      panel.append(preview);
    } else {
      panel.append(make("p", "context-copy", "悬停或点选带 + / − 的行，可在这里对照原文件与当前提案。"));
    }
  }

  function renderAnchor() {
    const preview = byId("anchor-preview");
    preview.replaceChildren();
    preview.classList.toggle("active", Boolean(state.anchor && state.anchor.filePath === state.filePath));
    if (!state.anchor || state.anchor.filePath !== state.filePath) {
      preview.append(document.createTextNode("尚未选择锚点。点一行，或在 Markdown / 源文件中选中一句话。"));
      return;
    }
    const range = state.anchor.startLine === state.anchor.endLine ? `第 ${state.anchor.startLine} 行` : `第 ${state.anchor.startLine}–${state.anchor.endLine} 行`;
    preview.append(make("strong", "", `${range} · ${state.anchor.type === "text" ? "选中文本" : "行锚点"}`));
    if (state.anchor.quote) preview.append(make("span", "anchor-quote", `“${state.anchor.quote.slice(0, 220)}${state.anchor.quote.length > 220 ? "…" : ""}”`));
  }

  function renderComments() {
    const list = byId("comment-list");
    list.replaceChildren();
    const visible = state.comments.filter((comment) => comment.filePath === state.filePath);
    const openCount = state.comments.filter((comment) => !comment.resolved).length;
    byId("comment-count").textContent = String(openCount);
    byId("comment-body").disabled = !state.filePath;
    if (!visible.length) list.append(make("p", "context-copy", "当前文件还没有批注。"));
    visible.forEach((comment) => {
      const anchorState = commentAnchorState(comment);
      const card = make("article", `comment-card ${comment.resolved ? "resolved" : ""} ${anchorState === "needs-reanchor" ? "stale-anchor" : ""}`);
      card.append(make("p", "", comment.body));
      const anchor = comment.anchor || {};
      if (anchor.quote) card.append(make("p", "comment-quote", `“${anchor.quote.slice(0, 150)}${anchor.quote.length > 150 ? "…" : ""}”`));
      const meta = make("div", "comment-meta");
      const range = anchor.startLine ? `L${anchor.startLine}${anchor.endLine !== anchor.startLine ? `–${anchor.endLine}` : ""}` : (comment.hunkId || "file");
      meta.append(make("span", "", `${range}${anchorState === "needs-reanchor" ? " · 需重新锚定" : ""}`));
      const toggle = make("button", "tiny-button", comment.resolved ? "重新打开" : "解决");
      toggle.type = "button";
      toggle.addEventListener("click", () => {
        comment.resolved = !comment.resolved;
        renderComments();
        renderState();
      });
      meta.append(toggle);
      card.append(meta);
      list.append(card);
    });
  }

  function renderState() {
    const dirty = bundle.files.filter((file) => state.edits.get(file.path) !== initialText(file)).length;
    const open = state.comments.filter((comment) => !comment.resolved).length;
    const staleMappings = bundle.files.filter(mappingNeedsReview).length;
    const unexplained = bundle.files.flatMap((file) => file.diff?.hunks || []).filter((hunk) => hunk.unexplained).length;
    const node = byId("review-state");
    node.replaceChildren();
    const text = make("span", "");
    text.append(make("strong", "", `${state.accepted.size}/${bundle.files.length} 文件已接受`));
    text.append(document.createTextNode(` · ${dirty} 已编辑 · ${open} 未解决 · ${mappedClaims.length} 项要求已映射${unexplained ? ` · ${unexplained} 块未解释` : ""}${staleMappings ? ` · ${staleMappings} 映射待复核` : ""}`));
    node.append(text);
  }

  function renderIssueStrip() {
    const claim = claimFor(state.selectedClaimId);
    const copy = byId("issue-strip-copy");
    copy.replaceChildren();
    if (claim) {
      copy.append(make("span", "requirement-key small", requirementKey(claim)));
      const text = make("div", "");
      text.append(make("strong", "", claim.label || claim.text));
      text.append(make("span", "", `${claimPaths(claim).length} 个相关文件 · ${claimHunks(claim).length} 个改动块`));
      copy.append(text);
    }
    byId("issue-strip").hidden = !state.issueOpen || state.zen;
  }

  function renderLayout() {
    const overview = byId("overview-screen");
    const review = byId("review-screen");
    const workbench = byId("workbench");
    const inReview = state.screen === "review";
    overview.hidden = inReview;
    review.hidden = !inReview;
    byId("action-bar").hidden = !inReview || state.zen;
    byId("command-bar").hidden = !state.commandOpen || state.zen;
    byId("restore-command").hidden = state.commandOpen || state.zen;
    byId("review-back-to-problems").hidden = !inReview || state.commandOpen || state.zen;
    byId("zen-exit").hidden = !state.zen;
    document.body.classList.toggle("zen-mode", state.zen);
    document.body.classList.toggle("overview-mode", !inReview);
    document.querySelectorAll(".review-only").forEach((node) => { node.hidden = !inReview; });
    workbench.classList.toggle("files-collapsed", !state.filesOpen || state.zen);
    workbench.classList.toggle("inspector-collapsed", !state.inspectorOpen || state.zen);
    byId("files-panel").hidden = !state.filesOpen || state.zen;
    byId("inspector-panel").hidden = !state.inspectorOpen || state.zen;
    byId("restore-issue").hidden = !inReview || state.issueOpen || state.zen;
    byId("restore-files").hidden = !inReview || state.filesOpen || state.zen;
    byId("restore-inspector").hidden = !inReview || state.inspectorOpen || state.zen;
    byId("toggle-report").setAttribute("aria-pressed", String(state.reportOpen));
    byId("toggle-issue").setAttribute("aria-pressed", String(state.issueOpen));
    byId("toggle-files").setAttribute("aria-pressed", String(state.filesOpen));
    byId("toggle-inspector").setAttribute("aria-pressed", String(state.inspectorOpen));
  }

  function renderAll() {
    closeLayer();
    const file = fileFor();
    renderHeader();
    renderProblems();
    renderFiles();
    renderToolbar(file);
    renderIntent(file);
    renderContent(file);
    renderContext();
    renderAnchor();
    renderComments();
    renderState();
    renderIssueStrip();
    renderLayout();
  }

  function responsePackage() {
    return {
      schemaVersion: "1.0",
      bundleId: bundle.bundleId,
      bundleDigest: bundle.bundleDigest,
      files: bundle.files.map((file) => ({
        path: file.path,
        accepted: state.accepted.has(file.path),
        desiredText: state.edits.get(file.path),
      })),
      comments: state.comments.map((comment) => ({
        ...comment,
        anchorState: commentAnchorState(comment),
        currentProposalFingerprint: contentFingerprint(state.edits.get(comment.filePath)),
      })),
      decisions: mappedClaims.map((claim) => {
        const needsReview = claimPaths(claim).some((path) => mappingNeedsReview(fileFor(path)));
        return {
          claimId: claim.id,
          requirementKey: requirementKey(claim),
          decision: needsReview ? "needs-reanchor" : "reviewed",
          filePaths: claimPaths(claim),
        };
      }),
    };
  }

  function feedbackMarkdown() {
    const response = responsePackage();
    const out = [
      `# Review feedback — ${bundle.title}`,
      "",
      `- Bundle: \`${bundle.bundleId}\``,
      `- Digest: \`${bundle.bundleDigest}\``,
      "",
      "## Requirement coverage",
      "",
      ...mappedClaims.map((claim) => `- ${requirementKey(claim)} — ${claim.label || claim.text}: ${claimPaths(claim).map((path) => "`" + path + "`").join(", ")}`),
      "",
    ];
    response.files.forEach((item) => {
      const file = fileFor(item.path);
      out.push(`## ${item.accepted ? "Accept" : "Reject"}: \`${item.path}\``, "");
      if (item.desiredText !== initialText(file)) {
        out.push("Edited proposal:", "", "```diff", buildPatch(item.path, file.baseline?.text ?? "", item.desiredText ?? ""), "```", "");
      }
      const comments = state.comments.filter((comment) => comment.filePath === item.path && !comment.resolved);
      if (comments.length) {
        out.push("Unresolved annotations:", "");
        comments.forEach((comment) => {
          const anchor = comment.anchor || {};
          const range = anchor.startLine ? `L${anchor.startLine}${anchor.endLine !== anchor.startLine ? `–${anchor.endLine}` : ""}` : (comment.hunkId || "file");
          const stale = commentAnchorState(comment) === "needs-reanchor" ? " · needs-reanchor" : "";
          out.push(`- [${range}${stale}] ${comment.body}${anchor.quote ? ` — “${anchor.quote.replace(/\s+/g, " ").slice(0, 180)}”` : ""}`);
        });
        out.push("");
      }
    });
    return out.join("\n");
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
    toast.timer = setTimeout(() => node.classList.remove("visible"), 2600);
  }

  byId("content-surface").addEventListener("mouseup", captureRenderedSelection);
  byId("content-surface").addEventListener("keyup", captureRenderedSelection);
  byId("edit-toggle").addEventListener("click", () => {
    if (!fileFor()) return;
    state.editMode = !state.editMode;
    state.view = "source";
    state.preview = null;
    renderAll();
  });
  byId("accept-file").addEventListener("click", () => {
    if (!state.filePath) return;
    if (state.accepted.has(state.filePath)) state.accepted.delete(state.filePath);
    else state.accepted.add(state.filePath);
    renderAll();
  });
  const showProblems = ({ focus = true } = {}) => {
    state.screen = "overview";
    state.zen = false;
    renderAll();
    if (focus) {
      const selected = [...byId("problem-list").querySelectorAll("[data-claim-id]")]
        .find((node) => node.dataset.claimId === state.selectedClaimId);
      (selected || byId("problem-map-title")).focus?.();
    }
  };
  const navigateToProblems = () => {
    if (state.screen !== "review") return;
    if (history.state?.workbenchScreen === "review") history.back();
    else {
      showProblems();
      writeHistory("overview", null, "replace");
    }
  };
  byId("show-problems").addEventListener("click", navigateToProblems);
  byId("review-back-to-problems").addEventListener("click", navigateToProblems);
  byId("toggle-report").addEventListener("click", () => { state.reportOpen = !state.reportOpen; renderAll(); });
  byId("toggle-issue").addEventListener("click", () => { state.issueOpen = !state.issueOpen; renderAll(); });
  byId("collapse-issue").addEventListener("click", () => { state.issueOpen = false; renderAll(); });
  byId("restore-issue").addEventListener("click", () => { state.issueOpen = true; renderAll(); byId("collapse-issue").focus(); });
  byId("toggle-files").addEventListener("click", () => { state.filesOpen = !state.filesOpen; renderAll(); });
  byId("collapse-files").addEventListener("click", () => { state.filesOpen = false; renderAll(); });
  byId("restore-files").addEventListener("click", () => { state.filesOpen = true; renderAll(); byId("collapse-files").focus(); });
  byId("toggle-inspector").addEventListener("click", () => { state.inspectorOpen = !state.inspectorOpen; renderAll(); });
  byId("collapse-inspector").addEventListener("click", () => { state.inspectorOpen = false; renderAll(); });
  byId("restore-inspector").addEventListener("click", () => { state.inspectorOpen = true; renderAll(); byId("collapse-inspector").focus(); });
  byId("collapse-command").addEventListener("click", () => { state.commandOpen = false; renderAll(); });
  byId("restore-command").addEventListener("click", () => { state.commandOpen = true; renderAll(); });
  byId("enter-zen").addEventListener("click", () => {
    state.zen = true;
    state.view = isMarkdown(fileFor()) ? "markdown" : "source";
    state.editMode = false;
    renderAll();
  });
  byId("zen-exit").addEventListener("click", () => { state.zen = false; renderAll(); });

  byId("comment-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const field = byId("comment-body");
    const body = field.value.trim();
    if (!body || !state.filePath) return;
    const anchor = state.anchor && state.anchor.filePath === state.filePath
      ? { ...state.anchor }
      : { type: "file", startLine: null, endLine: null, quote: "", view: state.view };
    state.comments.push({
      id: `comment-${Date.now()}`,
      filePath: state.filePath,
      hunkId: "current-proposal",
      anchor,
      body,
      resolved: false,
      proposalFingerprint: contentFingerprint(state.edits.get(state.filePath)),
    });
    field.value = "";
    renderComments();
    renderState();
    toast(anchor.type === "file" ? "已添加文件级批注" : "已添加带文本锚点的批注");
  });

  byId("download-patch").addEventListener("click", () => {
    const patch = bundle.files
      .filter((file) => state.accepted.has(file.path))
      .map((file) => buildPatch(file.path, applicationBase(file), state.edits.get(file.path) ?? ""))
      .join("\n");
    download(`${bundle.bundleId}.patch`, patch, "text/x-diff;charset=utf-8");
    toast("已下载当前 Patch");
  });

  byId("copy-feedback").addEventListener("click", async () => {
    await copyText(feedbackMarkdown());
    toast("结构化反馈已复制");
  });

  byId("prepare-apply").addEventListener("click", () => {
    const preview = byId("apply-preview");
    preview.replaceChildren();
    bundle.files.filter((file) => state.accepted.has(file.path)).forEach((file) => {
      const row = make("div", "apply-file");
      row.append(make("span", "", file.path));
      row.append(make("span", "", `${mappingNeedsReview(file) ? "映射待复核 · " : ""}expect ${file.applyPrecondition.kind === "exists" ? file.applyPrecondition.sha256 : "absent"}`));
      preview.append(row);
    });
    byId("apply-dialog").showModal();
  });

  byId("download-response").addEventListener("click", () => {
    download(`${bundle.bundleId}.review-response.json`, JSON.stringify(responsePackage(), null, 2) + "\n", "application/json;charset=utf-8");
    toast("响应包已下载；尚未写入工作空间");
  });

  byId("copy-apply-command").addEventListener("click", async () => {
    const command = [
      "请使用 HTML 组件与呈现模块的显式 adapter 应用这份响应：",
      `1. 用原始 bundle \`${bundle.bundleId}\` 和下载的 review-response.json 运行 workbench.py check。`,
      "2. 如果任一文件 stale，停止写入并保留编辑与批注。",
      "3. check 通过后运行 workbench.py apply，再读取文件验证结果。",
    ].join("\n");
    await copyText(command);
    toast("应用说明已复制");
  });

  window.addEventListener("popstate", (event) => {
    if (event.state?.workbenchScreen === "review") {
      enterReview(claimFor(event.state.claimId), { historyMode: "none" });
      return;
    }
    showProblems();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.zen) {
      state.zen = false;
      renderAll();
      return;
    }
    if (event.altKey && ["j", "k"].includes(event.key.toLowerCase())) {
      event.preventDefault();
      const visibleFiles = reviewFiles();
      const index = visibleFiles.findIndex((file) => file.path === state.filePath);
      const delta = event.key.toLowerCase() === "j" ? 1 : -1;
      const next = Math.max(0, Math.min(visibleFiles.length - 1, index + delta));
      if (visibleFiles[next]) {
        state.filePath = visibleFiles[next].path;
        state.view = isMarkdown(visibleFiles[next]) ? "markdown" : "source";
        state.editMode = false;
        state.preview = null;
        state.anchor = null;
        renderAll();
      }
    }
  });

  writeHistory("overview", null, "replace");
  renderAll();
})();
