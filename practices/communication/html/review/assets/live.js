// Only the loopback server includes this adapter. Offline reports use the same review model.
window.startLiveReview = function (capture, restore, response, download) {
  "use strict";
  const el = (id) => document.getElementById(id);
  const session = JSON.parse(el("live-session").textContent);
  const identity = { sessionId: session.sessionId, bundleDigest: session.bundleDigest };
  const draftKey = `review-draft:${identity.sessionId}:${identity.bundleDigest}`;
  let revision = session.revision;
  let receipt = session.applied;
  let timer, saving, unconfirmed, conflict = false, running = false;
  restore(session.state);
  let saved = JSON.stringify(capture());
  let current = saved;

  // Embedded JSON sorts object keys for safe rendering. Ignore key order when comparing drafts.
  function sameState(a, b) {
    const ordered = (_, value) => value && typeof value === "object" && !Array.isArray(value)
      ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, value[key]])) : value;
    return JSON.stringify(a, ordered) === JSON.stringify(b, ordered);
  }

  function status(message, error = false) {
    el("save-status").textContent = message;
    el("live-status").toggleAttribute("data-error", error);
    el("retry-save").hidden = !error || conflict;
    el("recover-draft").hidden = !conflict;
  }

  async function request(path, method, body) {
    const result = await fetch(path, { method, headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    const data = await result.json();
    if (!result.ok) {
      const error = new Error(data.error || result.statusText);
      error.code = data.code;
      error.status = result.status;
      throw error;
    }
    return data;
  }

  async function writeChanges() {
    while (current !== saved || unconfirmed) {
      // Replay the exact pending PUT before newer edits if the connection lost its reply.
      const sent = unconfirmed || { text: current, response: response() };
      unconfirmed = sent;
      try {
        const result = await request("/api/state", "PUT", {
          ...identity, revision, state: JSON.parse(sent.text), response: sent.response,
        });
        revision = result.revision;
      } catch (error) {
        conflict = error.code === "revision_conflict";
        if (error.status >= 400 && error.status < 500) unconfirmed = null;
        throw error;
      }
      saved = sent.text;
      unconfirmed = null;
    }
    status("已保存");
  }

  function flush() {
    clearTimeout(timer);
    if (conflict) return Promise.reject(new Error("保存版本冲突，请先保留本页草稿。"));
    if (!saving) {
      saving = writeChanges().catch((error) => {
        status(`保存失败 · ${error.message}`, true);
        throw error;
      }).finally(() => { saving = null; });
    }
    return saving;
  }

  function autosave() { flush().catch(() => {}); }

  function changed() {
    const next = JSON.stringify(capture());
    if (next === current) return;
    current = next;
    if (conflict) return;
    status("正在保存…");
    clearTimeout(timer);
    timer = setTimeout(autosave, 180);
  }

  function show(title, text) {
    const dialog = el("apply-dialog");
    el("apply-title").textContent = title;
    const detail = document.createElement("pre");
    detail.textContent = text;
    el("apply-preview").replaceChildren(detail);
    if (!dialog.open) dialog.showModal();
  }

  function showReceipt(value) {
    const lines = [...value.files];
    if (value.recoveryCopy) lines.push("", "恢复副本", value.recoveryCopy);
    if (value.warning) lines.push("", value.warning);
    show(value.files.length ? `已应用到 ${value.files.length} 个文件` : "已完成 · 没有文件需要写入", lines.join("\n"));
  }

  async function run(action) {
    if (running) return;
    running = true;
    const requested = current;
    show(action === "apply" ? "正在检查并应用…" : "正在检查…", "");
    try {
      await flush();
      if (requested !== current) throw new Error("审阅内容已改变，请再次检查。");
      const result = await request(`/api/${action}`, "POST", { ...identity, revision });
      if (action === "apply") {
        receipt = result;
        el("last-apply").hidden = false;
        showReceipt(result);
      } else {
        show("检查通过", result.files.join("\n") || "没有文件需要写入");
      }
    } catch (error) {
      if (error.code === "revision_conflict") {
        conflict = true;
        status(error.message, true);
      }
      const hint = error.code === "stale_targets" ? "目标文件已发生变化。审阅记录已保留。\n\n" : "";
      show(action === "apply" ? "应用未完成" : "检查未通过", hint + error.message);
    } finally {
      running = false;
    }
  }

  // Keep an in-flight or failed edit through a reload of this tab.
  try {
    const draft = JSON.parse(sessionStorage.getItem(draftKey));
    if (draft && !sameState(JSON.parse(draft.state), JSON.parse(saved))) {
      restore(JSON.parse(draft.state));
      current = JSON.stringify(capture());
      const delivered = draft.sending && draft.revision + 1 === revision &&
        sameState(JSON.parse(draft.sending), session.state);
      conflict = draft.revision !== revision && !delivered;
    }
    sessionStorage.removeItem(draftKey);
  } catch { /* Normal saving also works with browser storage disabled. */ }
  window.addEventListener("pagehide", () => {
    if (current !== saved || unconfirmed) {
      try { sessionStorage.setItem(draftKey, JSON.stringify({ revision, state: current, sending: unconfirmed?.text })); } catch {}
    }
  });
  window.addEventListener("beforeunload", (event) => {
    if (current !== saved || unconfirmed || running) { event.preventDefault(); event.returnValue = ""; }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && current !== saved && !conflict) autosave();
  });

  el("live-status").hidden = false;
  el("check-only").hidden = false;
  el("check-only").addEventListener("click", () => run("check"));
  el("last-apply").hidden = !receipt;
  el("last-apply").addEventListener("click", () => showReceipt(receipt));
  el("retry-save").addEventListener("click", () => { status("正在保存…"); autosave(); });
  el("recover-draft").addEventListener("click", () => {
    download("review-draft.json", JSON.stringify({ ...identity, revision, state: capture(), response: response() }, null, 2),
      "application/json;charset=utf-8");
    saved = current;
    unconfirmed = null;
    location.reload();
  });
  const dialog = el("apply-dialog");
  dialog.querySelector(".eyebrow").hidden = true;
  dialog.querySelector(".dialog-lead").hidden = true;
  dialog.querySelector(".dialog-actions").hidden = true;
  if (conflict) status("本页有未保存的草稿，其他窗口已保存新版本。", true);
  else if (current !== saved) { status("正在保存…"); autosave(); }
  else status("已保存");
  return { changed, run };
};
