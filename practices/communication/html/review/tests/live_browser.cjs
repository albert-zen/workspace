// Optional browser acceptance test. The application and Python suite need only the standard library.
const assert = require("node:assert/strict");
const { execFileSync, spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { once } = require("node:events");
const { createInterface } = require("node:readline");
const { pathToFileURL } = require("node:url");
const { chromium } = require("playwright");

async function verify() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "review-browser-"));
  const script = path.resolve(__dirname, "../scripts/workbench.py");
  const target = path.join(dir, "rules.md");
  const proposal = path.join(dir, "proposal.json");
  const bundle = path.join(dir, "review.json");
  const offline = path.join(dir, "review.html");
  const before = "# A\nold A\n# B\nold B\n";
  let server, browser, backup;
  try {
    fs.writeFileSync(target, before);
    fs.writeFileSync(proposal, JSON.stringify({
      claims: [{ id: "rules", filePath: "rules.md" }],
      files: [{ path: "rules.md", proposedText: before.replaceAll("old", "new") }],
      blocks: [1, 3].map((line) => ({ id: String(line), filePath: "rules.md", claimId: "rules",
        title: "Rule " + line, reason: "Review separately", evidenceIds: [],
        oldRange: [line, line + 1], newRange: [line, line + 1] })),
    }));
    execFileSync("python3", ["-B", script, "reflection-bundle", "--repo", dir, "--proposal", proposal, "--output", bundle]);
    execFileSync("python3", ["-B", script, "render", "--bundle", bundle, "--output", offline]);
    server = spawn("python3", ["-B", script, "serve", "--repo", dir, "--bundle", bundle, "--no-open"]);
    const [url] = await once(createInterface({ input: server.stdout }), "line");
    browser = await chromium.launch({ channel: "chrome" });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.setDefaultTimeout(5500);
    await page.goto(url + "#theme=rules");
    const action = (name) => page.locator(`[data-action="${name}"]`);
    const close = () => page.locator("#apply-dialog .dialog-close").click();
    const saved = () => page.waitForFunction(() => document.querySelector("#save-status").textContent === "已保存");
    assert(await page.locator("#prepare-apply").isDisabled());

    await action("line-comment").first().click({ force: true });
    await page.locator("[data-comment-input]").fill("line note");
    await page.locator(".comment-form [type=submit]").click();
    await page.locator(".line-text").filter({ hasText: "new A" }).first().evaluate((node) => {
      const range = document.createRange();
      range.selectNodeContents(node);
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      node.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });
    await page.locator("#selection-bubble").click();
    await page.locator("[data-comment-input]").fill("selection note");
    await page.locator(".comment-form [type=submit]").click();
    await action("theme-comment").first().click();
    await page.locator("#theme-comment-input").fill("theme note");
    await page.locator(".theme-comment-form [type=submit]").click();
    await page.locator("#collapse-evidence").click();
    await action("edit-unit").first().click();
    await page.locator("[data-edit-input]").fill("hand A");
    await saved();
    assert(await page.locator("[data-edit-input]").evaluate((node) => node === document.activeElement));
    await page.reload();
    assert.equal(await page.locator("[data-edit-input]").inputValue(), "hand A");
    await action("save-edit").click();
    await page.locator('[data-action="block-decision"][data-decision="accepted"]').first().press("Enter");
    await action("pending-as-rejected").click();
    await saved();
    await page.reload();
    const response = await page.evaluate(() => window.__workbenchDebug.response());
    assert.deepEqual(response.decisions.map((item) => item.decision), ["accepted", "rejected"]);
    assert.deepEqual(response.comments.map((item) => item.anchor.type), ["line", "text", "theme"]);
    assert.equal(response.comments[1].anchor.quote, "new A");
    assert.equal(response.files[0].desiredText, "# A\nhand A\n# B\nold B\n");

    const second = await browser.newPage();
    await second.goto(page.url());
    // Commit the PUT, then drop its acknowledgement and continue typing before retrying.
    await page.route("**/api/state", async (route) => {
      const result = await route.fetch();
      assert.equal(result.status(), 200);
      await route.abort();
    }, { times: 1 });
    await action("theme-comment").first().click();
    await page.locator("#theme-comment-input").fill("another draft");
    await page.locator("#retry-save").waitFor();
    await page.locator("#retry-save").click();
    await page.locator("#theme-comment-input").fill("continued after a lost reply");
    await saved();
    await page.locator("#collapse-evidence").click();
    await second.locator('[data-action="block-decision"][data-decision="rejected"]').first().click();
    await second.locator("#recover-draft").waitFor();
    assert.match(await second.locator("#save-status").innerText(), /新版本/);
    await second.close();

    await page.locator("#show-themes").click();
    await page.locator("#overview-screen").waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.locator("#check-only").click();
    await page.getByRole("heading", { name: "检查通过" }).waitFor();
    assert.equal(fs.readFileSync(target, "utf8"), before);
    await close();
    fs.writeFileSync(target, "external\n");
    await page.locator("#prepare-apply").click();
    await page.getByRole("heading", { name: "应用未完成" }).waitFor();
    assert.match(await page.locator("#apply-preview").innerText(), /Stale targets[\s\S]*rules\.md/);
    assert.equal(fs.readFileSync(target, "utf8"), "external\n");
    await close();
    fs.writeFileSync(target, before);
    await page.locator("#prepare-apply").click();
    await page.getByRole("heading", { name: "已应用到 1 个文件" }).waitFor();
    assert.equal(fs.readFileSync(target, "utf8"), response.files[0].desiredText);
    const receipt = (await (await page.request.get(url + "api/session")).json()).applied;
    backup = receipt.recoveryCopy;
    assert.equal(fs.readFileSync(path.join(backup, "0"), "utf8"), before);
    await page.reload();
    await page.locator("#last-apply").click();
    assert((await page.locator("#apply-preview").innerText()).includes(backup));
    await close();

    await page.goto(pathToFileURL(offline).href + "#theme=rules");
    assert(await page.locator("#live-status").isHidden());
    await action("pending-as-rejected").click();
    await page.locator("#prepare-apply").click();
    const downloading = page.waitForEvent("download");
    await page.locator("#download-response").click();
    const exportFile = await downloading;
    assert.match(exportFile.suggestedFilename(), /review-response\.json$/);
    assert.equal(await exportFile.failure(), null);
    await page.close();
    assert.deepEqual(errors, []);
    console.log("Live autosave, reload, conflict, retry, check, stale apply, direct apply, recovery and offline export passed.");
  } finally {
    if (browser) await browser.close();
    if (server) server.kill();
    if (backup) fs.rmSync(backup, { recursive: true, force: true });
    fs.rmSync(dir, { recursive: true, force: true });
  }
}
verify().catch((err) => { console.error(err); process.exitCode = 1; });
